package com.siyi.dshapp

import android.content.Context
import android.system.Os
import java.io.File
import java.util.zip.ZipInputStream

/**
 * 首次启动准备引擎环境：
 *  1. assets/engine.zip        → filesDir/engine（引擎树）
 *  2. assets/web-profile.zip   → filesDir/dsh-home（profile + 54 个 client-ui）
 *
 * 关键：三个 Android 定制包必须与引擎树是同一实例，否则
 *   - dsh-app-boot 双实例 → bootstrapIncludes 不共享 → settings 写不进（预览弹窗关不掉）
 *   - node-addon-system 原版 → flock is not supported on android-arm64
 *   - dsh-session-persistence-jsonl 原版 → link() EACCES
 * 所以解压后把 profile 里这三个目录换成指向引擎树的符号链接。
 */
object EngineInstaller {

    private const val MARK_ENGINE = ".engine-v2"
    private const val MARK_PROFILE = ".profile-v2"

    private val UNIFY_PKGS = listOf(
        "dsh-app-boot",
        "node-addon-system",
        "dsh-session-persistence-jsonl",
    )

    fun ensureAll(ctx: Context): String? {
        ensureEngine(ctx)?.let { return it }
        ensureProfile(ctx)?.let { return it }
        return unifyAndroidPackages(ctx)
    }

    private fun unzipAsset(ctx: Context, asset: String, dest: File): String? = try {
        ctx.assets.open(asset).use { input ->
            ZipInputStream(input.buffered()).use { zip ->
                var e = zip.nextEntry
                while (e != null) {
                    val out = File(dest, e.name)
                    if (!out.canonicalPath.startsWith(dest.canonicalPath + File.separator)) {
                        e = zip.nextEntry; continue
                    }
                    if (e.isDirectory) out.mkdirs()
                    else {
                        out.parentFile?.mkdirs()
                        out.outputStream().use { zip.copyTo(it) }
                    }
                    e = zip.nextEntry
                }
            }
        }
        null
    } catch (t: Throwable) {
        "$asset 解压失败: ${t.javaClass.simpleName}: ${t.message}"
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
        if (mark.exists() && File(home, "profiles/web/package.json").exists()) return null
        deleteRecursively(home); home.mkdirs()
        unzipAsset(ctx, "web-profile.zip", home)?.let { return it }
        mark.writeText("ok")
        return null
    }

    /** 把 profile 里三个包换成指向引擎树的 symlink，保证模块单实例 */
    private fun unifyAndroidPackages(ctx: Context): String? {
        val engineAi = File(EnginePaths.engineRoot(ctx), "node_modules/@deepseek-ai")
        val profileAi = File(EnginePaths.webProfile(ctx), "node_modules/@deepseek-ai")
        if (!engineAi.isDirectory) return "引擎树缺少 @deepseek-ai"
        if (!profileAi.isDirectory) return "profile 缺少 @deepseek-ai"

        for (name in UNIFY_PKGS) {
            val target = File(engineAi, name)
            val link = File(profileAi, name)
            if (!target.isDirectory) return "引擎树缺少 $name"
            runCatching {
                if (link.exists() || link.isSymbolicLink()) deleteRecursively(link)
                Os.symlink(target.absolutePath, link.absolutePath)
            }.onFailure {
                return "无法建立 $name 符号链接: ${it.message}"
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

    private fun File.isSymbolicLink(): Boolean =
        try { return java.nio.file.Files.isSymbolicLink(toPath()) } catch (_: Throwable) { return false }
}
