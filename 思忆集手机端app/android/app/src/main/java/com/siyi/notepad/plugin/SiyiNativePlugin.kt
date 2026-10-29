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
