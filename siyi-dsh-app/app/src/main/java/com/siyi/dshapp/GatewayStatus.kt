package com.siyi.dshapp

import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

/**
 * 本机网关的渠道状态探测。
 *
 * ## 为什么需要
 *
 * 8427 网关是 App **外部**的独立进程，不在 APK 里。它没起、渠道欠费、
 * 账号掉登录，App 自己完全管不了 —— 用户只看到「模型报错」，
 * 不知道是网络问题、余额问题还是账号掉了。
 *
 * 成熟产品必须把这件事说清楚。所以这里做两件事：
 *   1. 网关在不在（TCP 探 8427）
 *   2. 网关在的话，逐个探候选模型，把「能用 / 欠费 / 未登录 / 被限流」
 *      分类出来，让 UI 能显示「当前可用模型」
 *
 * ## 为什么不在 App 里直接管渠道
 *
 * 渠道的账号、凭据、余额都在网关进程里（creds.json），App 碰不到也不该碰。
 * App 只做「观测」，不做「控制」。
 */
object GatewayStatus {

    const val GATEWAY_PORT = 8427
    const val MODEL_PORT = 3180

    /** 单个候选渠道的探测结果。 */
    data class Channel(
        val model: String,
        val ok: Boolean,
        /** 分类后的原因：ok / balance / auth / rate_limited / unavailable / offline */
        val reason: String,
        val detail: String = ""
    )

    /** 一次完整探测的结果。 */
    data class Report(
        val gatewayUp: Boolean,
        val channels: List<Channel>,
        val checkedAt: Long = System.currentTimeMillis()
    ) {
        /** 真正可用的模型列表。 */
        val usable: List<String> get() = channels.filter { it.ok }.map { it.model }
    }

    /**
     * 候选模型列表。
     *
     * 这些是本地网关实际提供的渠道里比较可靠的几个；
     * 完整的清单可以从 GET /v1/models 拿，这里只做快速健康检查。
     */
    private val CANDIDATES = listOf(
        "minimax/MiniMax-M3.1-Flash",
        "minimax/minimax-m3",
        "loomy/qwen3.8-flash",
        "lobsterai/deepseek-v4-flash",
        "buddy/deepseek-v4-flash",
        "buddy/hy4free",
        "codearts/deepseek-v4-flash",
        "qoder/auto",
        "workbuddy/default-model",
        "opencode/deepseek-v4.1-flash-free"
    )

    /** 探测一次全部渠道。放在后台线程调用。 */
    fun probe(): Report {
        if (!portOpen(GATEWAY_PORT)) {
            return Report(false, emptyList())
        }
        val key = readKey()
        val out = ArrayList<Channel>()
        for (m in CANDIDATES) {
            out.add(probeOne(m, key))
        }
        return Report(true, out)
    }

    /** 探一个模型，分类它的失败原因。 */
    private fun probeOne(model: String, key: String): Channel {
        val body = """
            {"model":"$model","messages":[{"role":"user","content":"ping"}],
             "max_tokens":16,"stream":false}
        """.trimIndent()
        return try {
            val conn = URL("http://127.0.0.1:$GATEWAY_PORT/v1/chat/completions").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.connectTimeout = 12000
            conn.readTimeout = 20000
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            if (key.isNotEmpty()) conn.setRequestProperty("Authorization", "Bearer $key")
            conn.outputStream.use { it.write(body.toByteArray()) }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use { it.readText() } ?: ""
            conn.disconnect()
            if (code in 200..299) {
                // 200 但内容为空也算不可用 —— 有些渠道把 token 全烧在思考上
                val content = try {
                    JSONObject(text).optJSONArray("choices")?.let { arr ->
                            if (arr.length() == 0) null
                            else arr.optJSONObject(0)?.optJSONObject("message")
                                ?.optString("content").orEmpty().trim()
                        } ?: ""
                } catch (_: Throwable) { "" }
                if (content.isEmpty()) {
                    Channel(model, false, "empty", "返回 200 但内容为空")
                } else {
                    Channel(model, true, "ok")
                }
            } else {
                classify(code, text, model)
            }
        } catch (t: Throwable) {
            Channel(model, false, "unavailable", t.javaClass.simpleName + ": " + (t.message ?: ""))
        }
    }

    /** 按 HTTP 状态与错误文本分类失败原因。 */
    private fun classify(code: Int, text: String, model: String): Channel {
        val t = text.lowercase()
        return when {
            code == 402 || "insufficient_balance" in t || "balance" in t ->
                Channel(model, false, "balance", "余额不足")
            code == 401 || code == 403 || "credential" in t || "log in" in t ->
                Channel(model, false, "auth", "未登录或凭据失效")
            code == 429 || "rate" in t || "限流" in t ->
                Channel(model, false, "rate_limited", "被限流")
            code >= 500 ->
                Channel(model, false, "unavailable", "上游错误 HTTP $code")
            else ->
                Channel(model, false, "error", "HTTP $code")
        }
    }

    /** 网关密钥：与 EngineController 同源。 */
    private fun readKey(): String = runCatching {
        val f = java.io.File("/sdcard/思忆集/app/gateway.key")
        if (!f.exists()) return ""
        f.readText().lineSequence().firstOrNull()?.trim() ?: ""
    }.getOrDefault("")

    /** 端口是否在监听。 */
    fun portOpen(port: Int): Boolean = runCatching {
        java.net.Socket().use { s ->
            s.connect(java.net.InetSocketAddress("127.0.0.1", port), 1200)
            true
        }
    }.getOrDefault(false)
}
