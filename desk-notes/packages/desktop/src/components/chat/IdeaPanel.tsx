import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from '../../i18n'
import { getWsClient } from '../../utils/ai'
import { uid } from '../../utils/helpers'
import { useStore } from '../../store/useStore'
import { errorStore } from '../../store/errorStore'
import { enqueueSyncOp, subscribeSyncQueue, subscribeSyncQueueDrops, type QueuedOp } from '@desk-notes/shared'

interface IdeaItem {
  id: string
  content: string
  createdAt: number
  /** 后端收件箱条目 id（int，运行时形状；C-14①-3：禁止 String() 归一——repo 按 int 严格匹配，字符串 id 会让归档/删除静默 no-op） */
  entryId?: string | number
  /** 离线乐观条目：已入 syncQueue 等待重放，队列清空后由回读替换为真实条目 */
  pending?: boolean
  /** C-2：对应队列条目重试超限被丢弃——红显 + 可删除，消除"永远待同步"的误导 */
  failed?: boolean
}

interface InboxEntryLite {
  id: string | number
  content: string
  source?: string
  created_at?: string
}

/** 速记条目转笔记的展开提示词：短想法 → 结构化笔记 */
const EXPAND_PROMPT = (text: string) =>
  `请把下面这条简短想法展开成一篇结构化笔记：第一行输出合适的标题（以 # 开头），随后分「背景/思路/下一步」三节充实内容，保持原意、可以合理引申，直接输出笔记正文，不要任何解释：\n\n${text}`

/**
 * 速记工作台（对话右侧独立栏，2026-09-01 升级）
 *
 * 相对旧版"单调输入框"的四项升级（用户批准的 A+B 组）：
 * - A1 数据闭环：列表从后端收件箱回读（source=idea_panel），重启不丢、计数可见
 * - A2 不丢：保存失败不再 swallowError 静默——离线入 syncQueue 持久队列并乐观展示，
 *      队列清空（重放成功）后自动回读替换为服务端真实条目
 * - A3 条目操作：转笔记 / 转待办 / 删除（复用 InboxModule 同一组 RPC）
 * - B1/B2 AI 增强：一键"AI 展开成笔记"（chat.send 流式收集 → notes.create）、
 *      "以此开聊"（CustomEvent 注入聊天输入框草稿，不劫持发送）
 */
export function IdeaPanel({ collapsed, onToggleCollapse }: { collapsed: boolean; onToggleCollapse: () => void }) {
  const { t } = useTranslation()
  const addFloatingNote = useStore((s) => s.addFloatingNote)
  const [input, setInput] = useState('')
  const [ideas, setIdeas] = useState<IdeaItem[]>([])
  const [saving, setSaving] = useState(false)
  const [expanding, setExpanding] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [pendingCount, setPendingCount] = useState(0)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const prevPending = useRef(0)

  /** A1 回读：收件箱中来源为速记的条目（离线 pending 乐观条目保留在顶部） */
  const loadIdeas = useCallback(async () => {
    try {
      const ws = getWsClient()
      // V1 修复（2026-09-16）：服务端 inbox.list 默认只回**最旧** 50 条（asc），
      // 而速记是近期创建的 → 取最旧页根本筛不到 idea_panel 来源。
      // 改为按最新优先取一页（order=desc，limit 用服务端上限 200）。
      const items = await ws.call<InboxEntryLite[]>('inbox.list', {
        include_archived: false,
        limit: 200,
        order: 'desc',
      })
      if (!Array.isArray(items)) return
      const server: IdeaItem[] = items
        .filter((e) => e && e.source === 'idea_panel' && typeof e.content === 'string' && e.content.trim())
        .slice(0, 50)
        .map((e) => ({
          id: `srv_${e.id}`,
          entryId: e.id,
          content: e.content,
          createdAt: e.created_at ? new Date(e.created_at).getTime() || Date.now() : Date.now(),
        }))
      setIdeas((prev) => [...prev.filter((i) => i.pending), ...server])
      setLoadFailed(false)
    } catch {
      setLoadFailed(true)
    }
  }, [])

  useEffect(() => {
    void loadIdeas()
    // A-3：断连时跳过轮询——call 在断连期间会挂起 _waitOpen 排队，
    // 重连惊群时会齐发触发限流。
    const timer = setInterval(() => {
      if (getWsClient()?.status !== 'connected') return
      void loadIdeas()
    }, 30_000)
    return () => clearInterval(timer)
  }, [loadIdeas])

  /** A2：订阅全局待同步计数；队列从有到无 = 离线操作已重放成功 → 立即回读转正 */
  useEffect(() => subscribeSyncQueue(setPendingCount), [])
  useEffect(() => {
    if (prevPending.current > 0 && pendingCount === 0) {
      setIdeas((prev) => prev.filter((i) => !i.pending))
      void loadIdeas()
    }
    prevPending.current = pendingCount
  }, [pendingCount, loadIdeas])

  /** C-2：毒丸丢弃感知——对应 pending 条目转为 failed（按 content 匹配乐观条目） */
  useEffect(() => subscribeSyncQueueDrops((op: QueuedOp) => {
    if (op.method !== 'inbox.add') return
    const content = typeof op.params.content === 'string' ? op.params.content : null
    if (content == null) return
    setIdeas((prev) => prev.map((i) => (
      i.pending && !i.entryId && i.content === content ? { ...i, failed: true } : i
    )))
  }), [])

  /** 保存 → 后端收件箱；失败降级入持久队列（tag 是稳定数据非 UI 文案，固定中文） */
  const handleSave = useCallback(async () => {
    const text = input.trim()
    if (!text || saving) return
    setSaving(true)
    const params = { content: text, source: 'idea_panel', tags: ['临时想法'] }
    try {
      const ws = getWsClient()
      const result = await ws.call<{ id?: string | number }>('inbox.add', params)
      setIdeas((prev) => [{ id: uid(), content: text, createdAt: Date.now(), entryId: result?.id != null ? result.id : undefined }, ...prev])
      setInput('')
      errorStore.success(t('chat.ideaPanel.saved'))
      if (inputRef.current) inputRef.current.focus()
    } catch {
      enqueueSyncOp('inbox.add', params)
      setIdeas((prev) => [{ id: uid(), content: text, createdAt: Date.now(), pending: true }, ...prev])
      setInput('')
      errorStore.warn(t('chat.ideaPanel.offlineQueued'))
    } finally {
      setSaving(false)
    }
  }, [input, saving, t])

  /** 弹出为便签 → 复用现有 FloatingNote 系统（自带 AI 对话） */
  const handlePopNote = useCallback(
    (text: string) => {
      const clean = text.trim()
      if (!clean) return
      addFloatingNote({
        id: uid(),
        type: 'text',
        title: t('chat.ideaPanel.noteTitle'),
        content: clean,
        tags: ['临时想法'],
        x: 120 + Math.random() * 160,
        y: 100 + Math.random() * 120,
        width: 300,
        height: 260,
        zIndex: useStore.getState().nextZ,
        createdAt: Date.now(),
        updatedAt: Date.now(),
      })
    },
    [addFloatingNote, t],
  )

  /** A3 条目操作：转笔记 / 转待办 / 删除（成功后归档出列，与 InboxModule 同通道） */
  const handleMove = useCallback(
    async (item: IdeaItem, target: 'note' | 'todo' | 'delete') => {
      if (!item.entryId) return
      try {
        const ws = getWsClient()
        // C-14①-3：inbox.update 失败时返回 {error} 而非抛错——必须检查，
        // 否则本地出列了、后端没动，刷新即"复活"（假删）
        const assertOk = (res: unknown) => {
          if (res && typeof res === 'object' && 'error' in res && (res as { error?: unknown }).error) {
            throw new Error(String((res as { error: unknown }).error))
          }
        }
        if (target === 'note') {
          assertOk(await ws.call('notes.create', { title: '', content: item.content, tags: ['来自速记'] }))
          assertOk(await ws.call('inbox.update', { entry_id: item.entryId, status: 'archived' }))
          errorStore.success(t('chat.ideaPanel.movedToNote'))
        } else if (target === 'todo') {
          assertOk(await ws.call('tasks.create', { title: item.content.slice(0, 100), tags: ['来自速记'] }))
          assertOk(await ws.call('inbox.update', { entry_id: item.entryId, status: 'archived' }))
          errorStore.success(t('chat.ideaPanel.movedToTodo'))
        } else {
          assertOk(await ws.call('inbox.update', { entry_id: item.entryId, status: 'dismissed' }))
          errorStore.info(t('chat.ideaPanel.removed'))
        }
        setIdeas((prev) => prev.filter((i) => i.id !== item.id))
      } catch {
        errorStore.error(t('chat.ideaPanel.opFailed'))
      }
    },
    [t],
  )

  /** B1 AI 展开：短想法 → 流式生成结构化笔记 → 落库（失败保留原文可重试） */
  const handleAiExpand = useCallback(async () => {
    const text = input.trim()
    if (!text || expanding) return
    setExpanding(true)
    let acc = ''
    try {
      const ws = getWsClient()
      for await (const chunk of ws.streamTyped('chat.send', { message: EXPAND_PROMPT(text), context_messages: [], stream: true })) {
        if (chunk.type === 'chunk' && chunk.data) acc += chunk.data
      }
      if (!acc.trim()) throw new Error('empty_expand_result')
      await ws.call('notes.create', { title: text.slice(0, 30), content: acc.trim(), tags: ['AI 展开'] })
      setInput('')
      errorStore.success(t('chat.ideaPanel.aiExpanded'))
    } catch {
      errorStore.error(t('chat.ideaPanel.aiExpandFailed'))
    } finally {
      setExpanding(false)
    }
  }, [input, expanding, t])

  /** B2 以此开聊：把想法注入聊天输入框为草稿（不自动发送，用户可再编辑） */
  const handleStartChat = useCallback((text: string) => {
    const clean = text.trim()
    if (!clean) return
    window.dispatchEvent(new CustomEvent('siyi:chat-draft', { detail: clean }))
  }, [])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Ctrl/Cmd + Enter 保存
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        void handleSave()
      }
    },
    [handleSave],
  )

  // 面板展开时聚焦输入框
  useEffect(() => {
    if (!collapsed && inputRef.current) inputRef.current.focus()
  }, [collapsed])

  if (collapsed) {
    return (
      <aside className="idea-panel idea-panel--collapsed" data-testid="idea-panel" aria-label={t('chat.ideaPanel.title')}>
        <button
          className="idea-panel-toggle"
          onClick={onToggleCollapse}
          title={t('chat.ideaPanel.expand')}
          aria-label={t('chat.ideaPanel.expand')}
        >
          <span className="idea-panel-bulb">💡</span>
        </button>
      </aside>
    )
  }

  return (
    <aside className="idea-panel" data-testid="idea-panel" aria-label={t('chat.ideaPanel.title')}>
      <div className="idea-panel-heading">
        <div>
          <span className="agent-kicker">{t('chat.ideaPanel.kicker')}</span>
          <h2>
            {t('chat.ideaPanel.title')}
            {ideas.length > 0 && <span className="idea-panel-count">{ideas.length}</span>}
          </h2>
        </div>
        <button
          className="w-6 h-6 flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-colors"
          onClick={onToggleCollapse}
          title={t('chat.ideaPanel.collapse')}
          aria-label={t('chat.ideaPanel.collapse')}
        >
          <span aria-hidden>›</span>
        </button>
      </div>

      <div className="idea-panel-input-wrap">
        <textarea
          ref={inputRef}
          className="idea-panel-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('chat.ideaPanel.placeholder')}
          rows={3}
        />
        <div className="idea-panel-actions">
          <button
            className="idea-panel-btn idea-panel-btn--primary"
            onClick={() => void handleSave()}
            disabled={saving || expanding || !input.trim()}
          >
            {saving ? t('chat.ideaPanel.saving') : t('chat.ideaPanel.save')}
          </button>
          <button
            className="idea-panel-btn"
            onClick={() => handlePopNote(input)}
            disabled={!input.trim()}
            title={t('chat.ideaPanel.popNote')}
          >
            {t('chat.ideaPanel.popNote')}
          </button>
        </div>
        <div className="idea-panel-actions">
          <button
            className="idea-panel-btn idea-panel-btn--ai"
            onClick={() => void handleAiExpand()}
            disabled={expanding || saving || !input.trim()}
          >
            {expanding ? t('chat.ideaPanel.aiExpanding') : t('chat.ideaPanel.aiExpand')}
          </button>
          <button
            className="idea-panel-btn"
            onClick={() => handleStartChat(input)}
            disabled={!input.trim()}
          >
            {t('chat.ideaPanel.startChat')}
          </button>
        </div>
      </div>

      <div className="idea-panel-list">
        {loadFailed && (
          <div className="idea-panel-loadfail">
            <span>{t('chat.ideaPanel.loadFailed')}</span>
            <button className="idea-panel-link" onClick={() => { setLoadFailed(false); void loadIdeas() }}>{t('chat.ideaPanel.retry')}</button>
          </div>
        )}
        {ideas.length === 0 && !loadFailed ? (
          <p className="idea-panel-empty">{t('chat.ideaPanel.empty')}</p>
        ) : (
          ideas.map((item) => (
            <div key={item.id} className="idea-panel-item">
              <p className="idea-panel-item-text">{item.content}</p>
              <div className="idea-panel-item-actions">
                <span className="idea-panel-item-time">
                  {new Date(item.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  {item.pending && (
                    <span className={item.failed ? 'idea-panel-pending idea-panel-pending--failed' : 'idea-panel-pending'}>
                      {item.failed ? t('chat.ideaPanel.syncFailed') : t('chat.ideaPanel.pending')}
                    </span>
                  )}
                </span>
                <span className="idea-panel-item-btns">
                  <button className="idea-panel-link" onClick={() => handleStartChat(item.content)}>{t('chat.ideaPanel.startChat')}</button>
                  {item.entryId ? (
                    <>
                      <button className="idea-panel-link" onClick={() => void handleMove(item, 'note')}>{t('chat.ideaPanel.toNote')}</button>
                      <button className="idea-panel-link" onClick={() => void handleMove(item, 'todo')}>{t('chat.ideaPanel.toTodo')}</button>
                      <button className="idea-panel-link idea-panel-link--danger" onClick={() => void handleMove(item, 'delete')}>{t('chat.ideaPanel.remove')}</button>
                    </>
                  ) : (
                    <>
                      <button className="idea-panel-link" onClick={() => handlePopNote(item.content)}>{t('chat.ideaPanel.popNote')}</button>
                      {item.failed && (
                        <button
                          className="idea-panel-link idea-panel-link--danger"
                          onClick={() => setIdeas((prev) => prev.filter((i) => i.id !== item.id))}
                        >
                          {t('chat.ideaPanel.remove')}
                        </button>
                      )}
                    </>
                  )}
                </span>
              </div>
            </div>
          ))
        )}
      </div>
    </aside>
  )
}
