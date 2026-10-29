package com.siyi.notepad.phonebutler

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Environment
import android.os.StatFs
import org.json.JSONArray
import org.json.JSONObject

/**
 * 手机管家 — Android 系统能力封装。
 *
 * 移植自旧项目 com.siyi.app.ai.PhoneButlerDomain
 * 简化：去掉 Hilt + kotlinx.serialization 依赖，使用纯 org.json
 *
 * 所有方法返回 JSONObject（成功时含 result，失败时含 error）。
 *
 * R1_READ 工具（只读，无危险）：
 * - getBatteryStatus()
 * - getStorageInfo()
 * - getMemoryInfo()
 * - getNetworkStatus()
 * - getInstalledApps()
 */
class PhoneButlerDomain(private val context: Context) {

    /** 电池状态：电量百分比 + 是否充电。 */
    fun getBatteryStatus(): JSONObject {
        val batteryManager = context.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager
            ?: return error("Battery service not available")
        return try {
            val level = batteryManager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
            val charging = isCharging()
            success(JSONObject().apply {
                put("level", level)
                put("isCharging", charging)
                put("status", if (charging) "charging" else "discharging")
            })
        } catch (e: Exception) {
            error("Failed: ${e.message}")
        }
    }

    /** 存储空间：总量/已用/可用（字节）。 */
    fun getStorageInfo(): JSONObject {
        return try {
            val path = Environment.getDataDirectory()
            val stat = StatFs(path.path)
            val blockSize = stat.blockSizeLong
            val totalBlocks = stat.blockCountLong
            val availableBlocks = stat.availableBlocksLong
            val total = totalBlocks * blockSize
            val available = availableBlocks * blockSize
            val used = total - available
            success(JSONObject().apply {
                put("totalBytes", total)
                put("usedBytes", used)
                put("availableBytes", available)
                put("usedPercent", if (total > 0) "%.1f".format(used.toDouble() / total * 100) else "0")
            })
        } catch (e: Exception) {
            error("Failed to read storage: ${e.message}")
        }
    }

    /** 内存状态：总 RAM / 可用 RAM / 是否低内存。 */
    fun getMemoryInfo(): JSONObject {
        val activityManager = context.getSystemService(Context.ACTIVITY_SERVICE) as? ActivityManager
            ?: return error("Activity service not available")
        return try {
            val memInfo = ActivityManager.MemoryInfo().also { activityManager.getMemoryInfo(it) }
            success(JSONObject().apply {
                put("totalMem", memInfo.totalMem)
                put("availMem", memInfo.availMem)
                put("lowMemory", memInfo.lowMemory)
                put("threshold", memInfo.threshold)
            })
        } catch (e: Exception) {
            error("Failed: ${e.message}")
        }
    }

    /** 网络状态：是否联网 + 传输类型（wifi/cellular/ethernet/none）。 */
    fun getNetworkStatus(): JSONObject {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return error("Connectivity service not available")
        return try {
            val network = cm.activeNetwork
            val caps = network?.let { cm.getNetworkCapabilities(it) }
            val isWifi = caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
            val isCellular = caps?.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) == true
            val isEthernet = caps?.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) == true
            val isConnected = caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
            success(JSONObject().apply {
                put("isConnected", isConnected)
                put("isWifi", isWifi)
                put("isCellular", isCellular)
                put("isEthernet", isEthernet)
                put("transport", when {
                    isWifi -> "wifi"
                    isCellular -> "cellular"
                    isEthernet -> "ethernet"
                    isConnected -> "other"
                    else -> "none"
                })
            })
        } catch (e: Exception) {
            error("Failed: ${e.message}")
        }
    }

    /** 已安装的非系统 app 列表（按名称排序，最多 200 个）。 */
    fun getInstalledApps(limit: Int = 200): JSONObject {
        return try {
            val pm = context.packageManager
            val intent = Intent(Intent.ACTION_MAIN).apply { addCategory(Intent.CATEGORY_LAUNCHER) }
            val resolveInfos = pm.queryIntentActivities(intent, 0)
            val arr = JSONArray()
            resolveInfos
                .map { ri ->
                    JSONObject().apply {
                        put("name", ri.loadLabel(pm).toString())
                        put("packageName", ri.activityInfo.packageName)
                    }
                }
                .sortedBy { it.optString("name") }
                .take(limit.coerceAtLeast(1).coerceAtMost(500))
                .forEach { arr.put(it) }
            success(JSONObject().apply {
                put("apps", arr)
                put("total", arr.length())
            })
        } catch (e: Exception) {
            error("Failed: ${e.message}")
        }
    }

    // ── helpers ─────────────────────────────────────────

    private fun isCharging(): Boolean {
        return try {
            val intent = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val status = intent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
            status == BatteryManager.BATTERY_STATUS_CHARGING ||
                status == BatteryManager.BATTERY_STATUS_FULL
        } catch (_: Exception) {
            false
        }
    }

    private fun success(payload: JSONObject): JSONObject =
        JSONObject().apply {
            put("ok", true)
            put("result", payload)
        }

    private fun error(message: String): JSONObject =
        JSONObject().apply {
            put("ok", false)
            put("error", message)
        }
}
