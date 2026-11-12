package com.siyi.dshapp

import org.json.JSONObject
import java.util.concurrent.atomic.AtomicReference

/**
 * 交互能力的桥接中枢。
 *
 * DeviceBridge 在后台线程同步等待用户完成系统交互（选图/拍照/语音），
 * 而 ActivityResult 回调发生在主线程。这个对象用 AtomicReference 把两者接起来：
 *   request()   → 存 pending，通知 MainActivity 拉起对应 Intent
 *   onResult()  → 主线程回调，触发 pending 的 callback
 *   deliverUri()-> MainActivity 把 content:// 拷成 App 私有文件，回传路径
 */
object InteractionHub {

    data class Pending(
        val kind: String,            // pick / take_photo / voice
        val arg: String,             // mime 等
        val cb: (String) -> Unit
    )

    private val pending = AtomicReference<Pending?>(null)
    /** MainActivity 注册：当有新请求时要执行的拉起动作 */
    @Volatile var launcher: ((Pending) -> Unit)? = null

    fun request(kind: String, arg: String, cb: (String) -> Unit): Boolean {
        val p = Pending(kind, arg, cb)
        if (!pending.compareAndSet(null, p)) return false // 已有交互在进行
        val l = launcher
        if (l == null) {
            pending.set(null)
            cb(JSONObject().put("ok", false).put("error", "交互未就绪：Activity 未注册 launcher").toString(2))
            return false
        }
        // 拉起 Intent 必须在主线程
        android.os.Handler(android.os.Looper.getMainLooper()).post { l(p) }
        return true
    }

    /** 主线程：系统选择器返回结果。text 可直接作为工具结果 JSON。 */
    fun onResult(text: String) {
        val p = pending.getAndSet(null) ?: return
        runCatching { p.cb(text) }
    }

    fun cancel() {
        val p = pending.getAndSet(null) ?: return
        runCatching { p.cb(JSONObject().put("ok", false).put("error", "交互取消").toString(2)) }
    }

    fun current(): Pending? = pending.get()
}
