/**
 * 中医养生建议引擎 — 纯规则，无 AI 依赖
 * 基于天气、季节、八字五行给出简明养生提示
 */

import type { BaziProfile } from '@desk-notes/shared'

export interface TcmAdvice {
  /** 核心建议 */
  summary: string
  /** 饮食建议 */
  diet?: string
  /** 生活作息 */
  lifestyle?: string
}

interface WeatherInfo {
  temp: number
  feelsLike: number
  humidity: number
  condition: string
}

function getSeason(date: Date): { season: string; principle: string; organ: string; advice: string; diet: string } {
  const m = date.getMonth() + 1
  if (m >= 3 && m <= 5) return {
    season: '春', organ: '肝', principle: '疏肝理气',
    advice: '春宜养肝，早睡早起，适当运动，多与自然接触',
    diet: '多食绿色蔬菜（菠菜、芹菜），少食酸味食物',
  }
  if (m >= 6 && m <= 8) return {
    season: '夏', organ: '心', principle: '清热养心',
    advice: '夏宜养心，午休养心，避免大汗，心静自然凉',
    diet: '多食苦味食物（苦瓜、绿茶），适量补充水分',
  }
  if (m >= 9 && m <= 11) return {
    season: '秋', organ: '肺', principle: '润燥养肺',
    advice: '秋宜养肺，早卧早起，收敛神气',
    diet: '多食白色食物（梨、银耳、百合），少食辛辣',
  }
  return {
    season: '冬', organ: '肾', principle: '藏精温补',
    advice: '冬宜养肾，早卧晚起，待日出后运动，注意保暖',
    diet: '适当进补，多食黑色食物（黑豆、芝麻）, 温热饮食',
  }
}

function getWeatherAdvice(weather: WeatherInfo): TcmAdvice | null {
  const { temp, humidity, condition } = weather

  if (temp >= 38) {
    return {
      summary: '酷暑难耐，注意防暑降温',
      diet: '多饮绿豆汤、酸梅汤，食用西瓜、黄瓜等清热解暑食物',
      lifestyle: '避免正午外出，室内注意通风降温',
    }
  }
  if (temp >= 33) {
    return {
      summary: '天气炎热，注意防暑补水',
      diet: '适当饮用淡盐水、绿豆汤',
      lifestyle: '避免长时间暴晒，午后小憩养心',
    }
  }
  if (temp <= 0) {
    return {
      summary: '严寒天气，注意保暖防寒',
      diet: '宜进食温热食物（羊肉、姜汤），少量饮酒驱寒',
      lifestyle: '注意头部和脚部保暖，睡前热水泡脚',
    }
  }
  if (temp <= 8) {
    return {
      summary: '气温较低，注意添衣保暖',
      diet: '适当温补，多饮热汤热茶',
      lifestyle: '及时增添衣物，预防感冒',
    }
  }
  if (humidity >= 85) {
    return {
      summary: '湿气较重，注意祛湿健脾',
      diet: '薏米赤小豆汤、冬瓜汤有助于祛湿',
      lifestyle: '保持居所通风干燥，适当运动排汗',
    }
  }
  if (humidity <= 30) {
    return {
      summary: '气候干燥，注意润燥养肺',
      diet: '多食梨、银耳、百合等润燥食物，多饮水',
      lifestyle: '使用加湿器，避免过度出汗',
    }
  }
  if (condition.includes('雨') || condition.includes('雪') || condition.includes('冻')) {
    return {
      summary: '雨雪天气，注意关节保暖',
      diet: '可饮姜茶、红糖水驱寒暖身',
      lifestyle: '出行注意防滑，关节部位注意保暖',
    }
  }
  if (temp > 30 && humidity > 70) {
    return {
      summary: '闷热天气，注意清热祛湿',
      diet: '清淡饮食，多食冬瓜、苦瓜',
      lifestyle: '避免剧烈运动，保持室内通风',
    }
  }

  return null
}

function getBaziAdvice(bazi: BaziProfile): TcmAdvice | null {
  const missing = bazi.missingElement
  const wuxing = bazi.wuxing || {}

  // Find the weakest element (lowest count > 0)
  const entries = Object.entries(wuxing).filter(([, v]) => v >= 0)
  const weakest = entries.sort(([, a], [, b]) => a - b)[0]

  if (missing === '木' || (weakest && weakest[0] === '木' && weakest[1] <= 1)) {
    return {
      summary: '木气偏弱，宜疏肝养肝',
      diet: '多食绿色蔬菜，适量食用酸味食物',
      lifestyle: '多与自然接触，养绿植，早睡早起',
    }
  }
  if (missing === '火' || (weakest && weakest[0] === '火' && weakest[1] <= 1)) {
    return {
      summary: '火气不足，宜养心补阳',
      diet: '多食红色食物（红枣、番茄），适量食用苦味',
      lifestyle: '多晒太阳，适量运动提升阳气',
    }
  }
  if (missing === '土' || (weakest && weakest[0] === '土' && weakest[1] <= 1)) {
    return {
      summary: '土气偏弱，宜健脾养胃',
      diet: '多食黄色食物（小米、南瓜），规律饮食',
      lifestyle: '保持规律作息，注意消化系统养护',
    }
  }
  if (missing === '金' || (weakest && weakest[0] === '金' && weakest[1] <= 1)) {
    return {
      summary: '金气不足，宜润肺养气',
      diet: '多食白色食物（梨、银耳、豆腐）',
      lifestyle: '练习深呼吸，避免过度悲伤情绪',
    }
  }
  if (missing === '水' || (weakest && weakest[0] === '水' && weakest[1] <= 1)) {
    return {
      summary: '水气偏弱，宜补肾养阴',
      diet: '多食黑色食物（黑豆、黑芝麻、木耳）',
      lifestyle: '多饮水，避免熬夜，节制房事',
    }
  }

  return null
}

export function getTcmAdvice(
  weather: WeatherInfo | null,
  date: Date,
  bazi: BaziProfile | null,
): TcmAdvice[] {
  const result: TcmAdvice[] = []

  // Priority 1: Extreme weather
  if (weather) {
    const wAdvice = getWeatherAdvice(weather)
    if (wAdvice) result.push(wAdvice)
  }

  // Priority 2: Season principle
  const season = getSeason(date)
  result.push({
    summary: `${season.advice}`,
    diet: season.diet,
    lifestyle: season.advice,
  })

  // Priority 3: Bazi constitution (only if bazi available)
  if (bazi) {
    const bAdvice = getBaziAdvice(bazi)
    if (bAdvice) result.push(bAdvice)
  }

  return result.slice(0, 3)
}

/** 获取当前季节的中文名 */
export function getSeasonName(date: Date): string {
  return getSeason(date).season
}
