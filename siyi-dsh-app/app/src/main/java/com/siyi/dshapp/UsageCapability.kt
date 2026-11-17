package com.siyi.dshapp

import android.app.AppOpsManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Build
import android.os.Process
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

/**
 * 使用时长采集（B4）。
 *
 * PACKAGE_USAGE_STATS 是**特殊权限**，跟普通运行时权限完全不同：
 *   - 不能用 requestPermissions 申请；
 *   - 只能引导用户去「设置 → 特殊应用权限 → 使用情况访问」手动开；
 *   - 用户不开就必须优雅退化。
 *
 * 退化策略（方案里要求）：开不了就只统计思忆集自身。
 * 实现方式：查不到跨应用数据时，返回本 App 的进程运行时长 + 一个明确标记，
 * 让调用方知道拿到的是降级数据，而不是把空数组误当成「今天没用手机」。
 */
object UsageCapability {

    /** 检查使用情况访问权限。没权限时 AppOpsManager 会返回 MODE_DEFAULT。 */
    fun hasPermission(ctx: Context): Boolean {
        val appOps = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            appOps.unsafeCheckOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName
            )
        } else {
            @Suppress("DEPRECATION")
            appOps.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName
            )
        }
        return mode == AppOpsManager.MODE_ALLOWED
    }

    fun status(ctx: Context): String {
        val has = hasPermission(ctx)
        return JSONObject().put("ok", true)
            .put("granted", has)
            .put("sdk", Build.VERSION.SDK_INT)
            .put("hint", if (has) "已授权，可统计全部应用"
            else "需要在 设置 → 特殊应用权限 → 使用情况访问 里手动开启；未开启时只统计思忆集自身")
            .put("openHint", "Settings.ACTION_USAGE_ACCESS_SETTINGS")
            .toString(2)
    }

    /** 跳系统设置页让用户开使用情况访问权限。 */
    fun openSettings(ctx: Context): Boolean {
        val i = android.content.Intent(android.provider.Settings.ACTION_USAGE_ACCESS_SETTINGS)
            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        return runCatching { ctx.startActivity(i) }.isSuccess
    }

    /**
     * 统计区间内的使用时长。
     *
     * beginMs / endMs 毫秒时间戳，不传默认「今天 0 点到现在」。
     * 返回按总时长降序的应用列表，含前台总时长和最后使用时间。
     */
    fun summary(ctx: Context, beginMs: Long, endMs: Long, limit: Int): String {
        val now = System.currentTimeMillis()
        val start = if (beginMs > 0) beginMs else startOfToday()
        val end = if (endMs > start) endMs else now

        if (!hasPermission(ctx)) {
            // 降级：只报本应用。用不了 UsageStatsManager 时，
            // 本进程运行时长是唯一能拿到的真实指标。
            val up = android.os.SystemClock.elapsedRealtime()
            return JSONObject().put("ok", true)
                .put("degraded", true)
                .put("reason", "没有 PACKAGE_USAGE_STATS 权限，只统计思忆集自身")
                .put("begin", start).put("end", end)
                .put("items", JSONArray().put(JSONObject()
                    .put("package", ctx.packageName)
                    .put("foregroundMs", up)
                    .put("lastUsed", now)))
                .toString(2)
        }

        val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val pm = ctx.packageManager
        val agg = HashMap<String, JSONObject>()

        runCatching {
            // queryUsageStats 的粒度是系统自己定的（通常按天或按小时聚合），
            // 区间取得越准，系统给的桶越贴。这里直接用区间查，再自己累加。
            usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, start, end)?.forEach { s ->
                if (s.totalTimeInForeground <= 0) return@forEach
                val pkg = s.packageName ?: return@forEach
                val prev = agg[pkg]
                if (prev == null) {
                    agg[pkg] = JSONObject()
                        .put("package", pkg)
                        .put("foregroundMs", s.totalTimeInForeground)
                        .put("lastUsed", s.lastTimeUsed)
                } else {
                    prev.put("foregroundMs", prev.optLong("foregroundMs") + s.totalTimeInForeground)
                    if (s.lastTimeUsed > prev.optLong("lastUsed")) prev.put("lastUsed", s.lastTimeUsed)
                }
            }
        }.onFailure { t ->
            return JSONObject().put("ok", false)
                .put("error", "查询使用统计失败: ${t.javaClass.simpleName}")
                .put("detail", (t.message ?: "").take(200)).toString(2)
        }

        // 补上应用名，并按时长降序
        val list = agg.values.map { o ->
            val pkg = o.optString("package")
            val label = runCatching {
                pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
            }.getOrDefault(pkg)
            o.put("label", label)
        }.sortedByDescending { it.optLong("foregroundMs") }

        val arr = JSONArray()
        var total = 0L
        for (i in list.indices) {
            total += list[i].optLong("foregroundMs")
            if (i < limit) arr.put(list[i])
        }

        return JSONObject().put("ok", true)
            .put("degraded", false)
            .put("begin", start).put("end", end)
            .put("count", arr.length())
            .put("totalForegroundMs", total)
            .put("items", arr).toString(2)
    }

    private fun startOfToday(): Long {
        val c = Calendar.getInstance()
        c.set(Calendar.HOUR_OF_DAY, 0)
        c.set(Calendar.MINUTE, 0)
        c.set(Calendar.SECOND, 0)
        c.set(Calendar.MILLISECOND, 0)
        return c.timeInMillis
    }
}
