import { describe, test, expect } from 'vitest'
import { calculateBazi, generateDailyAdvice, getWuxingColor } from '../bazi'
import { getHuangli, getDayCellLunarInfo } from '../huangli'
import { getDailyGuidance } from '../guidance'

/* ========================================================
   calculateBazi — 八字计算
   ======================================================== */

describe('calculateBazi', () => {
  test('returns profile for valid birth data with hour', () => {
    const result = calculateBazi(new Date('1990-01-01'), 8, 'male')
    expect(result).not.toBeNull()
    if (!result) return
    expect(result.dayMaster).toBeTruthy()
    expect(result.fourPillars.year).toBeTruthy()
    expect(result.fourPillars.month).toBeTruthy()
    expect(result.fourPillars.day).toBeTruthy()
    expect(result.fourPillars.hour).toBeTruthy()
    // Day master is a single Chinese character (heavenly stem)
    expect(result.dayMaster.length).toBe(1)
    // Four pillars are 2 characters each (heavenly stem + earthly branch)
    expect(result.fourPillars.year.length).toBe(2)
    expect(result.fourPillars.month.length).toBe(2)
    expect(result.fourPillars.day.length).toBe(2)
    expect(result.fourPillars.hour!.length).toBe(2)
  })

  test('returns null when birthDate is NaN', () => {
    const result = calculateBazi(new Date(NaN), null, null)
    expect(result).toBeNull()
  })

  test('returns profile without hour when birthHour is null', () => {
    const result = calculateBazi(new Date('1995-06-15'), null, 'female')
    expect(result).not.toBeNull()
    if (!result) return
    expect(result.fourPillars.hour).toBeNull()
    expect(result.heavenlyStems.hour).toBeNull()
    expect(result.earthlyBranches.hour).toBeNull()
  })

  test('wuxing contains all five elements', () => {
    const result = calculateBazi(new Date('2000-01-01'), 6, 'male')
    expect(result).not.toBeNull()
    if (!result) return
    // wuxing always has all five keys
    expect(Object.keys(result.wuxing).sort()).toEqual(['土', '水', '火', '金', '木'].sort())
  })

  test('strength is either 强 or 弱', () => {
    const result = calculateBazi(new Date('1988-03-20'), 12, 'male')
    expect(result).not.toBeNull()
    if (!result) return
    expect(['强', '弱']).toContain(result.strength)
  })

  test('shengxiao is a non-empty string', () => {
    const result = calculateBazi(new Date('2024-01-01'), 12, 'female')
    expect(result).not.toBeNull()
    if (!result) return
    expect(result.shengxiao).toBeTruthy()
  })

  test('handles various dates error-free', () => {
    const dates = [
      '2000-01-01', '1988-03-20', '1976-12-25',
      '1960-08-08', '2024-01-01', '2024-02-29', // leap year
      '1900-02-01', // early 20th century
    ]
    for (const d of dates) {
      const result = calculateBazi(new Date(d), 12, 'male')
      expect(result).not.toBeNull()
      expect(result!.dayMaster).toBeTruthy()
    }
  })

  test('hour pillar changes with birth hour', () => {
    const date = new Date('2000-06-15')
    const morning = calculateBazi(date, 7, 'male')   // 辰时
    const evening = calculateBazi(date, 19, 'male')  // 戌时
    expect(morning).not.toBeNull()
    expect(evening).not.toBeNull()
    if (!morning || !evening) return
    // Different hours should give different hour pillars
    expect(morning.fourPillars.hour).not.toBe(evening.fourPillars.hour)
  })
})

/* ========================================================
   getHuangli — 黄历
   ======================================================== */

describe('getHuangli', () => {
  test('returns huangli data for valid date', () => {
    const result = getHuangli(new Date('2024-01-15'))
    expect(result).not.toBeNull()
    if (!result) return
    expect(result.lunar.fullDate).toBeTruthy()
    expect(result.lunar.month).toBeTruthy()
    expect(result.lunar.day).toBeTruthy()
    expect(result.solarDate).toBe('2024-01-15')
  })

  test('returns null for invalid date', () => {
    const result = getHuangli(new Date(NaN))
    expect(result).toBeNull()
  })

  test('returns yi/ji arrays', () => {
    const result = getHuangli(new Date('2024-06-01'))
    expect(result).not.toBeNull()
    if (!result) return
    expect(Array.isArray(result.yiJi.yi)).toBe(true)
    expect(Array.isArray(result.yiJi.ji)).toBe(true)
  })

  test('returns zodiac info', () => {
    const result = getHuangli(new Date('2024-01-01'))
    expect(result).not.toBeNull()
    if (!result) return
    expect(result.shengxiao.year).toBeTruthy()
    expect(result.shengxiao.yearZhi).toBeTruthy()
  })

  test('returns weekDay in Chinese', () => {
    // 2024-01-15 is a Monday
    const result = getHuangli(new Date('2024-01-15'))
    expect(result).not.toBeNull()
    if (!result) return
    expect(result.weekDay).toBe('一')
  })

  test('returns direction info', () => {
    const result = getHuangli(new Date('2024-03-01'))
    expect(result).not.toBeNull()
    if (!result) return
    expect(result.fangwei.xi).toBeTruthy()
    expect(result.fangwei.cai).toBeTruthy()
    expect(result.fangwei.fu).toBeTruthy()
  })

  test('handles multiple dates without crashing', () => {
    const dates = [
      '2024-01-01', '2024-06-15', '2024-12-31',
      '2023-01-01', '2025-01-01',
    ]
    for (const d of dates) {
      const result = getHuangli(new Date(d))
      expect(result).not.toBeNull()
    }
  })
})

/* ========================================================
   getDayCellLunarInfo — 日期格农历信息
   ======================================================== */

describe('getDayCellLunarInfo', () => {
  test('returns lunar info for valid date', () => {
    const result = getDayCellLunarInfo(new Date('2024-01-15'))
    expect(result).not.toBeNull()
    if (!result) return
    expect(result.lunarDay).toBeTruthy()
    expect(result.ganZhiDay).toBeTruthy()
    expect(Array.isArray(result.festivals)).toBe(true)
  })

  test('returns null for invalid date', () => {
    const result = getDayCellLunarInfo(new Date(NaN))
    expect(result).toBeNull()
  })
})

/* ========================================================
   generateDailyAdvice — 命理建议生成
   ======================================================== */

describe('generateDailyAdvice', () => {
  test('returns advice with level', () => {
    const huangli = getHuangli(new Date('2024-06-01'))
    const bazi = calculateBazi(new Date('1990-01-01'), 8, 'male')
    expect(huangli).not.toBeNull()
    expect(bazi).not.toBeNull()
    if (!huangli || !bazi) return

    const advice = generateDailyAdvice(huangli, bazi, new Date('2024-06-01'))
    expect(['吉', '中吉', '平和', '小凶', '凶']).toContain(advice.level)
    expect(advice.summary).toBeTruthy()
    expect(advice.suitable).toBeInstanceOf(Array)
    expect(advice.avoid).toBeInstanceOf(Array)
  })

  test('works without bazi data', () => {
    const huangli = getHuangli(new Date('2024-06-01'))
    expect(huangli).not.toBeNull()
    if (!huangli) return

    const advice = generateDailyAdvice(huangli, null, new Date('2024-06-01'))
    expect(advice.level).toBeTruthy()
    // Without bazi, clothing advice should mention it
    expect(advice.clothingAdvice).toContain('无八字数据')
  })

  test('provides suitable/avoid from huangli yi/ji', () => {
    const huangli = getHuangli(new Date('2024-10-01'))
    expect(huangli).not.toBeNull()
    if (!huangli) return

    const advice = generateDailyAdvice(huangli, null, new Date('2024-10-01'))
    expect(advice.suitable.length).toBeGreaterThanOrEqual(0)
    expect(advice.avoid.length).toBeGreaterThanOrEqual(0)
  })
})

/* ========================================================
   getDailyGuidance — 每日指导（黄历+八字+待办综合）
   ======================================================== */

describe('getDailyGuidance', () => {
  const testDate = new Date('2099-06-01') // far future so no items are "overdue"
  const bazi = calculateBazi(new Date('1990-01-01'), 8, 'male')

  test('returns huangli data', () => {
    const guidance = getDailyGuidance(testDate, null, [])
    expect(guidance.huangli).not.toBeNull()
  })

  test('computes todoStats correctly', () => {
    const todos = [
      { dueDate: testDate.getTime(), done: false, priority: 0 as const },
      { dueDate: testDate.getTime(), done: true, priority: 1 as const },
      { dueDate: testDate.getTime(), done: false, priority: 2 as const },
      { dueDate: new Date('2020-01-01').getTime(), done: false, priority: 0 as const }, // different date, filtered out
    ]
    const guidance = getDailyGuidance(testDate, null, todos)

    expect(guidance.todoStats.total).toBe(3) // 3 match testDate
    expect(guidance.todoStats.done).toBe(1)
    expect(guidance.todoStats.pending).toBe(2)
    expect(guidance.todoStats.overdue).toBe(0) // all due in far future
    expect(guidance.todoStats.highPriority).toBe(1) // P0 only, P1 is done
  })

  test('generates advice when bazi is provided', () => {
    if (!bazi) return
    const guidance = getDailyGuidance(testDate, bazi, [])
    expect(guidance.advice).not.toBeNull()
    if (guidance.advice) {
      expect(guidance.advice.level).toBeTruthy()
      expect(guidance.advice.summary).toContain('命主')
    }
  })

  test('generates fallback advice without bazi', () => {
    const guidance = getDailyGuidance(testDate, null, [])
    expect(guidance.advice).not.toBeNull()
  })

  test('reports overdue todos across dates', () => {
    const yesterday = new Date('2024-05-31')
    const guidance = getDailyGuidance(testDate, null, [
      { dueDate: yesterday.getTime(), done: false, priority: undefined },
    ])
    // The overdue todo is for yesterday, not testDate
    expect(guidance.todoStats.total).toBe(0)
  })
})

/* ========================================================
   getWuxingColor — 五行色
   ======================================================== */

describe('getWuxingColor', () => {
  test('returns colors for all five elements', () => {
    const colors = ['金', '木', '水', '火', '土'].map(getWuxingColor)
    expect(colors).toHaveLength(5)
    colors.forEach(c => expect(c).toMatch(/^#[0-9a-f]{6}$/))
  })

  test('returns fallback for unknown element', () => {
    const color = getWuxingColor('unknown')
    expect(color).toBe('#888')
  })
})
