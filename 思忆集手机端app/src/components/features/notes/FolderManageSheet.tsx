import { useState } from 'react'
import { Sheet, IconPlus, IconX } from '../../ui'
import { useStore } from '@desk-notes/shared'
import type { Folder } from '@desk-notes/shared'

interface FolderManageSheetProps {
  open: boolean
  onClose: () => void
  folders: Folder[]
  noteCounts: Map<string, number>
  onAddFolder: (name: string, parentId?: string) => void
  onRenameFolder: (id: string, name: string) => void
  onRemoveFolder: (id: string) => void
}

export default function FolderManageSheet({
  open, onClose, folders, noteCounts,
  onAddFolder, onRenameFolder, onRemoveFolder,
}: FolderManageSheetProps) {
  const [newName, setNewName] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [selectedParentId, setSelectedParentId] = useState<string | null>(null)

  const handleAdd = () => {
    if (!newName.trim()) return
    onAddFolder(newName.trim(), selectedParentId ?? undefined)
    setNewName('')
    setSelectedParentId(null)
  }

  const handleStartRename = (f: Folder) => {
    setEditingId(f.id)
    setEditName(f.name)
  }

  const handleConfirmRename = () => {
    if (editingId && editName.trim()) {
      onRenameFolder(editingId, editName.trim())
    }
    setEditingId(null)
  }

  const handleDelete = (id: string) => {
    if (confirmDelete === id) {
      onRemoveFolder(id)
      setConfirmDelete(null)
    } else {
      setConfirmDelete(id)
    }
  }

  const otherFolders = folders.filter(f => f.id !== 'default')

  // 递归构建文件夹树，返回平铺的列表含深度信息
  const buildTree = (parentId: string | null, depth: number = 0): { folder: Folder; depth: number }[] => {
    const children = otherFolders.filter(f => (f.parentId ?? null) === parentId)
    return children.flatMap(f => [
      { folder: f, depth },
      ...buildTree(f.id, depth + 1)
    ])
  }
  const folderTree = buildTree(null)

  return (
    <Sheet open={open} onClose={onClose} title="管理文件夹">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 16 }}>
        {otherFolders.length === 0 && (
          <div style={{
            fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)',
            textAlign: 'center', padding: '20px 0',
          }}>
            暂无文件夹
          </div>
        )}
        {folderTree.map(({ folder: f, depth }) => (
          <div key={f.id} style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '10px 0',
            paddingLeft: `${depth * 16 + 0}px`,
            borderBottom: '0.5px solid var(--border-subtle)',
          }}>
            {editingId === f.id ? (
              <>
                <input
                  value={editName}
                  onChange={e => setEditName(e.target.value)}
                  onBlur={handleConfirmRename}
                  onKeyDown={e => { if (e.key === 'Enter') handleConfirmRename() }}
                  autoFocus
                  style={{
                    flex: 1, border: 'none', background: 'var(--bg-primary)',
                    color: 'var(--text-primary)', fontSize: 'var(--text-body)',
                    padding: '6px 10px', borderRadius: 'var(--radius-sm)',
                    outline: 'none', fontFamily: 'inherit',
                  }}
                />
                <button
                  onClick={handleConfirmRename}
                  style={{
                    background: 'var(--accent)', color: '#fff', border: 'none',
                    borderRadius: 'var(--radius-sm)', padding: '6px 14px',
                    fontSize: 'var(--text-footnote)', cursor: 'pointer',
                  }}
                >
                  确定
                </button>
              </>
            ) : (
              <>
                {depth > 0 && (
                  <span style={{ color: 'var(--text-disabled)', fontSize: 12 }}>
                    └{' '}
                  </span>
                )}
                <span style={{
                  flex: 1, fontSize: 'var(--text-body)',
                  color: 'var(--text-primary)',
                }}>
                  {f.name}
                </span>
                <span style={{
                  fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
                  minWidth: 24, textAlign: 'right',
                }}>
                  {noteCounts.get(f.id) || 0}
                </span>
                <button
                  onClick={() => handleStartRename(f)}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: 'var(--accent)', fontSize: 'var(--text-footnote)',
                    padding: '4px 8px',
                  }}
                >
                  重命名
                </button>
                <button
                  onClick={() => handleDelete(f.id)}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: confirmDelete === f.id ? 'var(--danger)' : 'var(--text-disabled)',
                    fontSize: 'var(--text-footnote)',
                    display: 'flex', alignItems: 'center', gap: 4,
                    padding: '4px 4px',
                  }}
                >
                  {confirmDelete === f.id ? '确认删除' : <IconX size={14} />}
                </button>
              </>
            )}
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingBottom: 'calc(env(safe-area-inset-bottom, 48px) + 48px)' }}>
        {/* 父文件夹选择 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
            minWidth: 56,
          }}>
            父目录:
          </span>
          <select
            value={selectedParentId ?? ''}
            onChange={e => setSelectedParentId(e.target.value || null)}
            style={{
              flex: 1,
              padding: '8px 12px',
              borderRadius: 'var(--radius-sm)',
              border: '0.5px solid var(--border-default)',
              background: 'var(--bg-primary)',
              color: 'var(--text-primary)',
              fontSize: 'var(--text-footnote)',
              outline: 'none',
            }}
          >
            <option value="">根目录</option>
            {otherFolders.map(f => (
              <option key={f.id} value={f.id}>{f.name}</option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleAdd() }}
            placeholder="新建文件夹名称"
            style={{
              flex: 1, border: '0.5px solid var(--border-default)',
              background: 'var(--bg-primary)', color: 'var(--text-primary)',
              fontSize: 'var(--text-footnote)', padding: '10px 12px',
              borderRadius: 'var(--radius-sm)', outline: 'none',
              fontFamily: 'inherit',
            }}
          />
          <button
            onClick={handleAdd}
            disabled={!newName.trim()}
            style={{
              display: 'flex', alignItems: 'center', gap: 4,
              background: newName.trim() ? 'var(--accent)' : 'var(--bg-tertiary)',
              color: newName.trim() ? '#fff' : 'var(--text-disabled)',
              border: 'none', borderRadius: 'var(--radius-sm)',
              padding: '10px 16px', fontSize: 'var(--text-footnote)',
              cursor: newName.trim() ? 'pointer' : 'default',
            }}
          >
            <IconPlus size={16} /> 添加
          </button>
        </div>
      </div>

      {/* 全量导出按钮 */}
      <div style={{ marginTop: 16, paddingTop: 16, borderTop: '0.5px solid var(--border-subtle)' }}>
        <button
          onClick={() => {
            const allNotes = useStore.getState().floatingNotes
            const backup = {
              version: 1,
              exportedAt: new Date().toISOString(),
              noteCount: allNotes.length,
              folders: otherFolders,
              notes: allNotes,
            }
            const blob = new Blob([JSON.stringify(backup, null, 2)], { type: 'application/json' })
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = `思忆笔记备份_${new Date().toISOString().slice(0, 10)}.json`
            a.click()
            URL.revokeObjectURL(url)
          }}
          style={{
            width: '100%', padding: '12px 16px', textAlign: 'center',
            background: 'var(--accent-surface)', color: 'var(--accent)',
            border: 'none', borderRadius: 'var(--radius-sm)',
            fontSize: 'var(--text-body)', fontWeight: 500, cursor: 'pointer',
          }}
        >
          📤 导出全部笔记为 JSON 备份
        </button>
      </div>
    </Sheet>
  )
}
