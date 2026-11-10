package com.siyi.dshapp

import android.app.*
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import java.io.BufferedReader
import java.io.File

/**
 * 前台服务：解压引擎与 profile，启动 dsh web（--profile web --port 3080），
 * 从 stdout 抓取带 token 的 URL 存入 WebState。
 */
class EngineService : Service() {

    companion object {
        const val CHANNEL_ID = "siyi_engine"
        const val NOTIF_ID = 1001
        const val WEB_PORT = 3080

        @Volatile private var liveProcess: Process? = null
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
        if (process == null) Thread { boot() }.start()
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

    private fun boot() {
        val diag = File("/sdcard/思忆集/app/boot.log").also { it.parentFile?.mkdirs() }
        fun stage(msg: String) { runCatching { diag.appendText("[${System.currentTimeMillis()}] $msg\n") } }
        stage("boot start")
        var lastNotify = 0L
        EngineInstaller.onProgress = { n, txt ->
            stage("progress $n $txt")
            val now = System.currentTimeMillis()
            if (now - lastNotify > 2000) { lastNotify = now; update("正在解压 $txt（$n 个文件）…") }
        }
        val err = EngineInstaller.ensureAll(this)
        stage("ensureAll -> " + (err ?: "OK"))
        if (err != null) { update(err); return }

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

        // 设备能力桥：建 socketpair，把引擎端 fd 号传下去。
        // 失败不影响引擎启动（读类工具会缺失，但聊天仍可用）。
        val bridgeEnv = DeviceBridge.prepare()
        if (bridgeEnv != null) {
            pb.environment()[bridgeEnv.first] = bridgeEnv.second
            stage("bridge fd=" + bridgeEnv.second)
        } else {
            stage("bridge 未启用")
        }

        runCatching {
            stage("starting: " + cmd.joinToString(" "))
            val p = pb.start()
            stage("engine started, alive=" + p.isAlive)
            process = p
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
                            if (line.contains("startup failed", true) || line.contains("Error:", true)) {
                                WebState.setError(line.take(200))
                                update("启动异常: ${line.take(60)}")
                            }
                        }
                    }
                }
            }.start()
        }.onFailure { update("启动失败: ${it.message}") }
    }

    override fun onDestroy() {
        DeviceBridge.stop()
        runCatching { process?.destroy() }
        process = null
        WebState.clear()
        super.onDestroy()
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
