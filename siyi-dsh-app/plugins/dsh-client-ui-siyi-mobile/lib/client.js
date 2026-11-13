// 思忆集移动端布局补丁（浏览器半身）
//
// 解决三个窄屏问题：
//
// 1) 左侧图标栏占掉 12-14% 屏宽
//    DSH 布局包里有：const collapsedWidth = darwin || hasAttr('data-windows-titlebar') ? 0 : 56
//    也就是说给 <html> 加上 data-windows-titlebar 属性后，折叠轨道宽度变 0（完全隐藏），
//    侧边栏变成点按钮才滑出的抽屉——和官方 DSH 手机端一致。
//    该属性原本给 Windows 桌面用，会带来 padding-top / 圆角 / 拖拽伪元素三个副作用，
//    这里一并覆盖掉。
//
// 2) 设置面板在 376px 视口下右栏只剩约 140px，中文被迫逐字换行
//    面板原设计 800px、左栏固定 188px。窄屏改为纵向：标签横排在上、内容在下。
//
// 3) 通用弹窗宽度上限 380px / 440px 太窄
//
// ── 2026-11-14 折叠几何修正（关键）────────────────────────────
// 上一版的选择器写的是 [class*="_frame_"] / [class*="_centerCol_"]，
// 但这版 DSH 的 CSS Modules 类名是「前缀_hash_ + 名字」，实际是全小写拼接：
//   pI_x6G_frame  pI_x6G_centerCol  pI_x6G_sidebarCol
// 「_frame_」这种前后都带下划线的写法一个都匹配不上（实测 frame 的
// padding-top 仍是 40px，centerCol 的 y 起点也是 40px）。
// 于是 --dsh-windows-titlebar-height: 40px 生效了，但压掉顶部留白的规则失效，
// 屏幕上就出现「侧边栏没了、顶部却空出一条 40px 白带」的残留；折叠按钮是
// position:fixed、top:(40-28)/2=6px，正好卡在这条白带里，看着就是「太高、
// 贴到状态栏」。这就是用户看到的图一/图二。
//
// 现在的做法：
//   a) 选择器改成后缀匹配 [class$="_frame"] / [class$="_centerCol"]，
//      前缀 hash 变了也不影响；
//   b) 把 --dsh-windows-titlebar-height 压到 0，连同它派生的
//      --dsh-frame-top-clearance / --dsh-frame-chrome-top 一起归零，
//      白带彻底消失，其它插件也不再自作聪明留 40px；
//   c) 折叠按钮和「新建会话」按钮改成显式 top，不再依赖那个变量算
//      (H-28)/2（H=0 会算成 -14px，按钮直接被顶出视口）；
//      top:8px 是相对 WebView 视口的，App 侧已经把状态栏高度做成了
//      WebView 的 padding，所以 8px 落在状态栏下方。
//
// 实现要点：属性必须用 MutationObserver 持续保持。
// React 在挂载/重渲染时会整体重置 <html> 的属性，一次 setAttribute 会在
// 几百毫秒后被清掉（实测：设置后 attrNow=true，3 秒后 attrLater=false）。
//
// 不改动官方任何组件逻辑。
window.__ModuleLoader__.load({
  id: 'dsh-client-ui-siyi-mobile',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' })

    var STYLE_ID = 'siyi-mobile-layout'
    var ATTR = 'data-windows-titlebar'

    var CSS = [
      // ── 1. 隐藏左侧折叠轨道（collapsedWidth 变 0 后的副作用修正）──
      //
      // 顶部留白全部归零：白带、圆角、拖拽伪元素三个副作用一并清掉。
      // 注意 --dsh-frame-top-clearance / --dsh-frame-chrome-top 是由
      // --dsh-windows-titlebar-height 派生的，压 0 之后其它插件（会话头部、
      // 弹层）也不会再给顶部留 40px。
      'html[' + ATTR + '] {',
      '  --dsh-windows-titlebar-height: 0px !important;',
      '  --dsh-frame-top-clearance: 0px !important;',
      '  --dsh-frame-chrome-top: 0px !important;',
      '}',
      // 折叠态的两个按钮：Windows 标题栏里它们是 position:fixed，
      // top 由 (--dsh-windows-titlebar-height - 28px)/2 算出来。
      // 变量归零后会得到 -14px（被顶到视口外、看起来被状态栏盖住），
      // 所以这里直接钉死 top，不再参与那个计算。
      // 只对折叠态生效：展开态必须留在文档流里，否则会压在品牌图标上。
      'html[' + ATTR + '] .hHd-Xa_root.hHd-Xa_collapsed .hHd-Xa_toggle {',
      '  top: 8px !important;',
      '  left: 12px !important;',
      '}',
      'html[' + ATTR + '] .hHd-Xa_root.hHd-Xa_collapsed .hHd-Xa_newSession {',
      '  top: 8px !important;',
      '  left: 48px !important;',
      '}',
      // 展开态：把官方 Windows 规则给的 position:fixed 撤销，回到文档流。
      'html[' + ATTR + '] .hHd-Xa_root:not(.hHd-Xa_collapsed) .hHd-Xa_toggle {',
      '  position: static !important;',
      '  top: auto !important;',
      '  left: auto !important;',
      '}',
      'html[' + ATTR + '] .hHd-Xa_root:not(.hHd-Xa_collapsed) .hHd-Xa_newSession {',
      '  position: relative !important;',
      '  top: auto !important;',
      '  left: auto !important;',
      '}',
      // frame：去掉 Windows 标题栏的预留高度与圆角。
      // 类名是「hash_frame」形式，用后缀匹配，前缀变了也不受影响。
      'html[' + ATTR + '] [class$="_frame"] {',
      '  --dsh-windows-content-radius: 0px !important;',
      '  padding-top: 0 !important;',
      '  background: transparent !important;',
      '}',
      'html[' + ATTR + '] [class$="_frame"]::before { display: none !important }',
      'html[' + ATTR + '] [class$="_centerCol"] { border-radius: 0 !important }',
      // 折叠态侧栏容器自身也不留内边距（Windows 下是 padding:0，
      // 但保险起见写死，避免 hash 变体里又冒出 18px 顶部内边距）。
      'html[' + ATTR + '] .hHd-Xa_root.hHd-Xa_collapsed { padding: 0 !important }',
      // 折叠态：会话标题要让开左上角两个悬浮按钮。
      //
      // 官方机制是给 .titleRow 加 padding-inline-start =
      //   max(0px, calc(var(--dsh-frame-leading-clearance, 0px) - 20px))
      // 而这个变量只在 [data-platform=darwin] 下被赋值（折叠 160 / 全屏 84），
      // Android 上它就是 0 —— 于是标题内边距是 0，直接顶到最左边，
      // 被 toggle(12~40) 和 newSession(48~76) 两个 fixed 按钮压住。
      //
      // 这里按同一机制补值。实测标题起点 ≈ 30（基础内边距）+ padding，
      // padding = clearance - 20。按钮占到 76px，要让标题落在 ~80px：
      //   padding 50 → clearance 70
      // 取值宁小勿大：留白多一分，标题就少显示一分。
      // 只管折叠态：展开时侧栏本身占 280px，标题不需要额外让位。
      'html[' + ATTR + '] [class$="_frame"][data-sidebar-collapsed] {',
      '  --dsh-frame-leading-clearance: 70px !important;',
      '}',
      // 再挤一点宽度给标题：子智能体标签在窄屏上很占地方
      //（"1 个子智能体" + 箭头约 90px），缩一号；标题簇间距也收紧。
      'html[' + ATTR + '] [class$="_crumbSubagent"] {',
      '  font-size: 11px !important;',
      '  padding: 2px 6px !important;',
      '}',
      'html[' + ATTR + '] [class$="_titleCluster"] { gap: 6px !important }',
      // sidebarCol：折叠态宽度已是 0（collapsedWidth=0 由属性触发），
      // 这里只确保溢出内容不漏出来，绝不动 position —— 在 grid 子项上
      // 用 absolute 会破坏 DSH 的布局计算，导致白屏。
      'html[' + ATTR + '] [class$="_sidebarCol"] {',
      '  overflow: hidden !important;',
      '}',


      // ── 2. 设置面板窄屏适配 ──
      '@media (max-width: 720px) {',
      // 面板：占满宽度、纵向排列
      '.VOzbGW_panel {',
      '  width: calc(100vw - 16px) !important;',
      '  max-width: calc(100vw - 16px) !important;',
      '  flex-direction: column !important;',
      '}',
      // 左栏改顶部横向标签栏
      '.VOzbGW_panel > .VOzbGW_nav {',
      '  flex-direction: row !important;',
      '  flex: none !important;',
      '  width: 100% !important;',
      '  gap: 6px !important;',
      '  padding: 12px 12px 0 !important;',
      '  overflow-x: auto !important;',
      '  overflow-y: hidden !important;',
      '  align-items: center !important;',
      '}',
      '.VOzbGW_panel > .VOzbGW_nav > .VOzbGW_navTitle { display: none !important }',
      '.VOzbGW_panel > .VOzbGW_nav > .VOzbGW_navList {',
      '  flex-direction: row !important; gap: 6px !important; overflow: visible !important;',
      '}',
      '.VOzbGW_panel > .VOzbGW_nav .VOzbGW_navCell {',
      '  flex: 0 0 auto !important; width: auto !important;',
      '  white-space: nowrap !important; height: 34px !important;',
      '  padding: 6px 12px !important; font-size: 13px !important;',
      '}',
      // 内容区占满
      '.VOzbGW_panel > .VOzbGW_content {',
      '  flex: 1 1 auto !important; min-height: 0 !important;',
      '  width: 100% !important; overflow-y: auto !important;',
      '}',
      // 禁止中文逐字换行
      '.VOzbGW_content .VOzbGW_navLabel,',
      '.VOzbGW_content .VOzbGW_title,',
      '.VOzbGW_options .VOzbGW_label {',
      '  word-break: keep-all !important; overflow-wrap: normal !important;',
      '}',
      // 通用弹窗放开宽度
      '[role="dialog"] { max-width: calc(100vw - 16px) !important }',
      'html[' + ATTR + '] body {',
      '  padding-bottom: env(safe-area-inset-bottom, 0px) !important;',
      '}',
      '}',
    ].join('\n')

    /** 注入样式表。固定 style 元素，重复调用不叠加。 */
    function installStyles() {
      if (typeof document === 'undefined') return function () {}
      var old = document.getElementById(STYLE_ID)
      if (old) old.remove()
      var el = document.createElement('style')
      el.id = STYLE_ID
      el.textContent = CSS
      document.head.appendChild(el)
      return function () {
        try { el.remove() } catch (_) {}
      }
    }

    /**
     * 给 <html> 加 data-windows-titlebar，让 DSH 把折叠轨道宽度算成 0。
     *
     * 必须用 MutationObserver 持续保持：React 在挂载/重渲染时会整体重置
     * documentElement 的属性，一次 setAttribute 会在几百毫秒后被清掉。
     * 实测：设置后 attrNow=true，3 秒后 attrLater=false。
     */
    function installCollapse() {
      if (typeof document === 'undefined') return function () {}
      var root = document.documentElement
      if (!root) return function () {}

      var applying = false
      function ensure() {
        if (applying) return
        applying = true
        try {
          if (!root.hasAttribute(ATTR)) root.setAttribute(ATTR, '')
          if (!document.getElementById(STYLE_ID)) {
            var el = document.createElement('style')
            el.id = STYLE_ID
            el.textContent = CSS
            document.head.appendChild(el)
          }
        } catch (e) {} finally { applying = false }
      }

      ensure()

      var obs = null
      try {
        obs = new MutationObserver(function () { ensure() })
        obs.observe(root, { attributes: true, attributeFilter: [ATTR] })
        // head 里样式被删也要补回来
        if (document.head) obs.observe(document.head, { childList: true })
      } catch (e) {}

      // 兜底轮询：MutationObserver 覆盖不到的场景（例如整棵树被替换）
      var timer = setInterval(ensure, 800)

      return function () {
        try { if (obs) obs.disconnect() } catch (_) {}
        clearInterval(timer)
        try { root.removeAttribute(ATTR) } catch (_) {}
        try {
          var el = document.getElementById(STYLE_ID)
          if (el) el.remove()
        } catch (_) {}
      }
    }

    // ── 页面级安装（幂等单例）──────────────────────────────
    //
    // 这一段必须在模块体里执行：apply(ctx) 的调用时机由宿主决定，而宿主的
    // 客户端作用域会在 apply 返回后立刻关闭，补丁不能依赖它存活。
    var siyiInstalled = false
    function install() {
      if (typeof document === 'undefined') return
      if (siyiInstalled) return
      siyiInstalled = true
      installStyles()
      installCollapse()
      installTitleTap()
    }

    /**
     * 点会话标题 → 弹出完整标题。
     *
     * 为什么需要：窄屏下标题行同时要塞「标题 + 子智能体标签 + 菜单 + 右侧图标」，
     * 标题必然被 CSS 截断成「本地网…」。DOM 里的文本其实是完整的，
     * 只是视觉上 overflow:hidden，所以点一下把 textContent 原样弹出来即可。
     *
     * 用捕获阶段的事件委托，不绑死节点：React 重渲染会换掉整个标题 DOM，
     * 绑在元素上的监听会一起没；绑在 document 上就不会。
     */
    function installTitleTap() {
      if (typeof document === 'undefined') return
      if (window.__siyiTitleTap) return
      window.__siyiTitleTap = true

      var pop = null

      var closePop = function () {
        if (pop && pop.parentNode) pop.parentNode.removeChild(pop)
        pop = null
      }

      /**
       * 在标题正下方弹出完整标题。
       *
       * 样式全部走 DSH 自己的设计令牌，跟随主题（浅色/深色自动一致），
       * 不用硬编码颜色——上一版写死 #26262b 深色卡片，在浅色界面里非常突兀。
       */
      var showPop = function (text, anchor) {
        closePop()
        var r = anchor.getBoundingClientRect()
        var top = Math.round(r.bottom + 6)
        // 屏幕下半部分就翻到上方，避免贴底被裁
        var below = top + 120 < window.innerHeight
        pop = document.createElement('div')
        pop.id = 'siyi-title-pop'
        pop.textContent = text
        var css = [
          'position:fixed',
          'z-index:2147483000',
          'left:12px',
          'max-width:min(calc(100vw - 24px),420px)',
          'box-sizing:border-box',
          'padding:10px 14px',
          'background:var(--dsw-alias-bg-layer-2,var(--dsw-alias-bg-base,#fff))',
          'color:var(--dsw-alias-label-primary,#1a1a1a)',
          'border:1px solid var(--dsw-alias-border-l2,rgba(0,0,0,.10))',
          'border-radius:var(--dsw-radius-md,10px)',
          'box-shadow:0 8px 24px rgba(0,0,0,.14)',
          'font-size:14px',
          'line-height:1.55',
          'font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif',
          'white-space:pre-wrap',
          'word-break:break-word',
          'user-select:text'
        ]
        if (below) css.push('top:' + top + 'px')
        else css.push('bottom:' + Math.round(window.innerHeight - r.top + 6) + 'px')
        pop.style.cssText = css.join(';')
        document.body.appendChild(pop)
      }

      document.addEventListener('click', function (ev) {
        var t = ev.target
        // 点在弹层内部：交给浏览器（可选中文本），不关也不拦
        if (pop && pop.contains(t)) return

        // 找到标题 crumb（排除分隔符和子智能体标签，后者有自己的行为）
        var el = t
        var hops = 0
        while (el && el !== document.body && hops < 8) {
          var cn = (el.getAttribute && el.getAttribute('class')) || ''
          if (typeof cn === 'string' && cn.indexOf('_crumb') >= 0 &&
              cn.indexOf('_crumbSubagent') < 0 && cn.indexOf('_crumbSep') < 0) {
            var txt = (el.textContent || '').trim()
            if (txt) {
              ev.preventDefault()
              ev.stopPropagation()
              // 再点一次 = 收起（toggle），可控
              if (pop) closePop()
              else showPop(txt, el)
            }
            return
          }
          el = el.parentElement
          hops++
        }

        // 点在任何别处 = 收起
        if (pop) closePop()
      }, true)

      // 页面尺寸变化（旋转/分屏）时收起，避免弹层停在错误位置
      window.addEventListener('resize', function () { closePop() })
    }

    // 诊断面板：只在 URL 带 siyi-debug 时出现，平时完全不渲染。
    try {
      if (typeof location !== 'undefined' && location.hash.indexOf('siyi-debug') >= 0) {
        var mkBox = function () {
          var d = document.getElementById('siyi-debug-box')
          if (!d) {
            d = document.createElement('div')
            d.id = 'siyi-debug-box'
            d.style.cssText = 'position:fixed;z-index:2147483647;top:120px;left:0;right:0;' +
              'background:#d00;color:#fff;font:10px monospace;padding:4px;' +
              'white-space:pre-wrap;text-align:left'
            document.body.appendChild(d)
          }
          var root = document.documentElement
          var pick = function (sel) {
            var e = document.querySelector(sel)
            if (!e) return sel + '=none'
            var r = e.getBoundingClientRect()
            var cs = getComputedStyle(e)
            return sel + ' x=' + Math.round(r.left) + ' y=' + Math.round(r.top) +
              ' w=' + Math.round(r.width) + ' h=' + Math.round(r.height) + ' pos=' + cs.position
          }
          var frame = document.querySelector('[class$="_frame"]')
          var lines = [
            'attr=' + root.hasAttribute(ATTR)
            + ' style=' + document.querySelectorAll('style#' + STYLE_ID).length
            + ' vw=' + window.innerWidth + ' vh=' + window.innerHeight,
            pick('.hHd-Xa_toggle'),
            pick('.hHd-Xa_newSession'),
            pick('[class$="_frame"]'),
            pick('[class$="_sidebarCol"]'),
            pick('[class$="_centerCol"]'),
            'framePadTop=' + (frame ? getComputedStyle(frame).paddingTop : '-'),
            'cols=' + (frame ? getComputedStyle(frame).gridTemplateColumns : '-'),
          ]
          d.textContent = lines.join('\n')
        }
        setInterval(mkBox, 500)
        mkBox()
      }
    } catch (e) {}


    var inject = []

    /**
     * 宿主调用入口。
     *
     * 这里只做两件事：把补丁再装一次（幂等）、把「已安装」的旗子挂到页面作用域。
     *
     * 为什么不把拆除写进 ctx.effect：
     * 实测本版宿主的客户端插件作用域是「打开 → 调用 apply → 立刻关闭」，
     * apply 返回后几毫秒内 effect 的回收函数就被执行了。如果把移除样式/属性的
     * 逻辑挂在 effect 上，补丁会被自己拆掉——表现正是「进页面时侧边栏没了，
     * 过一会儿又冒回来 56px」，顶部还留一条 40px 白带。
     * 布局补丁是全页面级的（不属于某个组件），所以它跟页面同生命周期、不跟
     * 插件作用域。移除只发生在页面卸载时（style 和属性随文档一起消失）。
     */
    function apply(ctx) {
      install()
      // 保留 ctx 引用以避免未使用参数告警；不注册任何可拆除的贡献。
      void ctx
    }

    exports.apply = apply
    exports.inject = inject

    // 模块求值时立刻安装：这一步是补丁能不能生效的关键，见 install() 上方注释。
    try { install() } catch (_) {}

    return module.exports
  },
})
