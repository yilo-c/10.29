/** 手机端 AI 工具 — 翻译 + 摘要 + 语音朗读，纯客户端。 */

import { CloudAiClient } from './cloudAiClient'
import { tokenManager } from './tokenManager'

const cloudClient = new CloudAiClient()

/** 检查云端 AI 是否已配置 */
export function isAiConfigured(): boolean {
  return !!tokenManager.cloudApiKey
}

/** 翻译文本到中文 */
export async function translateText(text: string): Promise<string> {
  if (!tokenManager.cloudApiKey) throw new Error('AI not configured')
  const result = await cloudClient.sendMessage(
    `Translate the following to Chinese. Return ONLY the translation, no explanations:\n\n${text.slice(0, 2000)}`,
  )
  return result.reply
}

/** AI 摘要 */
export async function summarizeText(text: string): Promise<string> {
  if (!tokenManager.cloudApiKey) throw new Error('AI not configured')
  const result = await cloudClient.sendMessage(
    `用一句话概括以下内容（50字以内）：\n\n${text.slice(0, 2000)}`,
  )
  return result.reply
}

/** 语音朗读 (Web Speech API) */
export function speakText(text: string, lang = 'zh-CN'): void {
  if (!('speechSynthesis' in window)) return
  window.speechSynthesis.cancel() // stop any current speech
  const utterance = new SpeechSynthesisUtterance(text.slice(0, 500))
  utterance.lang = lang
  utterance.rate = 1.0
  utterance.pitch = 1.0
  utterance.onerror = (e) => { console.warn('[speakText] speech failed:', e.error) }
  window.speechSynthesis.speak(utterance)
}

export function stopSpeaking(): void {
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel()
  }
}

export interface SectionBrief {
  title: string      // e.g. "🌍 国外大势"
  items: string[]    // top 5 titles
  summary: string    // AI generated 1-2 sentence summary
}

export interface FullBrief {
  overall: string         // 综合摘要（放最前面）
  sections: SectionBrief[] // 7 个板块
}

/** Generate 7-section daily brief from articles */
export async function generateBrief(articles: { category?: string; title: string; summary?: string }[]): Promise<string> {
  if (!tokenManager.cloudApiKey) throw new Error('Cloud API key not configured')

  const SECTION_CONFIG: { cat: string; title: string; keys: string[] }[] = [
    { cat: 'international', title: '🌍 国外大势', keys: ['国际', '美国', '欧洲', '日本', '俄罗斯', '乌克兰', '中东', '联合国', '北约'] },
    { cat: 'domestic', title: '🇨🇳 国内大势', keys: ['中国', '北京', '国务院', '发改委', '中央', '国家', '政策', '经济'] },
    { cat: 'local', title: '🏙 城市热点', keys: ['成都', '四川', '北京', '上海', '深圳', '广州', '杭州', '南京', '武汉', '西安', '重庆'] },
    { cat: 'internet', title: '🌐 互联网热点', keys: ['互联网', '电商', '社交', '平台', 'App', '直播'] },
    { cat: 'ai', title: '🤖 AI 热点', keys: ['AI', 'GPT', 'Claude', '大模型', 'OpenAI', 'ChatGPT', '人工智能', '深度学习', 'LLM', '生成式'] },
    { cat: 'dev', title: '💻 技术热点', keys: ['GitHub', '开源', '编程', '前端', '后端', 'Rust', 'Python', 'Go', 'TypeScript', '数据库', '框架'] },
    { cat: 'tool', title: '🔧 工具开源', keys: ['苹果', '华为', '小米', '芯片', '手机', '特斯拉', '新能源', '显卡', '工具', '开源项目'] },
  ]

  // Group articles into sections
  const grouped: Record<string, string[]> = {}
  for (const section of SECTION_CONFIG) {
    grouped[section.cat] = []
  }

  for (const a of articles) {
    const titleLower = a.title.toLowerCase()
    for (const section of SECTION_CONFIG) {
      if (section.keys.some(k => titleLower.includes(k.toLowerCase()) || (a.summary || '').includes(k)) && grouped[section.cat].length < 5) {
        grouped[section.cat].push(`${a.title}`)
        break
      }
    }
  }

  // Build prompt
  let prompt = '你是新闻简报编辑。基于以下分类资讯生成今日简报。\n\n'
  prompt += '要求：\n'
  prompt += '1. 首先输出一段"📰 综合摘要"（150字以内，涵盖全部板块）\n'
  prompt += '2. 然后每个板块输出该板块的 1-2 句精华总结\n'
  prompt += '3. 最后列出该板块的 Top 5 标题（用 - 开头）\n\n'
  prompt += '输出格式：\n'
  prompt += '📰 综合摘要\nxxx\n\n'
  prompt += '🌍 国外大势\n总结：xxx\n- 标题1\n- 标题2\n\n（以此类推）\n\n'

  for (const section of SECTION_CONFIG) {
    const items = grouped[section.cat]
    if (items.length === 0) continue
    prompt += `## ${section.title}\n${items.map(t => `- ${t}`).join('\n')}\n\n`
  }

  const client = new CloudAiClient()
  const result = await client.sendMessage(prompt)
  return result.reply
}
