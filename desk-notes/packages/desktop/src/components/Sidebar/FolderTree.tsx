import React, { useState, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { getDescendantFolderIds } from '../../store/helpers'
import ContextMenu from '../Common/ContextMenu'
import type { Folder } from '../../types'
import { useTranslation } from '../../i18n'

interface TreeNodeProps {
  folder: Folder
  depth: number
  expandedSet: Set<string>
  onToggle: (id: string) => void
  renamingId: string | null
  renameValue: string
  onStartRename: (id: string, name: string) => void
  onRenameChange: (v: string) => void
  onFinishRename: (id: string) => void
  onCancelRename: () => void
  onContextMenu: (e: React.MouseEvent, folder: Folder) => void
  dragState: DragState
  onDragStart: (e: React.DragEvent, folder: Folder) => void
  onDragOver: (e: React.DragEvent, folder: Folder) => void
  onDragLeave: (e: React.DragEvent) => void
  onDrop: (e: React.DragEvent, targetFolder: Folder) => void
  onAddSubfolder: (parentId: string) => void
  addingSubfolderId: string | null
  newSubfolderName: string
  onNewSubfolderNameChange: (v: string) => void
  onFinishAddSubfolder: () => void
}

interface DragState {
  draggingId: string | null
  dropTargetId: string | null
  dropPosition: 'before' | 'inside' | 'after' | null
}

const TreeNode: React.FC<TreeNodeProps> = ({
  folder, depth, expandedSet, onToggle,
  renamingId, renameValue, onStartRename, onRenameChange, onFinishRename, onCancelRename,
  onContextMenu, dragState, onDragStart, onDragOver, onDragLeave, onDrop,
  onAddSubfolder, addingSubfolderId, newSubfolderName, onNewSubfolderNameChange, onFinishAddSubfolder,
}) => {
  const folders = useStore(s => s.folders)
  const floatingNotes = useStore(s => s.floatingNotes)
  const activeFolderId = useStore(s => s.activeFolderId)
  const setActiveFolder = useStore(s => s.setActiveFolder)
  const { t } = useTranslation()
  const isExpanded = expandedSet.has(folder.id)
  const children = folders.filter(f => f.parentId === folder.id).sort((a, b) => a.sortOrder - b.sortOrder)
  const isActive = activeFolderId === folder.id
  const isDragging = dragState.draggingId === folder.id
  const isDropTarget = dragState.dropTargetId === folder.id

  // Count notes in this folder AND all descendant folders
  const descendantIds = getDescendantFolderIds(folders, folder.id)
  const noteCount = floatingNotes.filter(n => n.folderId && descendantIds.includes(n.folderId)).length

  // Determine drop indicator class
  let dropClass = ''
  if (isDropTarget && dragState.dropPosition === 'before') dropClass = 'border-t border-fluent-blue/40'
  else if (isDropTarget && dragState.dropPosition === 'after') dropClass = 'border-b border-fluent-blue/40'
  else if (isDropTarget && dragState.dropPosition === 'inside') dropClass = 'bg-fluent-blue/[0.06]'

  return (
    <>
      <div
        className={`group flex items-center ${dropClass} ${isDragging ? 'opacity-40' : ''}`}
        draggable={renamingId !== folder.id}
        onContextMenu={e => onContextMenu(e, folder)}
        onDragStart={e => onDragStart(e, folder)}
        onDragOver={e => onDragOver(e, folder)}
        onDragLeave={onDragLeave}
        onDrop={e => onDrop(e, folder)}
      >
        {/* Indent spacer */}
        <div style={{ width: depth * 12 }} className="flex-shrink-0" />

        {/* Expand/collapse toggle */}
        <button
          onClick={e => { e.stopPropagation(); onToggle(folder.id) }}
          className="w-3.5 h-full flex items-center justify-center flex-shrink-0 text-[8px] text-text-dim hover:text-text-secondary transition-colors"
        >
          <Icon icon={`fa-solid fa-chevron-right transition-transform ${children.length > 0 && isExpanded ? 'rotate-90' : ''}`} />
        </button>

        {renamingId === folder.id ? (
          <input
            autoFocus
            value={renameValue}
            onChange={e => onRenameChange(e.target.value)}
            onBlur={() => onFinishRename(folder.id)}
            onKeyDown={e => {
              if (e.key === 'Enter') onFinishRename(folder.id)
              if (e.key === 'Escape') onCancelRename()
            }}
            className="flex-1 mx-1 bg-white/[0.06] text-xs text-text-secondary px-1.5 py-0.5 rounded outline-none border border-white/[0.06]"
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <button
            onClick={() => setActiveFolder(folder.id)}
            onDoubleClick={() => onStartRename(folder.id, folder.name)}
            className={`flex-1 flex items-center gap-2 py-1.5 text-sm transition-colors text-left ${
              isActive
                ? 'bg-white/[0.06] text-text-secondary'
                : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.03]'
            }`}
          >
            <Icon icon={`fa-solid ${isExpanded && children.length > 0 ? 'fa-folder-open' : 'fa-folder'} text-2xs w-3 text-center flex-shrink-0`} />
            <span className="flex-1 truncate">{folder.name}</span>
            <span className="text-2xs text-text-dim pr-1">{noteCount || ''}</span>
          </button>
        )}
      </div>

      {/* Children */}
      {isExpanded && children.length > 0 && (
        <div>
          {children.map(child => (
            <TreeNode
              key={child.id}
              folder={child}
              depth={depth + 1}
              expandedSet={expandedSet}
              onToggle={onToggle}
              renamingId={renamingId}
              renameValue={renameValue}
              onStartRename={onStartRename}
              onRenameChange={onRenameChange}
              onFinishRename={onFinishRename}
              onCancelRename={onCancelRename}
              onContextMenu={onContextMenu}
              dragState={dragState}
              onDragStart={onDragStart}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              onAddSubfolder={onAddSubfolder}
              addingSubfolderId={addingSubfolderId}
              newSubfolderName={newSubfolderName}
              onNewSubfolderNameChange={onNewSubfolderNameChange}
              onFinishAddSubfolder={onFinishAddSubfolder}
            />
          ))}
        </div>
      )}

      {/* Inline "add subfolder" input */}
      {addingSubfolderId === folder.id && (
        <div
          className="flex items-center px-2 py-1"
          style={{ paddingLeft: 12 + (depth + 1) * 12 }}
          onClick={e => e.stopPropagation()}
        >
          <input
            ref={el => el?.scrollIntoView({ block: 'nearest' })}
            autoFocus
            value={newSubfolderName}
            onChange={e => onNewSubfolderNameChange(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                e.preventDefault()
                onFinishAddSubfolder()
              }
              if (e.key === 'Escape') {
                e.preventDefault()
                onNewSubfolderNameChange('')
                onAddSubfolder('')
              }
            }}
            onBlur={() => {
              if (newSubfolderName.trim()) onFinishAddSubfolder()
              else { onNewSubfolderNameChange(''); onAddSubfolder('') }
            }}
            placeholder={t('folder.folderName')}
            className="flex-1 bg-white/[0.06] text-xs text-text-secondary placeholder:text-text-muted px-1.5 py-0.5 rounded outline-none border border-white/[0.06]"
          />
        </div>
      )}
    </>
  )
}

const FolderTree: React.FC = () => {
  const { t } = useTranslation()
  const folders = useStore(s => s.folders)
  const activeFolderId = useStore(s => s.activeFolderId)
  const setActiveFolder = useStore(s => s.setActiveFolder)
  const addFolder = useStore(s => s.addFolder)
  const removeFolder = useStore(s => s.removeFolder)
  const renameFolder = useStore(s => s.renameFolder)
  const floatingNotes = useStore(s => s.floatingNotes)
  // P4a：默认折叠，把垂直空间还给笔记列表
  const [expanded, setExpanded] = useState(false)

  // Expanded state per folder (all default expanded)
  const [expandedSet, setExpandedSet] = useState<Set<string>>(() => new Set(folders.map(f => f.id)))

  // Rename state
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')

  // Context menu state
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; folder: Folder } | null>(null)

  // Drag state
  const [dragState, setDragState] = useState<DragState>({
    draggingId: null, dropTargetId: null, dropPosition: null,
  })

  // Adding subfolder state
  const [addingSubfolderId, setAddingSubfolderId] = useState<string | null>(null)
  const [newSubfolderName, setNewSubfolderName] = useState('')

  // Root-level folders (no parentId)
  const rootFolders = folders
    .filter(f => !f.parentId && f.id !== 'default')
    .sort((a, b) => a.sortOrder - b.sortOrder)

  const totalNotes = floatingNotes.length
  const uncategorizedCount = floatingNotes.filter(n => !n.folderId).length

  const toggleExpanded = useCallback((id: string) => {
    setExpandedSet(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleContextMenuAction = useCallback((action: string) => {
    if (!contextMenu) return
    const { folder } = contextMenu

    switch (action) {
      case 'add-subfolder':
        setAddingSubfolderId(folder.id)
        setNewSubfolderName('')
        // Auto-expand the parent to show the new input
        setExpandedSet(prev => new Set([...prev, folder.id]))
        break
      case 'rename':
        setRenamingId(folder.id)
        setRenameValue(folder.name)
        break
      case 'delete':
        if (confirm(t('folder.deleteConfirm', { name: folder.name }))) {
          removeFolder(folder.id)
          if (activeFolderId === folder.id) setActiveFolder(null)
        }
        break
    }
    setContextMenu(null)
  }, [contextMenu, removeFolder, activeFolderId, setActiveFolder, t])

  // ── Drag-and-drop handlers ────────────────────────────────

  const handleDragStart = useCallback((e: React.DragEvent, folder: Folder) => {
    e.dataTransfer.setData('text/plain', folder.id)
    e.dataTransfer.effectAllowed = 'move'
    setDragState(prev => ({ ...prev, draggingId: folder.id }))
  }, [])

  const getDropPosition = useCallback((e: React.DragEvent, el: HTMLElement): 'before' | 'inside' | 'after' => {
    const rect = el.getBoundingClientRect()
    const y = e.clientY - rect.top
    const h = rect.height
    if (y < h * 0.25) return 'before'
    if (y > h * 0.75) return 'after'
    return 'inside'
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent, targetFolder: Folder) => {
    e.preventDefault()
    e.stopPropagation()
    if (dragState.draggingId === targetFolder.id) return
    // Prevent dropping onto own descendant
    const descendantIds = getDescendantFolderIds(folders, targetFolder.id)
    if (descendantIds.includes(dragState.draggingId ?? '')) return

    const pos = getDropPosition(e, e.currentTarget as HTMLElement)
    setDragState(prev => ({
      ...prev,
      dropTargetId: targetFolder.id,
      dropPosition: pos,
    }))
  }, [dragState.draggingId, folders, getDropPosition])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    // Only clear if we actually left this element (not entering a child)
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setDragState(prev => ({ ...prev, dropTargetId: null, dropPosition: null }))
    }
  }, [])

  const handleDrop = useCallback((e: React.DragEvent, targetFolder: Folder) => {
    e.preventDefault()
    e.stopPropagation()
    const draggedId = e.dataTransfer.getData('text/plain')
    if (!draggedId || draggedId === targetFolder.id) {
      setDragState({ draggingId: null, dropTargetId: null, dropPosition: null })
      return
    }

    const pos = dragState.dropPosition

    // Get the dragged folder's current parent
    const draggedFolder = folders.find(f => f.id === draggedId)
    if (!draggedFolder) {
      setDragState({ draggingId: null, dropTargetId: null, dropPosition: null })
      return
    }

    // Calculate new parentId and sortOrder
    let newParentId: string | undefined
    let newSortOrder: number

    if (pos === 'inside') {
      // Drop as child of target
      newParentId = targetFolder.id
      const siblings = folders.filter(f => f.parentId === targetFolder.id)
      newSortOrder = siblings.length > 0
        ? Math.max(...siblings.map(f => f.sortOrder)) + 1000
        : 1000
    } else {
      // Drop before/after target (same level as target)
      newParentId = targetFolder.parentId
      const siblings = folders
        .filter(f => f.parentId === targetFolder.parentId && f.id !== draggedId)
        .sort((a, b) => a.sortOrder - b.sortOrder)

      const targetIdx = siblings.findIndex(f => f.id === targetFolder.id)
      if (pos === 'before') {
        if (targetIdx <= 0) {
          newSortOrder = siblings.length > 0 ? siblings[0].sortOrder - 1000 : 1000
        } else {
          newSortOrder = (siblings[targetIdx - 1].sortOrder + siblings[targetIdx].sortOrder) / 2
        }
      } else {
        if (targetIdx >= siblings.length - 1) {
          newSortOrder = siblings.length > 0 ? siblings[siblings.length - 1].sortOrder + 1000 : 1000
        } else {
          newSortOrder = (siblings[targetIdx].sortOrder + siblings[targetIdx + 1].sortOrder) / 2
        }
      }
    }

    // Create a new folder with updated parent and sortOrder
    const updatedFolders = folders.map(f =>
      f.id === draggedId
        ? { ...f, parentId: newParentId, sortOrder: newSortOrder }
        : f
    )

    // Set the updated folders via the store action
    useStore.getState().reorderFolders(updatedFolders)

    setDragState({ draggingId: null, dropTargetId: null, dropPosition: null })
  }, [dragState.dropPosition, folders])

  // Clean up drag state on drag end
  useEffect(() => {
    const handleDragEnd = () => {
      setDragState({ draggingId: null, dropTargetId: null, dropPosition: null })
    }
    document.addEventListener('dragend', handleDragEnd)
    return () => document.removeEventListener('dragend', handleDragEnd)
  }, [])

  const handleAddSubfolder = useCallback((folderId: string) => {
    if (newSubfolderName.trim()) {
      addFolder(newSubfolderName.trim(), folderId || undefined)
    }
    setAddingSubfolderId(null)
    setNewSubfolderName('')
  }, [newSubfolderName, addFolder])

  const handleNewRootFolder = useCallback(() => {
    const id = addFolder(t('folder.newFolder'))
    setRenamingId(id)
    setRenameValue(t('folder.newFolder'))
    setExpandedSet(prev => new Set([...prev, id]))
  }, [addFolder, t])

  return (
    <>
      <div className="border-b border-white/[0.04]">
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center gap-2 px-3 py-2 text-xs text-text-muted hover:text-text-secondary hover:bg-white/[0.03] transition-colors"
        >
          <Icon icon={`fa-solid fa-chevron-right text-[9px] transition-transform ${expanded ? 'rotate-90' : ''}`} />
          <Icon icon="fa-solid fa-folder-open text-2xs" />
          {t('folder.allNotes')}
        </button>
        {expanded && (
          <div className="pb-1">
            {/* "All notes" root */}
            <button
              onClick={() => setActiveFolder(null)}
              className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-none text-sm transition-colors text-left ${
                activeFolderId === null
                  ? 'bg-white/[0.06] text-text-secondary'
                  : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.03]'
              }`}
            >
              <Icon icon="fa-solid fa-inbox text-2xs w-4 text-center" />
              <span className="flex-1 truncate">{t('folder.allNotes')}</span>
              <span className="text-2xs text-text-dim">{totalNotes}</span>
            </button>

            {/* Recursive folder tree */}
            {rootFolders.map(folder => (
              <TreeNode
                key={folder.id}
                folder={folder}
                depth={0}
                expandedSet={expandedSet}
                onToggle={toggleExpanded}
                renamingId={renamingId}
                renameValue={renameValue}
                onStartRename={(id, name) => {
                  setRenamingId(id)
                  setRenameValue(name)
                }}
                onRenameChange={setRenameValue}
                onFinishRename={(id) => {
                  const v = renameValue.trim()
                  if (v) renameFolder(id, v)
                  setRenamingId(null)
                  setRenameValue('')
                }}
                onCancelRename={() => {
                  setRenamingId(null)
                  setRenameValue('')
                }}
                onContextMenu={(e, folder) => {
                  e.preventDefault()
                  setContextMenu({ x: e.clientX, y: e.clientY, folder })
                }}
                dragState={dragState}
                onDragStart={handleDragStart}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onAddSubfolder={(parentId) => {
                  setAddingSubfolderId(parentId)
                  setNewSubfolderName('')
                }}
                addingSubfolderId={addingSubfolderId}
                newSubfolderName={newSubfolderName}
                onNewSubfolderNameChange={setNewSubfolderName}
                onFinishAddSubfolder={() => handleAddSubfolder(addingSubfolderId || '')}
              />
            ))}

            {/* Uncategorized */}
            <button
              onClick={() => setActiveFolder('__uncategorized__')}
              className={`w-full flex items-center gap-2 px-3 py-1.5 text-sm transition-colors text-left ${
                activeFolderId === '__uncategorized__'
                  ? 'bg-white/[0.06] text-text-secondary'
                  : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.03]'
              }`}
            >
              <Icon icon="fa-solid fa-question text-2xs w-4 text-center" />
              <span className="flex-1 truncate">{t('folder.noFolder')}</span>
              <span className="text-2xs text-text-dim">{uncategorizedCount}</span>
            </button>
            {/* New root folder */}
            <button
              onClick={handleNewRootFolder}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-text-muted hover:text-text-secondary hover:bg-white/[0.03] transition-colors"
            >
              <Icon icon="fa-solid fa-plus text-2xs w-4 text-center" />
              <span>{t('folder.newFolder')}</span>
            </button>
          </div>
        )}
      </div>

      {/* Context menu */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          items={[
            { label: t('folder.newSubfolder'), icon: 'fa-folder-open', onClick: () => handleContextMenuAction('add-subfolder') },
            { label: t('folder.rename'), icon: 'fa-pen', onClick: () => handleContextMenuAction('rename') },
            { label: t('folder.delete'), icon: 'fa-trash-can', danger: true, onClick: () => handleContextMenuAction('delete') },
          ]}
        />
      )}
    </>
  )
}

export default FolderTree
