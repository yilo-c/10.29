import React, { Suspense } from 'react'
import Icon from '../Common/Icon'

import type { FloatingNote as FN } from '../../types'
import { useStore } from '../../store/useStore'
import { onContentEdit } from '../../store/contentEditTracker'
import { useTranslation } from '../../i18n'
import { formatDueDate } from './FloatingNote.utils'
import FormatToolbar from './FormatToolbar'

const MdPreview = React.lazy(() => import('./MdPreview'))
const MDEditor = React.lazy(() => import('@uiw/react-md-editor'))

interface NoteContentProps {
  note: FN
  isArchived: boolean | undefined
  nd: React.CSSProperties | undefined
  viewMode: 'edit' | 'preview' | 'split'
  mdContent: string
  showFormatBar: boolean
  backlinks: FN[]
  showBacklinks: boolean
  todoText: string
  onViewModeChange: (mode: 'edit' | 'preview' | 'split') => void
  onShowFormatBarChange: (v: boolean) => void
  onMdContentChange: (v: string) => void
  onShowBacklinksChange: (v: boolean) => void
  onShowChatPanelChange: (v: boolean) => void
  onTodoTextChange: (v: string) => void
  onExtractTodos: () => void
  onAddTodo: () => void
  onToggleTodo: (id: string) => void
  onDeleteTodo: (id: string) => void
  onRenderTodoText: (text: string) => React.ReactNode
}

const FloatingNoteContent: React.FC<NoteContentProps> = ({
  note, isArchived, nd, viewMode, mdContent, showFormatBar, backlinks, showBacklinks, todoText,
  onViewModeChange, onShowFormatBarChange, onMdContentChange,
  onShowBacklinksChange, onShowChatPanelChange,
  onTodoTextChange, onExtractTodos, onAddTodo, onToggleTodo, onDeleteTodo, onRenderTodoText,
}) => {
  const updateFloatingNote = useStore(s => s.updateFloatingNote)
  const { t } = useTranslation()

  // content 恒为 Markdown 真源：编辑器变更直存 MD，不再转 HTML。
  const handleMdChange = (v: string) => {
    onMdContentChange(v)
    onContentEdit(note.id)
    updateFloatingNote(note.id, { content: v })
  }

  const readOnly = !!note.locked || !!isArchived

  const editor = (
    <div className="flex-1 min-h-0" data-color-mode="dark">
      <Suspense fallback={<div className="flex-1" />}>
        <MDEditor
          value={mdContent}
          onChange={val => handleMdChange(val ?? '')}
          preview="edit"
          hideToolbar={readOnly}
          visiableDragbar={false}
          textareaProps={{ placeholder: t('note.markdownPlaceholder') }}
        />
      </Suspense>
    </div>
  )

  return (
    <div className="flex-1 min-h-0 flex flex-col relative select-text" style={nd}>
      <div className="flex-1 min-h-0" style={{
        display: note.collapsed ? 'none' : undefined,
        overflowY: 'auto',
      }}>
        {note.type === 'text' ? (
          <div className="w-full h-full flex flex-col">
            {/* Fixed format toolbar: view mode + font size + AI chat */}
            <FormatToolbar
              x={0} y={0}
              visible={showFormatBar && !note.collapsed}
              containerEl={document.body}
              onClose={() => onShowFormatBarChange(false)}
              fixed
              fontSize={note.fontSize}
              onFontSizeChange={s => updateFloatingNote(note.id, { fontSize: s })}
              viewMode={viewMode}
              onViewModeChange={onViewModeChange}
              noteId={note.id}
              onOpenChat={() => onShowChatPanelChange(true)}
            />
            {viewMode === 'preview' ? (
              <Suspense fallback={null}><MdPreview content={mdContent} className="flex-1" /></Suspense>
            ) : viewMode === 'split' ? (
              <div className="flex-1 flex flex-col min-h-0">
                <div className="flex-1 min-h-0 flex flex-col border-b border-white/[0.06]">
                  {editor}
                </div>
                <Suspense fallback={null}><MdPreview content={mdContent} className="flex-1 min-h-[60px]" /></Suspense>
              </div>
            ) : (
              editor
            )}
            {/* Word count (char count of markdown) + extract todos */}
            {viewMode !== 'preview' && (
              <div className="flex items-center justify-between px-3 py-1 border-t border-white/[0.04]">
                <button
                  onClick={onExtractTodos}
                  className="flex items-center gap-1 text-2xs transition-all hover:text-text-secondary"
                  style={{ color: 'var(--text-muted)' }}
                  title={t('note.extractTodosTooltip')}
                >
                  <Icon icon="fa-solid fa-list-check text-[8px]" />
                  <span>{t('note.extractTodosBtn')}</span>
                </button>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {t('note.charCount', { count: mdContent.length })}
                </span>
              </div>
            )}
            {/* Embedded todo list in text notes */}
            {(note.todos || []).length > 0 && (
              <div className="border-t border-white/[0.04] px-3 py-1.5">
                {(note.todos || []).map(td => (
                  <div key={td.id} className="group flex items-center gap-2 py-0.5">
                    <button onClick={() => onToggleTodo(td.id)}
                      className={`w-3 h-3 rounded border flex items-center justify-center flex-shrink-0 transition-all ${td.done ? 'bg-fluent-blue/20 border-fluent-blue/30' : 'border-white/20 hover:border-white/40'}`}>
                      {td.done && <svg className="w-1.5 h-1.5 text-fluent-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg>}
                    </button>
                    <span className={`flex-1 text-xs ${td.done ? 'line-through' : ''}`}
                      style={{ color: td.done ? 'var(--text-secondary)' : 'var(--text-primary)' }}>
                      {td.text}
                    </span>
                    <button onClick={() => onDeleteTodo(td.id)}
                      className="opacity-0 group-hover:opacity-100 text-white/55 hover:text-red-400/50 transition-all text-[8px]">
                      <Icon icon="fa-solid fa-xmark" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="px-3 py-2">
            {(note.todos || []).map(td => (
              <div key={td.id} className="group flex items-center gap-2 py-1.5 rounded hover:bg-white/[0.03] transition-colors">
                <button onClick={() => onToggleTodo(td.id)}
                  className={`w-3.5 h-3.5 rounded border flex items-center justify-center flex-shrink-0 transition-all ${td.done ? 'bg-fluent-blue/20 border-fluent-blue/30' : 'border-white/20 hover:border-white/40'}`}>
                  {td.done && <svg className="w-2 h-2 text-fluent-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg>}
                </button>
                {td.priority !== undefined && (
                  <span className="flex-shrink-0 w-1.5 h-1.5 rounded-full"
                    style={{ background: td.priority === 0 ? 'var(--danger)' : td.priority === 1 ? 'var(--warning)' : 'var(--text-muted)' }} />
                )}
                <span className={`flex-1 text-xs ${td.done ? 'line-through' : ''}`}
                  style={{ color: td.done ? 'var(--text-secondary)' : 'var(--text-primary)' }}>
                  {onRenderTodoText(td.text)}
                </span>
                {td.dueDate && (
                  <span className="text-2xs text-text-muted flex-shrink-0">{formatDueDate(td.dueDate)}</span>
                )}
                <button onClick={() => onDeleteTodo(td.id)}
                  className="opacity-0 group-hover:opacity-100 text-white/55 hover:text-red-400/50 transition-all text-2xs">
                  <Icon icon="fa-solid fa-xmark" />
                </button>
              </div>
            ))}
            <div className="flex items-center gap-2 mt-2 pt-2 border-t border-white/[0.04]">
              <input value={todoText} onChange={e => onTodoTextChange(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && onAddTodo()}
                placeholder={t('note.addTodoPlaceholder')}
                disabled={isArchived || note.locked}
                className="flex-1 bg-transparent text-xs text-text-secondary placeholder:text-white/55 outline-none disabled:opacity-40" />
              {todoText.trim() && (
                <button onClick={onAddTodo} className="text-xs text-text-secondary hover:text-text-secondary transition-colors">{t('note.addTodoBtn')}</button>
              )}
            </div>
          </div>
        )}
        {/* Backlinks panel */}
        {backlinks.length > 0 && (
          <div className="border-t border-white/[0.04]">
            <button
              onClick={() => onShowBacklinksChange(!showBacklinks)}
              className="w-full flex items-center gap-1.5 px-3 py-1.5 text-xs text-white/45 hover:text-text-secondary transition-colors"
            >
              <Icon icon={`fa-solid fa-chevron-right text-[8px] transition-transform ${showBacklinks ? 'rotate-90' : ''}`} />
              {t('note.referencedBy')} ({backlinks.length})
            </button>
            {showBacklinks && (
              <div className="px-3 pb-2 space-y-0.5 max-h-32 overflow-y-auto">
                {backlinks.slice(0, 50).map(n => (
                  <button
                    key={n.id}
                    onClick={() => {
                      useStore.getState().focusNote(n.id)
                      onShowBacklinksChange(false)
                    }}
                    className="block w-full text-left text-xs text-fluent-blue/70 hover:text-fluent-blue truncate transition-colors py-0.5"
                  >
                    {n.title || t('note.newNote')}
                  </button>
                ))}
                {backlinks.length > 50 && (
                  <div className="text-xs text-text-muted pt-0.5">
                    {t('note.moreBacklinks', { count: backlinks.length - 50 })}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default FloatingNoteContent
