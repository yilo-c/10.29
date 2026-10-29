import type { PetMood } from '../types/pet'
import type { XiaoWenAction } from './xiaowenTypes'

export interface XiaoWenDecisionInput {
  mood: PetMood
  suggestedAction?: XiaoWenAction
  hasNotification?: boolean
  isChatOpen?: boolean
}

export interface XiaoWenDecision {
  action: XiaoWenAction | null
  reason: string
  priority: number
}

const VALID_ACTIONS: readonly XiaoWenAction[] = [
  'idle',
  'clickDefend',
  'shy',
  'dragFly',
  'land',
  'combo3',
  'combo5',
  'chatOpen',
  'notice',
  'glance',
  'hmph',
  'sleepy',
  'startup',
]

const MOOD_ACTIONS: Partial<Record<PetMood, XiaoWenDecision>> = {
  thinking: { action: 'chatOpen', reason: 'mood:thinking', priority: 40 },
  notification: { action: 'notice', reason: 'mood:notification', priority: 50 },
  alert: { action: 'notice', reason: 'mood:alert', priority: 50 },
  sleeping: { action: 'sleepy', reason: 'mood:sleeping', priority: 30 },
  happy: { action: 'combo3', reason: 'mood:happy', priority: 35 },
  done: { action: 'combo3', reason: 'mood:done', priority: 35 },
}

function isXiaoWenAction(value: string | undefined): value is XiaoWenAction {
  return Boolean(value && VALID_ACTIONS.includes(value as XiaoWenAction))
}

export function decideXiaoWenAction(input: XiaoWenDecisionInput): XiaoWenDecision {
  if (isXiaoWenAction(input.suggestedAction)) {
    return {
      action: input.suggestedAction,
      reason: 'suggested-action',
      priority: 80,
    }
  }

  if (input.hasNotification) {
    return { action: 'notice', reason: 'context:notification', priority: 60 }
  }

  if (input.isChatOpen && input.mood === 'idle') {
    return { action: 'chatOpen', reason: 'context:chat-open', priority: 25 }
  }

  return MOOD_ACTIONS[input.mood] ?? {
    action: null,
    reason: `mood:${input.mood}:no-op`,
    priority: 0,
  }
}
