import React, { useState, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'
import { PRESETS } from './InfoSectionPresets'

/**
 * D3（前后端对齐规划）：板块配置弹窗。
 *
 * 自包含：挂载即从后端读取板块配置，所有改动即时写回。
 * 数据源：
 * - info.sections.list（default / user / effective）
 * - info.sections.create / update / delete（自定义板块 + 覆盖默认板块）
 * - info.sections.ai_suggest_keywords（AI 联想关键词）
 *
 * 支持：关键词增删、预设短语一键加、AI 联想关键词、启用开关、自定义板块创建/删除。
 */

type Section = {
  id: string
  name: string
  emoji?: string
  keywords?: string[]
  sources?: string[]
  enabled?: boolean
  is_default?: boolean
}

type SourceOption = { name: string; display: string }

interface Props {
  onClose: () => void
  onChanged?: () => void
}

const PALETTE = [
  '#a855f7', '#3b82f6', 'var(--warning)', '#06b6d4', 'var(--danger)',
  '#10b981', '#f97316', '#14b8a6', '#e11d48',
]

function colorFor(section: Section): string {
  return PALETTE[Math.abs(hashStr(section.id)) % PALETTE.length]
}

function hashStr(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) {
    h = (h << 5) - h + s.charCodeAt(i)
    h |= 0
  }
  return h
}

const InfoSectionConfig: React.FC<Props> = ({ onClose, onChanged }) => {
  const { t } = useTranslation()

  const [sections, setSections] = useState<Section[]>([])
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  // 可用信息源
  const [availableSources, setAvailableSources] = useState<SourceOption[]>([])

  // 编辑态
  const [addingKey, setAddingKey] = useState<string | null>(null)
  const [addInput, setAddInput] = useState('')
  const [suggesting, setSuggesting] = useState<string | null>(null)

  // 自定义板块创建
  const [newSectionName, setNewSectionName] = useState('')
  const [creating, setCreating] = useState(false)

  // 板块标题编辑
  const [editingTitle, setEditingTitle] = useState<string | null>(null)
  const [titleInput, setTitleInput] = useState('')

  const flash = useCallback((kind: 'ok' | 'err', text: string) => {
    setMessage({ kind, text })
    setTimeout(() => setMessage(null), 3000)
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const client = getWsClient()
      if (client.status !== 'connected') {
        setLoading(false)
        return
      }
      const [secRes, srcRes] = await Promise.all([
        client.call<{ all?: Section[] }>('info.sections.list'),
        client.call<{ sources?: SourceOption[] }>('info.sources.list'),
      ])
      setSections(secRes?.all ?? [])
      setAvailableSources(srcRes?.sources ?? [])
    } catch (e) {
      swallowError('InfoSectionConfig:load', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const updateSection = useCallback(async (id: string, patch: Partial<Section>) => {
    try {
      const client = getWsClient()
      const res = await client.call<{ success?: boolean; error?: string }>('info.sections.update', { section_id: id, ...patch })
      if (res?.success === false) {
        flash('err', res.error ?? t('infoSection.saveFailed'))
        return false
      }
      onChanged?.()
      await load()
      return true
    } catch (e) {
      flash('err', t('infoSection.saveFailed'))
      swallowError('InfoSectionConfig:update', e)
      return false
    }
  }, [flash, load, onChanged, t])

  const addKeyword = useCallback(async (section: Section, kw: string) => {
    const k = kw.trim()
    if (!k) return
    const next = [...(section.keywords || [])]
    if (!next.includes(k)) next.push(k)
    if (next.length === (section.keywords || []).length) { setAddInput(''); return }
    await updateSection(section.id, { keywords: next })
    setAddInput('')
  }, [updateSection])

  const removeKeyword = useCallback(async (section: Section, kw: string) => {
    const next = (section.keywords || []).filter(k => k !== kw)
    await updateSection(section.id, { keywords: next })
  }, [updateSection])

  const toggleSource = useCallback(async (section: Section, srcName: string) => {
    const cur = section.sources || []
    const next = cur.includes(srcName) ? cur.filter(s => s !== srcName) : [...cur, srcName]
    await updateSection(section.id, { sources: next })
  }, [updateSection])

  const toggleEnabled = useCallback(async (section: Section) => {
    await updateSection(section.id, { enabled: !(section.enabled ?? true) })
  }, [updateSection])

  const aiSuggest = useCallback(async (section: Section) => {
    if (suggesting) return
    setSuggesting(section.id)
    try {
      const client = getWsClient()
      const res = await client.call<{ success?: boolean; keywords?: string[]; error?: string }>(
        'info.sections.ai_suggest_keywords', { name: section.name, count: 12 }
      )
      if (res?.success && Array.isArray(res.keywords) && res.keywords.length) {
        const next = [...(section.keywords || [])]
        for (const k of res.keywords) {
          if (!next.includes(k)) next.push(k)
        }
        if (next.length !== (section.keywords || []).length) {
          await updateSection(section.id, { keywords: next })
          flash('ok', t('infoSection.suggestedFmt', { name: section.name }))
        } else {
          flash('ok', t('infoSection.suggestSame'))
        }
      } else {
        flash('err', res?.error ?? t('infoSection.suggestFailed'))
      }
    } catch (e) {
      flash('err', t('infoSection.suggestFailed'))
      swallowError('InfoSectionConfig:aiSuggest', e)
    } finally {
      setSuggesting(null)
    }
  }, [flash, suggesting, updateSection, t])

  const createSection = useCallback(async () => {
    const name = newSectionName.trim()
    if (!name || creating) return
    setCreating(true)
    try {
      const client = getWsClient()
      const res = await client.call<{ success?: boolean; error?: string }>('info.sections.create', { name, emoji: '📌', keywords: [] })
      if (res?.success === false) {
        flash('err', res.error ?? t('infoSection.createFailed'))
      } else {
        setNewSectionName('')
        flash('ok', t('infoSection.createdFmt', { name }))
        onChanged?.()
        await load()
      }
    } catch (e) {
      flash('err', t('infoSection.createFailed'))
      swallowError('InfoSectionConfig:create', e)
    } finally {
      setCreating(false)
    }
  }, [creating, flash, load, newSectionName, onChanged, t])

  const deleteSection = useCallback(async (section: Section) => {
    if (!window.confirm(t('infoSection.deleteConfirmFmt', { name: section.name }))) return
    try {
      const client = getWsClient()
      const res = await client.call<{ success?: boolean; error?: string }>('info.sections.delete', { section_id: section.id })
      if (res?.success === false) {
        flash('err', res.error ?? t('infoSection.deleteFailed'))
      } else {
        flash('ok', t('infoSection.deletedFmt', { name: section.name }))
        onChanged?.()
        await load()
      }
    } catch (e) {
      flash('err', t('infoSection.deleteFailed'))
      swallowError('InfoSectionConfig:delete', e)
    }
  }, [flash, load, onChanged, t])

  const startEditTitle = useCallback((section: Section) => {
    setEditingTitle(section.id)
    setTitleInput(section.name)
  }, [])

  const saveTitle = useCallback(async (section: Section, currentValue?: string) => {
    const newName = (currentValue ?? titleInput).trim()
    if (!newName || newName === section.name) {
      setEditingTitle(null)
      return
    }
    await updateSection(section.id, { name: newName })
    setEditingTitle(null)
  }, [titleInput, updateSection])

  return (
    <div
      className="fixed inset-0 z-[99999] flex items-center justify-center p-6"
      style={{ background: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg max-h-[85vh] flex flex-col rounded-xl border shadow-2xl"
        style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--panel-border)' }}>
          <span className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
            <Icon icon="fa-solid fa-sliders mr-2 text-xs" style={{ color: 'var(--text-muted)' }} />
            {t('infoSection.title')}
          </span>
          <button onClick={onClose} className="text-xs px-2 py-1 rounded hover:bg-white/[0.06]" style={{ color: 'var(--text-muted)' }}>
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
          {message && (
            <div className={`text-xs ${message.kind === 'ok' ? 'text-fluent-green' : 'text-red-400'}`}>
              {message.text}
            </div>
          )}

          {loading ? (
            <div className="py-6 text-center text-xs" style={{ color: 'var(--text-dim)' }}>{t('infoSection.loading')}</div>
          ) : (
            sections.map(section => {
              const color = colorFor(section)
              const kws = section.keywords || []
              const presets = PRESETS[section.id] || []
              const unusedPresets = presets.filter(p => !kws.includes(p))
              const enabled = section.enabled ?? true

              return (
                <div key={section.id} className={`rounded-xl border p-3 ${enabled ? '' : 'opacity-55'}`} style={{ borderColor: 'var(--panel-border)', background: 'var(--overlay-faint)' }}>
                  {/* Section header */}
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm">{section.emoji || '📎'}</span>
                    {editingTitle === section.id ? (
                      <input
                        autoFocus
                        onMouseDown={e => e.stopPropagation()}
                        value={titleInput}
                        onChange={e => setTitleInput(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') { void saveTitle(section, e.currentTarget.value) }
                          if (e.key === 'Escape') { setEditingTitle(null) }
                        }}
                        onBlur={e => { void saveTitle(section, e.currentTarget.value) }}
                        className="flex-1 px-1.5 py-0.5 rounded text-sm bg-white/[0.06] border border-white/10 outline-none"
                        style={{ color: 'var(--text-primary)' }}
                      />
                    ) : (
                      <span
                        className="text-sm font-semibold flex-1 cursor-pointer hover:text-fluent-blue transition-colors"
                        style={{ color: 'var(--text-primary)' }}
                        onDoubleClick={() => startEditTitle(section)}
                        title={t('infoSection.renameTitle')}
                      >
                        {section.name}
                      </span>
                    )}
                    <button
                      onClick={() => deleteSection(section)}
                      className="text-2xs px-1.5 py-0.5 rounded hover:bg-red-500/10 hover:text-red-400 transition-colors"
                      style={{ color: 'var(--text-muted)' }}
                      title={t('infoSection.removeSection')}
                    >
                      <Icon icon="fa-solid fa-trash-can text-2xs" />
                    </button>
                    {/* 启用开关 */}
                    <button
                      onClick={() => toggleEnabled(section)}
                      className={`w-8 h-4.5 rounded-full transition-colors relative flex items-center px-0.5 ${enabled ? 'justify-end' : 'justify-start'}`}
                      style={{ background: enabled ? `${color}55` : 'rgba(255,255,255,0.12)', height: 18 }}
                      title={enabled ? t('infoSection.disableTitle') : t('infoSection.enableTitle')}
                    >
                      <span className="w-3.5 h-3.5 rounded-full bg-white shadow" />
                    </button>
                  </div>

                  {/* Keywords */}
                  <div className="flex flex-wrap gap-1.5 mb-1.5">
                    {kws.map(kw => (
                      <span
                        key={kw}
                        className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs cursor-pointer hover:opacity-70 transition-opacity"
                        style={{ background: `${color}18`, color, border: `1px solid ${color}30` }}
                        onClick={() => removeKeyword(section, kw)}
                        title={t('infoSection.removeTitle')}
                      >
                        {kw} ×
                      </span>
                    ))}
                    {addingKey === section.id ? (
                      <input
                        autoFocus
                        onMouseDown={e => e.stopPropagation()}
                        value={addInput}
                        onChange={e => setAddInput(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') { const v = e.currentTarget.value.trim(); if (v) void addKeyword(section, v); setAddingKey(null); setAddInput('') }
                          if (e.key === 'Escape') { setAddingKey(null); setAddInput('') }
                        }}
                        onBlur={() => { setAddingKey(null); setAddInput('') }}
                        className="w-32 px-1.5 py-0.5 rounded text-xs bg-white/[0.06] border border-white/10 outline-none"
                        style={{ color: 'var(--text-primary)' }}
                        placeholder={t('infoSection.newKeywordPlaceholder')}
                      />
                    ) : (
                      <button
                        onClick={() => setAddingKey(section.id)}
                        className="text-xs px-2 py-0.5 rounded-full hover:bg-white/[0.06] transition-colors"
                        style={{ color: 'var(--text-muted)', border: '1px dashed var(--panel-border)' }}
                      >
                        {t('infoSection.addKeyword')}
                      </button>
                    )}
                    {/* AI 联想 */}
                    <button
                      onClick={() => void aiSuggest(section)}
                      disabled={suggesting === section.id}
                      className="text-xs px-2 py-0.5 rounded-full hover:bg-white/[0.06] transition-colors disabled:opacity-50"
                      style={{ color: '#f472b6', border: '1px solid #f472b640' }}
                    >
                      {suggesting === section.id ? (
                        <><span className="inline-block w-2.5 h-2.5 border border-white/30 border-t-pink-400 rounded-full animate-spin align-middle mr-1" />{t('infoSection.suggesting')}</>
                      ) : (
                        <><Icon icon="fa-solid fa-wand-magic-sparkles text-[9px] mr-1" />{t('infoSection.aiSuggest')}</>
                      )}
                    </button>
                  </div>

                  {/* 信息源（多选、可编辑） */}
                  <div className="flex flex-wrap gap-1 mb-1.5">
                    <span className="text-2xs mr-0.5 self-center" style={{ color: 'var(--text-dim)' }}>{t('infoSection.sourcesLabel')}</span>
                    {availableSources.length === 0 && (
                      <span className="text-2xs self-center" style={{ color: 'var(--text-dim)' }}>{t('infoSection.loadingSources')}</span>
                    )}
                    {availableSources.map(src => {
                      const selected = (section.sources || []).includes(src.name)
                      return (
                        <button
                          key={src.name}
                          onClick={() => void toggleSource(section, src.name)}
                          className={`px-1.5 py-0.5 rounded-full text-2xs transition-all ${
                            selected
                              ? 'text-fluent-blue'
                              : 'text-text-muted hover:text-text-secondary border border-white/10'
                          }`}
                          style={selected ? { background: 'rgba(96,165,250,0.15)', border: '1px solid rgba(96,165,250,0.35)' } : undefined}
                          title={src.display}
                        >
                          {selected ? '✓ ' : '+ '}{src.display}
                        </button>
                      )
                    })}
                  </div>

                  {/* Preset suggestions */}
                  {unusedPresets.length > 0 && (
                    <div className="flex items-center gap-1 flex-wrap">
                      <span className="text-2xs mr-1" style={{ color: 'var(--text-dim)' }}>{t('infoSection.presetLabel')}</span>
                      {unusedPresets.slice(0, 6).map(p => (
                        <button
                          key={p}
                          onClick={() => void addKeyword(section, p)}
                          className="text-2xs px-1.5 py-0.5 rounded-full hover:bg-white/[0.08] transition-colors"
                          style={{ color: 'var(--text-muted)', border: '1px solid var(--panel-border)' }}
                        >
                          + {p}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })
          )}

          {/* 自定义板块创建 */}
          <div className="rounded-xl border border-dashed p-3" style={{ borderColor: 'var(--panel-border)' }}>
            <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-primary)' }}>
              <Icon icon="fa-solid fa-plus mr-1.5 text-2xs" style={{ color: 'var(--text-muted)' }} />
              {t('infoSection.addSection')}
            </div>
            <div className="flex items-center gap-1.5">
              <input
                onMouseDown={e => e.stopPropagation()}
                value={newSectionName}
                onChange={e => setNewSectionName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void createSection() } }}
                placeholder={t('infoSection.newSectionName')}
                className="flex-1 px-2 py-1.5 rounded-lg text-xs outline-none border"
                style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
              />
              <button
                onClick={() => void createSection()}
                disabled={!newSectionName.trim() || creating}
                className="px-3 py-1.5 rounded-lg text-xs font-medium text-fluent-blue disabled:opacity-30 transition-all"
                style={{ background: 'rgba(96,165,250,0.15)' }}
              >
                {creating ? t('infoSection.creating') : t('infoSection.createSection')}
              </button>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end px-4 py-3 border-t" style={{ borderColor: 'var(--panel-border)' }}>
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg text-xs transition-all"
            style={{ background: 'rgba(255,255,255,0.06)', color: 'var(--text-muted)' }}
          >
            {t('infoSection.cancel')}
          </button>
        </div>
      </div>
    </div>
  )
}

export default InfoSectionConfig