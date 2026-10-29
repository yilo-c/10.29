/**
 * 天气桥 — 桌面 `weather.current` RPC 的封装 + 缓存。
 *
 * 桌面端的天气服务带有缓存策略（默认 30 分钟），这里再叠一层手机端本地缓存
 * 以减少网络抖动时的失败率。
 */
import { callRpc } from '../rpcClient'

const STORAGE_KEY = 'siyi_mobile_weather_cache_v1'
const TTL_MS = 15 * 60_000

export interface WeatherSnapshot {
  city?: string
  temperature?: number
  text?: string
  humidity?: number
  wind?: string
  icon?: string
  updatedAt: number
}

interface CacheBlob {
  data: WeatherSnapshot
}

function readCache(): WeatherSnapshot | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as CacheBlob
    return parsed?.data ?? null
  } catch {
    return null
  }
}

function writeCache(data: WeatherSnapshot): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ data } as CacheBlob)) } catch { /* ignore */ }
}

export function getCachedWeather(): WeatherSnapshot | null {
  return readCache()
}

export async function fetchCurrentWeather(opts?: { force?: boolean }): Promise<WeatherSnapshot | null> {
  const cached = readCache()
  if (!opts?.force && cached && Date.now() - cached.updatedAt < TTL_MS) return cached
  try {
    const res = await callRpc<unknown>('weather.current', {})
    if (!res || typeof res !== 'object') return cached
    const obj = res as Record<string, unknown>
    const snapshot: WeatherSnapshot = {
      city: typeof obj.city === 'string' ? obj.city : undefined,
      temperature: typeof obj.temperature === 'number' ? obj.temperature : undefined,
      text: typeof obj.text === 'string'
        ? obj.text
        : typeof obj.condition === 'string' ? obj.condition as string : undefined,
      humidity: typeof obj.humidity === 'number' ? obj.humidity : undefined,
      wind: typeof obj.wind === 'string' ? obj.wind : undefined,
      icon: typeof obj.icon === 'string' ? obj.icon : undefined,
      updatedAt: Date.now(),
    }
    writeCache(snapshot)
    return snapshot
  } catch {
    return cached
  }
}
