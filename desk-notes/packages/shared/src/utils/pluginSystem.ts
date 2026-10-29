import type { FloatingNote } from '../types'

export interface Plugin {
  id: string
  name: string
  description: string
  version: string
  enabled: boolean
  onAppStart?: () => void
  onNoteCreated?: (note: FloatingNote) => void
  onNoteSaved?: (note: FloatingNote) => void
  onNoteDeleted?: (note: FloatingNote) => void
}

class PluginManager {
  private plugins = new Map<string, Plugin>()

  register(plugin: Plugin): void {
    this.plugins.set(plugin.id, { ...plugin })
  }

  unregister(id: string): void {
    this.plugins.delete(id)
  }

  getPlugins(): Plugin[] {
    return Array.from(this.plugins.values())
  }

  getPlugin(id: string): Plugin | undefined {
    return this.plugins.get(id)
  }

  setEnabled(id: string, enabled: boolean): void {
    const plugin = this.plugins.get(id)
    if (plugin) {
      plugin.enabled = enabled
    }
  }

  runHook(hookName: string, ...args: unknown[]): void {
    for (const plugin of this.plugins.values()) {
      if (!plugin.enabled) continue
      const fn = (plugin as unknown as Record<string, unknown>)[hookName]
      if (typeof fn === 'function') {
        try {
          fn.call(plugin, ...args)
        } catch (e: unknown) {
          console.error(`[PluginSystem] Plugin "${plugin.id}" error in ${hookName}:`, e)
        }
      }
    }
  }
}

export const pluginManager = new PluginManager()
