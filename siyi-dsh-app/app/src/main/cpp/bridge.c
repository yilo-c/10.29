// 思忆集设备桥的原生辅助：用标准 socketpair(2) 建一对已连接的 UNIX socket。
//
// 为什么必须走 JNI：
//   1. Os.socketpair 是 @hide API，各 ROM 签名不一致（本机是 5 参数 void 形式），
//      反射匹配既脆弱又受非 SDK 接口限制；
//   2. 用 LocalServerSocket + LocalSocket 虽然绕开隐藏 API，但它产生的是
//      「监听-接受」型 socket，libuv 的 uv_guess_handle 会判成 UV_UNKNOWN_HANDLE，
//      引擎侧 net.Socket({fd}) 直接报 "Unsupported fd type: UNKNOWN"。
// socketpair(2) 产生的是对等匿名 socket，libuv 能正确识别为 UV_NAMED_PIPE，
// 这正是官方 DSH 走的路径。
#include <jni.h>
#include <sys/socket.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>

#define BRIDGE_AF_UNIX      1   // AF_UNIX
#define BRIDGE_SOCK_STREAM  1   // SOCK_STREAM

/**
 * 建一对 socketpair。
 * @return 长度为 2 的 int 数组（两个 fd），失败返回 null。
 *         调用方负责在 Java 侧用 FileDescriptor 包装。
 */
JNIEXPORT jintArray JNICALL
Java_com_siyi_dshapp_NativeBridge_socketPair(JNIEnv *env, jclass clazz) {
    int fds[2] = { -1, -1 };
    if (socketpair(BRIDGE_AF_UNIX, BRIDGE_SOCK_STREAM, 0, fds) != 0) {
        return NULL;
    }
    jintArray result = (*env)->NewIntArray(env, 2);
    if (result == NULL) {
        close(fds[0]);
        close(fds[1]);
        return NULL;
    }
    jint vals[2] = { fds[0], fds[1] };
    (*env)->SetIntArrayRegion(env, result, 0, 2, vals);
    return result;
}

/**
 * 清除 fd 的 FD_CLOEXEC 位，让它在 exec 后仍被子进程继承。
 * @return 0 成功，-1 失败。
 */
JNIEXPORT jint JNICALL
Java_com_siyi_dshapp_NativeBridge_clearCloexec(JNIEnv *env, jclass clazz, jint fd) {
    int flags = fcntl(fd, F_GETFD);
    if (flags < 0) return -1;
    if (fcntl(fd, F_SETFD, flags & ~FD_CLOEXEC) < 0) return -1;
    return 0;
}

/** 关闭 fd。 */
JNIEXPORT jint JNICALL
Java_com_siyi_dshapp_NativeBridge_closeFd(JNIEnv *env, jclass clazz, jint fd) {
    return close(fd) == 0 ? 0 : -1;
}
