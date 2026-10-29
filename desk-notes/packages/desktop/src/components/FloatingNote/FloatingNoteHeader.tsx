import React, { useState, useRef, useEffect } from 'react'
import Icon from '../Common/Icon'

import { FloatingNote as FN } from '../../types'
import { useStore } from '../../store/useStore'
import { COLOR_PRESETS, formatRelativeTime, sanitizeHtml } from '../../utils/helpers'
import { addAutoSnapshot } from '../../utils/noteHistory'
import { getAllTemplates } from '../../utils/templates'
import { useTranslation } from '../../i18n'
import { onContentEdit } from '../../store/contentEditTracker'
import { hashColor } from './FloatingNote.utils'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

interface FloatingNoteHeaderProps {
  note: FN
  standalone?: boolean
  fullscreen: boolean
  noteOpacity: number
  handleMouseDown: (e: React.MouseEvent) => void
  handleFullscreenToggle: () => void
  handleTitleSave: (title: string) => void
  onRemove: () => void
  editorRef: React.RefObject<HTMLDivElement>
  timeoutIdsRef: React.MutableRefObject<ReturnType<typeof setTimeout>[]>
}

const FloatingNoteHeader: React.FC<FloatingNoteHeaderProps> = ({
  note, standalone, fullscreen, noteOpacity,
  handleMouseDown, handleFullscreenToggle, handleTitleSave,
  onRemove, editorRef, timeoutIdsRef,
}) => {
  const updateFloatingNote = useStore(s => s.updateFloatingNote)
  const theme = useStore(s => s.theme)
  const toggleTheme = useStore(s => s.toggleTheme)
  const backgroundMode = useStore(s => s.backgroundMode)
  const setBackgroundMode = useStore(s => s.setBackgroundMode)
  const locale = useStore(s => s.locale)
  const suggestedTags = useStore(s => s.suggestedTags[note.id])
  const acceptSuggestedTags = useStore(s => s.acceptSuggestedTags)
  const setSuggestedTags = useStore(s => s.setSuggestedTags)
  const { t } = useTranslation()

  const [showMore, setShowMore] = useState(false)
  const [showTagEditor, setShowTagEditor] = useState(false)
  const [tagInput, setTagInput] = useState('')
  const [moreUp, setMoreUp] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [showNoteBg, setShowNoteBg] = useState(false)
  const [showInsertTemplate, setShowInsertTemplate] = useState(false)
  const [refLinkCopied, setRefLinkCopied] = useState(false)

  const handleAddTag = () => {
    const v = tagInput.trim()
    if (!v) return
    const existing = note.tags || []
    if (existing.includes(v)) return
    updateFloatingNote(note.id, { tags: [...existing, v] })
    setTagInput('')
  }

  const handleRemoveTag = (tag: string) => {
    const existing = note.tags || []
    updateFloatingNote(note.id, { tags: existing.filter(t => t !== tag) })
  }

  const nd = isElectron && standalone ? ({ WebkitAppRegion: 'no-drag' } as unknown as React.CSSProperties) : undefined
  const isArchived = note.archived

  const timeInfo = React.useMemo(() => note.createdAt
    ? { display: formatRelativeTime(note.createdAt, locale), full: new Date(note.createdAt).toLocaleString(locale === 'en' ? 'en-US' : 'zh-CN') }
    : null
  , [note.createdAt, locale])
  const updateTimeInfo = React.useMemo(() => note.updatedAt && note.updatedAt !== note.createdAt
    ? { display: formatRelativeTime(note.updatedAt, locale), full: new Date(note.updatedAt).toLocaleString(locale === 'en' ? 'en-US' : 'zh-CN') }
    : null
  , [note.updatedAt, note.createdAt, locale])

  // Close more menu on outside click
  useEffect(() => {
    if (!showMore) return
    const handler = (e: MouseEvent) => {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setShowMore(false)
        setShowTagEditor(false)
        setTagInput('')
      }
    }
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [showMore])

  // Reset menu direction when closed
  useEffect(() => { if (!showMore) setMoreUp(false) }, [showMore])

  return (
    <>
      {/* Title bar */}
      <div
        className="flex items-center justify-between px-3 py-2.5 border-b border-white/[0.04] flex-shrink-0"
        style={standalone
          ? ({ WebkitAppRegion: (note.locked ? 'no-drag' : 'drag') } as unknown as React.CSSProperties)
          : { cursor: note.locked ? 'default' : 'grab' }
        }
        onMouseDown={handleMouseDown}
      >
        {/* Left section: collapse + functional buttons */}
        <div className="flex items-center gap-0.5 flex-shrink-0" style={nd}>
          {/* Collapse */}
          <button onClick={() => { if (!note.locked) updateFloatingNote(note.id, { collapsed: !note.collapsed }) }}
            className="w-4 h-4 flex items-center justify-center text-white/55 hover:text-text-secondary transition-all text-[8px] flex-shrink-0">
            <Icon icon={`fa-solid ${note.collapsed ? 'fa-chevron-right' : 'fa-chevron-down'}`} />
          </button>
          {/* Archive */}
          <button onClick={() => { if (!note.locked) updateFloatingNote(note.id, { archived: !note.archived }) }}
            className={`w-5 h-5 flex items-center justify-center rounded transition-all text-2xs ${isArchived ? 'text-amber-400/60' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.06]'}`}
            title={isArchived ? t('note.unarchive') : t('note.archive')}>
            <Icon icon="fa-solid fa-box-archive" />
          </button>
          {/* Pop out (Electron browser mode) */}
          {!standalone && isElectron && (
            <button onClick={() => {
              if (note.locked) return
              updateFloatingNote(note.id, { floated: true })
              ei!.createFloatingWindow({
                id: note.id, screenX: note.x, screenY: note.y,
                width: Math.max(360, note.width), height: Math.max(300, note.height),
                noteData: note,
              })
            }}
              className="w-5 h-5 flex items-center justify-center rounded transition-all text-2xs text-white/55 hover:text-text-secondary hover:bg-white/[0.06]"
              title={t('note.popOut')}>
              <Icon icon="fa-solid fa-up-right-and-down-left-from-center" />
            </button>
          )}
          {/* More menu (···) */}
          <div className="relative" ref={moreRef}>
            <button onClick={() => {
              if (!showMore) setMoreUp(false)
              setShowMore(!showMore)
            }}
              className={`w-5 h-5 flex items-center justify-center rounded transition-all text-2xs ${showMore ? 'text-fluent-blue/70' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.06]'}`}
              title={t('note.more')}>
              <Icon icon="fa-solid fa-ellipsis-vertical" />
            </button>
            {showMore && (
              <div className={`absolute z-50 border rounded-xl py-1 shadow-xl min-w-[160px] ${moreUp ? 'bottom-full left-0 mb-1' : 'top-full left-0 mt-1'}`}
                style={{ background: 'var(--tooltip-bg)', borderColor: 'var(--panel-border-accent)' }}>
                <button onClick={() => {
                  navigator.clipboard.writeText(`便签://${note.id}`)
                  setRefLinkCopied(true)
                  const t2 = setTimeout(() => setRefLinkCopied(false), 2000)
                  timeoutIdsRef.current.push(t2)
                  setShowMore(false)
                }}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors"
                  style={{ color: 'var(--text-secondary)' }}>
                  <Icon icon={`fa-solid ${refLinkCopied ? 'fa-check' : 'fa-link'} w-3 text-center text-2xs`} />
                  {refLinkCopied ? t('note.copied') : t('note.copyRefLink')}
                </button>
                <button onClick={() => setShowTagEditor(!showTagEditor)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors"
                  style={{ color: 'var(--text-secondary)' }}>
                  <Icon icon="fa-solid fa-tags w-3 text-center text-2xs" />
                  {t('note.manageTags')}
                </button>
                {showTagEditor && (
                  <div className="px-3 py-2 border-t border-white/[0.06]">
                    <div className="flex flex-wrap gap-1 mb-1.5">
                      {(note.tags || []).map(tag => (
                        <span key={tag}
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-2xs"
                          style={{ background: `${hashColor(tag)}20`, color: hashColor(tag) }}>
                          {tag}
                          <button onClick={() => handleRemoveTag(tag)} className="hover:opacity-70"><Icon icon="fa-solid fa-xmark" /></button>
                        </span>
                      ))}
                      {(note.tags || []).length === 0 && <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>{t('note.noTags')}</span>}
                    </div>
                    <div className="flex items-center gap-1">
                      <input value={tagInput} onChange={e => setTagInput(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handleAddTag() }}
                        placeholder={t('note.addTagPlaceholder')}
                        className="flex-1 text-xs px-1.5 py-1 rounded outline-none border"
                        style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }} />
                      <button onClick={handleAddTag} className="text-2xs px-1.5 py-1 rounded transition-colors"
                        style={{ color: 'var(--text-secondary)' }}><Icon icon="fa-solid fa-plus" /></button>
                    </div>
                  </div>
                )}
                <button onClick={() => {
                  addAutoSnapshot(note.id, note.title, note.content || '', note.todos)
                  setShowMore(false)
                }}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors"
                  style={{ color: 'var(--text-secondary)' }}>
                  <Icon icon="fa-solid fa-floppy-disk w-3 text-center text-2xs" />
                  {t('noteHistory.saveVersion')}
                </button>
                <button onClick={() => { setShowInsertTemplate(!showInsertTemplate) }}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors"
                  style={{ color: 'var(--text-secondary)' }}>
                  <Icon icon="fa-regular fa-copy w-3 text-center text-2xs" />
                  {t('toolbar.insertTemplate')}
                </button>
                {showInsertTemplate && (
                  <div className="px-3 py-2 border-t border-white/[0.06] max-h-[160px] overflow-y-auto">
                    {getAllTemplates().map(tmpl => (
                      <button key={tmpl.id}
                        onClick={() => {
                          const editor = editorRef.current
                          if (!editor || note.locked || isArchived) return
                          const sel = window.getSelection()
                          if (sel && sel.rangeCount && editor.contains(sel.anchorNode)) {
                            document.execCommand('insertHTML', false, sanitizeHtml(tmpl.content))
                          } else {
                            editor.insertAdjacentHTML('beforeend', sanitizeHtml(tmpl.content))
                          }
                          onContentEdit(note.id)
                          updateFloatingNote(note.id, { content: editor.innerHTML })
                          setShowInsertTemplate(false)
                          setShowMore(false)
                        }}
                        className="w-full flex items-center gap-2 px-2 py-1 text-2xs rounded hover:bg-white/[0.06] transition-colors text-left"
                        style={{ color: 'var(--text-secondary)' }}>
                        <Icon icon={`fa-solid ${tmpl.icon} w-3 text-center`} />
                        {tmpl.label}
                      </button>
                    ))}
                  </div>
                )}
                <button onClick={handleFullscreenToggle}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors"
                  style={{ color: 'var(--text-secondary)' }}>
                  <Icon icon={`fa-solid ${fullscreen ? 'fa-compress' : 'fa-expand'} w-3 text-center text-2xs`} />
                  {fullscreen ? t('note.exitFullscreen') : t('note.fullscreen')}
                </button>
              </div>
            )}
          </div>
        </div>
        {/* Middle section: title */}
        <div className="flex-1 ml-1 min-w-0 flex items-center gap-2">
          {editingTitle ? (
            <input autoFocus defaultValue={note.title}
              onBlur={e => { handleTitleSave(e.target.value); setEditingTitle(false) }}
              onKeyDown={e => { if (e.key === 'Enter') { handleTitleSave((e.target as HTMLInputElement).value); setEditingTitle(false) } }}
              className="bg-white/[0.06] text-xs text-text-primary px-2 py-0.5 rounded flex-1 outline-none border border-white/[0.06]"
              style={nd} />
          ) : (
            <span className="text-xs font-light truncate flex items-center gap-1.5" onDoubleClick={() => setEditingTitle(true)} style={{ color: 'var(--text-primary)', ...nd }}
              title={note.title}>
              {isArchived && <Icon icon="fa-solid fa-box-archive text-2xs text-text-muted" />}
              {note.title}
              {(note.refCount ?? 0) > 0 && (
                <span className="inline-flex items-center gap-0.5 text-2xs text-fluent-blue/60 bg-fluent-blue/[0.08] px-1 py-0.5 rounded" title={t('note.refCount')}>
                  <Icon icon="fa-solid fa-link" />{note.refCount}
                </span>
              )}
            </span>
          )}
          {note.tags && note.tags.length > 0 && !editingTitle && (
            <div className="flex items-center gap-1 flex-shrink-0 max-w-[80px] overflow-hidden">
              {note.tags.slice(0, 5).map(tag => (
                <span key={tag} className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: hashColor(tag) }} title={tag} />
              ))}
              {note.tags.length > 5 && (
                <span className="text-[8px] text-text-muted shrink-0">+{note.tags.length - 5}</span>
              )}
            </div>
          )}
          {/* AI suggested tags — user confirmation */}
          {suggestedTags && suggestedTags.length > 0 && (
            <div className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-fluent-blue/[0.1] border border-fluent-blue/20 ml-1">
              <span className="text-[7px] text-fluent-blue/70 whitespace-nowrap leading-none">
                AI: {suggestedTags.join(', ')}
              </span>
              <button
                onClick={() => acceptSuggestedTags(note.id)}
                className="text-[8px] text-fluent-blue/60 hover:text-fluent-blue/90 leading-none px-0.5"
                title={t('common.confirm')}
              >
                <Icon icon="fa-solid fa-check" />
              </button>
              <button
                onClick={() => setSuggestedTags(note.id, [])}
                className="text-[8px] text-text-muted hover:text-text-secondary leading-none px-0.5"
                title={t('common.cancel')}
              >
                <Icon icon="fa-solid fa-xmark" />
              </button>
            </div>
          )}
          {/* Time info */}
          {timeInfo && !editingTitle && !note.collapsed && (
            <span className="text-2xs text-text-dim whitespace-nowrap flex-shrink-0 max-w-[65px] truncate"
              title={updateTimeInfo
                ? `${t('note.createdAt')}: ${timeInfo.full}\n${t('note.modifiedAt')}: ${updateTimeInfo.full}`
                : `${t('note.createdAt')}: ${timeInfo.full}`}>
              {timeInfo.display}{updateTimeInfo && ` · ${updateTimeInfo.display}`}
            </span>
          )}
        </div>
        {/* Right section: pin + lock + appearance + window controls */}
        <div className="flex items-center gap-0.5 flex-shrink-0" style={nd}>
          {/* Pin */}
          <button onClick={() => { const np = !note.pinned; updateFloatingNote(note.id, { pinned: np, zIndex: np ? 999999 : note.zIndex }); if (standalone && isElectron) ei!.alwaysOnTop(np) }}
            className={`w-5 h-5 flex items-center justify-center rounded transition-all text-2xs ${note.pinned ? 'text-fluent-blue/60' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.06]'}`}
            title={note.pinned ? t('note.unpin') : t('note.pin')}>
            <Icon icon="fa-solid fa-thumbtack" />
          </button>
          {/* Lock */}
          <button onClick={() => updateFloatingNote(note.id, { locked: !note.locked })}
            className={`w-5 h-5 flex items-center justify-center rounded transition-all text-2xs ${note.locked ? 'text-amber-400/60' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.06]'}`}
            title={note.locked ? t('note.unlock') : t('note.lock')}>
            <Icon icon="fa-solid fa-lock" />
          </button>
          {/* Background settings */}
          <div className="relative">
            <button onClick={() => { if (!note.locked) setShowNoteBg(!showNoteBg) }}
              className="w-5 h-5 flex items-center justify-center rounded hover:text-text-secondary hover:bg-white/[0.06] transition-all text-2xs"
              style={{ color: 'var(--text-secondary)' }}
              title={t('note.appearance')}>
              <Icon icon="fa-solid fa-circle-half-stroke" />
            </button>
            {showNoteBg && (
              <div className="absolute top-full right-0 mt-1 z-50 border border-white/[0.08] rounded-xl px-3 py-2 shadow-xl flex flex-col gap-2 min-w-[180px]"
                onMouseDown={e => e.stopPropagation()}
                style={{ background: 'var(--panel-bg-solid)' }}>
                {/* Theme */}
                <div>
                  <div className="text-xs text-text-secondary mb-1.5">{t('note.theme.title')}</div>
                  <div className="flex items-center gap-1">
                    <button onClick={() => { if (theme !== 'dark') toggleTheme() }}
                      className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${theme === 'dark' ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'}`}>
                      {t('note.theme.dark')}
                    </button>
                    <button onClick={() => { if (theme !== 'light') toggleTheme() }}
                      className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${theme === 'light' ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'}`}>
                      {t('note.theme.light')}
                    </button>
                  </div>
                </div>
                <div className="border-t border-white/[0.06]" />
                {/* Background mode */}
                <div>
                  <div className="text-xs text-text-secondary mb-1.5">{t('note.background.title')}</div>
                  <div className="flex items-center gap-1">
                    {(['acrylic', 'pure-white', 'pure-black'] as const).map(m => (
                      <button key={m} onClick={() => setBackgroundMode(m)}
                        className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${backgroundMode === m ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'}`}>
                        {m === 'acrylic' ? t('note.background.acrylic') : m === 'pure-white' ? t('note.background.pureWhite') : t('note.background.pureBlack')}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="border-t border-white/[0.06]" />
                {/* Opacity */}
                <div className="flex items-center gap-2">
                  <span className="text-xs text-text-secondary w-7">{t('note.opacity')}</span>
                  <input type="range" min="0.15" max="1" step="0.05"
                    value={noteOpacity}
                    onChange={e => updateFloatingNote(note.id, { opacity: parseFloat(e.target.value) })}
                    className="flex-1 h-1 accent-fluent-blue cursor-pointer" />
                  <span className="text-xs text-text-secondary w-5 text-right">{Math.round(noteOpacity * 100)}%</span>
                </div>
                <div className="border-t border-white/[0.06] pt-2">
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <span className="text-xs text-text-secondary">{t('note.color')}</span>
                  </div>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {COLOR_PRESETS.map(p => (
                      <button key={p.label}
                        onClick={() => updateFloatingNote(note.id, { color: p.color || undefined })}
                        className="w-4 h-4 rounded-full border border-white/[0.15] flex items-center justify-center transition-transform hover:scale-125"
                        style={{ background: p.color || 'transparent' }}
                        title={p.label}>
                        {!p.color ? <span className="w-2 h-0.5 bg-white/30 rounded-full" /> : null}
                        {(note.color || '') === p.color && <Icon icon="fa-solid fa-check text-[6px] text-text-primary" />}
                      </button>
                    ))}
                    <label className="w-4 h-4 rounded-full border border-dashed border-white/[0.25] flex items-center justify-center cursor-pointer hover:border-white/50 transition-colors"
                      title={t('note.customColor')}>
                      <Icon icon="fa-solid fa-plus text-[7px] text-text-secondary" />
                      <input type="color" value={note.color || '#3b82f6'}
                        onChange={e => updateFloatingNote(note.id, { color: e.target.value })}
                        className="absolute opacity-0 w-0 h-0" />
                    </label>
                  </div>
                </div>
              </div>
            )}
          </div>
          {standalone && isElectron && (
            <>
              <button onClick={() => { if (!note.locked) ei!.minimize() }}
                className="w-5 h-5 flex items-center justify-center rounded hover:text-text-secondary hover:bg-white/[0.06] transition-all text-2xs"
                style={{ color: 'var(--text-secondary)' }}>
                <Icon icon="fa-regular fa-window-minimize" />
              </button>
              <button onClick={() => { if (!note.locked) ei!.close() }}
                className="w-5 h-5 flex items-center justify-center rounded hover:text-red-400/60 hover:bg-white/[0.06] transition-all text-2xs"
                style={{ color: 'var(--text-secondary)' }}>
                <Icon icon="fa-solid fa-xmark" />
              </button>
            </>
          )}
          {!standalone && (
            <button onClick={onRemove}
              className="w-5 h-5 flex items-center justify-center rounded text-white/55 hover:text-red-400/60 hover:bg-white/[0.06] text-2xs transition-all">
              <Icon icon="fa-solid fa-xmark" />
            </button>
          )}
        </div>
      </div>

      {/* Archived banner */}
      {isArchived && !note.collapsed && (
        <div className="flex items-center justify-center py-1 border-b border-white/[0.04]" style={{ background: 'rgba(251,191,36,0.06)' }}>
          <span className="text-2xs text-amber-400/60 flex items-center gap-1">
            <Icon icon="fa-solid fa-box-archive" /> {t('note.archivedBanner')}
          </span>
        </div>
      )}
    </>
  )
}

export default React.memo(FloatingNoteHeader)
