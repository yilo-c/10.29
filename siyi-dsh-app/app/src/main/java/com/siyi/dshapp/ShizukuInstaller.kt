package com.siyi.dshapp

import android.content.Context
import android.content.pm.PackageManager
import org.json.JSONObject
import java.io.File

/**
 * Shizuku 静默安装（B5）。
 *
 * 背景：用户明确说安装自己来（「安装我可以自己安装」），
 * 而 Android 上想免点击装 APK 只有两条路：
 *   1. Device Owner / 系统签名 —— 需要刷机或企业配置，不现实；
 *   2. Shizuku —— 用户手动启动一次 Shizuku 服务，之后 App 可借用它的
 *      adb shell 权限执行 pm install。
 * 这条路方案里已经定了，这里按它实现。
 *
 * 关键约束：Shizuku 的 API 依赖 dev.rikka.shizuku:api 这个库。
 * 本文件刻意**不直接 import 那个库的任何类**，而是全程用反射 ——
 * 原因是：
 *   a) 如果构建时没有该依赖，直接 import 会让整个模块编译失败；
 *   b) 反射让「没装 Shizuku」的机器也能正常跑，只是这个能力不可用；
 *   c) 用户可能根本没装 Shizuku，这时不该影响 App 其他部分。
 *
 * 反射契约（Shizuku 13.x）：
 *   Shizuku.pingBinder()                     → 服务是否在跑
 *   Shizuku.checkSelfPermission()            → 是否已授权本应用
 *   Shizuku.requestPermission(code)          → 请求授权
 *   Shizuku.newProcess(cmd[], env[], dir)    → 以 shell 身份起进程
 */
object ShizukuInstaller {

    private const val PKG = "dev.rikka.shizuku"

    /** Shizuku 相关类是否在设备上（App 是否装了 Shizuku）。 */
    private fun shizukuPresent(ctx: Context): Boolean = runCatching {
        ctx.packageManager.getPackageInfo(PKG, 0)
        true
    }.getOrDefault(false)

    /** 反射拿 Shizuku 类，拿不到返回 null。 */
    private fun shizukuClass(): Class<*>? = runCatching {
        Class.forName("rikka.shizuku.Shizuku")
    }.getOrNull()

    private fun call(name: String, vararg types: Class<*>): Any? = runCatching {
        val c = shizukuClass() ?: return@runCatching null
        val m = c.getMethod(name, *types)
        m.invoke(null)
    }.getOrNull()

    /**
     * 状态查询：给设置页和工具调用用。
     *
     * 四种状态要分清，否则用户不知道该干什么：
     *   not_installed  —— 手机没装 Shizuku，去装
     *   not_running    —— 装了但服务没起，去启动
     *   not_granted    —— 服务在跑但没授权，去授权
     *   ready          —— 可用
     */
    fun status(ctx: Context): String {
        val present = shizukuPresent(ctx)
        val cls = shizukuClass()
        if (!present || cls == null) {
            return JSONObject().put("ok", true)
                .put("state", "not_installed")
                .put("available", false)
                .put("hint", "未安装 Shizuku。可从应用商店或 GitHub 安装后启动服务，再用它免点击安装 APK")
                .toString(2)
        }
        val ping = call("pingBinder") as? Boolean ?: false
        if (!ping) {
            return JSONObject().put("ok", true)
                .put("state", "not_running")
                .put("available", false)
                .put("hint", "Shizuku 已安装但服务未运行，请打开 Shizuku App 启动服务")
                .toString(2)
        }
        val granted = runCatching {
            val c = shizukuClass() ?: return@runCatching false
            c.getMethod("checkSelfPermission").invoke(null) as? Boolean ?: false
        }.getOrDefault(false)
        return JSONObject().put("ok", true)
            .put("state", if (granted) "ready" else "not_granted")
            .put("available", granted)
            .put("hint", if (granted) "Shizuku 可用，可以免点击安装"
            else "Shizuku 服务在运行，但需要在本应用里授权一次")
            .toString(2)
    }

    /** 请求 Shizuku 授权（会弹系统确认框）。 */
    fun requestPermission(ctx: Context): String {
        val st = JSONObject(status(ctx))
        if (st.optString("state") == "not_installed") return st.toString(2)
        if (st.optString("state") == "not_running") return st.toString(2)
        runCatching {
            val c = shizukuClass() ?: return@runCatching
            // requestPermission(int) 需要 Activity 上下文才能弹框，
            // 这里传 0 作为 requestCode，实际行为由 Shizuku 自己管理
            val m = c.getMethod("requestPermission", Int::class.javaPrimitiveType)
            m.invoke(null, 0)
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
     * 为什么走 newProcess 而不是直接调 PackageInstaller：
     *   PackageInstaller 需要用户点确认（Android 8+ 强制）。
     *   走 shell 的 pm install 才是真静默，而 shell 权限只能从 Shizuku 借。
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
                .put("error", "Shizuku 不可用（$state）")
                .put("status", st)
                .put("fallback", "可以手动点安装：设置里打开「未知来源」，用文件管理器点这个 APK")
                .put("apkPath", apkPath).toString(2)
        }

        // pm install -r -d <path>
        //   -r 覆盖安装（保留数据的前提是签名一致）
        //   -d 允许降级（调试时有用）
        // Shizuku.newProcess(String[] cmd, String[] env, String dir)
        var out = ""
        var code = -1
        runCatching {
            val cls = shizukuClass() ?: throw IllegalStateException("Shizuku 类不可用")
            val m = cls.getMethod(
                "newProcess",
                Array<String>::class.java, Array<String>::class.java, String::class.java
            )
            val cmd = arrayOf("sh", "-c", "pm install -r -d " + shellQuote(f.absolutePath))
            val env = arrayOf("PATH=/sbin:/system/sbin:/system/bin:/system/xbin")
            val p = m.invoke(null, cmd, env, "/") as? Process
                ?: throw IllegalStateException("newProcess 返回 null")
            out = (p.inputStream.bufferedReader().readText() +
                p.errorStream.bufferedReader().readText()).take(4000)
            code = runCatching { p.waitFor() }.getOrDefault(-1)
        }.onFailure { t ->
            return JSONObject().put("ok", false)
                .put("error", "调用 Shizuku 失败: ${t.javaClass.simpleName}")
                .put("detail", (t.message ?: "").take(300)).toString(2)
        }

        // pm install 成功时 exit=0 且输出含 Success
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
