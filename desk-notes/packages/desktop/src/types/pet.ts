/** Modes for the floating pet avatar */
export type PetMood = 'idle' | 'listening' | 'thinking' | 'done' | 'happy' | 'alert' | 'notification' | 'sleeping'

/** Mood metadata used by both Orb and ChatPanel */
export interface MoodConfig {
  label: string
  color: string
}

export const MOOD_CONFIGS: Record<PetMood, MoodConfig> = {
  idle: { label: '就绪', color: 'var(--success)' },
  listening: { label: '聆听中', color: 'var(--brand)' },
  thinking: { label: '思考中', color: 'var(--warning)' },
  done: { label: '完成', color: 'var(--success)' },
  happy: { label: '开心', color: 'var(--brand)' },
  alert: { label: '有情况', color: 'var(--danger)' },
  notification: { label: '新通知', color: '#a855f7' },
  sleeping: { label: '休息中', color: '#6b7280' },
}

/** Pet activity level — what the pet is currently doing */
export type PetActivity = 'idling' | 'speaking' | 'sleeping' | 'playing'

/** Extended animation state beyond mood */
export interface PetAnimationState {
  mood: PetMood
  activity: PetActivity
  /** Override expression (e.g. forced blink, surprise) */
  expression?: 'normal' | 'blink' | 'surprise'
}

/** Event handlers a pet avatar can respond to */
export interface PetEventHandlers {
  onClick?: () => void
  onDoubleClick?: () => void
  onContextMenu?: () => void
}

/** Props for any PetAvatar component (Orb now, DesktopPet later) */
export interface PetAvatarProps {
  mood: PetMood
  /** Defaults to 72 when omitted */
  size?: number
  /** Unified event handlers — replaces bare onClick */
  eventHandlers?: PetEventHandlers
  /** Direct click handler (legacy, prefer eventHandlers.onClick) */
  onClick?: () => void
  /** When false, the avatar may render in a subdued state */
  active?: boolean
  /** Extended animation state for richer pet behavior */
  animationState?: PetAnimationState
}

/** Window & hit-area config for the pet BrowserWindow */
export interface PetWindowConfig {
  windowWidth: number
  windowHeight: number
  hitArea: { x: number; y: number; width: number; height: number }
}
