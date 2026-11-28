// 思忆集 · PC 入口（浏览器半身）SIYI-PCPUI-20261128
// 全局主面板：显示通过 Tailscale 连接的 PC 端 DSH 会话列表。
// 数据走本机 /siyi-pc/*（由 siyi-pc-bridge.js 提供，底层连 PC DSH）。
// 结构完全对齐 dsh-client-ui-siyi-notes：main 面板 + "更多"菜单入口（无常驻图标）。
window.__ModuleLoader__.load({
  id: 'dsh-client-ui-siyi-pc',
  factory: (require) => {
    var module = { exports: {} }
    var exports = module.exports
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' })
    const react = require('react')
    const { useState, useCallback, useEffect, useMemo, useRef } = react
    const h = react.createElement
    let P = {}
    try { P = require('@deepseek-ai/dsh-client-ui-primitives') || {} } catch {}
    const MenuItem = P.MenuItemButton
    const IconDesktop = P.IconDeviceDesktopOutlineRegular || P.IconMonitorOutlineRegular || P.IconFolderOpenOutlineRegular
    const TAB_ID = 'siyi-pc'

    // SIYI-PCOFFICIAL2-20261128: 严格按官方 ConversationRoot + MessageItem 源码复刻
    const CSS = `
/* 严格取自官方 bundle：ConversationRoot.module.css + MessageItem.module.css */
.siyi-pc-root{background:var(--dsw-alias-bg-base);flex-direction:column;min-width:0;height:100%;display:flex;position:relative}
/* header */
.siyi-pc-header{box-sizing:border-box;border-bottom:.5px solid var(--dsw-alias-border-l3);flex:none;grid-template-columns:auto minmax(0,1fr);min-height:76px;padding:10px 28px 0 20px;display:grid}
.siyi-pc-headerLeading{flex:none;grid-area:1/1;align-items:center;gap:8px;display:flex}
.siyi-pc-titleRow{min-height:30px;grid-column:2;align-items:center;gap:0;display:flex;container-type:inline-size}
.siyi-pc-titleCluster{flex:1;align-items:center;gap:10px;min-width:0;display:flex}
.siyi-pc-crumbs{white-space:nowrap;align-items:center;gap:4px;min-width:0;display:flex;overflow:hidden}
.siyi-pc-crumb{border-radius:var(--dsw-radius-md);max-width:220px;color:var(--dsw-alias-label-tertiary);text-overflow:ellipsis;white-space:nowrap;cursor:pointer;background:0 0;border:none;padding:4px 8px;font-size:14px;line-height:20px;display:inline-block;overflow:hidden}
.siyi-pc-crumbCurrent{color:var(--dsw-alias-label-primary);cursor:default;font-weight:500}
.siyi-pc-headerActions{flex:none;align-items:center;gap:8px;display:flex}
.siyi-pc-headerUtilities{flex:none;align-items:center;gap:8px;margin-left:20px;display:flex}
.siyi-pc-headerCorner{flex:none;align-items:center;margin-left:8px;margin-right:-16px;display:flex}
.siyi-pc-tabs{z-index:1;box-sizing:border-box;flex-wrap:wrap;grid-column:1/-1;align-items:flex-start;gap:16px 20px;width:100%;min-width:0;margin-top:10px;padding-left:8px;display:flex;position:relative}
.siyi-pc-viewTabs{flex-wrap:wrap;gap:32px;min-width:0;min-height:25px;display:flex}
.siyi-pc-tab{min-height:25px;color:var(--dsw-alias-label-tertiary);cursor:pointer;background:0 0;border:none;padding:0 0 9px;font-size:13px;font-weight:500;line-height:16px;position:relative}
.siyi-pc-tab.active{color:var(--dsw-alias-state-business-primary)}
.siyi-pc-tabLabel{text-overflow:ellipsis;white-space:nowrap;max-width:88px;display:block;overflow:hidden}
/* body */
.siyi-pc-body{--dsh-chat-content-width:clamp(680px, calc(100vw * .64), 920px);--dsh-composer-card-max-width:calc(var(--dsh-chat-content-width) + 32px);--dsh-composer-side-clearance:16px;--dsh-composer-dock-inset:8px;flex-direction:column;flex:1;min-height:0;display:flex;position:relative}
.siyi-pc-scrollBody{scrollbar-gutter:stable;flex-direction:column;flex:1;min-height:0;margin-right:2px;display:flex;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
.siyi-pc-msgs{width:100%;max-width:var(--dsh-chat-content-width);margin:0 auto;padding:16px 28px;display:flex;flex-direction:column;gap:12px}
/* 消息：官方 MessageItem 三层结构 */
.siyi-pc-userRow{flex-direction:column;align-items:flex-end;gap:6px;display:flex}
.siyi-pc-userStack{min-width:0;max-width:min(calc(var(--dsh-chat-content-width,748px) * .702), 82%);flex-direction:column;align-items:flex-end;gap:8px;display:flex}
.siyi-pc-bubble{background:var(--dsw-specific-bubble);border-radius:var(--dsw-radius-xl);max-width:100%;font-size:var(--dsh-content-font-size,14px);line-height:calc(22px + var(--dsh-content-font-delta,0px));color:var(--dsw-alias-label-primary);white-space:pre-wrap;word-break:break-word;padding:10px 16px}
.siyi-pc-assistant{max-width:100%;font-size:var(--dsh-content-font-size,14px);line-height:calc(22px + var(--dsh-content-font-delta,0px));color:var(--dsw-alias-label-primary);white-space:pre-wrap;word-break:break-word}
/* composer：官方渐变 + 窄栏 */
.siyi-pc-composerSeat{--dsh-composer-text-max-height:336px;flex-direction:column;flex:none;display:flex;z-index:7;background:linear-gradient(180deg,color-mix(in srgb,var(--dsw-alias-bg-base) 0%,transparent) 0px,var(--dsw-alias-bg-base) 36px);position:sticky;bottom:0;padding:0 16px 8px}
.siyi-pc-composerHero{width:min(calc(var(--dsh-composer-card-max-width) + 2 * var(--dsh-composer-side-clearance)),100%);z-index:1;align-self:center;gap:8px;display:flex;align-items:flex-end}
.siyi-pc-input{flex:1;min-width:0;min-height:38px;max-height:336px;padding:9px 14px;border:.5px solid var(--dsw-alias-border-l3);border-radius:var(--dsw-radius-xl);background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);font-size:14px;line-height:1.5;outline:none;font-family:inherit;resize:none;touch-action:manipulation;-webkit-appearance:none}
.siyi-pc-send{flex:none;width:38px;height:38px;border-radius:50%;border:none;display:flex;align-items:center;justify-content:center;background:var(--dsw-alias-bg-layer-3);color:var(--dsw-alias-label-tertiary);cursor:default;touch-action:manipulation}
.siyi-pc-send.on{background:var(--dsw-alias-state-business-primary);color:#fff;cursor:pointer}
/* 图标按钮 */
.siyi-pc-iconBtn{width:36px;height:36px;border:none;background:transparent;border-radius:var(--dsw-radius-md);color:var(--dsw-alias-label-secondary);display:flex;align-items:center;justify-content:center;cursor:pointer;flex:none;font-size:18px;touch-action:manipulation}
.siyi-pc-iconBtn:active{background:var(--dsw-alias-bg-layer-2)}
.siyi-pc-pill{padding:5px 11px;border-radius:999px;border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-secondary);font-size:11px;font-weight:600;white-space:nowrap;cursor:pointer}
/* 空态 */
.siyi-pc-empty{padding:48px 16px;text-align:center;color:var(--dsw-alias-label-tertiary);font-size:13px;line-height:1.7}
/* 列表 */
.siyi-pc-list{flex:1;overflow-y:auto;min-height:0;overscroll-behavior:contain}
.siyi-pc-row{display:flex;align-items:flex-start;gap:10px;padding:12px 20px;border-bottom:.5px solid var(--dsw-alias-border-l3);cursor:pointer;touch-action:manipulation}
.siyi-pc-row:active{background:var(--dsw-alias-bg-layer-2)}
.siyi-pc-dot{width:6px;height:6px;border-radius:50%;background:var(--dsw-alias-state-success,#34C759);flex:none;margin-top:8px}
.siyi-pc-rowBody{flex:1;min-width:0}
.siyi-pc-rowTitle{font-size:14px;font-weight:500;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.siyi-pc-rowMeta{font-size:11px;color:var(--dsw-alias-label-tertiary);margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-pc-rowTime{flex:none;font-size:11px;color:var(--dsw-alias-label-tertiary);margin-top:2px}
/* 模型 Sheet */
.siyi-pc-sheet{position:fixed;inset:0;z-index:2000;display:flex;align-items:flex-end;justify-content:center;background:rgba(0,0,0,.38)}
.siyi-pc-sheetPanel{width:100%;max-width:400px;max-height:76vh;background:var(--dsw-alias-bg-base);border-radius:16px 16px 0 0;display:flex;flex-direction:column;padding-bottom:calc(env(safe-area-inset-bottom,0px) + 12px)}
.siyi-pc-sheetHead{display:flex;align-items:center;justify-content:space-between;padding:14px 16px 8px;font-size:15px;font-weight:600}
.siyi-pc-sheetClose{border:none;background:none;color:var(--dsw-alias-state-business-primary);font-size:14px;cursor:pointer;padding:4px}
.siyi-pc-sheetBody{flex:1;overflow-y:auto;padding:0 12px;overscroll-behavior:contain}
.siyi-pc-mgroupName{font-size:11px;color:var(--dsw-alias-label-tertiary);padding:8px 4px 4px}
.siyi-pc-mrow{display:flex;align-items:center;gap:8px;min-height:44px;padding:0 10px;border-radius:8px;cursor:pointer;touch-action:manipulation;font-size:14px}
.siyi-pc-mrow:active{background:var(--dsw-alias-bg-layer-2)}
.siyi-pc-mrow.on{background:var(--dsw-alias-bg-layer-3)}
.siyi-pc-mname{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-pc-mcheck{color:var(--dsw-alias-state-business-primary);font-size:14px}
`;
    try { if (typeof document !== 'undefined' && !document.querySelector('style[data-plugin=syipc]')) { const el=document.createElement('style'); el.setAttribute('data-plugin','syipc'); el.textContent=CSS; document.head.appendChild(el) } } catch {}

    async function api(path) {
      const r = await fetch('/siyi-pc' + path, { signal: AbortSignal.timeout(20000) })
      return r.json()
    }

    function PcApp() {
      const [items, setItems] = useState([])
      const [loading, setLoading] = useState(true)
      const [error, setError] = useState('')
      const [q, setQ] = useState('')
      const [status, setStatus] = useState(null)
      const [sel, setSel] = useState(null)
      const [history, setHistory] = useState([])
      const [hLoading, setHLoading] = useState(false)
      const [input, setInput] = useState('')
      const [sending, setSending] = useState(false)
      const [modelSheet, setModelSheet] = useState(false)
      const [catalog, setCatalog] = useState(null)
      const [curModel, setCurModel] = useState(null)
      const [tab, setTab] = useState('chat')
      const msgRef = useRef(null)

      const load = useCallback(async () => {
        setLoading(true); setError('')
        try {
          const [st, r] = await Promise.all([api('/status'), api('/sessions')])
          setStatus(st)
          if (!r.ok) throw new Error(r.error || '读取失败')
          setItems(r.value?.items || [])
        } catch (e) { setError(e.message || '读取失败') }
        finally { setLoading(false) }
      }, [])
      useEffect(() => { load() }, [load])

      const openSession = useCallback(async (row) => {
        setSel(row); setHLoading(true); setHistory([]); setError('')
        try {
          const r = await api('/history?sessionId=' + encodeURIComponent(row.sessionId) + '&limit=50')
          if (!r.ok) throw new Error(r.error || '历史读取失败')
          const recs = r.value?.records || []
          // 官方不显示系统注入上下文：source.kind 过滤
          const HIDE = new Set(['runtime-context','time-context','system','context'])
          const msgs = recs.filter(x => ['user/message','assistant/message'].includes(x.event?.type)).map(m => {
            const ev = m.event, isUser = ev.type === 'user/message'
            if (isUser && HIDE.has(String(ev.data?.source?.kind || 'user'))) return null
            const parts = isUser ? (ev.data?.content || []) : (ev.data?.message?.content || [])
            const text = parts.filter(c => c && c.type === 'text').map(c => c.text).filter(Boolean).join('\n')
            return { role: isUser ? 'user' : 'assistant', text }
          }).filter(m => m && m.text && m.text.trim())
          setHistory(msgs)
          const ms = row.projections?.values?.modelSelection
          setCurModel(ms?.next || ms?.lastUsed || null)
        } catch (e) { setError('历史: ' + e.message) }
        finally { setHLoading(false) }
      }, [])

      useEffect(() => {
        const el = msgRef.current
        if (el) el.scrollTop = el.scrollHeight
      }, [history, hLoading])

      const pollHistory = useCallback(async (row, tries = 12) => {
        for (let i = 0; i < tries; i++) {
          await new Promise(r => setTimeout(r, 1500))
          try {
            const r = await api('/history?sessionId=' + encodeURIComponent(row.sessionId) + '&limit=50')
            if (!r.ok) continue
            const recs = r.value?.records || []
            const last = [...recs].reverse().find(x => x.event?.type === 'assistant/message')
            const newText = last ? ((last.event.data?.message?.content || []).filter(c => c.type === 'text').map(c => c.text).join('\n')) : ''
            const prev = history.filter(m => m.role === 'assistant').slice(-1)[0]
            if (newText && (!prev || newText !== prev.text)) { await openSession(row); return }
          } catch {}
        }
      }, [history, openSession])

      const send = useCallback(async () => {
        const text = input.trim()
        if (!text || !sel || sending) return
        setSending(true); setError('')
        try {
          const r = await fetch('/siyi-pc/command', {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ text, sessionId: sel.sessionId }),
          }).then(x => x.json())
          if (!r.ok) throw new Error(r.error || '发送失败')
          setHistory(hst => [...hst, { role: 'user', text }])
          setInput('')
          pollHistory(sel).catch(() => {})
        } catch (e) { setError('发送: ' + e.message) }
        finally { setSending(false) }
      }, [input, sel, sending, pollHistory])

      const createSession = useCallback(async () => {
        try {
          const r = await fetch('/siyi-pc/create', {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ cwd: sel ? sel.cwd : '' }),
          }).then(x => x.json())
          if (!r.ok) throw new Error(r.error || '创建失败')
          await load()
          const newId = r.value?.sessionId
          if (newId) {
            const found = (items || []).find(x => x.sessionId === newId)
            setSel(found || { sessionId: newId, cwd: '', projections: { values: { title: '新会话' } } })
            setHistory([])
          }
        } catch (e) { setError('新建: ' + e.message) }
      }, [sel, load, items])

      const openModelSheet = useCallback(async () => {
        setModelSheet(true)
        try { const r = await api('/models'); if (r.ok) setCatalog(r.value) }
        catch (e) { setError('模型: ' + e.message) }
      }, [])

      const pickModel = useCallback(async (provider, model) => {
        if (!sel) return
        setModelSheet(false)
        try {
          const r = await fetch('/siyi-pc/select-model', {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ sessionId: sel.sessionId, provider, model }),
          }).then(x => x.json())
          if (!r.ok) throw new Error(r.error || '切换失败')
          setCurModel({ provider, model })
          await openSession(sel)
        } catch (e) { setError('模型: ' + e.message) }
      }, [sel, openSession])

      const filtered = useMemo(() => {
        const arr = (items || []).slice()
        const kw = q.trim().toLowerCase()
        if (kw) return arr.filter(x => {
          const t = (x.projections?.values?.title || '').toLowerCase()
          const c = (x.cwd || '').toLowerCase()
          return t.includes(kw) || c.includes(kw) || (x.sessionId || '').includes(kw)
        })
        return arr.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
      }, [items, q])

      // 用户消息：官方三层 DOM
      const renderMsg = (m, i) => m.role === 'user'
        ? h('div', { key: i, className: 'siyi-pc-userRow' },
            h('div', { className: 'siyi-pc-userStack' },
              h('div', { className: 'siyi-pc-bubble' }, m.text.slice(0, 8000))))
        : h('div', { key: i, className: 'siyi-pc-assistant' }, m.text.slice(0, 8000))

      if (modelSheet) {
        const groups = catalog?.groups || []
        return h('div', { className: 'siyi-pc-sheet', onClick: () => setModelSheet(false) },
          h('div', { className: 'siyi-pc-sheetPanel', onClick: e => e.stopPropagation() },
            h('div', { className: 'siyi-pc-sheetHead' },
              h('span', null, '选择模型'),
              h('button', { className: 'siyi-pc-sheetClose', onClick: () => setModelSheet(false) }, '关闭')),
            h('div', { className: 'siyi-pc-sheetBody' },
              groups.map(g => h('div', { key: g.id },
                h('div', { className: 'siyi-pc-mgroupName' }, g.name || g.id),
                (g.models || []).map(m => {
                  const on = curModel && curModel.provider === g.id && curModel.model === m.id
                  return h('div', { key: m.id, className: 'siyi-pc-mrow' + (on ? ' on' : ''), onClick: () => pickModel(g.id, m.id) },
                    h('span', { className: 'siyi-pc-mname' }, m.name || m.id),
                    on ? h('span', { className: 'siyi-pc-mcheck' }, '✓') : null)
                }))),
              groups.length === 0 ? h('div', { className: 'siyi-pc-empty' }, '加载中…') : null)))
      }

      if (sel) {
        const meta = sel.projections?.values || {}
        const title = meta.title || '新会话'
        const modelLabel = curModel ? (curModel.model || '') : '模型'
        return h('div', { className: 'siyi-pc-root' },
          h('div', { className: 'siyi-pc-header' },
            h('div', { className: 'siyi-pc-headerLeading' },
              h('button', { className: 'siyi-pc-iconBtn', onClick: () => setSel(null), title: '返回' }, '‹'),
              h('button', { className: 'siyi-pc-iconBtn', onClick: createSession, title: '新建会话' }, '＋')),
            h('div', { className: 'siyi-pc-titleRow' },
              h('div', { className: 'siyi-pc-titleCluster' },
                h('nav', { className: 'siyi-pc-crumbs' },
                  h('span', { className: 'siyi-pc-crumb siyi-pc-crumbCurrent' }, title)),
                h('div', { className: 'siyi-pc-headerActions' },
                  h('button', { className: 'siyi-pc-pill', onClick: openModelSheet, title: '切换模型' }, modelLabel)),
                h('div', { className: 'siyi-pc-headerUtilities' },
                  h('button', { className: 'siyi-pc-iconBtn', onClick: load, title: '刷新' }, '↻'))),
              h('div', { className: 'siyi-pc-headerCorner' })),
            h('div', { className: 'siyi-pc-tabs' },
              h('div', { className: 'siyi-pc-viewTabs', role: 'tablist' },
                h('button', { role: 'tab', 'aria-selected': tab === 'chat', className: 'siyi-pc-tab' + (tab === 'chat' ? ' active' : ''), onClick: () => setTab('chat') }, h('span', { className: 'siyi-pc-tabLabel' }, '对话')),
                h('button', { role: 'tab', 'aria-selected': tab === 'traj', className: 'siyi-pc-tab' + (tab === 'traj' ? ' active' : ''), onClick: () => setTab('traj') }, h('span', { className: 'siyi-pc-tabLabel' }, '轨迹'))))),
          h('div', { className: 'siyi-pc-body' },
            h('div', { className: 'siyi-pc-scrollBody', ref: msgRef },
              h('div', { className: 'siyi-pc-msgs' },
                hLoading ? h('div', { className: 'siyi-pc-empty' }, '加载历史…') : null,
                error ? h('div', { className: 'siyi-pc-empty', style: { color: 'var(--dsw-alias-state-error-primary)' } }, error) : null,
                tab === 'chat' ? history.map(renderMsg) : h('div', { className: 'siyi-pc-empty' }, '轨迹视图'),
                history.length === 0 && !hLoading && tab === 'chat' ? h('div', { className: 'siyi-pc-empty' }, '还没有消息') : null)),
            h('div', { className: 'siyi-pc-composerSeat' },
              h('div', { className: 'siyi-pc-composerHero' },
                h('textarea', {
                  className: 'siyi-pc-input', placeholder: '给 PC 发指令…', value: input, rows: 1,
                  onChange: e => setInput(e.target.value),
                  onKeyDown: e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } },
                }),
                h('button', { className: 'siyi-pc-send' + (input.trim() ? ' on' : ''), onClick: send, disabled: sending || !input.trim() }, '↑')))),
        )
      }

      // 列表页
      return h('div', { className: 'siyi-pc-root' },
        h('div', { className: 'siyi-pc-header' },
          h('div', { className: 'siyi-pc-headerLeading' },
            h('button', { className: 'siyi-pc-iconBtn', onClick: load, title: '刷新' }, '↻'),
            h('button', { className: 'siyi-pc-iconBtn', onClick: createSession, title: '新建会话' }, '＋')),
          h('div', { className: 'siyi-pc-titleRow' },
            h('div', { className: 'siyi-pc-titleCluster' },
              h('nav', { className: 'siyi-pc-crumbs' },
                h('span', { className: 'siyi-pc-crumb siyi-pc-crumbCurrent' }, 'AI 助手')),
              h('div', { className: 'siyi-pc-headerActions' },
                h('button', { className: 'siyi-pc-pill', onClick: openModelSheet }, '模型')),
              h('div', { className: 'siyi-pc-headerUtilities' })),
            h('div', { className: 'siyi-pc-headerCorner' }))),
        h('div', { className: 'siyi-pc-body' },
          h('div', { className: 'siyi-pc-scrollBody' },
            h('div', { style: { width: '100%', maxWidth: 'var(--dsh-chat-content-width)', margin: '0 auto', padding: '16px 28px' } },
              h('input', {
                className: 'siyi-pc-input', placeholder: '搜索会话', value: q,
                onChange: e => setQ(e.target.value),
                style: { width: '100%', borderRadius: 'var(--dsw-radius-xl)' },
              }),
              h('div', { style: { padding: '8px 0 4px', fontSize: 11, color: 'var(--dsw-alias-label-tertiary)' } },
                status && status.ok ? ('已连接 PC · ' + (items.length || 0) + ' 个会话') : (status?.error || '未连接 PC')),
              error ? h('div', { className: 'siyi-pc-empty', style: { color: 'var(--dsw-alias-state-error-primary)' } }, error) : null,
              loading ? h('div', { className: 'siyi-pc-empty' }, '加载中…') : null,
              filtered.map(row => {
                const m = row.projections?.values || {}
                const lp = m.sessionListMetadata?.lastPromptAt
                const d = lp ? new Date(lp) : null
                const time = d ? (d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) + ' ' + d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })) : ''
                return h('div', { key: row.sessionId, className: 'siyi-pc-row', onClick: () => openSession(row) },
                  row.running ? h('div', { className: 'siyi-pc-dot' }) : null,
                  h('div', { className: 'siyi-pc-rowBody' },
                    h('div', { className: 'siyi-pc-rowTitle' }, m.title || '新会话'),
                    h('div', { className: 'siyi-pc-rowMeta' }, row.cwd || '')),
                  h('div', { className: 'siyi-pc-rowTime' }, time))
              })))),
      )
    }

    const inject = ['slots', 'layout']
    function apply(ctx) {
      // main 面板
      ctx.effect(() => ctx.slots.inject('main', () => ctx.slots.register({
        name: 'main', key: TAB_ID, inject: () => ({}),
      }, PcApp)), 'siyi-pc.main')
      // "更多"菜单入口（无常驻图标）
      ctx.effect(() => ctx.slots.inject('sidebar.panellist.actions', () => ctx.slots.register({
        name: 'sidebar.panellist.actions', id: TAB_ID + '-menu', inject: () => ({}),
      }, ({ onDismiss }) => {
        if (!MenuItem) return null
        return h(MenuItem, {
          icon: IconDesktop ? h(IconDesktop, { size: 16 }) : null,
          onSelect: () => { onDismiss && onDismiss(); try { ctx.layout.selectPanel(TAB_ID) } catch {} },
        }, 'PC 入口')
      })), 'siyi-pc.menu')
    }

    Object.defineProperty(exports, '__esModule', { value: true })
    exports.name = 'dsh-client-ui-siyi-pc'
    exports.apply = apply
    exports.inject = inject
    return module.exports
  },
})
