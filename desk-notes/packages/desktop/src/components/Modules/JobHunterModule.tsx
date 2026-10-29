import { useCallback, useEffect, useRef, useState } from 'react'
import Icon from '../Common/Icon'

import { getWsClient } from '../../utils/ai'

/* ── 类型（与后端 _job_hunter.py / storage.py 契约对齐，2026-08-17）── */

interface JobItem {
  id?: string
  job_id?: string
  title: string
  company?: string
  city?: string
  salary?: string
  platform?: string
  url?: string
  application_status?: string
  status?: string
  match_score?: number
  analyzed?: boolean
  summary?: string
  tags?: string[]
  created_at?: string
  updated_at?: string
  matched_at?: string
}

interface JobStats {
  total?: number
  analyzed?: number
  matched?: number
  avg_match_score?: number
  platforms?: Record<string, number>
  counts?: Record<string, number>
  funnel?: { total?: number; applied?: number; interviewing?: number; offered?: number }
}

interface JobPlatform {
  id: string
  name: string
  enabled?: boolean
  implemented?: boolean
}

interface JobProfile {
  name?: string
  city?: string
  years_of_experience?: number
  tech_stack?: string[]
  target_positions?: string[]
  expected_salary?: string
  education?: string
  industry_preference?: string[]
  exclude_keywords?: string[]
  self_introduction?: string
  custom_prompt?: string
  resume_text?: string
  resume_keywords?: string[]
}

const STATUS_LABELS: Record<string, string> = { new: '新', interested: '感兴趣', applied: '已投递', interviewing: '面试中', offered: '已录用', ignored: '忽略', saved: '保存' }
const STATUS_COLORS: Record<string, string> = {
  new: 'text-blue-400', interested: 'text-yellow-400', applied: 'text-green-400',
  interviewing: 'text-cyan-400', offered: 'text-emerald-400', ignored: 'text-gray-400', saved: 'text-purple-400',
}
const FILTER_STATUSES = ['', 'new', 'interested', 'applied', 'interviewing', 'offered', 'saved', 'ignored']

type TabKey = 'jobs' | 'profile' | 'resume' | 'insight'

/** job 记录兼容两种字段命名（status / application_status）。 */
const jobStatus = (j: JobItem): string => j.application_status ?? j.status ?? ''
const jobId = (j: JobItem): string => j.job_id ?? j.id ?? ''

function safeStr(v: unknown): string {
  return typeof v === 'string' ? v : Array.isArray(v) ? v.join(', ') : v != null ? String(v) : ''
}

export function JobHunterModule() {
  const [tab, setTab] = useState<TabKey>('jobs')

  const [jobs, setJobs] = useState<JobItem[]>([])
  const [stats, setStats] = useState<JobStats | null>(null)
  const [platforms, setPlatforms] = useState<JobPlatform[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [acting, setActing] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [scraping, setScraping] = useState(false)

  /* 画像 tab */
  const [profile, setProfile] = useState<JobProfile | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [extractText, setExtractText] = useState('')
  const [extracting, setExtracting] = useState(false)

  /* 简历 tab */
  const [resumeText, setResumeText] = useState('')
  const [targetRole, setTargetRole] = useState('')
  const [industry, setIndustry] = useState('')
  const [resumeResult, setResumeResult] = useState<{ kind: string; title: string; body: string } | null>(null)
  const [resumeBusy, setResumeBusy] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  /* 智能分析 tab */
  const [selectedJob, setSelectedJob] = useState<JobItem | null>(null)
  const [analysisText, setAnalysisText] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const [recommendations, setRecommendations] = useState<JobItem[]>([])
  const [insightOut, setInsightOut] = useState<{ kind: string; title: string; body: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const [jobRes, statRes, platRes] = await Promise.all([
        client.call<{ success?: boolean; jobs?: JobItem[]; items?: JobItem[] }>('job_hunter.jobs.list', { limit: 50, status: filter || undefined }),
        client.call<{ success?: boolean; stats?: JobStats }>('job_hunter.stats', {}),
        client.call<{ success?: boolean; platforms?: JobPlatform[] }>('job_hunter.platforms', {}),
      ])
      // 后端返回 {jobs}；兼容旧 {items} 字段
      const list = Array.isArray(jobRes?.jobs) ? jobRes.jobs : Array.isArray(jobRes?.items) ? jobRes.items : []
      setJobs(list)
      setStats(statRes?.stats ?? null)
      setPlatforms(Array.isArray(platRes?.platforms) ? platRes.platforms : [])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取求职数据失败')
    } finally { setLoading(false) }
  }, [filter])

  useEffect(() => { void load() }, [load])

  const loadProfile = useCallback(async () => {
    setProfileLoading(true)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const res = await client.call<{ success?: boolean; profile?: JobProfile }>('job_hunter.profile.get', {})
      setProfile(res?.profile ?? null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取画像失败')
    } finally { setProfileLoading(false) }
  }, [])

  useEffect(() => { if (tab === 'profile') void loadProfile() }, [tab, loadProfile])

  const scrape = useCallback(async () => {
    setScraping(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      await client.call('job_hunter.scrape.run', { platforms: [], keywords: [], city: '', page: 1 })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '启动抓取失败')
    } finally { setScraping(false); void load() }
  }, [load])

  const setStatus = useCallback(async (jobIdStr: string, status: string) => {
    setActing(jobIdStr)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      await client.call('job_hunter.jobs.set_status', { job_id: jobIdStr, status })
      setJobs((prev) => prev.map((j) => (jobId(j) === jobIdStr ? { ...j, application_status: status } : j)))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '更新状态失败')
    } finally { setActing(null) }
  }, [])

  /* 画像保存 */
  const saveProfile = useCallback(async () => {
    if (!profile) return
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      await client.call('job_hunter.profile.set', { profile })
      setError('画像已保存')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存画像失败')
    }
  }, [profile])

  /* 从简历提取画像 */
  const extractFromResume = useCallback(async () => {
    if (!extractText.trim()) return
    setExtracting(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const res = await client.call<{ success?: boolean; profile?: JobProfile; error?: string }>('job_hunter.profile.extract_from_resume', { resume_text: extractText })
      if (res?.success) {
        setProfile((prev) => ({ ...(prev ?? {}), ...(res.profile ?? {}) }))
        setError('已从简历提取画像，请检查并保存')
      } else {
        setError(res?.error ?? '提取失败')
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '提取失败')
    } finally { setExtracting(false) }
  }, [extractText])

  /* 简历上传解析（读文件 → base64 → upload_and_parse） */
  const onResumeFile = useCallback(async (file: File) => {
    setResumeBusy(true)
    setError(null)
    try {
      const buf = await file.arrayBuffer()
      const base64 = btoa(String.fromCharCode(...new Uint8Array(buf)))
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const res = await client.call<{ success?: boolean; text?: string; error?: string }>('job_hunter.resume.upload_and_parse', { filename: file.name, file_data: base64 })
      if (res?.success && res.text) {
        setResumeText(res.text)
        setResumeResult({ kind: 'parse', title: '简历解析成功', body: res.text.slice(0, 4000) })
      } else {
        setResumeResult({ kind: 'error', title: '解析失败', body: res?.error ?? '未知错误' })
      }
    } catch (reason) {
      setResumeResult({ kind: 'error', title: '解析失败', body: reason instanceof Error ? reason.message : '未知错误' })
    } finally { setResumeBusy(false) }
  }, [])

  const runResumeTool = useCallback(async (kind: 'diagnose' | 'rewrite' | 'section') => {
    if (!resumeText.trim()) { setError('请先输入或上传简历文本'); return }
    setResumeBusy(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const method = kind === 'diagnose' ? 'job_hunter.resume.diagnose' : kind === 'rewrite' ? 'job_hunter.resume.rewrite' : 'job_hunter.resume.rewrite_section'
      const params: Record<string, unknown> = { resume_text: resumeText, target_role: targetRole, industry }
      if (kind === 'section') params.section = '工作经历'
      const res = await client.call<{ success?: boolean; text?: string; content?: string; summary?: string; sections?: unknown; error?: string }>(method, params)
      if (res?.success) {
        const body = res.text ?? res.content ?? (res.summary ? `摘要：${res.summary}` : JSON.stringify(res.sections ?? {}, null, 2))
        const title = kind === 'diagnose' ? '简历诊断' : kind === 'rewrite' ? '简历改写' : '章节改写'
        setResumeResult({ kind, title, body: safeStr(body) })
      } else {
        setResumeResult({ kind: 'error', title: '操作失败', body: res?.error ?? '未知错误' })
      }
    } catch (reason) {
      setResumeResult({ kind: 'error', title: '操作失败', body: reason instanceof Error ? reason.message : '未知错误' })
    } finally { setResumeBusy(false) }
  }, [resumeText, targetRole, industry])

  /* 分析职位（流式） */
  const runAnalyze = useCallback(async (job: JobItem) => {
    const id = jobId(job)
    if (!id) return
    setSelectedJob(job)
    setAnalyzing(true)
    setAnalysisText('')
    setInsightOut(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      for await (const chunk of client.stream<string>('job_hunter.analyze_stream', { job_id: id })) {
        setAnalysisText((prev) => prev + chunk)
      }
    } catch (reason) {
      setAnalysisText((prev) => prev + `\n[分析中断: ${reason instanceof Error ? reason.message : '未知错误'}]`)
    } finally { setAnalyzing(false) }
  }, [])

  /* 职位级洞察：简历建议 / 面试准备 / 冷消息 */
  const runInsight = useCallback(async (kind: 'advice' | 'interview' | 'cold') => {
    const id = selectedJob ? jobId(selectedJob) : ''
    if (!id) return
    setInsightOut(null)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const method = kind === 'advice' ? 'job_hunter.resume_advice' : kind === 'interview' ? 'job_hunter.interview_prep' : 'job_hunter.cold_message.generate'
      const res = await client.call<{ success?: boolean; advice?: string; content?: string; message?: string; text?: string; error?: string }>(method, { job_id: id })
      if (res?.success) {
        const body = safeStr(res.advice ?? res.content ?? res.message ?? res.text)
        const title = kind === 'advice' ? '简历优化建议' : kind === 'interview' ? '面试准备' : '开场消息'
        setInsightOut({ kind, title, body })
      } else {
        setInsightOut({ kind: 'error', title: '获取失败', body: res?.error ?? '未知错误' })
      }
    } catch (reason) {
      setInsightOut({ kind: 'error', title: '获取失败', body: reason instanceof Error ? reason.message : '未知错误' })
    }
  }, [selectedJob])

  /* 匹配全部 + 推荐 */
  const runMatch = useCallback(async () => {
    setActing('__match__')
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      await client.call('job_hunter.match', {})
      const [recRes] = await Promise.all([
        client.call<{ success?: boolean; jobs?: JobItem[] }>('job_hunter.recommendations', { limit: 10, min_score: 60 }),
      ])
      setRecommendations(Array.isArray(recRes?.jobs) ? recRes.jobs : [])
      void load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '匹配失败')
    } finally { setActing(null) }
  }, [load])

  const busy = acting !== null

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-auto bg-[var(--body-bg)] p-6" aria-labelledby="jh-heading" data-testid="jobhunter-workspace">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--separator)] pb-5">
        <div>
          <span className="agent-kicker">JOB HUNTER</span>
          <h1 id="jh-heading" className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">求职猎手</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">职位追踪 · 简历优化 · 匹配分析（后端 job_hunter.* 26 个方法）</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={scrape} disabled={scraping || busy} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">
            {scraping ? '抓取中...' : '抓取职位'}
          </button>
          <button type="button" onClick={() => void load()} disabled={loading} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">
            {loading ? '读取中...' : '刷新'}
          </button>
        </div>
      </header>

      {/* Tabs */}
      <nav className="mt-4 flex items-center gap-1 border-b border-[var(--separator)]" aria-label="求职猎手功能">
        {([['jobs', '职位库'], ['profile', '我的画像'], ['resume', '简历工具'], ['insight', '智能分析']] as [TabKey, string][]).map(([k, label]) => (
          <button key={k} type="button" onClick={() => setTab(k)} className={`rounded-t-md px-4 py-2 text-sm transition-colors ${tab === k ? 'border-b-2 border-fluent-blue font-medium text-[var(--text-primary)]' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
            {label}
          </button>
        ))}
      </nav>

      {error && <div role="alert" className="mt-5 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-4 text-sm text-[var(--danger)]">{error}</div>}

      {/* ── Tab 1 职位库 ── */}
      {tab === 'jobs' && (
        <>
          <div className="mt-5 grid gap-4 md:grid-cols-4">
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
              <span className="text-xs text-[var(--text-muted)]">职位总数</span>
              <strong className="mt-2 block text-lg text-[var(--text-primary)]">{stats?.total ?? jobs.length}</strong>
            </div>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
              <span className="text-xs text-[var(--text-muted)]">已分析 / 已匹配</span>
              <strong className="mt-2 block text-lg text-[var(--text-primary)]">{stats?.analyzed ?? 0} / {stats?.matched ?? 0}</strong>
            </div>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
              <span className="text-xs text-[var(--text-muted)]">平均匹配分</span>
              <strong className="mt-2 block text-lg text-[var(--text-primary)]">{stats?.avg_match_score ?? 0}</strong>
            </div>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
              <span className="text-xs text-[var(--text-muted)]">招聘平台</span>
              <strong className="mt-2 block text-sm text-[var(--text-primary)]">{platforms.filter(p => p.enabled !== false && p.implemented !== false).length} 个可用</strong>
            </div>
          </div>

          <div className="mt-4 flex items-center justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-1 flex-wrap">
              {FILTER_STATUSES.map((s) => (
                <button key={s} type="button" onClick={() => setFilter(s)} className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${filter === s ? 'text-fluent-blue bg-fluent-blue/10' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
                  {s ? (STATUS_LABELS[s] || s) : '全部'}
                </button>
              ))}
            </div>
            <button type="button" onClick={() => void runMatch()} disabled={busy} className="rounded-md border border-[var(--separator)] px-3 py-1.5 text-xs text-[var(--text-secondary)] disabled:opacity-50">
              {acting === '__match__' ? '匹配中...' : '运行匹配 & 推荐'}
            </button>
          </div>

          {recommendations.length > 0 && (
            <div className="mt-3 rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
              <div className="mb-2 text-xs font-medium text-[var(--text-secondary)]">推荐职位（匹配分 ≥ 60）</div>
              <div className="flex flex-wrap gap-2">
                {recommendations.map((r) => (
                  <button key={jobId(r)} type="button" onClick={() => { setTab('insight'); void runAnalyze(r) }} className="rounded-md border border-[var(--separator)] px-2.5 py-1.5 text-left text-xs text-[var(--text-secondary)] hover:border-fluent-blue hover:text-fluent-blue">
                    {r.title}
                    <span className="ml-1.5 text-[var(--text-muted)]">{(r.match_score ?? 0).toFixed(0)} 分</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <main className="mt-3 min-h-0 flex-1">
            {loading ? (
              <p className="py-12 text-center text-sm text-[var(--text-muted)]">正在读取职位数据...</p>
            ) : jobs.length === 0 ? (
              <p className="py-12 text-center text-sm text-[var(--text-muted)]">{platforms.length > 0 ? '暂无匹配职位，请点击「抓取职位」开始。' : '暂无招聘平台数据，请检查后端配置。'}</p>
            ) : (
              <div className="overflow-auto rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)]">
                <table className="w-full min-w-[720px] text-xs">
                  <thead>
                    <tr className="border-b border-[var(--separator)]">
                      <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">职位</th>
                      <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">公司</th>
                      <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">城市 / 薪资</th>
                      <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">匹配分</th>
                      <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">平台</th>
                      <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">状态</th>
                      <th className="px-3 py-2 text-right text-[var(--text-muted)] font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((j) => {
                      const st = jobStatus(j)
                      return (
                        <tr key={jobId(j) || j.title} className="border-b border-[var(--separator)] last:border-b-0">
                          <td className="px-3 py-2.5">
                            <div className="font-medium text-[var(--text-primary)]">{j.title || '未命名'}</div>
                            {j.updated_at && <div className="text-xs text-[var(--text-muted)]">{new Date(j.updated_at).toLocaleDateString('zh-CN')}</div>}
                          </td>
                          <td className="px-3 py-2.5 text-[var(--text-secondary)]">{j.company || '—'}</td>
                          <td className="px-3 py-2.5 text-[var(--text-muted)] text-xs">
                            {[j.city, j.salary].filter(Boolean).join(' / ') || '—'}
                          </td>
                          <td className="px-3 py-2.5 text-xs">
                            {(j.match_score ?? 0) > 0
                              ? <span className="font-medium text-fluent-blue">{(j.match_score ?? 0).toFixed(0)}</span>
                              : <span className="text-[var(--text-muted)]">—</span>}
                          </td>
                          <td className="px-3 py-2.5 text-[var(--text-muted)] text-xs">{j.platform || '—'}</td>
                          <td className="px-3 py-2.5">
                            <span className="text-xs font-medium" style={{ color: st ? (STATUS_COLORS[st] || 'var(--text-secondary)') : 'var(--text-muted)' }}>{st ? (STATUS_LABELS[st] || st) : '—'}</span>
                          </td>
                          <td className="px-3 py-2.5 text-right">
                            <div className="flex items-center justify-end gap-1">
                              {['interested', 'applied', 'interviewing', 'offered', 'saved', 'ignored'].map((s) => (
                                <button
                                  key={s}
                                  type="button"
                                  onClick={() => void setStatus(jobId(j), s)}
                                  disabled={busy || acting === jobId(j)}
                                  className={`rounded px-1.5 py-0.5 text-xs transition-colors disabled:opacity-50 ${st === s ? 'bg-blue-500/10 text-blue-400' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--hover-bg)]'}`}
                                >
                                  {STATUS_LABELS[s] || s}
                                </button>
                              ))}
                              <button type="button" onClick={() => void runAnalyze(j)} disabled={analyzing} className="rounded px-1.5 py-0.5 text-xs text-[var(--text-muted)] hover:text-fluent-blue hover:bg-[var(--hover-bg)]" title="AI 分析职位">
                                <Icon icon="fa-solid fa-wand-magic-sparkles text-2xs" />
                              </button>
                              {j.url && (
                                <a href={j.url} target="_blank" rel="noopener noreferrer" className="rounded px-1.5 py-0.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]" title="查看原文">
                                  <Icon icon="fa-solid fa-arrow-up-right-from-square text-2xs" />
                                </a>
                              )}
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </main>
        </>
      )}

      {/* ── Tab 2 我的画像 ── */}
      {tab === 'profile' && (
        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-medium text-[var(--text-primary)]">求职画像</h2>
              <button type="button" onClick={() => void saveProfile()} disabled={profileLoading} className="rounded-md border border-[var(--separator)] px-3 py-1.5 text-xs text-[var(--text-secondary)] disabled:opacity-50">
                保存画像
              </button>
            </div>
            {profileLoading ? (
              <p className="py-8 text-center text-sm text-[var(--text-muted)]">读取画像中...</p>
            ) : (
              <div className="grid gap-3 text-sm">
                <Field label="姓名" value={profile?.name ?? ''} onChange={(v) => setProfile(p => ({ ...(p ?? {}), name: v }))} />
                <Field label="城市" value={profile?.city ?? ''} onChange={(v) => setProfile(p => ({ ...(p ?? {}), city: v }))} />
                <Field label="工作年限" type="number" value={String(profile?.years_of_experience ?? 0)} onChange={(v) => setProfile(p => ({ ...(p ?? {}), years_of_experience: Number(v) || 0 }))} />
                <Field label="技术栈（逗号分隔）" value={(profile?.tech_stack ?? []).join(', ')} onChange={(v) => setProfile(p => ({ ...(p ?? {}), tech_stack: v.split(/[,，]/).map(s => s.trim()).filter(Boolean) }))} />
                <Field label="目标岗位（逗号分隔）" value={(profile?.target_positions ?? []).join(', ')} onChange={(v) => setProfile(p => ({ ...(p ?? {}), target_positions: v.split(/[,，]/).map(s => s.trim()).filter(Boolean) }))} />
                <Field label="期望薪资" value={profile?.expected_salary ?? ''} onChange={(v) => setProfile(p => ({ ...(p ?? {}), expected_salary: v }))} />
                <Field label="学历" value={profile?.education ?? ''} onChange={(v) => setProfile(p => ({ ...(p ?? {}), education: v }))} />
                <Field label="行业偏好（逗号分隔）" value={(profile?.industry_preference ?? []).join(', ')} onChange={(v) => setProfile(p => ({ ...(p ?? {}), industry_preference: v.split(/[,，]/).map(s => s.trim()).filter(Boolean) }))} />
                <Field label="自我介绍" textarea value={profile?.self_introduction ?? ''} onChange={(v) => setProfile(p => ({ ...(p ?? {}), self_introduction: v }))} />
                <Field label="自定义提示词（可选）" textarea value={profile?.custom_prompt ?? ''} onChange={(v) => setProfile(p => ({ ...(p ?? {}), custom_prompt: v }))} />
              </div>
            )}
          </div>

          <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
            <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">从简历提取画像（AI）</h2>
            <textarea
              value={extractText}
              onChange={(e) => setExtractText(e.target.value)}
              placeholder="粘贴简历文本，AI 自动提取姓名/技术栈/目标岗位等画像字段…"
              className="h-56 w-full resize-none rounded-md border border-[var(--separator)] bg-[var(--input-bg)] p-3 text-xs text-[var(--text-primary)] outline-none focus:border-fluent-blue"
            />
            <button type="button" onClick={() => void extractFromResume()} disabled={extracting || !extractText.trim()} className="mt-3 rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">
              {extracting ? '提取中...' : 'AI 提取画像'}
            </button>
            {(profile?.resume_keywords?.length ?? 0) > 0 && (
              <div className="mt-4">
                <div className="mb-1.5 text-xs text-[var(--text-muted)]">简历关键词</div>
                <div className="flex flex-wrap gap-1.5">
                  {profile!.resume_keywords!.map((k) => (
                    <span key={k} className="rounded-full bg-fluent-blue/10 px-2.5 py-0.5 text-xs text-fluent-blue">{k}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Tab 3 简历工具 ── */}
      {tab === 'resume' && (
        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-medium text-[var(--text-primary)]">简历文本</h2>
              <button type="button" onClick={() => fileInputRef.current?.click()} disabled={resumeBusy} className="rounded-md border border-[var(--separator)] px-3 py-1.5 text-xs text-[var(--text-secondary)] disabled:opacity-50">
                上传简历文件
              </button>
              <input ref={fileInputRef} type="file" accept=".pdf,.doc,.docx,.txt,.md" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void onResumeFile(f); e.target.value = '' }} />
            </div>
            <textarea
              value={resumeText}
              onChange={(e) => setResumeText(e.target.value)}
              placeholder="输入或上传简历文本（PDF/DOCX/TXT 均可）…"
              className="h-64 w-full resize-none rounded-md border border-[var(--separator)] bg-[var(--input-bg)] p-3 text-xs text-[var(--text-primary)] outline-none focus:border-fluent-blue"
            />
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Field label="目标岗位" value={targetRole} onChange={setTargetRole} />
              <Field label="行业" value={industry} onChange={setIndustry} />
            </div>
            <div className="mt-3 flex gap-2">
              <button type="button" onClick={() => void runResumeTool('diagnose')} disabled={resumeBusy} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">
                {resumeBusy ? '处理中...' : 'AI 诊断评分'}
              </button>
              <button type="button" onClick={() => void runResumeTool('rewrite')} disabled={resumeBusy} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">
                AI 整份改写
              </button>
              <button type="button" onClick={() => void runResumeTool('section')} disabled={resumeBusy} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">
                改写工作经历
              </button>
            </div>
          </div>

          <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
            <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">AI 结果</h2>
            {resumeResult ? (
              <div>
                <div className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{resumeResult.title}</div>
                <pre className="max-h-[480px] overflow-auto whitespace-pre-wrap rounded-md border border-[var(--separator)] bg-[var(--body-bg)] p-3 text-xs leading-relaxed text-[var(--text-primary)]">{resumeResult.body}</pre>
              </div>
            ) : (
              <p className="py-12 text-center text-sm text-[var(--text-muted)]">运行诊断或改写后，结果将显示在这里。</p>
            )}
          </div>
        </div>
      )}

      {/* ── Tab 4 智能分析 ── */}
      {tab === 'insight' && (
        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
            <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">职位 AI 分析（流式）</h2>
            {selectedJob ? (
              <div className="mb-3 text-xs text-[var(--text-muted)]">
                当前职位：<span className="font-medium text-[var(--text-primary)]">{selectedJob.title}</span>
                {selectedJob.company ? ` · ${selectedJob.company}` : ''}
              </div>
            ) : (
              <p className="mb-3 text-xs text-[var(--text-muted)]">在「职位库」点击职位行的 ✨ 图标开始分析，或点击推荐职位。</p>
            )}
            <pre className="min-h-[240px] max-h-[420px] overflow-auto whitespace-pre-wrap rounded-md border border-[var(--separator)] bg-[var(--body-bg)] p-3 text-xs leading-relaxed text-[var(--text-primary)]">
              {analyzing ? `${analysisText}▍` : analysisText || '（暂无分析结果）'}
            </pre>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" onClick={() => void runInsight('advice')} disabled={!selectedJob} className="rounded-md border border-[var(--separator)] px-3 py-1.5 text-xs text-[var(--text-secondary)] disabled:opacity-40">
                简历优化建议
              </button>
              <button type="button" onClick={() => void runInsight('interview')} disabled={!selectedJob} className="rounded-md border border-[var(--separator)] px-3 py-1.5 text-xs text-[var(--text-secondary)] disabled:opacity-40">
                面试准备
              </button>
              <button type="button" onClick={() => void runInsight('cold')} disabled={!selectedJob} className="rounded-md border border-[var(--separator)] px-3 py-1.5 text-xs text-[var(--text-secondary)] disabled:opacity-40">
                生成开场消息
              </button>
            </div>
          </div>

          <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
            <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">职位级洞察</h2>
            {insightOut ? (
              <div>
                <div className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{insightOut.title}</div>
                <pre className="max-h-[480px] overflow-auto whitespace-pre-wrap rounded-md border border-[var(--separator)] bg-[var(--body-bg)] p-3 text-xs leading-relaxed text-[var(--text-primary)]">{insightOut.body}</pre>
              </div>
            ) : (
              <p className="py-12 text-center text-sm text-[var(--text-muted)]">选中职位后，点击上方按钮生成简历建议 / 面试准备 / 开场消息。</p>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

/* ── 小部件 ── */

function Field({ label, value, onChange, textarea, type }: {
  label: string
  value: string
  onChange: (v: string) => void
  textarea?: boolean
  type?: string
}) {
  const cls = 'w-full rounded-md border border-[var(--separator)] bg-[var(--input-bg)] px-3 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-fluent-blue'
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-[var(--text-muted)]">{label}</span>
      {textarea
        ? <textarea value={value} onChange={(e) => onChange(e.target.value)} className={`${cls} resize-none`} rows={3} />
        : <input type={type ?? 'text'} value={value} onChange={(e) => onChange(e.target.value)} className={cls} />}
    </label>
  )
}
