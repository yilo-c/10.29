package com.siyi.dshapp

import android.app.ActivityManager
import android.content.Context
import android.net.LocalServerSocket
import android.net.LocalSocket
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.os.StatFs
import android.system.Os
import android.system.OsConstants
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.FileDescriptor
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

    private var reader: BufferedReader? = null
    private var writer: OutputStreamWriter? = null
    private var engineFd: FileDescriptor? = null
    private var thread: Thread? = null
    private val running = AtomicBoolean(false)
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
                    peerSocket = peer
                    reader = BufferedReader(InputStreamReader(peer.inputStream, Charsets.UTF_8))
                    writer = OutputStreamWriter(peer.outputStream, Charsets.UTF_8)
                    log("步骤4: 读写流已建立，等 start() 启动读取循环")
                    // 用 CAS 保证只启动一次；start() 可能在 accept 之前就调用过
                    if (running.compareAndSet(false, true)) {
                        launchReadLoop(peer)
                    }
                } catch (t: Throwable) {
                    log("accept 线程异常: " + t.javaClass.simpleName + ": " + t.message)
                }
            }
            acceptThread.isDaemon = true
            acceptThread.start()
            serverSocket = server

            log("步骤5: 返回 DSH_BRIDGE_SOCK=" + name)
            "DSH_BRIDGE_SOCK" to name
        } catch (t: Throwable) {
            log("失败: " + t.javaClass.simpleName + ": " + t.message)
            runCatching { t.stackTrace.take(4).forEach { e -> log("  at " + e) } }
            null
        }
    }

    /** 服务端 socket 与已接受的连接，退出时关闭。 */
    private var serverSocket: LocalServerSocket? = null
    private var peerSocket: LocalSocket? = null

    /**
     * 清除 FD_CLOEXEC，让 fd 在 exec 后仍被子进程继承。
     * 优先走 JNI（直接 fcntl），失败再反射兜底。
     */
    private fun clearCloexec(fd: FileDescriptor) {
        if (NativeBridge.clearCloexec(fd)) {
            log("clearCloexec: JNI 成功")
            return
        }
        runCatching {
            val os = Class.forName("android.system.Os")
            for (m in os.declaredMethods) {
                if (m.name != "fcntlInt") continue
                val ps = m.parameterTypes
                if (ps.size == 3 && ps[0] == FileDescriptor::class.java &&
                    ps[1] == Int::class.javaPrimitiveType && ps[2] == Int::class.javaPrimitiveType
                ) {
                    m.invoke(null, fd, OsConstants.F_SETFD, 0)
                    log("clearCloexec: 反射 fcntlInt 成功")
                    return
                }
            }
        }
        log("clearCloexec: 全部路径失败，fd 可能在 exec 时被关闭")
    }






    /** 从 FileDescriptor 取 int fd（反射，Android 无公开 API）。 */
    private fun fdNumber(fd: FileDescriptor): Int {
        return try {
            val f = FileDescriptor::class.java.getDeclaredField("descriptor")
            f.isAccessible = true
            f.getInt(fd)
        } catch (t: Throwable) {
            // Android 12+ 字段名可能是 fd
            try {
                val f = FileDescriptor::class.java.getDeclaredField("fd")
                f.isAccessible = true
                f.getInt(fd)
            } catch (_: Throwable) {
                -1
            }
        }
    }

    fun start(ctx: Context) {
        appCtx = ctx.applicationContext
        // 若 accept 已完成，这里会直接起循环；否则等 accept 线程来起。
        val peer = peerSocket ?: return
        if (running.compareAndSet(false, true)) {
            launchReadLoop(peer)
        }
    }

    /** 起读取循环。同一个 peer 只起一次。 */
    private fun launchReadLoop(peer: LocalSocket) {
        thread = Thread({
            log("读取循环已启动")
            try {
                val r = reader ?: return@Thread
                while (running.get()) {
                    val line = r.readLine() ?: break
                    if (line.isBlank()) continue
                    handleLine(line)
                }
            } catch (t: Throwable) {
                log("读取循环异常: " + t.javaClass.simpleName + ": " + t.message)
            } finally {
                log("桥接已关闭")
                running.set(false)
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
            val w = writer ?: return
            w.write(obj.toString())
            w.write("\n")
            w.flush()
        } catch (t: Throwable) {
            log("发送失败: ${t.message}")
        }
    }

    /** 工具清单。每项都会被引擎注册成真正的 dsh tool。 */
    private fun descriptors(): JSONArray {
        val list = JSONArray()

        fun add(name: String, group: String, desc: String, schema: JSONObject, risk: String = "read") {
            list.put(JSONObject()
                .put("name", name)
                .put("group", group)
                .put("description", desc)
                .put("inputSchema", schema)
                .put("risk", risk)
                .put("needsUnlock", false)
                .put("timeoutMs", TIMEOUT_MS))
        }

        val empty = JSONObject().put("type", "object").put("properties", JSONObject())

        add("device_status", "system", "设备状态：电量、充电、存储、型号、系统版本、运行时长。", empty)
        add("device_storage", "files", "共享存储用量：总空间、可用空间、已用百分比。", empty)
        add("device_app_info", "apps", "本应用信息：版本名、数据目录、引擎是否在运行。", empty)
        add("device_bridge_status", "system", "设备能力桥状态：已注册工具数、连接状态。", empty)

        return list
    }

    private fun invoke(params: JSONObject): JSONObject {
        val tool = params.optString("tool", "")
        val args = params.optJSONObject("args") ?: JSONObject()
        val callId = params.optString("callId", "")
        if (cancelled.remove(callId) == true) {
            return fail("cancelled", "调用已取消")
        }
        val text = try {
            when (tool) {
                "device_status" -> deviceStatus()
                "device_storage" -> storageStatus()
                "device_app_info" -> appInfo()
                "device_bridge_status" -> bridgeStatus()
                else -> return fail("unknown_tool", "未知工具: $tool")
            }
        } catch (t: Throwable) {
            return fail("invoke_failed", "${t.javaClass.simpleName}: ${t.message}")
        }
        return JSONObject()
            .put("ok", true)
            .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", text)))
    }

    private fun fail(code: String, message: String) = JSONObject()
        .put("ok", false)
        .put("error", JSONObject().put("code", code).put("message", message))
        .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", message)))

    private fun deviceStatus(): String {
        val c = appCtx ?: return "应用上下文不可用"
        val bm = c.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager
        val level = bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1
        val charging = bm?.isCharging ?: false
        val act = c.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
        val mem = ActivityManager.MemoryInfo().also { act?.getMemoryInfo(it) }
        return JSONObject()
            .put("battery_percent", level)
            .put("charging", charging)
            .put("model", Build.MODEL)
            .put("manufacturer", Build.MANUFACTURER)
            .put("android", Build.VERSION.RELEASE)
            .put("sdk", Build.VERSION.SDK_INT)
            .put("uptime_ms", android.os.SystemClock.uptimeMillis())
            .put("avail_mem_mb", mem.availMem / 1048576)
            .put("low_memory", mem.lowMemory)
            .toString(2)
    }

    private fun storageStatus(): String {
        val dir = Environment.getExternalStorageDirectory()
        val st = StatFs(dir.path)
        val total = st.totalBytes
        val free = st.availableBytes
        return JSONObject()
            .put("path", dir.path)
            .put("total_mb", total / 1048576)
            .put("free_mb", free / 1048576)
            .put("used_percent", if (total > 0) ((total - free) * 100 / total) else 0)
            .toString(2)
    }

    private fun appInfo(): String {
        val c = appCtx ?: return "{}"
        val pm = c.packageManager
        val pi = pm.getPackageInfo(c.packageName, 0)
        return JSONObject()
            .put("package", c.packageName)
            .put("version", pi.versionName ?: "?")
            .put("data_dir", c.filesDir.path)
            .put("engine_running", EngineService.isRunning())
            .toString(2)
    }

    private fun bridgeStatus(): String = JSONObject()
        .put("connected", running.get())
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
        running.set(false)
        try { reader?.close() } catch (_: Throwable) {}
        try { writer?.close() } catch (_: Throwable) {}
        // 关掉两端的 socket，避免每次重启泄漏一对 fd
        try { peerSocket?.close() } catch (_: Throwable) {}
        try { serverSocket?.close() } catch (_: Throwable) {}
        try { engineFd?.let { Os.close(it) } } catch (_: Throwable) {}
        peerSocket = null
        serverSocket = null
        thread?.interrupt()
        thread = null
        log("桥接资源已释放")
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
            File("/sdcard/思忆集/app/bridge.log").appendText("[${System.currentTimeMillis()}] $msg\n")
        }
    }
}
