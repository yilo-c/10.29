import { useCallback, useEffect, useState } from 'react'

import { getWsClient } from '../../utils/ai'

/* ── 类型 ── */

interface RawItem {
  id?: string
  source?: string
  kind?: string
  sensitivity?: string
  content?: string
  meta?: { sender?: string; conversation?: string; timestamp?: string }
  created_at?: string
  processed?: number
}

interface Profile {
  long_term?: { focus_topics?: string[]; key_people?: string[]; common_sources?: string[]; recent_events?: string[] }
  context?: { current_focus?: string; active_project?: string }
  updated_at?: string
}

type Tab = 'import' | 'profile' | 'review' | 'items'

const IMPORT_HINTS: { key: string; label: string; format: string; hint: string }[] = [
  {
    key: 'wechat',
    label: '微信聊天记录',
    format: 'txt / csv',
    hint: '用 WeChatMsg（留痕）等工具导出聊天记录为 txt 或 csv 后导入。默认按「敏感」标记（仅本地保存）。',
  },
  {
    key: 'chatgpt',
    label: 'ChatGPT',
    format: 'conversations.json',
    hint: 'ChatGPT 设置 → 数据控制 → 导出数据，解压后选 conversations.json。',
  },
  {
    key: 'claude',
    label: 'Claude',
    format: 'project export.json',
    hint: 'Claude 项目导出 JSON 直接导入。',
  },
  {
    key: 'kimi',
    label: 'Kimi',
    format: 'json / 网页复制 Markdown',
    hint: 'Kimi 网页对话可复制保存为 Markdown（**用户**: / **AI**: 格式），或走自动同步（设置→Kimi）。',
  },
  {
    key: 'markdown',
    label: '通用对话（DeepSeek/豆包等）',
    format: 'txt / md',
    hint: '任意 AI 对话复制为「**用户**: xxx」+「**AI**: xxx」格式的文本文件即可导入。',
  },
]

function fmtTime(v?: string): string {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString('zh-CN')
}

export function PersonalModule() {
  const [tab, setTab] = useState<Tab>('import')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // 导入
  const [lastImport, setLastImport] = useState<Record<string, unknown> | null>(null)

  // 画像
  const [profile, setProfile] = useState<Profile | null>(null)
  const [editTopics, setEditTopics] = useState('')

  // 回顾
  const [period, setPeriod] = useState<'week' | 'month'>('week')
  const [review, setReview] = useState<Record<string, unknown> | null>(null)

  // 信息库
  const [items, setItems] = useState<RawItem[]>([])
  const [itemsSens, setItemsSens] = useState('')
  const [itemsSource, setItemsSource] = useState('')
  const [itemsStats, setItemsStats] = useState<Record<string, unknown> | null>(null)

  const loadProfile = useCallback(async () => {
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const res = await client.call<{ profile?: Profile }>('personal.profile.get', {})
      if (res?.profile) {
        setProfile(res.profile)
        setEditTopics((res.profile.long_term?.focus_topics ?? []).join(', '))
      } else setError('画像读取失败')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取画像失败')
    }
  }, [])

  const loadReview = useCallback(async () => {
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const res = await client.call<Record<string, unknown>>('personal.review', { period })
      setReview(res ?? null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '回顾生成失败')
    }
  }, [period])

  const loadItems = useCallback(async () => {
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const [ls, st] = await Promise.all([
        client.call<{ items?: RawItem[] }>('personal.raw_items.list', {
          sensitivity: itemsSens || undefined,
          source: itemsSource || undefined,
          limit: 100,
        }),
        client.call<{ stats?: Record<string, unknown> }>('personal.raw_items.stats', {}),
      ])
      setItems(ls?.items ?? [])
      setItemsStats(st?.stats ?? null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取信息库失败')
    }
  }, [itemsSens, itemsSource])

  useEffect(() => {
    setError(null)
    if (tab === 'profile') void loadProfile()
    if (tab === 'review') void loadReview()
    if (tab === 'items') void loadItems()
  }, [tab, loadProfile, loadReview, loadItems])

  // ── 导入 ──
  const importFile = useCallback(async () => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.txt,.csv,.json,.md'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      setBusy(true)
      setError(null)
      try {
        const client = getWsClient()
        if (!client) throw new Error('后端未连接')
        const res = await client.call<Record<string, unknown>>('data.import_chat', {
          // Electron renderer 的 File 带 path；浏览器环境回退 file.name
          file_path: (file as { path?: string }).path ?? file.name,
        })
        setLastImport(res ?? null)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '导入失败')
      } finally { setBusy(false) }
    }
    input.click()
  }, [])

  const extractProfile = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const res = await client.call<Record<string, unknown>>('personal.profile.extract', {})
      setLastImport(res ?? null)
      await loadProfile()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '画像提取失败')
    } finally { setBusy(false) }
  }, [loadProfile])

  const saveProfile = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const topics = editTopics.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
      const res = await client.call<{ success?: boolean }>('personal.profile.update', { focus_topics: topics })
      if (res?.success) { setError('画像已保存'); await loadProfile() }
      else setError('保存失败')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存失败')
    } finally { setBusy(false) }
  }, [editTopics, loadProfile])

  const setItemSensitivity = useCallback(async (id: string, sensitivity: string) => {
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      await client.call('personal.raw_items.set_sensitivity', { item_id: id, sensitivity })
      await loadItems()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '改标失败')
    }
  }, [loadItems])

  const profileTopics = profile?.long_term?.focus_topics ?? []
  const keyPeople = profile?.long_term?.key_people ?? []
  const reviewStats = (review?.stats ?? {}) as {
    total?: number; by_source?: Record<string, number>; by_sensitivity?: Record<string, number>; contacts?: string[]
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-auto bg-[var(--body-bg)] p-6" aria-labelledby="personal-heading" data-testid="personal-workspace">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--separator)] pb-5">
        <div>
          <span className="agent-kicker">PERSONAL KB</span>
          <h1 id="personal-heading" className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">个人知识体系</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">信息导入 · 画像提取 · 个人回顾 · 原始信息库（本地保存，敏感内容仅本机）</p>
        </div>
      </header>

      <nav className="mt-4 flex items-center gap-1 border-b border-[var(--separator)]" aria-label="个人知识体系功能">
        {([['import', '信息导入'], ['profile', '个人画像'], ['review', '个人回顾'], ['items', '信息库']] as [Tab, string][]).map(([k, label]) => (
          <button key={k} type="button" onClick={() => setTab(k)} className={`rounded-t-md px-4 py-2 text-sm transition-colors ${tab === k ? 'border-b-2 border-fluent-blue font-medium text-[var(--text-primary)]' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
            {label}
          </button>
        ))}
      </nav>

      {error && <div role="alert" className="mt-5 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-4 text-sm text-[var(--danger)]">{error}</div>}

      {/* ── 信息导入 ── */}
      {tab === 'import' && (
        <div className="mt-5 space-y-4">
          <div className="flex items-center gap-3">
            <button type="button" onClick={() => void importFile()} disabled={busy} className="rounded-md border border-[var(--separator)] bg-[var(--panel-bg-solid)] px-4 py-2 text-sm text-[var(--text-primary)] disabled:opacity-50">
              {busy ? '导入中...' : '选择聊天记录文件导入'}
            </button>
            <span className="text-xs text-[var(--text-muted)]">支持 txt / csv / json / md，自动识别微信与各 AI 格式</span>
          </div>

          {lastImport && (
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4 text-sm">
              <h3 className="mb-2 font-medium text-[var(--text-primary)]">上次结果</h3>
              <div className="grid grid-cols-2 gap-2 text-xs text-[var(--text-secondary)] md:grid-cols-4">
                <div>对话：{String(lastImport.total ?? 0)}</div>
                <div>已处理：{String(lastImport.imported ?? 0)}</div>
                <div>会话保存：{String(lastImport.sessions_saved ?? 0)}</div>
                <div>信息条目：{String(lastImport.raw_items_stored ?? 0)}</div>
              </div>
              {Boolean(lastImport.error) && <div className="mt-2 text-[var(--danger)]">{String(lastImport.error)}</div>}
            </div>
          )}

          <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
            <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">各来源导入说明</h2>
            <div className="space-y-2.5">
              {IMPORT_HINTS.map((h) => (
                <div key={h.key} className="rounded-md border border-[var(--separator)] px-3 py-2.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-[var(--text-primary)]">{h.label}</span>
                    <span className="rounded bg-fluent-blue/10 px-1.5 py-0.5 text-2xs text-fluent-blue">{h.format}</span>
                  </div>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">{h.hint}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── 个人画像 ── */}
      {tab === 'profile' && (
        <div className="mt-5 space-y-4">
          <div className="flex items-center gap-3">
            <button type="button" onClick={() => void extractProfile()} disabled={busy} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">
              {busy ? '提取中...' : '从导入内容提取画像'}
            </button>
            <span className="text-xs text-[var(--text-muted)]">扫描未处理的信息条目，提取兴趣/联系人/焦点写回画像</span>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
              <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">关注主题（focus_topics）</h2>
              {profileTopics.length === 0 ? (
                <p className="py-4 text-center text-sm text-[var(--text-muted)]">暂无主题，点击上方「提取画像」或手动编辑</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {profileTopics.map((t) => (
                    <span key={t} className="rounded-full bg-fluent-blue/10 px-2.5 py-0.5 text-xs text-fluent-blue">{t}</span>
                  ))}
                </div>
              )}
              <div className="mt-4">
                <textarea value={editTopics} onChange={(e) => setEditTopics(e.target.value)} rows={2}
                  className="w-full rounded-md border border-[var(--separator)] bg-[var(--input-bg)] px-3 py-2 text-xs text-[var(--text-secondary)] outline-none"
                  placeholder="逗号分隔，如：爬山, AI编程, 投资理财" />
                <button type="button" onClick={() => void saveProfile()} disabled={busy} className="mt-2 rounded-md border border-[var(--separator)] px-3 py-1.5 text-xs text-[var(--text-secondary)] disabled:opacity-50">
                  保存主题
                </button>
              </div>
            </div>

            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
              <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">重要人物</h2>
              {keyPeople.length === 0 ? (
                <p className="py-4 text-center text-sm text-[var(--text-muted)]">暂无（导入微信记录并提取后自动出现）</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {keyPeople.map((p) => (
                    <span key={p} className="rounded-full bg-green-500/10 px-2.5 py-0.5 text-xs text-green-400">{p}</span>
                  ))}
                </div>
              )}
              <div className="mt-4 border-t border-[var(--separator)] pt-3 text-xs text-[var(--text-muted)]">
                <div>当前专注：{profile?.context?.current_focus || '—'}</div>
                <div>活跃项目：{profile?.context?.active_project || '—'}</div>
                <div className="mt-1">更新时间：{fmtTime(profile?.updated_at)}</div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── 个人回顾 ── */}
      {tab === 'review' && (
        <div className="mt-5 space-y-4">
          <div className="flex items-center gap-3">
            <div className="flex rounded-md border border-[var(--separator)] overflow-hidden">
              {(['week', 'month'] as const).map((p) => (
                <button key={p} type="button" onClick={() => setPeriod(p)}
                  className={`px-4 py-1.5 text-xs ${period === p ? 'bg-fluent-blue/15 text-fluent-blue' : 'text-[var(--text-muted)]'}`}>
                  {p === 'week' ? '本周回顾' : '本月回顾'}
                </button>
              ))}
            </div>
            <button type="button" onClick={() => void loadReview()} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)]">刷新</button>
          </div>

          {review && (
            <>
              <div className="grid gap-3 md:grid-cols-4">
                <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3.5">
                  <span className="text-xs text-[var(--text-muted)]">条目总数</span>
                  <strong className="mt-1 block text-lg text-[var(--text-primary)]">{reviewStats.total ?? 0}</strong>
                </div>
                <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3.5">
                  <span className="text-xs text-[var(--text-muted)]">来源</span>
                  <strong className="mt-1 block text-sm text-[var(--text-primary)]">{Object.keys(reviewStats.by_source ?? {}).join(' / ') || '—'}</strong>
                </div>
                <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3.5">
                  <span className="text-xs text-[var(--text-muted)]">敏感条目</span>
                  <strong className="mt-1 block text-lg text-[var(--text-primary)]">{reviewStats.by_sensitivity?.sensitive ?? 0}</strong>
                </div>
                <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3.5">
                  <span className="text-xs text-[var(--text-muted)]">联系人</span>
                  <strong className="mt-1 block text-sm text-[var(--text-primary)]">{(reviewStats.contacts ?? []).length} 人</strong>
                </div>
              </div>

              {review.summary && (
                <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
                  <h2 className="mb-2 text-sm font-medium text-[var(--text-primary)]">AI 总结</h2>
                  <p className="text-sm leading-relaxed text-[var(--text-secondary)]">{String(review.summary)}</p>
                </div>
              )}
              {!review.summary && (
                <p className="text-xs text-[var(--text-muted)]">（AI 总结暂不可用——云端未付费或本地模型未就绪，仅显示统计）</p>
              )}

              {(review.highlights as string[] | undefined)?.length ? (
                <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
                  <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">高亮内容</h2>
                  <ul className="space-y-1.5 text-xs text-[var(--text-secondary)]">
                    {(review.highlights as string[]).map((h, i) => (
                      <li key={i} className="truncate rounded border border-[var(--separator)] px-2.5 py-1.5">{h}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          )}
          {!review && <p className="py-10 text-center text-sm text-[var(--text-muted)]">暂无数据，先导入一些聊天记录吧。</p>}
        </div>
      )}

      {/* ── 信息库 ── */}
      {tab === 'items' && (
        <div className="mt-5">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <select value={itemsSens} onChange={(e) => setItemsSens(e.target.value)} className="rounded-md border border-[var(--separator)] bg-[var(--input-bg)] px-2.5 py-1.5 text-xs text-[var(--text-secondary)] outline-none">
              <option value="">全部敏感度</option>
              <option value="sensitive">敏感</option>
              <option value="personal">普通</option>
            </select>
            <select value={itemsSource} onChange={(e) => setItemsSource(e.target.value)} className="rounded-md border border-[var(--separator)] bg-[var(--input-bg)] px-2.5 py-1.5 text-xs text-[var(--text-secondary)] outline-none">
              <option value="">全部来源</option>
              <option value="wechat">微信</option>
              <option value="chatgpt">ChatGPT</option>
              <option value="claude">Claude</option>
              <option value="kimi">Kimi</option>
              <option value="markdown">Markdown</option>
            </select>
            <button type="button" onClick={() => void loadItems()} className="rounded-md border border-[var(--separator)] px-3 py-1.5 text-xs text-[var(--text-secondary)]">刷新</button>
            {itemsStats && (
              <span className="ml-auto text-xs text-[var(--text-muted)]">
                共 {String((itemsStats as { total?: number }).total ?? 0)} 条 · 已消化 {String((itemsStats as { processed?: number }).processed ?? 0)}
              </span>
            )}
          </div>
          <div className="overflow-auto rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)]">
            <table className="w-full min-w-[760px] text-xs">
              <thead>
                <tr className="border-b border-[var(--separator)] text-left text-[var(--text-muted)]">
                  <th className="px-3 py-2 font-medium">敏感度</th>
                  <th className="px-3 py-2 font-medium">来源</th>
                  <th className="px-3 py-2 font-medium">发送人</th>
                  <th className="px-3 py-2 font-medium">内容</th>
                  <th className="px-3 py-2 font-medium">时间</th>
                  <th className="px-3 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && (
                  <tr><td colSpan={6} className="px-3 py-10 text-center text-sm text-[var(--text-muted)]">暂无信息条目，去「信息导入」导入聊天记录</td></tr>
                )}
                {items.map((it) => (
                  <tr key={it.id} className="border-b border-[var(--separator)] last:border-b-0">
                    <td className="px-3 py-2">
                      <span className={`rounded-full px-2 py-0.5 text-2xs ${it.sensitivity === 'sensitive' ? 'bg-[var(--danger-soft)] text-[var(--danger)]' : 'bg-green-500/10 text-green-400'}`}>
                        {it.sensitivity === 'sensitive' ? '敏感' : '普通'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-[var(--text-secondary)]">{it.source || '—'}</td>
                    <td className="px-3 py-2 text-[var(--text-secondary)]">{it.meta?.sender || '—'}</td>
                    <td className="max-w-[280px] px-3 py-2 text-[var(--text-primary)]"><span className="line-clamp-2">{it.content}</span></td>
                    <td className="px-3 py-2 text-xs text-[var(--text-muted)]">{fmtTime(it.created_at)}</td>
                    <td className="px-3 py-2">
                      {it.sensitivity === 'sensitive' ? (
                        <button type="button" onClick={() => void setItemSensitivity(it.id ?? '', 'personal')} className="rounded px-1.5 py-0.5 text-xs text-green-400 hover:bg-[var(--hover-bg)]">标为普通</button>
                      ) : (
                        <button type="button" onClick={() => void setItemSensitivity(it.id ?? '', 'sensitive')} className="rounded px-1.5 py-0.5 text-xs text-[var(--danger)] hover:bg-[var(--hover-bg)]">标为敏感</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}
