import type { FloatingBallPlugin, FloatingBallMood } from './index'
import type { PluginListenerHandle } from '@capacitor/core'

export class FloatingBallWeb implements FloatingBallPlugin {
  private listeners = new Map<string, Set<(...args: any[]) => void>>()

  async show(): Promise<void> {
    console.log('[FloatingBall Web] show (no-op in browser)')
  }
  async hide(): Promise<void> {
    console.log('[FloatingBall Web] hide')
  }
  async setMood(options: { mood: FloatingBallMood }): Promise<void> {
    console.log('[FloatingBall Web] mood:', options.mood)
  }
  async setBadge(options: { count: number }): Promise<void> {
    console.log('[FloatingBall Web] badge:', options.count)
  }
  async checkPermission(): Promise<{ granted: boolean }> {
    return { granted: true }
  }
  async requestPermission(): Promise<{ granted: boolean }> {
    return { granted: true }
  }

  async addListener(eventName: string, listener: (...args: any[]) => void): Promise<PluginListenerHandle> {
    if (!this.listeners.has(eventName)) this.listeners.set(eventName, new Set())
    this.listeners.get(eventName)!.add(listener)
    return {
      remove: () => {
        this.listeners.get(eventName)?.delete(listener)
        return Promise.resolve()
      }
    }
  }

  async removeAllListeners(): Promise<void> {
    this.listeners.clear()
  }
}
