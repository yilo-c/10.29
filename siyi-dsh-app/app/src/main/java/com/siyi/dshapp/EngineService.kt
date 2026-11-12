package com.siyi.dshapp

import android.app.*
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import java.io.BufferedReader
import java.io.File

/**
 * 前台服务：解压引擎与 profile，启动 dsh web（--profile web --port 3180），
 * 从 stdout 抓取带 token 的 URL 存入 WebState。
 */
class EngineService : Service() {

    companion object {
        const val CHANNEL_ID = "siyi_engine"
        const val NOTIF_ID = 1001
        const val WEB_PORT = 3180

        @Volatile private var liveProcess: Process? = null
        /**
         * boot() 的互斥闸门。
         *
         * onStartCommand 会被连续调用两次（MainActivity 拉起服务 + START_STICKY 重投），
         * 两次都在 process 还没赋值时读到 null，于是并发跑了两遍 boot()，
         * 起了两个引擎进程：先绑到 3180 的赢，另一个报 EADDRINUSE，
         * 连带 web-app 在 bindUrlHost 上抛 indexOf of undefined。
         * 输的那个进程往 stderr 打 "startup failed"，被 UI 当成整体失败 → 白屏。
         * 这里用 CAS 保证进程内只有一次启动。
         */
        private val booting = java.util.concurrent.atomic.AtomicBoolean(false)
        /** 供 DeviceBridge 的工具查询引擎是否仍在跑。 */
        fun isRunning(): Boolean = liveProcess?.isAlive == true
    }

    private var process: Process?
        get() = liveProcess
        set(value) { liveProcess = value }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL_ID, "引擎", NotificationManager.IMPORTANCE_LOW)
            getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIF_ID, notif("准备引擎…"))
        // process 为 null 只能说明「还没起来」，不能说明「该由我来起」：
        // 连续两次 onStartCommand 会同时看到 null。CAS 才是真正的闸门。
        // isAlive 而不只是 null：引擎崩掉后 process 仍非空，只看 null 就永远不会重启。
        val alive = process?.isAlive == true
        if (!alive && booting.compareAndSet(false, true)) {
            Thread {
                try { boot() } finally { booting.set(false) }
            }.start()
        }
        return START_STICKY
    }

    private fun notif(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("思忆")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .build()

    private fun update(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, notif(text))
    }

    /**
     * 启动阶段诊断，追加到 /sdcard/思忆集/app/boot.log。
     * 必须是类级方法：freePort() 里也要写日志，而 boot() 内的局部函数它看不见，
     * 写成局部函数会直接 Unresolved reference: stage，构建就停在 compileDebugKotlin。
     */
    private fun stage(msg: String) {
        runCatching {
            val diag = File("/sdcard/思忆集/app/boot.log")
            diag.parentFile?.mkdirs()
            diag.appendText("[${System.currentTimeMillis()}] $msg\n")
        }
    }

    private fun boot() {
        stage("boot start")
        // 引擎已经在跑就直接复用，绝不能再起一个。
        // 以前这里不看直接往下走，会起第二个引擎抢 3180，
        // 输的那个报 EADDRINUSE → web-app 拿不到 host → 白屏。
        if (process?.isAlive == true) {
            stage("引擎已在运行 pid=" + process?.pid() + "，复用")
            return
        }
        var lastNotify = 0L
        EngineInstaller.onProgress = { n, txt ->
            stage("progress $n $txt")
            val now = System.currentTimeMillis()
            if (now - lastNotify > 2000) { lastNotify = now; update("正在解压 $txt（$n 个文件）…") }
        }
        val err = EngineInstaller.ensureAll(this)
        stage("ensureAll -> " + (err ?: "OK"))
        if (err != null) { update(err); return }

        // 启动前清掉占用端口的残留引擎，否则 webserver 报 EADDRINUSE，
        // 连带 web-app 拿不到 host，界面白屏。
        //
        // ⚠ 实测（Android 15 / MIUI）：force-stop 确实会带走引擎进程，
        // 但「本次 App 进程里已经起了一个引擎」时它会一直占着 3180。
        // 以前这种情况下硬起第二个 → EADDRINUSE → web-app 拿不到 host → 白屏。
        // 现在端口腾不出来就放弃本次启动，等下一轮 onStartCommand 再试。
        if (!freePort(WEB_PORT)) {
            stage("端口未腾出，放弃本次启动（避免双引擎互撞）")
            update("引擎端口被占用，正在重试…")
            return
        }

        val nodeBin = File(applicationInfo.nativeLibraryDir, "libdshnode.so")
        val engine = EnginePaths.engineRoot(this)
        val binJs = File(engine, "node_modules/@deepseek-ai/dsh/lib/bin.js")
        val home = EnginePaths.dshHome(this)

        if (!nodeBin.exists()) { update("缺少 libdshnode.so"); return }
        if (!binJs.exists()) { update("引擎缺失: ${binJs.path}"); return }

        update("启动 web 服务…")
        val cmd = listOf(
            nodeBin.absolutePath, "--expose-internals", binJs.absolutePath,
            "--profile", "web", "--port", WEB_PORT.toString(), "--no-open"
        )
        val pb = ProcessBuilder(cmd)
        pb.directory(engine)
        pb.environment()["DSH_HOME"] = home.absolutePath
        pb.environment()["DSH_NATIVE_DIR"] = applicationInfo.nativeLibraryDir
        pb.environment()["TZ"] = "Asia/Shanghai"
        // Android 的 os.homedir() 返回 /data（App 无权访问），
        // 目录选择器会因此报 EACCES。改用共享存储作为 HOME。
        val userHome = File("/sdcard/思忆集")
        userHome.mkdirs()
        pb.environment()["HOME"] = userHome.absolutePath
        pb.environment()["USERPROFILE"] = userHome.absolutePath

        // 对齐官方 DSH 的引擎进程环境。
        // 关键一：我们用普通 node 启动，process.platform 是 linux 而不是 android，
        // dsh-bash-local 会去找 bash，但 Android 只有 /system/bin/sh，
        // 于是所有 shell 工具都报 spawn bash EACCES。显式指定即可绕开。
        // 关键二：PATH/TMPDIR 缺失会让 ripgrep、glob、mkdtemp 一起失效。
        val tmpDir = File(cacheDir, "tmp").also { it.mkdirs() }
        pb.environment()["DSH_BASH_SHELL"] = "/system/bin/sh"
        pb.environment()["SHELL"] = "/system/bin/sh"
        pb.environment()["PATH"] = EngineInstaller.binDir(this).absolutePath + ":" + applicationInfo.nativeLibraryDir + ":/system/bin:/system/xbin"
        pb.environment()["TMPDIR"] = tmpDir.absolutePath
        // glob/grep 依赖 ripgrep。引擎的 resolveRgPath 有 Android 分支
        // （platform === "android" 时直接用 PATH 里的 rg），但思忆集用普通 node 启动，
        // platform 是 linux，会去走 @vscode/ripgrep，而那个包没有 android-arm64 版本。
        // 显式给 DSH_RG_PATH 即可绕开，指向 files/bin/rg
        // （软链到 APK 内 librg.so —— 只有 native lib 目录允许执行）。
        pb.environment()["DSH_RG_PATH"] = File(EngineInstaller.binDir(this), "rg").absolutePath
        // 用户插件目录：引擎启动时扫描 /sdcard/思忆集/plugins/*.js 动态注册。
        // 有了它，以后加能力只需往目录里丢 .js 文件并重启引擎，不用再出 APK。
        val userPlugins = Capabilities.pluginsDir()
        if (!userPlugins.exists()) userPlugins.mkdirs()
        pb.environment()["DSH_USER_PLUGINS"] = userPlugins.absolutePath

        // 设备能力桥：先跑环境自检（结果写 bridge.log，装一次包就能定位断点），
        // 再建 socketpair 把引擎端 fd 号传下去。
        // 失败不影响引擎启动（设备类工具会缺失，但聊天与 shell 仍可用）。
        runCatching { DeviceBridge.envSelfCheck(this) }
            .onFailure { stage("envSelfCheck 异常: " + it.message) }
        val bridgeEnv = DeviceBridge.prepare()
        if (bridgeEnv != null) {
            pb.environment()[bridgeEnv.first] = bridgeEnv.second
            stage("bridge " + bridgeEnv.first + "=" + bridgeEnv.second)
        } else {
            stage("bridge 未启用")
        }

        runCatching {
            stage("starting: " + cmd.joinToString(" "))
            val p = pb.start()
            stage("engine started, alive=" + p.isAlive)
            process = p
            runCatching { writePidFile(p.pid()) }
            if (bridgeEnv != null) DeviceBridge.start(this@EngineService)
            // 读 stdout 抓 token
            val logFile = File("/sdcard/思忆集/app/engine-stdout.log").also { it.parentFile?.mkdirs() }
            Thread {
                runCatching {
                    p.inputStream.bufferedReader().use { br: BufferedReader ->
                        br.forEachLine { line ->
                            runCatching { logFile.appendText(line + "\n") }
                            val m = Regex("token=([A-Za-z0-9_-]+)").find(line)
                            if (m != null) {
                                WebState.setUrl("http://127.0.0.1:$WEB_PORT/?token=${m.groupValues[1]}")
                                update("就绪，可打开界面")
                            }
                        }
                    }
                }
            }.start()
            // 读 stderr 判断失败
            Thread {
                val errLog = File("/sdcard/思忆集/app/engine-stderr.log").also { it.parentFile?.mkdirs() }
                runCatching {
                    p.errorStream.bufferedReader().use { br ->
                        br.forEachLine { line ->
                            runCatching { errLog.appendText(line + "\n") }
                            // 只有「启动失败」才是致命信号；普通的 "Error:" 多半是
                            // 某个插件自己的告警（比如模型渠道 502），拿来判定整体
                            // 失败会把能用的引擎判死，界面就卡在错误页不再重试。
                            if (line.contains("startup failed", true)) {
                                // 引擎已经打出过带 token 的地址就说明服务其实起来了，
                                // 这条失败来自已经被淘汰的那次尝试，不能覆盖可用状态。
                                if (WebState.getUrl() == null) {
                                    WebState.setError(line.take(200))
                                    update("启动异常: ${line.take(60)}")
                                }
                            }
                        }
                    }
                }
            }.start()
        }.onFailure { update("启动失败: ${it.message}") }
    }

    override fun onDestroy() {
        DeviceBridge.stop()
        killEngine()
        WebState.clear()
        super.onDestroy()
    }

    /** 任务从最近列表划掉时也要清理，否则引擎残留占着端口。 */
    override fun onTaskRemoved(rootIntent: Intent?) {
        killEngine()
        super.onTaskRemoved(rootIntent)
    }

    /**
     * 强杀引擎子进程。
     * process.destroy() 只发 SIGTERM，node 引擎可能忽略或不及时响应，
     * 残留进程继续占着 3180，下次启动就报 EADDRINUSE。
     */
    /**
     * 确保端口空闲。被占用时找出占用进程并杀掉。
     *
     * 场景：上次退出时引擎没被清理干净，残留在后台占着 3180。
     * 新引擎启动会报 listen EADDRINUSE，连带 web-app 白屏。
     *
     * 只杀本 App 自己的引擎进程（按 cmdline 里的路径匹配），不动别人。
     */
    private fun freePort(port: Int): Boolean {
        if (!isPortInUse(port)) { stage("port " + port + " 空闲"); return true }
        stage("port " + port + " 被占用，尝试清理")
        // ⚠ 不能再靠扫 /proc 找进程。
        // Android 的 untrusted_app 域读不到其它进程的 /proc/<pid>/cmdline，
        // 扫描结果永远是空 → 清理永远无效 → 硬起第二个引擎 → EADDRINUSE → 白屏。
        // 改成读 pid 文件：引擎启动时把 pid 写在 App 自己的私有目录里，
        // App 重启后依然能读到，能精确找到那个孤儿。
        val orphans = readPidFile().filter { it != process?.pid() }
        stage("port " + port + " pid文件里的引擎: " + orphans.joinToString(","))
        for (pid in orphans) {
            stage("kill 残留引擎 pid=" + pid)
            // 先 SIGTERM 再 SIGKILL：node 引擎不一定响应 SIGTERM，
            // 只发一次就放弃的话新引擎照样撞 EADDRINUSE。
            runCatching { android.os.Process.killProcess(pid) }
            var waited = 0
            while (waited < 15 && isAlive(pid)) { Thread.sleep(100); waited++ }
            if (isAlive(pid)) {
                stage("SIGTERM 无效，SIGKILL pid=" + pid)
                runCatching {
                    ProcessBuilder("kill", "-9", pid.toString())
                        .start().waitFor(1, java.util.concurrent.TimeUnit.SECONDS)
                }
                waited = 0
                while (waited < 20 && isAlive(pid)) { Thread.sleep(100); waited++ }
            }
            stage("kill 完成 pid=" + pid + " alive=" + isAlive(pid))
        }
        clearPidFile()
        // 等端口释放，最多 5 秒
        for (i in 0 until 50) {
            if (!isPortInUse(port)) { stage("port " + port + " 已释放"); return true }
            Thread.sleep(100)
        }
        stage("port " + port + " 仍被占用")
        return false
    }

    /** 引擎 pid 文件：App 私有目录，App 重启后依然能读到。 */
    private fun pidFile(): File = File(applicationContext.filesDir, "engine.pid")

    /** 读 pid 文件里的引擎 pid 列表。 */
    private fun readPidFile(): List<Int> = runCatching {
        if (!pidFile().exists()) return emptyList()
        pidFile().readText()
            .split("\n".toRegex())
            .mapNotNull { it.trim().toIntOrNull() }
            .filter { it > 1 }
    }.getOrDefault(emptyList())

    /** 记录引擎 pid。 */
    private fun writePidFile(pid: Int) {
        runCatching {
            val all = (readPidFile() + pid).distinct()
            pidFile().writeText(all.joinToString("\n"))
        }
    }

    /** 清掉 pid 文件（引擎都清干净后）。 */
    private fun clearPidFile() {
        runCatching { pidFile().delete() }
    }

    /** 进程是否还活着。 */
    private fun isAlive(pid: Int): Boolean =
        runCatching { File("/proc/" + pid).exists() }.getOrDefault(false)

    /** 取子进程 pid（Process.pid() 在旧 API 上不可用，用反射兜底）。 */
    private fun Process.pid(): Int = runCatching {
        val m = Process::class.java.getMethod("pid")
        (m.invoke(this) as Number).toInt()
    }.getOrElse {
        // 退路：从 /proc 里找 cmdline 含 bin.js 且是本进程父子的那个
        runCatching {
            val parent = android.os.Process.myPid().toString()
            File("/proc").listFiles()
                ?.filter { it.isDirectory && it.name.all(Char::isDigit) }
                ?.firstOrNull { dir ->
                    val st = File(dir, "stat").readText()
                    st.substringAfter(") ").split(" ").getOrNull(1)?.trim() == parent &&
                        File(dir, "cmdline").readText().contains("bin.js")
                }
                ?.name?.toIntOrNull() ?: -1
        }.getOrDefault(-1)
    }

    /** 尝试绑定端口判断是否被占用。 */
    private fun isPortInUse(port: Int): Boolean {
        return try {
            java.net.ServerSocket().use { s ->
                s.reuseAddress = false
                s.bind(java.net.InetSocketAddress("127.0.0.1", port), 1)
            }
            false
        } catch (t: Throwable) {
            true
        }
    }

    private fun killEngine() {
        val p = process
        process = null
        if (p == null) { clearPidFile(); return }
        val pid = runCatching { p.pid() }.getOrDefault(-1)
        runCatching {
            p.destroy()
            if (!p.waitFor(2, java.util.concurrent.TimeUnit.SECONDS)) {
                p.destroyForcibly()
                p.waitFor(2, java.util.concurrent.TimeUnit.SECONDS)
            }
        }
        // 引擎可能不响应 SIGTERM，再补一刀 SIGKILL，否则它会变成孤儿占着 3180。
        if (pid > 1 && isAlive(pid)) {
            stage("SIGTERM 无效，SIGKILL 引擎 pid=" + pid)
            runCatching {
                ProcessBuilder("kill", "-9", pid.toString())
                    .start().waitFor(1, java.util.concurrent.TimeUnit.SECONDS)
            }
        }
        clearPidFile()
    }

}

/** 引擎就绪后共享给 UI 的地址 */
object WebState {
    @Volatile private var url: String? = null
    @Volatile private var error: String? = null
    fun setUrl(u: String) { url = u; error = null }
    fun setError(e: String) { error = e }
    fun getUrl(): String? = url
    fun getError(): String? = error
    fun isReady(): Boolean = url != null
    fun clear() { url = null; error = null }
}
