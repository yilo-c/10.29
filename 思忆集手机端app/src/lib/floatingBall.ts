import FloatingBall, { type FloatingBallMood, type DragEndInfo } from '../plugins/floating-ball/src'

export type { FloatingBallMood }

export interface FloatingBallState {
  visible: boolean
  mood: FloatingBallMood
  badgeCount: number
  permissionGranted: boolean
}

/** 悬浮球交互回调 —— 由业务层注入，让球真正“能做点事”而不是只有动画 */
export interface FloatingBallHandlers {
  /** 单击：唤出 AI 对话 */
  onTap?: () => void
  /** 双击：快速记一笔 */
  onDoubleTap?: () => void
  /** 长按：展开更多操作 */
  onLongPress?: () => void
  /** 拖拽结束：记住吸附位置 */
  onDragEnd?: (info: DragEndInfo) => void
}

type BallListener = (state: FloatingBallState) => void

/**
 * 思忆集悬浮球管理器 — 系统级悬浮窗控制
 *
 * 用法：
 *   import { floatingBall } from '../../lib/floatingBall'
 *   floatingBall.setHandlers({ onTap: () => openChat() })
 *   floatingBall.show()
 *   floatingBall.setMood('thinking')
 *   floatingBall.setBadge(3)
 *   floatingBall.onChange(state => console.log(state.mood))
 *
 * 历史问题（已修）：
 *  1. android/app/build.gradle 缺 `implementation project(':siyi-floating-ball')`，
 *     模块被 include 却从不编译 → 原生侧完全没有这个插件，所有调用静默失败。
 *  2. MainActivity 只注册了 SiyiNativePlugin，没注册 FloatingBallPlugin
 *     → 前端 registerPlugin('FloatingBall') 拿到的是 undefined。
 *  3. 本类只监听 onTap 等事件却只做 `_emit()`（广播状态），
 *     没有把点击转成业务动作 → 球能拖能变色，点了没反应。
 */
class FloatingBallManager {
  private _visible = false
  private _mood: FloatingBallMood = 'idle'
  private _badgeCount = 0
  private _permissionGranted = false
  private _listeners = new Set<BallListener>()
  private _cleanups: (() => void)[] = []
  private _handlers: FloatingBallHandlers = {}
  private _inited = false

  get state(): FloatingBallState {
    return {
      visible: this._visible,
      mood: this._mood,
      badgeCount: this._badgeCount,
      permissionGranted: this._permissionGranted,
    }
  }

  /** 注入交互回调（可随时更新，后设的覆盖先设的） */
  setHandlers(handlers: FloatingBallHandlers): void {
    this._handlers = { ...this._handlers, ...handlers }
  }

  // ── 生命周期 ──

  /** 初始化：检查权限并注册原生事件监听（幂等） */
  async init(): Promise<void> {
    if (this._inited) {
      // 已初始化则只刷新权限状态
      try {
        const perm = await FloatingBall.checkPermission()
        this._permissionGranted = perm.granted
        this._emit()
      } catch { /* native 不可用 */ }
      return
    }
    this._inited = true

    try {
      const perm = await FloatingBall.checkPermission()
      this._permissionGranted = perm.granted
    } catch {
      this._permissionGranted = false
    }

    // 注册原生事件 → 转成业务回调
    try {
      const tapHandler = await FloatingBall.addListener('onTap', () => {
        this._emit()
        this._handlers.onTap?.()
      })
      const dblHandler = await FloatingBall.addListener('onDoubleTap', () => {
        this._emit()
        this._handlers.onDoubleTap?.()
      })
      const longHandler = await FloatingBall.addListener('onLongPress', () => {
        this._emit()
        this._handlers.onLongPress?.()
      })
      const dragHandler = await FloatingBall.addListener('onDragEnd', (info: DragEndInfo) => {
        this._emit()
        this._handlers.onDragEnd?.(info)
      })

      this._cleanups = [
        () => { void tapHandler.remove() },
        () => { void dblHandler.remove() },
        () => { void longHandler.remove() },
        () => { void dragHandler.remove() },
      ]
    } catch {
      this._cleanups = []
    }

    this._emit()
  }

  /** 销毁：隐藏悬浮球并清理监听器 */
  async destroy(): Promise<void> {
    await this.hide()
    this._cleanups.forEach(fn => fn())
    this._cleanups = []
    this._listeners.clear()
    this._inited = false
  }

  // ── 控制 ──

  /**
   * 显示悬浮球。
   * 返回 false 有两种情况：权限被拒，或原生插件不可用（浏览器环境）。
   * 调用方应据此区分提示文案，而不是统一当成“权限被拒”。
   */
  async show(): Promise<{ ok: boolean; reason?: 'permission' | 'unavailable' }> {
    if (!this._permissionGranted) {
      try {
        const result = await FloatingBall.requestPermission()
        this._permissionGranted = result.granted
      } catch {
        return { ok: false, reason: 'unavailable' }
      }
      if (!this._permissionGranted) return { ok: false, reason: 'permission' }
    }
    try {
      await FloatingBall.show()
    } catch {
      return { ok: false, reason: 'unavailable' }
    }
    this._visible = true
    this._emit()
    return { ok: true }
  }

  /** 隐藏悬浮球 */
  async hide(): Promise<void> {
    try { await FloatingBall.hide() } catch { /* native 不可用 */ }
    this._visible = false
    this._emit()
  }

  /** 设置情绪状态 */
  async setMood(mood: FloatingBallMood): Promise<void> {
    this._mood = mood
    try { await FloatingBall.setMood({ mood }) } catch { /* ignore */ }
    this._emit()
  }

  /** 设置待办角标（0 隐藏） */
  async setBadge(count: number): Promise<void> {
    this._badgeCount = Math.max(0, count)
    try { await FloatingBall.setBadge({ count: this._badgeCount }) } catch { /* ignore */ }
    this._emit()
  }

  /** 重新检查悬浮窗权限（用户从系统设置返回后调用） */
  async refreshPermission(): Promise<boolean> {
    try {
      const perm = await FloatingBall.checkPermission()
      this._permissionGranted = perm.granted
    } catch {
      this._permissionGranted = false
    }
    this._emit()
    return this._permissionGranted
  }

  // ── 状态订阅 ──

  onChange(cb: BallListener): () => void {
    this._listeners.add(cb)
    cb(this.state)
    return () => { this._listeners.delete(cb) }
  }

  private _emit(): void {
    const s = this.state
    this._listeners.forEach(cb => {
      try { cb(s) } catch { /* 单个订阅者异常不影响其它 */ }
    })
  }
}

/** 全局单例 */
export const floatingBall = new FloatingBallManager()
export default floatingBall
