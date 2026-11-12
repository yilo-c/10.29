package com.siyi.dshapp

import android.app.ActivityManager
import android.content.Context
import android.net.LocalServerSocket
import android.net.LocalSocket
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.os.StatFs
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean

/**
 * DSH 设备能力桥的 App 侧实现。
 *
 * 契约（引擎侧 @dsh-android/plugin-device）：
 *   1. App 用 Os.socketpair 建一对 UNIX socket。
 *   2. 子进程端 fd 清除 CLOEXEC，通过环境变量 DSH_BRIDGE_FD 传下去。
 *   3. 换行分隔的 JSON-RPC 2.0，双向。
 *   4. 引擎发 capability/list 请求，App 返回 tools 数组，元素为 ToolDescriptor。
 *   5. 引擎发 capability/invoke 请求，App 执行并返回 ToolResult。
 *   6. App 可主动发 capability/changed 通知触发重新列工具。
 *
 * ToolDescriptor 字段：name/group/description/inputSchema/risk/needsUnlock/timeoutMs
 * ToolResult 字段：ok、content 文本块数组、error 的 code 与 message。
 */
object DeviceBridge {

    private const val TAG = "DeviceBridge"

    // 引擎侧默认 300s；needsUnlock 时 900s。这里给读操作短一些。
    private const val TIMEOUT_MS = 30_000

    /**
     * 一次桥接会话的所有状态，收在一处。
     *
     * 之前这些字段散落在类里（reader/writer/thread/running/serverSocket/peerSocket），
     * prepare() 和 start() 都能改它们，谁也不知道当前到底连的是哪一套：
     *   - prepare() 每次都新建 socket 名，第二次调用会另起一套，
     *     旧的 serverSocket 泄漏，引擎拿着旧名字去连 → 设备工具全部失联；
     *   - start() 与 prepare() 里的 accept 线程都用 running.compareAndSet 抢读取循环，
     *     但抢到的是「各自的」循环，状态对不上。
     *
     * 现在：会话是唯一的，prepare() 发现已有会话就直接复用。
     */
    private class Session {
        var name: String = ""
        var serverSocket: LocalServerSocket? = null
        var peerSocket: LocalSocket? = null
        var reader: BufferedReader? = null
        var writer: OutputStreamWriter? = null
        var thread: Thread? = null
        /** 读取循环是否在跑。 */
        val running = AtomicBoolean(false)
    }

    /** 当前会话。null = 未建立。 */
    @Volatile private var session: Session? = null

    private val cancelled = ConcurrentHashMap<String, Boolean>()
    private var appCtx: Context? = null

    /**
     * 建立设备桥并返回要交给引擎的环境变量。
     *
     * 为什么用抽象命名空间 socket 而不是 socketpair：
     * Android 的 ProcessBuilder 走 posix_spawn，默认只保留 0/1/2，
     * ≥3 的 fd 一律被关闭（即使清了 CLOEXEC），所以 fd 继承在手机上走不通。
     * 改成让 App 当服务端、引擎按名字连过来，就完全不依赖 fd 继承了。
     *
     * @return ("DSH_BRIDGE_SOCK" to 名字) 或 null（失败）
     */
    fun prepare(): Pair<String, String>? {
        // 已有会话就复用，不要另起一套：另起会让引擎拿着旧名字去连，
        // 设备工具会全部失联（表现为工具调用超时或 unknown tool）。
        session?.let {
            if (it.peerSocket != null || it.serverSocket != null) {
                log("=== prepare 复用已有会话 name=" + it.name + " ===")
                return "DSH_BRIDGE_SOCK" to it.name
            }
        }
        log("=== prepare 开始（抽象命名空间 socket）===")
        return try {
            val name = "siyi-bridge-" + java.util.UUID.randomUUID().toString().take(8)
            log("步骤1: 创建 LocalServerSocket, name=" + name)
            val server = LocalServerSocket(name)
            log("步骤2: LocalServerSocket 创建成功")

            // 引擎会连过来；accept 在独立线程里等，不阻塞主流程
            val acceptThread = Thread {
                try {
                    log("步骤3: 等待引擎连接…")
                    val peer = server.accept()
                    if (peer == null) {
                        log("步骤3 失败: accept 返回 null")
                        return@Thread
                    }
                    log("步骤3 成功: 引擎已连接")
                    val s = session ?: return@Thread
                    s.peerSocket = peer
                    s.reader = BufferedReader(InputStreamReader(peer.inputStream, Charsets.UTF_8))
                    s.writer = OutputStreamWriter(peer.outputStream, Charsets.UTF_8)
                    log("步骤4: 读写流已建立，等 start() 启动读取循环")
                    // 用 CAS 保证只启动一次；start() 可能在 accept 之前就调用过
                    if (s.running.compareAndSet(false, true)) {
                        launchReadLoop(s, peer)
                    }
                } catch (t: Throwable) {
                    log("accept 线程异常: " + t.javaClass.simpleName + ": " + t.message)
                }
            }
            acceptThread.isDaemon = true
            acceptThread.start()
            session = Session().also {
                it.name = name
                it.serverSocket = server
            }

            log("步骤5: 返回 DSH_BRIDGE_SOCK=" + name)
            "DSH_BRIDGE_SOCK" to name
        } catch (t: Throwable) {
            log("失败: " + t.javaClass.simpleName + ": " + t.message)
            runCatching { t.stackTrace.take(4).forEach { e -> log("  at " + e) } }
            null
        }
    }

    fun start(ctx: Context) {
        appCtx = ctx.applicationContext
        // 若 accept 已完成，这里会直接起循环；否则等 accept 线程来起。
        val s = session ?: return
        val peer = s.peerSocket ?: return
        if (s.running.compareAndSet(false, true)) {
            launchReadLoop(s, peer)
        }
    }

    /** 起读取循环。同一个 peer 只起一次。 */
    private fun launchReadLoop(s: Session, peer: LocalSocket) {
        s.thread = Thread({
            log("读取循环已启动")
            try {
                val r = s.reader ?: return@Thread
                while (s.running.get()) {
                    val line = r.readLine() ?: break
                    if (line.isBlank()) continue
                    handleLine(line)
                }
            } catch (t: Throwable) {
                log("读取循环异常: " + t.javaClass.simpleName + ": " + t.message)
            } finally {
                log("桥接已关闭")
                s.running.set(false)
            }
        }, "siyi-device-bridge").apply { isDaemon = true; start() }
    }

    private fun handleLine(line: String) {
        val msg = try { JSONObject(line) } catch (_: Throwable) { return }
        val method = msg.optString("method", "")
        val id = if (msg.has("id") && !msg.isNull("id")) msg.get("id") else null
        val params = msg.optJSONObject("params") ?: JSONObject()

        // App 侧不请求引擎，若收到请求一律 -32601
        if (id == null && method.isNotEmpty()) {
            onNotification(method, params)
            return
        }
        if (id == null) return

        try {
            when (method) {
                "capability/list" -> reply(id, JSONObject().put("tools", descriptors()))
                "capability/invoke" -> reply(id, invoke(params))
                else -> error(id, -32601, "method not found: $method")
            }
        } catch (t: Throwable) {
            error(id, -32603, t.message ?: "internal error")
        }
    }

    private fun onNotification(method: String, params: JSONObject) {
        when (method) {
            "capability/cancel" -> {
                val callId = params.optString("callId", "")
                if (callId.isNotEmpty()) cancelled[callId] = true
            }
        }
    }

    private fun reply(id: Any, result: Any) {
        send(JSONObject().put("jsonrpc", "2.0").put("id", id).put("result", result))
    }

    private fun error(id: Any, code: Int, message: String) {
        send(JSONObject()
            .put("jsonrpc", "2.0")
            .put("id", id)
            .put("error", JSONObject().put("code", code).put("message", message)))
    }

    @Synchronized
    private fun send(obj: JSONObject) {
        try {
            val w = session?.writer ?: return
            w.write(obj.toString())
            w.write("\n")
            w.flush()
        } catch (t: Throwable) {
            log("发送失败: ${t.message}")
        }
    }

    /** 工具清单。每项都会被引擎注册成真正的 dsh tool。 */
    /** 工具清单委托给 Capabilities：加能力只需改那一个文件。 */
    private fun descriptors(): JSONArray = Capabilities.descriptorList()

    private fun invoke(params: JSONObject): JSONObject {
        val tool = params.optString("tool", "")
        val args = params.optJSONObject("args") ?: JSONObject()
        val callId = params.optString("callId", "")
        if (cancelled.remove(callId) == true) {
            return fail("cancelled", "调用已取消")
        }
        val ctx = appCtx ?: return fail("no_context", "应用上下文不可用")
        // 全部能力统一交给 Capabilities（清单和实现都在那一个文件里）。
        // 以前这里单独硬编码分派 4 个工具，同一份能力分裂在两个文件里维护。
        // 先算出结果再包成 ToolResult：when 分支里不能直接 return，
        // 否则 when 的类型推断会失败（Nothing 与 String 混用）。
        val text: String? = try {
            Capabilities.invoke(ctx, tool, args)
        } catch (t: Throwable) {
            return fail("invoke_failed", "${t.javaClass.simpleName}: ${t.message}")
        }
        if (text == null) return fail("unknown_tool", "未知工具: $tool")
        return JSONObject()
            .put("ok", true)
            .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", text)))
    }

    private fun fail(code: String, message: String) = JSONObject()
        .put("ok", false)
        .put("error", JSONObject().put("code", code).put("message", message))
        .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", message)))

    /** 桥接状态（供 Capabilities 的 device_bridge_status 工具用）。 */
    fun statusJson(): String = JSONObject()
        .put("connected", session?.running?.get() == true)
        .put("tool_count", descriptors().length())
        .put("cancelled_pending", cancelled.size)
        .toString(2)

    /** 工具集变化后通知引擎重新列工具。 */
    fun notifyChanged() {
        send(JSONObject()
            .put("jsonrpc", "2.0")
            .put("method", "capability/changed")
            .put("params", JSONObject()))
    }

    fun stop() {
        val s = session
        session = null
        if (s == null) { log("桥接资源已释放（无会话）"); return }
        s.running.set(false)
        try { s.reader?.close() } catch (_: Throwable) {}
        try { s.writer?.close() } catch (_: Throwable) {}
        // 关掉两端的 socket，避免每次重启泄漏一对 fd
        try { s.peerSocket?.close() } catch (_: Throwable) {}
        try { s.serverSocket?.close() } catch (_: Throwable) {}
        s.thread?.interrupt()
        s.thread = null
        log("桥接资源已释放 name=" + s.name)
    }

    /**
     * 启动时的一次性环境自检：把定位问题需要的运行时事实全部写进日志。
     * 装一次包就能看清是 API 缺失、权限问题还是 fd 继承问题。
     */
    fun envSelfCheck(ctx: Context) {
        log("=== 环境自检 ===")
        log("android=" + Build.VERSION.RELEASE + " sdk=" + Build.VERSION.SDK_INT)
        log("abi=" + Build.SUPPORTED_ABIS.joinToString(","))
        log("nativeLibraryDir=" + ctx.applicationInfo.nativeLibraryDir)
        log("filesDir=" + ctx.filesDir.absolutePath)

        // 1) JNI 是否加载成功（socketpair 走这条路）
        log("NativeBridge.available: " + NativeBridge.available)
        runCatching {
            val pair = NativeBridge.createSocketPair()
            if (pair != null) {
                val a = NativeBridge.fdNumber(pair[0])
                val b = NativeBridge.fdNumber(pair[1])
                log("JNI socketpair 自检: 成功 fd=" + a + "," + b)
            } else {
                log("JNI socketpair 自检: 返回 null（so 未加载或 socketpair 失败）")
            }
        }.onFailure { log("JNI socketpair 自检异常: " + it.message) }

        // 2) 隐藏 API 探测（只记录，不依赖）
        runCatching {
            val os = Class.forName("android.system.Os")
            val sigs = os.declaredMethods
                .filter { it.name == "socketpair" }
                .joinToString(" ; ") { m ->
                    "socketpair(" + m.parameterTypes.joinToString(",") { it.simpleName } + ")->" + m.returnType.simpleName
                }
            log("Os.socketpair 候选: " + (sigs.ifEmpty { "(无)" }))
            val fcntl = os.declaredMethods.filter { it.name.startsWith("fcntl") }
                .joinToString(" ; ") { m -> m.name + "(" + m.parameterTypes.size + "参数)" }
            log("Os.fcntl* 候选: " + (fcntl.ifEmpty { "(无)" }))
        }.onFailure { log("Os 反射失败: " + it.message) }

        // 3) 当前进程 fd 概况
        runCatching {
            val list = File("/proc/self/fd").list()
            log("/proc/self/fd 数量: " + (list?.size ?: 0))
        }.onFailure { log("/proc/self/fd 读取失败: " + it.message) }

        // 4) bin 目录里的软链
        runCatching {
            val bin = EngineInstaller.binDir(ctx)
            val files = bin.list() ?: emptyArray()
            log("files/bin 内容: " + (if (files.isEmpty()) "(空)" else files.joinToString(",")))
            for (n in listOf("node", "rg")) {
                val p = File(bin, n)
                val exists = p.exists()
                val canExec = p.canExecute()
                val target = runCatching { java.nio.file.Files.readSymbolicLink(p.toPath()).toString() }.getOrNull()
                log("  " + n + ": exists=" + exists + " canExec=" + canExec + " target=" + (target ?: "(非软链)"))
            }
        }.onFailure { log("bin 目录检查失败: " + it.message) }

        log("=== 环境自检结束 ===")
    }

    private fun log(msg: String) {
        android.util.Log.i(TAG, msg)
        runCatching {
            AppLog.write("bridge.log", msg)
        }
    }
}
