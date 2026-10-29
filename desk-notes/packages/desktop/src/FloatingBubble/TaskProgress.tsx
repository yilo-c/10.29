import Icon from '../components/Common/Icon'
export interface TaskProgressData {
  task_id: string
  step: number
  total: number
  message: string
  seq?: number
}

interface Props {
  progress: TaskProgressData | null
}

export default function TaskProgress({ progress }: Props) {
  if (!progress || progress.total <= 0) return null

  const pct = Math.min(100, Math.round((progress.step / progress.total) * 100))
  const isComplete = progress.step >= progress.total
  const isError = progress.step <= 0 && progress.total > 0

  return (
    <div
      className="absolute bottom-1 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-2 py-1 rounded-full transition-all"
      style={{
        background: isError
          ? 'rgba(239,68,68,0.12)'
          : isComplete
            ? 'rgba(34,197,94,0.12)'
            : 'rgba(96,165,250,0.1)',
        border: `1px solid ${
          isError
            ? 'rgba(239,68,68,0.2)'
            : isComplete
              ? 'rgba(34,197,94,0.2)'
              : 'rgba(96,165,250,0.2)'
        }`,
        minWidth: 80,
        maxWidth: 160,
      }}
    >
      {/* Spinner or check */}
      {isComplete ? (
        <Icon icon="fa-solid fa-check text-[8px]" style={{ color: 'var(--success)' }} />
      ) : isError ? (
        <Icon icon="fa-solid fa-exclamation-triangle text-[8px]" style={{ color: 'var(--danger)' }} />
      ) : (
        <Icon icon="fa-solid fa-circle-notch text-[8px] animate-spin" style={{ color: 'var(--brand)' }} />
      )}

      {/* Message */}
      <span
        className="text-[10px] truncate"
        style={{
          color: isError ? '#fca5a5' : isComplete ? '#86efac' : '#93bbfc',
          maxWidth: 100,
        }}
      >
        {isComplete ? '完成' : progress.message}
      </span>

      {/* Progress bar */}
      <div
        className="h-1 rounded-full flex-shrink-0"
        style={{
          width: 36,
          background: 'rgba(255,255,255,0.08)',
          overflow: 'hidden',
        }}
      >
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{
            width: `${pct}%`,
            background: isError
              ? 'var(--danger)'
              : isComplete
                ? 'var(--success)'
                : 'var(--brand)',
          }}
        />
      </div>
    </div>
  )
}
