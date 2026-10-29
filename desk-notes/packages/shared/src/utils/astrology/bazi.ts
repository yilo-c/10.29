/**
 * 八字命理引擎
 * 提供四柱计算、五行分析、身强身弱判断、喜用神/忌神分析
 */
import lunar from 'lunar-javascript'
import type { HuangliData } from './types'

const { Solar } = lunar

export interface BaziProfile {
  /** 四柱八字 */
  fourPillars: {
    year: string
    month: string
    day: string
    hour: string | null
  }
  /** 天干 */
  heavenlyStems: {
    year: string
    month: string
    day: string
    hour: string | null
  }
  /** 地支 */
  earthlyBranches: {
    year: string
    month: string
    day: string
    hour: string | null
  }
  /** 日主 */
  dayMaster: string
  /** 五行分布 */
  wuxing: Record<string, number>
  /** 生肖 */
  shengxiao: string
  /** 纳音 */
  naYin: string
  /** 身强/身弱 */
  strength: '强' | '弱' | '中'
  /** 喜用神 */
  yongShen: string[]
  /** 忌神 */
  jiShen: string[]
  /** 五行缺失 */
  missingElement: string | null
  /** 日柱三元 */
  dayStemBranch: string
}

/** 天干 → 五行映射 */
const STEM_TO_WUXING: Record<string, string> = {
  甲: '木', 乙: '木',
  丙: '火', 丁: '火',
  戊: '土', 己: '土',
  庚: '金', 辛: '金',
  壬: '水', 癸: '水',
}

/** 地支 → 五行映射 */
const BRANCH_TO_WUXING: Record<string, string> = {
  寅: '木', 卯: '木',
  巳: '火', 午: '火',
  辰: '土', 戌: '土', 丑: '土', 未: '土',
  申: '金', 酉: '金',
  亥: '水', 子: '水',
}

/** 日干 → 旺月地支集合（身强判断） */
const MASTER_STRONG_MONTHS: Record<string, string[]> = {
  甲: ['寅', '卯'], 乙: ['寅', '卯'],
  丙: ['巳', '午'], 丁: ['巳', '午'],
  戊: ['辰', '戌', '丑', '未'], 己: ['辰', '戌', '丑', '未'],
  庚: ['申', '酉'], 辛: ['申', '酉'],
  壬: ['亥', '子'], 癸: ['亥', '子'],
}

/** 日干 → 生扶月地支集合（相生，也导致身强） */
const MASTER_BIRTH_MONTHS: Record<string, string[]> = {
  甲: ['亥', '子'], 乙: ['亥', '子'],
  丙: ['寅', '卯'], 丁: ['寅', '卯'],
  戊: ['巳', '午'], 己: ['巳', '午'],
  庚: ['辰', '戌', '丑', '未'], 辛: ['辰', '戌', '丑', '未'],
  壬: ['申', '酉'], 癸: ['申', '酉'],
}

/** 五行相生 */
const GENERATES: Record<string, string> = {
  木: '火', 火: '土', 土: '金', 金: '水', 水: '木',
}

/** 五行相克 */
const CONTROLS: Record<string, string> = {
  木: '土', 土: '水', 水: '火', 火: '金', 金: '木',
}

const ALL_WUXING = ['金', '木', '水', '火', '土']

function getHourPillar(dayStem: string, hour: number): string {
  // 五鼠遁元: 根据日干和时辰推时干
  const dayIndex = '甲乙丙丁戊己庚辛壬癸'.indexOf(dayStem)
  if (dayIndex === -1) return ''

  // 时柱地支（每个时辰2小时）
  const branchIndex = Math.floor((hour + 1) / 2) % 12
  const branch = '子丑寅卯辰巳午未申酉戌亥'[branchIndex]

  // 五鼠遁: 甲己→甲子, 乙庚→丙子, 丙辛→戊子, 丁壬→庚子, 戊癸→壬子
  const startStem = ['甲', '丙', '戊', '庚', '壬'][dayIndex % 5]
  const startIndex = '甲乙丙丁戊己庚辛壬癸'.indexOf(startStem)
  const stem = '甲乙丙丁戊己庚辛壬癸'[(startIndex + branchIndex) % 10]

  return stem + branch
}

/**
 * 根据出生日期和时间计算八字
 */
export function calculateBazi(
  birthDate: Date,
  birthHour: number | null,
  _gender: 'male' | 'female' | null
): BaziProfile | null {
  try {
    const solar = Solar.fromDate(birthDate)
    const lunar_ = solar.getLunar()
    const bazi = lunar_.getEightChar()

    const yearPillar = lunar_.getYearInGanZhi()
    const monthPillar = lunar_.getMonthInGanZhi()
    const dayPillar = lunar_.getDayInGanZhi()
    const dayStem = bazi.getDayGan()

    const yearStem = yearPillar[0]
    const yearBranch = yearPillar[1]
    const monthStem = monthPillar[0]
    const monthBranch = monthPillar[1]
    const dayStemChar = dayPillar[0]
    const dayBranch = dayPillar[1]

    // 时柱
    let hourPillar: string | null = null
    let hourStem: string | null = null
    let hourBranch: string | null = null
    if (birthHour !== null && birthHour >= 0 && birthHour <= 23) {
      hourPillar = getHourPillar(dayStem, birthHour)
      hourStem = hourPillar ? hourPillar[0] : null
      hourBranch = hourPillar ? hourPillar[1] : null
    }

    // 五行统计
    const wuxing: Record<string, number> = { 金: 0, 木: 0, 水: 0, 火: 0, 土: 0 }

    const allStems = [yearStem, monthStem, dayStemChar, ...(hourStem ? [hourStem] : [])]
    const allBranches = [yearBranch, monthBranch, dayBranch, ...(hourBranch ? [hourBranch] : [])]

    for (const s of allStems) {
      const wx = STEM_TO_WUXING[s]
      if (wx) wuxing[wx] = (wuxing[wx] || 0) + 1
    }
    for (const b of allBranches) {
      const wx = BRANCH_TO_WUXING[b]
      if (wx) wuxing[wx] = (wuxing[wx] || 0) + 1
    }

    // 缺失元素
    const missing = ALL_WUXING.find(el => !wuxing[el] || wuxing[el] === 0) || null

    // 身强/身弱判断（基于月令）
    const monthBranchChar = monthPillar[1]
    const isStrongMonth = MASTER_STRONG_MONTHS[dayStemChar]?.includes(monthBranchChar)
    const isBirthMonth = MASTER_BIRTH_MONTHS[dayStemChar]?.includes(monthBranchChar)

    let strength: '强' | '弱' | '中'
    if (isStrongMonth) strength = '强'
    else if (isBirthMonth) strength = '强'
    else strength = '弱'

    // 喜用神/忌神（简化版）
    const dayMasterElement = STEM_TO_WUXING[dayStemChar]
    let yongShen: string[] = []
    let jiShen: string[] = []

    if (strength === '强') {
      // 身强：喜克泄耗，忌生扶
      // 克我者 → 官杀
      if (CONTROLS[dayMasterElement]) yongShen.push(CONTROLS[dayMasterElement])
      // 我生者 → 食伤（泄）
      if (GENERATES[dayMasterElement]) yongShen.push(GENERATES[dayMasterElement])
      // 我克者 → 财
      if (CONTROLS[dayMasterElement]) jiShen.push(dayMasterElement)
      if (GENERATES[dayMasterElement]) {
        // 生我者 → 印（忌）
        const elementThatGeneratesMe = Object.entries(GENERATES).find(([, v]) => v === dayMasterElement)?.[0]
        if (elementThatGeneratesMe) jiShen.push(elementThatGeneratesMe)
      }
    } else if (strength === '弱') {
      // 身弱：喜生扶，忌克泄耗
      // 生我者 → 印
      const elementThatGeneratesMe = Object.entries(GENERATES).find(([, v]) => v === dayMasterElement)?.[0]
      if (elementThatGeneratesMe) yongShen.push(elementThatGeneratesMe)
      // 同我者 → 比劫
      yongShen.push(dayMasterElement)
      // 克我、我生、我克 → 忌
      if (CONTROLS[dayMasterElement]) jiShen.push(CONTROLS[dayMasterElement])
      if (GENERATES[dayMasterElement]) jiShen.push(GENERATES[dayMasterElement])
      if (CONTROLS[dayMasterElement]) {
        const elementIControl = Object.entries(CONTROLS).find(([, v]) => v === dayMasterElement)?.[0]
        if (elementIControl) jiShen.push(elementIControl)
      }
    }

    // 去重
    yongShen = [...new Set(yongShen)]
    jiShen = [...new Set(jiShen)]

    return {
      fourPillars: {
        year: yearPillar,
        month: monthPillar,
        day: dayPillar,
        hour: hourPillar,
      },
      heavenlyStems: {
        year: yearStem,
        month: monthStem,
        day: dayStemChar,
        hour: hourStem,
      },
      earthlyBranches: {
        year: yearBranch,
        month: monthBranch,
        day: dayBranch,
        hour: hourBranch,
      },
      dayMaster: dayStemChar,
      wuxing,
      shengxiao: lunar_.getYearShengXiao(),
      naYin: lunar_.getDayNaYin(),
      strength,
      yongShen,
      jiShen,
      missingElement: missing,
      dayStemBranch: dayPillar,
    }
  } catch (e: unknown) {
    console.warn('[bazi] calculateBazi failed:', e)
    return null
  }
}

export interface GuidanceAdvice {
  /** 综合等级 */
  level: '吉' | '中吉' | '平和' | '小凶' | '凶'
  /** 适合做的事 */
  suitable: string[]
  /** 避免做的事 */
  avoid: string[]
  /** 幸运方向 */
  luckyDirection: string
  /** 穿衣建议 */
  clothingAdvice: string
  /** 综合建议 */
  summary: string
}

/**
 * 将五行类型转为可读名称
 */
function wxLabel(wx: string): string {
  const map: Record<string, string> = { 金: '金', 木: '木', 水: '水', 火: '火', 土: '土' }
  return map[wx] || wx
}

/**
 * 根据八字和黄历生成每日命理建议
 */
export function generateDailyAdvice(
  huangli: HuangliData,
  bazi: BaziProfile | null,
  _date: Date
): GuidanceAdvice {
  // 基础黄历信息
  const yi = huangli.yiJi.yi
  const ji = huangli.yiJi.ji
  const jianChuLuck = huangli.jianChuLuck
  const xiuLuck = huangli.xiu.luck
  const chongSha = huangli.chongSha

  // 等级计算
  let score = 0
  if (jianChuLuck === '吉' || jianChuLuck === '大吉') score += 2
  else if (jianChuLuck === '凶' || jianChuLuck === '大凶') score -= 2
  if (xiuLuck === '吉') score += 1
  else if (xiuLuck === '凶') score -= 1

  const level: GuidanceAdvice['level'] =
    score >= 2 ? '吉' : score >= 1 ? '中吉' : score >= 0 ? '平和' : score >= -1 ? '小凶' : '凶'

  // 幸运方向
  const luckyDirection = huangli.fangwei.cai || huangli.fangwei.xi || ''

  // 穿衣建议
  let clothingAdvice = ''
  if (bazi) {
    if (bazi.yongShen.length > 0) {
      clothingAdvice = `宜穿${bazi.yongShen.map(wxLabel).join('、')}色系衣物`
      if (bazi.jiShen.length > 0) {
        clothingAdvice += `，避免${bazi.jiShen.map(wxLabel).join('、')}色`
      }
    }
  } else {
    clothingAdvice = '无八字数据，建议参考黄历宜忌'
  }

  // 综合建议
  const summaryParts: string[] = []

  if (bazi) {
    const dayElement = STEM_TO_WUXING[bazi.dayMaster]
    const todayGan = huangli.ganzhi.day[0]
    const todayElement = STEM_TO_WUXING[todayGan] || ''
    const relation = todayElement === dayElement ? '比和' :
      GENERATES[todayElement] === dayElement ? '生我' :
      GENERATES[dayElement] === todayElement ? '我生' :
      CONTROLS[todayElement] === dayElement ? '克我' :
      CONTROLS[dayElement] === todayElement ? '我克' : '平和'

    summaryParts.push(`命主为${dayElement}日主，今日天干五行"${relation}"`)

    // 结合五行缺失
    if (bazi.missingElement && bazi.yongShen.includes(bazi.missingElement)) {
      summaryParts.push(`八字缺${wxLabel(bazi.missingElement)}，今日宜补${wxLabel(bazi.missingElement)}`)
    }
  }

  if (yi.length > 0) {
    summaryParts.push(`宜：${yi.slice(0, 3).join('、')}`)
  }
  if (ji.length > 0) {
    summaryParts.push(`忌：${ji.slice(0, 3).join('、')}`)
  }

  if (chongSha.chong) {
    summaryParts.push(`冲${chongSha.chong}，${chongSha.sha ? '煞' + chongSha.sha : ''}`)
  }

  return {
    level,
    suitable: yi.slice(0, 5),
    avoid: ji.slice(0, 5),
    luckyDirection,
    clothingAdvice,
    summary: summaryParts.join('；') || '今日诸事平和，按计划行事即可。',
  }
}

/**
 * 获取五行对应的颜色
 */
export function getWuxingColor(wx: string): string {
  const map: Record<string, string> = {
    金: '#f0e68c',
    木: '#4ade80',
    水: '#60a5fa',
    火: '#f87171',
    土: '#fbbf24',
  }
  return map[wx] || '#888'
}

/**
 * 获取五行对应的中文名
 */
export function getWuxingLabel(wx: string): string {
  return wxLabel(wx)
}
