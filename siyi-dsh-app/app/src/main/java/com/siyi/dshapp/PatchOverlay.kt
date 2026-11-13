package com.siyi.dshapp

import android.content.Context
import java.io.File

/**
 * 补丁持久化层。
 *
 * ## 背景
 *
 * App 更新时 EngineInstaller 会用 APK 里的 engine.zip / web-profile.zip
 * 整体重写 files/engine 与 files/dsh-home/profiles —— 任何手工打到
 * node_modules 里的修正都会随之丢失，每次更新都要重打一遍。
 *
 * ## 方案
 *
 * 把补丁放在**共享存储**的固定目录（不随 APK 更新、不随清数据丢失）：
 *
 *   /sdcard/思忆集/patches/
 *     ├── profile/   对应 files/dsh-home/profiles/web/node_modules/
 *     └── engine/    对应 files/engine/node_modules/
 *
 * 目录结构按「相对 node_modules 的路径」原样摆放。例如终端补丁：
 *
 *   profile/node-pty/lib/index.js
 *   profile/node-pty/lib/android-pty-bridge.js
 *   profile/@snnh/dsh-llm-service-lite/lib/index.js
 *
 * 每次 EngineInstaller 完成解压后调用 apply()：按相对路径逐个覆盖回去。
 * 因此：
 *   - 更新 APK → 解压覆盖 → 立刻自动重放，补丁不丢
 *   - 普通重启 → 解压被跳过，但 apply() 仍会跑，保证幂等
 *   - 想要撤销 → 删掉 /sdcard/思忆集/patches 下的对应文件即可
 *
 * ## 幂等与安全
 *
 * - 只覆盖明确列出的文件，不动其它任何东西
 * - 指纹（文件数 + 总大小 + 修改时间）不变时跳过，避免每次启动重复 IO
 * - 补丁目录不存在或为空 → 直接返回，不影响正常启动
 * - 单个文件失败不阻断整体，只记日志
 */
object PatchOverlay {

    private val SRC_ROOT = File("/sdcard/思忆集/patches")

    /** 两个目标：profile 与 engine 的 node_modules。 */
    private data class Target(val name: String, val dir: (Context) -> File)

    private val TARGETS = listOf(
        Target("profile") { ctx ->
            File(EnginePaths.webProfile(ctx), "node_modules")
        },
        Target("engine") { ctx ->
            File(EnginePaths.engineRoot(ctx), "node_modules")
        }
    )

    /** 把共享存储的补丁重放到目标目录。 */
    @Synchronized
    fun apply(ctx: Context) {
        if (!SRC_ROOT.isDirectory) return
        for (t in TARGETS) {
            val src = File(SRC_ROOT, t.name)
            if (!src.isDirectory) continue
            val dst = t.dir(ctx)
            if (!dst.isDirectory) continue
            val files = collect(src)
            if (files.isEmpty()) continue
            val fp = fingerprint(files)
            val mark = File(ctx.filesDir, ".patch-" + t.name)
            val prev = if (mark.exists()) runCatching { mark.readText() }.getOrDefault("") else ""
            if (prev == fp) {
                AppLog.write("boot.log", "补丁 " + t.name + " 无变化，跳过（" + files.size + " 个文件）")
                continue
            }
            var ok = 0
            var fail = 0
            for ((rel, f) in files) {
                val out = File(dst, rel)
                runCatching {
                    out.parentFile?.mkdirs()
                    f.inputStream().use { i -> out.outputStream().use { o -> i.copyTo(o) } }
                    ok++
                }.onFailure {
                    fail++
                    AppLog.write("boot.log", "补丁覆盖失败 " + t.name + "/" + rel + ": " + it.message)
                }
            }
            runCatching { mark.writeText(fp) }
            AppLog.write("boot.log", "补丁 " + t.name + " 已重放: ok=" + ok + " fail=" + fail)
        }
    }

    /** 递归收集补丁文件，返回 (相对路径, 文件) 列表。跳过隐藏文件与 .bak。 */
    private fun collect(root: File): List<Pair<String, File>> {
        val out = ArrayList<Pair<String, File>>()
        fun walk(dir: File, prefix: String) {
            dir.listFiles()?.forEach { f ->
                val rel = if (prefix.isEmpty()) f.name else prefix + "/" + f.name
                if (f.isDirectory) {
                    if (!f.name.startsWith(".")) walk(f, rel)
                } else if (!f.name.startsWith(".") && ".bak" !in f.name) {
                    out.add(rel to f)
                }
            }
        }
        walk(root, "")
        return out
    }

    /** 指纹：文件数 + 总字节数 + 最新修改时间。内容变了或文件增删都会变。 */
    private fun fingerprint(files: List<Pair<String, File>>): String {
        var size = 0L
        var newest = 0L
        for (_, f) in files {
            size += f.length()
            if (f.lastModified() > newest) newest = f.lastModified()
        }
        return files.size.toString() + "-" + size + "-" + newest
    }
}
