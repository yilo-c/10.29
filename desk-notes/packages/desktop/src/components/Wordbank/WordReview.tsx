import React, { useEffect } from 'react'
import Icon from '../Common/Icon'

import SpeakButton from '../Common/SpeakButton'
import { useTranslation } from '../../i18n'
import type { Word, ReviewStats } from './types'

interface Props {
  reviewWords: Word[]
  reviewIndex: number
  reviewFlipped: boolean
  reviewStats: ReviewStats
  onFlip: () => void
  onRate: (quality: number) => void
  onExit: () => void
}

const WordReview: React.FC<Props> = ({ reviewWords, reviewIndex, reviewFlipped, reviewStats, onFlip, onRate, onExit }) => {
  const { t } = useTranslation()
  const currentWord = reviewWords[reviewIndex]
  const total = reviewWords.length
  const progress = reviewStats.remembered + reviewStats.forgotten
  const isDone = reviewIndex >= total

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (isDone) return
      if (e.repeat) return
      if (e.code === 'Space') {
        e.preventDefault()
        if (reviewFlipped) {
          // Space on flipped card = quality 4 (good)
          onRate(4)
        } else {
          onFlip()
        }
      }
      if (!reviewFlipped) return
      if (e.key === '1') { e.preventDefault(); onRate(1) }
      if (e.key === '2') { e.preventDefault(); onRate(2) }
      if (e.key === '3') { e.preventDefault(); onRate(3) }
      if (e.key === '4') { e.preventDefault(); onRate(4) }
      if (e.key === '5') { e.preventDefault(); onRate(5) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [reviewFlipped, isDone, onFlip, onRate])

  if (isDone) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4 p-4 min-h-0">
        <div className="w-full max-w-[280px] flex flex-col items-center gap-3">
          <div className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
            {t('wordReview.done')}
          </div>
          <div className="flex items-center gap-4 text-base">
            <span style={{ color: 'var(--success)' }}>
              <Icon icon="fa-solid fa-check mr-1" />{t('wordReview.rememberedFmt', { count: reviewStats.remembered })}
            </span>
            <span style={{ color: 'var(--danger)' }}>
              <Icon icon="fa-solid fa-xmark mr-1" />{t('wordReview.forgottenFmt', { count: reviewStats.forgotten })}
            </span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-white/[0.06]">
            <div className="h-full rounded-full bg-amber-400" style={{ width: '100%' }} />
          </div>
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {t('wordReview.accuracyFmt', { percent: total > 0 ? Math.round((reviewStats.remembered / total) * 100) : 0 })}
          </div>
          <button
            onClick={onExit}
            className="px-4 py-1.5 rounded-lg text-xs font-medium transition-all mt-2"
            style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}
          >
            {t('wordReview.finish')}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 p-4 min-h-0">
      <div className="flex items-center gap-2 w-full max-w-[280px]">
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {progress} / {total}
        </span>
        <div className="flex-1 h-1 rounded-full" style={{ background: 'rgba(255,255,255,0.06)' }}>
          <div className="h-full rounded-full transition-all" style={{ background: '#eab308', width: `${(progress / total) * 100}%` }} />
        </div>
        <span className="text-xs flex items-center gap-1" style={{ color: 'var(--success)' }}>
          <Icon icon="fa-solid fa-check text-[8px]" />{reviewStats.remembered}
        </span>
        <span className="text-xs flex items-center gap-1" style={{ color: 'var(--danger)' }}>
          <Icon icon="fa-solid fa-xmark text-[8px]" />{reviewStats.forgotten}
        </span>
      </div>
      <div
        onClick={onFlip}
        className="w-full max-w-[280px] min-h-[160px] flex flex-col items-center justify-center rounded-xl border cursor-pointer transition-all p-6 gap-3"
        style={{
          borderColor: reviewFlipped ? 'rgba(234,179,8,0.3)' : 'var(--panel-border)',
          background: reviewFlipped ? 'rgba(234,179,8,0.04)' : 'rgba(255,255,255,0.02)',
        }}
      >
        {!reviewFlipped ? (
          <>
            <div className="text-2xs font-medium" style={{ color: 'var(--text-dim)' }}>
              {t('wordReview.flipHint')}
            </div>
            <div className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
              {currentWord?.meaning}
            </div>
          </>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <div className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>
                {currentWord?.word}
              </div>
              <SpeakButton text={currentWord?.word ?? ''} lang="en" variant="icon-sm" />
            </div>
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {currentWord?.meaning}
            </div>
            {currentWord?.example && (
              <div className="text-2xs italic px-3 py-1.5 rounded" style={{ background: 'rgba(255,255,255,0.03)', color: 'var(--text-muted)' }}>
                {currentWord.example}
              </div>
            )}
            {currentWord?.examples && currentWord.examples.length > 0 && (
              <div className="flex flex-col gap-1 w-full px-1">
                {currentWord.examples.slice(0, 3).map((ex, i) => (
                  <div key={i} className="text-[8px] italic px-2 py-1 rounded"
                    style={{ background: 'rgba(255,255,255,0.02)', color: 'var(--text-muted)' }}>
                    • {ex}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
      <div className="flex items-center gap-0.5">
        {/* Always show hint text */}
        <span className="text-[8px] mr-1" style={{ color: 'var(--text-dim)' }}>
          {reviewFlipped ? t('wordReview.ratePrefix') : t('wordReview.spaceFlip')}
        </span>
        {reviewFlipped ? (
          <>
            <button onClick={() => onRate(1)} className="w-8 py-1.5 rounded text-xs font-medium transition-all hover:brightness-110"
              style={{ background: 'rgba(239,68,68,0.12)', color: '#f87171' }} title={t('wordReview.titleForgot')}>
              <span className="text-[7px] opacity-50">1</span> {t('wordReview.rateForgot')}
            </button>
            <button onClick={() => onRate(2)} className="w-8 py-1.5 rounded text-xs font-medium transition-all hover:brightness-110"
              style={{ background: 'rgba(249,115,22,0.12)', color: '#fb923c' }} title={t('wordReview.titleHard')}>
              <span className="text-[7px] opacity-50">2</span> {t('wordReview.rateHard')}
            </button>
            <button onClick={() => onRate(3)} className="w-8 py-1.5 rounded text-xs font-medium transition-all hover:brightness-110"
              style={{ background: 'rgba(234,179,8,0.12)', color: '#facc15' }} title={t('wordReview.titleSlow')}>
              <span className="text-[7px] opacity-50">3</span> {t('wordReview.rateSlow')}
            </button>
            <button onClick={() => onRate(4)} className="w-8 py-1.5 rounded text-xs font-medium transition-all hover:brightness-110"
              style={{ background: 'rgba(34,197,94,0.12)', color: '#4ade80' }} title={t('wordReview.titleGood')}>
              <span className="text-[7px] opacity-50">4</span> {t('wordReview.rateGood')}
            </button>
            <button onClick={() => onRate(5)} className="w-8 py-1.5 rounded text-xs font-medium transition-all hover:brightness-110"
              style={{ background: 'rgba(34,197,94,0.2)', color: 'var(--success)' }} title={t('wordReview.titleEasy')}>
              <span className="text-[7px] opacity-50">5</span> {t('wordReview.rateEasy')}
            </button>
          </>
        ) : (
          <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>{t('wordReview.tapOrSpace')}</span>
        )}
      </div>
    </div>
  )
}

export default WordReview
