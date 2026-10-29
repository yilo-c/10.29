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

  isAccessibilityRunning(): Promise<AccessibilityStatus>
  openAccessibilitySettings(): Promise<SiyiNativeBooleanResult>
  readScreen(): Promise<ScreenContentResult>
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
