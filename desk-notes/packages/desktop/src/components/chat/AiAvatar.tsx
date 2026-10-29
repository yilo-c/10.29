import { motion } from 'framer-motion'
import OrbFace from '../../FloatingBubble/OrbFace'
import type { PetMood } from '../../types/pet'

interface AiAvatarProps {
  /** 小问 orb 表情 mood；默认 idle */
  mood?: PetMood
  /** 直径，默认 28（与旧 w-7 一致） */
  size?: number
  /** 流式：在右下角显示呼吸状态小圆点 */
  streaming?: boolean
}

/**
 * AI 头像 — 复用「小问」orb 表情体系（OrbFace SVG），挂在品牌渐变圆底上，
 * 取代旧的 fa-robot 图标。mood 三态：thinking（流式/呼吸）、alert（工具调用/警示）、done（完成）。
 * thinking/listening 时整球轻微呼吸缩放。
 */
export function AiAvatar({ mood = 'idle', size = 28, streaming = false }: AiAvatarProps) {
  const breathing = mood === 'thinking' || mood === 'listening'
  // listening/done 更清爽，用实色品牌；其余用渐变
  const faceBg = mood === 'done' ? 'var(--brand)' : 'var(--brand-gradient)'
  return (
    <div className="relative flex-shrink-0" style={{ width: size, height: size }}>
      <motion.div
        className="absolute inset-0 rounded-full overflow-hidden flex items-center justify-center"
        animate={breathing ? { scale: [1, 1.07, 1] } : { scale: 1 }}
        transition={breathing ? { duration: 1.6, repeat: Infinity, ease: 'easeInOut' } : { duration: 0.2 }}
        style={{
          background: faceBg,
          boxShadow: mood === 'alert' ? '0 0 0 1px var(--danger), 0 0 10px rgba(239,68,68,0.55)' : undefined,
        }}
      >
        <div style={{ width: '56%', height: '56%' }}>
          <OrbFace mood={mood} />
        </div>
      </motion.div>
      {streaming && <span className="absolute -right-0.5 -bottom-0.5 w-2.5 h-2.5 rounded-full bg-brand animate-pulse ring-2 ring-[var(--surface)]" title="正在生成" />}
    </div>
  )
}