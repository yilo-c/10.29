import { Preferences } from '@capacitor/preferences'
import type { StateStorage } from 'zustand/middleware'

/**
 * P0-8 债（06-18 假✅）V2.1 批次4 真正接线：手机端 persist 存储适配器。
 *
 * 历史：手机端 main.tsx 只调了 setPlatformMobile()，从未调用 setPlatformStorage()，
 * 导致 zustand persist 全部写入 WebView localStorage —— PIN hash、配对状态等
 * 明文落盘（/data/data/com.siyi.notepad/app_webview/，root/ADB 可拉取）。
 *
 * 本适配器把 persist 存储切换到 Capacitor Preferences（Android
 * EncryptedSharedPreferences / iOS Keychain），并在首次 getItem 时把既有
 * localStorage 旧数据透明迁移进 Preferences。
 */
export const capacitorPlatformStorage: StateStorage = {
  getItem: async (name) => {
    try {
      const { value } = await Preferences.get({ key: name })
      if (value !== null && value !== undefined) return value
    } catch {
      /* Preferences 插件缺失（web dev）→ 回退 localStorage */
    }
    // 迁移：localStorage 旧数据 → Preferences（首次读取时搬迁）
    try {
      if (typeof localStorage === 'undefined') return null
      const legacy = localStorage.getItem(name)
      if (legacy !== null) {
        try {
          await Preferences.set({ key: name, value: legacy })
        } catch {
          /* web fallback：留在 localStorage */
        }
        return legacy
      }
    } catch {
      /* localStorage unavailable */
    }
    return null
  },
  setItem: async (name, value) => {
    try {
      await Preferences.set({ key: name, value })
    } catch {
      /* Preferences 插件缺失（web dev）→ 回退 localStorage */
      try {
        if (typeof localStorage !== 'undefined') localStorage.setItem(name, value)
      } catch {
        /* ignore */
      }
    }
  },
  removeItem: async (name) => {
    try {
      await Preferences.remove({ key: name })
    } catch {
      /* ignore */
    }
    try {
      if (typeof localStorage !== 'undefined') localStorage.removeItem(name)
    } catch {
      /* ignore */
    }
  },
}
