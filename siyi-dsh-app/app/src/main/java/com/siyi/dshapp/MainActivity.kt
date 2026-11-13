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


    private companion object {
        private const val KEY_LOADED_URL = "siyi.loadedUrl"
        private const val KEY_NAVIGATED = "siyi.navigated"
    }

    private lateinit var web: WebView
    private lateinit var waitView: TextView
    /** 是否启用本地骨架屏（assets/skeleton.html 存在时为 true）。 */
    private var skeletonReady = false
    private val handler = Handler(Looper.getMainLooper())
    private var navigated = false
    private var lastMsg = ""
    /** 白屏自愈计数：主框架加载失败后自动重试，成功后归零。 */
    private var retries = 0
    /** 上一次下发加载的时刻，用来判断「已经过去太久还是空的」。 */
    private var loadAt = 0L
    /** 当前 WebView 里加载的是哪个地址。引擎重启后 token 会变，靠它识别。 */
    private var loadedUrl: String? = null
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

    /**
     * 运行时权限申请器。
     *
     * 之前 App 从没申请过任何运行时权限（实测五个全是 granted=false），
     * 于是 device_notify / device_location / device_take_photo /
     * device_voice_input 这些工具会静默失败 —— 用户以为功能坏了，
     * 其实是没授权。
     */
    private val permLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { granted ->
        val denied = granted.filterValues { !it }.keys
        if (denied.isNotEmpty()) {
            // 不弹第二次系统框打扰用户，但把结果记下来，
            // 工具调用时能明确回报「缺权限」而不是「未知错误」。
            android.util.Log.i("SiyiWeb", "权限被拒: " + denied.joinToString(","))
        }
        android.util.Log.i("SiyiWeb", "权限结果: " + granted.toString())
    }

    /**
     * 首次启动时申请运行时权限。
     *
     * 策略：一次性把四项都提上去，由系统弹窗逐项询问，用户可以只勾一部分。
     * 不做「用到才问」是因为这些能力都由 AI 主动触发，
     * 等到用到时再问会显得突兀，而且很多用户会直接拒绝。
     */
    private fun requestRuntimePermissions() {
        val need = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            need.add(android.Manifest.permission.POST_NOTIFICATIONS)
        }
        need.add(android.Manifest.permission.ACCESS_FINE_LOCATION)
        need.add(android.Manifest.permission.ACCESS_COARSE_LOCATION)
        need.add(android.Manifest.permission.CAMERA)
        need.add(android.Manifest.permission.RECORD_AUDIO)
        val missing = need.filter {
            checkSelfPermission(it) != android.content.pm.PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) return
        android.util.Log.i("SiyiWeb", "申请运行时权限: " + missing.joinToString(","))
        runCatching { permLauncher.launch(missing.toTypedArray()) }
    }

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
        // 骨架屏：加载真实界面前先盖一层本地静态占位（与 DSH 主界面同色系），
        // 视觉上无白屏、无读条，READY 后再 loadUrl 换真页。
        // 仅当 assets 带 skeleton.html 时启用，缺文件退化回纯色 waitView。
        skeletonReady = runCatching {
            assets.open("skeleton.html").bufferedReader().use { it.readText() }
        }.getOrNull() != null

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
            // ⚠ 必须开启多窗口。登录按钮走的是 window.open(authorizeUrl)：
            // 设成 false 时 WebView 会把弹窗「就地导航」，结果停在 about:blank，
            // 整个界面变成白屏（logcat 里能看到 onPageFinished about:blank）。
            // 开启后由下面的 onCreateWindow 接住，交给系统浏览器打开。
            settings.setSupportMultipleWindows(true)
            settings.mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            // 诊断用：把 WebView 控制台与失败请求打到 logcat，
            // 登录白屏时靠这个定位前端卡在哪一步。
            webChromeClient = object : android.webkit.WebChromeClient() {
                /**
                 * 接住 window.open 的弹窗请求。
                 *
                 * 登录、第三方授权都是新窗口打开的。WebView 自己不该承载这些页面
                 * （它们不在我们的源里，也不该拿到 localStorage），
                 * 正确做法是丢给系统浏览器（Chrome），授权完它自己会回到
                 * http://127.0.0.1:3180/oauth/callback。
                 */
                override fun onCreateWindow(
                    view: WebView?,
                    isDialog: Boolean,
                    isUserGesture: Boolean,
                    resultMsg: android.os.Message?
                ): Boolean {
                    val transport = resultMsg?.obj as? WebView.WebViewTransport ?: return false
                    // 用一个临时 WebView 只为拿到要打开的 URL
                    val probe = WebView(this@MainActivity)
                    probe.webViewClient = object : WebViewClient() {
                        override fun shouldOverrideUrlLoading(
                            v: WebView?, req: android.webkit.WebResourceRequest?
                        ): Boolean {
                            val url = req?.url?.toString()
                            if (!url.isNullOrEmpty()) openExternally(url)
                            v?.stopLoading()
                            probe.destroy()
                            return true
                        }
                    }
                    transport.webView = probe
                    resultMsg.sendToTarget()
                    return true
                }

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
                    // 布局补丁是「一次性执行」的代码：它在页面加载时把
                    // data-windows-titlebar 加到 <html> 上，DSH 据此把左侧图标栏
                    // 宽度算成 0。
                    //
                    // ⚠ 引擎常驻后页面不会重新加载（URL 没变），如果首屏那次
                    //   补丁没跑成，之后永远不会重试 —— 表现就是侧边栏又冒出来。
                    //
                    // 所以这里在每次页面加载完成后，主动确认一次属性在不在；
                    // 不在就补上（等价于重跑补丁的 ensure）。
                    handler.postDelayed({ ensureLayoutPatch() }, 1200)
                    handler.postDelayed({ ensureLayoutPatch() }, 4000)
                    // 诊断：确认补丁状态
                    for (d in longArrayOf(1500L, 8000L)) {
                        handler.postDelayed({ dumpLayoutState() }, d)
                    }
                }
                /**
                 * 非引擎页面的导航一律交给系统浏览器。
                 *
                 * 这样 WebView 永远停在引擎页面上：外部授权页、外链都不会
                 * 把界面导航走，也就不会出现「跳出去回不来」的白屏。
                 */
                override fun shouldOverrideUrlLoading(
                    view: WebView?, req: android.webkit.WebResourceRequest?
                ): Boolean {
                    val url = req?.url?.toString() ?: return false
                    if (isEngineUrl(url)) return false      // 引擎页面留在 WebView
                    openExternally(url)
                    return true
                }

                override fun onReceivedError(view: WebView?, req: WebResourceRequest?, err: WebResourceError?) {
                    super.onReceivedError(view, req, err)
                    android.util.Log.e("SiyiWeb", "onReceivedError " + req?.url + " -> " + err?.description)
                    // 外部页面（登录授权页等）的加载失败不归我们管，
                    // 否则会把用户从授权流程里拽回来。
                    if (req?.isForMainFrame == true && isEnginePage()) {
                        val why = "页面加载失败：${err?.description ?: "未知错误"}"
                        if (retries < 5 && EngineController.state().url != null) {
                            retries++
                            showError("$why\n第 $retries 次重试…")
                            handler.postDelayed({ retryLoad(why) }, 1500L * retries)
                        } else {
                            showError(why)
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
        // 骨架屏就绪时：先铺本地骨架页（web 此刻已初始化），并隐藏纯色文字层。
        if (skeletonReady) {
            web.loadUrl("file:///android_asset/skeleton.html")
            waitView.visibility = View.GONE
        }

        // 挂载完成后再请求一次，双保险
        web.post { ViewCompat.requestApplyInsets(web) }

        setupInteraction()
        handleShare(intent)
        // 权限要在界面已经起来之后再申请，否则第一次弹窗会盖在空白页上。
        handler.postDelayed({ requestRuntimePermissions() }, 1200)
        runCatching { startForegroundService(Intent(this, EngineService::class.java)) }
        // 首次启动时引导用户加白名单，否则后台一冻结就白屏
        handler.postDelayed({ ensureBackgroundSurvival() }, 2500)
        poll()
    }

    /**
     * 前后台切换后的最终兜底：页面确实没有内容就整页重载一次。
     *
     * 后台期间引擎可能被冻结（MIUI 会挂起整个进程），回来时 WebView
     * 可能还停在旧帧、或者内容已经被系统回收。前面几次体检只是「问一问」，
     * 这里才真正动手重载。
     */
    private fun forceReloadIfBlank() {
        if (!isEnginePage()) return
        web.evaluateJavascript(
            "(function(){var r=document.getElementById('root');" +
            "return r && r.children.length > 0 ? 'yes' : 'no';})()"
        ) { value ->
            if (value != null && value.contains("yes")) return@evaluateJavascript
            val url = EngineController.state().url ?: return@evaluateJavascript
            android.util.Log.i("SiyiWeb", "后台回来页面为空，强制重载")
            retries = 0
            loadUrl(url)
            handler.postDelayed({ probeRendered() }, 2500)
        }
    }

    /** 重新拉一次页面。引擎可能还在解冻，失败会有下一轮 probeRendered 接着重试。 */
    private fun retryLoad(why: String) {
        if (!isEnginePage()) return
        val url = EngineController.state().url ?: return
        if (EngineController.state().error != null) return
        showError(why)
        loadUrl(url)
        handler.postDelayed({ probeRendered() }, 2000)
        handler.postDelayed({ probeRendered() }, 7000)
    }

    /** 真正下发加载，同时记下时刻并挂两个看门狗：请求卡死时也能自己救回来。 */
    private fun loadUrl(url: String) {
        loadAt = System.currentTimeMillis()
        loadedUrl = url
        web.loadUrl(url)
        // 骨架页是过渡用的，不该留在历史里：否则用户按返回键会退回骨架屏，
        // 表现为「一返回就变灰块」。真页加载完成后清一次历史。
        if (skeletonReady && url.startsWith("http")) {
            handler.postDelayed({
                runCatching { web.clearHistory() }
            }, 1500)
        }
        handler.postDelayed({ probeRendered() }, 6000)
        handler.postDelayed({ probeRendered() }, 13000)
    }

    /**
     * 确保布局补丁已生效：<html> 上必须有 data-windows-titlebar。
     *
     * DSH 的布局逻辑是
     *   collapsedWidth = darwin || html.hasAttribute("data-windows-titlebar") ? 0 : 56
     * 属性在 → 左侧图标栏宽度 0（完全隐藏，侧边栏变成点按钮才滑出的抽屉）
     * 属性不在 → 宽度 56px，那条图标轨道就一直杵在屏幕左边。
     *
     * 正常情况下补丁自己会加好这个属性。这里是兜底：常驻引擎让页面不再重载，
     * 首屏没生效就永远不生效，所以由 App 主动补一次。
     */
    private fun ensureLayoutPatch() {
        if (!isEnginePage()) return
        web.evaluateJavascript(
            "(function(){" +
            "  var r=document.documentElement;" +
            "  if(!r.hasAttribute('data-windows-titlebar')) r.setAttribute('data-windows-titlebar','');" +
            "  return r.hasAttribute('data-windows-titlebar');" +
            "})()"
        ) { v ->
            if (v != null && v.contains("true")) {
                android.util.Log.i("SiyiLayout", "已确保 data-windows-titlebar 存在")
            }
        }
    }

    /**
     * 诊断：把布局相关的 DOM 状态写进日志。
     *
     * 关心三件事：
     *   attr       — <html> 上有没有 data-windows-titlebar（决定图标栏宽度）
     *   styleTag   — 补丁注入的 <style id=siyi-mobile-layout> 在不在
     *   railW      — 左侧图标栏实际渲染宽度
     */
    private fun dumpLayoutState() {
        web.evaluateJavascript(
            "(function(){" +
            "  var r=document.documentElement;" +
            "  var attr=r.hasAttribute('data-windows-titlebar');" +
            "  var st=!!document.getElementById('siyi-mobile-layout');" +
            "  var col=document.querySelector('[class*=\"sidebarCol\"]');" +
            "  var w=col?Math.round(col.getBoundingClientRect().width):-1;" +
            "  return 'attr='+attr+' styleTag='+st+' railW='+w+" +
            "         ' viewport='+window.innerWidth;" +
            "})()"
        ) { v -> android.util.Log.i("SiyiLayout", "状态 " + v) }
    }

    /** 用 JS 检查 React 是否已挂载内容；一直没有内容就当作白屏重挂。 */
    /**
     * 当前 WebView 是不是引擎自己的页面。
     *
     * 白屏检测只能作用于引擎页面。登录时会跳到 platform.deepseek.com 的授权页，
     * 那页没有 #root，如果不加判断就会被误判成白屏、
     * 5 秒后强行 reload 回引擎地址 —— 等于把用户从授权页拽走，
     * 授权流程中断，日志里表现为 sign-in failed { errorCode: 'expired' }。
     */
    private fun isEnginePage(): Boolean {
        val u = runCatching { web.url }.getOrNull() ?: return false
        return isEngineUrl(u)
    }

    /** 给定 URL 是不是引擎自己的地址。 */
    private fun isEngineUrl(u: String): Boolean =
        u.startsWith("http://127.0.0.1:") && u.contains(":" + EngineController.WEB_PORT)

    /**
     * 用系统浏览器打开外部链接。
     *
     * 登录授权页必须走真实浏览器：它要用到平台的 cookie 和跳转，
     * 塞在 WebView 里既不安全也容易卡死（window.open 曾直接变成 about:blank）。
     * 授权完成后浏览器会命中 http://127.0.0.1:3180/oauth/callback，
     * 由引擎处理并把控制权交回 App。
     */
    private fun openExternally(url: String) {
        runCatching {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        }.onFailure {
            android.util.Log.e("SiyiWeb", "打开外部浏览器失败: " + it.message)
            // 打不开浏览器就退回用 WebView 加载，总比什么都不做强
            runCatching { web.loadUrl(url) }
        }
    }

    private fun probeRendered() {
        // 空页兜底：window.open 被就地导航、或外部页面销毁后，WebView 会停在
        // about:blank。这种状态既不是引擎页面、也没人会来救，必须自己拉回来。
        val now = runCatching { web.url }.getOrNull()
        if (now == null || now == "about:blank" || now.isEmpty()) {
            val engineUrl = EngineController.state().url
            if (engineUrl != null && retries < 5) {
                retries++
                showWaiting("界面已重置，正在恢复…")
                loadUrl(engineUrl)
            }
            return
        }
        // 其它外部页面（登录授权页等）不参与白屏检测，避免把用户从授权流程里拽走
        if (!isEnginePage()) {
            waitView.visibility = View.GONE
            return
        }
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
                val url = st.url
                when {
                    // 1) 有新地址且和页面上加载的不是同一个 → 加载它。
                    //    引擎崩溃自动重启后 token 会变，这里必须跟着换，
                    //    否则会一直用旧 token 刷一个已经死掉的地址。
                    url != null && url != loadedUrl -> {
                        navigated = true
                        retries = 0
                        showWaiting("引擎已就绪，正在加载界面…")
                        loadUrl(url)
                    }
                    // 2) 引擎正在重连/失败 → 明确告诉用户，并继续等
                    st.phase == EngineController.Phase.FAILED -> {
                        navigated = false
                        showError("引擎正在自动重试…\n\n" + (st.error ?: ""))
                        handler.postDelayed(this, 2000)
                    }
                    // 3) 首次启动中
                    url == null -> {
                        val s = (System.currentTimeMillis() - startAt) / 1000
                        showWaiting("正在启动引擎…\n已等待 $s 秒（首次需解压资源，可能数十秒）")
                        handler.postDelayed(this, 1500)
                    }
                }
            }
        }, 800)
    }


    // ── 后台存活引导 ────────────────────────────────────────────────────────
    // 这是本 App 最大的体验隐患：引擎是个子进程，App 一进后台，
    // MIUI 会冻结整个进程（线程全部挂起，看门狗也不再探测），
    // 内存紧张时还会直接杀掉。表现就是「回到 App 一片白，要等很久才恢复」。
    //
    // 代码层面只能做到两件事：
    //   1. 申请电池优化白名单（最有效，能显著降低被冻结的概率）
    //   2. 给出可操作的自启动指引（MIUI 的自启动开关没有公开 API）
    // 真正的「无限制」需要用户自己去设置里点，所以这里只做一次性提醒。

    private fun ensureBackgroundSurvival() {
        val pm = getSystemService(POWER_SERVICE) as android.os.PowerManager
        if (pm.isIgnoringBatteryOptimizations(packageName)) return
        // 已经问过就不再骚扰
        val prefs = getSharedPreferences("siyi", MODE_PRIVATE)
        if (prefs.getBoolean("asked_battery", false)) return

        android.app.AlertDialog.Builder(this)
            .setTitle("让引擎在后台活下来")
            .setMessage(
                "思忆集的 AI 引擎跑在后台。系统会为了省电把它冻结甚至杀掉，" +
                "这就是「切出去一会儿回来就白屏」的原因。\n\n" +
                "点「去设置」后会弹出系统授权，请选「允许」。\n" +
                "另外建议在小米/红米的「应用设置 → 省电策略」里选「无限制」，" +
                "并打开「自启动」。"
            )
            .setPositiveButton("去设置") { _, _ ->
                prefs.edit().putBoolean("asked_battery", true).apply()
                runCatching {
                    startActivity(
                        Intent(
                            android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                            android.net.Uri.parse("package:$packageName")
                        )
                    )
                }
            }
            .setNegativeButton("以后再说") { _, _ ->
                prefs.edit().putBoolean("asked_battery", true).apply()
            }
            .show()
    }

    private fun showWaiting(msg: String) {
        if (msg == lastMsg) return
        lastMsg = msg
        // 骨架屏就绪时：正常等待阶段让骨架占位顶着，不再盖文字层（视觉无读条）。
        // 出错等情况走 showError()，那里会强制显示文字。
        if (skeletonReady) {
            waitView.visibility = View.GONE
            return
        }
        waitView.visibility = View.VISIBLE
        waitView.text = msg
    }

    /**
     * 出错/需要用户知道的状态：强制显示文字层（骨架屏模式下也要盖住骨架）。
     * 否则引擎真的失败时，用户只会看到永远转的骨架，看不到原因。
     */
    private fun showError(msg: String) {
        if (msg == lastMsg) return
        lastMsg = msg
        waitView.setBackgroundColor(0xFF151517.toInt())
        waitView.alpha = 1.0f
        waitView.text = msg
        waitView.visibility = View.VISIBLE
    }

    override fun onResume() {
        super.onResume()
        // 与 onPause 配对：即使将来有人 pause 了 WebView，这里也会恢复渲染。
        runCatching { web.onResume() }
        val url = EngineController.state().url
        if (url != null && url != loadedUrl) {
            // 后台期间引擎重启过（token 变了），必须重新加载
            navigated = true
            loadUrl(url)
        } else if (url != null) {
            // 从后台回来：引擎可能刚被解冻，页面也可能被系统回收了内容。
            // 常驻引擎下页面不会重载，布局补丁若首屏没生效就永远不生效，
            // 所以每次回前台都补一次。
            handler.postDelayed({ ensureLayoutPatch() }, 800)
            // 先体检一次（2 秒），不行再重挂（6.5 秒），最后兜底整页重载（12 秒）。
            retries = 0
            loadAt = System.currentTimeMillis()
            handler.postDelayed({ probeRendered() }, 2000)
            handler.postDelayed({ probeRendered() }, 6500)
            handler.postDelayed({ forceReloadIfBlank() }, 12000)
        }
    }

    /**
     * 保存 WebView 状态。
     *
     * 不保存的话，旋转屏幕、分屏、或系统回收 Activity 之后 WebView 会重建，
     * 正在进行的对话直接丢失，用户看到的是空白页。
     */
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        runCatching { web.saveState(outState) }
        loadedUrl?.let { outState.putString(KEY_LOADED_URL, it) }
        outState.putBoolean(KEY_NAVIGATED, navigated)
    }

    override fun onRestoreInstanceState(savedInstanceState: Bundle) {
        super.onRestoreInstanceState(savedInstanceState)
        loadedUrl = savedInstanceState.getString(KEY_LOADED_URL)
        navigated = savedInstanceState.getBoolean(KEY_NAVIGATED, false)
        val restored = runCatching { web.restoreState(savedInstanceState) }.getOrNull()
        if (restored == null) {
            // 恢复不出来就走 poll() 重新加载
            loadedUrl = null
            navigated = false
        }
    }

    override fun onPause() {
        // ⚠ 这里**不能**调 web.onPause()。
        // WebView.onPause() 会停掉渲染，必须和 web.onResume() 成对使用；
        // 只 pause 不 resume，WebView 就永远停在暂停态，
        // 回到前台页面是一片空白，只能退出重进才恢复。
        //
        // 不用 pauseTimers()：那会让引擎里的 SSE 对话流断掉。
        // 代价是后台仍在跑 WebView 的定时器和网络请求 ——
        // 对这个 App 可以接受，换来的是前后台切换不掉链子。
        super.onPause()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        runCatching { web.destroy() }
        super.onDestroy()
    }
}
