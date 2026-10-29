import React, { useState, useMemo } from 'react'
import Icon from '../Common/Icon'

import { QIMEN_DOMAINS, QimenDomain, type QimenEvent } from '../../types'
import { getFengshuiAdvice } from '../../utils/astrology/fengshui'
import { useCompass } from '../../hooks/useCompass'
import CompassWidget from './CompassWidget'
import {
  generateAnalysis,
  assessmentBg,
  assessmentColor,
  SCENARIO_LABELS,
  type QimenAnalysis,
} from './QimenData'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

interface QimenPanelProps {
  initialEvent?: QimenEvent | null
  t: (key: string, params?: Record<string, string | number>) => string
  onCreateTodo?: (event: QimenEvent, analysis: QimenAnalysis) => void
}

type PanelMode = 'select' | 'result'

const QimenPanel: React.FC<QimenPanelProps> = ({ initialEvent, t, onCreateTodo }) => {
  const [mode, setMode] = useState<PanelMode>('select')
  const [domain, setDomain] = useState<QimenDomain | null>(initialEvent?.domain ?? null)
  const [scenario, setScenario] = useState<string | null>(initialEvent?.scenario ?? null)
  const [description, setDescription] = useState(initialEvent?.description ?? '')
  const [result, setResult] = useState<QimenAnalysis | null>(null)
  const [loading, setLoading] = useState(false)
  const [showFengshui, setShowFengshui] = useState(true)
  const compass = useCompass()

  // Lightweight fengshui hint (depends on qimen result; null until ready)
  const fengshuiAdvice = useMemo(() => {
    if (!result) return null
    return getFengshuiAdvice(domain || 'other', result.luckyDirection)
  }, [result, domain])

  const domainScenarios = useMemo(() => {
    if (!domain) return []
    return QIMEN_DOMAINS[domain]?.scenarios ?? []
  }, [domain])

  const handleAnalyze = async () => {
    if (!domain) return
    setLoading(true)
    await new Promise(r => setTimeout(r, 800))
    const analysis = generateAnalysis(domain, scenario, description)
    setResult(analysis)
    setMode('result')
    setLoading(false)
  }

  const handleReset = () => {
    setMode('select')
    setResult(null)
  }

  // From existing todo with full scenario -> auto-analyze (rebuild via key)
  React.useEffect(() => {
    if (initialEvent?.scenario) {
      handleAnalyze()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* select mode */
  if (mode === 'select') {
    return (
      <div className="rounded-lg px-3 py-2 space-y-2"
        style={{
          background: 'rgba(139,92,246,0.06)',
          border: '1px solid rgba(139,92,246,0.15)',
        }}
      >
        <div className="flex items-center gap-1">
          <Icon icon="fa-solid fa-compass text-xs" style={{ color: '#c4b5fd' }} />
          <span className="text-2xs font-medium" style={{ color: 'var(--text-secondary)' }}>
            {t('guidance.qimen')}
          </span>
          <span className="text-[8px]" style={{ color: 'var(--text-dim)' }}>
            · {t('guidance.qimenEntryTitle')}
          </span>
        </div>

        <div>
          <div className="text-[8px] mb-1" style={{ color: 'var(--text-muted)' }}>
            {t('guidance.qimenSelectDomain')}
          </div>
          <div className="grid grid-cols-4 gap-1">
            {(Object.entries(QIMEN_DOMAINS) as [QimenDomain, typeof QIMEN_DOMAINS[QimenDomain]][]).map(([key, def]) => (
              <button
                key={key}
                onClick={() => { setDomain(key); setScenario(null) }}
                className="flex flex-col items-center gap-0.5 px-1.5 py-1.5 rounded-lg transition-all text-2xs"
                style={{
                  background: domain === key ? '#8b5cf6' : 'var(--hover-bg)',
                  color: domain === key ? '#fff' : 'var(--text-muted)',
                  border: domain === key ? 'none' : '1px solid var(--panel-border)',
                }}
              >
                <Icon icon={`fa-solid ${def.icon} text-xs`} />
                <span>{t(`guidance.domain${key.charAt(0).toUpperCase() + key.slice(1)}` as string)}</span>
              </button>
            ))}
          </div>
        </div>

        {domain && domainScenarios.length > 0 && (
          <div>
            <div className="text-[8px] mb-1" style={{ color: 'var(--text-muted)' }}>
              {t('guidance.qimenSelectScenario')}
            </div>
            <div className="flex flex-wrap gap-1">
              {domainScenarios.map(s => (
                <button
                  key={s}
                  onClick={() => setScenario(s)}
                  className="px-1.5 py-0.5 rounded text-[8px] transition-all"
                  style={{
                    background: scenario === s ? 'rgba(139,92,246,0.25)' : 'var(--hover-bg)',
                    color: scenario === s ? '#c4b5fd' : 'var(--text-muted)',
                    border: scenario === s ? '1px solid rgba(139,92,246,0.3)' : '1px solid var(--panel-border)',
                  }}
                >
                  {t(`guidance.scenario${s.charAt(0).toUpperCase() + s.slice(1)}` as string)}
                </button>
              ))}
            </div>
          </div>
        )}

        <div>
          <div className="text-[8px] mb-1" style={{ color: 'var(--text-muted)' }}>
            {t('guidance.qimenDescribe')}
          </div>
          <input
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder={t('guidance.qimenPlaceholder')}
            className="w-full px-2 py-1 rounded-lg text-2xs outline-none transition-all"
            style={{
              background: 'var(--hover-bg)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--panel-border)',
            }}
          />
        </div>

        <button
          onClick={handleAnalyze}
          disabled={!domain || loading}
          className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg text-2xs transition-all disabled:opacity-40"
          style={{
            background: '#8b5cf6',
            color: '#fff',
          }}
        >
          {loading ? (
            <><Icon icon="fa-solid fa-spinner animate-spin text-xs" />{t('guidance.qimenLoading')}</>
          ) : (
            <><Icon icon="fa-solid fa-compass text-xs" />{t('guidance.qimenAnalyze')}</>
          )}
        </button>
      </div>
    )
  }

  /* result mode */
  if (!result) return null

  return (
    <div className="rounded-lg px-3 py-2 space-y-2"
      style={{
        background: 'rgba(139,92,246,0.06)',
        border: '1px solid rgba(139,92,246,0.15)',
      }}
    >
      {/* Header + rerun */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <span className="text-2xs font-medium" style={{ color: 'var(--text-secondary)' }}>
            <Icon icon="fa-solid fa-compass mr-1" />
            {t('guidance.qimen')}
          </span>
          {domain && (
            <span className="text-[8px] px-1.5 py-0.5 rounded"
              style={{ background: 'rgba(139,92,246,0.15)', color: '#c4b5fd' }}
            >
              {t(`guidance.domain${domain.charAt(0).toUpperCase() + domain.slice(1)}` as string)}
              {scenario && SCENARIO_LABELS[scenario] && ` · ${SCENARIO_LABELS[scenario]}`}
            </span>
          )}
        </div>
        <button
          onClick={handleReset}
          className="text-[8px] px-1.5 py-0.5 rounded transition-all"
          style={{ background: 'var(--hover-bg)', color: 'var(--text-muted)' }}
        >
          <Icon icon="fa-solid fa-rotate mr-0.5" />
          {t('guidance.qimenRerun')}
        </button>
      </div>

      {/* Ju summary */}
      <div className="flex items-center gap-2 text-[8px]" style={{ color: 'var(--text-muted)' }}>
        <span>{result.dunType}{result.juNumber}{t('qimen.juSuffix')}</span>
        <span>·</span>
        <span>{t('qimen.palacePrefix')}{result.palace}</span>
      </div>

      {/* Lucky direction - large */}
      <div className="rounded-lg px-2.5 py-1.5 text-center"
        style={{ background: 'rgba(139,92,246,0.1)' }}
      >
        <div className="text-[8px]" style={{ color: 'var(--text-muted)' }}>
          {t('guidance.qimenDirection')}
        </div>
        <div className="text-lg font-bold mt-0.5" style={{ color: '#c4b5fd' }}>
          <Icon icon="fa-solid fa-location-arrow mr-1" />
          {result.luckyDirection}
        </div>
      </div>

      {/* Compass (mobile only) */}
      {!isElectron && result.luckyDirection && (
        <div className="rounded-lg px-2.5 py-1.5"
          style={{ background: 'rgba(139,92,246,0.04)', border: '1px solid rgba(139,92,246,0.08)' }}
        >
          <CompassWidget
            heading={compass.heading}
            luckyDirection={result.luckyDirection}
            permission={compass.permission}
            onRequestPermission={compass.permission === 'prompt' ? compass.requestPermission : undefined}
          />
        </div>
      )}

      {/* Best times */}
      <div>
        <div className="text-[8px] mb-0.5" style={{ color: 'var(--text-muted)' }}>
          {t('guidance.qimenTime')}
        </div>
        <div className="flex gap-1 flex-wrap">
          {result.bestTimes.map((time, i) => (
            <span key={i}
              className="px-2 py-0.5 rounded text-[8px]"
              style={{ background: 'rgba(250,204,21,0.1)', color: '#fbbf24', border: '1px solid rgba(250,204,21,0.15)' }}
            >
              {time.label} {time.range}
            </span>
          ))}
        </div>
      </div>

      {/* Lucky colors */}
      <div>
        <div className="text-[8px] mb-0.5" style={{ color: 'var(--text-muted)' }}>
          {t('guidance.qimenColor')}
        </div>
        <div className="flex gap-1 flex-wrap items-center">
          {result.luckyColors.map((c, i) => (
            <span key={i} className="flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded"
              style={{ background: c + '22', color: c }}
            >
              <span className="inline-block w-3 h-3 rounded-full border border-white/10" style={{ background: c }} />
              {c}
            </span>
          ))}
        </div>
      </div>

      {/* Assessment */}
      <div className="rounded-lg px-2 py-1.5 text-2xs leading-relaxed"
        style={{
          background: assessmentBg(result.assessment),
          color: assessmentColor(result.assessment),
        }}
      >
        <span className="block text-[8px] font-medium mb-0.5" style={{ opacity: 0.7 }}>
          {t('guidance.qimenAssessment')}
        </span>
        {result.assessment}
      </div>

      {/* Strategy */}
      <div className="rounded-lg px-2 py-1.5 text-2xs leading-relaxed"
        style={{ background: 'rgba(139,92,246,0.08)', color: 'var(--text-secondary)' }}
      >
        <span className="block text-[8px] font-medium mb-0.5" style={{ color: 'var(--text-muted)' }}>
          {t('guidance.qimenStrategy')}
        </span>
        {result.strategy}
      </div>

      {/* Fengshui reference (lightweight) */}
      {fengshuiAdvice && (
        <div className="rounded-lg px-2 py-1.5 space-y-1"
          style={{ background: 'rgba(34,197,94,0.04)', border: '1px solid rgba(34,197,94,0.1)' }}
        >
          <button
            onClick={() => setShowFengshui(v => !v)}
            className="flex items-center gap-1 w-full text-left"
          >
            <Icon icon={`fa-solid fa-chevron-${showFengshui ? 'down' : 'right'} text-[8px] transition-transform`}
              style={{ color: 'var(--text-muted)' }} />
            <Icon icon="fa-solid fa-house-chimney text-2xs" style={{ color: '#4ade80' }} />
            <span className="text-[8px] font-medium" style={{ color: 'var(--text-secondary)' }}>
              {t('qimen.fengshuiHeader')}
            </span>
          </button>
          {showFengshui && (
            <div className="space-y-1 text-[8px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              <div className="flex gap-1.5">
                <span className="shrink-0 w-3 text-center" style={{ color: '#4ade80' }}>→</span>
                <span>{fengshuiAdvice.deskOrientation}</span>
              </div>
              <div className="flex gap-1.5">
                <span className="shrink-0 w-3 text-center" style={{ color: '#4ade80' }}>→</span>
                <span>{fengshuiAdvice.seatPlacement}</span>
              </div>
              <div className="flex gap-1.5">
                <span className="shrink-0 w-3 text-center" style={{ color: '#4ade80' }}>→</span>
                <span>{fengshuiAdvice.enhancement}</span>
              </div>
              <div className="flex gap-1.5">
                <span className="shrink-0 w-3 text-center" style={{ color: '#f87171' }}>✕</span>
                <span>{fengshuiAdvice.avoid}</span>
              </div>
              {fengshuiAdvice.extra && (
                <div className="flex gap-1.5">
                  <span className="shrink-0 w-3 text-center" style={{ color: '#4ade80' }}>→</span>
                  <span>{fengshuiAdvice.extra}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Create todo */}
      {onCreateTodo && domain && (
        <button
          onClick={() => onCreateTodo(
            { domain, scenario: scenario ?? undefined, description: description || undefined },
            result,
          )}
          className="w-full flex items-center justify-center gap-1.5 px-2 py-1 rounded-lg text-[8px] transition-all"
          style={{ background: 'rgba(139,92,246,0.12)', color: '#c4b5fd', border: '1px dashed rgba(139,92,246,0.25)' }}
        >
          <Icon icon="fa-solid fa-plus text-2xs" />
          {t('guidance.qimenCreateTodo')}
        </button>
      )}

      {/* Footer hint */}
      <div className="text-[7px] text-center italic" style={{ color: 'var(--text-dim)' }}>
        {t('guidance.qimenNote')}
      </div>
    </div>
  )
}

export default QimenPanel