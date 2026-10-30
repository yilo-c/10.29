package com.siyi.notepad.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

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

        /** 结构化节点上限：防止超长列表塞爆 WebView 与 AI 上下文。 */
        private const val MAX_NODES = 120

        @Volatile
        private var instance: SiyiAccessibilityService? = null

        fun isRunning(): Boolean = instance != null

        /** 返回当前屏幕的所有文本（树状采集）。未启用时返回 null。 */
        fun readCurrentScreen(): String? {
            val svc = instance ?: return null
            return svc.extractScreenText()
        }

        /**
         * 返回当前屏幕的结构化节点列表（文字 + 屏幕坐标 + viewId + 可点击性）。
         *
         * 为什么需要：纯文本 readCurrentScreen() 只给 AI 文字，AI 看不到按钮在哪，
         * 要点击只能瞎猜坐标。本方法把每个可操作节点的**中心点绝对坐标**一并返回，
         * AI 即可「看到按钮 → 直接点它」。
         *
         * 坐标说明：返回的 x/y 已是**屏幕绝对坐标**（含状态栏偏移），
         * 可直接传给 performTap()，无需再做换算。
         *
         * 返回 JSON 字符串；未启用无障碍时返回 null。
         */
        fun readScreenNodes(): String? {
            val svc = instance ?: return null
            return svc.extractScreenNodes()
        }

        /** 按可见文字/描述查找节点并点击其中心。成功返回 true。 */
        fun tapByText(text: String, exact: Boolean = false): Boolean {
            val svc = instance ?: return false
            return svc.tapNodeByText(text, exact)
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

    /**
     * 采集结构化节点，输出 JSON 数组字符串。
     *
     * 只收集「有意义」的节点，避免返回一整棵无用的容器树：
     *   - 有可见文字 / contentDescription，或
     *   - 自身可点击 / 可编辑 / 可勾选
     *
     * 每个节点包含屏幕绝对坐标（boundsInScreen）+ 中心点（cx, cy），
     * cx/cy 正是 performTap() 需要的入参，AI 拿到即可直接点。
     *
     * 无障碍节点必须 recycle()，否则长时间使用会泄漏 —— 这里用栈遍历，
     * 子节点入栈后由后续迭代负责回收，root 单独在调用方回收。
     */
    private fun extractScreenNodes(): String? {
        val root = rootInActiveWindow ?: return null
        val arr = JSONArray()
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.add(root)
        var emitted = 0
        try {
            while (stack.isNotEmpty() && emitted < MAX_NODES) {
                val node = stack.removeLast()
                val text = node.text?.toString()?.trim().orEmpty()
                val desc = node.contentDescription?.toString()?.trim().orEmpty()
                val label = text.ifEmpty { desc }
                val clickable = node.isClickable
                val editable = node.isEditable
                val checkable = node.isCheckable
                val scrollable = node.isScrollable
                val actionable = clickable || editable || checkable || scrollable

                // 只保留有文字的可操作节点，或带文字的普通节点（供 AI 理解界面）
                if (label.isNotEmpty() || actionable) {
                    val rect = Rect()
                    node.getBoundsInScreen(rect)
                    // 跳过零尺寸节点（不可见 / 未布局）
                    if (rect.width() > 0 && rect.height() > 0) {
                        val item = JSONObject()
                        item.put("text", label)
                        item.put("viewId", node.viewIdResourceName ?: "")
                        item.put("className", node.className?.toString() ?: "")
                        item.put("clickable", clickable)
                        if (editable) item.put("editable", true)
                        if (checkable) {
                            item.put("checkable", true)
                            item.put("checked", node.isChecked)
                        }
                        if (scrollable) item.put("scrollable", true)
                        item.put("left", rect.left)
                        item.put("top", rect.top)
                        item.put("right", rect.right)
                        item.put("bottom", rect.bottom)
                        // 中心点：直接作为 tap 入参
                        item.put("cx", rect.centerX())
                        item.put("cy", rect.centerY())
                        arr.put(item)
                        emitted++
                    }
                }

                for (i in 0 until node.childCount) {
                    val child = node.getChild(i)
                    if (child != null) stack.add(child)
                }
                if (node !== root) {
                    try { node.recycle() } catch (_: Exception) {}
                }
            }
        } catch (e: Exception) {
            android.util.Log.w("SiyiAccessibility", "extractScreenNodes failed: ${e.message}")
        } finally {
            try { root.recycle() } catch (_: Exception) {}
        }
        return arr.toString()
    }

    /**
     * 按文字/描述查找可点击节点并点击其中心点。
     *
     * 精确匹配失败时会退化为「包含匹配」，取第一个命中的可点击祖先或自身。
     * 找不到返回 false，由上层给出明确错误，不静默成功。
     */
    private fun tapNodeByText(target: String, exact: Boolean): Boolean {
        val root = rootInActiveWindow ?: return false
        val want = target.trim()
        if (want.isEmpty()) return false
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var hit: AccessibilityNodeInfo? = null
        try {
            while (queue.isNotEmpty() && hit == null) {
                val node = queue.removeFirst()
                val text = node.text?.toString()?.trim().orEmpty()
                val desc = node.contentDescription?.toString()?.trim().orEmpty()
                val matched = if (exact) (text == want || desc == want)
                              else (text.contains(want) || desc.contains(want))
                if (matched) {
                    // 自身可点击就用自身，否则向上找可点击的祖先
                    hit = findClickableSelfOrAncestor(node)
                }
                for (i in 0 until node.childCount) {
                    val child = node.getChild(i)
                    if (child != null) queue.add(child)
                }
            }
        } catch (e: Exception) {
            android.util.Log.w("SiyiAccessibility", "tapNodeByText failed: ${e.message}")
        } finally {
            try { root.recycle() } catch (_: Exception) {}
        }
        val target0 = hit ?: return false
        return try {
            val rect = Rect()
            target0.getBoundsInScreen(rect)
            if (rect.width() > 0 && rect.height() > 0) {
                dispatchTap(rect.centerX().toFloat(), rect.centerY().toFloat())
            } else false
        } finally {
            try { target0.recycle() } catch (_: Exception) {}
        }
    }

    /** 从 node 向上找第一个可点击节点（含自身）。 */
    private fun findClickableSelfOrAncestor(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var cur: AccessibilityNodeInfo? = node
        var depth = 0
        while (cur != null && depth < 6) {
            if (cur.isClickable) return cur
            cur = cur.parent
            depth++
        }
        // 没有可点击祖先时，退化为返回原节点（由上层直接点它中心）
        return node
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
