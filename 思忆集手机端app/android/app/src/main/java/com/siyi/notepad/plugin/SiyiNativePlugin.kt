package com.siyi.notepad.plugin

import android.content.Intent
import android.net.Uri
import android.provider.AlarmClock
import android.provider.Settings
import com.getcapacitor.JSObject
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.siyi.notepad.accessibility.SiyiAccessibilityService
import com.siyi.notepad.miui.RedmiNoteReader
import com.siyi.notepad.miui.RootShell
import com.siyi.notepad.phonebutler.PhoneButlerDomain
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.zip.GZIPInputStream

/**
 * SiyiNative — Capacitor 插件桥接。
 *
 * 把 Android 原生能力暴露给 React/JS 层使用。
 * 通过 `import SiyiNative from '../lib/native/siyiNative'` 在前端调用。
 *
 * 全部方法签名见 `siyiNative.ts`。
 *
 * 设计原则：
 * - 写操作（tap/swipe/launch）失败时返回 success=false，不抛异常
 * - 读操作（screen/battery/storage）总是返回 JSObject
 * - 无障碍服务未启动时多数方法返回 unavailable=true
 */
@CapacitorPlugin(name = "SiyiNative")
class SiyiNativePlugin : Plugin() {

    private val phoneButler: PhoneButlerDomain by lazy { PhoneButlerDomain(context) }
    private val redmiNotes: RedmiNoteReader by lazy { RedmiNoteReader(context) }

    // ── 系统栏 insets ─────────────────────────────────────

    /**
     * 返回系统栏占用的像素高度。
     *
     * 为什么需要：targetSdk 36 起 Android 强制 edge-to-edge，WebView 内容会延伸到
     * 系统导航栏/状态栏下方；而 WebView 默认不向 CSS 分发 inset，
     * 导致 `env(safe-area-inset-bottom)` 在三键导航模式下为 0，
     * App 自己的底部导航栏就画进了系统导航键区域（Redmi 实测重叠）。
     *
     * 返回 px（物理像素），前端除以 devicePixelRatio 转 CSS px 使用。
     */
    @PluginMethod
    fun getInsets(call: PluginCall) {
        val ret = JSObject()
        try {
            val activity = activity
            if (activity == null) {
                // Activity 不可用时退回资源维度（导航栏高度），至少不返回 0
                val res = context.resources
                val id = res.getIdentifier("navigation_bar_height", "dimen", "android")
                val h = if (id > 0) res.getDimensionPixelSize(id) else 0
                val sid = res.getIdentifier("status_bar_height", "dimen", "android")
                val sh = if (sid > 0) res.getDimensionPixelSize(sid) else 0
                ret.put("ok", false)
                ret.put("bottom", h)
                ret.put("top", sh)
                ret.put("source", "resources")
                ret.put("handledByNative", false)
                call.resolve(ret)
                return
            }

            val result = ret
            activity.runOnUiThread {
                try {
                    var bottom = 0
                    var top = 0
                    if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R) {
                        val wi = activity.windowManager.currentWindowMetrics
                        val insets = wi.windowInsets.getInsets(
                            android.view.WindowInsets.Type.navigationBars() or
                                android.view.WindowInsets.Type.statusBars()
                        )
                        bottom = insets.bottom
                        top = insets.top
                    } else {
                        @Suppress("DEPRECATION")
                        val v = activity.window.decorView
                        @Suppress("DEPRECATION")
                        val sysUi = v.systemUiVisibility
                        // 三键导航时 visible=false 才占用空间
                        val navVisible = (sysUi and android.view.View.SYSTEM_UI_FLAG_HIDE_NAVIGATION) == 0
                        if (navVisible) {
                            val res = activity.resources
                            val id = res.getIdentifier("navigation_bar_height", "dimen", "android")
                            bottom = if (id > 0) res.getDimensionPixelSize(id) else 0
                        }
                        val sid = activity.resources.getIdentifier("status_bar_height", "dimen", "android")
                        top = if (sid > 0) activity.resources.getDimensionPixelSize(sid) else 0
                    }
                    result.put("ok", true)
                    result.put("bottom", bottom)
                    result.put("top", top)
                    // 原生 MainActivity 已给 WebView 容器加过系统栏 padding，
                    // 此时 CSS 不应再叠加同样的留白，否则会双倍空白。
                    // 前端据此决定是否写入 --safe-bottom。
                    result.put("handledByNative", true)
                    result.put("density", activity.resources.displayMetrics.density.toDouble())
                    result.put("screenHeight", activity.resources.displayMetrics.heightPixels)
                    result.put("source", "windowInsets")
                } catch (e: Exception) {
                    val res = activity.resources
                    val id = res.getIdentifier("navigation_bar_height", "dimen", "android")
                    result.put("ok", false)
                    result.put("bottom", if (id > 0) res.getDimensionPixelSize(id) else 0)
                    result.put("top", 0)
                    result.put("error", e.message)
                    result.put("source", "fallback")
                    result.put("handledByNative", false)
                }
                call.resolve(result)
            }
        } catch (e: Exception) {
            ret.put("ok", false)
            ret.put("bottom", 0)
            ret.put("top", 0)
            ret.put("error", e.message)
            ret.put("handledByNative", false)
            call.resolve(ret)
        }
    }

    // ── 网络抓取（绕开 WebView CORS） ─────────────────────────

    /**
     * 原生 HTTP GET。
     *
     * 为什么需要：手机端资讯模块要直连各大新闻源（HN / TechCrunch / 36氪 / 少数派 …），
     * 但 WebView 里发跨域 fetch 会被 CORS 拦截（这些源不返回
     * Access-Control-Allow-Origin）。改由原生发请求即可绕开限制。
     *
     * VPN 兼容：系统级 VPN（如星辰）在 Android 上是全局的，
     * 本方法走系统网络栈，用户开 VPN 后自动经 VPN 出口，无需任何植入或配置。
     *
     * 参数：url, timeout(ms), headers(可选 JSON)
     * 返回：{ok, status, body, contentType, bytes, error?}
     *
     * 注意：本方法在工作线程执行，避免阻塞 UI；有大小上限防内存爆掉。
     */
    @PluginMethod
    fun httpFetch(call: PluginCall) {
        val urlStr = call.getString("url") ?: ""
        if (urlStr.isEmpty()) {
            val e = JSObject(); e.put("ok", false); e.put("error", "url required")
            call.resolve(e); return
        }
        val timeout = call.getInt("timeout", 20000) ?: 20000
        val maxBytes = 3 * 1024 * 1024   // 3MB 上限
        val customHeaders = HashMap<String, String>()
        try {
            val h = call.getObject("headers")
            h?.keys()?.forEach { k -> customHeaders[k] = h.getString(k) }
        } catch (_: Exception) { }

        Thread {
            val ret = JSObject()
            var conn: HttpURLConnection? = null
            try {
                val u = URL(urlStr)
                conn = (u.openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = timeout
                    readTimeout = timeout
                    instanceFollowRedirects = true
                    setRequestProperty("User-Agent",
                        "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36")
                    setRequestProperty("Accept", "application/rss+xml, application/atom+xml, application/xml, application/json, text/xml, text/html;q=0.9, */*;q=0.8")
                    setRequestProperty("Accept-Encoding", "gzip")
                    customHeaders.forEach { (k, v) -> setRequestProperty(k, v) }
                }

                val status = conn.responseCode
                val ctype = conn.contentType ?: ""
                val isGzip = (conn.contentEncoding ?: "").contains("gzip", ignoreCase = true)

                val stream = if (status in 200..299) conn.inputStream else (conn.errorStream ?: conn.inputStream)
                val raw = ByteArrayOutputStream()
                val buf = ByteArray(8192)
                var total = 0
                var truncated = false
                stream?.use { s0 ->
                    val s = if (isGzip) GZIPInputStream(s0) else s0
                    s.use { input ->
                        while (true) {
                            val n = input.read(buf)
                            if (n <= 0) break
                            if (total + n > maxBytes) {
                                raw.write(buf, 0, maxBytes - total)
                                truncated = true
                                break
                            }
                            raw.write(buf, 0, n)
                            total += n
                        }
                    }
                }

                val bytes = raw.toByteArray()
                val charset = Regex("charset=([\\w-]+)", RegexOption.IGNORE_CASE).find(ctype)?.groupValues?.getOrNull(1)
                    ?: "UTF-8"
                val body = try { String(bytes, charset(charset)) } catch (_: Exception) { String(bytes, Charsets.UTF_8) }

                ret.put("ok", status in 200..299)
                ret.put("status", status)
                ret.put("body", body)
                ret.put("contentType", ctype)
                ret.put("bytes", bytes.size)
                if (truncated) ret.put("truncated", true)
            } catch (e: Exception) {
                ret.put("ok", false)
                ret.put("status", 0)
                ret.put("body", "")
                ret.put("error", e.message ?: e.javaClass.simpleName)
            } finally {
                try { conn?.disconnect() } catch (_: Exception) { }
                call.resolve(ret)
            }
        }.start()
    }

    // ── 无障碍服务状态 ─────────────────────────────────────

    @PluginMethod
    fun isAccessibilityRunning(call: PluginCall) {
        call.resolve(JSObject().apply {
            put("running", SiyiAccessibilityService.isRunning())
        })
    }

    /** 跳转到系统无障碍设置页（用户首次使用前需手动启用）。 */
    @PluginMethod
    fun openAccessibilitySettings(call: PluginCall) {
        try {
            val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            call.resolve(JSObject().apply { put("ok", true) })
        } catch (e: Exception) {
            call.reject("Failed to open settings: ${e.message}")
        }
    }

    // ── 屏幕读取 + 手势 ─────────────────────────────────────

    @PluginMethod
    fun readScreen(call: PluginCall) {
        val text = SiyiAccessibilityService.readCurrentScreen()
        call.resolve(JSObject().apply {
            put("available", SiyiAccessibilityService.isRunning())
            put("text", text ?: "")
        })
    }

    @PluginMethod
    fun tap(call: PluginCall) {
        val x = call.getFloat("x")
        val y = call.getFloat("y")
        if (x == null || y == null) {
            call.reject("x and y required")
            return
        }
        val ok = SiyiAccessibilityService.performTap(x, y)
        call.resolve(JSObject().apply {
            put("success", ok)
            put("available", SiyiAccessibilityService.isRunning())
        })
    }

    @PluginMethod
    fun swipe(call: PluginCall) {
        val x1 = call.getFloat("x1")
        val y1 = call.getFloat("y1")
        val x2 = call.getFloat("x2")
        val y2 = call.getFloat("y2")
        val duration = call.getInt("duration") ?: 300
        if (x1 == null || y1 == null || x2 == null || y2 == null) {
            call.reject("x1,y1,x2,y2 required")
            return
        }
        val ok = SiyiAccessibilityService.performSwipe(x1, y1, x2, y2, duration.toLong())
        call.resolve(JSObject().apply {
            put("success", ok)
            put("available", SiyiAccessibilityService.isRunning())
        })
    }

    @PluginMethod
    fun navBack(call: PluginCall) {
        call.resolve(JSObject().apply { put("success", SiyiAccessibilityService.performBack()) })
    }

    @PluginMethod
    fun navHome(call: PluginCall) {
        call.resolve(JSObject().apply { put("success", SiyiAccessibilityService.performHome()) })
    }

    @PluginMethod
    fun navRecents(call: PluginCall) {
        call.resolve(JSObject().apply { put("success", SiyiAccessibilityService.performRecents()) })
    }

    /** 启动其他 app（按包名）。 */
    @PluginMethod
    fun launchApp(call: PluginCall) {
        val pkgName = call.getString("packageName")
        if (pkgName.isNullOrBlank()) {
            call.reject("packageName required")
            return
        }
        try {
            val intent = context.packageManager.getLaunchIntentForPackage(pkgName)
            if (intent != null) {
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(intent)
                call.resolve(JSObject().apply { put("success", true) })
            } else {
                call.resolve(JSObject().apply {
                    put("success", false)
                    put("error", "package not installed: $pkgName")
                })
            }
        } catch (e: Exception) {
            call.resolve(JSObject().apply {
                put("success", false)
                put("error", e.message ?: "launch failed")
            })
        }
    }

    // ── 手机管家（系统状态查询）─────────────────────────────

    @PluginMethod
    fun getBatteryStatus(call: PluginCall) {
        call.resolve(toJSObject(phoneButler.getBatteryStatus()))
    }

    @PluginMethod
    fun getStorageInfo(call: PluginCall) {
        call.resolve(toJSObject(phoneButler.getStorageInfo()))
    }

    @PluginMethod
    fun getMemoryInfo(call: PluginCall) {
        call.resolve(toJSObject(phoneButler.getMemoryInfo()))
    }

    @PluginMethod
    fun getNetworkStatus(call: PluginCall) {
        call.resolve(toJSObject(phoneButler.getNetworkStatus()))
    }

    @PluginMethod
    fun getInstalledApps(call: PluginCall) {
        val limit = call.getInt("limit") ?: 200
        call.resolve(toJSObject(phoneButler.getInstalledApps(limit)))
    }

    // ── MIUI/HyperOS 系统便签（需 root）─────────────────────

    @PluginMethod
    fun isRootAvailable(call: PluginCall) {
        val available = try { RootShell.isRootAvailable() } catch (_: Exception) { false }
        call.resolve(JSObject().apply { put("available", available) })
    }

    @PluginMethod
    fun readRedmiNotes(call: PluginCall) {
        val result = redmiNotes.readAllNotes()
        val arr = org.json.JSONArray()
        for (n in result.notes) {
            arr.put(JSONObject().apply {
                put("id", n.id)
                put("title", n.displayTitle)
                put("content", n.fullText)
                put("folderName", n.folderName ?: "默认")
                put("type", n.type)
                put("createdDate", n.createdDate)
                put("modifiedDate", n.modifiedDate)
            })
        }
        call.resolve(JSObject().apply {
            put("rootAvailable", result.rootAvailable)
            put("dbFound", result.dbFound)
            put("noteCount", result.noteCount)
            put("notes", arr)
            if (result.error != null) put("error", result.error)
        })
    }

    // ── 系统闹钟联动 ──────────────────────────────────────

    /** 打开系统闹钟应用并预填时间。若厂商不支持 ACTION_SET_ALARM 则返回不可用。 */
    @PluginMethod
    fun openSystemAlarm(call: PluginCall) {
        val hour = call.getInt("hour") ?: 0
        val minute = call.getInt("minute") ?: 0
        val message = call.getString("message") ?: "思忆便签提醒"
        try {
            val intent = Intent(AlarmClock.ACTION_SET_ALARM).apply {
                putExtra(AlarmClock.EXTRA_HOUR, hour)
                putExtra(AlarmClock.EXTRA_MINUTES, minute)
                putExtra(AlarmClock.EXTRA_MESSAGE, message)
                putExtra(AlarmClock.EXTRA_SKIP_UI, false)
            }
            if (intent.resolveActivity(context.packageManager) != null) {
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                context.startActivity(intent)
                call.resolve(JSObject().apply { put("success", true) })
            } else {
                call.resolve(JSObject().apply {
                    put("success", false)
                    put("error", "ACTION_SET_ALARM not supported")
                })
            }
        } catch (e: Exception) {
            call.resolve(JSObject().apply {
                put("success", false)
                put("error", e.message ?: "openSystemAlarm failed")
            })
        }
    }

    // ── helpers ─────────────────────────────────────────

    private fun toJSObject(json: JSONObject): JSObject {
        // Capacitor JSObject 继承自 JSONObject，可直接 fromJSONObject
        return JSObject.fromJSONObject(json)
    }
}
