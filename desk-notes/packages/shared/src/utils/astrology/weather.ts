/**
 * 天气工具 — 通过 wttr.in 获取实时天气（免费，无需 API Key）
 */

export interface WeatherData {
  /** 城市名 */
  city: string
  /** 天气状况（如"晴"、"多云"、"雨"） */
  condition: string
  /** 摄氏温度 */
  temp: number
  /** 体感温度 */
  feelsLike: number
  /** 湿度百分比 */
  humidity: number
  /** 风速 km/h */
  windSpeed: number
  /** 天气图标代码（用于 emoji 映射） */
  iconCode: string
  /** 数据获取时间戳（ms） */
  fetchedAt: number
}

const weatherCache = new Map<string, WeatherData>()
const STORAGE_KEY = 'desk_notes_weather_cache'

function persistCache(): void {
  const obj: Record<string, WeatherData> = {}
  weatherCache.forEach((v, k) => { obj[k] = v })
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(obj)) } catch { /* storage full */ }
}

function loadPersistedCache(): void {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return
    const obj = JSON.parse(raw) as Record<string, WeatherData>
    for (const [k, v] of Object.entries(obj)) {
      weatherCache.set(k, v)
    }
  } catch { /* corrupted data */ }
}

// Load persisted cache on module init
loadPersistedCache()

/** wttr.in 天气代码 → 中文描述 */
const WEATHER_CODES: Record<string, string> = {
  '113': '晴', '116': '多云', '119': '阴',
  '122': '阴', '143': '雾', '176': '阵雨',
  '179': '小雪', '182': '雨雪', '185': '冻雨',
  '200': '雷阵雨', '227': '小雪', '230': '大雪',
  '248': '雾', '260': '浓雾', '263': '小雨',
  '266': '小雨', '281': '冻雨', '284': '冻雨',
  '293': '小雨', '296': '小雨', '299': '中雨',
  '302': '中雨', '305': '大雨', '308': '大雨',
  '311': '冻雨', '314': '冻雨', '317': '雨雪',
  '320': '雨雪', '323': '小雪', '326': '小雪',
  '329': '大雪', '332': '大雪', '335': '大雪',
  '338': '大雪', '350': '冰雹', '353': '阵雨',
  '356': '大阵雨', '359': '暴雨', '362': '雨夹雪',
  '365': '雨夹雪', '368': '阵雪', '371': '大阵雪',
  '374': '冰雹', '377': '冰雹', '386': '雷阵雨',
  '389': '雷阵雨', '392': '雷阵雪', '395': '雷阵雪',
}

/** 天气代码 → emoji */
const WEATHER_EMOJI: Record<string, string> = {
  '113': '☀️', '116': '⛅', '119': '☁️',
  '122': '☁️', '143': '🌫️', '176': '🌦️',
  '179': '🌨️', '182': '🌧️', '185': '🌧️',
  '200': '⛈️', '227': '🌨️', '230': '❄️',
  '248': '🌫️', '260': '🌫️', '263': '🌦️',
  '266': '🌦️', '281': '🌧️', '284': '🌧️',
  '293': '🌦️', '296': '🌦️', '299': '🌧️',
  '302': '🌧️', '305': '🌧️', '308': '🌧️',
  '311': '🌧️', '314': '🌧️', '317': '🌧️',
  '320': '🌧️', '323': '🌨️', '326': '🌨️',
  '329': '❄️', '332': '❄️', '335': '❄️',
  '338': '❄️', '350': '🌨️', '353': '🌦️',
  '356': '🌧️', '359': '🌧️', '362': '🌧️',
  '365': '🌧️', '368': '🌨️', '371': '❄️',
  '374': '🌨️', '377': '🌨️', '386': '⛈️',
  '389': '⛈️', '392': '⛈️', '395': '⛈️',
}

export function getWeatherEmoji(code: string): string {
  return WEATHER_EMOJI[code] || '🌤️'
}

export function getWeatherDescription(code: string): string {
  return WEATHER_CODES[code] || '未知'
}

/**
 * 获取指定城市的实时天气
 * @param city 城市名（中文或英文），为空则自动检测
 */
export async function fetchWeather(city?: string): Promise<WeatherData> {
  const location = city || ''
  const cacheKey = location || '__auto__'
  const now = Date.now()

  // 内存缓存 5 分钟内有效，直接用
  const memCached = weatherCache.get(cacheKey)
  if (memCached && (now - memCached.fetchedAt < 5 * 60 * 1000)) {
    return memCached
  }

  const url = location
    ? `https://wttr.in/${encodeURIComponent(location)}?format=j1&lang=zh`
    : 'https://wttr.in?format=j1&lang=zh'

  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5000) })
    if (!res.ok) throw new Error(`天气 API ${res.status}`)

    const data = await res.json()
    const current = data.current_condition?.[0]
    const nearestArea = data.nearest_area?.[0]?.areaName?.[0]?.value || location || '未知'

    if (!current) throw new Error('无法获取天气数据')

    const weather: WeatherData = {
      city: nearestArea,
      condition: getWeatherDescription(current.weatherCode),
      temp: Math.round(Number(current.temp_C) || 0),
      feelsLike: Math.round(Number(current.FeelsLikeC) || 0),
      humidity: Number(current.humidity) || 0,
      windSpeed: Math.round(Number(current.windspeedKmph) || 0),
      iconCode: current.weatherCode,
      fetchedAt: now,
    }

    weatherCache.set(cacheKey, weather)
    persistCache()
    return weather
  } catch (err: unknown) {
    // 网络失败 → 尝试持久化缓存
    const persisted = weatherCache.get(cacheKey)
    if (persisted) return persisted
    throw err
  }
}

/** 读取缓存的天气数据（不触发网络请求），无缓存时返回 null */
export function getCachedWeather(city?: string): WeatherData | null {
  const cacheKey = (city || '') || '__auto__'
  return weatherCache.get(cacheKey) ?? null
}

/** 清空天气缓存 */
export function clearWeatherCache(): void {
  weatherCache.clear()
}
