// 思忆集 · 笔记面板（浏览器半身）D2/D3/D4
//
// 一个右侧栏页面，内含三个视图：
//   列表  D2：搜索、按类型筛选、按更新时间排
//   编辑  D3：大标题 + 正文 + 标签，仿 Redmi 笔记的排布
//   图谱  D4：标题成节点、双链成边，纯 SVG 无 d3
//
// 数据全部走同源 HTTP：/siyi-vault/{list,get,save,delete,graph,tasks,stats,upload,asset}
// 这个接口由 /sdcard/思忆集/plugins/siyi-vault.js 挂在引擎的 webServer 上，
// 所以前端不需要额外配置端口或鉴权（已经是登录态页面）。
//
// 为什么用 window.__ModuleLoader__ 而不是 ESM：
// DSH 的客户端插件全部是这个契约，宿主按 factory 注入 require，
// 只能拿到 react 和白名单里的官方包。
window.__ModuleLoader__.load({
  id: 'dsh-client-ui-siyi-notes',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' })

    const react = require('react')
    // SIYI-OFFICIALICON-20261119：复用 DSH 官方图标组件（factory 白名单）
    let P = {}
    try { P = require('@deepseek-ai/dsh-client-ui-primitives') || {} } catch (e) {}
    const IconClose = P.IconCloseOutlineRegular
    // SIYI-AIOFFICIAL-20261119：复用官方组件（Button / Checkbox / Input）
    const OButton = P.Button
    const OCheckbox = P.Checkbox
    const OInput = P.Input
    const OPill = P.Pill
    const OToast = P.Toast
    const OModal = P.Modal
    // SIYI-AIMENU-20261120：官方菜单/流光/提示
    const OMenu = P.Menu
    const OMenuItem = P.MenuItemButton
    const OShimmer = P.TextShimmer
    const OTooltip = P.Tooltip   // SIYI-OMODAL-20261120：格式/插入菜单   // SIYI-OTOAST-20261119：官方提示
    const OSeg = P.SegmentedControl   // SIYI-SEGMENTED-20261119：编辑/阅读切换   // SIYI-OBTN-20261119：文件夹 chips 用官方 Pill
    // SIYI-ICONBATCH-20261119：编辑器/列表用到的官方图标（缺则回落到文字）
    const I = {
      edit: P.IconEditOutlineRegular,
      checklist: P.IconChecklistOutlineRegular,
      plus: P.IconPlusOutlineRegular,
      image: P.IconPaperclipOutlineRegular,   // 图片/附件
      eye: P.IconBrowseOutlineRegular,        // 阅读/浏览
      more: P.IconEllipsisOutlineRegular,
      back: P.IconChevronLeftOutlineRegular,
      search: P.IconSearchOutlineRegular,
      calendar: P.IconCalendarOutlineRegular,
      graph: P.IconLinkOutlineRegular,
      data: P.IconDataOutlineRegular,
      alarm: P.IconAlarmClockOutlineRegular,
      list: P.IconFlatListOutlineRegular,
      clock: P.IconClockOutlineRegular,
      compare: P.IconCompareSplitOutlineRegular,   // SIYI-MODELICON-20261120：官方模型按钮图标
      link: P.IconLinkOutlineRegular,
      download: P.IconDownloadOutlineRegular,
      sparkle: P.IconSparkleRegular,
      send: P.IconSendOutlineRegular,
      stop: P.IconStopFillRegular,
      trash: P.IconTrashOutlineRegular,
      pin: P.IconPinOutlineRegular,
      folder: P.IconFolderOpenOutlineRegular,
      tag: undefined,     // 官方无 tag 图标，回落文字
      check: P.IconCheckOutlineRegular,
      code: P.IconCodeOutlineRegular,
      copy: P.IconCopyOutlineRegular,
      refresh: P.IconRefreshOutlineRegular,
      chevronDown: P.IconChevronDownOutlineRegular,
      close: P.IconCloseOutlineRegular,
      loading: P.IconLoadingOutlineRegular,
      // SIYI-ICONALL-20261119：菜单/状态类图标
      undo: P.IconChevronLeftOutlineRegular,
      redo: P.IconChevronRightOutlineRegular,
      quote: P.IconListPenOutlineRegular,
      pin: P.IconPinOutlineRegular,
      pinFill: P.IconPinFillRegular,
      star: P.IconPinOutlineRegular,
      check: P.IconCheckOutlineRegular,
      checkCircle: P.IconCheckCircleOutlineRegular,
      checkCircleFill: P.IconCheckCircleFillRegular,
      circle: P.IconCheckCircleOutlineRegular,
      note: P.IconListPenOutlineRegular,
      book: P.IconReadOnlyRegular,
      time: P.IconClockOutlineRegular,
      info: P.IconInfoOutlineRegular,
      warning: P.IconWarningTriangleOutlineRegular,
      question: P.IconQuestionOutlineRegular,
      data: P.IconDataOutlineRegular,
      plan: P.IconPlanOutlineRegular,
      light: P.IconLightOutlineRegular,
    }
    const h = react.createElement
    const { useState, useEffect, useRef, useCallback, useMemo } = react

    const API = '/siyi-vault'

    // ── 数据层 ──────────────────────────────────────────────────────
    async function api(path, opts) {
      const r = await fetch(API + path, opts)
      if (!r.ok) throw new Error('HTTP ' + r.status)
      return await r.json()
    }
    const listNotes = (params) => api('/list?' + new URLSearchParams(params || {}))
    const getNote = (id) => api('/get?id=' + encodeURIComponent(id))
    const saveNote = (data) => api('/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    const deleteNote = (id) => api('/delete?id=' + encodeURIComponent(id), { method: 'POST' })
    const getGraph = () => api('/graph')
    const getTasks = () => api('/tasks')
    const getStats = () => api('/stats')

    // AI 助手：走宿主插件 /siyi-vault/chat 代理，复用主 agent 已配好的模型与登录态。
    // 笔记面板不直接打主 Remote RPC —— prompt 只返回 accepted，回复走 follow WebSocket mux
    // 流（需登录签名），裸 fetch 页面接不到也不该碰主对话。代理层在引擎内聚合好回复后
    // 一次性返回 {ok, reply}，这里只负责发问题与显示。
    // 请求体固定为 { question, noteText }：noteText 由「引用当前笔记」开关决定传不传。
    // SIYI-AIMENU-20261120：可选 model（仅本次），服务端白名单校验
    const chatWithAI = (question, noteText, model, signal) => api('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, noteText: noteText || '', model: model || null }),
      signal,
    })

    // 图片上传：走宿主插件 /siyi-vault/upload 的原始字节协议
    // （协议注释见 plugins/siyi-vault.js 的 handleUpload 上方）。
    // 返回 { ok, url, name, size, dedup }，url 形如 /siyi-vault/asset/0123456789abcdef.png
    async function uploadImage(file) {
      const r = await fetch(API + '/upload', {
        method: 'POST',
        headers: {
          'Content-Type': file.type || 'application/octet-stream',
          'X-Siyi-Filename': encodeURIComponent(file.name || 'image'),
        },
        body: await file.arrayBuffer(),
      })
      let j = null
      try { j = await r.json() } catch (e) {}
      if (!r.ok || !j || !j.ok) throw new Error((j && j.error) || ('HTTP ' + r.status))
      return j
    }

    // ── 样式 ────────────────────────────────────────────────────────
    // 用 DSH 的 --dsw-alias-* 令牌，跟官方组件同风格；
    // 每个类名带 siyi- 前缀避免和官方 CSS Modules 的 hash 名撞。
    const CSS = `
/* ── 手机端 UI 约定（2026-11-17 打磨）───────────────────────────────
   1. 可点元素高度 >= 36px：WCAG 2.5.8 底线是 24px，Material 建议 48dp，
      手机上 30px 的按钮经常点不中，这里统一提到 36-44。
   2. -webkit-tap-highlight-color: transparent —— 去掉点击时的灰色方块闪。
   3. touch-action: manipulation —— 去掉双击缩放的 300ms 等待。
   4. overscroll-behavior: contain —— 列表滚到底不要带动整页橡皮筋。
   5. 底部工具栏留 env(safe-area-inset-bottom) —— 避开全面屏手势条。
   6. 正文 15px / 行高 1.75 —— 小四号偏上，长文阅读不累。            */
.siyi-notes-root{display:flex;flex-direction:column;height:100%;min-height:0;
  /* SIYI-TOPCLEAR-20261128：作为 main 面板时，顶部要给 DSH 的全局浮层（搜索/侧边栏/新建会话）让位。
     官方 main 面板用 --dsh-frame-top-clearance，Android 上该变量未定义，兜底 44px。 */
  padding-top:calc(var(--dsh-frame-top-clearance, 0px) + 44px);box-sizing:border-box;
  background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);
  font-size:14px;line-height:1.6;
  -webkit-tap-highlight-color:transparent}
/* SIYI-LISTIA-20261119：搜索独占整行，功能按钮移到第二行，新建用浮动按钮 */
.siyi-notes-tools{display:flex;align-items:center;justify-content:flex-end;gap:6px;padding:0 10px 8px;flex:none;overflow-x:auto}
/* SIYI-ICONLIST-20261119：带官方图标的按钮，图标与文字水平居中对齐 */
.siyi-btn-icon{display:inline-flex;align-items:center;justify-content:center;gap:5px}
.siyi-notes-fab{position:fixed;right:16px;bottom:86px;width:52px;height:52px;border-radius:50%;
  border:none;background:var(--dsw-alias-state-business-primary,#4a7dff);color:#fff;font-size:28px;
  display:flex;align-items:center;justify-content:center;box-shadow:0 4px 12px rgba(0,0,0,.25);z-index:4}

/* SIYI-SIMPLE-20261120：顶栏 ⋯ 菜单 */

/* SIYI-SORTBTN-20261120：菜单选中态 */
.siyi-simple-menu-item.on{color:var(--dsw-alias-state-business-primary);font-weight:600}
/* SIYI-MENUSTYLE-20261120：照主对话菜单弹窗——透明遮罩、白卡片、分组标题、勾选 */
.siyi-simple-menu{position:fixed;inset:0;z-index:60;background:transparent;display:flex;align-items:flex-start;justify-content:flex-end;padding:52px 10px 0}
.siyi-simple-menu-panel{min-width:220px;max-width:min(320px,calc(100vw - 24px));border-radius:14px;background:var(--dsw-specific-input-major,#fff);box-shadow:var(--dsw-elevation-prominent,0 8px 32px rgba(0,0,0,.18));padding:5px;display:flex;flex-direction:column;gap:1px;border:1px solid var(--dsw-alias-border-l3)}
.siyi-simple-menu-total{font-size:11px;color:var(--dsw-alias-label-tertiary);padding:8px 12px 4px}
.siyi-simple-menu-item{display:flex;align-items:center;gap:10px;min-height:40px;padding:0 12px;border:none;background:transparent;color:var(--dsw-alias-label-primary);font-size:13px;cursor:pointer;border-radius:9px;text-align:left;white-space:nowrap}
.siyi-simple-menu-item:active{background:var(--dsw-alias-interactive-bg-hover)}
.siyi-simple-menu-item.on{background:var(--dsw-alias-bg-layer-2);font-weight:500}
.siyi-simple-menu-icon{width:18px;flex:none;display:grid;place-items:center;color:var(--dsw-alias-label-secondary)}
.siyi-simple-menu-check{flex:none;margin-left:auto;color:var(--dsw-alias-state-business-primary);display:grid;place-items:center}
.siyi-notes-bar{display:flex;align-items:center;gap:8px;padding:8px 10px;flex:none;
  border-bottom:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-search{flex:1;min-width:0;height:38px;padding:0 12px;border-radius:10px;
  border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);
  color:var(--dsw-alias-label-primary);font-size:14px;outline:none;
  -webkit-appearance:none}
.siyi-notes-search:focus{border-color:var(--dsw-alias-border-l1)}
/* SIYI-OINPUT-20261119：官方 Input 外层占满搜索行 */
.siyi-notes-search-wrap{flex:1;min-width:0;width:100%}
.siyi-notes-btn{min-height:36px;padding:0 12px;border-radius:10px;flex:none;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center;
  border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);
  color:var(--dsw-alias-label-primary);font-size:14px;
  touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.siyi-notes-btn:active{background:var(--dsw-alias-bg-layer-3)}
.siyi-notes-btn.on{background:var(--dsw-alias-bg-layer-3);font-weight:600}
.siyi-notes-tabs{display:flex;gap:8px;padding:8px 10px;flex:none;overflow-x:auto;
  border-bottom:.5px solid var(--dsw-alias-border-l3);
  scrollbar-width:none;overscroll-behavior-x:contain;align-items:center}
.siyi-notes-tabs::-webkit-scrollbar{display:none}
/* 文件夹计数徽标：紧跟名字的小号数字 */
.siyi-notes-folder-count{font-size:11px;opacity:.6;margin-left:5px;
  font-variant-numeric:tabular-nums}
/* 新建文件夹的「＋」：和别的按钮同高，但视觉更轻，暗示次要动作 */
.siyi-notes-folder-add{min-width:36px;font-size:18px;line-height:1;
  padding:0 8px;color:var(--dsw-alias-label-secondary)}
/* 新建文件夹的输入区：整体不换行，避免挤掉其它按钮 */
.siyi-notes-folder-new{display:inline-flex;align-items:center;gap:6px;flex:none}
.siyi-notes-folder-input{height:36px;width:132px;padding:0 10px;border-radius:10px;
  border:.5px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-2);
  color:var(--dsw-alias-label-primary);font-size:14px;outline:none;
  -webkit-appearance:none;touch-action:manipulation}
.siyi-notes-folder-input:focus{border-color:var(--dsw-alias-label-primary)}
.siyi-notes-list{flex:1;overflow-y:auto;min-height:0;padding:4px 0;
  overscroll-behavior:contain;-webkit-overflow-scrolling:touch}

/* SIYI-SIMPLE1-20261120-ITEM：列表项规范 */
.siyi-notes-item{position:relative}
.siyi-notes-item-title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-notes-item-more{position:absolute;right:6px;top:50%;transform:translateY(-50%);width:44px;height:44px;border:none;background:transparent;color:var(--dsw-alias-label-tertiary);display:grid;place-items:center;cursor:pointer;border-radius:8px}
.siyi-notes-item-more:active{background:var(--dsw-alias-interactive-bg-hover)}
.siyi-notes-item-meta{padding-right:44px}
.siyi-notes-item{padding:12px;min-height:52px;box-sizing:border-box;cursor:pointer;
  border-bottom:.5px solid var(--dsw-alias-border-l3);touch-action:manipulation}
.siyi-notes-item:active{background:var(--dsw-alias-bg-layer-2)}
.siyi-notes-item-title{font-size:15px;font-weight:500;margin-bottom:4px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-notes-item-meta{font-size:12px;color:var(--dsw-alias-label-secondary);
  display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.siyi-notes-tag{font-size:11px;padding:2px 7px;border-radius:5px;
  background:var(--dsw-alias-bg-layer-3);color:var(--dsw-alias-label-secondary)}
.siyi-notes-empty{padding:48px 24px;text-align:center;
  color:var(--dsw-alias-label-tertiary);font-size:14px;line-height:1.7}
.siyi-notes-editor{flex:1;display:flex;flex-direction:column;min-height:0;overflow:hidden}
.siyi-notes-editor-head{display:flex;align-items:center;gap:8px;padding:8px 10px;flex:none;
  border-bottom:.5px solid var(--dsw-alias-border-l3)}
/* 标题用 textarea 而不是 input：手机上长标题必须能自动换行，
   input 是单行元素，超长只能横向拖动，没法看全（2026-11-17 实测截图确认）。
   overflow:hidden + resize:none + 自动增高，让它看起来像一行输入框，
   但内容多了会自然长高并保持所有文字可见。 */
.siyi-notes-title-input{display:block;width:100%;box-sizing:border-box;
  border:none;outline:none;background:transparent;
  color:var(--dsw-alias-label-primary);font-size:20px;font-weight:600;
  font-family:inherit;padding:14px 16px 8px;line-height:1.35;
  overflow:hidden;resize:none;white-space:pre-wrap;overflow-wrap:break-word;
  word-break:break-word;min-height:56px;max-height:40vh;-webkit-overflow-scrolling:touch}
.siyi-notes-body-input{width:100%;box-sizing:border-box;flex:1;border:none;outline:none;background:transparent;
  color:var(--dsw-alias-label-primary);font-size:15px;line-height:1.75;
  padding:8px 16px 20px;resize:none;font-family:inherit;min-height:0;
  overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
.siyi-notes-foot{font-size:11px;color:var(--dsw-alias-label-tertiary);
  padding:8px 16px;flex:none;display:flex;justify-content:space-between;
  border-top:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-graph{flex:1;min-height:0;overflow:hidden;position:relative;
  touch-action:none;user-select:none;-webkit-user-select:none}
.siyi-notes-graph svg{width:100%;height:100%;display:block}
.siyi-notes-legend{position:absolute;left:8px;bottom:8px;font-size:10px;
  color:var(--dsw-alias-label-tertiary);background:var(--dsw-alias-bg-layer-1);
  padding:5px 9px;border-radius:6px;border:.5px solid var(--dsw-alias-border-l3);
  pointer-events:none}
.siyi-notes-toast{position:absolute;left:50%;transform:translateX(-50%);
  bottom:calc(16px + env(safe-area-inset-bottom, 0px));
  background:var(--dsw-alias-bg-layer-3);color:var(--dsw-alias-label-primary);
  font-size:13px;padding:8px 16px;border-radius:16px;z-index:9;
  border:.5px solid var(--dsw-alias-border-l3);pointer-events:none;
  max-width:calc(100% - 32px);text-align:center}

/* SIYI-COMPOSERCSS-20261120：照官方 composer（对话输入框）复刻 */
.siyi-cmp-root{display:flex;flex-direction:column;gap:8px;padding:8px 10px 10px;background:var(--dsw-alias-bg-base);border-top:.5px solid var(--dsw-alias-border-l3);flex:none}
.siyi-cmp-log{max-height:38vh;overflow-y:auto;display:flex;flex-direction:column;gap:10px;padding:2px 2px 4px;-webkit-overflow-scrolling:touch}
.siyi-cmp-msg{display:flex;flex-direction:column;gap:4px;align-items:flex-start}
.siyi-cmp-msg.me{align-items:flex-end}
.siyi-cmp-bubble{max-width:86%;padding:8px 12px;border-radius:var(--dsw-radius-panel);background:var(--dsw-specific-input-major);box-shadow:var(--dsw-elevation-soft);font-size:var(--dsh-content-font-size,14px);line-height:1.7;white-space:pre-wrap;overflow-wrap:break-word}
.siyi-cmp-msg.me .siyi-cmp-bubble{background:var(--dsw-alias-state-business-primary);color:#fff}
.siyi-cmp-msg.err .siyi-cmp-bubble{background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-error)}
.siyi-cmp-insert{align-self:flex-start;font-size:12px;padding:3px 9px;border-radius:8px;border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary);cursor:pointer}
.siyi-cmp-card{box-sizing:border-box;width:100%;border-radius:var(--dsw-radius-panel);background:var(--dsw-specific-input-major);box-shadow:var(--dsw-elevation-soft);font-size:var(--dsh-content-font-size,14px);display:flex;flex-direction:column;gap:12px;padding-top:8px;position:relative}
.siyi-cmp-editor{box-sizing:border-box;width:100%;border:none;outline:none;background:transparent;color:var(--dsw-alias-label-primary);font-family:inherit;font-size:var(--dsh-content-font-size,14px);line-height:calc(24px + var(--dsh-content-font-delta,0px));padding:0 14px;resize:none;max-height:160px;overflow-y:auto;-webkit-overflow-scrolling:touch}
.siyi-cmp-editor::placeholder{color:var(--dsw-alias-label-tertiary)}
.siyi-cmp-row{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px;min-width:0;padding:2px 8px 6px}
.siyi-cmp-tools{display:flex;align-items:center;gap:8px;min-width:0}
.siyi-cmp-add{corner-shape:round;background:var(--dsw-specific-selector);width:28px;height:28px;color:var(--dsw-alias-label-primary);cursor:pointer;border:none;border-radius:999px;flex:none;display:grid;place-items:center;transition:background .12s}




/* SIYI-MORESMALL-20261120：更多浮层——底部紧凑卡片 */
/* SIYI-MORETIGHT-20261120：紧贴底栏按钮上方 */
/* SIYI-MORERIGHT-20261120：靠右 + 变窄 */
.siyi-more-scrim{align-items:flex-end!important;justify-content:flex-end!important;padding:0 8px 92px!important;background:transparent}
.siyi-more-panel{min-width:150px!important;max-width:min(180px,calc(100vw - 24px))!important;border-radius:12px!important;padding:3px!important}
.siyi-more-list{padding:0!important;gap:0!important;width:auto!important;min-width:0!important}
.siyi-more-item{min-height:0!important;height:auto!important;padding:7px 10px!important;gap:8px!important;font-size:12px!important;line-height:1.3}
.siyi-more-item svg{width:15px;height:15px}
/* SIYI-MOREUI-20261120：更多弹窗——干净列表 */
.siyi-more-list{display:flex;flex-direction:column;gap:2px;min-width:220px;padding:4px}
.siyi-more-item{display:flex;align-items:center;gap:14px;box-sizing:border-box;width:100%;min-height:48px;padding:0 14px;border:none;border-radius:10px;background:transparent;color:var(--dsw-alias-label-primary);font-size:14px;white-space:nowrap;text-align:left;cursor:pointer;touch-action:manipulation}
.siyi-more-item span{flex:1;min-width:0}
.siyi-more-item:active{background:var(--dsw-alias-interactive-bg-hover)}
.siyi-more-item.danger{color:var(--dsw-alias-label-error)}
/* SIYI-AX-20261120：尊重系统"减少动态效果"设置 */
@media (prefers-reduced-motion: reduce){
  .siyi-notes-root *{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important;scroll-behavior:auto!important}
}
/* SIYI-AX-20261120：键盘焦点可见（WCAG 2.4.7） */
.siyi-notes-root button:focus-visible,
.siyi-notes-root [role="button"]:focus-visible,
.siyi-notes-root input:focus-visible,
.siyi-notes-root select:focus-visible{outline:2px solid var(--dsw-alias-state-business-primary);outline-offset:2px}
/* SIYI-MODELICON-20261120：模型按钮——照主对话 trigger */
.siyi-cmp-model-icon{flex:none;color:var(--dsw-alias-label-secondary)}
.siyi-cmp-model-name{flex:1;min-width:0;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-cmp-model-chevron{flex:none;transition:transform .15s ease;color:var(--dsw-alias-label-tertiary)}
/* SIYI-HIDEMODELNAME-20261120：模型按钮只留图标+箭头 */
.siyi-cmp-model{min-width:0;padding:0 8px}
.siyi-cmp-model{display:inline-flex;align-items:center;gap:5px;min-height:28px;padding:0 10px;border-radius:999px;border:none;background:transparent;color:var(--dsw-alias-label-primary);font-size:13px;cursor:pointer}
.siyi-cmp-model:active{background:var(--dsw-alias-interactive-bg-hover)}
.siyi-cmp-trailing{display:flex;align-items:center;gap:6px;min-width:0}
.siyi-cmp-iconbtn{width:32px;height:32px;border:none;border-radius:999px;background:transparent;color:var(--dsw-alias-label-secondary);display:grid;place-items:center;cursor:pointer}
.siyi-cmp-iconbtn:active{background:var(--dsw-alias-interactive-bg-hover)}
.siyi-cmp-send{width:34px;height:34px;border:none;border-radius:999px;background:var(--dsw-alias-state-business-primary);color:#fff;display:grid;place-items:center;cursor:pointer;flex:none;transition:opacity .12s}
.siyi-cmp-send.off{opacity:.45;cursor:not-allowed}

/* SIYI-EDITCSS-20261120：选区改写区 */

/* SIYI-MERGEROW-20261120：展开区占满新行 */
/* SIYI-CHIPLINE-20261120：展开区纵向 */
.siyi-cmp-editrow,.siyi-cmp-diff{width:100%}
/* SIYI-CHIPLINE-20261120：chip 行——明确单行，不换行 */
.siyi-cmp-chipline{display:flex;flex-direction:row;flex-wrap:nowrap;align-items:center;gap:6px;overflow-x:auto;overflow-y:hidden;padding:4px 2px 0;-webkit-overflow-scrolling:touch}
.siyi-cmp-editzone{display:flex;flex-direction:column;flex-wrap:nowrap;align-items:center;gap:6px;overflow-x:auto;padding:4px 2px 0}
.siyi-cmp-editgrab{display:inline-flex;align-items:center;gap:5px;align-self:flex-start;white-space:nowrap;font-size:12px;padding:4px 10px;border-radius:999px;border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary);cursor:pointer}
.siyi-cmp-editsel{font-size:11px;color:var(--dsw-alias-label-tertiary);padding:0 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-cmp-editrow{display:flex;gap:6px;align-items:center}
.siyi-cmp-editinput{flex:1;min-width:0;height:32px;padding:0 10px;border-radius:10px;border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary);font-size:12px;outline:none;-webkit-appearance:none}
.siyi-cmp-editgo,.siyi-cmp-editcancel{flex:none;font-size:12px;padding:5px 12px;border-radius:8px;cursor:pointer}
.siyi-cmp-editgo{border:none;background:var(--dsw-alias-state-business-primary);color:#fff}
.siyi-cmp-editgo:disabled{opacity:.45;cursor:not-allowed}
.siyi-cmp-editcancel{border:.5px solid var(--dsw-alias-border-l3);background:transparent;color:var(--dsw-alias-label-secondary)}
.siyi-cmp-diff{display:flex;flex-direction:column;gap:4px;padding:8px;border-radius:10px;background:var(--dsw-alias-bg-layer-2)}
.siyi-cmp-diffrow{font-size:12px;line-height:1.5;overflow-wrap:break-word;white-space:pre-wrap}
.siyi-cmp-diffrow.old{color:var(--dsw-alias-label-error)}
.siyi-cmp-diffrow.new{color:var(--dsw-alias-state-success-primary,var(--dsw-alias-label-primary))}
.siyi-cmp-reason{font-size:11px;color:var(--dsw-alias-label-tertiary);margin-top:2px}

/* SIYI-MODELUI-20261120：模型选择弹窗——对齐官方 ModelSelect */

/* SIYI-MODELPOP-CSS-20261120：模型选择浮层——照主对话 popover */

/* SIYI-POPFIT-20261120：改写选中移到快捷指令下方，不动 JSX 结构 */
.siyi-cmp-root>.siyi-cmp-root>.siyi-cmp-card{order:0}
.siyi-cmp-root>.siyi-cmp-log{order:0}
.siyi-pop-scrim{position:fixed;inset:0;z-index:50;background:transparent;display:flex;align-items:flex-end;justify-content:center}
.siyi-pop-panel{box-sizing:border-box;width:min(420px,calc(100vw - 32px));max-height:min(360px,100vh - 96px);margin-bottom:8px;
  display:flex;flex-direction:column;border-radius:16px;background:var(--dsw-specific-input-major,#fff);
  box-shadow:var(--dsw-elevation-soft,0 8px 32px rgba(0,0,0,.16));overflow:hidden;padding-bottom:6px}
.siyi-pop-search{box-sizing:border-box;flex:none;width:calc(100% - 24px);height:40px;margin:12px 12px 4px;padding:0 14px;
  border-radius:10px;border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);
  color:var(--dsw-alias-label-primary);font-size:14px;outline:none;-webkit-appearance:none}
.siyi-pop-search:focus{border-color:var(--dsw-alias-border-l1)}
.siyi-pop-list{flex:1;min-height:0;max-height:min(280px,100vh - 220px);overflow-y:auto;padding:4px 8px;-webkit-overflow-scrolling:touch}
.siyi-pop-group{margin:6px 0 10px}
.siyi-pop-groupname{font-size:12px;color:var(--dsw-alias-label-tertiary);padding:6px 10px}
.siyi-pop-item{box-sizing:border-box;width:100%;min-height:40px;display:flex;align-items:center;padding:6px 10px;
  border:none;background:transparent;color:var(--dsw-alias-label-primary);font-size:14px;text-align:left;
  cursor:pointer;border-radius:8px;touch-action:manipulation}
.siyi-pop-item:active{background:var(--dsw-alias-interactive-bg-hover)}
.siyi-pop-item.on{color:var(--dsw-alias-state-business-primary)}
.siyi-pop-current{flex:none;display:flex;align-items:center;gap:10px;margin:6px 8px 0;padding:10px 12px;
  border-radius:10px;background:var(--dsw-alias-bg-layer-2);font-size:14px;color:var(--dsw-alias-label-primary)}
.siyi-pop-current-val{flex:1;min-width:0;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-pop-current-check{color:var(--dsw-alias-state-business-primary);display:grid;place-items:center}
.siyi-model-search{box-sizing:border-box;width:100%;height:36px;padding:0 12px;margin-bottom:10px;border-radius:10px;border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary);font-size:13px;outline:none;-webkit-appearance:none}
.siyi-model-search:focus{border-color:var(--dsw-alias-border-l1)}
.siyi-model-list{max-height:46vh;overflow-y:auto;margin:0 -8px;padding:0 4px;-webkit-overflow-scrolling:touch}
.siyi-model-group{margin-bottom:10px}
.siyi-model-groupname{font-size:11px;color:var(--dsw-alias-label-tertiary);padding:6px 10px 4px}
.siyi-model-item{box-sizing:border-box;width:100%;min-height:38px;display:flex;align-items:center;gap:10px;padding:6px 12px;border:none;border-radius:10px;background:transparent;color:var(--dsw-alias-label-primary);font-size:13px;text-align:left;cursor:pointer;touch-action:manipulation}
.siyi-model-item:hover,.siyi-model-item:active{background:var(--dsw-alias-interactive-bg-hover)}
.siyi-model-item.on{background:var(--dsw-alias-interactive-bg-selected,var(--dsw-alias-bg-layer-3))}
.siyi-model-check{width:18px;flex:none;display:grid;place-items:center;color:var(--dsw-alias-state-business-primary)}
.siyi-model-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-cmp-search{box-sizing:border-box;width:100%;height:34px;padding:0 12px;margin-bottom:8px;border-radius:10px;border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary);font-size:13px;outline:none;-webkit-appearance:none}
.siyi-cmp-search:focus{border-color:var(--dsw-alias-border-l1)}
.siyi-cmp-quick{display:flex;flex-wrap:nowrap;gap:6px;overflow-x:auto;overflow-y:hidden;padding:2px;flex:none;-webkit-overflow-scrolling:touch}
/* SIYI-QUICKCOMPACT-20261120：合并 order 定义，避免两条规则互相覆盖 */
.siyi-cmp-chip{flex:none;white-space:nowrap;font-size:12px;padding:4px 10px;border-radius:999px;border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary);cursor:pointer;touch-action:manipulation}
.siyi-cmp-chip:active{background:var(--dsw-alias-interactive-bg-hover)}

/* 图片：窄屏防撑破，阅读态/编辑态共用 */
.siyi-notes-body-input img{max-width:100%;height:auto;display:block;margin:8px 0;
  border-radius:8px}
/* ── AI 助手面板 ────────────────────────────────────────────────────
   半屏抽屉：高度 <= 50vh、可滚动、输入区避开软键盘与手势条。
   沿用手机端约定：触控 >=36px、touch-action:manipulation、
   overscroll-behavior:contain、色值全部走 --dsw-alias-*。          */
.siyi-notes-ai{display:flex;flex-direction:column;flex:none;
  max-height:50vh;height:50vh;min-height:0;
  border-top:.5px solid var(--dsw-alias-border-l3);
  background:var(--dsw-alias-bg-layer-2);
  overscroll-behavior:contain}
.siyi-notes-ai-head{display:flex;align-items:center;gap:8px;flex:none;
  padding:6px 10px;border-bottom:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-ai-title{font-size:13px;font-weight:600;color:var(--dsw-alias-label-primary);
  flex:1;min-width:0}
.siyi-notes-ai-quote{display:inline-flex;align-items:center;gap:4px;
  min-height:36px;padding:0 10px;border-radius:10px;cursor:pointer;flex:none;
  border:.5px solid var(--dsw-alias-border-l3);
  background:var(--dsw-alias-bg-layer-1);
  color:var(--dsw-alias-label-secondary);font-size:12px;
  touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.siyi-notes-ai-quote.on{background:var(--dsw-alias-bg-layer-3);
  color:var(--dsw-alias-label-primary);font-weight:600}
.siyi-notes-ai-close{min-height:36px;min-width:36px;padding:0 10px;border-radius:10px;
  cursor:pointer;flex:none;border:.5px solid var(--dsw-alias-border-l3);
  background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-secondary);
  font-size:16px;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.siyi-notes-ai-list{flex:1;min-height:0;overflow-y:auto;padding:8px 10px;
  overscroll-behavior:contain;-webkit-overflow-scrolling:touch;
  display:flex;flex-direction:column;gap:8px}
.siyi-notes-ai-msg{max-width:88%;padding:7px 10px;border-radius:12px;font-size:13px;
  line-height:1.6;white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere}
.siyi-notes-ai-msg.me{align-self:flex-end;
  background:var(--dsw-alias-bg-layer-3);color:var(--dsw-alias-label-primary)}
.siyi-notes-ai-msg.ai{align-self:flex-start;
  background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);
  border:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-ai-msg.err{align-self:stretch;color:var(--dsw-alias-label-warning,var(--dsw-alias-label-secondary));
  background:var(--dsw-alias-bg-layer-1);border:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-ai-insert{display:inline-flex;align-items:center;justify-content:center;
  min-height:36px;margin-top:6px;padding:0 12px;border-radius:10px;cursor:pointer;
  border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);
  color:var(--dsw-alias-label-primary);font-size:12px;
  touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.siyi-notes-ai-quick{display:flex;gap:6px;flex:none;overflow-x:auto;padding:4px 10px;
  border-top:.5px solid var(--dsw-alias-border-l3);scrollbar-width:none;
  overscroll-behavior-x:contain}
.siyi-notes-ai-quick::-webkit-scrollbar{display:none}
.siyi-notes-ai-quick button{flex:none;min-height:36px;padding:0 10px;border-radius:10px;
  cursor:pointer;border:.5px solid var(--dsw-alias-border-l3);
  background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-secondary);
  font-size:12px;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.siyi-notes-ai-inputbar{display:flex;align-items:flex-end;gap:8px;flex:none;
  padding:6px 10px calc(6px + env(safe-area-inset-bottom, 0px));
  border-top:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-ai-input{flex:1;min-width:0;min-height:40px;max-height:120px;
  padding:8px 10px;border-radius:10px;resize:none;
  border:.5px solid var(--dsw-alias-border-l3);
  background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);
  font-size:14px;line-height:1.5;font-family:inherit;outline:none;
  overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
.siyi-notes-ai-input:focus{border-color:var(--dsw-alias-border-l1)}
.siyi-notes-ai-send{flex:none;min-width:56px;min-height:40px;border-radius:10px;
  cursor:pointer;border:none;background:var(--dsw-alias-button-info-fill,var(--dsw-alias-bg-layer-3));
  color:var(--dsw-alias-label-primary);font-size:14px;
  touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.siyi-notes-ai-send:disabled{opacity:.5;cursor:default}

/* SIYI-MENUFINAL-20261120：所有弹出菜单——按文字长度自适应宽高，紧凑 */
.siyi-simple-menu-panel,.siyi-more-panel{width:max-content!important;min-width:0!important;max-width:min(260px,calc(100vw - 24px))!important;border-radius:10px!important;padding:3px!important}
.siyi-simple-menu-item,.siyi-more-item{box-sizing:border-box!important;width:100%!important;min-height:0!important;height:auto!important;padding:4px 8px!important;gap:7px!important;font-size:13px!important;line-height:1.3!important;white-space:nowrap!important}
.siyi-simple-menu-item svg,.siyi-more-item svg{width:15px!important;height:15px!important}
.siyi-simple-menu:not(.siyi-more-scrim){justify-content:flex-end!important;padding:50px 10px 0!important}
.siyi-more-scrim{justify-content:flex-end!important;align-items:flex-end!important;padding:0 8px 92px!important}


/* SIYI-POPTUNE-20261120：模型浮层紧凑数值（不动定位） */
.siyi-pop-search{height:32px;margin:6px 8px 2px;padding:0 10px;font-size:13px}
.siyi-pop-item{min-height:0;padding:5px 8px;font-size:13px;line-height:1.3}
.siyi-pop-group{margin:4px 0 6px}
.siyi-pop-groupname{font-size:11px;padding:4px 8px}
.siyi-pop-current{margin:4px 6px 0;padding:6px 10px;font-size:13px;gap:8px}


/* SIYI-POPAUTO-20261120：模型浮层适配文字长宽（与菜单一致） */
.siyi-pop-panel{width:max-content;max-width:260px;max-height:300px;border-radius:12px;padding:3px}
.siyi-pop-list{max-height:210px;padding:2px 6px}
.siyi-pop-search{width:calc(100% - 16px);margin:5px 8px 2px;height:30px;font-size:12px}
.siyi-pop-item{gap:8px;font-size:12px;line-height:1.25;white-space:nowrap}
.siyi-pop-groupname{font-size:11px}
.siyi-pop-current{font-size:12px;line-height:1.25}


/* SIYI-T2AUI-20261123 */
.siyi-task-alarm-btn{margin-left:auto;min-width:30px;height:30px;border:none;border-radius:8px;background:transparent;color:var(--dsw-alias-label-secondary);display:grid;place-items:center;cursor:pointer;touch-action:manipulation}
`

    function ensureCss() {
      const id = 'siyi-notes-css'
      if (document.getElementById(id)) return
      const el = document.createElement('style')
      el.id = id
      el.textContent = CSS
      document.head.appendChild(el)
    }

    // ── 小组件 ──────────────────────────────────────────────────────
    function fmtTime(iso) {
      if (!iso) return ''
      const d = new Date(iso)
      if (isNaN(d)) return String(iso)
      const now = new Date()
      const sameDay = d.toDateString() === now.toDateString()
      const pad = (n) => String(n).padStart(2, '0')
      if (sameDay) return pad(d.getHours()) + ':' + pad(d.getMinutes())
      if (d.getFullYear() === now.getFullYear()) return (d.getMonth() + 1) + '月' + d.getDate() + '日'
      return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
    }

    function kindLabel(k) {
      return ({ note: '笔记', source: '资料', task: '待办', daily: '日记', moc: '索引' })[k] || k
    }

    // ── D2 列表 ─────────────────────────────────────────────────────
    function ListView({ onOpen, onGraph, onMonth, onTasks, onNew, folder: folderProp, setFolder: setFolderProp, toast }) {
      const [items, setItems] = useState([])
      const [loading, setLoading] = useState(true)
      const [q, setQ] = useState('')
      const [kind, setKind] = useState('')
      // SIYI-SORT-20261120：排序方式（前端排序，服务端仍按更新时间返回）
      const [sortBy, setSortBy] = useState('updated-desc')
      // SIYI-LONGPRESS-20261120：长按弹出的操作菜单
      const [menuFor, setMenuFor] = useState(null)   // {id, title}
      const [moreOpen, setMoreOpen] = useState(false)   // SIYI-SIMPLE-20261120：顶栏更多菜单
      // SIYI-K1UI-20261123：知识收件箱
      const [inboxOpen, setInboxOpen] = useState(false)
      const [inboxItems, setInboxItems] = useState([])
      const [sortOpen, setSortOpen] = useState(false)   // SIYI-SORTBTN-20261120：排序菜单
      const [listVer, setListVer] = useState(0)      // 操作后刷新列表
      const [stats, setStats] = useState(null)
      // 文件夹栏状态。
      // folder 用 '' 表示「全部」，'__none__' 表示未归类（服务端用这个特殊值区分，
      // 因为空串在服务端表示「不过滤」）。
      const folder = folderProp || ''   // SIYI-FOLDER-20261126：文件夹状态提升到 NotesApp，新建笔记可继承
      const setFolder = setFolderProp || (() => {})
      const [folders, setFolders] = useState([])
      const [unfiled, setUnfiled] = useState(0)
      const [newFolder, setNewFolder] = useState(null)   // 非 null 时显示新建输入框

      const load = useCallback(async () => {
        setLoading(true)
        try {
          const p = {}
          if (q.trim()) p.q = q.trim()
          if (kind) p.kind = kind
          if (folder) p.folder = folder
          if (kind === 'task') {
            const t = await getTasks()
            setItems((t.items || []).map(x => ({ ...x, updated: x.updated || x.due })))
          } else {
            const r = await listNotes(p)
            setItems(r.items || [])
          }
          const s = await getStats()
          setStats(s)
          // 文件夹列表独立取，不受当前筛选影响（否则选中某个文件夹后，
          // 栏里就只剩它自己，没法切换到别的）。
          try {
            const f = await fetch('/siyi-vault/folders').then(r => r.json())
            if (f && f.ok) { setFolders(f.folders || []); setUnfiled(f.unfiled || 0) }
          } catch {}
        } catch (e) {
          toast('读取失败：' + e.message)
          setItems([])
        } finally { setLoading(false) }
      }, [q, kind, folder])

      useEffect(() => { load() }, [load])
      useEffect(() => { if (listVer) load() }, [listVer])   // SIYI-LISTMENU-20261120

      // SIYI-SORT-20261120：前端排序
      const sorted = useMemo(() => {
        const arr = (items || []).slice()
        const t = (v) => String(v || '')
        if (sortBy === 'updated-asc') arr.sort((a, b) => t(a.updated).localeCompare(t(b.updated)))
        else if (sortBy === 'created-desc') arr.sort((a, b) => t(b.created).localeCompare(t(a.created)))
        else if (sortBy === 'title-asc') arr.sort((a, b) => t(a.title).localeCompare(t(b.title), 'zh'))
        else arr.sort((a, b) => t(b.updated).localeCompare(t(a.updated)))
        return arr
      }, [items, sortBy])

      // 新建文件夹：调服务端接口，成功后刷新列表。
      const createFolder = async (name) => {
        const n = String(name || '').trim()
        if (!n) { setNewFolder(null); return }
        try {
          const r = await fetch('/siyi-vault/folder-create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: n }),
          }).then(r => r.json())
          if (!r || !r.ok) { toast('创建失败：' + ((r && r.error) || '未知错误')); return }
          toast(r.created ? ('已创建文件夹「' + n + '」') : ('文件夹「' + n + '」已存在'))
          setNewFolder(null)
          await load()
          if (r.created) setFolder(n)
        } catch (e) {
          toast('创建失败：' + e.message)
        }
      }

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-bar' },
          // SIYI-OINPUT-20261119：官方 Input（带搜索图标）
          OInput
            ? h(OInput, {
                icon: I.search ? h(I.search, { size: 16 }) : null,
                className: 'siyi-notes-search-wrap',
                placeholder: '搜索标题、正文或标签…',
                value: q,
                onChange: (e) => setQ(e.target.value),
              })
            : h('input', {
                className: 'siyi-notes-search', placeholder: '搜索标题、正文或标签…',
                value: q, onChange: (e) => setQ(e.target.value),
              }),
          // SIYI-SORTBTN-20261120：排序按钮（搜索右、更多左）
          h('button', {
            className: 'siyi-notes-btn siyi-btn-icon',
            title: '排序', 'aria-label': '排序',
            onClick: () => { setSortOpen(v => !v); setMoreOpen(false) },
          }, I.refresh ? h(I.refresh, { size: 18 }) : '⇅'),
          // SIYI-SIMPLE-20261120：顶栏更多菜单入口
          h('button', {
            className: 'siyi-notes-btn siyi-btn-icon',
            title: '更多功能', 'aria-label': '更多功能',
            onClick: () => { setMoreOpen(v => !v); setSortOpen(false) },
          }, I.more ? h(I.more, { size: 18 }) : '⋯'),
        ),
        // SIYI-LISTIA-20261119：低频功能移到第二行，不再挤占搜索框
        h('div', { className: 'siyi-notes-tools' },
          // SIYI-TODOINMORE-20261120：待办功能已全部收进顶栏 ⋯ 菜单
          // SIYI-SIMPLE-20261120：日历/待办/图谱已收进顶栏 ⋯ 菜单
          // SIYI-SORTBTN-20261120：类型/排序已收进顶栏菜单与排序按钮
                  ),
        // 文件夹栏：替代原来的类型栏（全部/笔记/待办/资料/日记/索引）。
        // 数据来自笔记 frontmatter 的 folder 字段，vault 已有 60 篇笔记、
        // 53 篇带 folder（现有「资讯」「知识」两个真实文件夹）。
        h('div', { className: 'siyi-notes-tabs' },
          h('button', {
            className: 'siyi-notes-btn' + (folder === '' ? ' on' : ''),
            onClick: () => setFolder(''),
          }, '全部'),
          folders.map(f => (OPill
            ? h(OPill, { key: f.name, active: folder === f.name, onClick: () => setFolder(f.name) }, f.name + ' ' + f.count)
            : h('button', {
                key: f.name,
                className: 'siyi-notes-btn' + (folder === f.name ? ' on' : ''),
                onClick: () => setFolder(f.name),
              }, f.name, h('span', { className: 'siyi-notes-folder-count' }, String(f.count))))),
          unfiled > 0 ? h('button', {
            className: 'siyi-notes-btn' + (folder === '__none__' ? ' on' : ''),
            onClick: () => setFolder('__none__'),
          }, '未归类', h('span', { className: 'siyi-notes-folder-count' }, String(unfiled))) : null,
          // 最右侧：创建文件夹。
          newFolder === null
            ? h('button', {
                className: 'siyi-notes-btn siyi-notes-folder-add',
                onClick: () => setNewFolder(''),
                title: '新建文件夹',
              }, I.folder ? h(I.folder, { size: 16 }) : '＋')
            : h('span', { className: 'siyi-notes-folder-new' },
                h('input', {
                  className: 'siyi-notes-folder-input',
                  placeholder: '文件夹名',
                  value: newFolder,
                  autoFocus: true,
                  onChange: (e) => setNewFolder(e.target.value),
                  onKeyDown: (e) => {
                    if (e.key === 'Enter') { e.preventDefault(); createFolder(newFolder) }
                    else if (e.key === 'Escape') { e.preventDefault(); setNewFolder(null) }
                  },
                }),
                h('button', {
                  className: 'siyi-notes-btn',
                  onClick: () => createFolder(newFolder),
                }, '确定'),
                h('button', {
                  className: 'siyi-notes-btn',
                  onClick: () => setNewFolder(null),
                }, '取消'),
              ),
          // SIYI-SIMPLE-20261120：统计数已移入 ⋯ 菜单
        ),
        h('div', { className: 'siyi-notes-list' },
          loading ? h('div', { className: 'siyi-notes-empty' }, '读取中…')
            : sorted.length === 0 ? h('div', { className: 'siyi-notes-empty' }, q ? '没有匹配的笔记' : '还没有笔记，点「新建」开始')
              : sorted.map(it => {
                  // SIYI-LONGPRESS-20261120：长按 500ms 弹操作菜单，短按打开
                  return h('div', {
                    key: it.id, className: 'siyi-notes-item',
                    // SIYI-SIMPLE1-20261120-ITEM：无障碍语义 + 键盘可达
                    role: 'button', tabIndex: 0,
                    'aria-label': it.title || '(无标题)',
                    onKeyDown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(it.id) } },
                    onContextMenu: (e) => { e.preventDefault(); setMenuFor({ id: it.id, title: it.title || '(无标题)' }) },
                    onPointerDown: (e) => {
                      if (e.pointerType === 'mouse') return
                      const t = setTimeout(() => {
                        setMenuFor({ id: it.id, title: it.title || '(无标题)' })
                      }, 500)
                      e.currentTarget.__lp = t
                    },
                    onPointerUp: (e) => { if (e.currentTarget.__lp) clearTimeout(e.currentTarget.__lp) },
                    onPointerLeave: (e) => { if (e.currentTarget.__lp) clearTimeout(e.currentTarget.__lp) },
                    onPointerCancel: (e) => { if (e.currentTarget.__lp) clearTimeout(e.currentTarget.__lp) },
                    onClick: () => { if (!menuFor) onOpen(it.id) },
                  },
                  h('div', { className: 'siyi-notes-item-title', title: it.title || '' }, it.title || '(无标题)'),
                  h('div', { className: 'siyi-notes-item-meta' },
                    // SIYI-SIMPLE1-20261120-ITEM：类型已移除，保留色点语义由 title 承载
                    it.due ? h('span', { style: { color: 'var(--dsw-alias-label-warning, #d97706)' } }, '截止 ' + it.due) : null,
                    h('span', null, fmtTime(it.updated)),
                    (it.tags || []).slice(0, 1).map(t => h('span', { key: t, className: 'siyi-notes-tag' }, '#' + t)),
                  ),
                  // SIYI-SIMPLE1-20261120-ITEM：长按的无障碍等价入口
                  h('button', {
                    className: 'siyi-notes-item-more',
                    'aria-label': '更多操作',
                    onClick: (e) => { e.stopPropagation(); setMenuFor({ id: it.id, title: it.title || '(无标题)' }) },
                  }, I.more ? h(I.more, { size: 16 }) : '⋯'),
                  )
                }),
        ),
        // SIYI-SIMPLE-20261120：顶栏 ⋯ 菜单（含统计/日历/待办/图谱）
        moreOpen ? h('div', { className: 'siyi-simple-menu', onClick: () => setMoreOpen(false) },
          h('div', { className: 'siyi-simple-menu-panel', onClick: (e) => e.stopPropagation() },
            h('div', { className: 'siyi-simple-menu-total' }, '共 ' + (stats ? stats.total : 0) + ' 篇'),
            // SIYI-MENUICONS-20261120：图标 + 文字，对齐主对话菜单
            h('button', { className: 'siyi-simple-menu-item', onClick: () => { setMoreOpen(false); onTasks() } }, I.checklist ? h(I.checklist,{size:16}) : null, h('span',null,'待办清单')),
            h('button', { className: 'siyi-simple-menu-item', onClick: async () => {
              setMoreOpen(false)
              try { const r = await api('/inbox-list'); setInboxItems(r.items || []); setInboxOpen(true) }
              catch (e) { toast('收件箱读取失败：' + e.message) }
            } }, I.data ? h(I.data,{size:16}) : null, h('span',null,'知识收件箱')),   // SIYI-K1UI-20261123
            h('button', { className: 'siyi-simple-menu-item', onClick: () => { setMoreOpen(false); onGraph() } }, I.graph ? h(I.graph,{size:16}) : null, h('span',null,'知识图谱')),
            h('div', { className: 'siyi-simple-menu-total' }, '类型筛选'),
            h('button', { className: 'siyi-simple-menu-item' + (kind === '' ? ' on' : ''), onClick: () => { setKind(''); setMoreOpen(false) } }, I.list ? h(I.list,{size:16}) : null, h('span',null,'全部类型'), kind === '' && I.check ? h('span',{className:'siyi-simple-menu-check'},h(I.check,{size:15})) : null),
            h('button', { className: 'siyi-simple-menu-item' + (kind === 'note' ? ' on' : ''), onClick: () => { setKind('note'); setMoreOpen(false) } }, I.edit ? h(I.edit,{size:16}) : null, h('span',null,'笔记'), kind === 'note' && I.check ? h('span',{className:'siyi-simple-menu-check'},h(I.check,{size:15})) : null),
            h('button', { className: 'siyi-simple-menu-item' + (kind === 'task' ? ' on' : ''), onClick: () => { setKind('task'); setMoreOpen(false) } }, I.checklist ? h(I.checklist,{size:16}) : null, h('span',null,'待办'), kind === 'task' && I.check ? h('span',{className:'siyi-simple-menu-check'},h(I.check,{size:15})) : null),
            h('button', { className: 'siyi-simple-menu-item' + (kind === 'source' ? ' on' : ''), onClick: () => { setKind('source'); setMoreOpen(false) } }, I.data ? h(I.data,{size:16}) : null, h('span',null,'资料'), kind === 'source' && I.check ? h('span',{className:'siyi-simple-menu-check'},h(I.check,{size:15})) : null),
            h('button', { className: 'siyi-simple-menu-item' + (kind === 'daily' ? ' on' : ''), onClick: () => { setKind('daily'); setMoreOpen(false) } }, I.calendar ? h(I.calendar,{size:16}) : null, h('span',null,'日记'), kind === 'daily' && I.check ? h('span',{className:'siyi-simple-menu-check'},h(I.check,{size:15})) : null),
          )) : null,
        // SIYI-SORTBTN-20261120：排序菜单（含按字母排序）
        sortOpen ? h('div', { className: 'siyi-simple-menu', onClick: () => setSortOpen(false) },
          h('div', { className: 'siyi-simple-menu-panel', onClick: (e) => e.stopPropagation() },
            [
              ['updated-desc', '最近更新', 'clock'],
              ['updated-asc', '最早更新', 'clock'],
              ['created-desc', '最近创建', 'refresh'],
              ['title-asc', '按字母排序', 'text'],
            ].map(([v, label, icon]) => h('button', {
              key: v,
              className: 'siyi-simple-menu-item' + (sortBy === v ? ' on' : ''),
              onClick: () => { setSortBy(v); setSortOpen(false) },
            },
              icon === 'clock' && I.clock ? h(I.clock,{size:16}) : null,
              icon === 'text' && I.code ? h(I.code,{size:16}) : null,
              icon === 'refresh' && I.refresh ? h(I.refresh,{size:16}) : null,
              icon === 'sort' && I.compare ? h(I.compare,{size:16}) : null,
              h('span', null, label),
              sortBy === v && I.check ? h('span',{className:'siyi-simple-menu-check'},h(I.check,{size:15})) : null)),
          )) : null,
        // SIYI-LISTIA-20261119：唯一主按钮——浮动新建
        // SIYI-ICONLIST-20261119：浮动新建用官方 plus 图标
        h('button', { className: 'siyi-notes-fab', onClick: onNew, title: '新建笔记' },
          I.plus ? h(I.plus, { size: 26 }) : '+'),
        // SIYI-K1UI-20261123：知识收件箱浮层
        inboxOpen ? h('div', { className: 'siyi-simple-menu', onClick: () => setInboxOpen(false) },
          h('div', { className: 'siyi-simple-menu-panel', onClick: (e) => e.stopPropagation(), style: { maxHeight: '70vh', overflowY: 'auto' } },
            h('div', { className: 'siyi-simple-menu-total' }, '知识收件箱 · ' + inboxItems.length + ' 待审'),
            inboxItems.length === 0
              ? h('div', { style: { padding: 12, fontSize: 12, color: 'var(--dsw-alias-label-tertiary)' } }, '暂无待审候选')
              : inboxItems.map(it => h('div', { key: it.id, style: { padding: '8px 0', borderBottom: '.5px solid var(--dsw-alias-border-l3)' } },
                  h('div', { style: { fontSize: 12, fontWeight: 600 } }, it.title),
                  h('div', { style: { fontSize: 10, color: 'var(--dsw-alias-label-tertiary)', margin: '2px 0 6px' } }, (it.source && it.source.type) || '手动'),
                  h('div', { style: { display: 'flex', gap: 6 } },
                    h('button', { className: 'siyi-notes-btn', onClick: async () => {
                      try { const r = await api('/inbox-accept', { method: 'POST', body: JSON.stringify({ id: it.id }) }); if (r.ok) { toast('已入库'); setInboxItems(v => v.filter(x => x.id !== it.id)) } else toast(r.error || '采纳失败') }
                      catch (e) { toast('采纳失败：' + e.message) }
                    } }, '采纳'),
                    h('button', { className: 'siyi-notes-btn', onClick: async () => {
                      try { await api('/inbox-reject', { method: 'POST', body: JSON.stringify({ id: it.id }) }); setInboxItems(v => v.filter(x => x.id !== it.id)) }
                      catch (e) { toast('忽略失败：' + e.message) }
                    } }, '忽略'),
                  ),
                )),
          ))
         : null,
        // SIYI-LONGPRESS-20261120：长按列表项弹出的操作菜单
        menuFor
          ? (OModal
              ? h(OModal, {
                  open: true,
                  onClose: () => setMenuFor(null),
                  title: menuFor.title,
                  closeLabel: '关闭',
                }, h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(88px, 1fr))', gap: 8 } },
                  h(OButton, {
                    variant: 'outline', size: 'sm',
                    icon: I.edit ? h(I.edit, { size: 15 }) : null,
                    onClick: () => { const id = menuFor.id; setMenuFor(null); onOpen(id) },
                  }, '打开'),
                  h(OButton, {
                    variant: 'outline', size: 'sm',
                    icon: I.link ? h(I.link, { size: 15 }) : null,
                    onClick: async () => {
                      try { await navigator.clipboard.writeText('[[' + menuFor.title + ']]'); toast('已复制双链') }
                      catch { toast('复制失败') }
                      setMenuFor(null)
                    },
                  }, '复制双链'),
                  h(OButton, {
                    variant: 'outline', size: 'sm',
                    icon: I.trash ? h(I.trash, { size: 15 }) : null,
                    onClick: async () => {
                      const id = menuFor.id
                      if (!window.confirm('确定删除「' + menuFor.title + '」？会移到回收站。')) return
                      setMenuFor(null)
                      try {
                        const r = await deleteNote(id)
                        if (r && r.ok) { toast('已移到回收站'); setListVer(v => v + 1) }
                        else toast('删除失败：' + ((r && r.error) || '未知'))
                      } catch (e) { toast('删除失败：' + e.message) }
                    },
                  }, '删除')
                ))
              : null)
          : null,
      )
    }

    // ── D3 编辑器 ───────────────────────────────────────────────────
    //
    // 按手机端笔记（小米/红米笔记、Google Keep、Obsidian mobile）重新设计：
    //   1. 底部固定工具栏，图标 40px+ 触控区（Material 48dp / WCAG 24px 标准）
    //   2. 分类入口而不是 13 个字母全铺开 —— 参考红米笔记的
    //      「画笔/语音/图片/手写/待办/文字」分类式底栏
    //   3. 正文区独立滚动，不靠移动光标来翻页
    //   4. source 类型或含 HTML 的笔记默认进阅读态，不显示源码
    //   5. 阅读态对安全 HTML（a/div align/br）做白名单渲染
    //   6. 内嵌 AI 助手面板（半屏抽屉），走 /siyi-vault/chat 复用主 agent

    // AI 助手面板：独立组件，状态自管。onInsert(reply) 回传正文，由 EditorView 走
    // 已有的 insertRaw 光标处插入逻辑；noteText 由父级按需传入（引用当前笔记）。
    const AI_QUICK = ['润色这篇笔记', '总结要点', '帮我起个标题']
    // SIYI-COMPOSERCLONE-20261120：照官方 composer（对话输入框）结构复刻
    // 结构取自 dsh-client-ui-conversation 的 InputBar：
    //   root > notice? > card > [editor, row[tools(+,模型), trailing(右侧操作)]]
    // CSS 值也照抄官方 InputBar_module 的关键规则，保证视觉一致。
    // SIYI-AITOAST-SCOPE-20261120：AIPanel 在 NotesApp 外部，toast 必须作为参数传入，否则作用域未定义导致白屏
    function AIPanel({ noteText, onInsert, onClose, bodyRef, onApplyEdit, noteId, revision, toast }) {
      // SIYI-EDITUI-20261120：选区改写状态
      const [editSel, setEditSel] = useState('')
      const [editInstr, setEditInstr] = useState('')
      const [editProposal, setEditProposal] = useState(null)
      const [editBusy, setEditBusy] = useState(false)
      const [msgs, setMsgs] = useState([])
      const [input, setInput] = useState('')
      const [quote, setQuote] = useState(true)
      const [busy, setBusy] = useState(false)
      const listRef = useRef(null)
      const inputRef = useRef(null)
      // SIYI-AIREALMODELS-20261120：模型目录从服务端拉取，与主对话模型选择保持一致
      const [modelGroups, setModelGroups] = useState([])
      const [modelSel, setModelSel] = useState(null)   // {provider, model, name}
      const [modelMenuOpen, setModelMenuOpen] = useState(false)
      const [modelQuery, setModelQuery] = useState('')
      // SIYI-AIBUTTONS-20261120：中止当前请求
      const abortRef = useRef(null)

      useEffect(() => {
        let alive = true
        fetch('/siyi-vault/models').then(r => r.json()).then(j => {
          if (!alive || !j || !j.ok) return
          const groups = Array.isArray(j.groups) ? j.groups : []
          setModelGroups(groups)
          // 默认选服务端给的 default（已优先免费可用模型），否则第一个
          const pick = (prov, mid) => {
            const g = groups.find(x => x.id === prov)
            const m = g && g.models.find(x => x.id === mid)
            return m ? { provider: prov, model: mid, name: m.name || mid } : null
          }
          const d = j.default && j.default.provider ? pick(j.default.provider, j.default.model) : null
          if (d) setModelSel(d)
          else {
            for (const g of groups) {
              if (g.models.length) { setModelSel({ provider: g.id, model: g.models[0].id, name: g.models[0].name }); break }
            }
          }
        }).catch(() => {})
        return () => { alive = false }
      }, [])

      // 按查询过滤分组（官方同款：>4 个模型才显示搜索）
      const visibleGroups = useMemo(() => {
        const q = modelQuery.trim().toLowerCase()
        return modelGroups.map(g => ({
          ...g,
          models: q ? g.models.filter(m =>
            String(m.name || '').toLowerCase().includes(q) ||
            String(m.id || '').toLowerCase().includes(q)) : g.models,
        })).filter(g => g.models.length > 0)
      }, [modelGroups, modelQuery])
      const modelChoices = useMemo(
        () => visibleGroups.flatMap(g => g.models.map(m => ({ group: g, model: m }))),
        [visibleGroups]
      )

      useEffect(() => {
        const el = listRef.current
        if (el) el.scrollTop = el.scrollHeight
      }, [msgs])

      const send = useCallback(async (text) => {
        const q = String(text || '').trim()
        if (!q || busy) return
        setMsgs(m => [...m, { role: 'me', text: q }])
        setInput('')
        setBusy(true)
        try {
          const ctxText = quote && noteText && noteText.trim() ? noteText : ''
          const chosen = modelSel
          if (!chosen) throw new Error('模型目录尚未就绪')
          const ac = new AbortController()
          abortRef.current = ac
          const r = await chatWithAI(q, ctxText, { provider: chosen.provider, model: chosen.model }, ac.signal)
          if (!r || !r.ok) throw new Error((r && r.error) || 'AI 回复失败')
          setMsgs(m => [...m, { role: 'ai', text: String(r.reply || '') }])
        } catch (e) {
          setMsgs(m => [...m, { role: 'err', text: '请求失败：' + (e && e.message ? e.message : String(e)) }])
        } finally {
          abortRef.current = null
          setBusy(false)
          if (inputRef.current) try { inputRef.current.focus() } catch {}
        }
      }, [busy, quote, noteText, modelSel])

      // SIYI-EDITUI-20261120：读取正文选区
      const grabSelection = () => {
        const ta = bodyRef && bodyRef.current
        if (!ta) { toast('正文未就绪'); return }
        const sel = String(ta.value || '').slice(ta.selectionStart, ta.selectionEnd)
        if (!sel.trim()) { toast('请先在正文中选中要改写的文字'); return }
        setEditSel(sel)
        setEditProposal(null)
        setEditInstr('')
      }

      // SIYI-EDITUI-20261120：请求 AI 生成改稿提案
      const requestEdit = useCallback(async () => {
        if (!editInstr.trim() || !editSel || editBusy) return
        setEditBusy(true)
        try {
          const r = await fetch('/siyi-vault/chat-edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              noteId: noteId || '',
              revision: revision || '',
              selection: editSel,
              instruction: editInstr.trim(),
              body: String((bodyRef && bodyRef.current && bodyRef.current.value) || ''),
            }),
          }).then(x => x.json())
          if (!r || !r.ok) { toast('改稿失败：' + ((r && r.error) || '未知')); return }
          setEditProposal(r.proposal)
        } catch (e) { toast('改稿失败：' + e.message) }
        finally { setEditBusy(false) }
      }, [editInstr, editSel, editBusy, noteId, revision, bodyRef])

      // SIYI-EDITUI-20261120：应用改稿（写回服务端 + 更新编辑器）
      const applyEdit = useCallback(async () => {
        if (!editProposal) return
        setEditBusy(true)
        try {
          const r = await fetch('/siyi-vault/apply-edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: noteId, proposal: editProposal }),
          }).then(x => x.json())
          if (!r || !r.ok) {
            if (r && r.code === 'CONFLICT') toast('笔记已被修改，请重新生成改稿')
            else toast('应用失败：' + ((r && r.error) || '未知'))
            return
          }
          // 回写编辑器正文（用 proposal.replacement 替换选区）
          if (onApplyEdit) onApplyEdit(editProposal.selection, editProposal.replacement)
          toast('已应用改写')
          setEditProposal(null)
          setEditInstr('')
          setEditSel('')
        } catch (e) { toast('应用失败：' + e.message) }
        finally { setEditBusy(false) }
      }, [editProposal, noteId, onApplyEdit])

      const canSend = !!input.trim() && !busy
      // SIYI-AIREALMODELS-20261120：模型选择（官方 Modal + 分组列表，同主对话的结构）
      // SIYI-MODELUI-20261120：模型选择弹窗——照官方 ModelSelect 结构与语义
      // 原生元素 + 官方 CSS token，避免官方 Menu/Button 在独立面板里崩溃。
      // 官方用 role="menuitemradio" + aria-checked，左对齐行，灰色分组标题。
      // SIYI-MODELPOP-20261120：模型选择浮层（popover，锚在按钮上方）——照主对话的样式。
      // 用原生元素 + 官方 CSS token，不用官方 Menu（独立面板里会白屏）。
      const modelMenu = modelMenuOpen
        ? h('div', { className: 'siyi-pop-scrim', onClick: () => setModelMenuOpen(false) },
            h('div', { className: 'siyi-pop-panel', onClick: (e) => e.stopPropagation() },
              // 搜索框
              h('input', {
                className: 'siyi-pop-search',
                placeholder: '搜索模型…',
                value: modelQuery,
                autoFocus: true,
                onChange: (e) => setModelQuery(e.target.value),
              }),
              h('div', { className: 'siyi-pop-list', role: 'menu' },
                visibleGroups.map(g => h('div', { key: g.id, className: 'siyi-pop-group' },
                  h('div', { className: 'siyi-pop-groupname' }, g.name),
                  g.models.map(m => {
                    const on = modelSel && modelSel.provider === g.id && modelSel.model === m.id
                    return h('button', {
                      key: g.id + '/' + m.id,
                      type: 'button',
                      role: 'menuitemradio',
                      'aria-checked': on ? 'true' : 'false',
                      className: 'siyi-pop-item' + (on ? ' on' : ''),
                      onClick: () => {
                        setModelSel({ provider: g.id, model: m.id, name: m.name })
                        setModelMenuOpen(false)
                        setModelQuery('')
                      },
                    }, m.name || m.id)
                  })
                ))
              ),
              // 底部固定当前模型（照主对话底部栏）
              h('div', { className: 'siyi-pop-current' },
                h('span', null, '模型'),
                h('span', { className: 'siyi-pop-current-val' },
                  modelSel ? (modelSel.name || modelSel.model) : '加载模型…'),
                h('span', { className: 'siyi-pop-current-check' }, I.check ? h(I.check, { size: 16 }) : '✓'),
              ),
            ),
          )
        : null

      return h('div', { className: 'siyi-cmp-root' },
        modelMenu,
        // 对话记录（官方 composer 本身不含历史，这里单独放在卡片上方）
        msgs.length || busy
          ? h('div', { className: 'siyi-cmp-log', ref: listRef },
              msgs.map((m, i) => h('div', { key: i, className: 'siyi-cmp-msg ' + m.role },
                h('div', { className: 'siyi-cmp-bubble' }, m.text),
                m.role === 'ai' && m.text
                  ? h('button', {
                      className: 'siyi-cmp-insert',
                      onClick: () => onInsert(m.text),
                    }, '插入到笔记')
                  : null,
              )),
              busy
                ? h('div', { className: 'siyi-cmp-msg ai' },
                    h('div', { className: 'siyi-cmp-bubble' },
                      OShimmer ? h(OShimmer, { active: true }, '正在思考…') : '正在思考…'))
                : null,
            )
          : null,
        // 官方 composer 卡片
        h('div', { className: 'siyi-cmp-card' },
          h('textarea', {
            ref: inputRef,
            className: 'siyi-cmp-editor',
            rows: 1,
            placeholder: '发消息或创建任务，/ 调用指令，@ 文件或对话',
            value: input,
            onChange: (e) => {
              setInput(e.target.value)
              const el = e.target
              el.style.height = 'auto'
              el.style.height = Math.min(el.scrollHeight, 160) + 'px'
            },
            onKeyDown: (e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (canSend) send(input) }
            },
          }),
          h('div', { className: 'siyi-cmp-row' },
            h('div', { className: 'siyi-cmp-tools' },
              // 「+」按钮（对齐官方 add）
              h('button', {
                className: 'siyi-cmp-add',
                title: '引用当前笔记',
                onClick: () => setQuote(v => !v),
                style: quote ? { background: 'var(--dsw-alias-state-business-primary)', color: '#fff' } : {},
              }, I.plus ? h(I.plus, { size: 14 }) : '+'),
              // SIYI-MODELICON-20261120：与主对话模型按钮一致（左 IconData + 名称 + 下箭头）
              h('button', {
                className: 'siyi-cmp-model',
                title: '模型：' + (modelSel ? (modelSel.name || modelSel.model) : '加载中…') + '（仅本次）',
                onClick: () => setModelMenuOpen(v => !v),
              },
                I.data ? h(I.data, { size: 15, className: 'siyi-cmp-model-icon' }) : null,
                // SIYI-HIDEMODELNAME-20261120：隐藏名称，只留图标+箭头；完整模型名放 title
                I.chevronDown ? h(I.chevronDown, { size: 14, className: 'siyi-cmp-model-chevron' }) : h('span', null, '⌄'),
              ),
            ),
            h('div', { className: 'siyi-cmp-trailing' },
              // SIYI-AIBUTTONS-20261120：收起（对应官方关闭）
              h('button', {
                className: 'siyi-cmp-iconbtn',
                title: '收起 AI',
                onClick: onClose,
              }, I.close ? h(I.close, { size: 18 }) : '✕'),
              // SIYI-AIBUTTONS-20261120：停止（忙碌时显示，对齐官方 interruptible 停止按钮）
              busy
                ? h('button', {
                    className: 'siyi-cmp-iconbtn',
                    title: '停止生成',
                    onClick: () => { setMsgs(m => m); abortRef.current && abortRef.current() },
                  }, I.stop ? h(I.stop, { size: 18 }) : '■')
                : null,
              // 发送（官方圆形主色按钮）
              h('button', {
                className: 'siyi-cmp-send' + (canSend ? '' : ' off'),
                disabled: !canSend,
                onClick: () => send(input),
                title: '发送',
              }, busy
                  ? (I.loading ? h(I.loading, { size: 18 }) : '…')
                  : (I.send ? h(I.send, { size: 18 }) : '↑')),
            ),
          ),
        ),
        // SIYI-CHIPLINE-20261120：chip 行（改写选中作为第4个chip，必然单行）
        h('div', { className: 'siyi-cmp-chipline' },
          AI_QUICK.map(q => h('button', {
            key: q,
            className: 'siyi-cmp-chip',
            disabled: busy,
            onClick: () => send(q),
          }, q)),
          h('button', {
            key: '__edit__',
            className: 'siyi-cmp-chip',
            title: '先选中正文文字，再点这里',
            onClick: grabSelection,
          }, I.edit ? h(I.edit, { size: 14 }) : null, '改写选中'),
        ),
        // SIYI-CHIPLINE-20261120：展开区（仅在改写时显示，占独立一行）
        (editSel || editProposal) ? h('div', { className: 'siyi-cmp-editzone' },
          editSel ? h('div', { className: 'siyi-cmp-editsel' }, '已选：' + editSel.slice(0, 60) + (editSel.length > 60 ? '…' : '')) : null,
          editSel && !editProposal ? h('div', { className: 'siyi-cmp-editrow' },
            h('input', {
              className: 'siyi-cmp-editinput',
              placeholder: '要求：如「改得更简洁」「换成正式语气」',
              value: editInstr,
              onChange: (e) => setEditInstr(e.target.value),
            }),
            h('button', {
              className: 'siyi-cmp-editgo',
              disabled: !editInstr.trim() || editBusy,
              onClick: requestEdit,
            }, '生成'),
          ) : null,
          editProposal ? h('div', { className: 'siyi-cmp-diff' },
            h('div', { className: 'siyi-cmp-diffrow old' }, '− ' + editProposal.selection.slice(0, 100)),
            h('div', { className: 'siyi-cmp-diffrow new' }, '+ ' + editProposal.replacement.slice(0, 100)),
            editProposal.reason ? h('div', { className: 'siyi-cmp-reason' }, editProposal.reason) : null,
            h('div', { className: 'siyi-cmp-editrow' },
              h('button', { className: 'siyi-cmp-editgo', disabled: editBusy, onClick: applyEdit }, '应用'),
              h('button', { className: 'siyi-cmp-editcancel', onClick: () => { setEditProposal(null); setEditInstr('') } }, '拒绝'),
            ),
          ) : null,
        ) : null,
      )
    }

    function EditorView({ id, onBack, toast, folder: folderProp }) {
      const [note, setNote] = useState(null)
      const [title, setTitle] = useState('')
      const [body, setBody] = useState('')
      const [tags, setTags] = useState('')
      const [saving, setSaving] = useState(false)
      const [dirty, setDirty] = useState(false)
      const [mode, setMode] = useState('edit')   // edit / read
      const [pinnedTop, setPinnedTop] = useState(false)
      const [fmtMenu, setFmtMenu] = useState(false)  // 格式二级菜单
      const [insMenu, setInsMenu] = useState(false)  // 插入二级菜单
      const [moreMenu, setMoreMenu] = useState(false)  // SIYI-MORE-20261120：更多菜单
      const [imgBusy, setImgBusy] = useState(false)   // 图片上传中
      const [aiOpen, setAiOpen] = useState(false)     // AI 助手面板展开/收起
      const [folder, setFolder] = useState(folderProp || '')   // SIYI-FOLDER-20261126
      const [folderMenu, setFolderMenu] = useState(false)   // SIYI-FOLDER-20261126
      const [folders, setFolders] = useState([])   // SIYI-FOLDER-20261126
      useEffect(() => {
        fetch('/siyi-vault/folders').then(r => r.json()).then(f => { if (f && f.ok) setFolders(f.folders || []) }).catch(() => {})
      }, [])
      const bodyRef = useRef(null)
      const titleRef = useRef(null)                   // 标题 textarea，用来自动增高
      // SIYI-S0-SAVE-20261119：服务端 revision 基线。更新必须带它，否则后端拒绝（CONFLICT 保护）。
      const revisionRef = useRef(null)                // 当前已保存内容的 revision
      const savingRef = useRef(false)                 // 保存中标记（避免闭包 stale）
      // SIYI-DRAFT-20261119：本地草稿（断网/切后台/杀进程可恢复）
      const draftKey = 'siyi-draft:' + (id || '__new__')
      const readDraft = () => {
        try { return JSON.parse(localStorage.getItem(draftKey) || 'null') } catch { return null }
      }
      const writeDraft = (data) => {
        try { localStorage.setItem(draftKey, JSON.stringify(data)) } catch {}
      }
      const clearDraft = () => {
        try { localStorage.removeItem(draftKey) } catch {}
      }
      // 标题随内容自动增高：先压回单行高度再按 scrollHeight 撑开，
      // 这样删除文字时也能正确收缩，不会只长不缩。
      const autoSizeTitle = (el) => {
        if (!el) return
        el.style.height = 'auto'
        el.style.height = Math.min(el.scrollHeight, Math.round(window.innerHeight * 0.4)) + 'px'
      }
      // 加载笔记、切换笔记、清空标题时都要重算高度。
      // 只在 onChange 里算的话，打开一篇长标题笔记时仍会被截断。
      useEffect(() => { autoSizeTitle(titleRef.current) }, [title, id])
      const fileRef = useRef(null)                    // 隐藏的 <input type=file>
      const historyRef = useRef({ stack: [], index: -1 })
      const skipHistory = useRef(false)

      // 是不是「阅读型」笔记：RSS 导入的 source，或正文里有 HTML 标签
      const isSourceLike = (meta, text) =>
        (meta && meta.kind === 'source') || /<[a-zA-Z][^>]*>/.test(text || '')

      useEffect(() => {
        let alive = true
        if (!id) {
          setNote(null); setTitle(''); setBody(''); setTags('')
          setFolder(folderProp || '')   // SIYI-FOLDER-20261126：新建笔记继承传入的文件夹
          revisionRef.current = null                  // SIYI-S0-SAVE-20261119
          setDirty(false); setMode('edit'); setPinnedTop(false)
          historyRef.current = { stack: [''], index: 0 }
          return
        }
        getNote(id).then(g => {
          if (!alive) return
          if (!g.ok) { toast('笔记不存在'); onBack(); return }
          setNote(g)
          revisionRef.current = g.revision || null   // SIYI-S0-SAVE-20261119
          setTitle(g.meta.title || '')
          setBody(g.body || '')
          setTags((g.meta.tags || []).join(', '))
          setFolder(g.meta.folder || '')   // SIYI-FOLDER-20261126
          setPinnedTop(g.meta.status === 'pinned')
          // 阅读型笔记默认进阅读态，避免一进来就看到满屏 HTML 源码
          setMode(isSourceLike(g.meta, g.body) ? 'read' : 'edit')
          setDirty(false)
          historyRef.current = { stack: [g.body || ''], index: 0 }
          // SIYI-DRAFT-20261119：若有本地草稿，恢复内容并提示（草稿优先于服务端版本）
          try {
            const d = readDraft()
            if (d && (d.title !== undefined || d.body !== undefined)) {
              if (d.title !== undefined) setTitle(d.title)
              if (d.body !== undefined) setBody(d.body)
              if (d.tags !== undefined) setTags(d.tags)
              setDirty(true)
              toast('已恢复未保存的本地草稿')
            }
          } catch {}
        }).catch(e => alive && toast('读取失败：' + e.message))
        return () => { alive = false }
      }, [id])

      // SIYI-S0-SAVE-20261119：保存时带 revision，响应按版本隔离，失败/冲突保留 dirty 供重试。
      const doSave = useCallback(async (opts) => {
        const silent = opts && opts.silent
        if (savingRef.current) return
        savingRef.current = true
        setSaving(true)
        // 发起时快照：响应回来时若内容已变，说明保存期间又有编辑，不清 dirty
        const snapshot = [body, title, tags].join(String.fromCharCode(0))
        const payload = {
          id: note ? note.id : undefined,
          title: title.trim() || '未命名',
          body: body,
          tags: tags.split(/[,，]/).map(s => s.trim()).filter(Boolean),
          kind: note ? note.meta.kind : 'note',
        }
        if (folder) payload.folder = folder   // SIYI-FOLDER-20261126：保存时带文件夹（新建默认归属 / 移动笔记）
        // SIYI-T0-20261121：任务状态由待办页管理，通用编辑器不得覆盖 done/open/archived
        if (note && note.meta && note.meta.kind === 'task') {
          payload.status = note.meta.status || 'open'
        } else {
          payload.status = pinnedTop ? 'pinned' : 'active'
        }
        // 更新必须带 expectedRevision；新建不带
        if (note && revisionRef.current) payload.expectedRevision = revisionRef.current
        try {
          const r = await saveNote(payload)
          if (!r.ok) {
            // CONFLICT：保留本地编辑和 dirty，提示用户，绝不自动覆盖
            if (r.code === 'CONFLICT') toast('笔记已被其他端修改，请手动处理冲突')
            else throw new Error(r.error || '保存失败')
            return
          }
          // 只有内容未变（保存期间没再输入）才清 dirty，避免旧响应覆盖新编辑
          const current = [body, title, tags].join(String.fromCharCode(0))
          if (current === snapshot) { setDirty(false); clearDraft() }
          revisionRef.current = r.revision || revisionRef.current
          if (!note) setNote({ id: r.id, meta: { title: payload.title, kind: payload.kind } })
          if (!silent) toast('已保存')
        } catch (e) {
          // 失败保留 dirty，不清除，供下次定时/手动重试
          toast('保存失败：' + e.message)
        } finally {
          savingRef.current = false
          setSaving(false)
        }
      }, [note, title, body, tags, pinnedTop])

      useEffect(() => {
        if (!dirty) return
        // SIYI-DRAFT-20261119：每次编辑都写本地草稿（防杀进程/断网）
        writeDraft({ title, body, tags, at: Date.now() })
        const t = setTimeout(() => doSave({ silent: true }), 1500)
        return () => clearTimeout(t)
      }, [dirty, title, body, tags, pinnedTop])

      // SIYI-DRAFT-20261119：页面隐藏/离开时立即尝试保存；失败也保留本地草稿供下次恢复。
      useEffect(() => {
        const flush = () => {
          if (!dirtyRef.current) return
          // 写草稿始终执行（同步、可靠）
          writeDraft({ title, body, tags, at: Date.now() })
          // 尽力触发一次保存（异步，不阻塞离开）
          try { doSaveRef.current && doSaveRef.current({ silent: true }) } catch {}
        }
        const onHide = () => { if (document.visibilityState === 'hidden') flush() }
        document.addEventListener('visibilitychange', onHide)
        window.addEventListener('pagehide', flush)
        return () => {
          document.removeEventListener('visibilitychange', onHide)
          window.removeEventListener('pagehide', flush)
        }
      }, [title, body, tags])

      // 供离开监听读取最新状态，避免闭包拿到旧值
      const dirtyRef = useRef(false)
      const doSaveRef = useRef(null)
      useEffect(() => { dirtyRef.current = dirty }, [dirty])
      useEffect(() => { doSaveRef.current = doSave }, [doSave])

      const pushHistory = useCallback((next) => {
        if (skipHistory.current) { skipHistory.current = false; return }
        const h = historyRef.current
        if (h.stack[h.index] === next) return
        h.stack = h.stack.slice(0, h.index + 1)
        h.stack.push(next)
        if (h.stack.length > 80) h.stack.shift()
        h.index = h.stack.length - 1
      }, [])

      const undo = () => {
        const h = historyRef.current
        if (h.index <= 0) { toast('没有可撤销的操作'); return }
        h.index--
        skipHistory.current = true
        setBody(h.stack[h.index]); setDirty(true)
      }
      const redo = () => {
        const h = historyRef.current
        if (h.index >= h.stack.length - 1) { toast('没有可重做的操作'); return }
        h.index++
        skipHistory.current = true
        setBody(h.stack[h.index]); setDirty(true)
      }

      const insert = (before, after, placeholder) => {
        const ta = bodyRef.current
        if (!ta) return
        const start = ta.selectionStart || 0, end = ta.selectionEnd || 0
        const sel = body.slice(start, end) || (placeholder || '')
        const next = body.slice(0, start) + before + sel + after + body.slice(end)
        pushHistory(next)
        setBody(next); setDirty(true)
        setFmtMenu(false); setInsMenu(false)
        setTimeout(() => {
          ta.focus()
          ta.selectionStart = ta.selectionEnd = start + before.length + sel.length
        }, 0)
      }
      const insertLine = (prefix) => {
        const ta = bodyRef.current
        if (!ta) return
        const start = ta.selectionStart || 0
        let lineStart = body.lastIndexOf('\n', start - 1) + 1
        const next = body.slice(0, lineStart) + prefix + body.slice(lineStart)
        pushHistory(next)
        setBody(next); setDirty(true)
        setFmtMenu(false); setInsMenu(false)
        setTimeout(() => { ta.focus(); ta.selectionStart = ta.selectionEnd = start + prefix.length }, 0)
      }
      // 纯插入一段文本（图片用），以 DOM 当前值为准，避免 await 之后闭包里的 body 过期
      const insertRaw = (text) => {
        const ta = bodyRef.current
        const cur = ta ? ta.value : body
        const start = ta ? (ta.selectionStart || 0) : cur.length
        const end = ta ? (ta.selectionEnd || 0) : cur.length
        const next = cur.slice(0, start) + text + cur.slice(end)
        pushHistory(next)
        setBody(next); setDirty(true)
        setFmtMenu(false); setInsMenu(false)
        setTimeout(() => {
          if (!ta) return
          ta.focus()
          ta.selectionStart = ta.selectionEnd = start + text.length
        }, 0)
      }
      // AI 回复插入：复用已有的 insertRaw（光标处插入），不另写一套。
      // 若正文当前是阅读态，先切回编辑态再插入，保证 textarea 已挂载。
      const insertAI = (text) => {
        if (mode === 'read') setMode('edit')
        // 阅读态时 textarea 下一帧才挂载，延后插入；编辑态直接插入光标处。
        if (mode === 'read') {
          setTimeout(() => insertRaw(text), 0)
        } else {
          insertRaw(text)
        }
        toast('已插入 AI 回复')
      }
      const toggleTodo = () => {
        const ta = bodyRef.current
        if (!ta) return
        const start = ta.selectionStart || 0
        let lineStart = body.lastIndexOf('\n', start - 1) + 1
        let lineEnd = body.indexOf('\n', lineStart)
        if (lineEnd < 0) lineEnd = body.length
        const line = body.slice(lineStart, lineEnd)
        let nextLine
        if (/^- \[x\] /i.test(line)) nextLine = line.replace(/^- \[x\] /i, '- [ ] ')
        else if (/^- \[ \] /.test(line)) nextLine = line.replace(/^- \[ \] /, '- [x] ')
        else nextLine = '- [ ] ' + line.replace(/^[-*]\s+/, '')
        const next = body.slice(0, lineStart) + nextLine + body.slice(lineEnd)
        pushHistory(next)
        setBody(next); setDirty(true)
        setFmtMenu(false); setInsMenu(false)
      }

      // 选图 -> 上传 -> 光标处插入 markdown 图片语法
      // <input type=file> 由 MainActivity 的 WebChromeClient.onShowFileChooser 弹系统选择器
      const onPickImage = async (e) => {
        const f = e.target && e.target.files && e.target.files[0]
        if (e.target) e.target.value = ''   // 允许连续选同一张
        if (!f) return
        const name = f.name || ''
        let ext = (name.match(/\.([A-Za-z0-9]+)$/) || [])[1] || ''
        if (!ext) ext = ({ 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif' })[f.type] || ''
        if (!/^(jpe?g|png|webp|gif)$/i.test(ext)) { toast('只支持 jpg/jpeg/png/webp/gif 图片'); return }
        if (f.size > 8 * 1024 * 1024) { toast('图片超过 8MB 上限'); return }
        setImgBusy(true)
        try {
          toast('图片上传中…')
          const r = await uploadImage(f)
          if (mode === 'read') setMode('edit')
          insertRaw('\n![](' + r.url + ')\n')
          toast('已插入图片')
        } catch (err) {
          toast('插入图片失败：' + (err && err.message ? err.message : err))
        } finally {
          setImgBusy(false)
        }
      }

      // 底栏图标按钮：文字用通用符号，触控区 44px
      // SIYI-A11Y-20261120：底栏按钮补齐无障碍语义与触控反馈
      const IconBtn = ({ label, glyph, icon, title, onClick, active, disabled }) => h('button', {
        type: 'button',
        title: title || label,
        'aria-label': title || label,
        'aria-pressed': active === undefined ? undefined : !!active,
        disabled: !!disabled,
        onClick,
        style: {
          minWidth: 44, minHeight: 44, flex: '1 1 0',
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', gap: 2,
          border: 'none', borderRadius: 10, cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.4 : 1,
          background: active ? 'var(--dsw-alias-bg-layer-3)' : 'transparent',
          color: active ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-secondary)',
          fontSize: 20, lineHeight: 1,
          touchAction: 'manipulation',
          WebkitTapHighlightColor: 'transparent',
          transition: 'background .12s ease',
        },
      },
        // SIYI-ICONBATCH-20261119：优先官方图标，缺则回落文字
        icon ? h(icon, { size: 20 }) : h('span', { style: { fontSize: 20 } }, glyph),
        label ? h('span', { style: { fontSize: 10 } }, label) : null,
      )

      const wordCount = (body || '').replace(/\s/g, '').length
      const linkCount = (body || '').match(/\[\[[^\]]+\]\]/g)?.length || 0

      // 二级菜单内容
      const fmtItems = [
        { g: 'B', t: '粗体', a: () => insert('**', '**', '粗体') },
        { g: 'I', t: '斜体', a: () => insert('*', '*', '斜体') },
        { g: 'U', t: '下划线', a: () => insert('<u>', '</u>', '下划线') },
        { g: 'S', t: '删除线', a: () => insert('~~', '~~', '删除线') },
        { g: 'H', t: '标题', a: () => insertLine('## ') },
        { g: '❝', icon: 'quote', t: '引用', a: () => insertLine('> ') },
        { g: '↶', icon: 'undo', t: '撤销', a: undo },
        { g: '↷', icon: 'redo', t: '重做', a: redo },
      ]
      const insItems = [
        { g: '☑', icon: 'checklist', t: '待办', a: toggleTodo },
        { g: '•', t: '列表', a: () => insertLine('- ') },
        { g: '1.', t: '编号', a: () => insertLine('1. ') },
        { g: '[[', t: '双链', a: () => insert('[[', ']]', '笔记标题') },
        { g: '#', t: '标签', a: () => insert(' #', '', '标签') },
        { g: '</>', t: '代码', a: () => insert('\n```\n', '\n```\n', 'code') },
        { g: '—', t: '分割线', a: () => { const n = body + '\n\n---\n\n'; pushHistory(n); setBody(n); setDirty(true); setInsMenu(false) } },
      ]

      return h('div', { className: 'siyi-notes-root' },
        // 顶部：返回 / 标题状态 / 阅读编辑切换
        h('div', { className: 'siyi-notes-editor-head' },
          // SIYI-OBTN-20261119：顶栏返回用官方 Button
          OButton
            ? h(OButton, { variant: 'ghost', size: 'sm', icon: I.back || null, onClick: onBack }, '返回')
            : h('button', { className: 'siyi-notes-btn siyi-btn-icon', onClick: onBack }, I.back ? h(I.back, { size: 16 }) : null, h('span', null, '返回')),
          h('span', {
            style: { flex: 1, fontSize: 11, color: 'var(--dsw-alias-label-tertiary)', textAlign: 'center' },
          }, saving ? '保存中…' : (dirty ? '未保存' : '已保存')),
          // SIYI-NODUPMODE-20261119：编辑/阅读切换已在底栏（IconBtn「阅读/编辑」），顶栏不再重复
          // SIYI-NOPIN-20261120：手机端不需要置顶，移除图钉按钮
          OButton
            ? h(OButton, {
                variant: aiOpen ? 'primary' : 'ghost',
                size: 'sm',
                title: 'AI 助手',
                onClick: () => setAiOpen(v => !v),
                icon: I.sparkle ? h(I.sparkle, { size: 14 }) : null,
              }, 'AI')
            : h('button', {
                className: 'siyi-notes-btn' + (aiOpen ? ' on' : ''),
                onClick: () => setAiOpen(v => !v),
                title: 'AI 助手',
              }, 'AI'),
            // SIYI-FOLDER-20261126：所属文件夹按钮（可移动笔记 / 新建默认归属）
            h('button', {
              className: 'siyi-notes-btn' + (folderMenu ? ' on' : ''),
              onClick: () => setFolderMenu(v => !v),
              title: '所属文件夹',
              style: { marginLeft: 6 },
            }, folder ? folder : '文件夹'),
        ),
        folderMenu
          ? h('div', { className: 'siyi-simple-menu siyi-more-scrim', onClick: () => setFolderMenu(false) },
              h('div', { className: 'siyi-simple-menu-panel siyi-more-panel', onClick: (e) => e.stopPropagation() },
                h('div', { className: 'siyi-simple-menu-total' }, '移动到文件夹'),
                h('div', { className: 'siyi-simple-menu-item' + (folder === '' ? ' on' : ''), onClick: () => { setFolder(''); setFolderMenu(false); toast('已设为未归类') } }, '未归类'),
                ...folders.map(f => h('div', { key: f.name, className: 'siyi-simple-menu-item' + (folder === f.name ? ' on' : ''), onClick: () => { setFolder(f.name); setFolderMenu(false); toast('已移动到「' + f.name + '」') } }, f.name)),
              )
            ) : null,
        // 内容区：独立滚动，高度占满
        h('div', { className: 'siyi-notes-editor' },
          h('textarea', {
            ref: titleRef,
            className: 'siyi-notes-title-input',
            placeholder: '标题',
            value: title,
            rows: 1,
            readOnly: mode === 'read',
            onChange: (e) => {
              setTitle(e.target.value)
              setDirty(true)
              autoSizeTitle(e.target)
            },
          }),
          mode === 'read'
            ? h('div', {
                className: 'siyi-notes-body-input',
                style: {
                  overflowY: 'auto', display: 'block', WebkitOverflowScrolling: 'touch',
                  lineHeight: 1.75,
                },
                dangerouslySetInnerHTML: { __html: renderRich(body) },
              })
            : h('textarea', {
                ref: bodyRef,
                className: 'siyi-notes-body-input',
                placeholder: '开始写…',
                value: body,
                onChange: (e) => { pushHistory(e.target.value); setBody(e.target.value); setDirty(true) },
                onKeyDown: (e) => {
                  if ((e.ctrlKey || e.metaKey) && e.key === 'b') { e.preventDefault(); insert('**', '**', '粗体') }
                  else if ((e.ctrlKey || e.metaKey) && e.key === 'i') { e.preventDefault(); insert('*', '*', '斜体') }
                  else if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); undo() }
                  else if ((e.ctrlKey || e.metaKey) && e.key === 'y') { e.preventDefault(); redo() }
                  else if (e.key === 'Tab') { e.preventDefault(); toggleTodo() }
                },
              }),
        ),
        // 隐藏的文件选择器，点「图片」按钮时触发
        h('input', {
          ref: fileRef,
          type: 'file',
          accept: 'image/*',
          style: { display: 'none' },
          onChange: onPickImage,
        }),
        // SIYI-MOREUI-20261120：更多弹窗——去掉标题，纵向列表，图标+文字左对齐不换行
        // SIYI-MORESMALL-20261120：紧凑底部浮层（不用 OModal，避免大标题栏）
        moreMenu
          ? h('div', { className: 'siyi-simple-menu siyi-more-scrim', onClick: () => setMoreMenu(false) },
              h('div', { className: 'siyi-simple-menu-panel siyi-more-panel', onClick: (e) => e.stopPropagation() },
                // SIYI-MORETIGHT-20261120：去掉小标题，紧贴文字大小
                h('div', { className: 'siyi-more-list' },
              h('button', {
                className: 'siyi-more-item',
                onClick: async () => {
                  try { await navigator.clipboard.writeText(body || ''); toast('已复制全文') } catch { toast('复制失败') }
                  setMoreMenu(false)
                },
              }, I.copy ? h(I.copy, { size: 18 }) : null, h('span', null, '复制全文')),
              h('button', {
                className: 'siyi-more-item',
                onClick: async () => {
                  try {
                    const r = await api('/inbox-add', { method: 'POST', body: JSON.stringify({ title: title || '未命名', body: body || '', source: { type: 'note', ref: id || '', excerpt: '' } }) })
                    setMoreMenu(false)
                    toast(r.duplicate ? '已存在相同内容，未重复存入' : '已存入收件箱，请去 ⋯ → 知识收件箱查看')
                  } catch (e) { setMoreMenu(false); toast('存入失败：' + e.message) }
                },
              }, I.data ? h(I.data, { size: 18 }) : null, h('span', null, '存入收件箱')),   // SIYI-K1SRC-20261123
              h('button', {
                className: 'siyi-more-item',
                onClick: async () => {
                  try { await navigator.clipboard.writeText('[[' + (title || '未命名') + ']]'); toast('已复制双链') } catch { toast('复制失败') }
                  setMoreMenu(false)
                },
              }, I.link ? h(I.link, { size: 18 }) : null, h('span', null, '复制双链')),
              h('button', {
                className: 'siyi-more-item',
                onClick: () => {
                  try {
                    const md = '---\ntitle: ' + (title || '未命名') + '\nupdated: ' + new Date().toISOString() + '\n---\n\n' + (body || '')
                    const blob = new Blob([md], { type: 'text/markdown' })
                    const a = document.createElement('a')
                    a.href = URL.createObjectURL(blob)
                    a.download = (title || 'note') + '.md'
                    a.click()
                    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
                    toast('已导出 Markdown')
                  } catch (e) { toast('导出失败') }
                  setMoreMenu(false)
                },
              }, I.download ? h(I.download, { size: 18 }) : null, h('span', null, '导出 MD')),
              h('button', {
                className: 'siyi-more-item danger',
                onClick: async () => {
                  setMoreMenu(false)
                  if (!note) { toast('新笔记无需删除'); return }
                  if (!window.confirm('确定删除这篇笔记？会移到回收站。')) return
                  try {
                    const r = await deleteNote(note.id)
                    if (r && r.ok) { toast('已移到回收站'); onBack() }
                    else toast('删除失败：' + ((r && r.error) || '未知'))
                  } catch (e) { toast('删除失败：' + e.message) }
                },
              }, I.trash ? h(I.trash, { size: 18 }) : null, h('span', null, '删除笔记')),
                ),
              ),
            )
          : null,
        // SIYI-OMODAL-20261120：格式/插入菜单用官方 Modal
        (fmtMenu || insMenu)
          ? (OModal
              ? h(OModal, {
                  open: true,
                  onClose: () => { setFmtMenu(false); setInsMenu(false) },
                  title: fmtMenu ? '格式' : '插入',
                  closeLabel: '关闭',
                }, h('div', {
                  style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(84px, 1fr))', gap: 8 },
                }, (fmtMenu ? fmtItems : insItems).map(it => h(OButton, {
                  key: it.t,
                  variant: 'outline',
                  size: 'sm',
                  onClick: () => { it.a(); setFmtMenu(false); setInsMenu(false) },
                  icon: it.icon && I[it.icon] ? h(I[it.icon], { size: 15 }) : null,
                }, it.t))))
              : h('div', {
                  style: {
                    display: 'flex', flexWrap: 'wrap', gap: 6, padding: '8px 10px',
                    borderTop: '.5px solid var(--dsw-alias-border-l3)',
                    background: 'var(--dsw-alias-bg-layer-2)', flex: 'none',
                  },
                }, (fmtMenu ? fmtItems : insItems).map(it => h('button', {
                  key: it.t, title: it.t,
                  onMouseDown: (e) => e.preventDefault(),
                  onClick: it.a,
                  style: {
                    minWidth: 48, minHeight: 44, padding: '0 10px', borderRadius: 10,
                    border: '.5px solid var(--dsw-alias-border-l3)',
                    background: 'var(--dsw-alias-bg-layer-1)',
                    color: 'var(--dsw-alias-label-primary)', fontSize: 14,
                  },
                }, it.icon && I[it.icon] ? h(I[it.icon], { size: 16 }) : it.g))))
          : null,
        // 底部固定工具栏：分类式，参考红米笔记
        h('div', {
          style: {
            display: 'flex', gap: 2, padding: '6px 8px',
            borderTop: '.5px solid var(--dsw-alias-border-l3)',
            background: 'var(--dsw-alias-bg-layer-2)', flex: 'none',
            paddingBottom: 'calc(6px + env(safe-area-inset-bottom, 0px))',
          },
        },
          h(IconBtn, { icon: I.edit, glyph: '✎', label: '格式', active: fmtMenu, onClick: () => { setFmtMenu(v => !v); setInsMenu(false) } }),
          // SIYI-SIMPLE2-20261120：待办已并入插入菜单
          h(IconBtn, { icon: I.plus, glyph: '＋', label: '插入', active: insMenu, onClick: () => { setInsMenu(v => !v); setFmtMenu(false) } }),
          h(IconBtn, { icon: I.image, glyph: '🖼', label: imgBusy ? '上传中' : '图片', active: imgBusy, onClick: () => { if (fileRef.current) fileRef.current.click() } }),
          h(IconBtn, { icon: mode === 'read' ? I.edit : I.eye, glyph: mode === 'read' ? '✎' : '👁', label: mode === 'read' ? '编辑' : '阅读', onClick: () => setMode(m => m === 'read' ? 'edit' : 'read') }),
          h(IconBtn, { icon: I.more, glyph: '⋯', label: '更多', active: moreMenu, onClick: () => { setMoreMenu(v => !v); setFmtMenu(false); setInsMenu(false) } }),
        ),
        // AI 助手半屏面板：展开时占地 50vh，编辑区自动收缩；可收起
        aiOpen ? h(AIPanel, {
          noteText: (title ? '# ' + title + '\n' : '') + (body || ''),
          onInsert: insertAI,
          onClose: () => setAiOpen(false),
          // SIYI-EDITPROPS-20261120：选区改写所需
          bodyRef,
          noteId: note && note.id,
          revision: revisionRef.current,
          toast,   // SIYI-AITOAST-SCOPE-20261120
          onApplyEdit: (sel, replacement) => {
            const ta = bodyRef && bodyRef.current
            const cur = String((ta && ta.value) || body || '')
            const idx = cur.indexOf(sel)
            if (idx < 0) { toast('选区已变化，请重新改写'); return }
            const next = cur.slice(0, idx) + replacement + cur.slice(idx + sel.length)
            setBody(next)
            setDirty(true)
            if (ta) ta.value = next
          },
        }) : null,
        // 底部信息条
        h('div', { className: 'siyi-notes-foot' },
          // SIYI-SIMPLE2-20261120：只留字数，更新时间不再常驻
          h('span', null, wordCount + ' 字'),
          h('span', null, linkCount ? linkCount + ' 双链' : ''),
        ),
      )
    }

    // 图片语法只放行两类 src：本站 asset（sha256 前16位 + 白名单扩展名）与 https。
    // 其余（javascript:、data:、../ 路径）一律原样显示文本，避免扩大注入面。
    const IMG_SRC_RE = /^(?:\/siyi-vault\/asset\/[0-9a-f]{16}\.(?:jpe?g|png|webp|gif)|https:\/\/[^\s"'<>()]+)$/
    function safeImgSrc(src) {
      const v = String(src || '').trim()
      return IMG_SRC_RE.test(v) ? v : null
    }
    function attrEsc(v) {
      return String(v || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;')
    }

    function renderMarkdown(md) {
      if (!md) return ''
      // ⚠ 不能整段先转义再解析：一旦 > 变成 &gt;，
      // 块引用的 /^> / 就永远匹配不上（这是实测踩到的 bug）。
      // 正确顺序是先按行识别结构，只对行内内容做转义。
      const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      const lines = md.split('\n')
      const out = []
      let inCode = false
      for (let raw of lines) {
        if (/^```/.test(raw)) { inCode = !inCode; out.push(inCode ? '<pre><code>' : '</code></pre>'); continue }
        if (inCode) { out.push(raw); continue }
        // 待办
        let m
        if ((m = raw.match(/^- \[( |x)\] (.*)$/i))) {
          const done = m[1].toLowerCase() === 'x'
          out.push('<div style="display:flex;gap:6px;align-items:flex-start;margin:2px 0">' +
            '<span style="flex:none">' + (done ? '☑' : '☐') + '</span>' +
            '<span' + (done ? ' style="text-decoration:line-through;opacity:.6"' : '') + '>' + inline(m[2]) + '</span></div>')
          continue
        }
        if ((m = raw.match(/^(#{1,4}) (.*)$/))) {
          const lv = m[1].length
          const size = [22, 19, 17, 15][lv - 1]
          out.push('<div style="font-size:' + size + 'px;font-weight:600;margin:10px 0 4px">' + inline(m[2]) + '</div>')
          continue
        }
        if (/^---+$/.test(raw)) { out.push('<hr style="border:none;border-top:.5px solid var(--dsw-alias-border-l3);margin:12px 0">'); continue }
        if ((m = raw.match(/^> (.*)$/))) {
          out.push('<div style="border-left:3px solid var(--dsw-alias-border-l1);padding-left:10px;opacity:.8;margin:4px 0">' + inline(m[1]) + '</div>')
          continue
        }
        if ((m = raw.match(/^[-*] (.*)$/))) { out.push('<div style="padding-left:16px;position:relative;margin:2px 0"><span style="position:absolute;left:4px">•</span>' + inline(m[1]) + '</div>'); continue }
        if ((m = raw.match(/^\d+\. (.*)$/))) { out.push('<div style="padding-left:20px;margin:2px 0">' + raw.replace(/^(.*)$/, '$1') .replace(/^(\d+)\. /, '<span style="opacity:.6">$1.</span> ') + '</div>'); continue }
        // 独占一行的图片：渲染成块级 <img>，max-width:100% 防撑破窄屏
        if ((m = raw.match(/^\s*!\[([^\]]*)\]\(([^()\s]+)\)\s*$/))) {
          const src = safeImgSrc(m[2])
          if (src) {
            out.push('<div style="margin:8px 0"><img src="' + attrEsc(src) + '" alt="' + attrEsc(m[1]) +
              '" style="max-width:100%;height:auto;display:block;border-radius:8px"></div>')
            continue
          }
        }
        if (raw.trim() === '') { out.push('<div style="height:8px"></div>'); continue }
        out.push('<div style="margin:2px 0">' + inline(raw) + '</div>')
      }
      return out.join('')
    }
    function inline(s) {
      // 先转义 HTML 特殊字符，再套 markdown 规则 —— 顺序不能反，
      // 否则用户输入的 <script> 会被当成标签渲染（XSS 风险），
      // 而我们自己的 <u> 规则又要在这之后才生效，所以用占位符保护。
      const U_OPEN = '\u0001UO\u0001', U_CLOSE = '\u0001UC\u0001'
      let t = String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/&lt;u&gt;/g, U_OPEN).replace(/&lt;\/u&gt;/g, U_CLOSE)
      return t
        .replace(/!\[([^\]]*)\]\(([^()\s]+)\)/g, (m0, alt, src) => {
          const ok = safeImgSrc(src)
          return ok ? '<img src="' + attrEsc(ok) + '" alt="' + attrEsc(alt) + '" style="max-width:100%;height:auto;display:inline-block;vertical-align:middle">' : m0
        })
        .replace(/\[\[([^\]]+)\]\]/g, '<span style="color:var(--dsw-alias-label-link, #3b82f6);border-bottom:1px dashed currentColor">$1</span>')
        .replace(/`([^`]+)`/g, '<code style="background:var(--dsw-alias-bg-layer-3);padding:1px 4px;border-radius:3px;font-size:.9em">$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>')
        .replace(/~~([^~]+)~~/g, '<del>$1</del>')
        .replace(new RegExp(U_OPEN + '([^]*?)' + U_CLOSE, 'g'), '<u>$1</u>')
        .replace(/#([\u4e00-\u9fa5A-Za-z0-9_]+)/g, '<span style="color:var(--dsw-alias-label-secondary)">#$1</span>')
    }

    /**
     * 阅读态渲染：markdown + 安全 HTML 白名单。
     *
     * 背景：RSS 导入的 source 笔记正文直接含
     *   <div align='right'><a href='...'>点击查看原文></a></div>
     * 纯 textarea 会把这段当源码显示，撑满屏幕（用户反馈图1）。
     * 这里对白名单标签做真实渲染，其余一律转义，兼顾可读与安全。
     *
     * 白名单：a(href)、div(align)、br、p、span、strong、em、u、code。
     * 属性只留 href 和 align，href 必须 http(s)，杜绝 javascript:。
     */
    function renderRich(md) {
      if (!md) return ''
      // 先跑 markdown，得到带 HTML 的字符串
      let html = renderMarkdown(md)
      // 白名单放行：把被转义的安全标签还原（仅限本应用自己产生的）
      const allowAttrs = (tag) => {
        // 取出标签内容，只保留安全属性
        return tag
      }
      // a 标签：href 必须 http(s)
      // ⚠ inline() 只转义双引号，单引号会原样保留。
      // 所以属性值要同时兼容 ' 和 &quot; 两种形态（RSS 源里两种都有）。
      html = html.replace(/&lt;a\s+href=(?:'|&quot;)(https?:\/\/[^'&]+)(?:'|&quot;)[^&]*&gt;([\s\S]*?)&lt;\/a&gt;/g,
        (m, href, txt) => '<a href="' + href + '" target="_blank" rel="noopener noreferrer" style="color:var(--dsw-alias-label-link,#3b82f6)">' + txt + '</a>')
      // div align / br / p / span
      html = html.replace(/&lt;div\s+align=(?:'|&quot;)(left|right|center)(?:'|&quot;)&gt;/g,
        (m, al) => '<div style="text-align:' + al + '">')
      html = html.replace(/&lt;\/div&gt;/g, '</div>')
      html = html.replace(/&lt;br\s*\/?&gt;/g, '<br>')
      html = html.replace(/&lt;p&gt;/g, '<div>').replace(/&lt;\/p&gt;/g, '</div>')
      html = html.replace(/&lt;span&gt;/g, '<span>').replace(/&lt;\/span&gt;/g, '</span>')
      html = html.replace(/&lt;strong&gt;/g, '<strong>').replace(/&lt;\/strong&gt;/g, '</strong>')
      html = html.replace(/&lt;em&gt;/g, '<em>').replace(/&lt;\/em&gt;/g, '</em>')
      html = html.replace(/&lt;code&gt;/g, '<code>').replace(/&lt;\/code&gt;/g, '</code>')
      return html
    }
    // ── D4 知识图谱 ─────────────────────────────────────────────────
    // 手机端交互（按 MDN Pointer Events 与 touch-action 规范）：
    //   - touch-action:none 关掉浏览器手势，pointermove 才收得到连续位移
    //   - 浏览器接管手势会发 pointercancel，必须处理，否则手势卡死
    //   - 点击判定：位移阈值放宽到 10px（手指点按几乎不可能零位移）
    //   - 节点直接绑 onClick（DOM 原生），绕开 pointer 位移判定 ——
    //     这是「点节点没反应」的正面修复
    //   - 节点命中半径 28px * 缩放补偿，手指点得准
    //   - 拖空白平移、双指捏合缩放、拖节点固定、双击复位
    function GraphView({ onBack, onOpen, toast }) {
      const [graph, setGraph] = useState(null)
      const [semantic, setSemantic] = useState(false)   // SIYI-K2UI-20261123
      const [sel, setSel] = useState(null)
      const [size, setSize] = useState({ w: 360, h: 480 })
      const [view, setView] = useState({ x: 0, y: 0, k: 1 })
      const [pinned, setPinned] = useState({})
      const wrapRef = useRef(null)
      const gesture = useRef(null)
      const pinch = useRef(null)

      // SIYI-K2UI-20261123：结构图 / 语义图切换加载
      useEffect(() => {
        const p = semantic ? api('/semantic-graph') : getGraph()
        p.then(g => { setGraph(g); setSel(null) }).catch(e => toast('图谱读取失败：' + e.message))
      }, [semantic])

      useEffect(() => {
        const el = wrapRef.current
        if (!el) return
        const ro = new ResizeObserver(entries => {
          const r = entries[0].contentRect
          setSize({ w: Math.max(200, r.width), h: Math.max(200, r.height) })
        })
        ro.observe(el)
        return () => ro.disconnect()
      }, [])

      const baseLayout = useMemo(() => {
        if (!graph || !graph.nodes || !graph.nodes.length) return null
        const nodes = graph.nodes
        const edges = (graph.edges || []).filter(e => e.target)
        const W = size.w, H = size.h, CX = W / 2, CY = H / 2
        const N = nodes.length
        const arr = nodes.map((n, i) => {
          const a = (i / N) * Math.PI * 2
          const r = Math.min(W, H) * 0.32
          return { id: n.id, x: CX + Math.cos(a) * r, y: CY + Math.sin(a) * r }
        })
        const idx = new Map(nodes.map((n, i) => [n.id, i]))
        const link = edges.map(e => ({ s: idx.get(e.source), t: idx.get(e.target) }))
          .filter(e => e.s != null && e.t != null)
        const deg = new Map(nodes.map(n => [n.id, 0]))
        for (const e of edges) {
          deg.set(e.source, (deg.get(e.source) || 0) + 1)
          deg.set(e.target, (deg.get(e.target) || 0) + 1)
        }
        for (let it = 0; it < 260; it++) {
          const t = 1 - it / 260
          for (let i = 0; i < arr.length; i++) {
            for (let j = i + 1; j < arr.length; j++) {
              let dx = arr[j].x - arr[i].x, dy = arr[j].y - arr[i].y
              let d2 = dx * dx + dy * dy
              if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1 }
              const d = Math.sqrt(d2)
              const rep = (1600 * t) / d2
              const fx = (dx / d) * rep, fy = (dy / d) * rep
              arr[i].x -= fx; arr[i].y -= fy
              arr[j].x += fx; arr[j].y += fy
            }
          }
          for (const l of link) {
            const a = arr[l.s], b = arr[l.t]
            if (!a || !b) continue
            const dx = b.x - a.x, dy = b.y - a.y
            const d = Math.sqrt(dx * dx + dy * dy) || 1
            const f = ((d - 76) * 0.055) * t
            const fx = (dx / d) * f, fy = (dy / d) * f
            a.x += fx; a.y += fy
            b.x -= fx; b.y -= fy
          }
          for (const a of arr) {
            a.x += (CX - a.x) * 0.006
            a.y += (CY - a.y) * 0.006
          }
        }
        for (const a of arr) {
          a.x = Math.max(30, Math.min(W - 30, a.x))
          a.y = Math.max(26, Math.min(H - 26, a.y))
        }
        return { nodes: arr, map: new Map(arr.map(a => [a.id, a])), link, deg, W, H }
      }, [graph, size])

      const nodes = useMemo(() => {
        if (!baseLayout) return []
        return baseLayout.nodes.map(n => {
          const p = pinned[n.id]
          return p ? { ...n, x: p.x, y: p.y } : n
        })
      }, [baseLayout, pinned])

      const pos = useMemo(() => {
        const m = new Map()
        for (const n of nodes) m.set(n.id, n)
        return m
      }, [nodes])

      const neighbors = useMemo(() => {
        if (!sel || !graph || !graph.edges) return null
        const s = new Set([sel])
        for (const e of graph.edges) {
          if (e.source === sel) s.add(e.target)
          if (e.target === sel) s.add(e.source)
        }
        return s
      }, [sel, graph])

      const localPoint = (e) => {
        const el = wrapRef.current
        if (!el) return { x: 0, y: 0 }
        const r = el.getBoundingClientRect()
        return { x: e.clientX - r.left, y: e.clientY - r.top }
      }
      const toCanvas = (p, v) => ({ x: (p.x - v.x) / v.k, y: (p.y - v.y) / v.k })
      const hitNode = (canvasPt) => {
        let best = null, bestD = 28   // 从 22 放宽到 28，手指点得准
        for (const n of nodes) {
          const d = Math.hypot(n.x - canvasPt.x, n.y - canvasPt.y)
          if (d < bestD) { bestD = d; best = n }
        }
        return best
      }

      // 点节点：DOM 原生 onClick，最可靠
      const clickNode = (id) => { setSel(prev => prev === id ? null : id) }

      const onPointerDown = (e) => {
        if (!wrapRef.current) return
        // 双指时不启动单指手势
        if (pinch.current) return
        const p = localPoint(e)
        const cp = toCanvas(p, view)
        const hit = hitNode(cp)
        gesture.current = {
          mode: hit ? 'node' : 'pan',
          nodeId: hit ? hit.id : null,
          startX: p.x, startY: p.y,
          startView: { ...view },
          startNode: hit ? { x: hit.x, y: hit.y } : null,
          moved: false,
        }
        try { wrapRef.current.setPointerCapture(e.pointerId) } catch (err) {}
      }

      const onPointerMove = (e) => {
        const g = gesture.current
        if (!g) return
        const p = localPoint(e)
        const dx = p.x - g.startX, dy = p.y - g.startY
        // 阈值从 3px 放宽到 10px：手指点按的微小抖动不该判成拖动
        if (Math.abs(dx) > 10 || Math.abs(dy) > 10) g.moved = true
        if (g.mode === 'pan') {
          setView({ ...g.startView, x: g.startView.x + dx, y: g.startView.y + dy })
        } else if (g.mode === 'node' && g.nodeId && g.moved) {
          const nx = g.startNode.x + dx / g.startView.k
          const ny = g.startNode.y + dy / g.startView.k
          setPinned(prev => ({ ...prev, [g.nodeId]: { x: nx, y: ny } }))
        }
      }

      const onPointerUp = (e) => {
        const g = gesture.current
        gesture.current = null
        if (!g) return
        try { wrapRef.current.releasePointerCapture(e.pointerId) } catch (err) {}
        // pointer 手势不在这里改 sel，点击交给节点的 onClick 处理，
        // 避免「既被判点击又被判选中」的冲突。
        if (g.mode === 'pan' && !g.moved) setSel(null)
      }

      // 浏览器接管手势时触发；必须清理，否则手势状态残留导致后续拖动失效
      const onPointerCancel = () => { gesture.current = null; pinch.current = null }

      const onWheel = (e) => {
        e.preventDefault()
        const p = localPoint(e)
        const factor = e.deltaY > 0 ? 0.9 : 1.1
        setView(v => {
          const k = Math.max(0.3, Math.min(3, v.k * factor))
          const cx = (p.x - v.x) / v.k, cy = (p.y - v.y) / v.k
          return { k, x: p.x - cx * k, y: p.y - cy * k }
        })
      }

      const onTouchStart = (e) => {
        if (e.touches.length === 2) {
          const [a, b] = e.touches
          pinch.current = {
            dist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
            k: view.k, v: { ...view },
          }
          gesture.current = null
        }
      }
      const onTouchMove = (e) => {
        const pc = pinch.current
        if (!pc || e.touches.length !== 2 || !wrapRef.current) return
        e.preventDefault()
        const [a, b] = e.touches
        const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)
        const r = wrapRef.current.getBoundingClientRect()
        const mx = (a.clientX + b.clientX) / 2 - r.left
        const my = (a.clientY + b.clientY) / 2 - r.top
        const k = Math.max(0.3, Math.min(3, pc.k * (dist / pc.dist)))
        const cx = (mx - pc.v.x) / pc.v.k, cy = (my - pc.v.y) / pc.v.k
        setView({ k, x: mx - cx * k, y: my - cy * k })
      }
      const onTouchEnd = () => { pinch.current = null }

      const resetView = () => { setView({ x: 0, y: 0, k: 1 }); setPinned({}); setSel(null) }

      const share = graph && graph.edges ? graph.edges.filter(e => !e.missing).length : 0
      const isolated = graph && graph.nodes ? graph.nodes.filter(n => (n.degree || 0) === 0).length : 0

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-editor-head' },
          h('button', { className: 'siyi-notes-btn', onClick: onBack }, '‹ 返回'),
          h('button', {
            className: 'siyi-notes-btn' + (semantic ? ' on' : ''),
            title: '切换结构/语义图谱',
            onClick: () => setSemantic(v => !v),
          }, semantic ? '语义' : '结构'),   // SIYI-K2UI-20261123
          h('button', {
            className: 'siyi-notes-btn',
            onClick: () => setView(v => ({ ...v, k: Math.min(3, v.k * 1.25) })),
          }, I.plus ? h(I.plus, { size: 16 }) : '＋'),
          h('button', {
            className: 'siyi-notes-btn',
            onClick: () => setView(v => ({ ...v, k: Math.max(0.3, v.k / 1.25) })),
          }, '－'),
          h('button', { className: 'siyi-notes-btn', onClick: resetView }, '复位'),
          h('span', {
            style: { marginLeft: 'auto', fontSize: 11, color: 'var(--dsw-alias-label-tertiary)' },
          }, graph ? (graph.nodes.length + ' 节点 · ' + share + ' 链') : '读取中…'),
        ),
        h('div', {
          className: 'siyi-notes-graph',
          ref: wrapRef,
          style: { touchAction: 'none', userSelect: 'none', WebkitUserSelect: 'none' },
          onPointerDown, onPointerMove, onPointerUp,
          onPointerCancel: onPointerCancel,
          onWheel, onTouchStart, onTouchMove, onTouchEnd,
          onDoubleClick: resetView,
        },
          !baseLayout ? h('div', { className: 'siyi-notes-empty' }, graph ? '没有可绘制的节点' : '读取中…')
            : h('svg', {
                viewBox: '0 0 ' + baseLayout.W + ' ' + baseLayout.H,
                style: { width: '100%', height: '100%', display: 'block' },
              },
                h('g', { transform: 'translate(' + view.x + ',' + view.y + ') scale(' + view.k + ')' },
                  baseLayout.link.map((l, i) => {
                    const a = pos.get(nodes[l.s] && nodes[l.s].id)
                    const b = pos.get(nodes[l.t] && nodes[l.t].id)
                    if (!a || !b) return null
                    const hi = sel && (a.id === sel || b.id === sel)
                    const dim = neighbors && !(neighbors.has(a.id) && neighbors.has(b.id))
                    return h('line', {
                      key: 'e' + i,
                      x1: a.x, y1: a.y, x2: b.x, y2: b.y,
                      stroke: hi ? 'var(--dsw-alias-label-secondary)' : 'var(--dsw-alias-border-l3)',
                      strokeWidth: (hi ? 1.8 : 0.8) / Math.max(0.6, view.k),
                      opacity: dim ? 0.15 : 1,
                    })
                  }),
                  nodes.map(n => {
                    const d = baseLayout.deg.get(n.id) || 0
                    // 最小半径从 4 提到 6，孤立节点也能点中
                    const r = 6 + Math.min(7, d * 1.4)
                    const active = sel === n.id
                    const dim = neighbors && !neighbors.has(n.id)
                    return h('g', {
                      key: n.id,
                      // 关键：节点自身绑 onClick，不受 pointer 位移阈值影响
                      onClick: () => clickNode(n.id),
                      style: { cursor: 'pointer' },
                    },
                      // 透明大圆作触控热区，放大点击范围到 28px
                      h('circle', {
                        cx: n.x, cy: n.y, r: 20,
                        fill: 'transparent', pointerEvents: 'all',
                      }),
                      h('circle', {
                        cx: n.x, cy: n.y, r: active ? r + 2 : r,
                        fill: active ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-tertiary)',
                        opacity: dim ? 0.2 : (active ? 1 : (d ? .85 : .6)),
                        stroke: active ? 'var(--dsw-alias-label-primary)' : 'none',
                        strokeWidth: 1.5,
                        pointerEvents: 'none',
                      }),
                      (d >= 2 || active) ? h('text', {
                        x: n.x, y: n.y - r - 5, textAnchor: 'middle',
                        fontSize: 9 / Math.max(0.7, view.k),
                        fill: 'var(--dsw-alias-label-tertiary)',
                        opacity: dim ? 0.2 : 1,
                        pointerEvents: 'none',
                      }, (graph.nodes.find(x => x.id === n.id) || {}).title || '') : null,
                    )
                  }),
                ),
              ),
          sel ? h('div', {
            style: {
              position: 'absolute', left: 8, right: 8, top: 8,
              background: 'var(--dsw-alias-bg-layer-2)',
              border: '.5px solid var(--dsw-alias-border-l3)',
              borderRadius: 8, padding: '10px 12px', fontSize: 13, zIndex: 5,
            },
          },
            h('div', { style: { fontWeight: 600, marginBottom: 6, lineHeight: 1.4 } },
              (graph.nodes.find(x => x.id === sel) || {}).title || ''),
            h('div', { style: { display: 'flex', gap: 6 } },
              h('button', {
                className: 'siyi-notes-btn', style: { minHeight: 42, flex: 1 },
                onClick: () => onOpen(sel),
              }, '打开笔记'),
              h('button', {
                className: 'siyi-notes-btn', style: { minHeight: 42 },
                onClick: () => { setPinned(p => { const c = { ...p }; delete c[sel]; return c }); toast('已解除固定') },
              }, '解除固定'),
              h('button', {
                className: 'siyi-notes-btn', style: { minHeight: 42 },
                onClick: () => setSel(null),
              }, '关闭'),
            ),
          ) : null,
          h('div', { className: 'siyi-notes-legend' },
            '点节点查看 · 拖动平移 · 双指缩放 · 双击复位'),
        ),
      )
    }
    // ── E1 月历视图 ─────────────────────────────────────────────────
    //
    // 只做月网格 + 每日待办 + 逾期标红，不搬 desk-notes 那套
    // （它依赖 astrology、黄历、八字一大堆，跟这里没关系）。
    //
    // due 的来源是 task 笔记的 frontmatter，格式 YYYY-MM-DD，有空值，
    // 所以按日期分桶时要跳过 null。
    function MonthView({ onBack, onOpen, toast }) {
      const today = new Date()
      const [ym, setYm] = useState({ y: today.getFullYear(), m: today.getMonth() })
      const [tasks, setTasks] = useState([])
      const [selDay, setSelDay] = useState(null)
      const [loading, setLoading] = useState(true)

      const load = useCallback(async () => {
        setLoading(true)
        try {
          // done=1 拿全部，月历上要能看到已完成（淡色），只有未完成才标红
          const r = await api('/tasks?done=1')
          setTasks(r.items || [])
        } catch (e) { toast('待办读取失败：' + e.message) }
        finally { setLoading(false) }
      }, [])

      useEffect(() => { load() }, [load])

      const byDay = useMemo(() => {
        const map = new Map()
        for (const t of tasks) {
          if (!t.due) continue
          const k = String(t.due).slice(0, 10)
          if (!map.has(k)) map.set(k, [])
          map.get(k).push(t)
        }
        return map
      }, [tasks])

      const grid = useMemo(() => {
        const first = new Date(ym.y, ym.m, 1)
        const startDow = first.getDay()
        const daysInMonth = new Date(ym.y, ym.m + 1, 0).getDate()
        const cells = []
        for (let i = 0; i < startDow; i++) cells.push(null)
        for (let d = 1; d <= daysInMonth; d++) {
          cells.push({ d, key: ym.y + '-' + String(ym.m + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0') })
        }
        while (cells.length % 7 !== 0) cells.push(null)
        return cells
      }, [ym])

      const todayKey = today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0') + '-' + String(today.getDate()).padStart(2, '0')
      const selTasks = selDay ? (byDay.get(selDay.key) || []) : []

      const prevMonth = () => setYm(v => v.m === 0 ? { y: v.y - 1, m: 11 } : { y: v.y, m: v.m - 1 })
      const nextMonth = () => setYm(v => v.m === 11 ? { y: v.y + 1, m: 0 } : { y: v.y, m: v.m + 1 })

      const overdueCount = useMemo(() => {
        let n = 0
        for (const [k, list] of byDay) {
          if (k >= todayKey) continue
          n += list.filter(t => t.status !== 'done').length
        }
        return n
      }, [byDay, todayKey])

      const WEEK = ['日', '一', '二', '三', '四', '五', '六']

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-editor-head' },
          h('button', { className: 'siyi-notes-btn siyi-btn-icon', onClick: onBack },
          I.back ? h(I.back, { size: 16 }) : null, h('span', null, '返回')),
          h('button', { className: 'siyi-notes-btn', onClick: prevMonth }, '‹'),
          h('span', { style: { fontSize: 13, fontWeight: 600, minWidth: 76, textAlign: 'center' } },
            ym.y + ' 年 ' + (ym.m + 1) + ' 月'),
          h('button', { className: 'siyi-notes-btn', onClick: nextMonth }, '›'),
          overdueCount > 0 ? h('span', {
            style: { marginLeft: 'auto', fontSize: 11, color: '#dc2626' },
          }, '逾期 ' + overdueCount) : null,
        ),
        h('div', {
          style: {
            display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', flex: 'none',
            borderBottom: '.5px solid var(--dsw-alias-border-l3)',
          },
        }, WEEK.map(w => h('div', {
          key: w,
          style: { textAlign: 'center', fontSize: 10, padding: '4px 0', color: 'var(--dsw-alias-label-tertiary)' },
        }, w))),
        h('div', {
          style: {
            display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gridAutoRows: '1fr',
            flex: 'none', borderBottom: '.5px solid var(--dsw-alias-border-l3)',
          },
        }, grid.map((c, i) => {
          if (!c) return h('div', { key: 'e' + i })
          const list = byDay.get(c.key) || []
          const openN = list.filter(t => t.status !== 'done').length
          const isToday = c.key === todayKey
          const isSel = selDay && selDay.key === c.key
          const isPast = c.key < todayKey
          return h('div', {
            key: c.key,
            onClick: () => setSelDay(isSel ? null : c),
            style: {
              position: 'relative', minHeight: 46, padding: '4px 1px',
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              cursor: 'pointer',
              background: isSel ? 'var(--dsw-alias-bg-layer-3)' : 'transparent',
              border: isToday ? '.5px solid var(--dsw-alias-label-secondary)' : 'none',
              borderRadius: isToday ? 6 : 0,
            },
          },
            h('span', {
              style: {
                fontSize: 12, lineHeight: 1.5,
                fontWeight: isToday ? 700 : 400,
                color: isPast && openN > 0 ? '#dc2626' : 'var(--dsw-alias-label-primary)',
              },
            }, c.d),
            openN > 0 ? h('span', {
              style: {
                fontSize: 9, lineHeight: 1, marginTop: 1,
                padding: '1px 4px', borderRadius: 6,
                background: isPast ? 'rgba(220,38,38,.15)' : 'var(--dsw-alias-bg-layer-3)',
                color: isPast ? '#dc2626' : 'var(--dsw-alias-label-secondary)',
              },
            }, openN) : null,
          )
        })),
        h('div', { className: 'siyi-notes-list' },
          selDay ? h('div', {
            style: {
              padding: '6px 12px', fontSize: 11,
              color: 'var(--dsw-alias-label-tertiary)',
              borderBottom: '.5px solid var(--dsw-alias-border-l3)',
            },
          }, selDay.key + ' · ' + (selTasks.length ? selTasks.length + ' 项' : '无待办'))
            : h('div', {
              style: {
                padding: '6px 12px', fontSize: 11,
                color: 'var(--dsw-alias-label-tertiary)',
              },
            }, loading ? '读取中…' : '点日期查看当天待办'),
          (selDay ? selTasks : tasks.filter(t => !t.due)).map(t => h('div', {
            key: t.id, className: 'siyi-notes-item', onClick: () => onOpen(t.id),
          },
            h('div', {
              className: 'siyi-notes-item-title',
              style: t.status === 'done' ? { textDecoration: 'line-through', opacity: .55 } : null,
            }, t.title),
            h('div', { className: 'siyi-notes-item-meta' },
              h('span', null, ({ open: '未完成', done: '已完成', archived: '已归档' })[t.status] || t.status),
              t.due ? h('span', null, t.due) : h('span', null, '无截止'),
            ),
          )),
        ),
      )
    }

    // ── E2 待办视图 ─────────────────────────────────────────────────
    //
    // 增删改都在这里。新建走 save 接口（kind=task，status=open），
    // 完成是改 status 为 done，不删文件 —— 保留痕迹，月历上能显示已完成。
    function TaskView({ onBack, onOpen, toast }) {
      const [items, setItems] = useState([])
      const [loading, setLoading] = useState(true)
      const [showDone, setShowDone] = useState(false)
      const [draft, setDraft] = useState(null)   // { title, due }
      const [busy, setBusy] = useState(false)
      // SIYI-CALMERGE-20261122：月历状态（组件内，不新建子组件）
      const __today = new Date()
      const [calYm, setCalYm] = useState({ y: __today.getFullYear(), m: __today.getMonth() })
      const [selKey, setSelKey] = useState(null)

      const load = useCallback(async () => {
        setLoading(true)
        try {
          const r = await api('/tasks' + (showDone ? '?done=1' : ''))
          setItems(r.items || [])
        } catch (e) { toast('待办读取失败：' + e.message) }
        finally { setLoading(false) }
      }, [showDone])

      useEffect(() => { load() }, [load])

      const save = async (payload) => {
        if (busy) return
        setBusy(true)
        try {
          const r = await saveNote(payload)
          if (!r.ok) {
            if (r.code === 'CONFLICT') toast('任务已被其他端修改，请刷新后重试')
            else toast(r.error || '保存失败')
            return false   // SIYI-T0-20261121：失败不清除草稿
          }
          toast(payload.status === 'done' ? '已完成' : '已保存')
          await load()
          return true
        } catch (e) { toast('保存失败：' + e.message); return false }
        finally { setBusy(false) }
      }


      // SIYI-T2AUI-20261123：待办设真闹钟（due日期+用户输入时间，走设备桥）
      const setAlarm = async (t) => {
        if (!t.due) { toast('该待办没有截止日期，无法设闹钟'); return }
        const time = window.prompt('闹钟时间（24小时制 HH:mm）', '09:00')
        if (!time || !/^\d{1,2}:\d{2}$/.test(time)) { toast('时间格式应为 HH:mm'); return }
        const [hh, mm] = time.split(':')
        const ms = new Date(t.due + 'T' + String(hh).padStart(2,'0') + ':' + mm + ':00').getTime()
        if (!Number.isFinite(ms) || ms <= Date.now()) { toast('时间已过，请选未来时间'); return }
        if (busy) return
        setBusy(true)
        try {
          const r = await api('/alarm-set', { method: 'POST', body: JSON.stringify({ triggerAtMs: ms, title: t.title, text: '待办提醒：' + t.title }) })
          if (r && r.ok) toast('闹钟已设置：' + t.due + ' ' + time)
          else toast('设置失败：' + ((r && r.error) || '未知错误'))
        } catch (e) { toast('设置失败：' + e.message) }
        finally { setBusy(false) }
      }

      const todayKey = (() => {
        const d = new Date()
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0')
      })()

      const submitDraft = async () => {
        if (!draft || !draft.title.trim()) return
        // SIYI-T0-20261121：save 返回成功与否，失败保留草稿供重试
        const ok = await save({
          kind: 'task',
          title: draft.title.trim(),
          status: 'open',
          due: draft.due || null,
          body: draft.title.trim(),
        })
        if (ok) setDraft(null)
      }

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-editor-head' },
          h('button', { className: 'siyi-notes-btn siyi-btn-icon', onClick: onBack },
          I.back ? h(I.back, { size: 16 }) : null, h('span', null, '返回')),
          h('button', {
            className: 'siyi-notes-btn' + (showDone ? ' on' : ''),
            onClick: () => setShowDone(v => !v),
          }, showDone ? '含已完成' : '只看未完成'),
          h('button', {
            className: 'siyi-notes-btn',
            style: { marginLeft: 'auto' },
            onClick: () => setDraft({ title: '', due: selKey || '' }),   // SIYI-CALMERGE-20261122
          }, I.plus ? h(I.plus, { size: 15 }) : null, '待办'),
        ),
        // SIYI-CALMERGE-20261122：内嵌月历
        (() => {
          const first = new Date(calYm.y, calYm.m, 1)
          const cells = []
          for (let i = 0; i < first.getDay(); i++) cells.push(null)
          const dn = new Date(calYm.y, calYm.m + 1, 0).getDate()
          const cnt = {}
          for (const t of items) { if (t.due) { const k = String(t.due).slice(0, 10); cnt[k] = (cnt[k] || 0) + 1 } }
          for (let d = 1; d <= dn; d++) {
            const key = calYm.y + '-' + String(calYm.m + 1).padStart(2, '0') + '-' + String(d).padStart(2, '0')
            cells.push({ d, key })
          }
          while (cells.length % 7 !== 0) cells.push(null)
          const tk = __today.getFullYear() + '-' + String(__today.getMonth() + 1).padStart(2, '0') + '-' + String(__today.getDate()).padStart(2, '0')
          const W = ['日', '一', '二', '三', '四', '五', '六']
          return h('div', { style: { flex: 'none', borderBottom: '.5px solid var(--dsw-alias-border-l3)', padding: '4px 6px' } },
            h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, padding: '2px 0 4px' } },
              h('button', { className: 'siyi-notes-btn', onClick: () => setCalYm(v => v.m === 0 ? { y: v.y - 1, m: 11 } : { y: v.y, m: v.m - 1 }) }, '‹'),
              h('span', { style: { fontSize: 12, fontWeight: 600, minWidth: 84, textAlign: 'center' } }, calYm.y + '-' + String(calYm.m + 1).padStart(2, '0')),
              h('button', { className: 'siyi-notes-btn', onClick: () => setCalYm(v => v.m === 11 ? { y: v.y + 1, m: 0 } : { y: v.y, m: v.m + 1 }) }, '›'),
            ),
            h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', textAlign: 'center' } },
              W.map(w => h('div', { key: w, style: { fontSize: 9, color: 'var(--dsw-alias-label-tertiary)', padding: '1px 0' } }, w)),
            ),
            h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(7,1fr)' } },
              cells.map((c, i) => {
                if (!c) return h('div', { key: 'e' + i })
                const sel = selKey === c.key
                const today = c.key === tk
                return h('button', {
                  key: c.key,
                  onClick: () => setSelKey(sel ? null : c.key),
                  style: {
                    minHeight: 28, border: 'none', cursor: 'pointer', fontSize: 11, padding: 0,
                    background: sel ? 'var(--dsw-alias-bg-layer-3)' : 'transparent',
                    color: today ? 'var(--dsw-alias-text-link)' : 'var(--dsw-alias-label-primary)',
                    fontWeight: today ? 700 : 400, borderRadius: sel ? 6 : 0, position: 'relative',
                  },
                }, c.d, cnt[c.key] ? h('span', { style: { position: 'absolute', bottom: 1, left: '50%', transform: 'translateX(-50%)', width: 3, height: 3, borderRadius: '50%', background: 'var(--dsw-alias-label-secondary)' } }) : null)
              }),
            ),
          )
        })(),
        draft ? h('div', {
          style: {
            padding: '8px 10px', flex: 'none',
            borderBottom: '.5px solid var(--dsw-alias-border-l3)',
            display: 'flex', flexDirection: 'column', gap: 6,
          },
        },
          h('input', {
            className: 'siyi-notes-search',
            placeholder: '待办内容',
            autoFocus: true,
            value: draft.title,
            onChange: (e) => setDraft({ ...draft, title: e.target.value }),
            onKeyDown: (e) => { if (e.key === 'Enter') submitDraft() },
          }),
          h('div', { style: { display: 'flex', gap: 6, alignItems: 'center' } },
            h('input', {
              className: 'siyi-notes-search',
              type: 'date',
              value: draft.due,
              onChange: (e) => setDraft({ ...draft, due: e.target.value }),
            }),
            h('button', { className: 'siyi-notes-btn', onClick: submitDraft, disabled: busy }, '添加'),
            h('button', { className: 'siyi-notes-btn', onClick: () => setDraft(null) }, '取消'),
          ),
        ) : null,
        h('div', { className: 'siyi-notes-list' },
          loading ? h('div', { className: 'siyi-notes-empty' }, '读取中…')
            : items.length === 0 ? h('div', { className: 'siyi-notes-empty' }, '没有待办')
              : (selKey ? items.filter(t => (t.due || '').slice(0, 10) === selKey) : items).length === 0
                ? h('div', { className: 'siyi-notes-empty' }, '当天没有待办')
              : (selKey ? items.filter(t => (t.due || '').slice(0, 10) === selKey) : items).map(t => {
                const done = t.status === 'done'
                const overdue = !done && t.due && String(t.due).slice(0, 10) < todayKey
                return h('div', { key: t.id, className: 'siyi-notes-item' },
                  h('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 8 } },
                    h('button', {
                      onClick: () => save({ id: t.id, status: done ? 'open' : 'done', expectedRevision: t.revision }),   // SIYI-T0-20261121
                      disabled: busy,
                      style: {
                        flex: 'none', width: 30, height: 30, marginTop: 0,
                        borderRadius: '50%', cursor: 'pointer',
                        border: '.5px solid ' + (done ? 'var(--dsw-alias-label-tertiary)' : 'var(--dsw-alias-label-secondary)'),
                        background: done ? 'var(--dsw-alias-bg-layer-3)' : 'transparent',
                        color: 'var(--dsw-alias-label-secondary)', fontSize: 11, lineHeight: 1, padding: 0,
                      },
                    }, done ? '✓' : ''),
                    h('div', { style: { flex: 1, minWidth: 0 } },
                      h('div', {
                        className: 'siyi-notes-item-title',
                        onClick: () => onOpen(t.id),
                        style: done ? { textDecoration: 'line-through', opacity: .55 } : { cursor: 'pointer' },
                      }, t.title),
                      h('div', { className: 'siyi-notes-item-meta' },
                        t.due ? h('span', {
                          style: overdue ? { color: '#dc2626' } : null,
                        }, (overdue ? '逾期 ' : '') + t.due) : h('span', null, '无截止'),
                        (t.tags || []).slice(0, 2).map(x => h('span', { key: x, className: 'siyi-notes-tag' }, '#' + x)),
                        !done && t.due ? h('button', { className: 'siyi-task-alarm-btn', title: '设闹钟', 'aria-label': '设闹钟', onClick: (e) => { e.stopPropagation(); setAlarm(t) } }, I.alarm ? h(I.alarm, { size: 14 }) : '⏰') : null,   // SIYI-T2AUI-20261123
                      ),
                    ),
                  ),
                )
              }),
        ),
      )
    }

    // ── 主容器 ──────────────────────────────────────────────────────
    function NotesApp() {
      const [view, setView] = useState('list')
      const [id, setId] = useState(null)
      const [toastMsg, setToastMsg] = useState('')
      const toastTimer = useRef(null)

      const toast = useCallback((m) => {
        setToastMsg(m)
        if (toastTimer.current) clearTimeout(toastTimer.current)
        toastTimer.current = setTimeout(() => setToastMsg(''), 2200)
      }, [])

      useEffect(() => { ensureCss() }, [])

      const open = (nid) => { setId(nid); setView('edit') }
      const back = () => { setView('list'); setId(null) }
      const graph = () => setView('graph')
      const month = () => setView('month')
      const tasks = () => setView('tasks')
      const [folder, setFolder] = useState('')   // SIYI-FOLDER-20261126：当前文件夹（新建笔记继承）

      let body
      if (view === 'list') {
        body = h(ListView, {
          onOpen: open,
          onGraph: graph,
          onMonth: month,
          onTasks: tasks,
          onNew: () => { setId(null); setView('edit') },
          folder, setFolder,
          toast,
        })
      } else if (view === 'graph') {
        body = h(GraphView, { onBack: back, onOpen: open, toast })
      } else if (view === 'month') {
        body = h(MonthView, { onBack: back, onOpen: open, toast })
      } else if (view === 'tasks') {
        body = h(TaskView, { onBack: back, onOpen: open, toast })
      } else {
        body = h(EditorView, { id, onBack: back, toast, folder })   // SIYI-FOLDER-20261126
      }

      return h('div', { style: { position: 'relative', height: '100%', minHeight: 0 } },
        body,
        // SIYI-OTOAST-20261119：官方 Toast（受控，onDone 时清除）
        toastMsg
          ? (OToast
              ? h(OToast, {
                  key: toastMsg + Date.now(),
                  text: toastMsg,
                  holdMs: 2200,
                  onDone: () => setToastMsg(''),
                })
              : h('div', { className: 'siyi-notes-toast' }, toastMsg))
          : null,
      )
    }

    // 右侧栏空白处的入口卡片
    function GuideCard({ onOpen }) {
      return h('button', {
        onClick: onOpen,
        style: {
          display: 'flex', alignItems: 'center', gap: 14, width: '100%',
          minHeight: 56, padding: '14px 20px', boxSizing: 'border-box',
          border: '.5px solid var(--dsw-alias-border-l3)', borderRadius: 12,
          background: 'var(--dsw-alias-bg-layer-1)', cursor: 'pointer',
          textAlign: 'left', color: 'var(--dsw-alias-label-primary)',
        },
      },
        I.note ? h(I.note, { size: 22 }) : h('span', { style: { fontSize: 22 } }, '📝'),
        h('span', { style: { display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 } },
          h('span', { style: { fontSize: 14, lineHeight: 1.4 } }, '我的笔记'),
          h('span', { style: { fontSize: 11, lineHeight: 1.4, color: 'var(--dsw-alias-label-tertiary)' } },
            '列表、编辑、知识图谱'),
        ),
      )
    }

    const TAB_ID = 'siyi-notes'

    // ── 插件入口 ────────────────────────────────────────────────────
    const inject = ['slots', 'layout']   // SIYI-MAINPANEL-20261128 + SIYI-PLANB-20261128

    function apply(ctx) {
      // SIYI-MAINPANEL-20261128：作为全局主面板注册（不依赖会话），左侧栏有独立按钮 + "更多"菜单条目

      // 主面板内容：NotesApp 直接作为 main 面板渲染
      ctx.effect(() => ctx.slots.inject('main', () => ctx.slots.register({
        name: 'main',
        key: TAB_ID,
        
        inject: () => ({}),
      }, NotesApp)), 'siyi-notes.main')

      // SIYI-PLANB-20261128：只在"更多"菜单加入口，不占常驻区（不影响会话列表）
      let P
      try { P = require('@deepseek-ai/dsh-client-ui-primitives') || {} } catch {}
      const MenuItem = P.MenuItemButton
      const IconFolder = P.IconFolderOpenOutlineRegular
      ctx.effect(() => ctx.slots.inject('sidebar.panellist.actions', () => ctx.slots.register({
        name: 'sidebar.panellist.actions',
        id: TAB_ID + '-menu',
        inject: () => ({}),
      }, ({ onDismiss }) => {
        if (!MenuItem) return null
        return h(MenuItem, {
          icon: IconFolder ? h(IconFolder, { size: 16 }) : null,
          onSelect: () => { onDismiss && onDismiss(); try { ctx.layout.selectPanel(TAB_ID) } catch {} },
        }, '我的笔记')
      })), 'siyi-notes.menu')


    }

    exports.apply = apply
    exports.inject = inject
    return module.exports
  },
})
