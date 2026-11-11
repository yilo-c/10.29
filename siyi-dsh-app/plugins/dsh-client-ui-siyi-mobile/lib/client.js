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
      // 关键：--dsh-windows-titlebar-height 不能压成 0。
      // 左侧折叠按钮的样式是：
      //   .hHd-Xa_toggle { top: calc((var(--dsh-windows-titlebar-height) - 28px) / 2);
      //                    position: fixed; left: 12px }
      // 代入 0 得 top = -14px，按钮被顶到视口上方，正好被状态栏盖住。
      // 右侧按钮不走 fixed，所以在文档流里被 WebView padding 正常推下来 —— 这就是
      // 「右边好、左边坏」的全部原因。
      //
      // WebView 已由 App 侧加了状态栏高度的 padding（实测 118px），
      // 所以这里给一个让按钮落在视口内的取值：top = (40 - 28)/2 = 6px。
      'html[' + ATTR + '] {',
      '  --dsh-windows-titlebar-height: 40px !important;',
      '}',
      // 兜底：直接钉住折叠按钮位置，不依赖上面那个变量参与计算
      'html[' + ATTR + '] .hHd-Xa_toggle {',
      '  top: 6px !important;',
      '  left: 12px !important;',
      '}',
      // 去掉圆角与顶部拖拽条
      'html[' + ATTR + '] [class*="_frame_"] {',
      '  --dsh-windows-content-radius: 0px !important;',
      '  padding-top: 0 !important;',
      '}',
      'html[' + ATTR + '] [class*="_frame_"]::before { display: none !important }',
      'html[' + ATTR + '] [class*="_centerCol_"] { border-radius: 0 !important }',
      // 轨道列：关闭态完全消失（不占 grid 列位）。
      // 折叠时 DSH 给第一列算出 0px，这里让列本身也不渲染，双保险。
      // 展开时 DSH 把第一列算成 264px 并挤压主内容 —— 用绝对定位让侧边栏
      // 浮在上层、主内容保持全宽（覆盖式抽屉，不挤压）。
      // sidebarCol：折叠态宽度已是 0（collapsedWidth=0 由属性触发），
      // 这里只确保溢出内容不漏出来，绝不动 position —— 在 grid 子项上
      // 用 absolute 会破坏 DSH 的布局计算，导致白屏。
      'html[' + ATTR + '] body .pI_x6G_sidebarCol {',
      '  overflow: hidden !important;',
      '}',
      // 展开态：DSH 给 frame 的第一列一个正值（--dsh-windows-sidebar-width > 0）
      // 就让侧边栏滑入可见



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
     * 返回卸载函数（移除属性）。
     */
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
        try { clearInterval(drawerTimer) } catch (_) {}
        try { root.removeAttribute(ATTR) } catch (_) {}
        try {
          var el = document.getElementById(STYLE_ID)
          if (el) el.remove()
        } catch (_) {}
      }
    }

    var inject = []

    function apply(ctx) {
      var disposeStyle = installStyles()
      var disposeAttr = installCollapse()
      if (ctx && typeof ctx.effect === 'function') {
        ctx.effect(function () {
          disposeStyle()
          disposeAttr()
        })
      }
    }

    exports.apply = apply
    exports.inject = inject

    // 立即执行：不依赖 cordis 调用 apply。
    // 纯 DOM 副作用，重复执行无副作用（样式用固定 id，属性用 setAttribute）。
    try {
      installStyles()
      installCollapse()
    } catch (e) {
      // 浏览器环境异常时静默，不影响主流程
    }

    return module.exports
  },
})
