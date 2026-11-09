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

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && !Environment.isExternalStorageManager()) {
            runCatching {
                startActivity(Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                    Uri.parse("package:$packageName")))
            }
        }

        // 等待页用原生 TextView，避免反复重载 WebView 造成白屏闪烁
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
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    super.onPageFinished(view, url)
                    // 页面能正常渲染就隐藏等待层
                    if (url != null && url.contains("token=")) {
                        handler.postDelayed({ waitView.visibility = View.GONE }, 1200)
                    }
                }
                override fun onReceivedError(view: WebView?, req: WebResourceRequest?, err: WebResourceError?) {
                    super.onReceivedError(view, req, err)
                    // 主文档失败才提示
                    if (req?.isForMainFrame == true) {
                        navigated = false
                        showWaiting("连接引擎失败，正在重试…\n${err?.description ?: ""}")
                    }
                }
            }
        }
        CookieManager.getInstance().setAcceptCookie(true)
        setContentView(web)

        // 把等待层盖在 WebView 上面
        addContentView(waitView, android.view.ViewGroup.LayoutParams(
            android.view.ViewGroup.LayoutParams.MATCH_PARENT,
            android.view.ViewGroup.LayoutParams.MATCH_PARENT))

        runCatching { startForegroundService(Intent(this, EngineService::class.java)) }
        poll()
    }

    private fun poll() {
        handler.postDelayed(object : Runnable {
            override fun run() {
                val url = WebState.getUrl()
                val err = WebState.getError()
                when {
                    url != null && !navigated -> {
                        navigated = true
                        showWaiting("引擎已就绪，正在加载界面…")
                        web.loadUrl(url)
                    }
                    err != null -> showWaiting("引擎启动失败\n\n$err")
                    else -> {
                        val elapsed = (System.currentTimeMillis() - startAt) / 1000
                        showWaiting("正在启动引擎…\n已等待 ${elapsed} 秒（首次启动需解压约 150MB，可能 1-3 分钟）")
                        handler.postDelayed(this, 1500)
                    }
                }
            }
        }, 800)
    }

    private val startAt = System.currentTimeMillis()

    /** 只有文本变化才更新，避免无谓重绘 */
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
