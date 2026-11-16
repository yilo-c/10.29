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
.siyi-notes-graph{flex:1;min-height:0;overflow:hidden;position:relative}
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
    function EditorView({ id, onBack, toast }) {
      const [note, setNote] = useState(null)
      const [title, setTitle] = useState('')
      const [body, setBody] = useState('')
      const [tags, setTags] = useState('')
      const [saving, setSaving] = useState(false)
      const [dirty, setDirty] = useState(false)
      const bodyRef = useRef(null)

      useEffect(() => {
        let alive = true
        if (!id) {
          setNote(null); setTitle(''); setBody(''); setTags(''); setDirty(false)
          return
        }
        getNote(id).then(g => {
          if (!alive) return
          if (!g.ok) { toast('笔记不存在'); onBack(); return }
          setNote(g)
          setTitle(g.meta.title || '')
          setBody(g.body || '')
          setTags((g.meta.tags || []).join(', '))
          setDirty(false)
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
            tags: tags.split(',').map(s => s.trim()).filter(Boolean),
            kind: note ? note.meta.kind : 'note',
          }
          const r = await saveNote(payload)
          if (!r.ok) throw new Error(r.error || '保存失败')
          setDirty(false)
          if (!note) { setNote({ id: r.id, meta: { ...payload, ...{} } }) }
          if (!silent) toast('已保存')
        } catch (e) { toast('保存失败：' + e.message) }
        finally { setSaving(false) }
      }, [note, title, body, tags, saving])

      // 自动保存：停笔 1.5 秒
      useEffect(() => {
        if (!dirty) return
        const t = setTimeout(() => doSave({ silent: true }), 1500)
        return () => clearTimeout(t)
      }, [dirty, title, body, tags])

      const wordCount = (body || '').replace(/\s/g, '').length

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-editor-head' },
          h('button', { className: 'siyi-notes-btn', onClick: onBack }, '返回'),
          h('button', {
            className: 'siyi-notes-btn' + (dirty ? ' on' : ''),
            onClick: () => doSave({}),
            disabled: saving,
          }, saving ? '保存中…' : (dirty ? '保存' : '已保存')),
          h('span', { style: { marginLeft: 'auto', fontSize: 11, color: 'var(--dsw-alias-label-tertiary)' } },
            note && note.links && note.links.length ? '双链 ' + note.links.length : ''),
        ),
        h('div', { className: 'siyi-notes-editor' },
          h('input', {
            className: 'siyi-notes-title-input',
            placeholder: '标题',
            value: title,
            onChange: (e) => { setTitle(e.target.value); setDirty(true) },
          }),
          h('textarea', {
            ref: bodyRef,
            className: 'siyi-notes-body-input',
            placeholder: '开始写…用 [[双链]] 和 #标签 组织内容',
            value: body,
            onChange: (e) => { setBody(e.target.value); setDirty(true) },
          }),
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
          h('span', null, wordCount + ' 字'),
          h('span', null, note && note.meta ? '更新于 ' + fmtTime(note.meta.updated) : '新笔记'),
        ),
      )
    }

    // ── D4 知识图谱 ─────────────────────────────────────────────────
    function GraphView({ onBack, onOpen, toast }) {
      const [graph, setGraph] = useState(null)
      const [sel, setSel] = useState(null)
      const [size, setSize] = useState({ w: 360, h: 480 })
      const wrapRef = useRef(null)

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

      // 力导向布局：迭代 300 次，纯手写，不用 d3
      const layout = useMemo(() => {
        if (!graph || !graph.nodes || !graph.nodes.length) return null
        const nodes = graph.nodes
        const edges = (graph.edges || []).filter(e => e.target)
        const W = size.w, H = size.h, CX = W / 2, CY = H / 2
        const N = nodes.length
        const pos = new Map()
        nodes.forEach((n, i) => {
          const a = (i / N) * Math.PI * 2
          const r = Math.min(W, H) * 0.34
          pos.set(n.id, { x: CX + Math.cos(a) * r, y: CY + Math.sin(a) * r })
        })
        const deg = new Map(nodes.map(n => [n.id, 0]))
        for (const e of edges) {
          deg.set(e.source, (deg.get(e.source) || 0) + 1)
          deg.set(e.target, (deg.get(e.target) || 0) + 1)
        }
        const idx = new Map(nodes.map((n, i) => [n.id, i]))
        const link = edges.map(e => ({ s: idx.get(e.source), t: idx.get(e.target) })).filter(e => e.s != null && e.t != null)
        const arr = nodes.map(n => ({ id: n.id, x: pos.get(n.id).x, y: pos.get(n.id).y }))
        // 简单弹簧-斥力
        for (let it = 0; it < 300; it++) {
          const k = 0.06 * (1 - it / 300) + 0.01
          for (let i = 0; i < arr.length; i++) {
            for (let j = i + 1; j < arr.length; j++) {
              let dx = arr[j].x - arr[i].x, dy = arr[j].y - arr[i].y
              let d2 = dx * dx + dy * dy
              if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = 1 }
              const d = Math.sqrt(d2)
              const rep = 900 / d2
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
            const f = (d - 90) * k
            const fx = (dx / d) * f, fy = (dy / d) * f
            a.x += fx; a.y += fy
            b.x -= fx; b.y -= fy
          }
          for (const a of arr) {
            a.x += (CX - a.x) * 0.004
            a.y += (CY - a.y) * 0.004
            a.x = Math.max(24, Math.min(W - 24, a.x))
            a.y = Math.max(20, Math.min(H - 20, a.y))
          }
        }
        const map = new Map(arr.map(a => [a.id, a]))
        return { nodes: arr, map, link, deg, W, H }
      }, [graph, size])

      const share = graph && graph.edges ? graph.edges.filter(e => !e.missing).length : 0
      const isolated = graph && graph.nodes ? graph.nodes.filter(n => (n.degree || 0) === 0).length : 0

      return h('div', { className: 'siyi-notes-root' },
        h('div', { className: 'siyi-notes-editor-head' },
          h('button', { className: 'siyi-notes-btn', onClick: onBack }, '返回'),
          h('span', { style: { fontSize: 11, color: 'var(--dsw-alias-label-tertiary)', marginLeft: 4 } },
            graph ? (graph.nodes.length + ' 节点 · ' + share + ' 条双链') : '读取中…'),
        ),
        h('div', { className: 'siyi-notes-graph', ref: wrapRef },
          !layout ? h('div', { className: 'siyi-notes-empty' }, graph ? '没有可绘制的节点' : '读取中…')
            : h('svg', { viewBox: '0 0 ' + layout.W + ' ' + layout.H },
                layout.link.map((l, i) => {
                  const a = layout.nodes[l.s], b = layout.nodes[l.t]
                  if (!a || !b) return null
                  const hi = sel && (a.id === sel || b.id === sel)
                  return h('line', {
                    key: 'e' + i,
                    x1: a.x, y1: a.y, x2: b.x, y2: b.y,
                    stroke: hi ? 'var(--dsw-alias-label-secondary)' : 'var(--dsw-alias-border-l3)',
                    strokeWidth: hi ? 1.6 : 0.8,
                  })
                }),
                layout.nodes.map(n => {
                  const d = layout.deg.get(n.id) || 0
                  const r = 4 + Math.min(6, d * 1.4)
                  const active = sel === n.id
                  return h('g', { key: n.id, style: { cursor: 'pointer' }, onClick: () => setSel(n.id) },
                    h('circle', {
                      cx: n.x, cy: n.y, r: r,
                      fill: active ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-tertiary)',
                      opacity: active ? 1 : (d ? .85 : .45),
                    }),
                    d >= 2 || active ? h('text', {
                      x: n.x, y: n.y - r - 4, textAnchor: 'middle',
                      fontSize: 9, fill: 'var(--dsw-alias-label-tertiary)',
                    }, (graph.nodes.find(x => x.id === n.id) || {}).title || '') : null,
                  )
                }),
              ),
          sel ? h('div', {
            style: {
              position: 'absolute', left: 8, right: 8, top: 8,
              background: 'var(--dsw-alias-bg-layer-2)',
              border: '.5px solid var(--dsw-alias-border-l3)',
              borderRadius: 8, padding: '8px 10px', fontSize: 12,
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
              onClick: () => setSel(null),
            }, '关闭'),
          ) : null,
          h('div', { className: 'siyi-notes-legend' },
            isolated > 0 ? isolated + ' 个孤立节点' : '全部连通'),
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
