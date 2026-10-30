import { registerPlugin } from '@capacitor/core'

export interface SiyiNativeBooleanResult {
  success?: boolean
  ok?: boolean
  error?: string
  available?: boolean
}

export interface AccessibilityStatus {
  running: boolean
}

export interface ScreenContentResult {
  available: boolean
  text: string
}

/**
 * 结构化读屏返回的单个节点。
 *
 * 坐标系：所有坐标均为**屏幕绝对像素**（已含状态栏/系统栏偏移），
 * 与原生 tap(x, y) 的入参一致，可直接传，无需再换算。
 * cx/cy 是节点中心点，是点击最省事的目标。
 */
export interface ScreenNode {
  text: string
  viewId: string
  className: string
  clickable: boolean
  editable?: boolean
  checkable?: boolean
  checked?: boolean
  scrollable?: boolean
  left: number
  top: number
  right: number
  bottom: number
  /** 中心点 X（屏幕绝对像素） */
  cx: number
  /** 中心点 Y（屏幕绝对像素） */
  cy: number
}

export interface ScreenNodesResult {
  available: boolean
  nodes: ScreenNode[]
  count: number
}

export interface BatteryStatus {
  ok: boolean
  error?: string
  result?: {
    level: number
    isCharging: boolean
    status: string
  }
}

export interface StorageInfo {
  ok: boolean
  error?: string
  result?: {
    totalBytes: number
    usedBytes: number
    availableBytes: number
    usedPercent: string
  }
}

export interface MemoryInfo {
  ok: boolean
  error?: string
  result?: {
    totalMem: number
    availMem: number
    lowMemory: boolean
    threshold: number
  }
}

export interface NetworkStatus {
  ok: boolean
  error?: string
  result?: {
    isConnected: boolean
    isWifi: boolean
    isCellular: boolean
    isEthernet: boolean
    transport: string
  }
}

export interface InstalledAppInfo {
  name: string
  packageName: string
}

export interface InstalledAppsResult {
  ok: boolean
  error?: string
  result?: {
    apps: InstalledAppInfo[]
    total: number
  }
}

export interface RootStatus {
  available: boolean
}

/** root 命令执行结果。blocked=true 表示被只读白名单拦下，并非命令本身失败。 */
export interface RootCommandResult {
  success: boolean
  available: boolean
  blocked?: boolean
  exitCode?: number
  stdout?: string
  stderr?: string
  error?: string
}

export interface RedmiNote {
  id: number
  title: string
  content: string
  folderName: string
  type: number
  createdDate: number
  modifiedDate: number
}

export interface RedmiNotesResult {
  rootAvailable: boolean
  dbFound: boolean
  noteCount: number
  notes: RedmiNote[]
  error?: string
}

export interface HttpFetchResult {
  ok: boolean
  status: number
  body: string
  contentType?: string
  bytes?: number
  truncated?: boolean
  error?: string
}

export interface SystemInsets {
  ok: boolean
  /** 系统导航栏高度（物理 px） */
  bottom: number
  /** 状态栏高度（物理 px） */
  top: number
  /** 屏幕密度，用于 px → CSS px 换算 */
  density?: number
  screenHeight?: number
  source?: string
  /** 原生 MainActivity 是否已给 WebView 容器加上系统栏 padding（是则 CSS 不再叠加） */
  handledByNative?: boolean
  error?: string
}

export interface SiyiNativePlugin {
  /** 读取系统栏 insets（edge-to-edge 下 App 需要据此避让系统导航键） */
  getInsets(): Promise<SystemInsets>

  /**
   * 原生 HTTP GET，绕开 WebView 的 CORS 限制。
   * 走系统网络栈，开启系统级 VPN 后自动经 VPN 出口。
   */
  httpFetch(options: {
    url: string
    timeout?: number
    headers?: Record<string, string>
  }): Promise<HttpFetchResult>

  isAccessibilityRunning(): Promise<AccessibilityStatus>
  openAccessibilitySettings(): Promise<SiyiNativeBooleanResult>
  readScreen(): Promise<ScreenContentResult>
  /** 结构化读屏：带屏幕绝对坐标的可操作节点，AI 可直接点 cx/cy */
  readScreenNodes(): Promise<ScreenNodesResult>
  /** 按文字点击（原生侧查找节点并点其中心），不受坐标换算影响 */
  tapNode(options: { text: string; exact?: boolean }): Promise<SiyiNativeBooleanResult>
  tap(options: { x: number; y: number }): Promise<SiyiNativeBooleanResult>
  swipe(options: { x1: number; y1: number; x2: number; y2: number; duration?: number }): Promise<SiyiNativeBooleanResult>
  navBack(): Promise<SiyiNativeBooleanResult>
  navHome(): Promise<SiyiNativeBooleanResult>
  navRecents(): Promise<SiyiNativeBooleanResult>
  launchApp(options: { packageName: string }): Promise<SiyiNativeBooleanResult>

  getBatteryStatus(): Promise<BatteryStatus>
  getStorageInfo(): Promise<StorageInfo>
  getMemoryInfo(): Promise<MemoryInfo>
  getNetworkStatus(): Promise<NetworkStatus>
  getInstalledApps(options?: { limit?: number }): Promise<InstalledAppsResult>

  isRootAvailable(): Promise<RootStatus>
  /**
   * 受控 root 命令执行（为「本地 AI 全权管理」预埋）。
   *
   * 未 root 时 available=false 并直接失败；未显式传 allowDangerous 时
   * 仅放行只读命令白名单。root 本质是无限权限，应用层无法真正沙箱，
   * 开关必须握在用户手里。
   */
  runRootCommand(options: {
    command: string
    allowDangerous?: boolean
    timeoutMs?: number
  }): Promise<RootCommandResult>
  readRedmiNotes(): Promise<RedmiNotesResult>

  /** 打开系统自带闹钟应用并预填时间（不保证所有厂商都支持）。 */
  openSystemAlarm(options: { hour: number; minute: number; message?: string }): Promise<SiyiNativeBooleanResult>
}

const SiyiNative = registerPlugin<SiyiNativePlugin>('SiyiNative')

export function withNativeTimeout<T>(promise: Promise<T>, ms: number = 5000, label: string = ''): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(label ? `${label} timeout after ${ms}ms` : `native call timeout`)), ms)
    ),
  ])
}

export default SiyiNative
