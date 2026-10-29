import React, { useRef, useEffect, useState } from 'react'
import Icon from '../Common/Icon'

import { useModels, type ModelInfo } from '@desk-notes/shared'
import { useTranslation } from '../../i18n'

interface ModelSelectorProps {
  selected: ModelInfo | null
  onSelect: (model: ModelInfo | null) => void
  disabled?: boolean
}

export const ModelSelector: React.FC<ModelSelectorProps> = ({ selected, onSelect, disabled }) => {
  const { t } = useTranslation()
  const { models, loading, error, refresh } = useModels()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const grouped = models.reduce<Record<string, ModelInfo[]>>((acc, m) => {
    (acc[m.provider] ??= []).push(m)
    return acc
  }, {})

  const shortLabel = selected
    ? `${selected.provider}:${selected.model.length > 12 ? selected.model.slice(0, 12) + '…' : selected.model}`
    : 'auto'

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => { if (!disabled) setOpen(!open) }}
        disabled={disabled}
        className="px-2 h-6 flex items-center gap-1.5 rounded-lg text-xs font-medium transition-colors max-w-[130px] hover:bg-hover-bg"
        style={{ color: selected ? 'var(--text-secondary)' : 'var(--text-muted)' }}
        title={selected ? `${selected.provider} / ${selected.model}` : t('modelSelector.autoSelectTitle')}
      >
        <Icon icon="fa-solid fa-microchip text-2xs flex-shrink-0" />
        <span className="truncate">{shortLabel}</span>
        <Icon icon="fa-solid fa-chevron-down text-2xs flex-shrink-0" />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 z-50 bg-surface border border-border rounded-lg py-1 shadow-lg min-w-[200px] max-h-[340px] overflow-y-auto">
          {/* Auto option */}
          <button
            onClick={() => { onSelect(null); setOpen(false) }}
            className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-hover-bg ${
              !selected ? 'text-text-primary bg-hover-bg' : 'text-text-secondary'
            }`}
          >
            {!selected ? <Icon icon="fa-solid fa-check text-2xs text-brand w-2.5" /> : <span className="w-2.5" />}
            {t('modelSelector.autoSelect')}
            <span className="text-2xs ml-auto text-text-muted">default</span>
          </button>

          <div className="mx-3 my-1 border-t border-border" />

          {loading && (
            <div className="px-3 py-2 text-2xs text-text-muted">
              <Icon icon="fa-solid fa-spinner fa-spin mr-1" />{t('modelSelector.loading')}
            </div>
          )}

          {error && (
            <div className="px-3 py-2 text-2xs text-danger">
              <Icon icon="fa-solid fa-triangle-exclamation mr-1" />
              {error}
              <button
                onClick={(e) => { e.stopPropagation(); refresh() }}
                className="ml-1 underline hover:text-red-300"
              >
                {t('modelSelector.retry')}
              </button>
            </div>
          )}

          {!loading && !error && Object.entries(grouped).map(([provider, providerModels]) => (
            <div key={provider}>
              <div className="px-3 py-1 text-2xs font-semibold uppercase tracking-wider text-text-muted">
                {provider}
              </div>
              {providerModels.map((m) => {
                const isSelected = selected?.provider === m.provider && selected?.model === m.model
                return (
                  <button
                    key={`${m.provider}:${m.model}`}
                    onClick={() => { onSelect(m); setOpen(false) }}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-hover-bg ${
                      isSelected ? 'text-text-primary bg-hover-bg' : 'text-text-secondary'
                    }`}
                  >
                    {isSelected ? <Icon icon="fa-solid fa-check text-2xs text-brand w-2.5" /> : <span className="w-2.5" />}
                    <span className="truncate">{m.model}</span>
                  </button>
                )
              })}
            </div>
          ))}

          {!loading && !error && models.length === 0 && (
            <div className="px-3 py-2 text-2xs text-text-muted">
              {t('modelSelector.noModels')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
