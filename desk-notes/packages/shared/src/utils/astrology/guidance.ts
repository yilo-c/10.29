/**
 * 每日命理指导引擎
 * 结合黄历 + 八字 + 待办事项生成每日综合建议
 */
import type { HuangliData } from './types'
import { generateDailyAdvice, type BaziProfile, type GuidanceAdvice } from './bazi'
import { getHuangli } from './huangli'

export interface DailyGuidance {
  /** 黄历数据 */
  huangli: HuangliData | null
  /** 命理建议 */
  advice: GuidanceAdvice | null
  /** 当日待办统计 */
  todoStats: {
    total: number
    done: number
    pending: number
    overdue: number
    highPriority: number
  }
}

/**
 * 生成某天的完整指导数据
 */
export function getDailyGuidance(
  date: Date,
  bazi: BaziProfile | null,
  todos: { dueDate?: number; done: boolean; priority?: number }[]
): DailyGuidance {
  const huangli = getHuangli(date)

  let advice: GuidanceAdvice | null = null
  if (huangli) {
    advice = generateDailyAdvice(huangli, bazi, date)
  }

  // 待办统计
  const dayStart = new Date(date)
  dayStart.setHours(0, 0, 0, 0)
  const dayEnd = new Date(date)
  dayEnd.setHours(23, 59, 59, 999)

  const dayTodos = todos.filter(t => {
    if (!t.dueDate) return false
    return t.dueDate >= dayStart.getTime() && t.dueDate <= dayEnd.getTime()
  })

  const now = Date.now()
  const todoStats = {
    total: dayTodos.length,
    done: dayTodos.filter(t => t.done).length,
    pending: dayTodos.filter(t => !t.done).length,
    overdue: dayTodos.filter(t => !t.done && t.dueDate && t.dueDate < now).length,
    highPriority: dayTodos.filter(t => !t.done && t.priority !== undefined && t.priority <= 1).length,
  }

  return { huangli, advice, todoStats }
}

/**
 * 生成今日综合评语
 * @param dateSeed 日期种子（如 "2026/5/14"），同一天返回相同结果
 */
export function generateSummaryQuote(guidance: DailyGuidance, dateSeed?: string): string {
  const { advice, todoStats } = guidance

  if (!advice) {
    if (todoStats.pending === 0) return '今日无事，静坐品茶也是修行。'
    return `今日还有 ${todoStats.pending} 项待办，加油完成吧。`
  }

  const quotes: Record<string, string[]> = {
    吉: ['诸事皆宜，顺势而为必有所得。', '吉星高照，宜把握良机。', '天地交泰，万事亨通。'],
    中吉: ['诸事顺遂，宜稳步推进。', '天时地利，宜积极作为。', '运势向好，宜把握时机。'],
    平和: ['平淡是真，宜按部就班。', '诸事平和，宜稳中求进。', '不急不躁，随缘而行。'],
    小凶: ['宜静不宜动，谨慎行事。', '诸事有阻，宜放缓节奏。', '小心驶得万年船。'],
    凶: ['宜守不宜攻，以退为进。', '诸事不利，宜静观其变。', '韬光养晦，待时而动。'],
  }

  const todays = quotes[advice.level] || quotes.平和

  // 用日期种子做确定性选择，同一天始终一致
  let idx: number
  if (dateSeed) {
    let hash = 0
    for (let i = 0; i < dateSeed.length; i++) {
      hash = ((hash << 5) - hash) + dateSeed.charCodeAt(i)
      hash |= 0
    }
    idx = Math.abs(hash) % todays.length
  } else {
    idx = Math.floor(Math.random() * todays.length)
  }
  const quote = todays[idx]

  if (todoStats.pending > 0) {
    return `今日${advice.level}，${quote} 还有 ${todoStats.pending} 项待办待处理。`
  }
  return `今日${advice.level}，${quote}`
}
