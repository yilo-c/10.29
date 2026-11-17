package com.siyi.dshapp

import android.content.Context
import android.content.pm.PackageManager
import org.json.JSONObject
import rikka.shizuku.Shizuku
import java.io.File

/**
 * Shizuku 静默安装（B5）。
 *
 * 背景：Android 上想免点击装 APK 只有两条路 ——
 *   Device Owner / 系统签名（要刷机或企业配置），
 *   或者 Shizuku（用户手动启动一次服务，之后 App 借用它的 adb shell 权限）。
 * 方案里定的是 Shizuku。
 *
 * ── 这个文件修过两次真 bug（2026-11-17）────────────────────────────
 *
 * 第一次：它其实是个「半成品」。
 *   代码用反射调 rikka.shizuku.Shizuku，但 build.gradle 没加 shizuku 依赖、
 *   Manifest 没注册 ShizukuProvider，dex 里根本没有 rikka/shizuku 类。
 *   Class.forName 必然抛 ClassNotFoundException 被 runCatching 吞掉，
 *   于是 status 永远返回 not_installed、install 永远失败 ——
 *   工具和入口全都在，底层完全没接上。
 *   修法：加 dev.rikka.shizuku:api + :provider 依赖，注册 Provider，
 *   并改成直接 import 官方类（编译期可检查，比反射可靠）。
 *
 * 第二次：checkSelfPermission 的返回类型判错了。
 *   官方签名是 public static int checkSelfPermission()，
 *   返回 PackageManager.PERMISSION_GRANTED(0) / PERMISSION_DENIED(-1)。
 *   原来写 as? Boolean ?: false，类型转换永远失败，结果恒为「未授权」。
 *   现在直接按 Int 比较。
 *
 * ── 唯一保留反射的地方 ────────────────────────────────────────────
 * Shizuku.newProcess 在 13.1.5 里是 private（Shizuku.java:491），
 * 公开 API 没有等价入口。要执行 pm install 只能反射调它。
 * 已在 Sources jar 里核实签名：
 *   private static ShizukuRemoteProcess newProcess(String[] cmd, String[] env, String dir)
 * ShizukuRemoteProcess extends Process，所以反射拿到的对象能当 Process 用。
 */
object ShizukuInstaller {

    /**
     * Shizuku 服务端 App 的包名。
     *
     * ⚠ 这里踩过真 bug（2026-11-17 实测发现）：
     * 原来写的是 "dev.rikka.shizuku"，那是 Shizuku 的 **开发库命名空间**
     * （dev.rikka.shizuku:api 这个 Maven 坐标），不是手机上的 App 包名。
     * 结果用户的手机明明装了 Shizuku 13.5.4（moe.shizuku.privileged.api），
     * device_shizuku_status 却一直报 not_installed —— 不是旧 bug 的复现，
     * 是包名查错了，永远查不到。
     *
     * 真实包名是 moe.shizuku.privileged.api。保留 dev.rikka.shizuku 作兜底，
     * 因为个别分支/魔改版可能沿用旧名字，多查一个没有副作用。
     */
    private val APP_PKGS = listOf(
        "moe.shizuku.privileged.api",
        "dev.rikka.shizuku"
    )
    private const val REQ_CODE = 1001

    /** 授权结果监听器是否已注册（避免重复注册）。 */
    @Volatile
    private var listenerRegistered = false

    /** Shizuku 服务端 App 是否装在设备上。 */
    private fun shizukuPresent(ctx: Context): Boolean = APP_PKGS.any { pkg ->
        runCatching {
            ctx.packageManager.getPackageInfo(pkg, 0)
            true
        }.getOrDefault(false)
    }

    /**
     * 注册授权结果监听器。
     *
     * 官方 requestPermission 只负责发起，结果通过
     * OnRequestPermissionResultListener 回调 —— 不注册的话，
     * 用户点了「允许」App 也不知道，状态会一直停在未授权。
     * 进程内只注册一次。
     */
    private fun ensureListener() {
        if (listenerRegistered) return
        synchronized(this) {
            if (listenerRegistered) return
            runCatching {
                Shizuku.addRequestPermissionResultListener { code, grant ->
                    runCatching {
                        System.err.println(
                            "[shizuku] 授权结果 code=" + code + " grant=" + grant +
                                (if (grant == PackageManager.PERMISSION_GRANTED) "（已允许）" else "（被拒绝）")
                        )
                    }
                }
                listenerRegistered = true
            }
        }
    }

    /**
     * 状态查询。四种状态要分清，否则用户不知道该干什么：
     *   not_installed  —— 手机没装 Shizuku，去装
     *   not_running    —— 装了但服务没起，去启动
     *   not_granted    —— 服务在跑但没授权，去授权
     *   ready          —— 可用
     */
    fun status(ctx: Context): String {
        if (!shizukuPresent(ctx)) {
            return JSONObject().put("ok", true)
                .put("state", "not_installed")
                .put("available", false)
                .put("hint", "未安装 Shizuku。可从应用商店或 GitHub 安装后启动服务，再用它免点击安装 APK")
                .toString(2)
        }
        val ping = runCatching { Shizuku.pingBinder() }.getOrDefault(false)
        if (!ping) {
            return JSONObject().put("ok", true)
                .put("state", "not_running")
                .put("available", false)
                .put("hint", "Shizuku 已安装但服务未运行，请打开 Shizuku App 启动服务")
                .toString(2)
        }
        val granted = runCatching {
            Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
        }.getOrDefault(false)
        return JSONObject().put("ok", true)
            .put("state", if (granted) "ready" else "not_granted")
            .put("available", granted)
            .put("version", runCatching { Shizuku.getVersion() }.getOrDefault(-1))
            .put("uid", runCatching { Shizuku.getUid() }.getOrDefault(-1))
            .put("hint", if (granted) "Shizuku 可用，可以免点击安装"
            else "Shizuku 服务在运行，但需要在本应用里授权一次")
            .toString(2)
    }

    /** 请求 Shizuku 授权（会弹系统确认框）。 */
    fun requestPermission(ctx: Context): String {
        val st = JSONObject(status(ctx))
        val state = st.optString("state")
        if (state == "not_installed" || state == "not_running") return st.toString(2)
        ensureListener()
        runCatching {
            Shizuku.requestPermission(REQ_CODE)
        }.onFailure { t ->
            return JSONObject().put("ok", false)
                .put("error", "请求授权失败: ${t.javaClass.simpleName}")
                .put("detail", (t.message ?: "").take(200)).toString(2)
        }
        return JSONObject().put("ok", true)
            .put("hint", "已发起授权请求，请在系统弹框里点允许").toString(2)
    }

    /**
     * 用 Shizuku 执行 pm install 安装 APK。
     *
     * 走 shell 的 pm install 才是真静默；PackageInstaller 在 Android 8+
     * 强制要求用户点确认，达不到「免点击」的目的。
     *
     * newProcess 是私有方法（见文件头），所以这里用反射。
     */
    fun install(ctx: Context, apkPath: String): String {
        val f = File(apkPath)
        if (!f.exists()) return JSONObject().put("ok", false)
            .put("error", "APK 不存在: $apkPath").toString(2)
        if (!f.canRead()) return JSONObject().put("ok", false)
            .put("error", "APK 不可读（权限问题）: $apkPath").toString(2)
        if (!f.name.endsWith(".apk")) return JSONObject().put("ok", false)
            .put("error", "文件扩展名不是 .apk").toString(2)

        val st = JSONObject(status(ctx))
        val state = st.optString("state")
        if (state != "ready") {
            return JSONObject().put("ok", false)
                .put("error", "Shizuku 不可用（" + state + "）")
                .put("status", st)
                .put("fallback", "可以手动点安装：设置里打开「未知来源」，用文件管理器点这个 APK")
                .put("apkPath", apkPath).toString(2)
        }

        // pm install -r -d <path>：-r 覆盖安装，-d 允许降级
        var out = ""
        var code = -1
        runCatching {
            val m = Shizuku::class.java.getDeclaredMethod(
                "newProcess",
                Array<String>::class.java, Array<String>::class.java, String::class.java
            )
            m.isAccessible = true
            val cmd = arrayOf("sh", "-c", "pm install -r -d " + shellQuote(f.absolutePath))
            val env = arrayOf("PATH=/sbin:/system/sbin:/system/bin:/system/xbin")
            val proc = m.invoke(null, cmd, env, "/") as? Process
                ?: throw IllegalStateException("newProcess 返回 null")
            out = (proc.inputStream.bufferedReader().readText() +
                proc.errorStream.bufferedReader().readText()).take(4000)
            code = runCatching { proc.waitFor() }.getOrDefault(-1)
        }.onFailure { t ->
            return JSONObject().put("ok", false)
                .put("error", "调用 Shizuku 失败: ${t.javaClass.simpleName}")
                .put("detail", (t.message ?: "").take(300)).toString(2)
        }

        val success = code == 0 && out.contains("Success", ignoreCase = true)
        return JSONObject().put("ok", success)
            .put("exit", code)
            .put("output", out)
            .put("apkPath", apkPath)
            .apply { if (!success) put("hint", "常见原因：签名不一致需要先卸载、或 APK 损坏") }
            .toString(2)
    }

    /** shell 单引号转义，防止路径里的空格和特殊字符破坏命令。 */
    private fun shellQuote(s: String): String =
        "'" + s.replace("'", "'\\''") + "'"
}
