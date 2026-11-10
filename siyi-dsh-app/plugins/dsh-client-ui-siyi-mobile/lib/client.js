// 思忆集移动端布局补丁（浏览器半身）
//
// 背景：DSH 设置面板按桌面宽度设计
//   .VOzbGW_panel { width: 800px; max-width: calc(100vw - 48px) }
//   .VOzbGW_nav   { width: 188px; flex: none }
// 在 376px 视口的手机上，面板被压到 328px，左侧固定 188px 不收缩，
// 右侧内容区只剩约 140px，中文被迫逐字换行。
//
// 做法：只针对设置面板的类名做窄屏覆盖。
// 面板用的是固定前缀 .VOzbGW_（由 dsh-client-ui-settings-general 注入），
// 所以直接用这个前缀，不用 [class*=panel] 这类宽泛匹配——那样会误伤主界面。
// 也不用 :has()，避免老 WebView 解析异常导致整段样式失效。
window.__ModuleLoader__.load({
  id: 'dsh-client-ui-siyi-mobile',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' })

    var STYLE_ID = 'siyi-mobile-layout'

    var CSS = [
      '@media (max-width: 720px) {',
      // 面板：占满可用宽度，纵向排列（标签栏在上，内容在下）
      '.VOzbGW_panel {',
      '  width: calc(100vw - 16px) !important;',
      '  max-width: calc(100vw - 16px) !important;',
      '  flex-direction: column !important;',
      '}',
      // 左侧导航：改为顶部横向标签栏，可横向滚动
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
      // 隐藏「设置」大标题，省出横向空间
      '.VOzbGW_panel > .VOzbGW_nav > .VOzbGW_navTitle { display: none !important }',
      // 导航列表横向排列
      '.VOzbGW_panel > .VOzbGW_nav > .VOzbGW_navList {',
      '  flex-direction: row !important;',
      '  gap: 6px !important;',
      '  overflow: visible !important;',
      '}',
      // 导航项按内容宽度，不拉伸
      '.VOzbGW_panel > .VOzbGW_nav .VOzbGW_navCell {',
      '  flex: 0 0 auto !important;',
      '  width: auto !important;',
      '  white-space: nowrap !important;',
      '  height: 34px !important;',
      '  padding: 6px 12px !important;',
      '  font-size: 13px !important;',
      '}',
      // 内容区占满剩余高度，可滚动
      '.VOzbGW_panel > .VOzbGW_content {',
      '  flex: 1 1 auto !important;',
      '  min-height: 0 !important;',
      '  width: 100% !important;',
      '  overflow-y: auto !important;',
      '}',
      // 只对设置面板内部的文字禁止逐字换行（不动主界面）
      '.VOzbGW_content .VOzbGW_navLabel,',
      '.VOzbGW_content .VOzbGW_title,',
      '.VOzbGW_options .VOzbGW_label {',
      '  word-break: keep-all !important;',
      '  overflow-wrap: normal !important;',
      '}',
      '}',
    ].join('\n')

    /** 注入样式表；固定 style 元素，重复调用不叠加。@returns 卸载函数 */
    function installStyles() {
      if (typeof document === 'undefined') return () => {}
      var existing = document.getElementById(STYLE_ID)
      if (existing) existing.remove()
      var el = document.createElement('style')
      el.id = STYLE_ID
      el.textContent = CSS
      document.head.appendChild(el)
      return function () {
        try { el.remove() } catch (_) {}
      }
    }

    var inject = []

    function apply(ctx) {
      var dispose = installStyles()
      if (ctx && typeof ctx.effect === 'function') {
        ctx.effect(function () { return dispose })
      }
    }

    exports.apply = apply
    exports.inject = inject
    return module.exports
  },
})
