package com.siyi.notepad.miui

import java.io.BufferedReader
import java.io.InputStreamReader

/**
 * Root shell 工具 — 通过 `su -c` 执行 root 命令。
 *
 * 移植自旧项目 com.siyi.app.miui.RootShell，包名改为 com.siyi.notepad.miui。
 *
 * 仅当用户的 Redmi 已 root 时可用。未 root 设备 isRootAvailable() 返回 false。
 */
object RootShell {

    data class Result(val exitCode: Int, val stdout: String, val stderr: String) {
        val isSuccess: Boolean get() = exitCode == 0
    }

    /** 检测设备是否有 root 访问权限。 */
    fun isRootAvailable(): Boolean {
        return try {
            val process = ProcessBuilder("which", "su")
                .redirectErrorStream(true)
                .start()
            val reader = BufferedReader(InputStreamReader(process.inputStream))
            val line = reader.readLine()
            process.waitFor()
            line != null && line.isNotBlank()
        } catch (_: Exception) {
            false
        }
    }

    /** 通过 `su -c` 执行单条 shell 命令。仅本文件内部使用，避免外部传入未净化命令。 */
    private fun run(command: String): Result {
        return try {
            val process = ProcessBuilder("su", "-c", command)
                .redirectErrorStream(false)
                .start()
            val stdout = BufferedReader(InputStreamReader(process.inputStream)).readText()
            val stderr = BufferedReader(InputStreamReader(process.errorStream)).readText()
            val exitCode = process.waitFor()
            Result(exitCode, stdout.trim(), stderr.trim())
        } catch (e: Exception) {
            Result(-1, "", "RootShell failed: ${e.message}")
        }
    }

    /** 用 root 复制文件（绕过 app 沙箱）。 */
    fun copyWithRoot(source: String, target: String): Result =
        run("cp -f \"$source\" \"$target\" && chmod 644 \"$target\"")
}
