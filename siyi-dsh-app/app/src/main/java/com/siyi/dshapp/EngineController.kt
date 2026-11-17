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

    /** 用户覆盖层：放 sdcard，覆盖安装不会被动。 */
    private val USER_PATCH = File("/sdcard/思忆集/config/user-patch.yml")

    /** 本次启动是否真的带上了 --patch（只有带上才可能因为用户层坏掉而起不来）。 */
    private val patchActive = AtomicBoolean(false)

    /** 用户层被判死过一次之后，本进程生命周期内不再加载，避免反复启动反复失败。 */
    private val userPatchDisabled = AtomicBoolean(false)

    /** 启动超时：从拉起进程到打出带 token 地址的最长等待。 */
    private const val START_TIMEOUT_MS = 180_000L

    // ── 存活判定参数（288 版重做，说明见文件末 supervise）─────────────────
    //
    // 286 及之前是「连续 5 次探测失败就判死」，间隔 2 秒 —— 只有 6 秒。
    // 实测这台机器上引擎冷启动要 30 秒以上（会话越大越久：当前会话
    // 7.5MB 压缩 / 24.5MB 解压 / 一万多条事件，恢复要重新解压加建索引），
    // 于是启动保护期一过就开始计数，10 秒内必被判死，形成
    // 「启动 → 误判 → 杀掉 → 再启动」的死循环。用户看到的就是界面
    // 突然「任务已停止」，然后自己刷新重进。
    //
    // 现在把三种情况分开：
    //   1. 进程没了（/proc/<pid> 消失）→ 真死，立刻重启；
    //   2. 进程还在、CPU 还在涨       → 在干活，继续等；
    //   3. 进程还在、CPU 不动、端口不通 → 才怀疑卡死，且要等够久。
    /** 探测间隔。 */
    private const val PROBE_INTERVAL_MS = 2000L

    /** 单次 TCP 连接超时。本机回环 800ms 足够。 */
    private const val PROBE_TIMEOUT_MS = 800

    /** 启动保护期：这段时间端口不通只记日志、绝不计数。 */
    private const val START_GRACE_MS = 90_000L

    /** 端口持续不通多久才允许考虑重启（进程已消失时不受此限）。 */
    private const val UNREACHABLE_JUDGE_MS = 20_000L

    /** 进程还在但 CPU 长期不动时的最长容忍时间，超过才动手杀。 */
    private const val HARD_DEAD_MS = 60_000L

    /**
     * 稳定多久才算「这次启动真的成功」。
     *
     * 之前一拿到 token 就把 restartCount 清零，于是「起来 → 崩 → 再起来 → 再崩」
     * 的抖动永远攒不到 MAX_RESTARTS，重启上限形同虚设
     * （实测 20:39 与 20:42 两轮风暴里计数每次都是从 1 重新开始）。
     * 改成连续稳定满这么久才清零，抖动就会被计入上限并最终停下来。
     */
    private const val STABLE_RESET_MS = 60_000L

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
        // ⚠ 必须在这里也重放补丁（2026-11-17 实测发现）：
        // ensureAll/PatchOverlay 只在 boot()（冷启动）里调用，
        // 而常驻引擎走的是 adoptExistingEngine 这条路，完全绕开了它。
        // 结果是：用户改了 /sdcard/思忆集/patches/ 里的补丁，
        // 只要引擎没被真正重启，补丁就永远不生效 ——
        // 表现和「补丁没写对」一模一样，极难分辨。
        //
        // 补丁是纯文件覆盖，不影响正在运行的引擎进程（模块已经加载进内存），
        // 只影响引擎下次启动时读到的文件，所以这里重放是安全的。
        runCatching { PatchOverlay.apply(ctx) }
            .onFailure { stage("接管后重放补丁失败: " + (it.message ?: "")) }
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
        //
        // 注意要扫出**全部**引擎再逐个杀：异常重启可能留下多个，
        // 只杀一个会剩下残余进程占着 3180，下次启动报 EADDRINUSE。
        val all = findMainEnginePids()
        val real = all.firstOrNull() ?: findEnginePid()
        for (pid in all) {
            stage("停止引擎 pid=" + pid)
            runCatching { android.os.Process.killProcess(pid) }
        }
        if (real > 1) {
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
        // ── 用户覆盖层 ──────────────────────────────────────────────────────
        // 叠加顺序是「bundle 层 -> profile 的 cordis.patch.yml -> --patch 覆盖层」，
        // 所以这里能覆盖 APK 内置的默认模型，且优先级最高；文件在 /sdcard，
        // 覆盖安装不会被动，配置因此能跨版本留存。
        //
        // ⚠ 两个坑，285 版就是死在这里（装完直接打不开）：
        //
        // 坑一：位置。dsh 启动器只认自己那批 flag，**第一个不认识的 token 之后
        //   全部原样交给内层应用**。所以 --patch 必须写在 --port/--no-open 之前；
        //   写成 `--port 3180 --no-open --patch x.yml` 会被 web 应用当成它自己的参数，
        //   报 `error: unknown option '--patch'` 立即退出 —— 界面永远白屏。
        //
        // 坑二：内容。APK 内的 profile-patch.yml 已经挂好了 provider 和全部插件，
        //   覆盖层再 insert 一遍会出现同 id 的第二份条目，引擎以
        //   「invalid config / startup failed」整树启动失败。所以只允许「整行覆盖」。
        //
        // 兜底：静态校验不过就跳过；过了还是起不来，看门狗会把文件改名成
        //   .rejected 并在不带它的情况下重启，保证不会出现「装完打不开」。
        val patchArgs = mutableListOf<String>()
        if (!userPatchDisabled.get() && USER_PATCH.isFile) {
            val reject = userPatchRejectReason(USER_PATCH)
            if (reject != null) {
                stage("跳过 user-patch.yml：" + reject + "，本次只用 APK 内置配置")
            } else {
                patchArgs.add("--patch")
                patchArgs.add(USER_PATCH.absolutePath)
                patchActive.set(true)
                stage("使用 user-patch.yml: " + USER_PATCH.absolutePath)
            }
        }

        val cmd = mutableListOf<String>()
        cmd.add("/system/bin/setsid")
        cmd.add(nodeBin.absolutePath)
        cmd.add("--expose-internals")
        cmd.add(binJs.absolutePath)
        // ↓ 以下是启动器自己的 flag，必须排在任何内层参数之前
        cmd.add("--profile"); cmd.add("web")
        cmd.addAll(patchArgs)
        // ↓ 以下两个属于内层 web 应用，一旦出现，后面的东西它全收
        cmd.add("--port"); cmd.add(WEB_PORT.toString())
        cmd.add("--no-open")
        val pb = ProcessBuilder(cmd)
        pb.directory(engine)
        // stdout/stderr 直接重定向到文件，而不是靠 App 进程去读管道。
        //
        // 常驻引擎的关键问题：之前 App 进程负责读引擎的 stdout/stderr，
        // 一旦那个 App 进程死了（切出、被回收），管道就没人读了，
        // 引擎再往里写会阻塞；新 App 进程接管后也没有管道可读，
        // engine-stderr.log 直接断掉 —— 登录失败这类线索就没了。
        //
        // 重定向到文件后，日志由引擎自己写，跨进程、跨会话都在。
        val outFile = File(LOG_DIR, "engine-stdout.log").also { it.parentFile?.mkdirs() }
        val errFile = File(LOG_DIR, "engine-stderr.log").also { it.parentFile?.mkdirs() }
        pb.redirectOutput(outFile)
        pb.redirectError(errFile)
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

            // 轮询 stdout 文件找 token。
            //
            // 不能用 bufferedReader().forEachLine()：那是「读一遍就到头」的快照，
            // 而引擎是**追加写**这个文件，读完之后新追加的内容永远读不到，
            // 于是 180 秒超时判定失败 —— 这正是刚把 App 改坏的那一版。
            // 也不能再 AppLog.write 回同一文件，那会自我追加成死循环。
            watchStdoutForToken()
            watchStderrForFailure()
        }.onFailure { fail("启动失败: ${it.message}", onStatus) }
    }

    /**
     * 轮询 stdout 文件找带 token 的地址。
     *
     * 引擎启动时会把 stdout 重定向到这个文件。每次只读 pos 之后的新增内容，
     * 所以既抓得到 token，也不会重复处理。
     */
    private fun watchStdoutForToken() {
        Thread {
            val f = File(LOG_DIR, "engine-stdout.log")
            f.parentFile?.mkdirs()
            var pos = 0L
            var elapsed = 0L
            while (!stopping.get() && elapsed < START_TIMEOUT_MS) {
                Thread.sleep(500)
                elapsed += 500
                if (stateRef.get().phase == Phase.READY) return@Thread
                runCatching {
                    if (!f.exists()) return@runCatching
                    val len = f.length()
                    if (len < pos) pos = 0
                    if (len <= pos) return@runCatching
                    java.io.RandomAccessFile(f, "r").use { raf ->
                        raf.seek(pos)
                        val buf = ByteArray((len - pos).toInt().coerceAtMost(1 shl 20))
                        val n = raf.read(buf)
                        if (n <= 0) return@runCatching
                        pos += n
                        val chunk = String(buf, 0, n, Charsets.UTF_8)
                        val m = Regex("token=([A-Za-z0-9_-]+)").find(chunk)
                        if (m != null) {
                            val url = "http://127.0.0.1:" + WEB_PORT + "/?token=" + m.groupValues[1]
                            stateRef.set(State(Phase.READY, url = url))
                            // ⚠ 这里**不再**把 restartCount 清零。
                            // 之前一拿到 token 就清零，导致「起来→崩→再起来→再崩」
                            // 这种抖动永远攒不到 MAX_RESTARTS，重启上限形同虚设
                            // （实测 20:39 与 20:42 两轮风暴里计数每次都从 1 重新开始）。
                            // 现在改由存活看门狗在「连续稳定 60 秒」之后清零。
                            statusSink?.invoke("就绪，可打开界面")
                            stage("从 stdout 抓到 token，状态置为 READY")
                        }
                    }
                }
            }
        }.apply { isDaemon = true; start() }
    }

    /** 轮询 stderr 文件，只看「启动失败」这类致命信号。 */
    private fun watchStderrForFailure() {
        Thread {
            val f = File(LOG_DIR, "engine-stderr.log")
            f.parentFile?.mkdirs()
            var pos = 0L
            while (!stopping.get()) {
                Thread.sleep(1000)
                runCatching {
                    if (!f.exists()) return@runCatching
                    val len = f.length()
                    if (len < pos) pos = 0
                    if (len <= pos) return@runCatching
                    java.io.RandomAccessFile(f, "r").use { raf ->
                        raf.seek(pos)
                        val buf = ByteArray((len - pos).toInt().coerceAtMost(1 shl 20))
                        val n = raf.read(buf)
                        if (n <= 0) return@runCatching
                        pos += n
                        val chunk = String(buf, 0, n, Charsets.UTF_8)
                        // ── 用户层自动降级（288 收紧）──────────────────────
                        //
                        // 286 的写法「只要 stderr 出现 startup failed / failed to parse
                        // 就把 user-patch.yml 改名 .rejected」是一个**误伤源**：
                        // 实测 20:38 那次引擎其实正在正常启动（11:38:41 端口已 listening=true），
                        // 1 秒后就被这段逻辑判死、把一份完全合法的配置改名成了 .rejected。
                        // 结果是用户配置被丢掉、默认模型回退，而且用户根本不知道为什么。
                        //
                        // 现在只保留一个**确定性**信号：引擎明确说它不认识 --patch。
                        // 那才是「参数位置错了」的铁证，且与配置文件内容无关。
                        // 配置内容导致的启动失败交给下面「引擎确实没起来」的判断去处理，
                        // 不再靠字符串猜。
                        if (patchActive.get() && chunk.contains("unknown option", true)) {
                            disableUserPatch("引擎不认 --patch 参数")
                        }
                        if (chunk.contains("startup failed", true) && stateRef.get().phase != Phase.READY) {
                            stateRef.set(State(Phase.FAILED, error = "引擎启动失败"))
                            statusSink?.invoke("引擎启动失败，正在重试…")
                        }
                    }
                }
            }
        }.apply { isDaemon = true; start() }
    }

    /**
     * 静态校验用户覆盖层。返回 null 表示可用，否则返回拒绝原因。
     *
     * 只挡已知的两种「装完打不开」，不做完整 YAML 解析：
     *   1. 能读、非空；
     *   2. 顶层是数组（引擎要求 patch 文件是 `- ...` 列表，否则直接抛错退出）；
     *   3. 不含 insert: / cordis:group —— 这会把已经存在的条目再挂一遍。
     * 真正的语法错误交给引擎报错，再由看门狗降级（改名 + 不带它重启）。
     */
    private fun userPatchRejectReason(f: File): String? {
        val text = runCatching { f.readText() }.getOrNull() ?: return "读不出来"
        if (text.isBlank()) return "空文件"
        val firstLine = text.lineSequence()
            .map { it.substringBefore("#").trim() }
            .firstOrNull { it.isNotEmpty() } ?: return "只有注释"
        if (!firstLine.startsWith("-")) return "顶层必须是 YAML 数组（以 - 开头）"
        val bad = text.lineSequence()
            .map { it.substringBefore("#").trim() }
            .firstOrNull { it.startsWith("insert:") || it.contains("cordis:group") }
        if (bad != null) return "含 insert / cordis:group，会造成条目重复（" + bad + "）"
        return null
    }

    /**
     * 判死用户覆盖层：改名保全现场，下次启动不再带它。
     * 只在本次启动确实带过 --patch 时动作，所以不会误伤正常配置。
     */
    private fun disableUserPatch(reason: String) {
        if (!patchActive.compareAndSet(true, false)) return
        userPatchDisabled.set(true)
        stage("user-patch.yml 导致引擎启动失败（" + reason + "）：改名 .rejected，改为只用 APK 内置配置")
        runCatching {
            val bak = File(USER_PATCH.absolutePath + ".rejected")
            if (bak.exists()) bak.delete()
            if (USER_PATCH.isFile && !USER_PATCH.renameTo(bak)) {
                stage("警告：改名为 .rejected 失败，目录不可写")
            }
        }
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
        // ⚠ 不能只靠读 /proc/<pid>/cmdline：untrusted_app 域读不到别的进程的，
        //   实测这里扫出来是空的。退路是 /proc/<pid>/exe 这个软链 ——
        //   它的目标就是 libdshnode.so，同样能认出引擎，而且软链目标通常可读。
        File("/proc").listFiles()
            ?.filter { it.isDirectory && it.name.all(Char::isDigit) }
            ?.firstOrNull { dir ->
                val pid = dir.name.toIntOrNull() ?: return@firstOrNull false
                if (pid == android.os.Process.myPid()) return@firstOrNull false
                val exe = runCatching {
                    java.nio.file.Files.readSymbolicLink(File(dir, "exe").toPath()).toString()
                }.getOrNull()
                if (exe != null && exe.contains("libdshnode.so")) return@firstOrNull true
                // 兜底：cmdline 能读就读它（有些 ROM 对同 uid 放宽）
                // ⚠ 参数之间是 NUL 不是空格，必须先替换，否则 "--port 3180" 永远匹配不上。
                val cmd = runCatching { File(dir, "cmdline").readText() }.getOrNull()
                    ?.replace('\u0000', ' ') ?: return@firstOrNull false
                cmd.contains("bin.js") && cmd.contains("--port " + WEB_PORT)
            }
            ?.name?.toIntOrNull() ?: -1
    }.getOrDefault(-1)

    /** 引擎的 HTTP 端口是否还在响应（连接能否建立）。与 ROM 实现无关，必定有效。 */
    private fun isListening(): Boolean = runCatching {
        java.net.Socket().use { sock ->
            sock.connect(java.net.InetSocketAddress("127.0.0.1", WEB_PORT), PROBE_TIMEOUT_MS)
            true
        }
    }.getOrDefault(false)

    /**
     * 判断某个 pid 是不是「主引擎进程」。
     *
     * 为什么要按 cmdline 再筛一遍：引擎跑起来之后会派生短命子进程，
     * 它们的 exe 同样指向 libdshnode.so。如果只看 exe，
     * findEnginePid 可能扫到一个马上退出的子进程，看门狗就会误判「引擎已死」。
     * 主进程的特征是 cmdline 里同时有 bin.js、--profile 和 --port <我们的端口>。
     *
     * ⚠ /proc/<pid>/cmdline 的参数之间是 **NUL 字节**，不是空格。
     * 实测原始内容是
     *   ...libdshnode.so\0--expose-internals\0...bin.js\0--profile\0web\0--port\03180\0--no-open\0
     * 所以直接 contains("--port 3180") 永远匹配不上（这是个真实踩过的坑）。
     * 这里先把 NUL 换成空格再比对。
     * 另外有些实现会把末尾的 NUL 也写进去，split 之后会有空串，用 filter 去掉。
     *
     * cmdline 读不到时不能直接判「不是」：某些 ROM 收紧 /proc 权限，
     * 读不到是权限问题而不是进程不存在。那种情况下退回看 exe 软链，
     * 宁可误认为「活着」（继续观察），也不能误判「已死」（会误杀）。
     */
    private fun isMainEngine(pid: Int): Boolean {
        val raw = runCatching { File("/proc/$pid/cmdline").readText() }.getOrNull()
        val cmd = raw?.replace('\u0000', ' ')?.trim()
        if (!cmd.isNullOrEmpty()) {
            return cmd.contains("bin.js") && cmd.contains("--profile") && cmd.contains("--port " + WEB_PORT)
        }
        // cmdline 不可读：只要求 exe 指向 libdshnode.so
        return runCatching {
            java.nio.file.Files.readSymbolicLink(File("/proc/$pid/exe").toPath())
                .toString().contains("libdshnode.so")
        }.getOrDefault(false)
    }

    /** 指定 pid 是否还活着且仍是主引擎。 */
    private fun enginePidAlive(pid: Int): Boolean =
        pid > 1 && File("/proc/$pid").exists() && isMainEngine(pid)

    /**
     * 找「主引擎进程」的 pid，找不到返回 -1。
     *
     * 与 findEnginePid 的区别：这里坚持用 cmdline 特征
     * （同时含 bin.js、--profile、--port <我们的端口>）再确认一遍。
     * findEnginePid 只看 exe 指向 libdshnode.so，会匹配到引擎派生的短命子进程；
     * 看门狗拿它当存活判据就会误判「引擎已死」。
     * cmdline 读不到时返回 -1，由调用方按「未知」处理（继续等，不杀）。
     */
    private fun findMainEnginePid(): Int = findMainEnginePids().firstOrNull() ?: -1

    /** 找出全部主引擎进程的 pid（异常重启可能留下多个）。 */
    private fun findMainEnginePids(): List<Int> = runCatching {
        File("/proc").listFiles()
            ?.filter { it.isDirectory && it.name.all(Char::isDigit) }
            ?.mapNotNull { it.name.toIntOrNull() }
            ?.filter { pid ->
                pid != android.os.Process.myPid() && enginePidAlive(pid)
            }
            ?: emptyList()
    }.getOrDefault(emptyList())

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

    /**
     * 读一个进程累计占用的 CPU 时间（jiffies，utime + stime）。
     *
     * 用途：区分「引擎在干活」和「引擎僵住」。
     * 解压大会话、建索引时 CPU 一直在涨；真卡死的进程 CPU 是停的。
     * 同 uid 的 /proc/<pid>/stat 实测可读，读不到就返回 -1（当作未知，保守不杀）。
     *
     * stat 的格式是「pid (comm) state ppid ...」，comm 里可能带空格和括号，
     * 所以必须从最后一个 ')' 之后开始切分，不能直接 split(" ")。
     */
    private fun cpuTicks(pid: Int): Long = runCatching {
        if (pid <= 1) return -1L
        val raw = File("/proc/$pid/stat").readText()
        val tail = raw.substringAfterLast(") ")
        val fields = tail.trim().split(WHITESPACE)
        // tail 从 state(第3列) 开始：state=0, ppid=1, ..., utime=11, stime=12
        val utime = fields.getOrNull(11)?.toLongOrNull() ?: return -1L
        val stime = fields.getOrNull(12)?.toLongOrNull() ?: return -1L
        utime + stime
    }.getOrDefault(-1L)

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
        // 启动超时看门狗。
        //
        // ⚠ 不能再用 p.isAlive()：这台机器上它永远返回 true（见下方存活看门狗注释），
        // 于是「引擎早就崩了」也要空等到 180 秒。改成看真实 pid 是否还在 /proc 里。
        // 另外这里只改状态、不杀进程 —— 杀进程是存活看门狗的职责，
        // 两边都动手会互相打架（一边判超时、一边刚重启完）。
        Thread {
            val startedAt = System.currentTimeMillis()
            val deadline = startedAt + START_TIMEOUT_MS
            var seen = false
            while (System.currentTimeMillis() < deadline) {
                if (stateRef.get().phase == Phase.READY) return@Thread  // 已就绪
                val real = findMainEnginePid()
                if (real > 1) {
                    seen = true
                } else if (seen) {
                    // 锁定过之后又消失，说明确实退出了；交给存活看门狗立刻重启，
                    // 不必在这里空等到 180 秒。
                    stage("启动超时看门狗：主引擎进程已消失，提前结束等待")
                    return@Thread
                } else if (System.currentTimeMillis() - startedAt > START_GRACE_MS) {
                    // 保护期都过了还一次没扫到，视为启动失败。
                    stage("启动超时看门狗：保护期内始终未出现主引擎进程")
                    return@Thread
                }
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
        // ── 存活看门狗（288 重做）──────────────────────────────────────────
        //
        // 为什么不再用 Process 的任何方法：Android 的 Process.waitFor() /
        // isAlive() 都依赖内部「退出通知」线程（ProcessManager.waitForProcessExit），
        // 实测这台机器上那个线程不工作，引擎被 kill -9 之后 waitFor 永久阻塞、
        // isAlive 永远为 true，看门狗一声不响，白屏不会自愈。所以判据是
        // HTTP 端口 + /proc/<pid> 是否存在。
        //
        // 286 及之前的算法有一个致命缺陷：只要端口不通就累加计数，
        // 连续 5 次（2 秒一次，仅 6 秒）就判死并 kill。而引擎冷启动、
        // 解压大会话、建索引都要几十秒，于是「启动中」被当成「已死」，
        // 杀掉再启动、启动再被杀，界面上表现为突然停止并自行刷新。
        //
        // 现在的判据（互相独立）：
        //   A. 进程消失（/proc/<pid> 不存在）      → 真死，立即重启，不等；
        //   B. 进程在、CPU 在涨                     → 在干活，不判死；
        //   C. 进程在、CPU 不动、端口不通           → 先等 20 秒，
        //      仍不通且 CPU 停滞累计超过 60 秒才动手杀。
        // 启动保护期从 30 秒放宽到 90 秒，保护期内完全不计数。
        Thread {
            stage("看门狗已挂上（HTTP 探测 + /proc 存活 + CPU 活跃度）")
            val spawnedAt = System.currentTimeMillis()
            var unreachableSince = 0L          // 端口开始不通的时刻，0 表示当前通
            var lastCpu = -1L                  // 上一次读到的 CPU 累计时间
            var lastCpuChangeAt = spawnedAt    // CPU 最后一次变化的时刻
            var judged = false                 // 是否已判死
            var sawProcess = false             // 是否成功锁定过引擎 pid
            var trackedPid = -1                // 锁定后的主引擎 pid，-1 表示尚未锁定
            var missedPidProbes = 0            // 连续扫不到主引擎 pid 的次数
            var stableSince = 0L               // 端口持续稳定的起点，0 表示当前不稳定

            while (!stopping.get()) {
                Thread.sleep(PROBE_INTERVAL_MS)
                val now = System.currentTimeMillis()
                val phase = stateRef.get().phase
                val listening = isListening()

                // 先看有没有锁定主引擎 pid。锁定之后只认这一个，
                // 免得被引擎派生的短命子进程（exe 同样指向 libdshnode.so）带偏。
                if (trackedPid <= 1) trackedPid = findMainEnginePid()
                var real = trackedPid
                if (real > 1 && !File("/proc/$real").exists()) real = -1

                // ── 情况 A：进程没了 ──────────────────────────────────────
                // setsid 之后 processRef 的句柄很快失效，不能拿它判断，
                // 只能按扫出来的真实 pid 判。
                //
                // ⚠ 三个保守条件，缺一不可，否则会误杀：
                //   1. 端口也必须不通。端口还在响应说明服务活着，
                //      此时「扫不到 pid」只是 /proc 权限或扫描抖动，不算死。
                //   2. 要连续两次都扫不到（missedPidProbes >= 2）。
                //   3. 保护期内从未扫到过就不判死。
                if (real <= 1) {
                    trackedPid = -1
                    if (listening) {
                        missedPidProbes = 0
                        unreachableSince = 0L
                        stage("探测 listening=true 但未锁定 pid，按存活处理")
                        continue
                    }
                    if (!sawProcess && now - spawnedAt < START_GRACE_MS) {
                        stage("探测 尚未锁定引擎 pid（启动 " + ((now - spawnedAt) / 1000) + "s），继续等")
                        continue
                    }
                    missedPidProbes++
                    if (missedPidProbes < 2) {
                        stage("探测 连续第 " + missedPidProbes + " 次扫不到主引擎 pid 且端口不通，再确认一次")
                        continue
                    }
                    stage("探测 listening=$listening phase=$phase 主引擎进程已消失，判定真死")
                    judged = true
                    clearPidFile(appCtx ?: ctx)
                    processRef.set(null)
                    stateRef.set(State(Phase.FAILED, error = "引擎进程已退出，正在重启…"))
                    statusSink?.invoke("引擎进程已退出，正在重启…")
                    break
                }
                sawProcess = true
                missedPidProbes = 0

                // ── 情况 B：端口通 ────────────────────────────────────────
                if (listening) {
                    unreachableSince = 0L
                    lastCpu = cpuTicks(real)
                    lastCpuChangeAt = now
                    // 连续稳定满 60 秒才认为这次启动真的成功，清掉重启计数。
                    // 一 READY 就清零会让「起来又崩」的抖动绕开 MAX_RESTARTS 上限。
                    if (stableSince == 0L) stableSince = now
                    if (restartCount.get() > 0 && now - stableSince >= STABLE_RESET_MS) {
                        restartCount.set(0)
                        stage("引擎已稳定运行 " + (STABLE_RESET_MS / 1000) + "s，重启计数归零")
                    }
                    if (phase == Phase.READY) stage("探测 listening=true phase=READY 正常")
                    continue
                }
                // 端口一旦不通，稳定性重新计时。
                stableSince = 0L

                // ── 端口不通 ──────────────────────────────────────────────
                val inGrace = now - spawnedAt < START_GRACE_MS

                // ⚠ 保护期内把起点一直往后推，不要在保护期里攒时间。
                // 否则 90 秒保护期结束时 downFor 已经是 90 秒，
                // 一次探测就直接越过 HARD_DEAD_MS 把还在启动的引擎杀掉 ——
                // 那正是 286 版的老毛病换了个形式。
                // 这样写的结果是：保护期结束后再重新数 20 秒 / 60 秒。
                if (inGrace) { unreachableSince = now; continue }

                if (unreachableSince == 0L) unreachableSince = now
                val downFor = now - unreachableSince

                // CPU 活跃度：涨了就刷新时间戳。读不到（-1）时保守处理，
                // 当作「有活动」，避免因为读不到 /proc 而误杀。
                val cpu = cpuTicks(real)
                if (cpu < 0 || lastCpu < 0 || cpu != lastCpu) {
                    lastCpuChangeAt = now
                    lastCpu = cpu
                }
                val cpuIdleFor = now - lastCpuChangeAt
                val cpuBusy = cpuIdleFor < PROBE_INTERVAL_MS * 3

                stage(
                    "探测 listening=false phase=$phase 不通=" + (downFor / 1000) + "s" +
                        " cpu变化距今=" + (cpuIdleFor / 1000) + "s" +
                        " cpu=" + cpu
                )

                // 还没到「持续不通」的门槛，继续等。
                if (downFor < UNREACHABLE_JUDGE_MS) continue

                // CPU 还在动：在干活，继续等（大会话恢复会持续几十秒）。
                if (cpuBusy) {
                    stage("端口不通但 CPU 仍在活动，继续等待（已 " + (downFor / 1000) + "s）")
                    continue
                }

                // 到这里才是「端口不通 + CPU 不动」，还要熬满 HARD_DEAD_MS。
                if (downFor < HARD_DEAD_MS) {
                    stage("端口不通且 CPU 停滞，观察中（" + (downFor / 1000) + "s/" + (HARD_DEAD_MS / 1000) + "s）")
                    continue
                }

                // ── 情况 C：确认卡死 ──────────────────────────────────────
                stage("引擎确认卡死（不通 " + (downFor / 1000) + "s，CPU 停滞 " + (cpuIdleFor / 1000) + "s），准备重启")
                runCatching { android.os.Process.killProcess(real) }
                clearPidFile(appCtx ?: ctx)
                processRef.set(null)
                stateRef.set(State(Phase.FAILED, error = "引擎无响应，正在重启…"))
                statusSink?.invoke("引擎无响应，正在重启…")
                judged = true
                break
            }
            if (stopping.get()) { stage("主动停止，不做重启"); return@Thread }
            if (!judged) return@Thread

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
