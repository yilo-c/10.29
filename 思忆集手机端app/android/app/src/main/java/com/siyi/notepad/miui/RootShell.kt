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

    /** 通过 `su -c` 执行单条 shell 命令。 */
    fun run(command: String): Result {
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

    /**
     * 带超时的 root 执行：防止某条命令挂住导致 App 侧永久等待。
     *
     * 实现说明：Java 侧没有原生的「杀进程树」能力，这里用 waitFor(timeout)
     * 加 destroyForcibly 兜底；su 派生的子进程可能残留，属已知限制，
     * 因此调用方应尽量使用有界命令（如加 timeout 参数）。
     */
    fun runWithTimeout(command: String, timeoutMs: Long = 15_000L): Result {
        return try {
            val process = ProcessBuilder("su", "-c", command)
                .redirectErrorStream(false)
                .start()
            val done = process.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)
            if (!done) {
                process.destroyForcibly()
                return Result(-1, "", "RootShell timeout after ${timeoutMs}ms")
            }
            val stdout = BufferedReader(InputStreamReader(process.inputStream)).readText()
            val stderr = BufferedReader(InputStreamReader(process.errorStream)).readText()
            Result(process.exitValue(), stdout.trim(), stderr.trim())
        } catch (e: Exception) {
            Result(-1, "", "RootShell failed: ${e.message}")
        }
    }

    /** 用 root 复制文件（绕过 app 沙箱）。 */
    fun copyWithRoot(source: String, target: String): Result =
        run("cp -f \"$source\" \"$target\" && chmod 644 \"$target\"")
}
