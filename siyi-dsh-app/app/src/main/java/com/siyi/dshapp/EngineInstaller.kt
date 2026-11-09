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
        return unifyAndroidPackages(ctx)
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
