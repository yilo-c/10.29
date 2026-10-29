/** 全局 window 扩展类型声明 — 替代项目中散落的 (window as any)。
 *
 * 覆盖范围:
 *  - __useStore       Zustand store 引用 (App.tsx 挂载，E2E fixtures / FloatingBubble 消费)
 *  - __quickCaptureSubmit / __floatingBubble  快速捕获 & 浮动气泡桥
 *  - electronAPI      contextBridge 暴露的 Electron API (preload.cjs)
 *  - bridge           遗留 IPC 桥 (AIActivityPanel / FloatingBubbleApp 消费)
 */

import type { StoreApi } from 'zustand'

/** Zustand 根 store — App.tsx 在挂载时写入 window.__useStore */
interface AppStore {
  getState: () => {
    activeModule: string
    bootPhase: string
    panel: { x: number; y: number }
    setActiveModule?: (m: string) => void
    floatingNotes?: unknown[]
    todos?: Array<{ done: boolean; id: unknown }>
    preferredModelName?: string
    conversations?: unknown[]
    activeConversationId?: string
    navCollapsed?: boolean
    [key: string]: unknown
  }
  setState: (partial: Record<string, unknown>) => void
  subscribe: (listener: (state: unknown) => void) => () => void
}

/** Electron contextBridge API (preload.cjs) */
interface ElectronAPI {
  minimize?: () => void
  close?: () => void
  createViewWindow?: (data: Record<string, unknown>) => void
  createFloatingWindow?: (opts: Record<string, unknown>) => void
  deleteFloatingWindow?: (noteId: unknown) => void
  hideFloatingWindows?: () => void
  showFloatingWindows?: () => void
  openFloatingChat?: () => void
  hideFloatingChat?: () => void
  resizeWindow?: (w: number, h: number) => void
  fullscreen?: () => void
  alwaysOnTop?: (on: boolean) => void
  dragStart?: (offsetX: number, offsetY: number) => void
  dragEnd?: () => void
  ignoreMouseEvents?: (opts: Record<string, unknown>) => void
  setResizable?: (resizable: boolean) => void
  showSaveDialog?: (data: Record<string, unknown>) => void
  setAutoLaunch?: (enable: boolean) => void
  updateBackendStatus?: (connected: boolean) => void
  sendBackgroundMode?: (mode: string) => void
  updateRecentNotes?: (notes: unknown[]) => void
  closeClipboardPopup?: () => void

  // invoke/handle 方法
  showOpenDialog?: (options: Record<string, unknown>) => Promise<unknown>
  getAutoLaunch?: () => Promise<boolean>
  fileStore?: {
    init: () => Promise<void>
    readFile: (path: string) => Promise<string | null>
    writeFile: (path: string, content: string) => Promise<void>
    deleteFile: (path: string) => Promise<void>
    readNote: (noteId: string) => Promise<string | null>
    writeNote: (noteId: string, data: unknown) => Promise<void>
    listNotes: () => Promise<string[]>
    noteExists: (noteId: string) => Promise<boolean>
  }
  imageStore?: {
    save: (data: Record<string, unknown>) => Promise<string>
    read: (filename: string) => Promise<ArrayBuffer | null>
    delete: (filename: string) => Promise<void>
    list: () => Promise<string[]>
  }
  updateReminders?: (todos: unknown[]) => Promise<void>
  readUsageDoc?: () => Promise<string>
  update?: {
    check: () => Promise<unknown>
    download: () => Promise<unknown>
    install: () => Promise<unknown>
    onChecking: (cb: () => void) => void
    onAvailable: (cb: (info: unknown) => void) => void
    onNotAvailable: (cb: (info: unknown) => void) => void
    onError: (cb: (err: unknown) => void) => void
    onProgress: (cb: (progress: unknown) => void) => void
    onDownloaded: (cb: (info: unknown) => void) => void
  }

  // 事件回调注册
  onTrayAction?: (callback: (action: string) => void) => void
  onGlobalShortcut?: (callback: (action: string) => void) => void
  onNoteReturned?: (callback: (data: Record<string, unknown>) => void) => void
  onNoteSynced?: (callback: (data: Record<string, unknown>) => void) => void
  onNativeThemeChanged?: (callback: (info: { darkMode: boolean }) => void) => void
  onNoteFileChanged?: (callback: (data: Record<string, unknown>) => void) => void
  onClipboardChange?: (callback: (text: string) => void) => void
  onClipboardPopupText?: (callback: (text: string) => void) => void
}

/** 遗留 IPC 桥（AIActivityPanel / FloatingBubbleApp 消费） */
interface LegacyBridge {
  call_service?: (service: string, method: string, ...args: unknown[]) => Promise<unknown>
  send?: (channel: string, ...args: unknown[]) => void
  on?: (channel: string, callback: (...args: unknown[]) => void) => void
  removeAllListeners?: (channel: string) => void
  [key: string]: unknown
}

declare global {
  interface Window {
    /** Zustand store — App.tsx 挂载 */
    __useStore?: StoreApi<AppStore>

    /** 快速捕获表单提交回调 */
    __quickCaptureSubmit?: (data: Record<string, unknown>) => void

    /** 浮动气泡桥引用 */
    __floatingBubble?: any

    /** Electron contextBridge API */
    electronAPI?: ElectronAPI

    /** 遗留 IPC 桥 */
    bridge?: any
  }
}

export {}
