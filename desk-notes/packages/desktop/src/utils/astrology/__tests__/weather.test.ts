import { describe, test, expect } from 'vitest'
import { getWeatherEmoji, getWeatherDescription, clearWeatherCache, getCachedWeather } from '../weather'

describe('getWeatherEmoji', () => {
  test('returns emoji for known code', () => {
    expect(getWeatherEmoji('113')).toBe('☀️')
    expect(getWeatherEmoji('116')).toBe('⛅')
    expect(getWeatherEmoji('200')).toBe('⛈️')
  })

  test('returns fallback for unknown code', () => {
    expect(getWeatherEmoji('999')).toBe('🌤️')
    expect(getWeatherEmoji('')).toBe('🌤️')
  })
})

describe('getWeatherDescription', () => {
  test('returns chinese description for known code', () => {
    expect(getWeatherDescription('113')).toBe('晴')
    expect(getWeatherDescription('116')).toBe('多云')
    expect(getWeatherDescription('200')).toBe('雷阵雨')
  })

  test('returns "未知" for unknown code', () => {
    expect(getWeatherDescription('999')).toBe('未知')
    expect(getWeatherDescription('')).toBe('未知')
  })
})

describe('clearWeatherCache / getCachedWeather', () => {
  test('clearWeatherCache does not throw', () => {
    expect(() => clearWeatherCache()).not.toThrow()
  })

  test('getCachedWeather returns null after clear', () => {
    clearWeatherCache()
    expect(getCachedWeather()).toBeNull()
    expect(getCachedWeather('上海')).toBeNull()
  })
})
