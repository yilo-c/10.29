package com.siyi.dshapp

import java.io.FileDescriptor

/**
 * 设备桥的原生辅助（JNI）。
 *
 * 为什么需要它：见 cpp/bridge.c 顶部注释。
 * 一句话：只有 libc 的 socketpair(2) 能同时满足
 *   ① 不碰 @hide API（Os.socketpair 签名各 ROM 不一）
 *   ② 产生 libuv 能识别的对等 socket（LocalSocket 会被判成 UNKNOWN）
 */
object NativeBridge {

    /** so 是否加载成功；失败时所有方法返回失败值，不抛异常。 */
    val available: Boolean by lazy {
        runCatching {
            System.loadLibrary("siyi_bridge")
            true
        }.getOrElse { false }
    }

    private external fun socketPair(): IntArray?
    private external fun clearCloexec(fd: Int): Int
    private external fun closeFd(fd: Int): Int

    /**
     * 建一对已连接的 UNIX socket。
     * @return 两个 FileDescriptor（[0] 给 App，[1] 给引擎），失败返回 null。
     */
    fun createSocketPair(): Array<FileDescriptor>? {
        if (!available) return null
        val fds = runCatching { socketPair() }.getOrNull() ?: return null
        if (fds.size != 2 || fds[0] < 0 || fds[1] < 0) return null
        // 用反射把 int fd 包成 FileDescriptor
        val a = fdOf(fds[0]) ?: return null
        val b = fdOf(fds[1]) ?: return null
        return arrayOf(a, b)
    }

    /** 清除 CLOEXEC，使 fd 在 exec 后被子进程继承。 */
    fun clearCloexec(fd: FileDescriptor): Boolean {
        if (!available) return false
        val n = fdNumber(fd)
        if (n < 0) return false
        return runCatching { clearCloexec(n) == 0 }.getOrDefault(false)
    }

    /** 从 FileDescriptor 取 int fd。 */
    fun fdNumber(fd: FileDescriptor): Int {
        runCatching {
            val f = FileDescriptor::class.java.getDeclaredField("descriptor")
            f.isAccessible = true
            return f.getInt(fd)
        }
        runCatching {
            val f = FileDescriptor::class.java.getDeclaredField("fd")
            f.isAccessible = true
            return f.getInt(fd)
        }
        return -1
    }

    /** 用 int fd 构造 FileDescriptor。 */
    private fun fdOf(n: Int): FileDescriptor? {
        return runCatching {
            val fd = FileDescriptor()
            runCatching {
                val f = FileDescriptor::class.java.getDeclaredField("descriptor")
                f.isAccessible = true
                f.setInt(fd, n)
                return@runCatching true
            }.getOrElse {
                val f = FileDescriptor::class.java.getDeclaredField("fd")
                f.isAccessible = true
                f.setInt(fd, n)
            }
            fd
        }.getOrNull()
    }
}
