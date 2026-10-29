import { registerPlugin, type PluginListenerHandle } from '@capacitor/core'

export type FloatingBallMood = 'idle' | 'thinking' | 'happy' | 'alert'

export interface DragEndInfo {
  x: number
  y: number
  edge: 'left' | 'right'
}

export interface FloatingBallPlugin {
  /** 显示悬浮球并启动前台服务 */
  show(): Promise<void>
  /** 隐藏并销毁悬浮球 */
  hide(): Promise<void>
  /** 设置情绪状态（光晕颜色 + 指示器） */
  setMood(options: { mood: FloatingBallMood }): Promise<void>
  /** 设置待办角标（0 隐藏） */
  setBadge(options: { count: number }): Promise<void>
  /** 检查悬浮窗权限 */
  checkPermission(): Promise<{ granted: boolean }>
  /** 引导用户授予悬浮窗权限 */
  requestPermission(): Promise<{ granted: boolean }>

  /** 单击回调 */
  addListener(eventName: 'onTap', listener: () => void): Promise<PluginListenerHandle>
  /** 双击回调 */
  addListener(eventName: 'onDoubleTap', listener: () => void): Promise<PluginListenerHandle>
  /** 长按回调 */
  addListener(eventName: 'onLongPress', listener: () => void): Promise<PluginListenerHandle>
  /** 拖拽结束回调（含吸附边缘方向） */
  addListener(eventName: 'onDragEnd', listener: (info: DragEndInfo) => void): Promise<PluginListenerHandle>
}

const FloatingBall = registerPlugin<FloatingBallPlugin>('FloatingBall', {
  android: () => import('./web').then(m => new m.FloatingBallWeb()),
})

export { FloatingBall }
export default FloatingBall
