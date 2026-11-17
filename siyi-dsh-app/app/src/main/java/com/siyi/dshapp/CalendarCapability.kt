package com.siyi.dshapp

import android.Manifest
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.CalendarContract
import org.json.JSONArray
import org.json.JSONObject
import java.util.TimeZone

/**
 * 日历读写能力（B2）。
 *
 * 为什么要单独一个文件：Capabilities.kt 已经 700 多行，日历这块
 * 涉及权限检查、ContentResolver 查询、事件写入三套逻辑，混进去会很难维护。
 *
 * 权限模型（这是设计上的关键取舍）：
 *   READ_CALENDAR / WRITE_CALENDAR 是**运行时危险权限**。
 *   方案里明确要求「首次申请放设置页手动触发，不要放启动路径」——
 *   因为启动路径弹权限框会打断用户进入对话，而这类能力又是 AI 主动调用的，
 *   用户还没表达需求时弹框很容易被拒。
 *   所以这里不主动申请，只检查并明确回报缺什么。
 *   用户在设置页点「授予日历权限」后才申请（见 MainActivity.requestCalendarPermission）。
 *
 * 读写在主线程之外由 DeviceBridge 的后台线程调用，
 * ContentResolver 本身线程安全，不需要额外加锁。
 */
object CalendarCapability {

    private fun hasRead(ctx: Context): Boolean =
        ctx.checkSelfPermission(Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED

    private fun hasWrite(ctx: Context): Boolean =
        ctx.checkSelfPermission(Manifest.permission.WRITE_CALENDAR) == PackageManager.PERMISSION_GRANTED

    /** 权限状态，给设置页和工具调用共用。 */
    fun status(ctx: Context): String = JSONObject()
        .put("ok", true)
        .put("read", hasRead(ctx))
        .put("write", hasWrite(ctx))
        .put("hint", if (hasRead(ctx) && hasWrite(ctx)) "已授权"
        else "需要在设置页手动授予日历读写权限（不会在启动时弹窗）")
        .toString(2)

    /**
     * 列日历账户。
     *
     * 用途：写入事件时必须指定一个可写的 calendarId。
     * 手机上通常有本地日历和若干账号日历，本地日历（ACCOUNT_TYPE_LOCAL）最稳，
     * 不依赖任何账号登录，所以优先返回它。
     */
    fun listCalendars(ctx: Context): String {
        if (!hasRead(ctx)) return needPerm("READ_CALENDAR")
        val out = JSONArray()
        runCatching {
            val proj = arrayOf(
                CalendarContract.Calendars._ID,
                CalendarContract.Calendars.CALENDAR_DISPLAY_NAME,
                CalendarContract.Calendars.ACCOUNT_NAME,
                CalendarContract.Calendars.ACCOUNT_TYPE,
                CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL,
                CalendarContract.Calendars.CALENDAR_COLOR,
                CalendarContract.Calendars.VISIBLE,
            )
            ctx.contentResolver.query(
                CalendarContract.Calendars.CONTENT_URI, proj, null, null, null
            )?.use { c ->
                while (c.moveToNext()) {
                    val access = c.getInt(4)
                    out.put(JSONObject()
                        .put("id", c.getLong(0))
                        .put("name", c.getString(1) ?: "")
                        .put("account", c.getString(2) ?: "")
                        .put("type", c.getString(3) ?: "")
                        .put("writable", access >= CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR)
                        .put("color", c.getInt(5))
                        .put("visible", c.getInt(6) == 1))
                }
            }
        }.onFailure { return fail("查询日历失败", it) }
        return JSONObject().put("ok", true).put("count", out.length()).put("items", out).toString(2)
    }

    /**
     * 查某段时间的事件。
     *
     * beginMs / endMs 是毫秒时间戳。不传就查「今天」。
     * 用 INSTANCE 表而不是 EVENTS 表：INSTANCE 展开重复事件，
     * 直接把每次发生算成一条，日历视图要的就是这个语义。
     */
    fun listEvents(ctx: Context, beginMs: Long, endMs: Long, limit: Int): String {
        if (!hasRead(ctx)) return needPerm("READ_CALENDAR")
        val start = if (beginMs > 0) beginMs else startOfToday()
        val end = if (endMs > start) endMs else start + 24L * 3600 * 1000
        val out = JSONArray()
        runCatching {
            val proj = arrayOf(
                CalendarContract.Instances.EVENT_ID,
                CalendarContract.Instances.TITLE,
                CalendarContract.Instances.BEGIN,
                CalendarContract.Instances.END,
                CalendarContract.Instances.ALL_DAY,
                CalendarContract.Instances.EVENT_LOCATION,
                CalendarContract.Instances.DESCRIPTION,
                CalendarContract.Instances.CALENDAR_ID,
            )
            // Instances 的查询 URI 必须带 begin/end 参数，否则系统抛 IllegalArgumentException
            val uri = CalendarContract.Instances.CONTENT_URI.buildUpon()
                .appendPath(start.toString())
                .appendPath(end.toString())
                .build()
            ctx.contentResolver.query(uri, proj, null, null,
                CalendarContract.Instances.BEGIN + " ASC")?.use { c ->
                var n = 0
                while (c.moveToNext() && n < limit) {
                    out.put(JSONObject()
                        .put("eventId", c.getLong(0))
                        .put("title", c.getString(1) ?: "")
                        .put("begin", c.getLong(2))
                        .put("end", c.getLong(3))
                        .put("allDay", c.getInt(4) == 1)
                        .put("location", c.getString(5) ?: "")
                        .put("description", c.getString(6) ?: "")
                        .put("calendarId", c.getLong(7)))
                    n++
                }
            }
        }.onFailure { return fail("查询事件失败", it) }
        return JSONObject().put("ok", true)
            .put("begin", start).put("end", end)
            .put("count", out.length()).put("items", out).toString(2)
    }

    /**
     * 写一条事件（B2 核心）。
     *
     * calendarId 不传时自动挑第一个可写的日历，优先本地日历。
     * 这样调用方不需要先查 calendarId，少一次往返。
     */
    fun insertEvent(
        ctx: Context, title: String, beginMs: Long, endMs: Long,
        allDay: Boolean, location: String, description: String, calendarId: Long,
    ): String {
        if (!hasWrite(ctx)) return needPerm("WRITE_CALENDAR")
        if (title.isBlank()) return JSONObject().put("ok", false).put("error", "title 不能为空").toString(2)
        if (beginMs <= 0) return JSONObject().put("ok", false).put("error", "beginMs 必须是毫秒时间戳").toString(2)
        val end = if (endMs > beginMs) endMs else beginMs + 3600L * 1000

        val cal = if (calendarId > 0) calendarId else pickWritableCalendar(ctx)
        if (cal <= 0) return JSONObject().put("ok", false)
            .put("error", "没有可写的日历账户。请先在系统日历里建一个本地日历").toString(2)

        var newId = -1L
        runCatching {
            val v = ContentValues().apply {
                put(CalendarContract.Events.CALENDAR_ID, cal)
                put(CalendarContract.Events.TITLE, title)
                put(CalendarContract.Events.DESCRIPTION, description)
                put(CalendarContract.Events.EVENT_LOCATION, location)
                put(CalendarContract.Events.DTSTART, beginMs)
                put(CalendarContract.Events.DTEND, end)
                put(CalendarContract.Events.ALL_DAY, if (allDay) 1 else 0)
                put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
                // 明确不设提醒的默认行为，避免系统给某些账户强加提醒
                put(CalendarContract.Events.HAS_ALARM, 0)
            }
            val uri = ctx.contentResolver.insert(CalendarContract.Events.CONTENT_URI, v)
            newId = uri?.let { ContentUris.parseId(it) } ?: -1L
        }.onFailure { return fail("写入事件失败", it) }

        if (newId <= 0) return JSONObject().put("ok", false).put("error", "写入返回空 uri").toString(2)
        return JSONObject().put("ok", true)
            .put("eventId", newId)
            .put("calendarId", cal)
            .put("begin", beginMs)
            .put("end", end)
            .toString(2)
    }

    /** 修改事件：只改传入的字段。 */
    fun updateEvent(
        ctx: Context, eventId: Long, title: String, beginMs: Long, endMs: Long,
        location: String, description: String,
    ): String {
        if (!hasWrite(ctx)) return needPerm("WRITE_CALENDAR")
        if (eventId <= 0) return JSONObject().put("ok", false).put("error", "eventId 无效").toString(2)
        var rows = 0
        runCatching {
            val v = ContentValues()
            if (title.isNotEmpty()) v.put(CalendarContract.Events.TITLE, title)
            if (beginMs > 0) v.put(CalendarContract.Events.DTSTART, beginMs)
            if (endMs > beginMs) v.put(CalendarContract.Events.DTEND, endMs)
            if (location.isNotEmpty()) v.put(CalendarContract.Events.EVENT_LOCATION, location)
            if (description.isNotEmpty()) v.put(CalendarContract.Events.DESCRIPTION, description)
            if (v.size() == 0) return JSONObject().put("ok", false).put("error", "没有要改的字段").toString(2)
            val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
            rows = ctx.contentResolver.update(uri, v, null, null)
        }.onFailure { return fail("修改事件失败", it) }
        return JSONObject().put("ok", rows > 0).put("rows", rows).put("eventId", eventId).toString(2)
    }

    /** 删事件。Android 上删 Events 会连带删 Instances。 */
    fun deleteEvent(ctx: Context, eventId: Long): String {
        if (!hasWrite(ctx)) return needPerm("WRITE_CALENDAR")
        if (eventId <= 0) return JSONObject().put("ok", false).put("error", "eventId 无效").toString(2)
        var rows = 0
        runCatching {
            val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId)
            rows = ctx.contentResolver.delete(uri, null, null)
        }.onFailure { return fail("删除事件失败", it) }
        return JSONObject().put("ok", rows > 0).put("rows", rows).toString(2)
    }

    /**
     * E3 第一档：用系统日历 App 打开「新建事件」并预填。
     *
     * 这是零权限路径 —— 不需要 READ/WRITE_CALENDAR，只是发个 Intent
     * 让系统日历自己处理，用户确认后由日历 App 自己写入。
     * 方案里建议先做这一档，因为它的权限成本为零，且失败时用户能看到发生了什么。
     */
    fun openEventEditor(
        ctx: Context, title: String, beginMs: Long, endMs: Long,
        allDay: Boolean, location: String, description: String,
    ): String {
        if (beginMs <= 0) return JSONObject().put("ok", false).put("error", "beginMs 必须是毫秒时间戳").toString(2)
        val end = if (endMs > beginMs) endMs else beginMs + 3600L * 1000
        // 两个标准 Insert Intent，按优先级尝试：
        //   Insert   —— 打开新建界面并预填，用户点保存
        //   Edit     —— 某些 ROM 只认这个
        val candidates = listOf(
            Intent(Intent.ACTION_INSERT).setData(CalendarContract.Events.CONTENT_URI),
            Intent(Intent.ACTION_EDIT).setData(CalendarContract.Events.CONTENT_URI),
        )
        var lastErr = ""
        for (i in candidates) {
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            i.putExtra(CalendarContract.Events.TITLE, title)
            i.putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, beginMs)
            i.putExtra(CalendarContract.EXTRA_EVENT_END_TIME, end)
            i.putExtra(CalendarContract.Events.ALL_DAY, allDay)
            if (location.isNotEmpty()) i.putExtra(CalendarContract.Events.EVENT_LOCATION, location)
            if (description.isNotEmpty()) i.putExtra(CalendarContract.Events.DESCRIPTION, description)
            val r = runCatching { ctx.startActivity(i) }
            if (r.isSuccess) {
                return JSONObject().put("ok", true)
                    .put("method", i.action)
                    .put("begin", beginMs).put("end", end)
                    .put("note", "已打开系统日历，请在日历界面确认保存").toString(2)
            }
            lastErr = r.exceptionOrNull()?.message ?: "未知错误"
        }
        return JSONObject().put("ok", false)
            .put("error", "打开系统日历失败：$lastErr")
            .put("hint", "设备可能没有系统日历 App").toString(2)
    }

    // ── 内部工具 ────────────────────────────────────────────────────────

    /** 挑一个可写日历，优先本地日历（不依赖账号登录）。 */
    private fun pickWritableCalendar(ctx: Context): Long {
        var best = -1L
        runCatching {
            val proj = arrayOf(
                CalendarContract.Calendars._ID,
                CalendarContract.Calendars.ACCOUNT_TYPE,
                CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL,
            )
            ctx.contentResolver.query(
                CalendarContract.Calendars.CONTENT_URI, proj, null, null, null
            )?.use { c ->
                while (c.moveToNext()) {
                    val access = c.getInt(2)
                    if (access < CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR) continue
                    val isLocal = c.getString(1) == CalendarContract.ACCOUNT_TYPE_LOCAL
                    if (isLocal) return c.getLong(0)
                    if (best < 0) best = c.getLong(0)
                }
            }
        }
        return best
    }

    private fun startOfToday(): Long {
        val c = java.util.Calendar.getInstance()
        c.set(java.util.Calendar.HOUR_OF_DAY, 0)
        c.set(java.util.Calendar.MINUTE, 0)
        c.set(java.util.Calendar.SECOND, 0)
        c.set(java.util.Calendar.MILLISECOND, 0)
        return c.timeInMillis
    }

    private fun needPerm(p: String) = JSONObject().put("ok", false)
        .put("error", "缺少 $p 权限")
        .put("hint", "在设置页点「授予日历权限」后重试").toString(2)

    private fun fail(what: String, t: Throwable) = JSONObject().put("ok", false)
        .put("error", "$what: ${t.javaClass.simpleName}")
        .put("detail", (t.message ?: "").take(300)).toString(2)
}
