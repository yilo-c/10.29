/// <reference types="vite/client" />

interface ElectronAPI {
  minimize: () => void
  close: () => void
  createViewWindow: (data: { viewType: 'notes' | 'todo' | 'chat' | 'info' | 'floating' | 'knowledge-graph'; backgroundMode?: string }) => void
  createFloatingWindow: (data: Record<string, unknown>) => void
  deleteFloatingWindow: (noteId: string) => void
  hideFloatingWindows: () => void
  showFloatingWindows: () => void
  resizeWindow: (w: number, h: number) => void
  openFloatingChat: () => void
  hideFloatingChat: () => void
  sendVoiceDraft: (text: string) => void
  configurePet: (config: { windowWidth?: number; windowHeight?: number; hitArea?: { x: number; y: number; width: number; height: number } }) => void
  fullscreen: () => void
  alwaysOnTop: (on: boolean) => void
  dragStart: (offsetX: number, offsetY: number) => void
  dragEnd: () => void
  ignoreMouseEvents: (opts: { ignore: boolean }) => void
  showSaveDialog: (data: { filename: string; content: string; mimeType?: string }) => void
  showOpenDialog: (options?: Record<string, unknown>) => Promise<{ canceled: boolean; content?: string }>
  setAutoLaunch: (enable: boolean) => void
  getAutoLaunch: () => Promise<boolean>
  getPendingVoiceDraft: () => Promise<string | null>
  onTrayAction: (callback: (action: { type: string; noteId?: string }) => void) => void
  onGlobalShortcut: (callback: (data: { action: string; content?: string }) => void) => () => void
  onNoteReturned: (callback: (data: { id: string }) => void) => () => void
  onNoteSynced: (callback: (data: { id: string }) => void) => () => void
  onNoteDeleted: (callback: (data: { id: string }) => void) => () => void
  setResizable: (resizable: boolean) => void
  fileStore: {
    init: () => Promise<{ migrated: boolean }>
    readFile: (relativePath: string) => Promise<string | null>
    writeFile: (relativePath: string, content: string) => Promise<void>
    deleteFile: (relativePath: string) => Promise<void>
    readNote: (noteId: string) => Promise<Record<string, unknown> | null>
    writeNote: (noteId: string, noteData: Record<string, unknown>) => Promise<void>
    listNotes: () => Promise<string[]>
    noteExists: (noteId: string) => Promise<boolean>
  }
  updateReminders: (todos: Array<Record<string, unknown>>) => Promise<void>
  sendBackgroundMode: (mode: string) => void
  updateRecentNotes: (notes: Record<string, unknown>[]) => void
  updateBackendStatus: (connected: boolean) => void
  onClipboardChange: (callback: (data: { text: string }) => void) => () => void
  onClipboardPopupText: (callback: (data: { text: string }) => void) => () => void
  onVoiceDraft: (callback: (text: string) => void) => void
  onClipboardPopupResized: (callback: () => void) => () => void
  closeClipboardPopup: () => void
  resizeClipboardPopup: (opts: { width: number; height: number }) => void
  rpcCall: (method: string, params?: Record<string, unknown>) => Promise<{ success: boolean; data?: unknown; error?: string }>
  captureScreen: () => Promise<{ success: boolean; dataUrl?: string; width?: number; height?: number; error?: string }>
  ocr: {
    status: () => Promise<{ success: boolean; data?: Record<string, unknown>; error?: string }>
    extractBase64: (data: string) => Promise<{ success: boolean; data?: unknown; error?: string }>
  }
  transcribe: {
    status: () => Promise<{ success: boolean; data?: Record<string, unknown>; error?: string }>
    transcribeBase64: (data: string, format?: string) => Promise<{ success: boolean; data?: unknown; error?: string }>
  }
  onGlobalShortcutConflict: (callback: (data: { failures: Array<{ accel: string; label: string }> }) => void) => () => void
  onNativeThemeChanged: (callback: (data: { darkMode: boolean }) => void) => () => void
  onNoteFileChanged: (callback: (data: { noteId: string }) => void) => () => void
  imageStore: {
    save: (data: { dataUrl: string; name?: string }) => Promise<{ path: string | null }>
    read: (filename: string) => Promise<string | null>
    delete: (filename: string) => Promise<boolean>
    list: () => Promise<string[]>
  }
  readUsageDoc: () => Promise<string | null>

  update: {
    check: () => Promise<void>
    download: () => Promise<void>
    install: () => Promise<void>
    onChecking: (callback: () => void) => () => void
    onAvailable: (callback: (info: Record<string, unknown>) => void) => () => void
    onNotAvailable: (callback: () => void) => () => void
    onError: (callback: (msg: string) => void) => () => void
    onProgress: (callback: (progress: Record<string, unknown>) => void) => () => void
    onDownloaded: (callback: (info: Record<string, unknown>) => void) => () => void
  }
}

interface Window {
  electronAPI?: ElectronAPI
}

interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[]
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>
  prompt(): Promise<void>
}
