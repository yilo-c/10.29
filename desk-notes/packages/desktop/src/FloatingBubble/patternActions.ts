/**
 * patternActions.ts — 划词浮窗动作清单与后端 pattern.execute RPC 调用器。
 *
 * 动作 id/label/icon 与后端注册表逐一镜像（services/pattern_actions.py:354-368，
 * 15 个内置动作）。后端暂无 pattern.menu RPC，故此处硬编码清单为前端镜像，
 * 若后端增删动作需同步本表（或等 T5 注册表收编后改走统一 RPC）。
 */
import { getWsClient } from '../utils/ai'

export interface PatternActionDef {
  id: string
  label: string
  icon: string
}

/** 与 PatternActionRegistry._register_builtin 一一对应。 */
export const PATTERN_MENU_ACTIONS: PatternActionDef[] = [
  { id: 'continue', label: '续写', icon: '✍️' },
  { id: 'explain', label: '解释', icon: '💡' },
  { id: 'translate', label: '翻译', icon: '🌐' },
  { id: 'translate_en', label: '译成英文', icon: '🇬🇧' },
  { id: 'summarize', label: '总结', icon: '🔍' },
  { id: 'polish', label: '润色', icon: '✏️' },
  { id: 'save_note', label: '保存笔记', icon: '📌' },
  { id: 'fetch_url', label: '提取链接', icon: '🔗' },
  { id: 'word_count', label: '字数统计', icon: '📊' },
  { id: 'keywords', label: '提取关键词', icon: '🏷' },
  { id: 'json_format', label: 'JSON 美化', icon: '🔧' },
  { id: 'base64_encode', label: 'Base64 编码', icon: '🔐' },
  { id: 'base64_decode', label: 'Base64 解码', icon: '🔓' },
  { id: 'date_convert', label: '日期转换', icon: '📅' },
  { id: 'code_format', label: '代码格式化', icon: '💻' },
]

/** 划词浮窗默认只展示高频动作（避免 15 键挤爆小窗），其余折叠在“更多”。 */
export const PRIMARY_ACTION_IDS = [
  'explain', 'translate', 'summarize', 'polish', 'save_note', 'keywords',
] as const

export function actionsForDisplay(): { primary: PatternActionDef[]; more: PatternActionDef[] } {
  const primary = PRIMARY_ACTION_IDS
    .map(id => PATTERN_MENU_ACTIONS.find(a => a.id === id))
    .filter((a): a is PatternActionDef => !!a)
  const more = PATTERN_MENU_ACTIONS.filter(a => !PRIMARY_ACTION_IDS.includes(a.id as typeof PRIMARY_ACTION_IDS[number]))
  return { primary, more }
}

/** pattern.execute 前端超时（ms）：本次不动后端超时，前端侧防御。 */
export const PATTERN_EXECUTE_TIMEOUT_MS = 15_000

export interface PatternExecuteResult {
  ok: boolean
  result: string
  error: string
  timedOut: boolean
}

/** ⑥ 空 payload 前端防御：收件/动作链路统一判空口径（trim 后非空）。 */
export function isCapturableText(text: string | null | undefined): boolean {
  return !!text && text.trim().length > 0
}

/**
 * 调用后端 pattern.execute。后端契约（backend/handlers/_ai_pattern.py:18-31）：
 * 入参 {action_id, text}，返回 {result} 或 {result:"", error}。
 * 失败/超时不抛异常，统一映射为 PatternExecuteResult，供调用方行内提示。
 */
export async function executePatternAction(
  actionId: string,
  text: string,
  timeoutMs: number = PATTERN_EXECUTE_TIMEOUT_MS,
): Promise<PatternExecuteResult> {
  const known = PATTERN_MENU_ACTIONS.some(a => a.id === actionId)
  if (!known) return { ok: false, result: '', error: `未知操作: ${actionId}`, timedOut: false }
  // 空 payload 防御：不发无内容的 RPC，不写空收件
  if (!isCapturableText(text)) return { ok: false, result: '', error: '选中文本为空', timedOut: false }

  const client = getWsClient()
  if (client.status !== 'connected') {
    return { ok: false, result: '', error: '后端未连接，请启动思忆集后端后重试', timedOut: false }
  }

  let timer: ReturnType<typeof setTimeout> | null = null
  try {
    const resp = await Promise.race([
      client.call<{ result?: string; error?: string }>('pattern.execute', { action_id: actionId, text: text.trim() }, timeoutMs),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(`pattern.execute 超时（${timeoutMs / 1000}s）`)), timeoutMs)
      }),
    ])
    const error = String(resp?.error ?? '')
    const result = String(resp?.result ?? '')
    if (error) return { ok: false, result, error, timedOut: false }
    return { ok: true, result, error: '', timedOut: false }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return { ok: false, result: '', error: msg, timedOut: msg.includes('超时') }
  } finally {
    if (timer) clearTimeout(timer)
  }
}
