import React from 'react'
import { MOOD_CONFIGS, type PetAvatarProps, type PetMood } from '../types/pet'
import OrbFace from './OrbFace'

const MOOD_GLOW: Record<PetMood, string> = {
  idle: 'rgba(34,197,94,0.25)',
  listening: 'rgba(96,165,250,0.3)',
  thinking: 'rgba(245,158,11,0.3)',
  done: 'rgba(34,197,94,0.25)',
  happy: 'rgba(96,165,250,0.25)',
  alert: 'rgba(239,68,68,0.3)',
  notification: 'rgba(168,85,247,0.3)',
  sleeping: 'rgba(107,114,128,0.15)',
}

function animationClass(mood: PetMood): string {
  switch (mood) {
    case 'idle':
    case 'happy':
      return 'orb-breathe'
    case 'listening':
      return 'orb-breathe'
    case 'thinking':
      return 'orb-think-shake'
    case 'done':
      return ''
    case 'alert':
    case 'notification':
      return 'orb-notif-bounce'
    case 'sleeping':
      return ''
    default:
      return 'orb-breathe'
  }
}

function glowAnimationClass(mood: PetMood): string {
  switch (mood) {
    case 'idle':
    case 'happy':
    case 'sleeping':
      return 'orb-glow-pulse'
    case 'thinking':
      return 'orb-think-ring'
    case 'done':
    case 'listening':
    case 'alert':
    case 'notification':
      return ''
    default:
      return 'orb-glow-pulse'
  }
}

/**
 * PetAvatar implementation — a floating orb with glassmorphism style,
 * SVG facial expressions, breathing animation, and mood-reactive glow.
 */
const Orb: React.FC<PetAvatarProps> = ({ mood, size = 48, onClick }) => {
  const cfg = MOOD_CONFIGS[mood]
  const s = size
  const animCls = animationClass(mood)
  const glowCls = glowAnimationClass(mood)
  const orBounce = mood === 'done' ? 'orb-done-bounce' : ''
  const orSleep = mood === 'sleeping' ? 'orb-sleep-breathe' : ''

  return (
    <div
      className={`relative flex-shrink-0 select-none cursor-pointer transition-transform active:scale-95 ${animCls} ${orBounce}`}
      style={{
        width: s, height: s,
        animationDuration: mood === 'sleeping' ? '6s' : undefined,
      }}
      onClick={onClick}
      role="button"
      tabIndex={0}
      aria-label={cfg.label}
    >
      {/* Mood glow aura */}
      <div
        className={`absolute rounded-full ${glowCls} ${orSleep}`}
        style={{
          width: s, height: s,
          background: MOOD_GLOW[mood],
          filter: 'blur(10px)',
          transform: mood === 'sleeping' ? 'scale(0.9)' : 'scale(1.15)',
          transition: 'background 0.4s, transform 0.6s',
        }}
      />
      {/* Thinking ring (conic-gradient border) */}
      {mood === 'thinking' && (
        <div
          className="absolute rounded-full orb-think-ring"
          style={{
            width: s + 8, height: s + 8,
            left: -4, top: -4,
            background: 'conic-gradient(from 0deg, transparent 0deg, rgba(245,158,11,0.6) 90deg, transparent 180deg, rgba(245,158,11,0.6) 270deg, transparent 360deg)',
            borderRadius: '50%',
            pointerEvents: 'none',
          }}
        />
      )}
      {/* Glass orb body */}
      <div
        className="absolute rounded-full flex items-center justify-center"
        style={{
          width: s, height: s,
          background: mood === 'sleeping'
            ? 'color-mix(in srgb, var(--panel-bg) 60%, transparent)'
            : 'var(--panel-bg)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          border: '1px solid var(--panel-border)',
          boxShadow: `0 4px 16px rgba(0,0,0,0.25), 0 0 0 1px rgba(255,255,255,0.04)`,
          overflow: 'hidden',
          opacity: mood === 'sleeping' ? 0.5 : 1,
          transition: 'opacity 0.8s, background 0.8s',
        }}
      >
        {/* Face area — roughly 50% of orb size */}
        <div style={{ width: '54%', height: '54%' }}>
          <OrbFace mood={mood} />
        </div>
      </div>
      {/* Mood indicator dot */}
      <div
        className="absolute rounded-full"
        style={{
          width: Math.round(s * 0.18), height: Math.round(s * 0.18),
          bottom: -1, right: -1,
          background: cfg.color,
          boxShadow: `0 0 0 2px var(--panel-bg), 0 0 6px ${cfg.color}`,
          transition: 'background 0.4s',
        }}
      />
      {/* Notification badge */}
      {mood === 'notification' && (
        <div
          className="absolute rounded-full flex items-center justify-center"
          style={{
            width: Math.round(s * 0.3), height: Math.round(s * 0.3),
            top: -2, right: -2,
            background: '#a855f7',
            color: '#fff',
            fontSize: Math.round(s * 0.14),
            fontWeight: 700,
            boxShadow: '0 0 0 2px var(--panel-bg)',
            pointerEvents: 'none',
          }}
        >
          !
        </div>
      )}
    </div>
  )
}

export default Orb
