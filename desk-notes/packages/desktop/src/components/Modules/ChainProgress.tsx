import { useEffect } from 'react'
import Icon from '../Common/Icon'

import { useChainProgress } from '../../hooks/useChainProgress'
import { useTranslation } from '../../i18n'

export default function ChainProgress() {
  const { t } = useTranslation()
  const { chain, clearChain, workflow, clearWorkflow } = useChainProgress()

  // Auto-dismiss after 8s on completion
  useEffect(() => {
    if (!chain || chain.status === 'running') return
    const timer = setTimeout(() => clearChain(), 8000)
    return () => clearTimeout(timer)
  }, [chain?.status, clearChain])

  useEffect(() => {
    if (!workflow || workflow.status === 'running') return
    const timer = setTimeout(() => clearWorkflow(), 8000)
    return () => clearTimeout(timer)
  }, [workflow?.status, clearWorkflow])

  // Show workflow progress when no chain is active
  const activeProgress = chain ?? workflow
  if (!activeProgress) return null

  const isWorkflow = !chain && !!workflow
  const isDone = activeProgress.status !== 'running'
  let pct: number
  if (isWorkflow) {
    pct = workflow!.total > 0 ? Math.round(workflow!.current / workflow!.total * 100) : 0
  } else {
    pct = chain!.total_steps > 0 ? Math.round((chain!.completed + chain!.failed) / chain!.total_steps * 100) : 0
  }
  const stepLabel = isWorkflow ? `${workflow!.current}/${workflow!.total}` : t('chainProgress.stepCount', { done: chain!.completed + chain!.failed, total: chain!.total_steps })

  return (
    <div className="border-t px-3 py-2 space-y-1.5" style={{ borderColor: 'var(--separator)', background: 'var(--panel-bg-solid)' }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
          {isDone ? (
            activeProgress.status === 'completed'
              ? <Icon icon="fa-solid fa-circle-check text-xs" style={{ color: 'var(--success)' }} />
              : <Icon icon="fa-solid fa-circle-exclamation text-xs" style={{ color: 'var(--danger)' }} />
          ) : (
            <Icon icon="fa-solid fa-circle-notch text-xs animate-spin" style={{ color: 'var(--text-muted)' }} />
          )}
          {isWorkflow && workflow!.status === 'running' ? (workflow!.label || t('chainProgress.running')) : isDone ? t('chainProgress.completed') : t('chainProgress.running')}
        </div>
        {!isDone && (
          <span className="text-[8px]" style={{ color: 'var(--text-dim)' }}>
            {stepLabel}
          </span>
        )}
      </div>

      {/* Progress bar */}
      <div className="h-1 rounded-full overflow-hidden" style={{ background: 'var(--hover-bg)' }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${pct}%`,
            background: isDone
              ? activeProgress.status === 'completed' ? 'var(--success)' : 'var(--danger)'
              : 'var(--brand-gradient)',
          }}
        />
      </div>

      {/* Steps (chain only — workflow shows current label) */}
      {!isWorkflow && chain!.nodes.length > 0 && (
        <div className="space-y-0.5 max-h-24 overflow-y-auto">
          {chain!.nodes.map((node) => (
            <div key={node.node_id} className="flex items-center gap-1.5 text-2xs" style={{ color: 'var(--text-secondary)' }}>
              {node.status === 'running' && (
                <Icon icon="fa-solid fa-spinner text-[7px] animate-spin" style={{ color: 'var(--brand)' }} />
              )}
              {node.status === 'completed' && (
                <Icon icon="fa-solid fa-check text-[7px]" style={{ color: 'var(--success)' }} />
              )}
              {node.status === 'failed' && (
                <Icon icon="fa-solid fa-xmark text-[7px]" style={{ color: 'var(--danger)' }} />
              )}
              {node.status === 'pending' && (
                <Icon icon="fa-regular fa-circle text-[7px]" style={{ color: 'var(--text-dim)' }} />
              )}
              <span className="truncate flex-1">{node.description}</span>
              {node.error && (
                <span className="text-[7px] truncate max-w-[120px]" style={{ color: 'var(--danger)' }}>{node.error}</span>
              )}
            </div>
          ))}
        </div>
      )}
      {isWorkflow && workflow!.status === 'running' && (
        <div className="flex items-center gap-1.5 text-2xs" style={{ color: 'var(--text-secondary)' }}>
          <Icon icon="fa-solid fa-spinner text-[7px] animate-spin" style={{ color: 'var(--brand)' }} />
          <span className="truncate">{workflow!.label}</span>
        </div>
      )}
    </div>
  )
}
