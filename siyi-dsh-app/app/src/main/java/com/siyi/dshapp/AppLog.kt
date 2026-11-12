package com.siyi.dshapp

import java.io.File

/**
 * 统一日志出口：带大小上限和轮转。
 *
 * 之前每个模块各自 `File(...).appendText(...)` 往 /sdcard/思忆集/app 目录下的 .log 文件追加，
 * 没有上限也没有轮转。长期运行会把共享存储写满 —— 实测已经因此装不上新包
 * （INSTALL_FAILED_INSUFFICIENT_STORAGE）。
 *
 * 现在统一走这里：
 *   - 单文件超过 MAX_BYTES 就轮转成 xxx.log.1，再超就 .2、.3……
 *   - 只保留 KEEP 份历史，更老的直接丢掉
 *   - 写失败一律静默（日志不能反过来把功能搞挂）
 */
object AppLog {

    private val DIR = File("/sdcard/思忆集/app")

    /** 单个日志文件的上限：2MB。够排查问题，又不会把存储写满。 */
    private const val MAX_BYTES = 2L * 1024 * 1024

    /** 保留几份历史（.1 最新，.3 最旧）。 */
    private const val KEEP = 3

    /** 追加一行。name 是文件名，例如 "boot.log"。 */
    @Synchronized
    fun write(name: String, msg: String) {
        runCatching {
            DIR.mkdirs()
            val f = File(DIR, name)
            if (f.exists() && f.length() > MAX_BYTES) rotate(name)
            f.appendText("[" + System.currentTimeMillis() + "] " + msg + "\n")
        }
    }

    /** 轮转：xxx.log → xxx.log.1，旧的 .1 变 .2，依此类推，超出 KEEP 的删掉。 */
    private fun rotate(name: String) {
        runCatching {
            // 最老的那份直接删
            File(DIR, name + "." + KEEP).delete()
            // 依次后移
            for (i in (KEEP - 1) downTo 1) {
                val from = File(DIR, name + "." + i)
                if (from.exists()) from.renameTo(File(DIR, name + "." + (i + 1)))
            }
            // 当前文件变成 .1
            File(DIR, name).renameTo(File(DIR, name + ".1"))
        }
    }

    /** 清掉所有日志（给「清理」功能用）。 */
    @Synchronized
    fun clearAll() {
        runCatching {
            DIR.listFiles()?.forEach { f ->
                if (f.name.endsWith(".log") || f.name.contains(".log.")) f.delete()
            }
        }
    }
}
