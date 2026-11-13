package com.siyi.dshapp

import android.app.Application
import java.io.PrintWriter
import java.io.StringWriter

/**
 * 应用入口：只做两件事 —— 装全局崩溃捕获、清理过期日志。
 *
 * 之前没有任何兜底：引擎或 App 崩溃时，除了 logcat（重启就滚掉）什么线索都不留，
 * 用户看到的只有「白屏 / 闪退」，排查全靠猜。
 */
class App : Application() {

    override fun onCreate() {
        super.onCreate()
        installCrashHandler()
        // 自检钩子：验证「崩溃捕获是否真的落盘」—— 这件事只有真崩一次才知道。
        //
        // ⚠ 用文件而不是广播触发。广播要在 Manifest 里注册 receiver 并导出，
        //   那会给外部开一个能主动让 App 崩溃的入口，是安全风险。
        //   改成监视一个哨兵文件：外部（adb / 测试脚本）创建它即可触发。
        val sentinel = java.io.File(filesDir, "test-crash.flag")
        if (sentinel.exists()) {
            // 启动时发现哨兵：先删掉，避免崩溃重启后无限循环
            runCatching { sentinel.delete() }
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                // 抛真实异常，走完整的未捕获异常路径
                throw IllegalStateException("思忆集崩溃捕获自检（这是故意抛的）")
            }, 1500)
        }
    }

    /**
     * 全局未捕获异常捕获。
     *
     * 记下完整堆栈再交给系统默认处理（该崩还是崩，只是留下证据）。
     * 写的是 crash.log，走 AppLog 所以会自动轮转。
     */
    private fun installCrashHandler() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            runCatching {
                val sw = StringWriter()
                error.printStackTrace(PrintWriter(sw))
                AppLog.write(
                    "crash.log",
                    "=== 未捕获异常 thread=" + thread.name + " ===\n" + sw.toString()
                )
            }
            // 交回系统，保持标准的崩溃行为（不要吞掉异常）
            previous?.uncaughtException(thread, error)
        }
    }
}
