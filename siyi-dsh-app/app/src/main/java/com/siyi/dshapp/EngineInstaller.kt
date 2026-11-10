package com.siyi.dshapp

import android.content.Context
import android.system.Os
import java.io.File
import java.util.zip.ZipInputStream

/**
 * 首次启动准备引擎环境：
 *  1. assets/engine.zip      → filesDir/engine（引擎树，248 包）
 *  2. assets/web-profile.zip → filesDir/dsh-home（profile + 264 包，31212 个文件）
 *
 * 关键：三个 Android 定制包必须与引擎树同实例，否则
 *   - dsh-app-boot 双实例 → settings 写不进（预览弹窗关不掉）
 *   - node-addon-system 原版 → flock is not supported on android-arm64
 *   - dsh-session-persistence-jsonl 原版 → link() EACCES
 * 解压后把 profile 里这三个目录换成指向引擎树的符号链接。
 *
 * 解压带进度回调与完整性校验：web-profile 有 3 万多个文件，
 * 必须确认全部解开，否则前端资源缺失会表现为「页面一直加载」。
 */
object EngineInstaller {

    private const val MARK_ENGINE = ".engine-v3"
    private const val MARK_PROFILE = ".profile-v3"

    private val UNIFY_PKGS = listOf(
        "dsh-app-boot",
        "node-addon-system",
        "dsh-session-persistence-jsonl",
    )

    /** 进度回调：(已解压文件数, 说明文本) */
    var onProgress: ((Int, String) -> Unit)? = null

    fun ensureAll(ctx: Context): String? {
        ensureEngine(ctx)?.let { return it }
        ensureProfile(ctx)?.let { return it }
        unifyAndroidPackages(ctx)?.let { return it }
        return ensureBinLinks(ctx)
    }

    /** filesDir/bin 目录：放 node 与 rg 的可执行软链，供 shell 工具与 glob 使用。 */
    fun binDir(ctx: Context): File = File(ctx.filesDir, "bin")

    /**
     * 官方 DSH 在 filesDir/bin 下建了 node 与 rg 两个软链，指向 APK 内的 .so，
     * 并把该目录加进 PATH。思忆集缺这一步，导致：
     *   - glob / 内容搜索找不到 rg → ripgrep launch failed
     *   - 子进程类工具找不到可用的 node
     * 这里按同样方式补齐；链接目标用 nativeLibraryDir 里的真实 .so。
     */
    private fun ensureBinLinks(ctx: Context): String? {
        val bin = binDir(ctx)
        if (!bin.isDirectory) bin.mkdirs()
        val lib = ctx.applicationInfo.nativeLibraryDir
        val pairs = arrayOf(
            "node" to "libdshnode.so",
            "rg" to "librg.so",
        )
        for ((name, so) in pairs) {
            val target = File(lib, so)
            if (!target.exists()) continue // APK 没带就跳过，不阻断启动
            val link = File(bin, name)
            if (link.exists() || link.isSymbolicLink()) runCatching { deleteRecursively(link) }
            try {
                Os.symlink(target.absolutePath, link.absolutePath)
                runCatching {
                    val osCls = Class.forName("android.system.Os")
                    for (m in osCls.declaredMethods) {
                        if (m.name == "chmod") {
                            try { m.invoke(null, link.absolutePath, 493); break } catch (_: Throwable) {}
                        }
                    }
                }
            } catch (t: Throwable) {
                // 软链失败时退化为复制（极少数 ROM 不允许私有目录建链）
                try {
                    target.copyTo(link, overwrite = true)
                    link.setExecutable(true, false)
                } catch (_: Throwable) {}
            }
        }
        return null
    }

    private fun unzipAsset(ctx: Context, asset: String, dest: File): String? = try {
        var count = 0
        ctx.assets.open(asset).use { input ->
            ZipInputStream(input.buffered(1 shl 16)).use { zip ->
                var e = zip.nextEntry
                while (e != null) {
                    val out = File(dest, e.name)
                    if (!out.canonicalPath.startsWith(dest.canonicalPath + File.separator)) {
                        e = zip.nextEntry; continue
                    }
                    if (e.isDirectory) {
                        out.mkdirs()
                    } else {
                        out.parentFile?.mkdirs()
                        out.outputStream().buffered(1 shl 16).use { zip.copyTo(it) }
                        count++
                        if (count % 500 == 0) onProgress?.invoke(count, asset)
                    }
                    e = zip.nextEntry
                }
            }
        }
        onProgress?.invoke(count, "$asset 完成")
        null
    } catch (t: Throwable) {
        "$asset 解压失败（已解 $asset 前若干文件）: ${t.javaClass.simpleName}: ${t.message}"
    }

    private fun ensureEngine(ctx: Context): String? {
        val root = EnginePaths.engineRoot(ctx)
        val mark = File(root, MARK_ENGINE)
        if (mark.exists() && File(root, "node_modules/@deepseek-ai/dsh/lib/bin.js").exists()) return null
        deleteRecursively(root); root.mkdirs()
        unzipAsset(ctx, "engine.zip", root)?.let { return it }
        mark.writeText("ok")
        return null
    }

    private fun ensureProfile(ctx: Context): String? {
        val home = EnginePaths.dshHome(ctx)
        val mark = File(home, MARK_PROFILE)
        val pkg = File(home, "profiles/web/package.json")
        val boot = File(home, "profiles/web/node_modules/@deepseek-ai/dsh-app-boot")
        if (mark.exists() && pkg.exists() && boot.exists()) return null
        deleteRecursively(home); home.mkdirs()
        unzipAsset(ctx, "web-profile.zip", home)?.let { return it }
        if (!pkg.exists()) return "web-profile.zip 解压后缺少 profiles/web/package.json"
        if (!boot.exists()) return "web-profile.zip 解压后缺少 dsh-app-boot"
        mark.writeText("ok")
        return null
    }

    private fun unifyAndroidPackages(ctx: Context): String? {
        val engineAi = File(EnginePaths.engineRoot(ctx), "node_modules/@deepseek-ai")
        val profileAi = File(EnginePaths.webProfile(ctx), "node_modules/@deepseek-ai")
        if (!engineAi.isDirectory) return "引擎树缺少 @deepseek-ai"
        if (!profileAi.isDirectory) return "profile 缺少 @deepseek-ai"

        for (name in UNIFY_PKGS) {
            val target = File(engineAi, name)
            val link = File(profileAi, name)
            if (!target.isDirectory) return "引擎树缺少 $name"
            try {
                if (link.exists() || link.isSymbolicLink()) deleteRecursively(link)
                Os.symlink(target.absolutePath, link.absolutePath)
            } catch (t: Throwable) {
                return "无法建立 $name 符号链接: ${t.javaClass.simpleName}: ${t.message}"
            }
        }
        return linkDevicePlugin(ctx)
    }

    /**
     * @dsh-android 下的包不在 npm 上（404），只存在于官方 APK 的 engine.zip 里。
     * web profile 的 pnpm 安装拿不到它们，必须从引擎树链过来：
     *   - plugin-device  ：设备能力桥（device_* 工具）
     *   - plugin-account ：OAuth 登录的本地回调服务器，缺了它登录页白屏
     */
    private val ANDROID_PKGS = listOf("plugin-device", "plugin-account")

    /**
     * @dsh-android/plugin-device 不在 npm 上（404），只存在于官方 APK 的 engine.zip 里。
     * web profile 的 pnpm 安装拿不到它，所以从引擎树链过来；
     * 缺了它，App 传过去的 DSH_BRIDGE_FD 没人读，device_* 工具不会注册。
     * 这个包不参与「双实例」问题（profile 原本没有它），链过去只是补一个入口。
     */
    private fun linkDevicePlugin(ctx: Context): String? {
        val scope = File(EnginePaths.webProfile(ctx), "node_modules/@dsh-android")
        if (!scope.isDirectory) scope.mkdirs()
        for (pkg in ANDROID_PKGS) {
            val target = File(EnginePaths.engineRoot(ctx), "node_modules/@dsh-android/$pkg")
            if (!target.isDirectory) continue // 引擎树没有就跳过，不阻断启动
            val link = File(scope, pkg)
            if (link.isSymbolicLink()) continue
            try {
                if (link.exists()) deleteRecursively(link)
                Os.symlink(target.absolutePath, link.absolutePath)
            } catch (t: Throwable) {
                return "无法链接 $pkg: ${t.javaClass.simpleName}: ${t.message}"
            }
        }
        return null
    }

    private fun deleteRecursively(f: File) {
        if (f.isDirectory && !f.isSymbolicLink()) {
            f.listFiles()?.forEach { deleteRecursively(it) }
        }
        f.delete()
    }

    private fun File.isSymbolicLink(): Boolean {
        return try { java.nio.file.Files.isSymbolicLink(toPath()) } catch (_: Throwable) { false }
    }
}
