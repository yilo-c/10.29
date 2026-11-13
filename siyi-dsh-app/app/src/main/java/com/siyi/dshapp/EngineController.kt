package com.siyi.dshapp

import android.content.Context
import java.io.BufferedReader
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * 引擎的唯一所有者。
 *
 * ## 为什么要有这一层
 *
 * 之前的架构把引擎状态劈成了两半：
 *   - 进程句柄放在 EngineService 的静态字段 liveProcess
 *   - 启动地址/错误放在另一个独立的全局单例 WebState
 * 两个状态源没有共同所有者，任何一方都能改，UI 读到的可能是半新半旧的状态。
 *
 * 启动路径也有多条：MainActivity 会 startForegroundService，BootReceiver 也会，
 * 外加 START_STICKY 的系统重投。三条路径都直接落到 EngineService.onStartCommand，
 * 而 onStartCommand 只是「看到 process 为 null 就再 boot 一次」，
 * 于是 boot() 会跑第二遍，第二个引擎抢 3180 失败 → web-app 拿不到 host → 白屏。
 *
 * ## 现在
 *
 * 所有启动路径都只做一件事：EngineController.ensureRunning(context)。
 * 引擎进程、地址、错误全部收在这一处，由它独占。
 * 并发调用由 AtomicBoolean 串行化，第二次调用发现引擎已活着就直接返回。
 */
object EngineController {

    /** 引擎状态。UI 的唯一数据源。 */
    data class State(
        val phase: Phase,
        val url: String? = null,
        val error: String? = null
    )

    enum class Phase { IDLE, STARTING, READY, FAILED }

    /** 端口：思忆集专用，与官方 DSH 的 3080 隔离。 */
    const val WEB_PORT = 3180

    private val LOG_DIR = File("/sdcard/思忆集/app")

    /** 引擎子进程（唯一持有者）。 */
    private val processRef = AtomicReference<Process?>(null)

    /** 当前状态（唯一持有者）。 */
    private val stateRef = AtomicReference(State(Phase.IDLE))

    /**
     * 启动闸门。保证任意时刻只有一次启动流程在跑；
     * 后来的调用发现引擎已活着就直接返回，不会再起第二个。
     */
    private val starting = AtomicBoolean(false)

    /** 主动停止标记。用来区分「我们杀的」和「它自己崩的」——
     *  崩溃才需要自动重启，主动停止不该被 watchdog 又拉起来。 */
    private val stopping = AtomicBoolean(false)

    /** 持有 Context 供 watchdog 重启时用。 */
    @Volatile private var appCtx: Context? = null

    /** 状态变化回调（通知栏文案）。 */
    @Volatile private var statusSink: ((String) -> Unit)? = null

    /** 连续自动重启次数。成功后清零；超过上限就停在 FAILED，不再无限重试。 */
    private val restartCount = java.util.concurrent.atomic.AtomicInteger(0)

    /** 自动重启上限：超过就停手，避免崩溃循环把电池和存储写爆。 */
    private const val MAX_RESTARTS = 3

    /** 启动超时：从拉起进程到打出带 token 地址的最长等待。 */
    private const val START_TIMEOUT_MS = 180_000L

    // ── 对外 API ────────────────────────────────────────────────────────────

    /** 当前状态。 */
    fun state(): State = stateRef.get()

    /**
     * 引擎是否在跑。
     *
     * ⚠ 不能只看 processRef：setsid 之后引擎不是本进程的直接子进程，
     * 句柄会很快失效（isAlive 变 false），但引擎其实活着。
     * 所以判据是**端口是否在监听** —— 这才是引擎的真实状态。
     */
    fun isRunning(): Boolean = isListening()

    /**
     * 确保引擎在跑。**唯一**的启动入口。
     * MainActivity、BootReceiver、START_STICKY 重投全部走这里，重复调用安全。
     */
    fun ensureRunning(ctx: Context, onStatus: (String) -> Unit = {}) {
        appCtx = ctx.applicationContext
        statusSink = onStatus
        stopping.set(false)
        if (isRunning()) {
            stage("引擎已在运行 pid=" + pidOf(processRef.get()) + "，复用")
            return
        }
        // 本进程没有引擎句柄，但引擎可能是**上一个 App 进程**留下的常驻进程。
        // 这种情况直接接管，用户就是 0 等待（方案 B 的核心）。
        if (adoptExistingEngine(ctx)) {
            stage("接管到常驻引擎 pid=" + pidOf(processRef.get()) + "，跳过启动")
            onStatus("引擎已就绪")
            return
        }
        if (!starting.compareAndSet(false, true)) {
            stage("启动流程正在进行中，跳过重复启动")
            return
        }
        try {
            boot(ctx.applicationContext, onStatus)
        } finally {
            starting.set(false)
        }
    }

    /**
     * 接管上一个 App 进程留下的常驻引擎。
     *
     * 方案 B 的关键：引擎常驻，App 退出时不杀它。下次打开 App 时进程内的
     * processRef 是空的（静态字段随进程一起没了），但引擎进程还活着。
     *
     * 这里靠三件事认出它：
     *   1. pid 文件里有它的 pid
     *   2. 那个 pid 还在（/proc/<pid> 存在）
     *   3. 端口 3180 正在监听且能连上（说明它服务正常）
     *
     * 认出来之后把 URL 找回来。怎么找？引擎每次启动都会往 stdout 打
     * 带 token 的地址，但那是上一次进程的 stdout，已经拿不到了。
     * 所以这里用最稳的办法：直接问引擎要一个带新 token 的地址 ——
     * 读 /sdcard/思忆集/app/engine-stdout.log 里最后一条 dsh web: 行。
     * 那个文件是 App 写的，跨进程也读得到。
     *
     * @return 是否接管成功
     */
    private fun adoptExistingEngine(ctx: Context): Boolean {
        val pids = readPidFile(ctx)
        if (pids.isEmpty()) return false
        val alive = pids.filter { isAlive(it) }
        if (alive.isEmpty()) {
            // pid 都死了，文件是陈旧的，清掉避免下次误判
            clearPidFile(ctx)
            return false
        }
        if (!isListening()) return false   // 进程在但服务没起来，不算接管

        val url = lastAdvertisedUrl()
        if (url == null) {
            // 认得出进程但拿不到地址：只能重新起一个，旧的清掉
            stage("常驻引擎 pid=" + alive.joinToString(",") + " 在，但拿不到地址，重新启动")
            for (pid in alive) runCatching { android.os.Process.killProcess(pid) }
            clearPidFile(ctx)
            return false
        }
        // 接管成功：登记 pid，状态置 READY
        processRef.set(null)   // 句柄不属于本进程，但 pid 文件已够用
        stateRef.set(State(Phase.READY, url = url))
        stage("已接管常驻引擎 url=" + url)
        return true
    }

    /**
     * 从 stdout 日志里取引擎最后公布的地址。
     * 那是 App 自己写的文件，跨进程可读。
     */
    private fun lastAdvertisedUrl(): String? = runCatching {
        val f = File("/sdcard/思忆集/app/engine-stdout.log")
        if (!f.exists()) return null
        var found: String? = null
        f.forEachLine { line ->
            val m = Regex("dsh web: (http://\\S+)").find(line)
            if (m != null) found = m.groupValues[1]
        }
        found
    }.getOrNull()

    /** 停止引擎。 */
    fun stop() {
        stopping.set(true)
        restartCount.set(0)
        val p = processRef.get()
        processRef.set(null)
        stateRef.set(State(Phase.IDLE))
        val c = appCtx
        // setsid 之后 p 只是 setsid 的句柄，杀它对真正的引擎无效。
        // 统一按 pid 文件里登记的真实 pid 清理。
        val real = findEnginePid()
        if (real > 1) {
            stage("停止引擎 pid=" + real)
            runCatching { android.os.Process.killProcess(real) }
            var waited = 0
            while (waited < 20 && isAlive(real)) { Thread.sleep(100); waited++ }
            if (isAlive(real)) {
                stage("SIGTERM 无效，SIGKILL pid=" + real)
                runCatching {
                    ProcessBuilder("kill", "-9", real.toString())
                        .start().waitFor(1, java.util.concurrent.TimeUnit.SECONDS)
                }
            }
        } else if (p != null) {
            // 没有登记 pid（刚启动就停），退回杀句柄
            runCatching {
                p.destroy()
                if (!p.waitFor(2, java.util.concurrent.TimeUnit.SECONDS)) p.destroyForcibly()
            }
        }
        if (c != null) clearPidFile(c)
    }

    // ── 内部实现 ────────────────────────────────────────────────────────────

    private fun boot(ctx: Context, onStatus: (String) -> Unit) {
        stage("boot start")
        stateRef.set(State(Phase.STARTING))

        val installErr = EngineInstaller.ensureAll(ctx)
        stage("ensureAll -> " + (installErr ?: "OK"))
        if (installErr != null) { fail(installErr, onStatus); return }

        // 端口必须真的没人监听才能起，否则第二个引擎会撞 EADDRINUSE → 白屏。
        if (!freePort(ctx, WEB_PORT)) {
            stage("端口未腾出，放弃本次启动（避免双引擎互撞）")
            fail("引擎端口被占用，正在重试…", onStatus)
            return
        }

        val nodeBin = File(ctx.applicationInfo.nativeLibraryDir, "libdshnode.so")
        val engine = EnginePaths.engineRoot(ctx)
        val binJs = File(engine, "node_modules/@deepseek-ai/dsh/lib/bin.js")
        val home = EnginePaths.dshHome(ctx)

        if (!nodeBin.exists()) { fail("缺少 libdshnode.so", onStatus); return }
        if (!binJs.exists()) { fail("引擎缺失: ${binJs.path}", onStatus); return }

        onStatus("启动 web 服务…")
        // ⚠ 用 setsid 脱离进程组。
        //
        // Android 的 force-stop / 杀包会按 uid 清理**同 uid 的所有进程**，
        // 包括引擎子进程。所以「App 退出但引擎继续跑」在普通 spawn 下做不到。
        //
        // setsid 让引擎进入**新会话**，PPID 变成 1（被 init 收养），
        // 实测 App 被 force-stop 后它仍然存活（验证过：setsid sleep 200
        // 在 force-stop 后 PPID=1 依然在跑）。
        //
        // 于是引擎成了真正的常驻服务：App 退出它继续跑，
        // 下次打开 App 直接接管，0 等待 —— 这是方案 B 的核心。
        val cmd = listOf(
            "/system/bin/setsid",
            nodeBin.absolutePath, "--expose-internals", binJs.absolutePath,
            "--profile", "web", "--port", WEB_PORT.toString(), "--no-open"
        )
        val pb = ProcessBuilder(cmd)
        pb.directory(engine)
        pb.environment()["DSH_HOME"] = home.absolutePath
        pb.environment()["DSH_NATIVE_DIR"] = ctx.applicationInfo.nativeLibraryDir
        pb.environment()["TZ"] = "Asia/Shanghai"
        // Android 的 os.homedir() 返回 /data（App 无权访问），
        // 目录选择器会因此报 EACCES。改用共享存储作为 HOME。
        val userHome = File("/sdcard/思忆集")
        userHome.mkdirs()
        pb.environment()["HOME"] = userHome.absolutePath
        pb.environment()["USERPROFILE"] = userHome.absolutePath

        // 对齐官方 DSH 的引擎进程环境。
        // 关键一：普通 node 启动时 platform 是 linux 而不是 android，
        // dsh-bash-local 会去找 bash，但 Android 只有 /system/bin/sh，
        // 于是所有 shell 工具都报 spawn bash EACCES。显式指定即可绕开。
        // 关键二：PATH/TMPDIR 缺失会让 ripgrep、glob、mkdtemp 一起失效。
        val tmpDir = File(ctx.cacheDir, "tmp").also { it.mkdirs() }
        pb.environment()["DSH_BASH_SHELL"] = "/system/bin/sh"
        pb.environment()["SHELL"] = "/system/bin/sh"
        pb.environment()["PATH"] = EngineInstaller.binDir(ctx).absolutePath + ":" +
            ctx.applicationInfo.nativeLibraryDir + ":/system/bin:/system/xbin"
        pb.environment()["TMPDIR"] = tmpDir.absolutePath
        // glob/grep 依赖 ripgrep。引擎的 resolveRgPath 有 Android 分支，
        // 但思忆集用普通 node 启动，platform 是 linux，会去走 @vscode/ripgrep，
        // 而那个包没有 android-arm64 版本。显式给 DSH_RG_PATH 绕开。
        pb.environment()["DSH_RG_PATH"] = File(EngineInstaller.binDir(ctx), "rg").absolutePath
        // 用户插件目录：引擎启动时扫描 /sdcard/思忆集/plugins/*.js 动态注册。
        // 有了它，以后加能力只需往目录里丢 .js 文件并重启引擎，不用再出 APK。
        val userPlugins = Capabilities.pluginsDir()
        if (!userPlugins.exists()) userPlugins.mkdirs()
        pb.environment()["DSH_USER_PLUGINS"] = userPlugins.absolutePath

        // 本机网关密钥：不再硬编码在 assets/profile-patch.yml 里（那会进版本库）。
        // 运行时从这里读，由人/脚本预先放好：
        //   /sdcard/思忆集/app/gateway.key
        // 读不到就留空，profile 侧会用占位串，模型调用会 401 ——
        // 但界面会明确提示「未配置密钥」，不会变成莫名的白屏。
        val gwKey = readGatewayKey()
        if (gwKey != null) {
            pb.environment()["SIYI_GATEWAY_KEY"] = gwKey
            stage("gateway key loaded len=" + gwKey.length)
        } else {
            stage("警告：/sdcard/思忆集/app/gateway.key 不存在，模型调用会 401")
        }

        // 设备能力桥：先跑环境自检（结果写 bridge.log，装一次包就能定位断点），
        // 再建 socketpair 把引擎端 fd 号传下去。
        // 失败不影响引擎启动（设备类工具会缺失，但聊天与 shell 仍可用）。
        runCatching { DeviceBridge.envSelfCheck(ctx) }
            .onFailure { stage("envSelfCheck 异常: " + it.message) }
        val bridgeEnv = DeviceBridge.prepare()
        if (bridgeEnv != null) {
            pb.environment()[bridgeEnv.first] = bridgeEnv.second
            stage("bridge " + bridgeEnv.first + "=" + bridgeEnv.second)
        } else {
            stage("bridge 未启用")
        }

        // 最后一道保险：解压和端口检查期间引擎可能已经起来了
        // （看门狗重启、或另一条启动路径），这时绝不能再起第二个。
        if (isRunning()) {
            stage("启动前复查：引擎已在运行，放弃本次启动")
            return
        }

        runCatching {
            stage("starting: " + cmd.joinToString(" "))
            val p = pb.start()
            stage("engine started, alive=" + p.isAlive)
            processRef.set(p)
            // setsid 之后拿不到真实 pid（句柄指向的是 setsid 本身），
            // 所以稍等一下扫 /proc 找到真正的引擎进程再登记。
            Thread {
                Thread.sleep(600)
                val real = findEnginePid()
                if (real > 1) {
                    writePidFile(ctx, real)
                    stage("引擎 pid=" + real + "（已脱离 App 进程）")
                } else {
                    stage("警告：找不到脱离后的引擎 pid")
                }
            }.start()
            if (bridgeEnv != null) DeviceBridge.start(ctx)
            // 挂上监督：引擎崩了要有人知道，否则状态永远停在 READY，
            // UI 会拿着已经死掉的地址反复刷新 → 白屏且不自愈。
            supervise(ctx, p)

            // 读 stdout 抓 token
            val logFile = File(LOG_DIR, "engine-stdout.log").also { it.parentFile?.mkdirs() }
            Thread {
                runCatching {
                    p.inputStream.bufferedReader().use { br: BufferedReader ->
                        br.forEachLine { line ->
                            AppLog.write("engine-stdout.log", line)
                            val m = Regex("token=([A-Za-z0-9_-]+)").find(line)
                            if (m != null) {
                                val url = "http://127.0.0.1:$WEB_PORT/?token=${m.groupValues[1]}"
                                stateRef.set(State(Phase.READY, url = url))
                                restartCount.set(0)   // 成功就绪，重启计数归零
                                onStatus("就绪，可打开界面")
                            }
                        }
                    }
                }
            }.start()

            // 读 stderr 判断失败
            Thread {
                val errLog = File(LOG_DIR, "engine-stderr.log").also { it.parentFile?.mkdirs() }
                runCatching {
                    p.errorStream.bufferedReader().use { br ->
                        br.forEachLine { line ->
                            AppLog.write("engine-stderr.log", line)
                            // 只有「启动失败」才是致命信号；普通的 "Error:" 多半是
                            // 某个插件自己的告警（比如模型渠道 502），拿来判定整体
                            // 失败会把能用的引擎判死，界面就卡在错误页不再重试。
                            if (line.contains("startup failed", true)) {
                                // 已经拿到带 token 的地址就说明服务其实起来了，
                                // 这条失败来自被淘汰的那次尝试，不能覆盖可用状态。
                                if (stateRef.get().phase != Phase.READY) {
                                    stateRef.set(State(Phase.FAILED, error = line.take(200)))
                                    onStatus("启动异常: " + line.take(60))
                                }
                            }
                        }
                    }
                }
            }.start()
        }.onFailure { fail("启动失败: ${it.message}", onStatus) }
    }

    private fun fail(msg: String, onStatus: (String) -> Unit) {
        stage(msg)
        stateRef.set(State(Phase.FAILED, error = msg))
        onStatus(msg)
    }

    // ── 端口与孤儿进程 ──────────────────────────────────────────────────────

    /**
     * 确保端口没人监听。
     *
     * 场景：App 被杀但引擎没死（它是 App 的子进程，PPID 指向 App），
     * 引擎变成孤儿继续占着 3180。新引擎启动会报 listen EADDRINUSE，
     * 连带 web-app 拿不到 host，界面白屏。
     *
     * 找孤儿靠 pid 文件而不是扫 /proc：Android 的 untrusted_app 域读不到
     * 别的进程的 /proc/<pid>/cmdline，扫描结果永远是空。
     */
    private fun freePort(ctx: Context, port: Int): Boolean {
        if (!isPortInUse(port)) { stage("port " + port + " 空闲"); return true }
        stage("port " + port + " 被占用，尝试清理")
        // ⚠ 常驻引擎时代，pid 文件里登记的可能正是**正在提供服务**的那个引擎，
        // 杀掉它等于自己掐断自己。所以先看端口：端口还在监听说明它活着，
        // 不能动；只有「进程在但端口不响应」才是该清的残留。
        val orphans = readPidFile(ctx).filter { pid ->
            if (pid == pidOf(processRef.get())) return@filter false
            // 端口在监听 → 有引擎在服务，动它就是自断
            if (isListening()) return@filter false
            // 端口没人听但进程还在 → 残留，杀掉
            !isAlive(pid)
        }
        stage("port " + port + " pid文件里的引擎: " + readPidFile(ctx).joinToString(",")
            + " 待清理: " + orphans.joinToString(","))
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
        clearPidFile(ctx)
        // 等端口释放，最多 5 秒
        for (i in 0 until 50) {
            if (!isPortInUse(port)) { stage("port " + port + " 已释放"); return true }
            Thread.sleep(100)
        }
        stage("port " + port + " 仍被占用")
        return false
    }

    /**
     * 端口是否真的被**监听**。
     *
     * 不能只看「能不能 bind 成功」：刚杀掉的引擎会留下 TIME_WAIT 连接，
     * 那种状态下 bind 会失败（EADDRINUSE），但端口已经没人监听了。
     * 于是判定「仍被占用」→ 放弃启动 → 界面白屏。
     *
     * 正确做法：读 /proc/net/tcp{,6}，只看状态为 LISTEN(0A) 的行。
     */
    /**
     * 读本机网关密钥。放共享存储而不是 assets，避免密钥进版本库。
     * 只取第一行并去掉空白，兼容用户用文本编辑器保存的情况。
     */
    private fun readGatewayKey(): String? = runCatching {
        // 优先读私有目录：共享存储里任何 App 都能读，密钥不该放那儿。
        // 私有目录里的文件在「清除应用数据」时会一起没了，所以同时兼容
        // 共享存储的老位置（读到了就顺手搬过来）。
        val priv = File(ctxFiles(), "gateway.key")
        if (priv.exists()) {
            val v = priv.readText().lineSequence().firstOrNull()?.trim()
            if (!v.isNullOrEmpty()) return v
        }
        val shared = File("/sdcard/思忆集/app/gateway.key")
        if (!shared.exists()) return null
        val v = shared.readText().lineSequence().firstOrNull()?.trim()
        if (v.isNullOrEmpty()) return null
        // 迁移到私有目录
        runCatching {
            priv.parentFile?.mkdirs()
            priv.writeText(v + "\n")
            shared.delete()
            AppLog.write("boot.log", "网关密钥已迁移到私有目录")
        }
        v
    }.getOrNull()

    /** App 私有目录。 */
    private fun ctxFiles(): java.io.File = appCtx?.filesDir
        ?: java.io.File("/data/data/com.siyi.dshapp/files")

    /**
     * 扫描 /proc 找出真正的引擎进程 pid。
     *
     * setsid 之后 ProcessBuilder 拿到的句柄指向 setsid 自己，
     * 而 setsid 会立刻 exec 成引擎 —— 但 Java 侧拿不到 exec 后的 pid。
     * 只能按 cmdline 特征找：同时包含 libdshnode.so、bin.js 和 --port 3180。
     */
    private fun findEnginePid(): Int = runCatching {
        File("/proc").listFiles()
            ?.filter { it.isDirectory && it.name.all(Char::isDigit) }
            ?.firstOrNull { dir ->
                val cmd = runCatching { File(dir, "cmdline").readText() }.getOrNull() ?: return@firstOrNull false
                cmd.contains("libdshnode.so") && cmd.contains("bin.js") && cmd.contains("--port " + WEB_PORT)
            }
            ?.name?.toIntOrNull() ?: -1
    }.getOrDefault(-1)

    /** 引擎的 HTTP 端口是否还在响应（连接能否建立）。与 ROM 实现无关，必定有效。 */
    private fun isListening(): Boolean = runCatching {
        java.net.Socket().use { sock ->
            sock.connect(java.net.InetSocketAddress("127.0.0.1", WEB_PORT), 1500)
            true
        }
    }.getOrDefault(false)

    private fun isPortInUse(port: Int): Boolean {
        val hex = port.toString(16).padStart(4, '0').uppercase()
        for (f in listOf("/proc/net/tcp", "/proc/net/tcp6")) {
            var listening = false
            runCatching {
                File(f).forEachLine { line ->
                    val parts = line.trim().split(WHITESPACE)
                    if (parts.size < 4) return@forEachLine
                    if (!parts[1].endsWith(":" + hex)) return@forEachLine
                    if (parts[3] == "0A") listening = true
                }
            }
            if (listening) return true
        }
        return false
    }

    // ── pid 文件 ────────────────────────────────────────────────────────────

    private fun pidFile(ctx: Context) = File(ctx.filesDir, "engine.pid")

    private fun readPidFile(ctx: Context): List<Int> = runCatching {
        if (!pidFile(ctx).exists()) return emptyList()
        pidFile(ctx).readText()
            .split("\n".toRegex())
            .mapNotNull { it.trim().toIntOrNull() }
            .filter { it > 1 }
    }.getOrDefault(emptyList())

    private fun writePidFile(ctx: Context, pid: Int) {
        if (pid <= 1) return
        runCatching {
            val all = (readPidFile(ctx) + pid).distinct()
            pidFile(ctx).writeText(all.joinToString("\n"))
        }
    }

    private fun clearPidFile(ctx: Context) {
        runCatching { pidFile(ctx).delete() }
    }

    // ── 杂项 ────────────────────────────────────────────────────────────────

    /**
     * 取子进程 pid。
     *
     * 先试 Process.pid()（API 33+ / Java 9+ 有），拿不到就扫 /proc：
     * 找 PPID 等于本进程、且 cmdline 里带 libdshnode.so 的那个。
     * 都失败返回 -1 —— 调用方必须能容忍 -1（不要拿它做唯一判据）。
     */
    private fun pidOf(p: Process?): Int {
        if (p == null) return -1
        val direct = runCatching {
            val m = Process::class.java.getMethod("pid")
            (m.invoke(p) as Number).toInt()
        }.getOrDefault(-1)
        if (direct > 1) return direct
        return runCatching {
            val parent = android.os.Process.myPid().toString()
            File("/proc").listFiles()
                ?.filter { it.isDirectory && it.name.all(Char::isDigit) }
                ?.firstOrNull { dir ->
                    val stat = runCatching { File(dir, "stat").readText() }.getOrNull() ?: return@firstOrNull false
                    // stat: pid (comm) state ppid ...
                    val ppid = stat.substringAfter(") ").split(" ").getOrNull(1)?.trim()
                    if (ppid != parent) return@firstOrNull false
                    val cmd = runCatching { File(dir, "cmdline").readText() }.getOrNull() ?: return@firstOrNull false
                    cmd.contains("libdshnode.so")
                }
                ?.name?.toIntOrNull() ?: -1
        }.getOrDefault(-1)
    }

    /** 进程是否还活着。 */
    private fun isAlive(pid: Int): Boolean =
        runCatching { File("/proc/" + pid).exists() }.getOrDefault(false)

    /** 启动阶段诊断，追加到 /sdcard/思忆集/app/boot.log。 */
    private fun stage(msg: String) {
        runCatching {
            AppLog.write("boot.log", msg)
        }
    }


    // ── 监督：崩溃感知 + 超时 + 自动重启 ─────────────────────────────────────

    /**
     * 盯住引擎进程。
     *
     * 之前引擎起完就没人管了：它被系统杀掉或自己崩掉之后，
     * stateRef 永远停在 READY，UI 继续用旧 token 刷页面 → 白屏且不会自愈。
     *
     * 现在做三件事：
     *   1. 轮询进程是否还活着，死了立刻把状态改成 FAILED（UI 会显示等待并重试）
     *   2. 启动超时：长时间拿不到 token 也判失败，不让用户无限等
     *   3. 非主动停止的情况下自动重启，带指数退避和次数上限
     */
    private fun supervise(ctx: Context, p: Process) {
        // 启动超时看门狗
        Thread {
            val deadline = System.currentTimeMillis() + START_TIMEOUT_MS
            while (System.currentTimeMillis() < deadline) {
                if (!p.isAlive()) return@Thread          // 已退出，交给下面的死亡检测
                if (stateRef.get().phase == Phase.READY) return@Thread  // 已就绪
                Thread.sleep(1000)
            }
            // 只有「当前这个进程」仍代表引擎时才判超时。
            // 否则上一轮的超时看门狗会误伤重启后的新进程。
            if (processRef.get() === p && stateRef.get().phase == Phase.STARTING) {
                stage("启动超时（" + (START_TIMEOUT_MS / 1000) + "s 未就绪），判定失败")
                stateRef.set(State(Phase.FAILED, error = "引擎启动超时，正在重试…"))
                statusSink?.invoke("引擎启动超时，正在重试…")
            }
        }.apply { isDaemon = true; start() }

        // 存活看门狗：用 HTTP 探测，不用 Process 的任何方法。
        //
        // 为什么：Android 的 Process.waitFor() / isAlive() 都依赖一个内部
        // 「退出通知」线程（ProcessManager.waitForProcessExit）。实测在这台机器上
        // 那个线程不工作，引擎被 kill -9 之后 waitFor 永久阻塞、isAlive 永远为 true，
        // 看门狗一声不响，白屏永远不会自愈。
        // 引擎的职责就是提供 HTTP 服务，端口不响应 == 它已经没用了，直接判死。
        Thread {
            stage("看门狗已挂上（HTTP 探测）")
            var fails = 0
            val spawnedAt = System.currentTimeMillis()
            while (!stopping.get()) {
                Thread.sleep(2000)
                val listening = isListening()
                val phase = stateRef.get().phase
                stage("探测 listening=" + listening + " phase=" + phase + " fails=" + fails)
                if (listening) { fails = 0; continue }
                if (phase != Phase.READY && System.currentTimeMillis() - spawnedAt < 30000L) {
                    fails = 0
                    continue
                }
                fails++
                stage("HTTP 探测失败 " + fails + "/5")
                if (fails < 5) continue

                stage("引擎无响应（连续5次），判定已退出")
                // setsid 之后 p 是 setsid 的句柄，杀它没用；
                // 要按 pid 文件里登记的真实引擎 pid 清理，否则残留进程会占着端口。
                val c0 = appCtx ?: ctx
                val real = findEnginePid()
                if (real > 1) {
                    stage("清理无响应的引擎 pid=" + real)
                    runCatching { android.os.Process.killProcess(real) }
                }
                clearPidFile(c0)
                processRef.set(null)
                stateRef.set(State(Phase.FAILED, error = "引擎无响应，正在重启…"))
                statusSink?.invoke("引擎无响应，正在重启…")
                break
            }
            if (stopping.get()) { stage("主动停止，不做重启"); return@Thread }
            if (fails < 5) return@Thread

            val c = appCtx ?: ctx
            val n = restartCount.incrementAndGet()
            if (n > MAX_RESTARTS) {
                stage("已连续重启 " + n + " 次，停止自动重启")
                stateRef.set(State(Phase.FAILED, error = "引擎反复退出，请手动重启应用"))
                statusSink?.invoke("引擎反复退出，请手动重启应用")
                return@Thread
            }
            // 指数退避：1s, 2s, 4s
            val delayMs = 1000L shl (n - 1)
            stage("第 " + n + " 次自动重启，等待 " + delayMs + "ms")
            Thread.sleep(delayMs)
            if (stopping.get()) { stage("等待期间被主动停止，放弃重启"); return@Thread }
            ensureRunning(c, statusSink ?: {})
        }.apply { isDaemon = true; start() }
    }

    /** 空白分隔的正则（/proc/net/tcp 每列之间是空格）。 */
    private val WHITESPACE = Regex("\\s+")
}
