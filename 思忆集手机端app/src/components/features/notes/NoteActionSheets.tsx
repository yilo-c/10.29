import { useState } from 'react'
import { useStore } from '@desk-notes/shared'
import { IconX, Sheet, Button } from '../../ui'
import type { FloatingNote } from '@desk-notes/shared'

const NOTE_COLORS = ['#FF6B6B', '#FFB84D', '#FFD93D', '#6BCB77', '#4D96FF', '#9B59B6', '#FF8C94', '#A8E6CF']

interface NoteActionSheetsProps {
  actionNoteId: string | null
  onClose: () => void
  otherFolders: { id: string; name: string }[]
  noteCounts: Map<string, number>
  onDelete: (id: string) => void
  onShare: (note: FloatingNote) => void
  onMoveNote: (noteId: string, folderId: string | null) => void
}

function ActionRow({ label, danger, onClick }: { label: string; danger?: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      display: 'flex', alignItems: 'center', width: '100%',
      padding: '12px 4px', border: 'none', background: 'none',
      cursor: 'pointer', fontSize: 'var(--text-body)',
      color: danger ? 'var(--danger)' : 'var(--text-primary)',
      borderRadius: 'var(--radius-sm)',
    }}>
      {label}
    </button>
  )
}

function FolderRow({ label, count, active, onClick }: { label: string; count?: number; active?: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      display: 'flex', alignItems: 'center', width: '100%',
      padding: '12px 4px', border: 'none', background: 'none',
      cursor: 'pointer', fontSize: 'var(--text-body)',
      color: active ? 'var(--accent)' : 'var(--text-primary)',
      fontWeight: active ? 600 : 400, borderRadius: 'var(--radius-sm)',
    }}>
      <span style={{ flex: 1, textAlign: 'left' }}>{label}</span>
      {count !== undefined && <span style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)' }}>{count}</span>}
    </button>
  )
}

export default function NoteActionSheets({ actionNoteId, onClose, otherFolders, noteCounts, onDelete, onShare, onMoveNote }: NoteActionSheetsProps) {
  const notes = useStore(s => s.floatingNotes)
  const updateFloatingNote = useStore(s => s.updateFloatingNote)
  const actionNote = actionNoteId ? notes.find(n => n.id === actionNoteId) || null : null
  const [showColorPicker, setShowColorPicker] = useState(false)
  const [showTagEditor, setShowTagEditor] = useState(false)
  const [tagInput, setTagInput] = useState('')
  const [showMoveFolder, setShowMoveFolder] = useState(false)

  return (
    <>
      {/* Action Sheet */}
      <Sheet open={!!actionNoteId && !showColorPicker && !showTagEditor && !showMoveFolder} onClose={onClose}>
        {actionNote && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {actionNote.title || '无标题'}
            </div>
            <ActionRow label="标记颜色" onClick={() => setShowColorPicker(true)} />
            <ActionRow label={actionNote.tags?.length ? `标签 (${actionNote.tags.length})` : '添加标签'} onClick={() => { setTagInput(''); setShowTagEditor(true) }} />
            <ActionRow label={actionNote.pinned ? '取消置顶' : '置顶'} onClick={() => { updateFloatingNote(actionNote.id, { pinned: !actionNote.pinned }); onClose() }} />
            {otherFolders.length > 0 && <ActionRow label="移动到文件夹" onClick={() => setShowMoveFolder(true)} />}
            <ActionRow label="分享" onClick={() => onShare(actionNote)} />
            <ActionRow label="导出为 .md" onClick={() => {
              const content = `${actionNote.title || ''}\n\n${actionNote.content || ''}`
              const blob = new Blob([content], { type: 'text/markdown' })
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url
              a.download = `${actionNote.title || '笔记'}.md`
              a.click()
              URL.revokeObjectURL(url)
              onClose()
            }} />
            <div style={{ borderTop: '0.5px solid var(--border-subtle)', margin: '4px 0' }} />
            <ActionRow label="删除" danger onClick={() => onDelete(actionNote.id)} />
          </div>
        )}
      </Sheet>

      {/* Color Picker Sheet */}
      <Sheet open={showColorPicker} onClose={() => setShowColorPicker(false)} title="标记颜色">
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', justifyContent: 'center', padding: '8px 0' }}>
          <button onClick={() => { if (actionNoteId) updateFloatingNote(actionNoteId, { color: undefined }); setShowColorPicker(false) }} style={{ width: 44, height: 44, borderRadius: '50%', border: '2px solid var(--border-default)', background: 'transparent', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <IconX size={16} />
          </button>
          {NOTE_COLORS.map(c => (
            <button key={c} onClick={() => { if (actionNoteId) updateFloatingNote(actionNoteId, { color: c }); setShowColorPicker(false) }} style={{ width: 44, height: 44, borderRadius: '50%', border: `3px solid ${actionNote?.color === c ? c : 'transparent'}`, outline: `2px solid ${actionNote?.color === c ? c : 'transparent'}`, outlineOffset: 2, background: c, cursor: 'pointer', transition: 'transform 0.15s ease' }} />
          ))}
        </div>
      </Sheet>

      {/* Tag Editor Sheet */}
      <Sheet open={showTagEditor} onClose={() => setShowTagEditor(false)} title="标签">
        {actionNote && (
          <div>
            {actionNote.tags && actionNote.tags.length > 0 && (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                {actionNote.tags.map(tag => (
                  <div key={tag} style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 'var(--radius-full)', background: 'var(--accent-surface)', color: 'var(--accent)', fontSize: 'var(--text-footnote)', fontWeight: 500 }}>
                    {tag}
                    <button onClick={() => { if (actionNoteId) updateFloatingNote(actionNoteId, { tags: (actionNote.tags || []).filter(t => t !== tag) }) }} style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', padding: 0, display: 'flex' }}>
                      <IconX size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input value={tagInput} onChange={e => setTagInput(e.target.value)} placeholder="输入标签名称" style={{ flex: 1, padding: '8px 10px', borderRadius: 'var(--radius-sm)', border: '0.5px solid var(--border-default)', background: 'var(--bg-tertiary)', color: 'var(--text-primary)', fontSize: 'var(--text-footnote)', outline: 'none' }}
                onKeyDown={e => { if (e.key === 'Enter' && tagInput.trim() && actionNoteId) { const t = tagInput.trim(); if (!(actionNote.tags || []).includes(t)) updateFloatingNote(actionNoteId, { tags: [...(actionNote.tags || []), t] }); setTagInput('') } }}
              />
              <Button variant="primary" disabled={!tagInput.trim()} onClick={() => { if (tagInput.trim() && actionNoteId) { const t = tagInput.trim(); if (!(actionNote.tags || []).includes(t)) updateFloatingNote(actionNoteId, { tags: [...(actionNote.tags || []), t] }); setTagInput('') } }}>
                添加
              </Button>
            </div>
          </div>
        )}
      </Sheet>

      {/* Move Folder Sheet */}
      <Sheet open={showMoveFolder} onClose={() => setShowMoveFolder(false)} title="移动到文件夹">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {actionNoteId && (
            <FolderRow label="无（移出文件夹）" active={false} onClick={() => onMoveNote(actionNoteId, null)} />
          )}
          {otherFolders.map(f => (
            <FolderRow key={f.id} label={f.name} count={noteCounts.get(f.id)} active={actionNote?.folderId === f.id} onClick={() => onMoveNote(actionNoteId!, f.id)} />
          ))}
        </div>
      </Sheet>
    </>
  )
}
