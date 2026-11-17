package com.siyi.dshapp

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject

/**
 * 精确闹钟能力（B3）。
 *
 * 权限模型（Android 12 之后变化很大）：
 *   - SCHEDULE_EXACT_ALARM    Android 12+，**用户可撤销**的特殊权限，
 *                            要用 Settings.canScheduleExactAlarms() 检查，
 *                            没有时必须引导去设置页开。
 *   - USE_EXACT_ALARM         Android 13+，安装即授予、用户不能撤销，
 *                            但 Google Play 对该权限有严格审核政策。
 *   两个都声明，运行时优先用 canScheduleExactAlarms 判断实际能力。
 *
 * 闹钟本身用 setExactAndAllowWhileIdle：Doze 下也能触发，
 * 代价是系统会对每个 App 做频率限制（约 9 分钟一次），
 * 对本用途完全够。
 *
 * 触发方式：闹钟到点后发一个 PendingIntent，这里用 BroadcastReceiver
 * 拉起通知（AlarmReceiver）。不用 Activity，避免从后台弹界面。
 */
object AlarmCapability {

    /** 已登记的闹钟 ID，用 requestCode 区分，存在内存里够用（重启后由调用方重新登记）。 */
    private val scheduled = ArrayList<JSONObject>()
    private var nextRequestCode = 1000

    fun status(ctx: Context): String {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        // canScheduleExactAlarms 是 API 31+ 才有，低版本直接视为允许
        val canExact = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            am.canScheduleExactAlarms()
        } else true
        val hasPerm = ctx.checkSelfPermission(android.Manifest.permission.SCHEDULE_EXACT_ALARM) ==
            PackageManager.PERMISSION_GRANTED
        val out = JSONObject()
            .put("ok", true)
            .put("canExact", canExact)
            .put("permGranted", hasPerm)
            .put("sdk", Build.VERSION.SDK_INT)
            .put("scheduled", scheduled.length())
        if (!canExact) {
            out.put("hint", "精确闹钟未授权。请在设置页点「授予精确闹钟」，或在系统设置里允许本应用设置闹钟")
        }
        return out.toString(2)
    }

    /**
     * 设一个精确闹钟。
     *
     * triggerAtMs 是毫秒时间戳。必须 > 当前时间，否则立即触发（这里直接拒绝，
     * 避免调用方写错时间导致静默立即响）。
     */
    fun set(ctx: Context, triggerAtMs: Long, title: String, text: String): String {
        if (triggerAtMs <= 0) return JSONObject().put("ok", false)
            .put("error", "triggerAtMs 必须是毫秒时间戳").toString(2)
        val now = System.currentTimeMillis()
        if (triggerAtMs <= now) return JSONObject().put("ok", false)
            .put("error", "触发时间必须晚于当前时间")
            .put("now", now).put("triggerAtMs", triggerAtMs)
            .put("diffSeconds", (triggerAtMs - now) / 1000).toString(2)

        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val canExact = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            am.canScheduleExactAlarms()
        } else true
        if (!canExact) return JSONObject().put("ok", false)
            .put("error", "没有精确闹钟权限")
            .put("hint", "在设置页点「授予精确闹钟」，或在系统设置里允许本应用设置闹钟")
            .put("openSettingsHint", "Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM").toString(2)

        val code = nextRequestCode++
        val i = Intent(ctx, AlarmReceiver::class.java).apply {
            action = AlarmReceiver.ACTION_FIRE
            putExtra("title", title.ifBlank { "思忆集提醒" })
            putExtra("text", text)
            putExtra("requestCode", code)
        }
        val pi = PendingIntent.getBroadcast(
            ctx, code, i,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        runCatching {
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMs, pi)
        }.onFailure { t ->
            return JSONObject().put("ok", false)
                .put("error", "设置闹钟失败: ${t.javaClass.simpleName}")
                .put("detail", (t.message ?: "").take(200)).toString(2)
        }

        val rec = JSONObject()
            .put("requestCode", code)
            .put("triggerAt", triggerAtMs)
            .put("title", title)
            .put("text", text)
        scheduled.add(rec)
        return JSONObject().put("ok", true)
            .put("requestCode", code)
            .put("triggerAt", triggerAtMs)
            .put("inSeconds", (triggerAtMs - now) / 1000)
            .toString(2)
    }

    /** 取消一个闹钟。 */
    fun cancel(ctx: Context, requestCode: Int): String {
        if (requestCode <= 0) return JSONObject().put("ok", false).put("error", "requestCode 无效").toString(2)
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val i = Intent(ctx, AlarmReceiver::class.java).apply {
            action = AlarmReceiver.ACTION_FIRE
            putExtra("requestCode", requestCode)
        }
        val pi = PendingIntent.getBroadcast(
            ctx, requestCode, i,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        am.cancel(pi)
        pi.cancel()
        var removed = false
        for (k in scheduled.indices.reversed()) {
            if (scheduled[k].optInt("requestCode") == requestCode) {
                scheduled.removeAt(k); removed = true
            }
        }
        return JSONObject().put("ok", true).put("requestCode", requestCode)
            .put("removed", removed).toString(2)
    }

    /** 列出本进程内登记过的闹钟。 */
    fun list(): String {
        val arr = JSONArray()
        for (r in scheduled) arr.put(r)
        return JSONObject().put("ok", true).put("count", arr.length()).put("items", arr).toString(2)
    }

    /** 检查是否有精确闹钟权限，没有时返回可用来跳设置的 action 名。 */
    fun canExact(ctx: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        return am.canScheduleExactAlarms()
    }

    /** 跳系统设置页让用户开精确闹钟权限。由设置页调用。 */
    fun openExactAlarmSettings(ctx: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return false
        val i = Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM)
            .setData(Uri.parse("package:" + ctx.packageName))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return runCatching { ctx.startActivity(i) }.isSuccess
    }
}
