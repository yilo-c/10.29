import { pluginManager } from '../utils/pluginSystem'

let noteCreateCount = 0
let noteSaveCount = 0
let noteDeleteCount = 0

export function registerBuiltinPlugins(): void {
  pluginManager.register({
    id: 'builtin.stats',
    name: '使用统计',
    description: '记录便签创建、保存和删除的数量',
    version: '1.0.0',
    enabled: true,
    onNoteCreated: () => {
      noteCreateCount++
    },
    onNoteSaved: () => {
      noteSaveCount++
    },
    onNoteDeleted: () => {
      noteDeleteCount++
    },
  })
}

export function getStats() {
  return {
    created: noteCreateCount,
    saved: noteSaveCount,
    deleted: noteDeleteCount,
  }
}
