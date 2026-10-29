import { useState, useEffect, useMemo } from 'react'
import { useStore, fetchWeather, getWeatherEmoji, getCachedWeather, calculateBazi } from '@desk-notes/shared'
import type { WeatherData, BaziProfile } from '@desk-notes/shared'
import { getTcmAdvice } from '../../../lib/tcmEngine'
import { swallowError } from '../../../lib/swallowError'

export default function WeatherCard() {
  const userProfile = useStore(s => s.userProfile)
  const city = userProfile?.city
  const [weather, setWeather] = useState<WeatherData | null>(() => getCachedWeather(city))
  const [loading, setLoading] = useState(!weather)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(false)
    fetchWeather(city)
      .then((data: WeatherData) => { if (!cancelled) { setWeather(data); setLoading(false) } })
      .catch(() => { if (!cancelled) { setError(true); setLoading(false) } })
    return () => { cancelled = true }
  }, [city])

  const tcmTip = useMemo(() => {
    if (!weather) return ''
    const date = new Date()

    let bazi: BaziProfile | null = null
    if (userProfile?.birthDate) {
      try {
        bazi = calculateBazi(new Date(userProfile.birthDate), userProfile.birthHour ?? null, userProfile.gender)
      } catch (e) { /* ignore */ swallowError('WeatherCard:31', e) }
    }

    const advice = getTcmAdvice(
      { temp: weather.temp, feelsLike: weather.feelsLike, humidity: weather.humidity, condition: weather.condition },
      date,
      bazi,
    )
    return advice[0]?.summary || ''
  }, [weather, userProfile])

  return (
    <div style={{
      borderRadius: 'var(--radius-md)',
      background: 'var(--bg-secondary)',
      padding: '12px 14px',
      marginBottom: 10,
    }}>
      <div style={{
        fontSize: 'var(--text-headline)',
        fontWeight: 600,
        color: 'var(--text-primary)',
        marginBottom: 6,
      }}>
        天气
      </div>

      {loading && (
        <div style={{
          height: 60,
          borderRadius: 'var(--radius-sm)',
          background: 'var(--bg-tertiary)',
          opacity: 0.5,
        }} />
      )}

      {error && !weather && (
        <div style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
          {city
            ? '天气数据暂不可用'
            : '在 设置 中填写所在城市以查看天气'}
        </div>
      )}

      {weather && (
        <div style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ fontSize: 28 }}>{getWeatherEmoji(weather.iconCode)}</span>
            <div>
              <span style={{ fontWeight: 600, fontSize: 'var(--text-body)', color: 'var(--text-primary)' }}>
                {weather.temp}°C
              </span>
              <span style={{ marginLeft: 8 }}>{weather.condition}</span>
              {city && <span style={{ marginLeft: 6, color: 'var(--text-tertiary)' }}>· {city}</span>}
            </div>
          </div>
          <div style={{
            display: 'flex', gap: 12,
            fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)',
          }}>
            <span>体感 {weather.feelsLike}°C</span>
            <span>湿度 {weather.humidity}%</span>
            <span>风 {weather.windSpeed}km/h</span>
          </div>

          {tcmTip && (
            <div style={{
              marginTop: 8,
              padding: '8px 10px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-tertiary)',
              fontSize: 'var(--text-caption-1)',
              color: 'var(--tone-smooth)',
              lineHeight: 1.5,
            }}>
              {tcmTip}
            </div>
          )}
        </div>
      )}

      {error && weather && (
        <div style={{ fontSize: 10, color: 'var(--text-disabled)', marginTop: 4 }}>
          数据可能延迟
        </div>
      )}
    </div>
  )
}
