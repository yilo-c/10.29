package com.siyi.notepad.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.os.Build
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 无障碍服务 — 屏幕内容读取 + 模拟手势。
 *
 * 移植自旧项目 com.siyi.app.accessibility.SiyiAccessibilityService
 * 改包名 com.siyi.app → com.siyi.notepad
 *
 * 用户首次使用前需手动在系统「设置 → 无障碍」启用本服务。
 *
 * 暴露的能力（通过 SiyiNativePlugin 给 JS 层）：
 * - readCurrentScreen() : String  — 拍当前屏文字快照
 * - performTap(x, y) : Boolean    — 模拟点击坐标
 * - performSwipe(x1, y1, x2, y2, duration) : Boolean — 模拟滑动
 * - performBack() / performHome() / performRecents() : Boolean — 系统导航
 */
class SiyiAccessibilityService : AccessibilityService() {

    companion object {
        const val ACTION_READ_SCREEN = "com.siyi.notepad.action.READ_SCREEN"
        const val ACTION_TAP = "com.siyi.notepad.action.TAP"
        const val EXTRA_SCREEN_TEXT = "screen_text"
        const val EXTRA_TAP_X = "tap_x"
        const val EXTRA_TAP_Y = "tap_y"

        @Volatile
        private var instance: SiyiAccessibilityService? = null

        fun isRunning(): Boolean = instance != null

        /** 返回当前屏幕的所有文本（树状采集）。未启用时返回 null。 */
        fun readCurrentScreen(): String? {
            val svc = instance ?: return null
            return svc.extractScreenText()
        }

        /** 在 (x, y) 屏幕坐标模拟一次轻点。需要 Android 7.0+。 */
        fun performTap(x: Float, y: Float): Boolean {
            val svc = instance ?: return false
            return svc.dispatchTap(x, y)
        }

        /** 模拟从 (x1,y1) 到 (x2,y2) 的滑动，duration 单位毫秒。 */
        fun performSwipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long = 300L): Boolean {
            val svc = instance ?: return false
            return svc.dispatchSwipe(x1, y1, x2, y2, durationMs)
        }

        fun performBack(): Boolean = instance?.performGlobalAction(GLOBAL_ACTION_BACK) ?: false
        fun performHome(): Boolean = instance?.performGlobalAction(GLOBAL_ACTION_HOME) ?: false
        fun performRecents(): Boolean = instance?.performGlobalAction(GLOBAL_ACTION_RECENTS) ?: false
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // On-demand 读取，不做持续监听
    }

    override fun onInterrupt() {}

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // 安全：仅接受本应用发出的 intent，拒绝第三方应用通过 exported
        // service 注入 READ_SCREEN/TAP 指令（AndroidManifest 已设
        // exported=false，此处为纵深防御）。
        val intentPkg = intent?.`package` ?: intent?.component?.packageName
        if (intent != null && intentPkg != null && intentPkg != packageName) {
            android.util.Log.w("SiyiAccessibility", "Rejected cross-package intent from $intentPkg")
            return START_NOT_STICKY
        }
        when (intent?.action) {
            ACTION_READ_SCREEN -> {
                val text = extractScreenText()
                if (text != null) {
                    val broadcast = Intent(ACTION_READ_SCREEN)
                        .putExtra(EXTRA_SCREEN_TEXT, text)
                        .setPackage(packageName)
                    sendBroadcast(broadcast)
                }
            }
            ACTION_TAP -> {
                val x = intent.getFloatExtra(EXTRA_TAP_X, -1f)
                val y = intent.getFloatExtra(EXTRA_TAP_Y, -1f)
                if (x >= 0 && y >= 0) dispatchTap(x, y)
            }
        }
        return super.onStartCommand(intent, flags, startId)
    }

    private fun extractScreenText(): String? {
        val root = rootInActiveWindow ?: return null
        val text = collectText(root)
        try { root.recycle() } catch (_: Exception) {}
        return text.takeIf { it.isNotBlank() }
    }

    private fun collectText(root: AccessibilityNodeInfo?): String {
        if (root == null) return ""
        val sb = StringBuilder()
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.add(root)
        while (stack.isNotEmpty()) {
            val node = stack.removeLast()
            if (node.text?.isNotEmpty() == true) sb.appendLine(node.text)
            if (node.contentDescription?.isNotEmpty() == true) sb.appendLine(node.contentDescription)
            for (i in 0 until node.childCount) {
                val child = node.getChild(i)
                if (child != null) stack.add(child)
            }
            if (node !== root) {
                try { node.recycle() } catch (_: Exception) {}
            }
        }
        return sb.toString()
    }

    private fun dispatchTap(x: Float, y: Float): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false
        val path = Path().apply { moveTo(x, y) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 100))
            .build()
        return dispatchGesture(gesture, null, null)
    }

    private fun dispatchSwipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false
        val path = Path().apply {
            moveTo(x1, y1)
            lineTo(x2, y2)
        }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs))
            .build()
        return dispatchGesture(gesture, null, null)
    }
}
