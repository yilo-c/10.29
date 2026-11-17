// 思忆集 · 笔记面板（浏览器半身）D2/D3/D4
//
// 一个右侧栏页面，内含三个视图：
//   列表  D2：搜索、按类型筛选、按更新时间排
//   编辑  D3：大标题 + 正文 + 标签，仿 Redmi 笔记的排布
//   图谱  D4：标题成节点、双链成边，纯 SVG 无 d3
//
// 数据全部走同源 HTTP：/siyi-vault/{list,get,save,delete,graph,tasks,stats}
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
  background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);
  font-size:14px;line-height:1.6;
  -webkit-tap-highlight-color:transparent}
.siyi-notes-bar{display:flex;align-items:center;gap:8px;padding:8px 10px;flex:none;
  border-bottom:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-search{flex:1;min-width:0;height:38px;padding:0 12px;border-radius:10px;
  border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);
  color:var(--dsw-alias-label-primary);font-size:14px;outline:none;
  -webkit-appearance:none}
.siyi-notes-search:focus{border-color:var(--dsw-alias-border-l1)}
.siyi-notes-btn{min-height:36px;padding:0 12px;border-radius:10px;flex:none;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center;
  border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);
  color:var(--dsw-alias-label-primary);font-size:14px;
  touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.siyi-notes-btn:active{background:var(--dsw-alias-bg-layer-3)}
.siyi-notes-btn.on{background:var(--dsw-alias-bg-layer-3);font-weight:600}
.siyi-notes-tabs{display:flex;gap:8px;padding:8px 10px;flex:none;overflow-x:auto;
  border-bottom:.5px solid var(--dsw-alias-border-l3);
  scrollbar-width:none;overscroll-behavior-x:contain}
.siyi-notes-tabs::-webkit-scrollbar{display:none}
.siyi-notes-list{flex:1;overflow-y:auto;min-height:0;padding:4px 0;
  overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
.siyi-notes-item{padding:12px;min-height:52px;box-sizing:border-box;cursor:pointer;
  border-bottom:.5px solid var(--dsw-alias-border-l3);touch-action:manipulation}
.siyi-notes-item:active{background:var(--dsw-alias-bg-layer-2)}
.siyi-notes-item-title{font-size:15px;font-weight:500;margin-bottom:4px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-notes-item-meta{font-size:12px;color:var(--dsw-alias-label-tertiary);
  display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.siyi-notes-tag{font-size:11px;padding:2px 7px;border-radius:5px;
  background:var(--dsw-alias-bg-layer-3);color:var(--dsw-alias-label-secondary)}
.siyi-notes-empty{padding:48px 24px;text-align:center;
  color:var(--dsw-alias-label-tertiary);font-size:14px;line-height:1.7}
.siyi-notes-editor{flex:1;display:flex;flex-direction:column;min-height:0;overflow:hidden}
.siyi-notes-editor-head{display:flex;align-items:center;gap:8px;padding:8px 10px;flex:none;
  border-bottom:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-title-input{width:100%;border:none;outline:none;background:transparent;
  color:var(--dsw-alias-label-primary);font-size:20px;font-weight:600;
  padding:14px 16px 8px;line-height:1.35}
.siyi-notes-body-input{width:100%;flex:1;border:none;outline:none;background:transparent;
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
    function ListView({ onOpen, onGraph, onMonth, onTasks, onNew, toast }) {
      const [items, setItems] = useState([])
      const [loading, setLoading] = useState(true)
      const [q, setQ] = useState('')
      const [kind, setKind] = useState('')
      const [stats, setStats] = useState(null)

      const load = useCallback(async () => {
        setLoading(true)
        try {
          const p = {}
          if (q.trim()) p.q = q.trim()
          if (kind) p.kind = kind
          if (kind === 'task') {
            const t = await getTasks()
            setItems((t.items || []).map(x => ({ ...x, updated: x.updated || x.due })))
          } else {
            const r = await listNotes(p)
            setItems(r.items || [])
          }
          const s = await getStats()
          setStats(s)
        } catch (e) {
          toast('读取失败：' + e.message)
          setItems([])
        } finally { setLoading(false) }
      }, [q, kind])

      useEffect(() => { load() }, [load])

      const kinds = [['', '全部'], ['note', '笔记'], ['task', '待办'], ['source', '资料'], ['daily', '日记'], ['moc', '索引']]

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-bar' },
          h('input', {
            className: 'siyi-notes-search', placeholder: '搜索标题或标签…',
            value: q, onChange: (e) => setQ(e.target.value),
          }),
          h('button', { className: 'siyi-notes-btn', onClick: onNew }, '新建'),
          h('button', { className: 'siyi-notes-btn', onClick: onMonth, title: '月历' }, '日历'),
          h('button', { className: 'siyi-notes-btn', onClick: onTasks, title: '待办清单' }, '待办'),
          h('button', { className: 'siyi-notes-btn', onClick: onGraph, title: '知识图谱' }, '图谱'),
        ),
        h('div', { className: 'siyi-notes-tabs' },
          kinds.map(([k, label]) => h('button', {
            key: k || 'all',
            className: 'siyi-notes-btn' + (kind === k ? ' on' : ''),
            onClick: () => setKind(k),
          }, label)),
          stats ? h('span', {
            className: 'siyi-notes-btn',
            style: { cursor: 'default', opacity: .7, marginLeft: 'auto', whiteSpace: 'nowrap' },
          }, '共 ' + stats.total + ' 篇') : null,
        ),
        h('div', { className: 'siyi-notes-list' },
          loading ? h('div', { className: 'siyi-notes-empty' }, '读取中…')
            : items.length === 0 ? h('div', { className: 'siyi-notes-empty' }, q ? '没有匹配的笔记' : '还没有笔记，点「新建」开始')
              : items.map(it => h('div', {
                  key: it.id, className: 'siyi-notes-item', onClick: () => onOpen(it.id),
                },
                h('div', { className: 'siyi-notes-item-title' }, it.title || '(无标题)'),
                h('div', { className: 'siyi-notes-item-meta' },
                  h('span', null, kindLabel(it.kind)),
                  it.due ? h('span', { style: { color: 'var(--dsw-alias-label-warning, #d97706)' } }, '截止 ' + it.due) : null,
                  h('span', null, fmtTime(it.updated)),
                  (it.tags || []).slice(0, 2).map(t => h('span', { key: t, className: 'siyi-notes-tag' }, '#' + t)),
                ),
              )),
        ),
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
    function EditorView({ id, onBack, toast }) {
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
      const bodyRef = useRef(null)
      const historyRef = useRef({ stack: [], index: -1 })
      const skipHistory = useRef(false)

      // 是不是「阅读型」笔记：RSS 导入的 source，或正文里有 HTML 标签
      const isSourceLike = (meta, text) =>
        (meta && meta.kind === 'source') || /<[a-zA-Z][^>]*>/.test(text || '')

      useEffect(() => {
        let alive = true
        if (!id) {
          setNote(null); setTitle(''); setBody(''); setTags('')
          setDirty(false); setMode('edit'); setPinnedTop(false)
          historyRef.current = { stack: [''], index: 0 }
          return
        }
        getNote(id).then(g => {
          if (!alive) return
          if (!g.ok) { toast('笔记不存在'); onBack(); return }
          setNote(g)
          setTitle(g.meta.title || '')
          setBody(g.body || '')
          setTags((g.meta.tags || []).join(', '))
          setPinnedTop(g.meta.status === 'pinned')
          // 阅读型笔记默认进阅读态，避免一进来就看到满屏 HTML 源码
          setMode(isSourceLike(g.meta, g.body) ? 'read' : 'edit')
          setDirty(false)
          historyRef.current = { stack: [g.body || ''], index: 0 }
        }).catch(e => alive && toast('读取失败：' + e.message))
        return () => { alive = false }
      }, [id])

      const doSave = useCallback(async (opts) => {
        const silent = opts && opts.silent
        if (saving) return
        setSaving(true)
        try {
          const payload = {
            id: note ? note.id : undefined,
            title: title.trim() || '未命名',
            body: body,
            tags: tags.split(/[,，]/).map(s => s.trim()).filter(Boolean),
            kind: note ? note.meta.kind : 'note',
          }
          if (pinnedTop) payload.status = 'pinned'
          const r = await saveNote(payload)
          if (!r.ok) throw new Error(r.error || '保存失败')
          setDirty(false)
          if (!note) setNote({ id: r.id, meta: { title: payload.title, kind: payload.kind } })
          if (!silent) toast('已保存')
        } catch (e) { toast('保存失败：' + e.message) }
        finally { setSaving(false) }
      }, [note, title, body, tags, pinnedTop, saving])

      useEffect(() => {
        if (!dirty) return
        const t = setTimeout(() => doSave({ silent: true }), 1500)
        return () => clearTimeout(t)
      }, [dirty, title, body, tags, pinnedTop])

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

      // 底栏图标按钮：文字用通用符号，触控区 44px
      const IconBtn = ({ label, glyph, title, onClick, active }) => h('button', {
        title: title || label,
        onClick,
        style: {
          minWidth: 44, minHeight: 44, flex: '1 1 0',
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', gap: 2,
          border: 'none', borderRadius: 10, cursor: 'pointer',
          background: active ? 'var(--dsw-alias-bg-layer-3)' : 'transparent',
          color: active ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-secondary)',
          fontSize: 20, lineHeight: 1,
        },
      },
        h('span', { style: { fontSize: 20 } }, glyph),
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
        { g: '❝', t: '引用', a: () => insertLine('> ') },
        { g: '↶', t: '撤销', a: undo },
        { g: '↷', t: '重做', a: redo },
      ]
      const insItems = [
        { g: '☑', t: '待办', a: toggleTodo },
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
          h('button', { className: 'siyi-notes-btn', onClick: onBack }, '‹ 返回'),
          h('span', {
            style: { flex: 1, fontSize: 11, color: 'var(--dsw-alias-label-tertiary)', textAlign: 'center' },
          }, saving ? '保存中…' : (dirty ? '未保存' : '已保存')),
          h('button', {
            className: 'siyi-notes-btn' + (mode === 'read' ? ' on' : ''),
            onClick: () => setMode(m => m === 'read' ? 'edit' : 'read'),
          }, mode === 'read' ? '编辑' : '阅读'),
          h('button', {
            className: 'siyi-notes-btn' + (pinnedTop ? ' on' : ''),
            onClick: () => { setPinnedTop(v => !v); setDirty(true) },
            title: '置顶',
          }, pinnedTop ? '★' : '☆'),
        ),
        // 内容区：独立滚动，高度占满
        h('div', { className: 'siyi-notes-editor' },
          h('input', {
            className: 'siyi-notes-title-input',
            placeholder: '标题',
            value: title,
            readOnly: mode === 'read',
            onChange: (e) => { setTitle(e.target.value); setDirty(true) },
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
        // 二级菜单（在底栏上方弹出）
        (fmtMenu || insMenu) ? h('div', {
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
        }, it.g))) : null,
        // 底部固定工具栏：分类式，参考红米笔记
        h('div', {
          style: {
            display: 'flex', gap: 2, padding: '6px 8px',
            borderTop: '.5px solid var(--dsw-alias-border-l3)',
            background: 'var(--dsw-alias-bg-layer-2)', flex: 'none',
            paddingBottom: 'calc(6px + env(safe-area-inset-bottom, 0px))',
          },
        },
          h(IconBtn, { glyph: '✎', label: '格式', active: fmtMenu, onClick: () => { setFmtMenu(v => !v); setInsMenu(false) } }),
          h(IconBtn, { glyph: '☑', label: '待办', onClick: toggleTodo }),
          h(IconBtn, { glyph: '＋', label: '插入', active: insMenu, onClick: () => { setInsMenu(v => !v); setFmtMenu(false) } }),
          h(IconBtn, { glyph: mode === 'read' ? '✎' : '👁', label: mode === 'read' ? '编辑' : '阅读', onClick: () => setMode(m => m === 'read' ? 'edit' : 'read') }),
          h(IconBtn, { glyph: '⋯', label: '更多', onClick: () => { setFmtMenu(true); setInsMenu(false) } }),
        ),
        // 底部信息条
        h('div', { className: 'siyi-notes-foot' },
          h('span', null, wordCount + ' 字' + (linkCount ? ' · ' + linkCount + ' 双链' : '')),
          h('span', null, note && note.meta ? '更新于 ' + fmtTime(note.meta.updated) : '新笔记'),
        ),
      )
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
      const [sel, setSel] = useState(null)
      const [size, setSize] = useState({ w: 360, h: 480 })
      const [view, setView] = useState({ x: 0, y: 0, k: 1 })
      const [pinned, setPinned] = useState({})
      const wrapRef = useRef(null)
      const gesture = useRef(null)
      const pinch = useRef(null)

      useEffect(() => {
        getGraph().then(setGraph).catch(e => toast('图谱读取失败：' + e.message))
      }, [])

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
            className: 'siyi-notes-btn',
            onClick: () => setView(v => ({ ...v, k: Math.min(3, v.k * 1.25) })),
          }, '＋'),
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
          h('button', { className: 'siyi-notes-btn', onClick: onBack }, '返回'),
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
          if (!r.ok) throw new Error(r.error || '保存失败')
          toast(payload.status === 'done' ? '已完成' : '已保存')
          await load()
        } catch (e) { toast('保存失败：' + e.message) }
        finally { setBusy(false) }
      }

      const todayKey = (() => {
        const d = new Date()
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0')
      })()

      const submitDraft = async () => {
        if (!draft || !draft.title.trim()) return
        await save({
          kind: 'task',
          title: draft.title.trim(),
          status: 'open',
          due: draft.due || null,
          body: draft.title.trim(),
        })
        setDraft(null)
      }

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-editor-head' },
          h('button', { className: 'siyi-notes-btn', onClick: onBack }, '返回'),
          h('button', {
            className: 'siyi-notes-btn' + (showDone ? ' on' : ''),
            onClick: () => setShowDone(v => !v),
          }, showDone ? '含已完成' : '只看未完成'),
          h('button', {
            className: 'siyi-notes-btn',
            style: { marginLeft: 'auto' },
            onClick: () => setDraft({ title: '', due: '' }),
          }, '＋ 待办'),
        ),
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
              : items.map(t => {
                const done = t.status === 'done'
                const overdue = !done && t.due && String(t.due).slice(0, 10) < todayKey
                return h('div', { key: t.id, className: 'siyi-notes-item' },
                  h('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 8 } },
                    h('button', {
                      onClick: () => save({ id: t.id, status: done ? 'open' : 'done' }),
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

      let body
      if (view === 'list') {
        body = h(ListView, {
          onOpen: open,
          onGraph: graph,
          onMonth: month,
          onTasks: tasks,
          onNew: () => { setId(null); setView('edit') },
          toast,
        })
      } else if (view === 'graph') {
        body = h(GraphView, { onBack: back, onOpen: open, toast })
      } else if (view === 'month') {
        body = h(MonthView, { onBack: back, onOpen: open, toast })
      } else if (view === 'tasks') {
        body = h(TaskView, { onBack: back, onOpen: open, toast })
      } else {
        body = h(EditorView, { id, onBack: back, toast })
      }

      return h('div', { style: { position: 'relative', height: '100%', minHeight: 0 } },
        body,
        toastMsg ? h('div', { className: 'siyi-notes-toast' }, toastMsg) : null,
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
        h('span', { style: { fontSize: 22 } }, '📝'),
        h('span', { style: { display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 } },
          h('span', { style: { fontSize: 14, lineHeight: 1.4 } }, '我的笔记'),
          h('span', { style: { fontSize: 11, lineHeight: 1.4, color: 'var(--dsw-alias-label-tertiary)' } },
            '列表、编辑、知识图谱'),
        ),
      )
    }

    const TAB_ID = 'siyi-notes'

    // ── 插件入口 ────────────────────────────────────────────────────
    const inject = ['slots', 'sidebarRight', 'sidebarRightTabs']

    function apply(ctx) {
      // 注册页面类型
      ctx.effect(() => ctx.sidebarRightTabs.register({
        id: TAB_ID,
        kind: TAB_ID,
        multiple: false,
        title: () => '我的笔记',
        guide: [{
          id: 'open',
          order: 30,
          title: () => '我的笔记',
          description: () => '列表、编辑、知识图谱',
        }],
      }), 'siyi-notes.type')

      // 入口卡片。
      //
      // ⚠ 打开 tab 的正确方式是 tab.actions.openTab(kind, opts)，
      // 它来自 useTabInfo() 这个 hook，不是组件 props 上的 openTab。
      // 这一点照 dsh-client-ui-sidebar-terminal 的 TerminalGuide 抄的
      // （那边第 94 行就是 tab.actions.openTab("terminal", { replaceTab: true })）。
      ctx.effect(() => ctx.slots.inject('sidebar.right.tab.guide.entry', () => ctx.slots.register({
        name: 'sidebar.right.tab.guide.entry',
        key: TAB_ID,
        inject: () => ({ hooks: {} }),
      }, ({ useTabInfo }) => {
        const { tab } = useTabInfo()
        return h(GuideCard, {
          onOpen: () => {
            try { tab.actions.openTab(TAB_ID, { replaceTab: true }) } catch (e) {}
          },
        })
      })), 'siyi-notes.guide')

      // 页面正文
      ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register({
        name: 'sidebar.right.pane.tab',
        key: TAB_ID,
        inject: () => ({}),
      }, NotesApp)), 'siyi-notes.body')

      // 页面标题
      ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab.title', () => ctx.slots.register({
        name: 'sidebar.right.pane.tab.title',
        key: TAB_ID,
        inject: () => ({}),
      }, () => '我的笔记')), 'siyi-notes.title')
    }

    exports.apply = apply
    exports.inject = inject
    return module.exports
  },
})
