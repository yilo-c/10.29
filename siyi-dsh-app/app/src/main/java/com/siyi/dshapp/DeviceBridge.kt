package com.siyi.dshapp

import android.app.ActivityManager
import android.content.Context
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

    /** 建 socketpair，返回要交给 ProcessBuilder 的 (环境变量名, fd号)。 */
    fun prepare(): Pair<String, String>? {
        return try {
            // Os.socketpair 的签名在不同 Android 版本有差异：
            // 有 3 参数返回 FileDescriptor[] 的，也有 4 参数填充数组的。
            // 用反射逐个试，避免编译期绑死某一个签名。
            val fds = openSocketPair()
            val app = fds[0]
            val eng = fds[1]
            clearCloexec(eng) // 清除 CLOEXEC，exec 后保留
            reader = BufferedReader(InputStreamReader(FileInputStream(app), Charsets.UTF_8))
            writer = OutputStreamWriter(FileOutputStream(app), Charsets.UTF_8)
            engineFd = eng
            "DSH_BRIDGE_FD" to fdNumber(eng).toString()
        } catch (t: Throwable) {
            log("socketpair 失败: ${t.javaClass.simpleName}: ${t.message}")
            null
        }
    }

    /** 清除 FD_CLOEXEC，让 fd 在 exec 后仍被子进程继承。 */
    private fun clearCloexec(fd: FileDescriptor) {
        val os = Class.forName("android.system.Os")
        // fcntlInt(FileDescriptor, int, int) 或 fcntlVoid 等变体
        for (m in os.declaredMethods) {
            if (m.name != "fcntlInt") continue
            try { m.invoke(null, fd, OsConstants.F_SETFD, 0); return } catch (_: Throwable) {}
        }
        // 退路：用 OsConstants 的 fcntl 直接不可用时，交给 ParcelFileDescriptor 的反射
        android.util.Log.w(TAG, "fcntlInt 不可用，fd 可能被子进程关闭")
    }

    /** 反射调用 Os.socketpair，兼容 3 参数与 4 参数两种签名。 */
    private fun openSocketPair(): Array<FileDescriptor> {
        val os = Class.forName("android.system.Os")
        val afUnix = OsConstants.AF_UNIX
        val sockStream = OsConstants.SOCK_STREAM
        // 先试 3 参数：socketpair(int,int,int) -> FileDescriptor[]
        for (m in os.declaredMethods) {
            if (m.name != "socketpair") continue
            val ps = m.parameterTypes
            try {
                if (ps.size == 3 && ps.all { it == Int::class.javaPrimitiveType }) {
                    val r = m.invoke(null, afUnix, sockStream, 0)
                    if (r is Array<*> && r.size >= 2 && r[0] is FileDescriptor && r[1] is FileDescriptor) {
                        return arrayOf(r[0] as FileDescriptor, r[1] as FileDescriptor)
                    }
                }
            } catch (_: Throwable) {}
        }
        // 再试 4 参数：socketpair(int,int,int,FileDescriptor[])
        val out = arrayOfNulls<FileDescriptor>(2)
        for (m in os.declaredMethods) {
            if (m.name != "socketpair") continue
            val ps = m.parameterTypes
            try {
                if (ps.size == 4 && ps[3] == Array<FileDescriptor>::class.java) {
                    m.invoke(null, afUnix, sockStream, 0, out)
                    val a = out[0]; val b = out[1]
                    if (a != null && b != null) return arrayOf(a, b)
                }
            } catch (_: Throwable) {}
        }
        throw IllegalStateException("Os.socketpair 没有可用签名")
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
        if (!running.compareAndSet(false, true)) return
        thread = Thread({
            val r = reader ?: return@Thread
            log("桥接已启动")
            try {
                while (running.get()) {
                    val line = r.readLine() ?: break
                    if (line.isBlank()) continue
                    handleLine(line)
                }
            } catch (t: Throwable) {
                log("读取循环异常: ${t.javaClass.simpleName}: ${t.message}")
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
        try { engineFd?.let { Os.close(it) } } catch (_: Throwable) {}
        thread?.interrupt()
        thread = null
    }

    private fun log(msg: String) {
        android.util.Log.i(TAG, msg)
        runCatching {
            File("/sdcard/思忆集/app/bridge.log").appendText("[${System.currentTimeMillis()}] $msg\n")
        }
    }
}
