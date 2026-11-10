package com.siyi.dshapp

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var waitView: TextView
    private val handler = Handler(Looper.getMainLooper())
    private var navigated = false
    private var lastMsg = ""
    private val startAt = System.currentTimeMillis()

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && !Environment.isExternalStorageManager()) {
            runCatching {
                startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:$packageName")))
            }
        }

        waitView = TextView(this).apply {
            text = "正在启动引擎…"
            setTextColor(0xFF9BA7B4.toInt())
            textSize = 15f
            gravity = android.view.Gravity.CENTER
            setBackgroundColor(0xFF151517.toInt())
            setPadding(48, 48, 48, 48)
        }

        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.databaseEnabled = true
            settings.allowFileAccess = true
            settings.allowContentAccess = true
            settings.mediaPlaybackRequiresUserGesture = false
            settings.cacheMode = android.webkit.WebSettings.LOAD_DEFAULT
            // 登录页白屏的三处修复：
            // 1) UA：默认 WebView UA 会让前端走「内嵌浏览器」分支，而那个分支
            //    依赖官方 App 的 android-account 插件提供回调，我们没有，页面卡空白。
            //    去掉 wv 标记让前端走正常浏览器分支。
            // 2) 第三方 Cookie：OAuth 登录依赖跨域 cookie（codebuddy.cn → DSH 域）。
            // 3) 混合内容：引擎页面是 http，放行混合加载避免资源被静默丢弃。
            val defaultUa = android.webkit.WebSettings.getDefaultUserAgent(this@MainActivity)
            settings.userAgentString = defaultUa.replace(" wv)", ")")
            settings.javaScriptCanOpenWindowsAutomatically = true
            settings.setSupportMultipleWindows(false)
            settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    super.onPageFinished(view, url)
                    // 页面加载完再探测 React 是否真正渲染（#root 有子节点）
                    handler.postDelayed({ probeRendered() }, 800)
                    handler.postDelayed({ probeRendered() }, 2500)
                    handler.postDelayed({ probeRendered() }, 5000)
                }
                override fun onReceivedError(view: WebView?, req: WebResourceRequest?, err: WebResourceError?) {
                    super.onReceivedError(view, req, err)
                    if (req?.isForMainFrame == true) {
                        showWaiting("页面加载失败：${err?.description ?: "未知错误"}")
                    }
                }
            }
        }
        CookieManager.getInstance().setAcceptCookie(true)
        // web 已完成赋值，这里才能引用它：OAuth 登录依赖跨域 cookie
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)
        setContentView(web)

        addContentView(waitView, ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT))

        runCatching { startForegroundService(Intent(this, EngineService::class.java)) }
        poll()
    }

    /** 用 JS 检查 React 是否已挂载内容 */
    private fun probeRendered() {
        web.evaluateJavascript(
            "(function(){var r=document.getElementById('root');" +
            "return r && r.children.length > 0 ? 'yes' : 'no';})()"
        ) { value ->
            if (value != null && value.contains("yes")) {
                waitView.visibility = View.GONE
            }
        }
    }

    private fun poll() {
        handler.postDelayed(object : Runnable {
            override fun run() {
                val url = WebState.getUrl()
                val err = WebState.getError()
                when {
                    err != null -> showWaiting("引擎启动失败\n\n$err")
                    url != null && !navigated -> {
                        navigated = true
                        showWaiting("引擎已就绪，正在加载界面…")
                        web.loadUrl(url)
                    }
                    url == null -> {
                        val s = (System.currentTimeMillis() - startAt) / 1000
                        showWaiting("正在启动引擎…\n已等待 $s 秒（首次需解压 400MB 资源，可能数分钟）")
                        handler.postDelayed(this, 1500)
                    }
                }
            }
        }, 800)
    }

    private fun showWaiting(msg: String) {
        if (msg == lastMsg) return
        lastMsg = msg
        waitView.visibility = View.VISIBLE
        waitView.text = msg
    }

    override fun onResume() {
        super.onResume()
        val url = WebState.getUrl()
        if (url != null && !navigated) { navigated = true; web.loadUrl(url) }
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        runCatching { web.destroy() }
        super.onDestroy()
    }
}
