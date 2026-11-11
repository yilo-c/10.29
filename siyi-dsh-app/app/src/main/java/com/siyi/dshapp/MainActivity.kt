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
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

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
            // 诊断用：把 WebView 控制台与失败请求打到 logcat，
            // 登录白屏时靠这个定位前端卡在哪一步。
            webChromeClient = object : android.webkit.WebChromeClient() {
                override fun onConsoleMessage(m: android.webkit.ConsoleMessage?): Boolean {
                    if (m != null) {
                        android.util.Log.i("SiyiWeb", m.messageLevel().name + " " + m.message() +
                            " @" + m.sourceId() + ":" + m.lineNumber())
                    }
                    return true
                }
            }
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    super.onPageFinished(view, url)
                    android.util.Log.i("SiyiWeb", "onPageFinished " + url)
                    handler.postDelayed({ probeRendered() }, 800)
                    handler.postDelayed({ probeRendered() }, 2500)
                    handler.postDelayed({ probeRendered() }, 5000)
                }
                override fun onReceivedError(view: WebView?, req: WebResourceRequest?, err: WebResourceError?) {
                    super.onReceivedError(view, req, err)
                    android.util.Log.e("SiyiWeb", "onReceivedError " + req?.url + " -> " + err?.description)
                    if (req?.isForMainFrame == true) {
                        showWaiting("页面加载失败：${err?.description ?: "未知错误"}")
                    }
                }
                override fun onReceivedHttpError(view: WebView?, req: WebResourceRequest?, res: android.webkit.WebResourceResponse?) {
                    super.onReceivedHttpError(view, req, res)
                    android.util.Log.e("SiyiWeb", "HTTP " + res?.statusCode + " " + req?.url)
                }
            }
        }
        CookieManager.getInstance().setAcceptCookie(true)
        // web 已完成赋值，这里才能引用它：OAuth 登录依赖跨域 cookie
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)
        // WebView 默认 edge-to-edge，内容会画到状态栏与导航条下面，
        // 顶部按钮因此被遮挡。env(safe-area-inset-*) 在普通 Android WebView 里
        // 恒为 0（只有 display-cutout 全屏模式才有值），所以必须在原生侧补 padding。
        // 顶部避让。两条路径都要走，因为监听器在部分 ROM 上不触发
        // （实测 SiyiInsets 一条日志都没有，说明回调从未被调用）：
        //   1. onCreate 里直接读一次 rootWindowInsets，立即设 padding
        //   2. 监听器应对旋转 / 分屏 / 状态栏显隐变化
        fun applyBars(insets: WindowInsetsCompat?) {
            if (insets == null) return
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            // FIT_INSETS_CONTROLLED + LAYOUT_IN_SCREEN 下，系统把状态栏 inset
            // 派发成 0（实测 top=0 bottom=153），直接用会在状态栏下面画内容。
            // 改从系统资源读真实状态栏高度，硬加顶部 padding。
            val statusPx = try {
                val id = resources.getIdentifier("status_bar_height", "dimen", "android")
                if (id > 0) resources.getDimensionPixelSize(id) else 0
            } catch (t: Throwable) { 0 }
            val top = maxOf(bars.top, statusPx)
            android.util.Log.i("SiyiInsets", "barsTop=" + bars.top + " statusRes=" + statusPx +
                " top=" + top + " bottom=" + bars.bottom)
            web.setPadding(bars.left, top, bars.right, bars.bottom)
        }
        ViewCompat.setOnApplyWindowInsetsListener(web) { v, insets ->
            applyBars(insets)
            insets
        }
        setContentView(web)

        // 立即读一次（此时 web 已挂到窗口，rootWindowInsets 可用）
        web.post {
            runCatching {
                applyBars(ViewCompat.getRootWindowInsets(web))
            }.onFailure {
                android.util.Log.w("SiyiInsets", "getRootWindowInsets 失败: " + it.message)
            }
        }

        addContentView(waitView, ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT))

        // 挂载完成后再请求一次，双保险
        web.post { ViewCompat.requestApplyInsets(web) }

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
