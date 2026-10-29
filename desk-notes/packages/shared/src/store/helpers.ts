import type { TodoItem, Folder } from '../types'

/**
 * 递归收集待办的所有后代 ID
 */
export function getDescendantIds(todos: TodoItem[], parentId: string): string[] {
  const directIds = todos.filter(t => t.parentId === parentId).map(t => t.id)
  return [...directIds, ...directIds.flatMap(id => getDescendantIds(todos, id))]
}

/** 递归收集文件夹的所有后代 ID（包括自身） */
export function getDescendantFolderIds(folders: Folder[], folderId: string): string[] {
  const children = folders.filter(f => f.parentId === folderId)
  return [folderId, ...children.flatMap(c => getDescendantFolderIds(folders, c.id))]
}

/** 获取文件夹的所有祖先 ID（父、祖父……） */
export function getAncestorFolderIds(folders: Folder[], folderId: string): string[] {
  const result: string[] = []
  let current = folders.find(f => f.id === folderId)
  while (current?.parentId) {
    const parent = folders.find(f => f.id === current!.parentId)
    if (parent) {
      result.push(parent.id)
      current = parent
    } else {
      break
    }
  }
  return result
}
