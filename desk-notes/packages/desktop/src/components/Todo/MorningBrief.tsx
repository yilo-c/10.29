import React, { useState, useEffect, useMemo } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { getHuangli, calculateBazi, getDailyGuidance, generateSummaryQuote } from '../../utils/astrology'
import { fetchWeather, getWeatherEmoji, type WeatherData } from '../../utils/astrology/weather'
import { getFengshuiAdvice } from '../../utils/astrology/fengshui'
import { getAIGuidance } from '../../utils/astrology/ai-guidance'
import { getLocalAIConfig } from '../../utils/aiConfig'

const MorningBrief: React.FC = () => {
  const { t } = useTranslation()
  const todos = useStore(s => s.todos)
  const userProfile = useStore(s => s.userProfile)
  const aiEnabled = useStore(s => s.aiEnabled)
  const activeCat = useStore(s => s.activeCat)

  const [collapsed, setCollapsed] = useState(false)
  const [weather, setWeather] = useState<WeatherData | null>(null)
  const [aiAdvice, setAiAdvice] = useState<string | null>(null)
  const [aiLoading, setAiLoading] = useState(false)

  const today = useMemo(() => new Date(), [])

  // 黄历
  const huangli = useMemo(() => getHuangli(today), [today])

  // 八字
  const baziProfile = useMemo(() => {
    if (!userProfile?.birthDate) return null
    return calculateBazi(
      new Date(userProfile.birthDate),
      userProfile.birthHour ?? null,
      userProfile.gender ?? null,
    )
  }, [userProfile?.birthDate, userProfile?.birthHour, userProfile?.gender])

  // 每日指导
  const dailyGuidance = useMemo(() => {
    const list = activeCat === 'all' ? todos : todos.filter(t => t.category === activeCat)
    return getDailyGuidance(today, baziProfile, list)
  }, [today, baziProfile, todos, activeCat])

  // 天气：优先 GPS 定位 → 城市设置 → IP 自动
  useEffect(() => {
    let cancelled = false

    const doFetch = (location?: string) => {
      fetchWeather(location).then(w => { if (!cancelled) setWeather(w) }).catch((err: unknown) => {
        console.error('[weather] fetch failed:', err instanceof Error ? err.message : err)
      })
    }

    if ('geolocation' in navigator) {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          if (!cancelled) doFetch(`${pos.coords.latitude},${pos.coords.longitude}`)
        },
        () => {
          // GPS 失败 → 用城市设置
          if (!cancelled) doFetch(userProfile?.city || undefined)
        },
        { timeout: 5000, enableHighAccuracy: false },
      )
    } else {
      doFetch(userProfile?.city || undefined)
    }

    return () => { cancelled = true }
  }, [userProfile?.city])

  // AI 建议（仅在生日信息已填时）
  useEffect(() => {
    if (!aiEnabled || !huangli || !baziProfile) return
    let cancelled = false
    setAiLoading(true)
    getAIGuidance(today, huangli, baziProfile, todos.filter(t => !t.done), (chunk) => {
      if (!cancelled) setAiAdvice(chunk)
    }, getLocalAIConfig(), weather).then(result => {
      if (!cancelled) setAiAdvice(result.text)
    }).catch((err: unknown) => {
      console.error('[astro] AI guidance failed:', err instanceof Error ? err.message : err)
    }).finally(() => {
      if (!cancelled) setAiLoading(false)
    })
    return () => { cancelled = true }
  }, [today, huangli?.ganzhi?.day, weather?.temp, todos, baziProfile, aiEnabled])

  const luckyDir = dailyGuidance?.advice?.luckyDirection ?? null
  const advice = dailyGuidance?.advice

  // 风水建议（基于今日吉方）
  const fengshuiTip = useMemo(() => {
    if (!luckyDir || !advice) return null
    return getFengshuiAdvice('life', luckyDir)
  }, [luckyDir, advice])

  const strength = baziProfile?.strength
  const missingElement = baziProfile?.missingElement

  // 天气 emoji
  const weatherEmoji = weather ? getWeatherEmoji(weather.iconCode) : null

  return (
    <div
      className="mb-2 rounded-lg border"
      style={{
        borderColor: 'var(--border-color, rgba(255,255,255,0.06))',
        background: 'rgba(255,255,255,0.02)',
      }}
    >
      <button
        onClick={() => setCollapsed(v => !v)}
        className="flex items-center gap-1.5 w-full text-left px-2.5 py-1.5"
      >
        <Icon icon={`fa-solid fa-chevron-${collapsed ? 'right' : 'down'} text-xs transition-transform`}
          style={{ color: 'var(--text-muted)' }} />
        <Icon icon="fa-solid fa-sun text-xs" style={{ color: 'var(--text-muted)' }} />
        <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
          {t('calendar.morningBrief')}
        </span>
        <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
          {today.getMonth() + 1}/{today.getDate()}
        </span>
      </button>

      {!collapsed && (
        <div className="px-3 pb-3 space-y-2.5 text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          {/* 行1：农历 + 天气 */}
          <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-dim)' }}>
            {huangli && (
              <span>
                {t('morningBrief.lunarFmt', {
                  lunarMonth: huangli.lunar.month,
                  lunarDay: huangli.lunar.day,
                  gzYear: huangli.ganzhi.year,
                  gzMonth: huangli.ganzhi.month,
                  gzDay: huangli.ganzhi.day,
                })}
                {huangli.shengxiao.year && t('morningBrief.shengxiaoYearFmt', { animal: huangli.shengxiao.year })}
              </span>
            )}
            {weather && (
              <span className="flex items-center gap-0.5">
                <span>{weatherEmoji}</span>
                <span>{userProfile?.city || weather.city}</span>
                <span>{t('morningBrief.weatherFmt', { temp: weather.temp, condition: weather.condition })}</span>
              </span>
            )}
          </div>

          {/* 行2：宜忌 + 冲煞 */}
          {huangli && (
            <div className="flex items-center gap-2 text-xs flex-wrap">
              {huangli.yiJi.yi.length > 0 && (
                <span><span style={{ color: 'var(--success)' }}>{t('morningBrief.yi')}</span> {huangli.yiJi.yi.slice(0, 4).join(' ')}</span>
              )}
              {huangli.yiJi.ji.length > 0 && (
                <span><span style={{ color: 'var(--danger)' }}>{t('morningBrief.ji')}</span> {huangli.yiJi.ji.slice(0, 4).join(' ')}</span>
              )}
              {huangli.chongSha && (
                <span style={{ color: 'var(--text-dim)' }}>
                  {t('morningBrief.chongShaFmt', { chong: huangli.chongSha.chong, sha: huangli.chongSha.sha })}
                </span>
              )}
            </div>
          )}

          {/* 行3：八字 + 穿衣建议（含幸运色信息） */}
          {baziProfile && (
            <div className="flex items-center gap-2 text-xs flex-wrap" style={{ color: 'var(--text-dim)' }}>
              <span>{baziProfile.fourPillars.year} | {baziProfile.fourPillars.month} | {baziProfile.fourPillars.day}</span>
              {baziProfile.fourPillars.hour && <span>| {baziProfile.fourPillars.hour}</span>}
              {strength && <span>{t('morningBrief.strengthFmt', { strength })}</span>}
              {missingElement && <span>{t('morningBrief.missingFmt', { element: missingElement })}</span>}
              {advice?.clothingAdvice && (
                <span>· {advice.clothingAdvice}</span>
              )}
            </div>
          )}

          {/* 行4：吉方 + 风水催旺 */}
          {luckyDir && fengshuiTip && (
            <div className="flex items-center gap-2 text-xs flex-wrap">
              <span className="flex items-center gap-0.5" style={{ color: '#c4b5fd' }}>
                <Icon icon="fa-solid fa-location-arrow text-[7px]" />
                {t('morningBrief.luckyDirFmt', { dir: luckyDir })}
              </span>
              <span style={{ color: 'var(--text-dim)' }}>{fengshuiTip.enhancement}</span>
            </div>
          )}

          {/* 行5：每日签语 */}
          {dailyGuidance && (() => {
            const quote = generateSummaryQuote(dailyGuidance, today.toDateString())
            return (
              <div className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                <Icon icon="fa-solid fa-quote-left text-[7px] mr-0.5" style={{ opacity: 0.4 }} />
                {quote}
              </div>
            )
          })()}

          {/* 行6：AI 建议 */}
          {aiLoading && (
            <div className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
              <Icon icon="fa-solid fa-spinner animate-spin" />
              {t('morningBrief.aiAnalyzing')}
            </div>
          )}
          {aiAdvice && (
            <div
              className="rounded-lg px-3 py-2 text-xs leading-relaxed"
              style={{ background: 'rgba(96,165,250,0.08)', color: 'var(--text-muted)' }}
            >
              <span className="block text-2xs font-medium mb-0.5" style={{ color: 'var(--brand)' }}>
                <Icon icon="fa-solid fa-robot mr-0.5" />{t('morningBrief.aiAdvice')}
              </span>
              {aiAdvice}
            </div>
          )}

          {/* 行7：待办统计 */}
          {dailyGuidance && dailyGuidance.todoStats.total > 0 && (
            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-dim)' }}>
              <span>
                {t('morningBrief.todoStatsFmt', {
                  total: dailyGuidance.todoStats.total,
                  done: dailyGuidance.todoStats.done,
                  pending: dailyGuidance.todoStats.pending,
                })}
              </span>
              {dailyGuidance.todoStats.overdue > 0 && (
                <span style={{ color: 'var(--danger)' }}>
                  {t('morningBrief.overdueFmt', { count: dailyGuidance.todoStats.overdue })}
                </span>
              )}
              {dailyGuidance.todoStats.highPriority > 0 && (
                <span style={{ color: 'var(--warning)' }}>
                  {t('morningBrief.highPriorityFmt', { count: dailyGuidance.todoStats.highPriority })}
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default MorningBrief
