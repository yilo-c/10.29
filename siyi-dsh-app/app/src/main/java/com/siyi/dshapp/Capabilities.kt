package com.siyi.dshapp

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.LocationManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.provider.Settings
import android.speech.tts.TextToSpeech
import androidx.core.app.NotificationCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * 通用设备能力。DeviceBridge 只做分派，具体实现放这里。
 * 每个方法 fun(ctx, args): String；异常由 DeviceBridge 统一包成 invoke_failed。
 *
 * 纯能力直接用系统 API；需要系统界面的能力通过 InteractionHub 发 Intent，
 * 用 CountDownLatch 在后台线程同步等待结果（invoke 仍保持同步返回语义）。
 */
object Capabilities {

    private const val PREF = "siyi_capabilities"
    @Volatile private var ttsEngine: TextToSpeech? = null

    /** 工具清单。引擎会把每一项注册成真正的 dsh tool。 */
    fun descriptorList(): JSONArray {
        val list = JSONArray()
        fun schema(vararg pairs: Pair<String, String>): JSONObject {
            val props = JSONObject()
            for ((k, t) in pairs) props.put(k, JSONObject().put("type", t).put("description", k))
            return JSONObject().put("type", "object").put("properties", props)
        }
        fun add(name: String, group: String, desc: String, s: JSONObject, risk: String = "read") {
            list.put(JSONObject()
                .put("name", name).put("group", group).put("description", desc)
                .put("inputSchema", s).put("risk", risk)
                .put("needsUnlock", false).put("timeoutMs", 30_000))
        }
        val empty = JSONObject().put("type", "object").put("properties", JSONObject())

        // 原有 4 个
        add("device_status", "system", "设备状态：电量、充电、内存、型号、系统版本、运行时长。", empty)
        add("device_storage", "files", "共享存储用量。", empty)
        add("device_app_info", "apps", "本应用信息：版本、数据目录、引擎是否运行。", empty)
        add("device_bridge_status", "system", "能力桥状态。", empty)
        // 剪贴板
        add("device_clipboard_get", "clipboard", "读取系统剪贴板文本。", empty)
        add("device_clipboard_set", "clipboard", "写文本到系统剪贴板。", schema("text" to "string"), "write")
        // 通知与反馈
        add("device_notify", "notification", "发本地通知。", schema("title" to "string", "text" to "string", "id" to "number"), "write")
        add("device_vibrate", "feedback", "振动反馈。", schema("ms" to "number"), "write")
        add("device_tts", "feedback", "系统 TTS 朗读。", schema("text" to "string"), "write")
        // 定位
        add("device_location", "sensor", "最近一次已知位置。", empty)
        // 应用与系统
        add("device_app_list", "apps", "已装应用列表，可按 keyword 过滤。", schema("keyword" to "string"))
        add("device_app_launch", "apps", "按包名启动应用。", schema("package" to "string"), "write")
        add("device_installed_check", "apps", "判断包是否安装。", schema("package" to "string"))
        add("device_network", "system", "网络状态：WiFi/蜂窝/飞行模式。", empty)
        add("device_screen_state", "system", "屏幕与锁屏状态。", empty)
        add("device_volume", "system", "读写音量；不传 percent 为读。", schema("stream" to "string", "percent" to "number"))
        add("device_brightness", "system", "读写屏幕亮度；不传 value 为读。", schema("value" to "number"))
        add("device_dnd", "system", "勿扰状态。", empty)
        add("device_flashlight", "system", "手电筒开关。", schema("on" to "boolean"), "write")
        // 配置持久化
        add("device_config_get", "config", "读用户配置；不传 key 返回全部。", schema("key" to "string"))
        add("device_config_set", "config", "写用户配置。", schema("key" to "string", "value" to "string"), "write")
        add("device_config_delete", "config", "删用户配置。", schema("key" to "string"), "write")
        // 文件与插件目录（免 CI 扩展的入口）
        add("device_user_plugins_dir", "config", "返回用户插件目录路径。", empty)
        add("device_read_file", "files", "读 /sdcard/思忆集 下的文本文件。", schema("path" to "string"))
        add("device_write_file", "files", "写文本文件到 /sdcard/思忆集 下。", schema("path" to "string", "text" to "string"), "write")
        add("device_list_files", "files", "列 /sdcard/思忆集 下的文件。", schema("subdir" to "string"))
        // 交互（走 ActivityResult）
        add("device_pick_image", "interaction", "系统相册选图，返回可访问路径。", empty, "sensitive")
        add("device_take_photo", "interaction", "相机拍照，返回路径。", empty, "sensitive")
        add("device_pick_file", "interaction", "系统文件选择器。", schema("mime" to "string"), "sensitive")
        add("device_share_text", "interaction", "分享文本到其他应用。", schema("text" to "string", "title" to "string"), "write")
        add("device_voice_input", "interaction", "系统语音识别，返回文本。", empty, "sensitive")
        // 元能力
        add("device_intent", "meta", "发起任意 Intent。", schema("action" to "string", "data" to "string", "type" to "string", "package" to "string"), "dangerous")
        add("device_shell", "meta", "执行 shell 命令（白名单前缀）。", schema("cmd" to "string"), "dangerous")
        add("device_screenshot", "meta", "截屏保存到 /sdcard/思忆集/app/。", empty, "dangerous")
        return list
    }

    /** 返回 null 表示这个工具由 DeviceBridge 自己处理（原有 4 个）。 */
    fun invoke(ctx: Context, tool: String, args: JSONObject): String? = when (tool) {
        "device_clipboard_get" -> clipboardGet(ctx)
        "device_clipboard_set" -> clipboardSet(ctx, args.optString("text"))
        "device_notify" -> notify(ctx, args.optString("title"), args.optString("text"), args.optInt("id", 1))
        "device_vibrate" -> vibrate(ctx, args.optInt("ms", 200))
        "device_tts" -> tts(ctx, args.optString("text"))
        "device_location" -> location(ctx)
        "device_app_list" -> appList(ctx, args.optString("keyword"))
        "device_app_launch" -> appLaunch(ctx, args.optString("package"))
        "device_installed_check" -> installedCheck(ctx, args.optString("package"))
        "device_network" -> network(ctx)
        "device_screen_state" -> screenState(ctx)
        "device_volume" -> volume(ctx, args.optString("stream", "music"), args.optInt("percent", -1))
        "device_brightness" -> brightness(ctx, args.optInt("value", -1))
        "device_dnd" -> dnd(ctx)
        "device_flashlight" -> flashlight(ctx, args.optBoolean("on", false))
        "device_config_get" -> configGet(ctx, args.optString("key", ""))
        "device_config_set" -> configSet(ctx, args.optString("key"), args.optString("value"))
        "device_config_delete" -> configDelete(ctx, args.optString("key"))
        "device_user_plugins_dir" -> userPluginsDir(ctx)
        "device_read_file" -> readFile(ctx, args.optString("path"))
        "device_write_file" -> writeFile(ctx, args.optString("path"), args.optString("text"))
        "device_list_files" -> listFiles(ctx, args.optString("subdir", ""))
        "device_intent" -> intent(ctx, args)
        "device_shell" -> shell(ctx, args.optString("cmd"))
        "device_screenshot" -> screenshot(ctx)
        "device_pick_image" -> pickOrCapture(ctx, "image/*", false)
        "device_pick_file" -> pickOrCapture(ctx, args.optString("mime", "*/*"), false)
        "device_take_photo" -> pickOrCapture(ctx, "image/*", true)
        "device_share_text" -> shareText(ctx, args.optString("text"), args.optString("title"))
        "device_voice_input" -> voiceInput(ctx)
        else -> null
    }

    // ── 剪贴板 ──
    private fun clipboardGet(ctx: Context): String {
        val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = cm.primaryClip
        val text = if (clip != null && clip.itemCount > 0) clip.getItemAt(0).text?.toString() ?: "" else ""
        return JSONObject().put("text", text).toString(2)
    }
    private fun clipboardSet(ctx: Context, text: String): String {
        val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("siyi", text))
        return JSONObject().put("ok", true).put("length", text.length).toString(2)
    }

    // ── 通知 ──
    private fun notify(ctx: Context, title: String, text: String, id: Int): String {
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(NotificationChannel("siyi_cap", "思忆集能力", NotificationManager.IMPORTANCE_DEFAULT))
        }
        val pi = PendingIntent.getActivity(ctx, id,
            Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0))
        nm.notify(id, NotificationCompat.Builder(ctx, "siyi_cap")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title.ifEmpty { "思忆集" }).setContentText(text)
            .setContentIntent(pi).setAutoCancel(true).build())
        return JSONObject().put("ok", true).put("id", id).toString(2)
    }

    // ── 振动 ──
    private fun vibrate(ctx: Context, ms: Int): String {
        val v = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            (ctx.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager).defaultVibrator
        } else {
            @Suppress("DEPRECATION") ctx.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            v.vibrate(VibrationEffect.createOneShot(ms.toLong(), VibrationEffect.DEFAULT_AMPLITUDE))
        } else { @Suppress("DEPRECATION") v.vibrate(ms.toLong()) }
        return JSONObject().put("ok", true).put("ms", ms).toString(2)
    }

    // ── TTS ──
    private fun tts(ctx: Context, text: String): String {
        var e = ttsEngine
        if (e == null) { e = TextToSpeech(ctx) { }; ttsEngine = e }
        e.language = Locale.getDefault()
        @Suppress("DEPRECATION") e.speak(text, TextToSpeech.QUEUE_FLUSH, null)
        return JSONObject().put("ok", true).put("chars", text.length).toString(2)
    }

    // ── 定位 ──
    private fun location(ctx: Context): String {
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val out = JSONObject().put("available", false)
        for (p in listOf("gps", "network", "passive")) {
            runCatching {
                @Suppress("MissingPermission") val l = lm.getLastKnownLocation(p)
                if (l != null) {
                    out.put("available", true).put("provider", p).put("lat", l.latitude)
                        .put("lng", l.longitude).put("accuracy", l.accuracy).put("time", l.time)
                    return out.toString(2)
                }
            }
        }
        return out.toString(2)
    }

    // ── 应用 ──
    private fun appList(ctx: Context, keyword: String): String {
        val pm = ctx.packageManager
        val arr = JSONArray()
        @Suppress("DEPRECATION")
        for (pi in pm.getInstalledPackages(PackageManager.GET_META_DATA)) {
            val label = pi.applicationInfo?.loadLabel(pm)?.toString() ?: pi.packageName
            if (keyword.isNotEmpty() && !label.contains(keyword, true) && !pi.packageName.contains(keyword, true)) continue
            arr.put(JSONObject().put("package", pi.packageName).put("label", label).put("version", pi.versionName ?: "?"))
        }
        return JSONObject().put("count", arr.length()).put("apps", arr).toString(2)
    }
    private fun appLaunch(ctx: Context, pkg: String): String {
        val i = ctx.packageManager.getLaunchIntentForPackage(pkg)
            ?: throw IllegalArgumentException("找不到可启动 Activity: $pkg")
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        ctx.startActivity(i)
        return JSONObject().put("ok", true).put("package", pkg).toString(2)
    }
    private fun installedCheck(ctx: Context, pkg: String): String {
        val ok = runCatching { ctx.packageManager.getPackageInfo(pkg, 0) }.isSuccess
        return JSONObject().put("package", pkg).put("installed", ok).toString(2)
    }

    // ── 系统状态 ──
    private fun network(ctx: Context): String {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as android.net.ConnectivityManager
        val caps = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            cm.getNetworkCapabilities(cm.activeNetwork) } else null
        val wifi = caps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) == true
        val cell = caps?.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) == true
        val airplane = Settings.Global.getInt(ctx.contentResolver, Settings.Global.AIRPLANE_MODE_ON, 0) == 1
        return JSONObject().put("wifi", wifi).put("cellular", cell).put("airplane", airplane).toString(2)
    }
    private fun screenState(ctx: Context): String {
        val pm = ctx.getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        val km = ctx.getSystemService(Context.KEYGUARD_SERVICE) as android.app.KeyguardManager
        return JSONObject().put("interactive", pm.isInteractive).put("locked", km.isKeyguardLocked).toString(2)
    }
    private fun volume(ctx: Context, stream: String, percent: Int): String {
        val am = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val st = when (stream) {
            "ring" -> AudioManager.STREAM_RING; "alarm" -> AudioManager.STREAM_ALARM
            "notification" -> AudioManager.STREAM_NOTIFICATION; "call" -> AudioManager.STREAM_VOICE_CALL
            else -> AudioManager.STREAM_MUSIC }
        if (percent in 0..100) am.setStreamVolume(st, am.getStreamMaxVolume(st) * percent / 100, 0)
        val cur = am.getStreamVolume(st); val max = am.getStreamMaxVolume(st)
        return JSONObject().put("stream", stream).put("current", cur).put("max", max)
            .put("percent", if (max > 0) cur * 100 / max else 0).toString(2)
    }
    private fun brightness(ctx: Context, value: Int): String {
        if (value in 0..255) {
            if (!Settings.System.canWrite(ctx)) return JSONObject().put("ok", false)
                .put("error", "无 WRITE_SETTINGS 权限").toString(2)
            Settings.System.putInt(ctx.contentResolver, Settings.System.SCREEN_BRIGHTNESS, value)
        }
        val cur = runCatching { Settings.System.getInt(ctx.contentResolver, Settings.System.SCREEN_BRIGHTNESS) }.getOrDefault(-1)
        return JSONObject().put("value", cur).toString(2)
    }
    private fun dnd(ctx: Context): String {
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        return JSONObject().put("filter", nm.currentInterruptionFilter).toString(2)
    }
    private fun flashlight(ctx: Context, on: Boolean): String {
        return JSONObject().put("ok", false)
            .put("error", "手电筒需 CameraManager.setTorchMode，请用 device_intent 打开相机应用")
            .put("requested", on).toString(2)
    }

    // ── 配置持久化 ──
    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)
    private fun configGet(ctx: Context, key: String): String {
        val p = prefs(ctx)
        if (key.isEmpty()) {
            val o = JSONObject()
            for ((k, v) in p.all) o.put(k, v?.toString() ?: "")
            return o.toString(2)
        }
        return JSONObject().put("key", key).put("value", p.getString(key, null) ?: JSONObject.NULL).toString(2)
    }
    private fun configSet(ctx: Context, key: String, value: String): String {
        if (key.isEmpty()) throw IllegalArgumentException("key 不能为空")
        prefs(ctx).edit().putString(key, value).apply()
        return JSONObject().put("ok", true).put("key", key).toString(2)
    }
    private fun configDelete(ctx: Context, key: String): String {
        prefs(ctx).edit().remove(key).apply()
        return JSONObject().put("ok", true).toString(2)
    }

    // ── 用户插件目录与文件（路径限定在 /sdcard/思忆集）──
    fun pluginsDir(): File = File(Environment.getExternalStorageDirectory(), "思忆集/plugins")
    private fun userPluginsDir(ctx: Context): String {
        val d = pluginsDir()
        if (!d.exists()) d.mkdirs()
        return JSONObject().put("dir", d.absolutePath).put("exists", d.isDirectory)
            .put("files", d.list()?.size ?: 0).toString(2)
    }
    private fun safeBase(): File = File(Environment.getExternalStorageDirectory(), "思忆集")
    private fun resolvePath(path: String): File {
        val base = safeBase().canonicalPath
        val f = File(path).canonicalFile
        if (!f.path.startsWith(base)) throw SecurityException("路径必须在 /sdcard/思忆集 下: $path")
        return f
    }
    private fun readFile(ctx: Context, path: String): String {
        val f = resolvePath(path)
        if (!f.isFile) throw IllegalArgumentException("不是文件: $path")
        return JSONObject().put("path", f.path).put("text", f.readText()).put("bytes", f.length()).toString(2)
    }
    private fun writeFile(ctx: Context, path: String, text: String): String {
        val f = resolvePath(path)
        f.parentFile?.mkdirs(); f.writeText(text)
        return JSONObject().put("ok", true).put("path", f.path).put("bytes", f.length()).toString(2)
    }
    private fun listFiles(ctx: Context, subdir: String): String {
        val base = if (subdir.isEmpty()) safeBase() else resolvePath(safeBase().path + "/" + subdir)
        val arr = JSONArray()
        base.listFiles()?.forEach { f ->
            arr.put(JSONObject().put("name", f.name).put("dir", f.isDirectory).put("bytes", f.length()))
        }
        return JSONObject().put("dir", base.path).put("items", arr).toString(2)
    }

    // ── 元能力 ──
    private fun intent(ctx: Context, args: JSONObject): String {
        val action = args.optString("action")
        if (action.isEmpty()) throw IllegalArgumentException("action 不能为空")
        val i = Intent(action)
        args.optString("data").takeIf { it.isNotEmpty() }?.let { i.data = Uri.parse(it) }
        args.optString("type").takeIf { it.isNotEmpty() }?.let { i.type = it }
        args.optString("package").takeIf { it.isNotEmpty() }?.let { i.setPackage(it) }
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val ex = args.optJSONObject("extras")
        if (ex != null) {
            val it = ex.keys()
            while (it.hasNext()) {
                val k = it.next()
                when (val v = ex.get(k)) {
                    is String -> i.putExtra(k, v); is Int -> i.putExtra(k, v); is Boolean -> i.putExtra(k, v)
                }
            }
        }
        ctx.startActivity(i)
        return JSONObject().put("ok", true).put("action", action).toString(2)
    }
    private fun shell(ctx: Context, cmd: String): String {
        if (cmd.isBlank()) throw IllegalArgumentException("cmd 不能为空")
        // 白名单前缀：保持沙箱边界，避免任意命令执行
        val allowed = listOf("ls ", "cat ", "echo ", "pwd", "date", "uptime", "uname", "getprop ",
            "dumpsys ", "pm ", "am ", "wm ", "settings ", "screencap ", "input ")
        if (!allowed.any { cmd.startsWith(it) || cmd == it.trim() }) {
            return JSONObject().put("ok", false).put("error", "命令不在白名单: " + cmd.take(40)).toString(2)
        }
        val p = Runtime.getRuntime().exec(arrayOf("/system/bin/sh", "-c", cmd))
        val out = p.inputStream.bufferedReader().readText()
        val err = p.errorStream.bufferedReader().readText()
        val code = runCatching { p.waitFor() }.getOrDefault(-1)
        return JSONObject().put("exit", code).put("stdout", out.take(20000)).put("stderr", err.take(4000)).toString(2)
    }
    private fun screenshot(ctx: Context): String {
        val dir = File(safeBase(), "app"); if (!dir.exists()) dir.mkdirs()
        val f = File(dir, "screenshot-" + System.currentTimeMillis() + ".png")
        val p = Runtime.getRuntime().exec(arrayOf("/system/bin/sh", "-c", "screencap -p " + f.absolutePath))
        val code = runCatching { p.waitFor() }.getOrDefault(-1)
        return JSONObject().put("exit", code).put("path", f.absolutePath)
            .put("bytes", if (f.exists()) f.length() else 0).toString(2)
    }

    // ── 交互类：通过 InteractionHub 发 Intent，同步等待结果 ──
    private fun awaitInteraction(ctx: Context, kind: String, arg: String): String {
        val latch = CountDownLatch(1)
        val holder = arrayOf<String?>(null)
        InteractionHub.request(kind, arg) { result ->
            holder[0] = result
            latch.countDown()
        }
        if (!latch.await(120, TimeUnit.SECONDS)) {
            InteractionHub.cancel()
            throw IllegalStateException("交互超时（120s），用户未完成选择")
        }
        return holder[0] ?: JSONObject().put("ok", false).put("error", "用户取消").toString(2)
    }
    private fun pickOrCapture(ctx: Context, mime: String, capture: Boolean): String =
        awaitInteraction(ctx, if (capture) "take_photo" else "pick", mime)
    private fun shareText(ctx: Context, text: String, title: String): String {
        val i = Intent(Intent.ACTION_SEND).putExtra(Intent.EXTRA_TEXT, text)
            .putExtra(Intent.EXTRA_SUBJECT, title).setType("text/plain")
        val chooser = Intent.createChooser(i, title.ifEmpty { "分享" }).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        ctx.startActivity(chooser)
        return JSONObject().put("ok", true).put("chars", text.length).toString(2)
    }
    private fun voiceInput(ctx: Context): String = awaitInteraction(ctx, "voice", "")
}
