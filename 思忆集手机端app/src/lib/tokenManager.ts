import { isSensitiveKey, secureGet, secureSet } from './secureStore'

const KEY_API_TOKEN = 'siyi_api_token'
const KEY_DEVICE_ID = 'siyi_device_id'
const KEY_USER_ID = 'siyi_user_id'
const KEY_BASE_URL = 'siyi_base_url'
const KEY_IS_PAIRED = 'siyi_is_paired'
const KEY_DEVICE_NAME = 'siyi_device_name'
const KEY_CLOUD_AI_ENABLED = 'siyi_cloud_ai_enabled'
const KEY_PROMPT_BEFORE_CLOUD = 'siyi_prompt_before_cloud'
const KEY_PREFER_DESKTOP_AI = 'siyi_prefer_desktop_ai'
const KEY_CLOUD_API_KEY = 'siyi_cloud_api_key'
const KEY_CLOUD_API_BASE_URL = 'siyi_cloud_api_base_url'
const KEY_CLOUD_MODEL = 'siyi_cloud_model'
const KEY_BACKEND_PORT = 'siyi_backend_port'

const DEFAULT_BACKEND_PORT = 18765
const DEFAULT_CLOUD_API_BASE_URL = 'https://api.deepseek.com'

// 注意：默认 baseUrl 不能写死具体局域网 IP。
// 此前是 'http://192.168.1.100:8000' —— 端口 8000 与本文件的
// DEFAULT_BACKEND_PORT=18765 自相矛盾（8000 是早期模板遗留值），
// 且该 IP 对任何用户都不存在。未配对时返回空串，让调用方走
// 「未配置 → 提示配对」的正常分支，而不是静默去连一个错误地址。
const DEFAULT_BASE_URL = ''

/**
 * Read a value, routing sensitive keys through Capacitor Preferences
 * (Android EncryptedSharedPreferences / iOS Keychain). Non-sensitive keys
 * remain in localStorage to keep this API synchronous.
 *
 * Sensitive keys require initSecureStore() to have completed at bootstrap;
 * before that, getters return null. See secureStore.ts.
 */
function get(key: string): string | null {
  if (isSensitiveKey(key)) return secureGet(key)
  return localStorage.getItem(key)
}

function set(key: string, value: string | null): void {
  if (isSensitiveKey(key)) {
    secureSet(key, value)
    return
  }
  if (value === null) {
    localStorage.removeItem(key)
  } else {
    localStorage.setItem(key, value)
  }
}

function getBool(key: string, fallback: boolean): boolean {
  const v = get(key)
  if (v === null) return fallback
  return v === 'true'
}

function setBool(key: string, value: boolean): void {
  set(key, value ? 'true' : 'false')
}

export const tokenManager = {
  get apiToken(): string | null { return get(KEY_API_TOKEN) },
  set apiToken(v: string | null) { set(KEY_API_TOKEN, v) },

  get deviceId(): string | null { return get(KEY_DEVICE_ID) },
  set deviceId(v: string | null) { set(KEY_DEVICE_ID, v) },

  get userId(): string | null { return get(KEY_USER_ID) },
  set userId(v: string | null) { set(KEY_USER_ID, v) },

  get baseUrl(): string { return get(KEY_BASE_URL) ?? DEFAULT_BASE_URL },
  set baseUrl(v: string | null) { set(KEY_BASE_URL, v) },

  get isPaired(): boolean { return getBool(KEY_IS_PAIRED, false) },
  set isPaired(v: boolean) { setBool(KEY_IS_PAIRED, v) },

  get deviceName(): string | null { return get(KEY_DEVICE_NAME) },
  set deviceName(v: string | null) { set(KEY_DEVICE_NAME, v) },

  get cloudAiEnabled(): boolean { return getBool(KEY_CLOUD_AI_ENABLED, false) },
  set cloudAiEnabled(v: boolean) { setBool(KEY_CLOUD_AI_ENABLED, v) },

  get promptBeforeCloud(): boolean { return getBool(KEY_PROMPT_BEFORE_CLOUD, true) },
  set promptBeforeCloud(v: boolean) { setBool(KEY_PROMPT_BEFORE_CLOUD, v) },

  get preferDesktopAi(): boolean { return getBool(KEY_PREFER_DESKTOP_AI, true) },
  set preferDesktopAi(v: boolean) { setBool(KEY_PREFER_DESKTOP_AI, v) },

  get cloudApiKey(): string { return get(KEY_CLOUD_API_KEY) ?? '' },
  set cloudApiKey(v: string | null) { set(KEY_CLOUD_API_KEY, v) },

  get cloudApiBaseUrl(): string { return get(KEY_CLOUD_API_BASE_URL) ?? DEFAULT_CLOUD_API_BASE_URL },
  set cloudApiBaseUrl(v: string | null) { set(KEY_CLOUD_API_BASE_URL, v) },

  /**
   * 云端模型名 / Cloud model id.
   *
   * 为什么需要用户显式配置：此前模型完全由 API 地址「猜」出来
   * （inferModel：含 deepseek 就返回 deepseek-v4-flash），
   * 用户既看不到也不能改。一旦服务商改名或用户要用别的模型，
   * 界面无从下手，只能报一个看不懂的 400。
   * 现在：用户留空时仍回落到地址推断（保持旧行为），
   * 填了就以填的为准。
   */
  get cloudModel(): string { return get(KEY_CLOUD_MODEL) ?? '' },
  set cloudModel(v: string | null) { set(KEY_CLOUD_MODEL, v) },

  get backendPort(): number {
    const v = get(KEY_BACKEND_PORT)
    return v ? parseInt(v, 10) : DEFAULT_BACKEND_PORT
  },
  set backendPort(v: number | null) { set(KEY_BACKEND_PORT, v !== null ? String(v) : null) },

  clearAll(): void {
    // Sensitive keys go through set(null), which routes via secureStore.
    set(KEY_API_TOKEN, null)
    set(KEY_CLOUD_API_KEY, null)
    // Non-sensitive prefs stay in localStorage.
    localStorage.removeItem(KEY_DEVICE_ID)
    localStorage.removeItem(KEY_USER_ID)
    localStorage.removeItem(KEY_BASE_URL)
    localStorage.removeItem(KEY_IS_PAIRED)
    localStorage.removeItem(KEY_DEVICE_NAME)
    localStorage.removeItem(KEY_CLOUD_AI_ENABLED)
    localStorage.removeItem(KEY_PROMPT_BEFORE_CLOUD)
    localStorage.removeItem(KEY_PREFER_DESKTOP_AI)
    localStorage.removeItem(KEY_CLOUD_API_BASE_URL)
    localStorage.removeItem(KEY_BACKEND_PORT)
  },
}
