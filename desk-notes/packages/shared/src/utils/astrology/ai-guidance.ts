import type { BaziProfile } from './bazi'
import type { HuangliData } from './types'
import type { AIConfig } from '../../types'
import type { WeatherData } from './weather'
import { getAIProvider } from '../ai'

export interface AIGuidanceResult {
  text: string
  cached: boolean
}

function buildPrompt(
  date: Date,
  huangli: HuangliData,
  bazi: BaziProfile | null,
  todos: { text: string; done: boolean; priority?: number }[],
  weather?: WeatherData | null,
): string {
  const dateStr = `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`
  const yi = huangli.yiJi.yi.join('、') || '无'
  const ji = huangli.yiJi.ji.join('、') || '无'
  const chong = huangli.chongSha.chong || '无'
  const sha = huangli.chongSha.sha || '无'
  const jianChu = huangli.jianChu || '—'
  const jianChuLuck = huangli.jianChuLuck || '—'

  let baziSection = ''
  if (bazi) {
    const wxChart = Object.entries(bazi.wuxing)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${k}${v}`)
      .join(' ')
    baziSection = `【八字】日主${bazi.dayMaster}，${bazi.strength === '强' ? '身强' : bazi.strength === '弱' ? '身弱' : '中和'}`
    if (bazi.yongShen.length > 0) baziSection += `，喜${bazi.yongShen.join('、')}`
    if (bazi.jiShen.length > 0) baziSection += `，忌${bazi.jiShen.join('、')}`
    baziSection += `；五行（${wxChart}）`
    if (bazi.missingElement) baziSection += `，缺${bazi.missingElement}`
    baziSection += '\n'
  }

  let weatherSection = ''
  if (weather) {
    weatherSection = `【天气】${weather.city} ${weather.condition} ${weather.temp}°C（体感${weather.feelsLike}°C），湿度${weather.humidity}%，风速${weather.windSpeed}km/h\n`
  }

  const activeTodos = todos.filter(t => !t.done)
  const todosStr = activeTodos.length > 0
    ? activeTodos.map(t => `${t.text}${t.priority !== undefined && t.priority <= 1 ? '（重要）' : ''}`).join('、')
    : '无待办事项'

  return `你是一位精通中国传统文化的顾问，结合八字命理、黄历择吉和天气为用户提供今日生活建议。
请根据以下信息，给出简明、实用、口语化的综合建议（150字以内）：

【日期】${dateStr}
【黄历】宜：${yi}；忌：${ji}；冲${chong}煞${sha}；${jianChu}（${jianChuLuck}）
${baziSection}${weatherSection}【待办】${todosStr}

从以下几个角度给出建议：
1. 今日整体运势（结合天气对情绪和状态的影响）
2. 待办事项的优先顺序建议
3. 适合的着装颜色和穿搭建议（考虑天气因素）
4. 需要注意的事项`
}

/** 内存缓存：dateString → AI 结果 */
const adviceCache = new Map<string, string>()

export async function getAIGuidance(
  date: Date,
  huangli: HuangliData,
  bazi: BaziProfile | null,
  todos: { text: string; done: boolean; priority?: number }[],
  onChunk?: (text: string) => void,
  aiConfig?: AIConfig,
  weather?: WeatherData | null,
): Promise<AIGuidanceResult> {
  const cacheKey = date.toDateString()

  // 命中缓存
  const cached = adviceCache.get(cacheKey)
  if (cached) return { text: cached, cached: true }

  if (!aiConfig) throw new Error('AI 配置未提供')
  if (!aiConfig.enabled) throw new Error('AI 未启用')

  const prompt = buildPrompt(date, huangli, bazi, todos, weather)

  // 统一通过 AIProvider 调用（复用已有的流式/Ollama/OpenAI 逻辑）
  const provider = getAIProvider(aiConfig)
  let fullText = ''
  const chunkHandler = onChunk
    ? (chunk: string) => { fullText = chunk; onChunk(chunk) }
    : (chunk: string) => { fullText = chunk }
  const result = await provider.processText('continue', prompt, chunkHandler)

  fullText = result
  adviceCache.set(cacheKey, fullText)
  return { text: fullText, cached: false }
}

/** 清空 AI 建议缓存 */
export function clearAICache(): void {
  adviceCache.clear()
}
