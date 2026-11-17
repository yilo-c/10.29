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
.siyi-notes-root{display:flex;flex-direction:column;height:100%;min-height:0;
  background:var(--dsw-alias-bg-layer-1);color:var(--dsw-alias-label-primary);
  font-size:14px;line-height:1.6}
.siyi-notes-bar{display:flex;align-items:center;gap:6px;padding:8px 10px;flex:none;
  border-bottom:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-search{flex:1;min-width:0;height:30px;padding:0 10px;border-radius:8px;
  border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);
  color:var(--dsw-alias-label-primary);font-size:13px;outline:none}
.siyi-notes-search:focus{border-color:var(--dsw-alias-border-l1)}
.siyi-notes-btn{height:30px;padding:0 10px;border-radius:8px;flex:none;cursor:pointer;
  border:.5px solid var(--dsw-alias-border-l3);background:var(--dsw-alias-bg-layer-2);
  color:var(--dsw-alias-label-primary);font-size:13px}
.siyi-notes-btn:active{background:var(--dsw-alias-bg-layer-3)}
.siyi-notes-btn.on{background:var(--dsw-alias-bg-layer-3);font-weight:600}
.siyi-notes-tabs{display:flex;gap:6px;padding:6px 10px;flex:none;overflow-x:auto;
  border-bottom:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-tabs::-webkit-scrollbar{display:none}
.siyi-notes-list{flex:1;overflow-y:auto;min-height:0;padding:4px 0}
.siyi-notes-item{padding:10px 12px;cursor:pointer;border-bottom:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-item:active{background:var(--dsw-alias-bg-layer-2)}
.siyi-notes-item-title{font-size:14px;font-weight:500;margin-bottom:3px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.siyi-notes-item-meta{font-size:11px;color:var(--dsw-alias-label-tertiary);
  display:flex;gap:8px;align-items:center}
.siyi-notes-tag{font-size:10px;padding:1px 6px;border-radius:4px;
  background:var(--dsw-alias-bg-layer-3);color:var(--dsw-alias-label-secondary)}
.siyi-notes-empty{padding:40px 20px;text-align:center;color:var(--dsw-alias-label-tertiary);font-size:13px}
.siyi-notes-editor{flex:1;display:flex;flex-direction:column;min-height:0;overflow:hidden}
.siyi-notes-editor-head{display:flex;align-items:center;gap:6px;padding:8px 10px;flex:none;
  border-bottom:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-title-input{width:100%;border:none;outline:none;background:transparent;
  color:var(--dsw-alias-label-primary);font-size:19px;font-weight:600;
  padding:14px 16px 6px;line-height:1.35}
.siyi-notes-body-input{width:100%;flex:1;border:none;outline:none;background:transparent;
  color:var(--dsw-alias-label-primary);font-size:15px;line-height:1.75;
  padding:6px 16px 16px;resize:none;font-family:inherit;min-height:0}
.siyi-notes-foot{font-size:11px;color:var(--dsw-alias-label-tertiary);
  padding:6px 16px;flex:none;display:flex;justify-content:space-between;
  border-top:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-graph{flex:1;min-height:0;overflow:hidden;position:relative;touch-action:none;user-select:none;-webkit-user-select:none}
.siyi-notes-graph svg{width:100%;height:100%;display:block}
.siyi-notes-legend{position:absolute;left:8px;bottom:8px;font-size:10px;
  color:var(--dsw-alias-label-tertiary);background:var(--dsw-alias-bg-layer-1);
  padding:4px 8px;border-radius:6px;border:.5px solid var(--dsw-alias-border-l3)}
.siyi-notes-toast{position:absolute;left:50%;transform:translateX(-50%);bottom:16px;
  background:var(--dsw-alias-bg-layer-3);color:var(--dsw-alias-label-primary);
  font-size:12px;padding:6px 14px;border-radius:14px;z-index:9;
  border:.5px solid var(--dsw-alias-border-l3)}
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
    // 对齐 Redmi 笔记的功能集（对照它的实际编辑器逐项确认）：
    //   工具栏：粗体 / 斜体 / 下划线 / 删除线 / 标题 / 无序列表 /
    //           有序列表 / 待办 / 引用 / 代码 / 分割线 / 双链 / 标签
    //   编辑区：所见即所得的轻量渲染（预览态），不是纯 textarea
    //   其它：撤销 / 重做、字数统计、置顶、自动保存、手动保存
    //
    // 关键取舍：不引入 ProseMirror/Slate。
    //   方案里明确写了「不搬 ProseMirror，太重且和 DSH 渲染体系打架」。
    //   这里用 contentEditable + document.execCommand 这类浏览器原生能力，
    //   简单命令（加粗、列表）交给它，复杂结构（待办、双链）走 markdown 文本。
    //   好处是零依赖、行为可预期；代价是富文本能力不如专业编辑器，
    //   但笔记场景够用，且文件仍是标准 markdown，外部可读可迁移。
    function EditorView({ id, onBack, toast }) {
      const [note, setNote] = useState(null)
      const [title, setTitle] = useState('')
      const [body, setBody] = useState('')
      const [tags, setTags] = useState('')
      const [saving, setSaving] = useState(false)
      const [dirty, setDirty] = useState(false)
      const [preview, setPreview] = useState(false)
      const [pinnedTop, setPinnedTop] = useState(false)
      const bodyRef = useRef(null)
      const historyRef = useRef({ stack: [], index: -1 })
      const skipHistory = useRef(false)

      useEffect(() => {
        let alive = true
        if (!id) {
          setNote(null); setTitle(''); setBody(''); setTags('')
          setDirty(false); setPreview(false); setPinnedTop(false)
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

      // 自动保存：停笔 1.5 秒
      useEffect(() => {
        if (!dirty) return
        const t = setTimeout(() => doSave({ silent: true }), 1500)
        return () => clearTimeout(t)
      }, [dirty, title, body, tags, pinnedTop])

      // ── 撤销 / 重做（自己维护栈，因为 contentEditable 的原生 undo 不可靠）──
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
        toast('已撤销')
      }
      const redo = () => {
        const h = historyRef.current
        if (h.index >= h.stack.length - 1) { toast('没有可重做的操作'); return }
        h.index++
        skipHistory.current = true
        setBody(h.stack[h.index]); setDirty(true)
        toast('已重做')
      }

      // ── 编辑器：往光标处插入 markdown 片段 ──
      const insert = (before, after, placeholder) => {
        const ta = bodyRef.current
        if (!ta) return
        const start = ta.selectionStart, end = ta.selectionEnd
        const sel = body.slice(start, end) || (placeholder || '')
        const next = body.slice(0, start) + before + sel + after + body.slice(end)
        pushHistory(next)
        setBody(next); setDirty(true)
        // 恢复光标到片段内部
        requestAnimationFrame(() => {
          ta.focus()
          ta.selectionStart = ta.selectionEnd = start + before.length + sel.length
        })
      }

      // 行首插入（列表、标题、引用这类）
      const insertLine = (prefix) => {
        const ta = bodyRef.current
        if (!ta) return
        const start = ta.selectionStart
        // 找到当前行开头
        let lineStart = body.lastIndexOf('\n', start - 1) + 1
        const next = body.slice(0, lineStart) + prefix + body.slice(lineStart)
        pushHistory(next)
        setBody(next); setDirty(true)
        requestAnimationFrame(() => {
          ta.focus()
          ta.selectionStart = ta.selectionEnd = start + prefix.length
        })
      }

      // 待办：切换行首的 [ ] / [x]
      const toggleTodo = () => {
        const ta = bodyRef.current
        if (!ta) return
        const start = ta.selectionStart
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
        requestAnimationFrame(() => { ta.focus() })
      }

      const TOOLS = [
        { label: 'B', tip: '粗体', act: () => insert('**', '**', '粗体'), style: { fontWeight: 700 } },
        { label: 'I', tip: '斜体', act: () => insert('*', '*', '斜体'), style: { fontStyle: 'italic' } },
        { label: 'U', tip: '下划线', act: () => insert('<u>', '</u>', '下划线'), style: { textDecoration: 'underline' } },
        { label: 'S', tip: '删除线', act: () => insert('~~', '~~', '删除线'), style: { textDecoration: 'line-through' } },
        { label: 'H', tip: '标题', act: () => insertLine('## ') },
        { label: '•', tip: '无序列表', act: () => insertLine('- ') },
        { label: '1.', tip: '有序列表', act: () => insertLine('1. ') },
        { label: '☑', tip: '待办', act: toggleTodo },
        { label: '❝', tip: '引用', act: () => insertLine('> ') },
        { label: '</>', tip: '代码块', act: () => insert('\n```\n', '\n```\n', 'code'), style: { fontSize: 10 } },
        { label: '—', tip: '分割线', act: () => { const next = body + '\n\n---\n\n'; pushHistory(next); setBody(next); setDirty(true) } },
        { label: '[[', tip: '双链', act: () => insert('[[', ']]', '笔记标题') },
        { label: '#', tip: '标签', act: () => insert(' #', '', '标签') },
      ]

      const wordCount = (body || '').replace(/\s/g, '').length
      const linkCount = (body || '').match(/\[\[[^\]]+\]\]/g)?.length || 0
      const todoDone = (body || '').match(/^- \[x\]/gim)?.length || 0
      const todoAll = (body || '').match(/^- \[[ x]\]/gim)?.length || 0

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-editor-head' },
          h('button', { className: 'siyi-notes-btn', onClick: onBack }, '返回'),
          h('button', {
            className: 'siyi-notes-btn' + (dirty ? ' on' : ''),
            onClick: () => doSave({}),
            disabled: saving,
          }, saving ? '保存中…' : (dirty ? '保存' : '已保存')),
          h('button', { className: 'siyi-notes-btn', onClick: undo, title: '撤销' }, '↶'),
          h('button', { className: 'siyi-notes-btn', onClick: redo, title: '重做' }, '↷'),
          h('button', {
            className: 'siyi-notes-btn' + (preview ? ' on' : ''),
            onClick: () => setPreview(v => !v),
            title: '预览',
          }, preview ? '编辑' : '预览'),
          h('button', {
            className: 'siyi-notes-btn' + (pinnedTop ? ' on' : ''),
            onClick: () => { setPinnedTop(v => !v); setDirty(true) },
            title: '置顶',
          }, pinnedTop ? '已置顶' : '置顶'),
        ),
        h('div', { className: 'siyi-notes-editor' },
          h('input', {
            className: 'siyi-notes-title-input',
            placeholder: '标题',
            value: title,
            onChange: (e) => { setTitle(e.target.value); setDirty(true) },
          }),
          preview
            ? h('div', {
                className: 'siyi-notes-body-input',
                style: { overflowY: 'auto', display: 'block' },
                dangerouslySetInnerHTML: { __html: renderMarkdown(body) },
              })
            : h('textarea', {
                ref: bodyRef,
                className: 'siyi-notes-body-input',
                placeholder: '开始写…用 [[双链]] 和 #标签 组织内容',
                value: body,
                onChange: (e) => {
                  const v = e.target.value
                  pushHistory(v)
                  setBody(v); setDirty(true)
                },
                onKeyDown: (e) => {
                  // 常用快捷键
                  if ((e.ctrlKey || e.metaKey) && e.key === 'b') { e.preventDefault(); insert('**', '**', '粗体') }
                  else if ((e.ctrlKey || e.metaKey) && e.key === 'i') { e.preventDefault(); insert('*', '*', '斜体') }
                  else if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); undo() }
                  else if ((e.ctrlKey || e.metaKey) && e.key === 'y') { e.preventDefault(); redo() }
                  // Tab 在待办行里切换完成状态
                  else if (e.key === 'Tab') { e.preventDefault(); toggleTodo() }
                },
              }),
          // 工具栏：只在编辑态显示
          preview ? null : h('div', {
            style: {
              display: 'flex', flexWrap: 'wrap', gap: 4, padding: '6px 10px',
              borderTop: '.5px solid var(--dsw-alias-border-l3)', flex: 'none',
              background: 'var(--dsw-alias-bg-layer-2)',
            },
          }, TOOLS.map(t => h('button', {
            key: t.tip,
            title: t.tip,
            className: 'siyi-notes-btn',
            style: { minWidth: 32, padding: '0 6px', fontSize: 12, ...t.style },
            onMouseDown: (e) => e.preventDefault(),   // 防止点工具栏时编辑区失焦
            onClick: t.act,
          }, t.label))),
          h('input', {
            style: {
              border: 'none', outline: 'none', background: 'transparent',
              color: 'var(--dsw-alias-label-tertiary)', fontSize: 12,
              padding: '4px 16px', flex: 'none',
            },
            placeholder: '标签，逗号分隔',
            value: tags,
            onChange: (e) => { setTags(e.target.value); setDirty(true) },
          }),
        ),
        h('div', { className: 'siyi-notes-foot' },
          h('span', null, wordCount + ' 字' + (linkCount ? ' · ' + linkCount + ' 双链' : '') +
            (todoAll ? ' · 待办 ' + todoDone + '/' + todoAll : '')),
          h('span', null, note && note.meta ? '更新于 ' + fmtTime(note.meta.updated) : '新笔记'),
        ),
      )
    }

    /** 极简 markdown 渲染，只覆盖笔记实际用到的语法。 */
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
    // ── D4 知识图谱 ─────────────────────────────────────────────────
    //
    // 交互设计参考 Obsidian 的图谱（这是目前移动端做得最成熟的实现）：
    //   1. 单指拖动空白处  → 平移画布（问题反馈「无法滑动」的正面修复）
    //   2. 双指捏合        → 缩放，0.3x 到 3x
    //   3. 拖动节点        → 移动单个节点，并固定住它的位置
    //   4. 点节点          → 高亮它和它的邻居，其余淡化
    //   5. 双击空白        → 复位视图
    //   6. 节点大小按连接数，孤立节点更小更淡
    //
    // 为什么不用 d3：它的 zoom/force 在 WebView 里体积大且和 React 的
    // 事件系统要额外接一层。这里的布局和变换都是手写，几十行就够，
    // 而且完全可控（力导向只在初始化跑一次，之后位置由位置表维护，
    // 避免持续计算导致手机发热）。
    function GraphView({ onBack, onOpen, toast }) {
      const [graph, setGraph] = useState(null)
      const [sel, setSel] = useState(null)
      const [size, setSize] = useState({ w: 360, h: 480 })
      // 视图变换：平移 + 缩放
      const [view, setView] = useState({ x: 0, y: 0, k: 1 })
      // 节点手工位置覆盖（拖动后固定）
      const [pinned, setPinned] = useState({})
      const wrapRef = useRef(null)
      const gesture = useRef(null)

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

      // ── 力导向布局：只在节点/尺寸变化时算一次 ──
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
        // 迭代退火：前期斥力大、后期收敛
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
        // 收敛后一次性夹到可视区，避免飞出屏幕
        for (const a of arr) {
          a.x = Math.max(28, Math.min(W - 28, a.x))
          a.y = Math.max(24, Math.min(H - 24, a.y))
        }
        return { nodes: arr, map: new Map(arr.map(a => [a.id, a])), link, deg, W, H }
      }, [graph, size])

      // 应用 pin 后的最终坐标
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

      // 选中节点的邻居集合（用于高亮）
      const neighbors = useMemo(() => {
        if (!sel || !graph || !graph.edges) return null
        const s = new Set([sel])
        for (const e of graph.edges) {
          if (e.source === sel) s.add(e.target)
          if (e.target === sel) s.add(e.source)
        }
        return s
      }, [sel, graph])

      // ── 手势 ────────────────────────────────────────────────────
      const localPoint = (e) => {
        const el = wrapRef.current
        if (!el) return { x: 0, y: 0 }
        const r = el.getBoundingClientRect()
        return { x: e.clientX - r.left, y: e.clientY - r.top }
      }
      // 屏幕坐标 → 画布坐标（逆变换）
      const toCanvas = (p, v) => ({ x: (p.x - v.x) / v.k, y: (p.y - v.y) / v.k })

      const hitNode = (canvasPt) => {
        let best = null, bestD = 22
        for (const n of nodes) {
          const d = Math.hypot(n.x - canvasPt.x, n.y - canvasPt.y)
          if (d < bestD) { bestD = d; best = n }
        }
        return best
      }

      const onPointerDown = (e) => {
        if (!wrapRef.current) return
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
        // 捕获指针，手指移出容器也能持续收到事件
        try { wrapRef.current.setPointerCapture(e.pointerId) } catch (err) {}
      }

      const onPointerMove = (e) => {
        const g = gesture.current
        if (!g) return
        const p = localPoint(e)
        const dx = p.x - g.startX, dy = p.y - g.startY
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) g.moved = true
        if (g.mode === 'pan') {
          setView({ ...g.startView, x: g.startView.x + dx, y: g.startView.y + dy })
        } else if (g.mode === 'node' && g.nodeId) {
          // 节点跟随手指：位移要除以缩放比，否则放大后手感发飘
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
        // 没移动 = 点击
        if (!g.moved) {
          if (g.mode === 'node') {
            setSel(prev => prev === g.nodeId ? null : g.nodeId)
          } else {
            setSel(null)
          }
        }
      }

      // 滚轮缩放（桌面/调试用）与双指捏合（移动端）
      const onWheel = (e) => {
        e.preventDefault()
        const p = localPoint(e)
        const factor = e.deltaY > 0 ? 0.9 : 1.1
        setView(v => {
          const k = Math.max(0.3, Math.min(3, v.k * factor))
          // 以指针位置为锚点缩放
          const cx = (p.x - v.x) / v.k, cy = (p.y - v.y) / v.k
          return { k, x: p.x - cx * k, y: p.y - cy * k }
        })
      }

      // 双指捏合：用 touch 事件算两指距离比
      const pinch = useRef(null)
      const onTouchStart = (e) => {
        if (e.touches.length === 2) {
          const [a, b] = e.touches
          pinch.current = {
            dist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
            k: view.k,
            mid: { x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 },
            v: { ...view },
          }
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

      const resetView = () => { setView({ x: 0, y: 0, k: 1 }); setPinned({}) }

      const share = graph && graph.edges ? graph.edges.filter(e => !e.missing).length : 0
      const isolated = graph && graph.nodes ? graph.nodes.filter(n => (n.degree || 0) === 0).length : 0

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-editor-head' },
          h('button', { className: 'siyi-notes-btn', onClick: onBack }, '返回'),
          h('button', {
            className: 'siyi-notes-btn',
            onClick: () => setView(v => ({ ...v, k: Math.min(3, v.k * 1.25) })),
            title: '放大',
          }, '＋'),
          h('button', {
            className: 'siyi-notes-btn',
            onClick: () => setView(v => ({ ...v, k: Math.max(0.3, v.k / 1.25) })),
            title: '缩小',
          }, '－'),
          h('button', { className: 'siyi-notes-btn', onClick: resetView, title: '复位视图' }, '复位'),
          h('span', {
            style: { marginLeft: 'auto', fontSize: 11, color: 'var(--dsw-alias-label-tertiary)' },
          }, graph ? (graph.nodes.length + ' 节点 · ' + share + ' 链') : '读取中…'),
        ),
        h('div', {
          className: 'siyi-notes-graph',
          ref: wrapRef,
          // touchAction none 是关键：否则浏览器会把拖动解释成页面滚动，
          // pointer 事件在移动端根本收不到连续 move，表现就是「滑不动」。
          style: { touchAction: 'none', userSelect: 'none', cursor: gesture.current && gesture.current.mode === 'pan' ? 'grabbing' : 'grab' },
          onPointerDown,
          onPointerMove,
          onPointerUp,
          onPointerCancel: onPointerUp,
          onWheel,
          onTouchStart,
          onTouchMove,
          onTouchEnd,
          onDoubleClick: resetView,
        },
          !baseLayout ? h('div', { className: 'siyi-notes-empty' }, graph ? '没有可绘制的节点' : '读取中…')
            : h('svg', {
                viewBox: '0 0 ' + baseLayout.W + ' ' + baseLayout.H,
                style: { width: '100%', height: '100%', display: 'block' },
              },
                h('g', {
                  transform: 'translate(' + view.x + ',' + view.y + ') scale(' + view.k + ')',
                },
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
                    const r = 4 + Math.min(7, d * 1.5)
                    const active = sel === n.id
                    const dim = neighbors && !neighbors.has(n.id)
                    return h('g', {
                      key: n.id,
                      style: { cursor: 'pointer' },
                    },
                      h('circle', {
                        cx: n.x, cy: n.y, r: active ? r + 2 : r,
                        fill: active ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-tertiary)',
                        opacity: dim ? 0.2 : (active ? 1 : (d ? .85 : .5)),
                        stroke: active ? 'var(--dsw-alias-label-primary)' : 'none',
                        strokeWidth: 1.5,
                      }),
                      (d >= 2 || active) ? h('text', {
                        x: n.x, y: n.y - r - 5, textAnchor: 'middle',
                        fontSize: 9 / Math.max(0.7, view.k),
                        fill: 'var(--dsw-alias-label-tertiary)',
                        opacity: dim ? 0.2 : 1,
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
              borderRadius: 8, padding: '8px 10px', fontSize: 12, zIndex: 5,
            },
          },
            h('div', { style: { fontWeight: 600, marginBottom: 4 } },
              (graph.nodes.find(x => x.id === sel) || {}).title || ''),
            h('button', {
              className: 'siyi-notes-btn', style: { height: 26, fontSize: 12 },
              onClick: () => onOpen(sel),
            }, '打开'),
            h('button', {
              className: 'siyi-notes-btn', style: { height: 26, fontSize: 12, marginLeft: 6 },
              onClick: () => { setPinned(p => { const c = { ...p }; delete c[sel]; return c }); toast('已解除固定') },
            }, '解除固定'),
            h('button', {
              className: 'siyi-notes-btn', style: { height: 26, fontSize: 12, marginLeft: 6 },
              onClick: () => setSel(null),
            }, '关闭'),
          ) : null,
          h('div', { className: 'siyi-notes-legend' },
            '拖动平移 · 双指缩放 · 拖节点固定 · 双击复位' +
            (isolated > 0 ? ' · ' + isolated + ' 孤立' : '')),
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
              position: 'relative', minHeight: 40, padding: '3px 1px',
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
                        flex: 'none', width: 20, height: 20, marginTop: 1,
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
