import FloatingBall, { type FloatingBallMood } from '../plugins/floating-ball/src'

export type { FloatingBallMood }

export interface FloatingBallState {
  visible: boolean
  mood: FloatingBallMood
  badgeCount: number
  permissionGranted: boolean
}

type BallListener = (state: FloatingBallState) => void

/**
 * 思忆集悬浮球管理器 — 系统级悬浮窗控制
 *
 * 用法：
 *   import { floatingBall } from '../../lib/floatingBall'
 *   floatingBall.show()
 *   floatingBall.setMood('thinking')
 *   floatingBall.setBadge(3)
 *   floatingBall.onChange(state => console.log(state.mood))
 */
class FloatingBallManager {
  private _visible = false
  private _mood: FloatingBallMood = 'idle'
  private _badgeCount = 0
  private _permissionGranted = false
  private _listeners = new Set<BallListener>()
  private _cleanups: (() => void)[] = []

  get state(): FloatingBallState {
    return {
      visible: this._visible,
      mood: this._mood,
      badgeCount: this._badgeCount,
      permissionGranted: this._permissionGranted,
    }
  }

  // ── 生命周期 ──

  /** 初始化：检查权限并注册原生事件监听 */
  async init(): Promise<void> {
    const perm = await FloatingBall.checkPermission()
    this._permissionGranted = perm.granted

    // 注册原生事件
    const tapHandler = await FloatingBall.addListener('onTap', () => this._emit())
    const dblHandler = await FloatingBall.addListener('onDoubleTap', () => this._emit())
    const longHandler = await FloatingBall.addListener('onLongPress', () => this._emit())
    const dragHandler = await FloatingBall.addListener('onDragEnd', () => {
      this._emit()
    })

    this._cleanups = [
      () => tapHandler.remove(),
      () => dblHandler.remove(),
      () => longHandler.remove(),
      () => dragHandler.remove(),
    ]
  }

  /** 销毁：隐藏悬浮球并清理监听器 */
  async destroy(): Promise<void> {
    await this.hide()
    this._cleanups.forEach(fn => fn())
    this._cleanups = []
    this._listeners.clear()
  }

  // ── 控制 ──

  /** 显示悬浮球（自动请求权限） */
  async show(): Promise<boolean> {
    if (!this._permissionGranted) {
      const result = await FloatingBall.requestPermission()
      this._permissionGranted = result.granted
      if (!result.granted) return false
    }
    await FloatingBall.show()
    this._visible = true
    this._emit()
    return true
  }

  /** 隐藏悬浮球 */
  async hide(): Promise<void> {
    await FloatingBall.hide()
    this._visible = false
    this._emit()
  }

  /** 设置情绪状态 */
  async setMood(mood: FloatingBallMood): Promise<void> {
    this._mood = mood
    await FloatingBall.setMood({ mood })
    this._emit()
  }

  /** 设置待办角标（0 隐藏） */
  async setBadge(count: number): Promise<void> {
    this._badgeCount = Math.max(0, count)
    await FloatingBall.setBadge({ count: this._badgeCount })
    this._emit()
  }

  // ── 状态订阅 ──

  onChange(cb: BallListener): () => void {
    this._listeners.add(cb)
    cb(this.state)
    return () => this._listeners.delete(cb)
  }

  private _emit(): void {
    const s = this.state
    this._listeners.forEach(cb => cb(s))
  }
}

/** 全局单例 */
export const floatingBall = new FloatingBallManager()
export default floatingBall
