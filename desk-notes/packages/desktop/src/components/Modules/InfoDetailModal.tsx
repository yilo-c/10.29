import React, { useState, useCallback, useEffect, useRef } from 'react'
import Icon from '../Common/Icon'

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CATEGORY_META, type RawSourceItem } from '@desk-notes/shared'
import { getWsClient } from '../../utils/ai'
import { SOURCE_META } from './info-constants'
import { useTranslation } from '../../i18n'

interface Props {
  item: RawSourceItem | null
  onClose: () => void
  savedUrls?: Set<string>
  onToggleBookmark?: (item: RawSourceItem) => void
}

const InfoDetailModal: React.FC<Props> = ({ item, onClose, savedUrls, onToggleBookmark }) => {
  const { t } = useTranslation()
  const [translating, setTranslating] = useState(false)
  const [translated, setTranslated] = useState<string | null>(null)
  const [showTranslation, setShowTranslation] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const savedTimerRef = useRef<ReturnType<typeof setTimeout>>()

  // Clear saved-state timeout on unmount
  useEffect(() => {
    return () => { if (savedTimerRef.current) clearTimeout(savedTimerRef.current) }
  }, [])

  if (!item) return null

  const meta = SOURCE_META[item.source] || { label: item.source, icon: 'fa-regular fa-circle', color: '#888' }
  const isSaved = !!(item.url && savedUrls?.has(item.url))
  const wsClient = getWsClient()

  const hasCJK = (text: string) => /[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/.test(text.slice(0, 200))

  const handleTranslate = useCallback(async () => {
    const textToTranslate = [item.title, item.summary].filter(Boolean).join('\n\n')
    if (!textToTranslate.trim()) return

    if (showTranslation && translated) {
      setShowTranslation(false)
      return
    }

    if (translated) {
      setShowTranslation(true)
      return
    }

    setTranslating(true)
    try {
      if (wsClient.status !== 'connected') {
        setTranslated(t('infoDetail.backendNotConnected'))
        setShowTranslation(true)
        return
      }
      const result = await wsClient.call<{ translated: string }>('translate.text', {
        text: textToTranslate,
        source: 'en',
        target: 'zh',
      })
      const text = result?.translated || ''
      setTranslated(text || t('infoDetail.translationEmpty'))
      setShowTranslation(true)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : t('infoDetail.translateFailed')
      setTranslated(`⚠️ ${msg}`)
      setShowTranslation(true)
    } finally {
      setTranslating(false)
    }
  }, [item, translated, showTranslation, wsClient])

  const handleSaveToKnowledgeBase = useCallback(async () => {
    if (saved || saving) return
    setSaving(true)
    try {
      const sourceLabel = SOURCE_META[item.source]?.label || item.source
      const content = [
        `> ${t('infoDetail.sourceLabel')}: ${sourceLabel} ｜ [${t('infoDetail.viewOriginal')}](${item.url})`,
        '',
        item.summary || '',
        item.tags?.length ? `\n---\n${t('infoDetail.tagPrefix')}: ${item.tags.map(tag => `#${tag}`).join(' ')}` : '',
      ].filter(Boolean).join('\n')
      await wsClient.call('notes.create', {
        title: item.title_zh || item.title,
        content,
        tags: [item.source, t('infoDetail.knowledgeBase'), ...(item.tags || [])],
      })
      setSaved(true)
      savedTimerRef.current = setTimeout(() => setSaved(false), 2000)
    } catch {
      return // don't clear saved state on failure; finally handles setSaving(false)
    } finally {
      setSaving(false)
    }
  }, [item, saved, saving, wsClient])

  return (
    <div
      className="fixed inset-0 z-[99999] flex items-center justify-center p-6"
      style={{ background: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg max-h-[80vh] flex flex-col rounded-xl border shadow-2xl"
        style={{
          background: 'var(--panel-bg)',
          borderColor: 'var(--panel-border)',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* ── Header bar: back + source + close ── */}
        <div className="flex items-center justify-between px-4 pt-3 pb-1">
          <div className="flex items-center gap-2 min-w-0">
            <button
              onClick={onClose}
              className="flex items-center gap-1 px-2 py-1 rounded-md text-xs hover:bg-white/[0.08] transition-all shrink-0"
              style={{ color: 'var(--text-muted)' }}
            >
              <Icon icon="fa-solid fa-arrow-left text-xs" />
              <span>{t('infoDetail.back')}</span>
            </button>
            <div className="w-px h-4 bg-white/[0.08] shrink-0" />
            <div
              className="w-5 h-5 rounded flex items-center justify-center shrink-0"
              style={{ background: `${meta.color}18`, color: meta.color, fontSize: 9 }}
            >
              <Icon icon={meta.icon} />
            </div>
            <span className="text-xs font-medium truncate" style={{ color: meta.color }}>
              {meta.label}
            </span>
          </div>
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded text-xs hover:bg-white/[0.08] transition-all shrink-0"
            style={{ color: 'var(--text-muted)' }}
            title={t('infoDetail.close')}
          >
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        {/* ── Scrollable body ── */}
        <div className="flex-1 overflow-y-auto px-4 py-2">
          {/* Category */}
          {item.category && (
            <div className="mb-2">
              <span
                className="text-2xs px-1.5 py-0.5 rounded"
                style={{
                  background: `${(CATEGORY_META[item.category] || CATEGORY_META['general']).color}18`,
                  color: (CATEGORY_META[item.category] || CATEGORY_META['general']).color,
                }}
              >
                {(CATEGORY_META[item.category] || CATEGORY_META['general']).label}
              </span>
            </div>
          )}

          {/* Title */}
          <h2 className="text-sm font-semibold leading-snug mb-1" style={{ color: 'var(--text-primary)' }}>
            {item.title_zh || item.title}
          </h2>
          {item.title_zh && item.title !== item.title_zh && (
            <h3 className="text-xs leading-snug mb-3" style={{ color: 'var(--text-muted)' }}>
              {item.title}
            </h3>
          )}
          {!item.title_zh && <div className="mb-3" />}

          {/* Meta row */}
          <div className="flex items-center gap-3 mb-4 text-xs" style={{ color: 'var(--text-muted)' }}>
            {item.score > 0 && (
              <span><Icon icon="fa-regular fa-arrow-up mr-1" />{item.score}</span>
            )}
            {item.publishedAt > 0 && (
              <span>
                {new Date(item.publishedAt).toLocaleString(undefined, {
                  year: 'numeric', month: '2-digit', day: '2-digit',
                  hour: '2-digit', minute: '2-digit',
                })}
              </span>
            )}
            {item.tags.length > 0 && (
              <span className="truncate max-w-[160px]">
                {item.tags.map(t => `#${t}`).join(' ')}
              </span>
            )}
          </div>

          {/* Chinese summary (from AI brief) */}
          {item.summary_zh && (
            <div className="mb-3 text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {item.summary_zh}
              </ReactMarkdown>
            </div>
          )}

          {/* English summary */}
          {item.summary && (
            <div className="mb-4 text-xs leading-relaxed" style={{ color: item.summary_zh ? 'var(--text-muted)' : 'var(--text-secondary)' }}>
              {item.summary_zh && (
                <div className="text-2xs font-medium mb-1" style={{ color: 'var(--text-dim)' }}>{t('infoDetail.originalSummary')}</div>
              )}
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {item.summary}
              </ReactMarkdown>
            </div>
          )}

          {/* ── AI Translation panel ── */}
          {!hasCJK(item.title + (item.summary || '')) && (
            <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--panel-border)' }}>
              <button
                onClick={handleTranslate}
                disabled={translating}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
                style={{ background: showTranslation ? 'rgba(34,197,94,0.1)' : 'rgba(96,165,250,0.1)', color: showTranslation ? 'var(--success)' : 'var(--brand)' }}
              >
                {translating ? (
                  <><div className="w-3 h-3 border-2 border-white/30 border-t-white/80 rounded-full animate-spin" />{t('infoDetail.translating')}</>
                ) : (
                  <><Icon icon="fa-solid fa-language text-xs" />{showTranslation ? t('infoDetail.collapseTranslation') : t('infoDetail.translateAll')}</>
                )}
              </button>

              {showTranslation && translated && (
                <div className="mt-2 p-3 rounded-lg text-xs leading-relaxed" style={{ background: 'rgba(34,197,94,0.05)', border: '1px solid rgba(34,197,94,0.15)', color: 'var(--text-secondary)' }}>
                  <div className="text-2xs font-medium mb-1" style={{ color: 'var(--success)' }}>{t('infoDetail.aiChineseTranslation')}</div>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {translated}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Footer actions ── */}
        <div className="flex items-center gap-2 px-4 py-3 border-t shrink-0" style={{ borderColor: 'var(--panel-border)' }}>
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
            style={{ background: 'rgba(255,255,255,0.07)', color: 'var(--text-secondary)' }}
          >
            <Icon icon="fa-solid fa-arrow-up-right-from-square text-xs" />
            {t('infoDetail.viewOriginal')}
          </a>
          {onToggleBookmark && (
            <button
              onClick={() => onToggleBookmark(item)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{
                background: isSaved ? 'rgba(234,179,8,0.15)' : 'rgba(234,179,8,0.08)',
                color: isSaved ? '#eab308' : 'var(--text-muted)',
              }}
            >
              <Icon icon={`${isSaved ? 'fa-solid' : 'fa-regular'} fa-bookmark text-xs`} />
              {isSaved ? t('infoDetail.bookmarked') : t('infoDetail.bookmarkLater')}
            </button>
          )}
          <button
            onClick={handleSaveToKnowledgeBase}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
            style={{
              background: saved ? 'rgba(34,197,94,0.15)' : 'rgba(250,204,21,0.1)',
              color: saved ? 'var(--success)' : '#eab308',
            }}
          >
            {saving ? (
              <><div className="w-3 h-3 border-2 border-white/30 border-t-white/80 rounded-full animate-spin" />{t('infoDetail.saving')}</>
            ) : saved ? (
              <><Icon icon="fa-solid fa-check text-xs" />{t('infoDetail.savedToKb')}</>
            ) : (
              <><Icon icon="fa-regular fa-bookmark text-xs" />{t('infoDetail.saveToKnowledgeBase')}</>
            )}
          </button>
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg text-xs transition-all"
            style={{ color: 'var(--text-muted)' }}
          >
            {t('infoDetail.close')}
          </button>
        </div>
      </div>
    </div>
  )
}

export default InfoDetailModal
