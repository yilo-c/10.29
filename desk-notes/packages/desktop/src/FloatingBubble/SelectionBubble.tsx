import React, { useState, useEffect, useCallback, useRef } from 'react'
import { actionsForDisplay, executePatternAction, isCapturableText } from './patternActions'

interface BubbleState {
  text: string
  x: number
  y: number
}

interface RunState {
  actionId: string
  busy: boolean
  result: string
  error: string
}

const MAX_SELECTION = 5000

function selectionInEditable(): boolean {
  const el = document.activeElement
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || (el as HTMLElement).isContentEditable === true
}

/**
 * SelectionBubble — 划词浮窗动作菜单（V1-7 捕获接线）。
 *
 * 在悬浮层窗口内选中文字 → 浮出动作条 → 动作走后端 pattern.execute RPC
 * （带 PATTERN_EXECUTE_TIMEOUT 超时），失败在气泡内行内提示，不静默。
 */
const SelectionBubble: React.FC = () => {
  const [bubble, setBubble] = useState<BubbleState | null>(null)
  const [run, setRun] = useState<RunState | null>(null)
  const [showMore, setShowMore] = useState(false)
  const barRef = useRef<HTMLDivElement>(null)
  const { primary, more } = actionsForDisplay()

  const detect = useCallback(() => {
    const sel = window.getSelection()
    const text = sel ? sel.toString() : ''
    // ⑥ 空 payload 防御：trim 后为空不弹、不发
    if (!isCapturableText(text) || selectionInEditable()) {
      setBubble(null)
      return
    }
    const range = sel && sel.rangeCount > 0 ? sel.getRangeAt(0) : null
    const rect = range ? range.getBoundingClientRect() : null
    setBubble({
      text: text.slice(0, MAX_SELECTION),
      x: Math.max(8, Math.min((rect?.left ?? 80) + (rect?.width ?? 0) / 2, window.innerWidth - 8)),
      y: Math.max(40, (rect?.top ?? 40) - 8),
    })
    setRun(null)
    setShowMore(false)
  }, [])

  useEffect(() => {
    const onMouseUp = () => { setTimeout(detect, 0) }
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') { setBubble(null); setRun(null) } }
    const onDocDown = (e: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node)) {
        setBubble(null)
        setRun(null)
      }
    }
    document.addEventListener('mouseup', onMouseUp)
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onDocDown)
    return () => {
      document.removeEventListener('mouseup', onMouseUp)
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('mousedown', onDocDown)
    }
  }, [detect])

  const runAction = useCallback(async (actionId: string) => {
    if (!bubble || run?.busy) return
    setRun({ actionId, busy: true, result: '', error: '' })
    const resp = await executePatternAction(actionId, bubble.text)
    setRun({ actionId, busy: false, result: resp.result, error: resp.error })
  }, [bubble, run])

  if (!bubble) return null
  const label = (id: string) => [...primary, ...more].find(a => a.id === id)?.label ?? id

  return (
    <div
      ref={barRef}
      data-testid="selection-bubble"
      className="fixed z-[9999] flex flex-col gap-1 rounded-xl border shadow-2xl backdrop-blur-xl p-1.5"
      style={{
        left: bubble.x,
        top: bubble.y,
        transform: 'translate(-50%, -100%)',
        background: 'var(--panel-bg-solid, rgba(26,26,36,0.96))',
        borderColor: 'var(--panel-border-accent, rgba(96,165,250,0.25))',
        pointerEvents: 'auto',
        maxWidth: 320,
      }}
      onMouseUp={e => e.stopPropagation()}
    >
      <div className="flex items-center gap-0.5 flex-wrap">
        {primary.map(a => (
          <button
            key={a.id}
            onClick={() => void runAction(a.id)}
            disabled={run?.busy === true}
            className="px-2 py-1 rounded-lg text-[11px] transition-all disabled:opacity-40"
            style={{ background: run?.actionId === a.id ? 'rgba(96,165,250,0.25)' : 'rgba(255,255,255,0.05)', color: 'rgba(255,255,255,0.75)' }}
            title={`pattern.execute → ${a.label}`}
          >
            {a.icon} {a.label}
          </button>
        ))}
        <button
          onClick={() => setShowMore(v => !v)}
          className="px-2 py-1 rounded-lg text-[11px]"
          style={{ background: 'rgba(255,255,255,0.05)', color: 'rgba(255,255,255,0.55)' }}
          title="更多动作"
        >
          …
        </button>
      </div>
      {showMore && (
        <div className="flex items-center gap-0.5 flex-wrap">
          {more.map(a => (
            <button
              key={a.id}
              onClick={() => void runAction(a.id)}
              disabled={run?.busy === true}
              className="px-2 py-1 rounded-lg text-[11px] disabled:opacity-40"
              style={{ background: 'rgba(255,255,255,0.05)', color: 'rgba(255,255,255,0.65)' }}
            >
              {a.icon} {a.label}
            </button>
          ))}
        </div>
      )}
      {run && (
        <div
          className="px-2 py-1.5 rounded-lg text-[11px] leading-relaxed overflow-y-auto whitespace-pre-wrap break-words"
          style={{
            maxHeight: 140,
            background: run.error ? 'rgba(248,113,113,0.1)' : 'rgba(34,197,94,0.08)',
            color: run.error ? 'rgb(248,113,113)' : 'var(--text-primary, rgba(255,255,255,0.85))',
          }}
        >
          {run.busy ? `「${label(run.actionId)}」处理中…（超时保护已启用）`
            : run.error ? <>❌ {label(run.actionId)} 失败：{run.error}
                <button
                  onClick={() => void runAction(run.actionId)}
                  className="ml-2 underline"
                  style={{ color: 'var(--brand, #93c5fd)' }}
                >
                  重试
                </button>
              </>
            : run.result || '（无输出）'}
          {!run.busy && run.result && (
            <button
              onClick={() => { void navigator.clipboard?.writeText(run.result) }}
              className="ml-2 underline"
              style={{ color: 'var(--brand, #93c5fd)' }}
            >
              复制
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default SelectionBubble
