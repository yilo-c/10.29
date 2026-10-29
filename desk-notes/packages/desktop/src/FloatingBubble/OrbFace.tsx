import React, { useEffect, useRef, useState } from 'react'
import type { PetMood } from '../types/pet'

interface OrbFaceProps {
  mood: PetMood
}

/** Random blink interval between 3.5–5 seconds */
function useBlink() {
  const [blinking, setBlinking] = useState(false)
  const blinkTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const scheduleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let cancelled = false
    const schedule = () => {
      const delay = 3500 + Math.random() * 1500
      scheduleTimerRef.current = setTimeout(() => {
        if (cancelled) return
        setBlinking(true)
        blinkTimerRef.current = setTimeout(() => {
          if (!cancelled) setBlinking(false)
        }, 120)
        schedule()
      }, delay)
    }
    schedule()
    return () => {
      cancelled = true
      if (scheduleTimerRef.current) clearTimeout(scheduleTimerRef.current)
      if (blinkTimerRef.current) clearTimeout(blinkTimerRef.current)
    }
  }, [])
  return blinking
}

/** SVG face — 72×72 viewBox, expressions respond to mood */
const OrbFace: React.FC<OrbFaceProps> = ({ mood }) => {
  const blinking = useBlink()
  const color = 'var(--text-secondary)'
  const strokeW = 2

  // Eye y-position: slightly lower during blink
  const eyeY = blinking ? 32 : 26

  // Build eye elements per mood
  let leftEye: React.ReactNode
  let rightEye: React.ReactNode
  let mouth: React.ReactNode

  switch (mood) {
    case 'idle': {
      leftEye = blinking ? (
        <line x1={21} y1={26} x2={27} y2={26} stroke={color} strokeWidth={2.5} strokeLinecap="round" />
      ) : (
        <circle cx={24} cy={26} r={3.5} fill={color} />
      )
      rightEye = blinking ? (
        <line x1={45} y1={26} x2={51} y2={26} stroke={color} strokeWidth={2.5} strokeLinecap="round" />
      ) : (
        <circle cx={48} cy={26} r={3.5} fill={color} />
      )
      mouth = (
        <path d="M30 38 Q36 42 42 38" stroke={color} strokeWidth={strokeW} fill="none" strokeLinecap="round" />
      )
      break
    }
    case 'listening': {
      // Slightly wider eyes, open mouth (attentive)
      leftEye = <circle cx={24} cy={25} r={4} fill={color} />
      rightEye = <circle cx={48} cy={25} r={4} fill={color} />
      mouth = (
        <ellipse cx={36} cy={40} rx={3} ry={4} fill="none" stroke={color} strokeWidth={1.5} />
      )
      break
    }
    case 'thinking': {
      // One eye squinted (thinking face)
      leftEye = <circle cx={24} cy={26} r={3.5} fill={color} />
      // Squinted eye
      rightEye = <circle cx={48} cy={24} r={2.5} fill="none" stroke={color} strokeWidth={1.8} />
      mouth = (
        <path d="M30 42 Q36 38 42 42" stroke={color} strokeWidth={strokeW} fill="none" strokeLinecap="round" />
      )
      break
    }
    case 'done': {
      // Happy squint
      leftEye = (
        <path d="M21 26 Q24 22 27 26" stroke={color} strokeWidth={2.5} fill="none" strokeLinecap="round" />
      )
      rightEye = (
        <path d="M45 26 Q48 22 51 26" stroke={color} strokeWidth={2.5} fill="none" strokeLinecap="round" />
      )
      mouth = (
        <path d="M24 36 Q36 48 48 36" stroke={color} strokeWidth={strokeW} fill="none" strokeLinecap="round" />
      )
      break
    }
    case 'happy': {
      leftEye = (
        <path d="M21 26 Q24 22 27 26" stroke={color} strokeWidth={2.5} fill="none" strokeLinecap="round" />
      )
      rightEye = (
        <path d="M45 26 Q48 22 51 26" stroke={color} strokeWidth={2.5} fill="none" strokeLinecap="round" />
      )
      mouth = (
        <path d="M24 36 Q36 48 48 36" stroke={color} strokeWidth={strokeW} fill="none" strokeLinecap="round" />
      )
      break
    }
    case 'alert': {
      leftEye = <circle cx={24} cy={eyeY} r={5.5} fill="none" stroke={color} strokeWidth={1.8} />
      rightEye = <circle cx={48} cy={eyeY} r={5.5} fill="none" stroke={color} strokeWidth={1.8} />
      mouth = (
        <ellipse cx={36} cy={40} rx={4} ry={5} fill="none" stroke={color} strokeWidth={1.8} />
      )
      break
    }
    case 'notification': {
      // Wide eyes, small o mouth (surprised)
      leftEye = <circle cx={24} cy={25} r={4.5} fill="none" stroke={color} strokeWidth={1.8} />
      rightEye = <circle cx={48} cy={25} r={4.5} fill="none" stroke={color} strokeWidth={1.8} />
      mouth = (
        <ellipse cx={36} cy={41} rx={3} ry={3.5} fill="none" stroke={color} strokeWidth={1.5} />
      )
      break
    }
    case 'sleeping': {
      // Closed eyes (zzz)
      leftEye = (
        <line x1={21} y1={26} x2={27} y2={26} stroke={color} strokeWidth={2.5} strokeLinecap="round" />
      )
      rightEye = (
        <line x1={45} y1={26} x2={51} y2={26} stroke={color} strokeWidth={2.5} strokeLinecap="round" />
      )
      mouth = (
        <path d="M30 38 Q36 36 42 38" stroke={color} strokeWidth={1.5} fill="none" strokeLinecap="round" />
      )
      break
    }
  }

  return (
    <svg
      viewBox="0 0 72 72"
      fill="none"
      style={{ width: '100%', height: '100%', display: 'block' }}
    >
      {leftEye}
      {rightEye}
      {mouth}
      {mood === 'sleeping' && (
        <text x={52} y={18} fontSize={8} fill={color} opacity={0.6}>zzz</text>
      )}
    </svg>
  )
}

export default OrbFace
