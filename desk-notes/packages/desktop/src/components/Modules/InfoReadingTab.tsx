import React from 'react'
import Icon from '../Common/Icon'

import type { RawSourceItem } from '@desk-notes/shared'
import { useTranslation } from '../../i18n'

interface Props {
  savedArticles: RawSourceItem[]
  loading: boolean
  backendConnected: boolean
  onToggleBookmark: (item: RawSourceItem) => void
}

const InfoReadingTab: React.FC<Props> = ({ savedArticles, loading, backendConnected, onToggleBookmark }) => {
  const { t } = useTranslation()
  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 gap-2">
        <div className="w-3 h-3 border-2 border-white/20 border-t-white/70 rounded-full animate-spin" />
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('infoReading.loading')}</span>
      </div>
    )
  }

  if (savedArticles.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 gap-2 text-center">
        <div className="text-xl opacity-20"><Icon icon="fa-regular fa-bookmark" /></div>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {backendConnected ? t('infoReading.emptyBookmarks') : t('infoReading.backendDisconnected')}
        </div>
        <div className="text-xs" style={{ color: 'var(--text-dim)' }}>
          {t('infoReading.hintPrefix')}<Icon icon="fa-regular fa-bookmark" />{t('infoReading.hintSuffix')}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      {savedArticles.map(item => (
        <div
          key={item.id}
          className="flex items-start gap-3 px-3 py-2.5 rounded-xl border transition-all hover:bg-white/[0.04] group"
          style={{ borderColor: 'var(--panel-border)' }}
        >
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 cursor-pointer"
            style={{ background: 'rgba(234,179,8,0.1)', color: '#eab308', fontSize: 10 }}
          >
            <Icon icon="fa-solid fa-bookmark" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs font-medium leading-snug line-clamp-2" style={{ color: 'var(--text-primary)' }}>
              {item.title_zh || item.title}
            </div>
            {item.title_zh && item.title !== item.title_zh && (
              <div className="text-xs mt-0.5 leading-snug line-clamp-1" style={{ color: 'var(--text-muted)' }}>
                {item.title}
              </div>
            )}
            {(item as any).ai_summary && (
              <div className="text-2xs mt-1 leading-relaxed line-clamp-2" style={{ color: 'var(--text-secondary)' }}>
                {(item as any).ai_summary}
              </div>
            )}
            <div className="flex items-center gap-2 mt-1.5">
              <span className="text-2xs px-1 py-0.5 rounded" style={{ background: 'rgba(234,179,8,0.1)', color: '#eab308' }}>
                {item.source}
              </span>
              {item.url && (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity text-2xs flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-white/[0.08]"
                  style={{ color: 'var(--text-muted)' }}
                  onClick={e => e.stopPropagation()}
                  title={t('infoReading.openOriginal')}
                >
                  <Icon icon="fa-solid fa-arrow-up-right-from-square text-[8px]" />
                </a>
              )}
            </div>
          </div>
          <button
            onClick={e => { e.stopPropagation(); onToggleBookmark(item) }}
            className="shrink-0 w-6 h-6 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 transition-opacity hover:bg-white/[0.08]"
            style={{ color: 'var(--danger)' }}
            title={t('infoReading.removeBookmark')}
          >
            <Icon icon="fa-solid fa-trash text-xs" />
          </button>
        </div>
      ))}
    </div>
  )
}

export default InfoReadingTab
