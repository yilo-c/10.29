package com.siyi.dshapp

import android.annotation.SuppressLint
import android.app.Activity
import android.content.ClipData
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebChromeClient.FileChooserParams
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.result.registerForActivityResult
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
    /** 白屏自愈计数：主框架加载失败后自动重试，成功后归零。 */
    private var retries = 0
    /** 上一次下发加载的时刻，用来判断「已经过去太久还是空的」。 */
    private var loadAt = 0L
    private val startAt = System.currentTimeMillis()

    // 文件选择 / 交互回调
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    /** GetContent 在 launch(mime) 时才决定类型，一个 launcher 覆盖图片与文件。 */
    private val pickLauncher = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        deliverUri("pick", uri)
    }
    private val takePhotoLauncher = registerForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        deliverUri("take_photo", if (ok) photoUri else null)
    }
    /** WebView 的 <input type=file> 回调：用新 ActivityResult API 接，避免与旧 requestCode 混用。 */
    private val fileChooserLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        val uris: Array<Uri>? = if (r.resultCode == Activity.RESULT_OK) {
            val clip = r.data?.clipData
            if (clip != null) Array(clip.itemCount) { clip.getItemAt(it).uri }
            else r.data?.data?.let { arrayOf(it) }
        } else null
        filePathCallback?.onReceiveValue(uris)
        filePathCallback = null
    }
    private val voiceLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        val list = r.data?.getStringArrayListExtra(android.speech.RecognizerIntent.EXTRA_RESULTS)
        val text = list?.firstOrNull() ?: ""
        InteractionHub.onResult(org.json.JSONObject().put("ok", text.isNotEmpty())
            .put("text", text).toString())
    }
    private var photoUri: Uri? = null
    private var textForWeb: String? = null

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
                // WebView 不自带文件选择器：宿主不实现这个方法，
                // <input type="file"> 就是静默无反应。
                override fun onShowFileChooser(
                    view: WebView,
                    cb: ValueCallback<Array<Uri>>,
                    params: FileChooserParams
                ): Boolean {
                    filePathCallback?.onReceiveValue(null)
                    filePathCallback = cb
                    return try {
                        val i = params.createIntent()
                        i.type = "*/*"
                        i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.mode == FileChooserParams.MODE_OPEN_MULTIPLE)
                        fileChooserLauncher.launch(Intent.createChooser(i, "选择文件"))
                        true
                    } catch (t: Throwable) {
                        filePathCallback = null
                        false
                    }
                }
            }
            // 下载链接：交给系统 DownloadManager
            setDownloadListener { url, _, _, _, _ ->
                runCatching {
                    val req = android.app.DownloadManager.Request(Uri.parse(url))
                    req.setNotificationVisibility(android.app.DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    val dm = getSystemService(android.content.Context.DOWNLOAD_SERVICE) as android.app.DownloadManager
                    dm.enqueue(req)
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
                        val why = "页面加载失败：${err?.description ?: "未知错误"}"
                        if (retries < 5 && EngineController.state().url != null) {
                            retries++
                            showWaiting("$why\n第 $retries 次重试…")
                            handler.postDelayed({ retryLoad(why) }, 1500L * retries)
                        } else {
                            showWaiting(why)
                        }
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

        setupInteraction()
        handleShare(intent)
        runCatching { startForegroundService(Intent(this, EngineService::class.java)) }
        poll()
    }

    /** 重新拉一次页面。引擎可能还在解冻，失败会有下一轮 probeRendered 接着重试。 */
    private fun retryLoad(why: String) {
        val url = EngineController.state().url ?: return
        if (EngineController.state().error != null) return
        showWaiting(why)
        loadUrl(url)
        handler.postDelayed({ probeRendered() }, 2000)
        handler.postDelayed({ probeRendered() }, 7000)
    }

    /** 真正下发加载，同时记下时刻并挂两个看门狗：请求卡死时也能自己救回来。 */
    private fun loadUrl(url: String) {
        loadAt = System.currentTimeMillis()
        web.loadUrl(url)
        handler.postDelayed({ probeRendered() }, 6000)
        handler.postDelayed({ probeRendered() }, 13000)
    }

    /** 用 JS 检查 React 是否已挂载内容；一直没有内容就当作白屏重挂。 */
    private fun probeRendered() {
        web.evaluateJavascript(
            "(function(){var r=document.getElementById('root');" +
            "return r && r.children.length > 0 ? 'yes' : 'no';})()"
        ) { value ->
            if (value != null && value.contains("yes")) {
                waitView.visibility = View.GONE
                lastMsg = ""
                retries = 0
            } else if (retries < 5 && System.currentTimeMillis() - loadAt > 5000) {
                // 页面 DOM 一直是空的 = 白屏。多半是引擎刚解冻/刚就绪还没响应，
                // 隔一会儿重挂一次，别把用户丢在空白页上。
                retries++
                retryLoad("界面空白，第 $retries 次重新加载…")
            }
        }
    }

    /** InteractionHub 的拉起动作：主线程执行，按 kind 发对应 Intent。 */
    private fun setupInteraction() {
        InteractionHub.launcher = { p ->
            when (p.kind) {
                "pick" -> {
                    val mime = p.arg.ifEmpty { "*/*" }
                    pickLauncher.launch(mime)
                }
                "take_photo" -> {
                    // FileProvider 要求目标文件先存在（哪怕 0 字节），相机才有地方写。
                    val dir = java.io.File(cacheDir, "photos").also { it.mkdirs() }
                    val f = java.io.File(dir, "photo-" + System.currentTimeMillis() + ".jpg")
                    f.createNewFile()
                    val uri = androidx.core.content.FileProvider.getUriForFile(
                        this, packageName + ".fileprovider", f)
                    photoUri = uri
                    // 相机 App 以别的 uid 运行，必须显式授权写这个 content://
                    runCatching {
                        grantUriPermission("com.android.camera", uri,
                            Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        grantUriPermission("com.google.android.GoogleCamera", uri,
                            Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    }
                    takePhotoLauncher.launch(uri)
                }
                "voice" -> {
                    val i = Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                        .putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                            android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    runCatching { voiceLauncher.launch(i) }
                        .onFailure { InteractionHub.cancel() }
                }
                else -> InteractionHub.cancel()
            }
        }
    }

    /** 把 content:// URI 拷成 App 私有文件，结果回传给交互等待方。 */
    private fun deliverUri(kind: String, uri: Uri?) {
        val out = org.json.JSONObject()
        if (uri == null) {
            out.put("ok", false).put("error", "用户取消")
        } else {
            runCatching {
                val dir = java.io.File(cacheDir, "picked").also { it.mkdirs() }
                val name = "picked-" + System.currentTimeMillis()
                val f = java.io.File(dir, name)
                contentResolver.openInputStream(uri)?.use { input ->
                    f.outputStream().use { out2 -> input.copyTo(out2) }
                }
                out.put("ok", true).put("path", f.absolutePath).put("bytes", f.length())
                    .put("uri", uri.toString())
            }.onFailure { out.put("ok", false).put("error", it.message) }
        }
        InteractionHub.onResult(out.toString())
    }


    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    /** 接收别的 App 分享进来的文本/图片，写入剪贴板并记入日志，供 AI 读取。 */
    private fun handleShare(i: Intent?) {
        if (i?.action != Intent.ACTION_SEND) return
        val text = i.getStringExtra(Intent.EXTRA_TEXT)
        val stream = i.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)
        val sb = StringBuilder()
        text?.let { sb.append(it) }
        stream?.let { sb.append("[共享文件] ").append(it.toString()) }
        if (sb.isNotEmpty()) {
            val value = sb.toString()
            val cm = getSystemService(android.content.Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("siyi_share", value))
            android.util.Log.i("SiyiShare", value.take(200))
            textForWeb = value
        }
    }

    @Suppress("DEPRECATION")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    private fun poll() {
        handler.postDelayed(object : Runnable {
            override fun run() {
                val st = EngineController.state()
                when {
                    // 地址优先：只要引擎打出了带 token 的地址，就是能用的。
                    // 曾经的错误（比如被淘汰的那次启动）不能挡住可用的引擎。
                    st.url != null && !navigated -> {
                        navigated = true
                        showWaiting("引擎已就绪，正在加载界面…")
                        loadUrl(st.url)
                    }
                    // 出错也要继续轮询：引擎可能稍后起来，或者用户手动重启一下就好。
                    st.phase == EngineController.Phase.FAILED -> {
                        showWaiting("引擎启动失败，正在自动重试…\n\n" + (st.error ?: ""))
                        handler.postDelayed(this, 3000)
                    }
                    else -> {
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
        val url = EngineController.state().url
        if (url != null && !navigated) {
            navigated = true
            loadUrl(url)
        } else if (url != null) {
            // 从后台回来：引擎可能刚被解冻，页面还是空的。重置计数后重新体检一次，
            // 6 秒后仍没有内容就自动重挂（probeRendered 自己会处理）。
            retries = 0
            loadAt = System.currentTimeMillis()
            handler.postDelayed({ probeRendered() }, 2000)
            handler.postDelayed({ probeRendered() }, 6500)
        }
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        runCatching { web.destroy() }
        super.onDestroy()
    }
}
