/* ── 小问台词：有后端时从 xiaowen.quote RPC 获取记忆驱动台词，无后端时固定数组兜底 ── */

import type { XiaoWenAction } from './xiaowenTypes'
import { swallowError } from '../utils/swallowError'

/** 本地固定台词（无后端时兜底） */
const FALLBACK_DIALOGUES: Record<XiaoWenAction, string[]> = {
  idle: [
    '剑在人在……呼……',
    '此剑随我八百年……哦不是，八年。',
    '华山剑法第三式……咦，第三式是什么来着？',
  ],
  clickDefend: ['干嘛！——哦，是你啊。'],
  shy: ['戳一下两下的……当心我用剑气挠你痒痒哦！'],
  dragFly: ['喂喂，我自己会走……', '华山轻功都被你浪费了。'],
  land: ['到了——唔，落地有点歪。'],
  combo3: ['再三挑衅——华山剑法第一式·疏狂剑意！……看招！'],
  combo5: ['千山冷落凌云道——！一生疏狂——！剑并箫！……哼！'],
  chatOpen: ['你聊你的，我练我的。——诶等等，你说的那个我也有兴趣。'],
  notice: ['嗯？有风声。'],
  glance: ['……才、才没有在看你呢。', '只是刚好朝那边看了一眼而已。'],
  hmph: ['哼。', '哼--别得意，本姑娘只是今天心情好。'],
  blink: [],  // 眨眼插曲不刷台词
  sleepy: ['亥时了……华山弟子也是要睡觉的……哈啊~~'],
  startup: ['小问报到——今天也是剑意满满的一天！'],
}

/** 情绪 → action 映射（用于 xiaowen.quote 调用） */
function moodToAction(mood?: string): XiaoWenAction {
  switch (mood) {
    case 'idle': case 'done': return 'idle'
    case 'happy': return 'idle'  // happy → idle 台词
    case 'thinking': return 'idle'
    case 'alert': case 'notification': return 'notice'
    case 'sleeping': return 'sleepy'
    default: return 'idle'
  }
}

/**
 * 获取小问台词。优先通过 xiaowen.quote RPC 获取记忆驱动台词。
 * @param action 动作类型
 * @param mood 当前情绪（可选，用于 RPC）
 */
export async function getXiaoWenLine(action: XiaoWenAction, mood?: string): Promise<string> {
  // 尝试从后端获取记忆驱动台词
  try {
    const { getWsClient } = await import('@desk-notes/shared')
    const client = getWsClient()
    const rpcAction = mood ? moodToAction(mood) : action
    const result: any = await client.call('xiaowen.quote', { action: rpcAction })
    if (result?.quote) {
      return result.quote
    }
  } catch (e) {
    // 无后端连接，fallback 到本地固定数组
    swallowError('xiaowenDialogue:51', e)
  }

  // 无后端时兜底
  const lines = FALLBACK_DIALOGUES[action] || FALLBACK_DIALOGUES.idle
  return lines[Math.floor(Math.random() * lines.length)] ?? ''
}

/** 同步版本（用于不需要异步的场景） */
export function getFallbackLine(action: XiaoWenAction): string {
  const lines = FALLBACK_DIALOGUES[action] || FALLBACK_DIALOGUES.idle
  return lines[Math.floor(Math.random() * lines.length)] ?? ''
}
