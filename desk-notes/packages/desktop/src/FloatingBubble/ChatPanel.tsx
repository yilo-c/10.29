import React, { useState, useRef } from 'react'
import Icon from '../components/Common/Icon'

import { useStore } from '../store/useStore'
import { useTranslation } from '../i18n'
import { COLOR_PRESETS } from '../utils/helpers'
import CustomThemeEditor from '../components/Common/CustomThemeEditor'
import ThemeScope from '../components/Common/ThemeScope'
import { MOOD_CONFIGS, type PetMood } from '../types/pet'
// F-P0：复用主窗 U1 批的等待状态收口与 M2.2 的审批卡，不另造一套。
import { WaitingStatus } from '../components/chat/WaitingStatus'
import { useWaitingStage } from '../components/chat/useWaitingStage'
import { normalizeAgentEvents } from '../components/chat/agentEvents'
import { ApprovalCard } from '../components/chat/ApprovalCard'
import { ConfirmationCard } from '../components/chat/ConfirmationCard'
import type { ChatStreamEvent } from '../components/chat/useChatStream'
import type { ApprovalItem } from '../hooks/useSandboxApprovals'
import type { ConfirmItem } from '../hooks/useConfirmApprovals'
// Task #9 对话附件：复用主窗同一套采集逻辑与守卫口径（chatAttachments.ts 单一真源）
import { collectAttachments, formatSize, toWireParam, type PendingAttachment, type WireAttachment } from '../components/chat/chatAttachments'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export type { ChatMessage }

interface ChatPanelProps {
  mood: PetMood
  theme: 'dark' | 'light'
  onThemeChange: (t: 'dark' | 'light') => void
  onMinimize: () => void
  onClose: () => void
  onSend: (text: string, attachments?: WireAttachment[]) => void
  messages: ChatMessage[]
  /** Called when user drags the panel header to reposition */
  onMovePanel?: (x: number, y: number) => void
  /** When true, uses Electron window-level drag (main-process cursor polling) instead of DOM mousemove */
  windowDrag?: boolean
  /** Initial panel width */
  initWidth?: number
  /** Initial panel height */
  initHeight?: number
  /** Called when user resizes the panel */
  onResize?: (w: number, h: number) => void
  /** Selected AI provider */
  selectedProvider?: string
  /** Provider change handler */
  onProviderChange?: (id: string) => void
  /** Available provider options */
  providers?: ReadonlyArray<{ id: string; label: string }>
  /** F-P0：本轮流是否在途（驱动等待状态行） */
  streaming?: boolean
  /** F-P0：本轮流开始时刻（前端派生计时，null=非流式） */
  streamStartedAt?: number | null
  /** F-P0：放行的元数据帧（tool_progress/tool_result…），渲染为轻量执行流 */
  streamEvents?: ChatStreamEvent[]
  /** F-P0：R4 待批队列（sandbox.list_pending + sandbox.pending_added 驱动） */
  pendingApprovals?: ApprovalItem[]
  /** F-P0：批准 → sandbox.approve {pending_id} */
  onApproveApproval?: (pendingId: string) => Promise<{ ok: boolean; error?: string }>
  /** F-P0：拒绝 → sandbox.reject {pending_id} */
  onRejectApproval?: (pendingId: string) => Promise<{ ok: boolean; error?: string }>
  /** F-P1-1：队列2（butler CONFIRM）待批条目（action.confirm_required 驱动）。
   *  与队列1 sandbox 审批是两套独立队列（id 空间不互通，混用 → expired_or_not_found）。 */
  pendingConfirmations?: ConfirmItem[]
  /** F-P1-1：批准 → confirmation.resolve {confirmation_id, approved:true, reason} → chat.resume */
  onApproveConfirmation?: (confirmationId: string, reason: string, alwaysAllow: boolean) => Promise<{ ok: boolean; expired?: boolean; error?: string }>
  /** F-P1-1：拒绝 → confirmation.resolve {approved:false, reason?}（不 resume） */
  onRejectConfirmation?: (confirmationId: string, reason?: string) => Promise<{ ok: boolean; expired?: boolean; error?: string }>
}

const MIN_W = 260
const MIN_H = 300

const ChatPanel: React.FC<ChatPanelProps> = ({
  mood, theme, onThemeChange, onMinimize, onClose, onSend, messages,
  onMovePanel, windowDrag = false, initWidth = 320, initHeight = 420, onResize,
  selectedProvider, onProviderChange, providers,
  streaming = false, streamStartedAt = null, streamEvents = [],
  pendingApprovals = [], onApproveApproval, onRejectApproval,
  pendingConfirmations = [], onApproveConfirmation, onRejectConfirmation,
}) => {
  const { t } = useTranslation()
  const panel = useStore(s => s.panel)
  const locale = useStore(s => s.locale)
  const togglePanelPin = useStore(s => s.togglePanelPin)
  const togglePanelLock = useStore(s => s.togglePanelLock)
  const setLocale = useStore(s => s.setLocale)
  const setPanelColor = useStore(s => s.setPanelColor)
  // Local appearance state — scoped to this panel only
  const [localBgMode, setLocalBgMode] = useState<'acrylic' | 'pure-white' | 'pure-black'>('acrylic')
  const [localOpacity, setLocalOpacity] = useState(0.8)
  const [showAppearance, setShowAppearance] = useState(false)
  const [closeHover, setCloseHover] = useState(false)
  const [showProviderMenu, setShowProviderMenu] = useState(false)
  const [input, setInput] = useState('')
  // Task #9 对话附件（与主窗 ChatComposer 同口径，逻辑在 chatAttachments.ts 单一真源）
  const [attachments, setAttachments] = useState<PendingAttachment[]>([])
  const [attachNotice, setAttachNotice] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const appearanceBtnRef = useRef<HTMLButtonElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Panel size (resizable)
  const [panelW, setPanelW] = useState(initWidth)
  const [panelH, setPanelH] = useState(initHeight)
  const panelWRef = useRef(panelW)
  const panelHRef = useRef(panelH)

  // ── Header drag ──
  const headerRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef({ startX: 0, startY: 0, origX: 0, origY: 0 })
  const [dragging, setDragging] = useState(false)

  const handleHeaderMouseDown = (e: React.MouseEvent) => {
    // Ignore clicks on buttons inside the header
    if ((e.target as HTMLElement).closest('button')) return
    e.preventDefault()

    // Window-level drag via main-process cursor polling
    if (windowDrag) {
      const ei = window.electronAPI as ElectronAPI | undefined
      if (ei?.dragStart) {
        const offsetX = window.screenX - e.screenX
        const offsetY = window.screenY - e.screenY
        ei.dragStart(offsetX, offsetY)
        setDragging(true)
        const onUp = () => {
          setDragging(false)
          ei.dragEnd()
          window.removeEventListener('mouseup', onUp)
        }
        window.addEventListener('mouseup', onUp)
        return
      }
    }

    if (!onMovePanel) return
    const parent = (e.currentTarget as HTMLElement).parentElement!
    const rect = parent.getBoundingClientRect()
    dragRef.current = { startX: e.clientX, startY: e.clientY, origX: rect.left, origY: rect.top }
    setDragging(true)

    const onMove = (ev: MouseEvent) => {
      onMovePanel(
        dragRef.current.origX + ev.clientX - dragRef.current.startX,
        dragRef.current.origY + ev.clientY - dragRef.current.startY,
      )
    }
    const onUp = () => {
      setDragging(false)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  // ── Resize handle (bottom-right) ──
  const [resizing, setResizing] = useState(false)
  const resizeRef = useRef({ startX: 0, startY: 0, startW: 0, startH: 0 })

  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    resizeRef.current = { startX: e.clientX, startY: e.clientY, startW: panelWRef.current, startH: panelHRef.current }
    setResizing(true)

    const onMove = (ev: MouseEvent) => {
      const dw = ev.clientX - resizeRef.current.startX
      const dh = ev.clientY - resizeRef.current.startY
      const newW = Math.max(MIN_W, resizeRef.current.startW + dw)
      const newH = Math.max(MIN_H, resizeRef.current.startH + dh)
      panelWRef.current = newW
      panelHRef.current = newH
      setPanelW(newW)
      setPanelH(newH)
      onResize?.(newW, newH)
    }
    const onUp = () => {
      setResizing(false)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  // Auto-scroll to bottom on new messages
  React.useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // F-P0 执行流：与主窗同一套归一化（agentEvents.normalizeAgentEvents），只换渲染密度。
  const executionEvents = React.useMemo(() => normalizeAgentEvents(streamEvents), [streamEvents])
  // F-P0 等待状态：复用 U1 收口（notice 文案优先，否则阶段推断；计时前端 startedAt 派生）。
  const waitingStage = useWaitingStage(streamEvents, streaming ? streamStartedAt : null)

  const moodCfg = MOOD_CONFIGS[mood]

  // ── Button styling matching HeaderBar exactly ──
  const iconBtn = 'w-6 h-6 flex items-center justify-center rounded-md transition-all text-xs'

  const closeBtn = closeHover
    ? `${iconBtn} text-red-400/60 hover:bg-white/[0.06]`
    : `${iconBtn} text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]`

  const addFiles = async (files: File[]) => {
    if (!files.length) return
    const { accepted, rejected } = await collectAttachments(files, attachments)
    if (accepted.length) setAttachments((prev) => [...prev, ...accepted])
    setAttachNotice(rejected.length ? rejected.map((r) => `${r.name}：${r.reason}`).join('；') : '')
  }

  const removeAttachment = (id: string) => setAttachments((prev) => prev.filter((a) => a.id !== id))

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    onSend(text, toWireParam(attachments))
    setInput('')
    setAttachments([])
    setAttachNotice('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // Auto-resize textarea
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }

  return (
    <ThemeScope theme={theme} backgroundMode={localBgMode}>
      <div
        className="flex flex-col rounded-xl border select-none overflow-hidden fluent-shadow-lg"
        style={{
          background: 'var(--panel-bg)',
          borderColor: 'var(--panel-border)',
          height: panelH,
          width: panelW,
          opacity: localOpacity,
          position: 'relative',
        }}
      >
        {/* ── Header (draggable, exactly matches HeaderBar.tsx style) ── */}
        <div
          ref={headerRef}
          className={`flex items-center justify-between px-3 h-9 flex-shrink-0 border-b ${dragging ? 'cursor-grabbing' : (onMovePanel || windowDrag) ? 'cursor-grab' : ''}`}
          style={{ borderColor: 'var(--separator)', WebkitAppRegion: 'no-drag' } as unknown as React.CSSProperties}
          onMouseDown={handleHeaderMouseDown}
        >
          {/* Left: mood dot + title + provider selector */}
          <div className="flex items-center gap-2 min-w-0">
            <div
              className="w-2 h-2 rounded-full flex-shrink-0"
              style={{ background: moodCfg.color }}
            />
            <span className="text-[11px] font-medium tracking-wide whitespace-nowrap"
              style={{ color: 'var(--text-primary)' }}>
              小问
            </span>
            <span className="text-xs truncate max-w-[80px]"
              style={{ color: 'var(--text-muted)' }}>
              · {moodCfg.label}
            </span>
            {/* Provider selector */}
            {providers && onProviderChange && (
              <div className="relative ml-1">
                <button
                  onClick={() => setShowProviderMenu(!showProviderMenu)}
                  className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-text-secondary hover:text-text-secondary hover:bg-white/[0.04] transition-colors"
                >
                  {providers.find(p => p.id === selectedProvider)?.label || '自动'}
                  <Icon icon="fa-solid fa-chevron-down text-[7px]" />
                </button>
                {showProviderMenu && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setShowProviderMenu(false)} />
                    <div className="absolute top-full left-0 mt-1 z-50 bg-[var(--surface-elevated)] border border-white/[0.08] rounded-lg py-1 shadow-xl min-w-[100px]">
                      {providers.map(p => (
                        <button
                          key={p.id}
                          onClick={() => { onProviderChange(p.id); setShowProviderMenu(false) }}
                          className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-xs transition-colors text-left ${
                            selectedProvider === p.id
                              ? 'text-fluent-blue bg-white/[0.04]'
                              : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.03]'
                          }`}
                        >
                          {selectedProvider === p.id && <Icon icon="fa-solid fa-check text-[8px] text-fluent-blue" />}
                          <span className={selectedProvider === p.id ? '' : 'ml-3.5'}>{p.label}</span>
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>

          {/* Right: buttons matching HeaderBar exactly + minimize/close */}
          <div className="flex items-center gap-0.5">
            {/* Pin (same as HeaderBar) */}
            <button onClick={togglePanelPin}
              className={`${iconBtn} ${panel.pinned ? 'text-fluent-blue/70' : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]'}`}
              title={panel.pinned ? t('note.unpin') : t('note.pin')}>
              <Icon icon="fa-solid fa-thumbtack" />
            </button>

            {/* Lock (same as HeaderBar) */}
            <button onClick={togglePanelLock}
              className={`${iconBtn} ${panel.locked ? 'text-amber-400/60' : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]'}`}
              title={t('lock.title')}>
              <Icon icon="fa-solid fa-lock" />
            </button>

            {/* Appearance (same as HeaderBar — inline dropdown) */}
            <div className="relative">
              <button ref={appearanceBtnRef}
                onClick={() => setShowAppearance(!showAppearance)}
                className={iconBtn + ' text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]'}
                title={t('note.appearance')}>
                <Icon icon="fa-solid fa-circle-half-stroke" />
              </button>
              {showAppearance && (
                <div
                  className="absolute top-full right-0 mt-1 z-50 border border-white/[0.08] rounded-xl px-3 py-2 shadow-xl flex flex-col gap-2 min-w-[180px]"
                  onMouseDown={e => e.stopPropagation()}
                  style={{ background: 'var(--panel-bg-solid)' }}
                >
                  {/* Theme */}
                  <div>
                    <div className="text-xs text-text-secondary mb-1.5">{t('note.theme.title')}</div>
                    <div className="flex items-center gap-1">
                      <button onClick={() => { if (theme !== 'dark') onThemeChange('dark') }}
                        className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${theme === 'dark' ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'}`}>
                        {t('note.theme.dark')}
                      </button>
                      <button onClick={() => { if (theme !== 'light') onThemeChange('light') }}
                        className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${theme === 'light' ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'}`}>
                        {t('note.theme.light')}
                      </button>
                    </div>
                  </div>
                  <div className="border-t border-white/[0.06]" />
                  {/* Background mode */}
                  <div>
                    <div className="text-xs text-text-secondary mb-1.5">{t('settings.background')}</div>
                    <div className="flex items-center gap-1">
                      {(['acrylic', 'pure-white', 'pure-black'] as const).map(m => (
                        <button key={m} onClick={() => setLocalBgMode(m)}
                          className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${localBgMode === m ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'}`}>
                          {t('backgroundMode.' + m)}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="border-t border-white/[0.06]" />
                  {/* Opacity */}
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-text-secondary w-7">{t('settings.opacity')}</span>
                    <input type="range" min="0.15" max="1" step="0.05" value={localOpacity}
                      onChange={e => setLocalOpacity(parseFloat(e.target.value))}
                      className="flex-1 h-1 accent-fluent-blue cursor-pointer" />
                    <span className="text-xs text-text-secondary w-5 text-right">{Math.round(localOpacity * 100)}%</span>
                  </div>
                  <div className="border-t border-white/[0.06] pt-2">
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <span className="text-xs text-text-secondary">{t('settings.color')}</span>
                    </div>
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {COLOR_PRESETS.map(p => (
                        <button key={p.label} onClick={() => setPanelColor(p.color || undefined)}
                          className="w-4 h-4 rounded-full border border-white/[0.15] flex items-center justify-center transition-transform hover:scale-125"
                          style={{ background: p.color || 'transparent' }} title={p.label}>
                          {!p.color ? <span className="w-2 h-0.5 bg-white/30 rounded-full" /> : null}
                          {(panel.color || '') === p.color && <Icon icon="fa-solid fa-check text-[6px] text-text-primary" />}
                        </button>
                      ))}
                      <label className="w-4 h-4 rounded-full border border-dashed border-white/[0.25] flex items-center justify-center cursor-pointer hover:border-white/50 transition-colors"
                        title={t('note.customColor')}>
                        <Icon icon="fa-solid fa-plus text-[7px] text-text-secondary" />
                        <input type="color" value={panel.color || '#3b82f6'}
                          onChange={e => setPanelColor(e.target.value)}
                          className="absolute opacity-0 w-0 h-0" />
                      </label>
                    </div>
                  </div>
                  <div className="border-t border-white/[0.06] pt-2">
                    <div className="text-xs text-text-secondary mb-1.5">{t('settings.language')}</div>
                    <div className="flex items-center gap-1">
                      <button onClick={() => setLocale('zh-CN')}
                        className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${locale === 'zh-CN' ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'}`}>
                        中文
                      </button>
                      <button onClick={() => setLocale('en')}
                        className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${locale === 'en' ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'}`}>
                        English
                      </button>
                    </div>
                  </div>
                  <CustomThemeEditor />
                </div>
              )}
            </div>

            {/* Minimize (ChatPanel-specific: back to orb) */}
            <button onClick={onMinimize}
              className={iconBtn + ' text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]'}
              title="最小化">
              <Icon icon="fa-regular fa-window-minimize" />
            </button>

            {/* Close (ChatPanel-specific: back to orb) */}
            <button onClick={onClose}
              className={closeBtn}
              title="关闭"
              onMouseEnter={() => setCloseHover(true)}
              onMouseLeave={() => setCloseHover(false)}>
              <Icon icon="fa-solid fa-xmark" />
            </button>
          </div>
        </div>

        {/* ── Messages ── */}
        <div className="flex-1 overflow-y-auto px-3 py-2.5 space-y-2 min-h-0 select-text">
          {messages.length === 0 && (
            <div className="flex items-center justify-center h-full text-xs text-center px-4 select-none"
              style={{ color: 'var(--text-muted)' }}>
              问小问点什么吧～
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[85%] rounded-xl px-3 py-1.5 text-xs leading-relaxed whitespace-pre-wrap break-words ${
                msg.role === 'user'
                  ? 'bg-fluent-blue/15 text-text-primary rounded-br-md'
                  : 'bg-white/[0.04] text-text-secondary rounded-bl-md'
              }`}>
                {msg.content}
              </div>
            </div>
          ))}

          {/* F-P0 执行流：内核工具调用帧（tool_progress/tool_result）就地成行——
              此前这些帧被 frameRouting 静默丢弃，用户看不到内核真跑了什么。 */}
          {executionEvents.slice(-3).map((ev, i) => (
            <div key={ev.id || i} data-testid="floating-exec-event" className="flex justify-start">
              <div className="max-w-[85%] flex items-center gap-1.5 rounded-lg px-2 py-1 text-2xs text-text-secondary bg-white/[0.03] border border-white/[0.06]">
                <Icon icon="fa-solid fa-gear" className="text-text-muted flex-shrink-0" />
                <span className="font-medium text-text-primary flex-shrink-0">{ev.title}</span>
                {ev.detail ? <span className="truncate break-all">{ev.detail}</span> : null}
                <span
                  className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${ev.status === 'running' ? 'bg-brand animate-pulse' : ev.status === 'done' ? 'bg-success' : 'bg-warning'}`}
                  title={ev.status}
                />
              </div>
            </div>
          ))}

          {/* F-P0 等待状态：复用 U1 的 WaitingStatus（后端 notice 文案优先）。 */}
          {streaming && (
            <div data-testid="floating-waiting" className="flex justify-start">
              <div className="rounded-lg px-2 py-1 text-2xs" style={{ color: 'var(--text-muted)' }}>
                <WaitingStatus
                  startedAt={streamStartedAt ?? null}
                  stage={waitingStage.stage}
                  lastMessage={waitingStage.lastNoticeMessage}
                />
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* F-P0 R4 就地审批条（队列1）：R4 高危操作（删除类）走后端 sandbox 审批队列，
            悬浮窗此前无审批 UI → 从悬浮窗发起的指令永远 pending（管理链死锁）。
            契约（09-16 实测定案）：R4 走 sandbox.approve/sandbox.reject {pending_id}；
            confirmation.resolve 是另一套队列（TTL 300s + 必带 reason），不可混用，
            否则会得到 expired_or_not_found 假象。
            F-P1-1（2026-09-17）：TODO(F-P0-2) 已兑现 —— 下方并存队列2
            （action.confirm_required → ConfirmationCard → confirmation.resolve +
            chat.resume / 过期收态）。两套 UI 并存、状态机各走各的 id 空间。 */}
        {pendingApprovals.length > 0 && onApproveApproval && onRejectApproval && (
          <div
            data-testid="floating-approval-bar"
            className="flex-shrink-0 max-h-[45%] overflow-y-auto px-3 py-2 space-y-2 border-t"
            style={{ borderColor: 'var(--separator)' }}
          >
            {pendingApprovals.slice(0, 3).map(item => (
              <ApprovalCard
                key={item.pending_id}
                item={item}
                onApprove={onApproveApproval}
                onReject={onRejectApproval}
              />
            ))}
          </div>
        )}

        {/* F-P1-1 队列2 就地审批条（butler CONFIRM）：订阅 action.confirm_required，
            渲染 ConfirmationCard（理由必填 + 允许执行/始终允许/拒绝），
            批准后由宿主 confirmation.resolve → chat.resume 续跑到完成。 */}
        {pendingConfirmations.length > 0 && onApproveConfirmation && onRejectConfirmation && (
          <div
            data-testid="floating-confirmation-bar"
            className="flex-shrink-0 max-h-[45%] overflow-y-auto px-3 py-2 space-y-2 border-t"
            style={{ borderColor: 'var(--separator)' }}
          >
            {pendingConfirmations.slice(0, 3).map(item => (
              <ConfirmationCard
                key={item.confirmation_id}
                item={item}
                onApprove={onApproveConfirmation}
                onReject={onRejectConfirmation}
              />
            ))}
          </div>
        )}

        {/* ── Input bar ── */}
        <div className="flex-shrink-0 px-3 pb-3 pt-2 border-t"
          style={{ borderColor: 'var(--separator)' }}>
          {/* Task #9 待发附件 chips + 拒绝提示（与主窗同口径） */}
          {(attachments.length > 0 || attachNotice) && (
            <div className="mb-1.5 flex flex-col gap-1" data-testid="floating-attachment-strip">
              {attachments.map((a) => (
                <span key={a.id} className="inline-flex items-center gap-1.5 self-start rounded-full border px-2 py-0.5 text-2xs max-w-full"
                  style={{ borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}>
                  <Icon icon={a.isImage ? 'fa-solid fa-image' : 'fa-solid fa-file-lines'} />
                  <span className="truncate max-w-[140px]">{a.name}</span>
                  <span style={{ color: 'var(--text-secondary)' }}>{formatSize(a.sizeBytes)}</span>
                  <button type="button" onClick={() => removeAttachment(a.id)} aria-label={`移除附件 ${a.name}`}
                    className="opacity-60 hover:opacity-100">
                    <Icon icon="fa-solid fa-xmark" />
                  </button>
                </span>
              ))}
              {attachNotice && <span className="text-2xs text-red-400" role="alert">{attachNotice}</span>}
            </div>
          )}
          <div
            className="flex items-end gap-2 rounded-xl border px-3 py-2 transition-all"
            style={{
              background: 'var(--input-bg)',
              borderColor: 'var(--panel-border)',
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              data-testid="floating-attachment-input"
              onChange={(e) => {
                const files = Array.from(e.target.files || [])
                e.target.value = ''
                if (files.length) void addFiles(files)
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-fluent-blue/70 hover:text-fluent-blue hover:bg-fluent-blue/10 transition-all text-xs flex-shrink-0"
              title="添加附件（文本/图片；也可粘贴或拖入）"
              aria-label="添加附件"
            >
              <Icon icon="fa-solid fa-paperclip" />
            </button>
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              onPaste={(event) => {
                const files = Array.from(event.clipboardData?.files || [])
                if (files.length) {
                  event.preventDefault()
                  void addFiles(files)
                }
              }}
              onDragOver={(e) => { e.preventDefault() }}
              onDrop={(e) => {
                e.preventDefault()
                const files = Array.from(e.dataTransfer?.files || [])
                if (files.length) void addFiles(files)
              }}
              placeholder="问小问... (Enter 发送)"
              rows={1}
              className="flex-1 bg-transparent border-none outline-none resize-none text-xs leading-relaxed"
              style={{ color: 'var(--text-primary)', maxHeight: 120 }}
            />
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className="w-7 h-7 flex items-center justify-center rounded-lg bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-30 transition-all text-xs flex-shrink-0"
              title="发送"
            >
              <Icon icon="fa-solid fa-arrow-up" />
            </button>
          </div>
        </div>

        {/* ── Resize handle (bottom-right corner) ── */}
        <div
          onMouseDown={handleResizeStart}
          className="absolute right-0 bottom-0 w-4 h-4 cursor-nwse-resize z-50 group"
        >
          <svg
            viewBox="0 0 10 10"
            className={`absolute right-0.5 bottom-0.5 w-3 h-3 transition-all ${resizing ? 'text-text-muted' : 'text-text-dim group-hover:text-text-muted'}`}
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <line x1="2" y1="10" x2="10" y2="2" />
            <line x1="5" y1="10" x2="10" y2="5" />
          </svg>
        </div>
      </div>
    </ThemeScope>
  )
}

export default ChatPanel
