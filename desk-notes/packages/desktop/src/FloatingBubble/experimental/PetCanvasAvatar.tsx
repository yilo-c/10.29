/**
 * 小问 Canvas 纯绘制桌宠 — 全程序化绘制 + 丝滑骨骼插值
 */
import React, { useRef, useEffect, useCallback } from 'react'
import type { PetAvatarProps } from '../../types/pet'
import { drawXiaoWen, defaultState, poseTargets, lerpState, type XiaoWenState } from './drawXiaoWen'

type PoseKey = 'idle' | 'defend' | 'shy' | 'fly' | 'alert'

interface Particle {
  x: number; y: number; vx: number; vy: number
  life: number; maxLife: number; size: number
  color: string; opacity: number
}

function rand(min: number, max: number) { return Math.random() * (max - min) + min }

const DBL_CLICK_MS = 280
const ACTION_HOLD_MS = 600

const PetCanvasAvatar: React.FC<PetAvatarProps> = ({
  mood, size = 72, eventHandlers, onClick, active = true,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number>(0)
  const particlesRef = useRef<Particle[]>([])
  const prevMoodRef = useRef(mood)
  const transitionRef = useRef(0)
  const actionTimerRef = useRef(0)
  const isFlyingRef = useRef(false)
  const comboCountRef = useRef(0)
  const lastClickTimeRef = useRef(0)
  const resolvedOnClick = eventHandlers?.onClick ?? onClick
  const resolvedOnDblClick = eventHandlers?.onDoubleClick

  // Drag
  const mouseDownRef = useRef(false)
  const mouseStartRef = useRef({ x: 0, y: 0 })
  const flyParticleTimerRef = useRef(0)

  // 当前动画状态 (current + target + lerp factor)
  const curStateRef = useRef<XiaoWenState>(defaultState('idle'))
  const targetRef = useRef<Partial<XiaoWenState>>({})
  const lerpSpeedRef = useRef(0.08)
  const targetPoseRef = useRef<PoseKey>('idle')

  // ── Particles ──
  const spawnMist = useCallback((x: number, y: number) => {
    particlesRef.current.push({
      x, y, vx: rand(-0.2, 0.2), vy: rand(-0.4, -0.1),
      life: 0, maxLife: rand(30, 60), size: rand(3, 7),
      color: 'rgba(200, 230, 255, 0.4)', opacity: 0.6,
    })
  }, [])
  const spawnSwordSpark = useCallback((x: number, y: number) => {
    for (let i = 0; i < 3; i++) {
      const angle = rand(-0.8, 0.8) - Math.PI / 2
      particlesRef.current.push({
        x, y,
        vx: Math.cos(angle) * rand(0.5, 2), vy: Math.sin(angle) * rand(0.5, 2),
        life: 0, maxLife: rand(15, 30), size: rand(1.5, 3),
        color: 'rgba(100, 200, 255, 0.8)', opacity: 1,
      })
    }
  }, [])
  const spawnHearts = useCallback((x: number, y: number) => {
    for (let i = 0; i < 4; i++) {
      const angle = rand(-1, 1) - Math.PI / 2
      particlesRef.current.push({
        x: x + rand(-5, 5), y,
        vx: Math.cos(angle) * rand(0.2, 0.6), vy: Math.sin(angle) * rand(0.4, 1.0),
        life: 0, maxLife: rand(25, 45), size: rand(2, 4),
        color: 'rgba(255, 140, 180, 0.8)', opacity: 1,
      })
    }
  }, [])

  const setPose = useCallback((pose: PoseKey, holdMs = 0) => {
    targetPoseRef.current = pose
    targetRef.current = poseTargets(pose)
    if (holdMs > 0) actionTimerRef.current = holdMs
  }, [])

  // ── Clicks ──
  const handleSingleClick = useCallback(() => {
    setPose('defend', ACTION_HOLD_MS)
    spawnSwordSpark(size * 0.62, size * 0.35)
    spawnSwordSpark(size * 0.68, size * 0.5)
    resolvedOnClick?.()
  }, [resolvedOnClick, spawnSwordSpark, size, setPose])

  const handleDoubleClick = useCallback(() => {
    setPose('shy', ACTION_HOLD_MS + 200)
    spawnHearts(size * 0.4, size * 0.25)
    spawnHearts(size * 0.6, size * 0.3)
    resolvedOnDblClick?.()
  }, [resolvedOnDblClick, spawnHearts, size, setPose])

  const handleCombo = useCallback(() => {
    comboCountRef.current = 0
    setPose('alert', 400)
    for (let i = 0; i < 12; i++) {
      const angle = rand(0, Math.PI * 2)
      particlesRef.current.push({
        x: size / 2, y: size / 2,
        vx: Math.cos(angle) * rand(1.5, 4), vy: Math.sin(angle) * rand(1.5, 4),
        life: 0, maxLife: rand(20, 40), size: rand(2, 5),
        color: 'rgba(100, 200, 255, 0.9)', opacity: 1,
      })
    }
  }, [size, setPose])

  const handleClick = useCallback(() => {
    if (isFlyingRef.current) return
    const now = Date.now()
    const dt = now - lastClickTimeRef.current
    if (dt < DBL_CLICK_MS) {
      comboCountRef.current = 0
      handleDoubleClick()
      lastClickTimeRef.current = 0
    } else {
      lastClickTimeRef.current = now
      setTimeout(() => {
        if (lastClickTimeRef.current === now) {
          comboCountRef.current++
          if (comboCountRef.current >= 5) handleCombo()
          else handleSingleClick()
          lastClickTimeRef.current = 0
        }
      }, DBL_CLICK_MS)
    }
  }, [handleSingleClick, handleDoubleClick, handleCombo])

  // ── Drag → fly ──
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    mouseDownRef.current = true
    mouseStartRef.current = { x: e.clientX, y: e.clientY }
  }, [])
  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!mouseDownRef.current) return
    const dx = e.clientX - mouseStartRef.current.x
    const dy = e.clientY - mouseStartRef.current.y
    if (dx * dx + dy * dy > 64 && !isFlyingRef.current) {
      isFlyingRef.current = true
      setPose('fly')
    }
    if (isFlyingRef.current) {
      flyParticleTimerRef.current++
      if (flyParticleTimerRef.current % 2 === 0) {
        spawnMist(rand(size * 0.2, size * 0.8), size)
      }
    }
  }, [size, spawnMist, setPose])
  const handleMouseUp = useCallback(() => {
    mouseDownRef.current = false
    if (isFlyingRef.current) {
      isFlyingRef.current = false
      setPose('idle')
      for (let i = 0; i < 4; i++) spawnMist(rand(size * 0.3, size * 0.7), size * 0.9)
    }
  }, [size, spawnMist, setPose])

  // ── Main loop ──
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const dpr = window.devicePixelRatio || 1
    canvas.width = size * dpr
    canvas.height = size * dpr
    canvas.style.width = size + 'px'
    canvas.style.height = size + 'px'

    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)

    if (mood !== prevMoodRef.current) {
      transitionRef.current = 1
      prevMoodRef.current = mood
    }

    const animate = (ts: number) => {
      const t = ts / 1000
      const sc = size / 512

      // ── Pose timer ──
      if (actionTimerRef.current > 0) {
        actionTimerRef.current -= 16
        if (actionTimerRef.current <= 0) {
          actionTimerRef.current = 0
          if (!isFlyingRef.current) {
            targetPoseRef.current = 'idle'
            targetRef.current = poseTargets('idle')
          }
        }
      }

      // ── 插值到目标状态 ──
      const cur = curStateRef.current
      const tgt = targetRef.current
      const speed = lerpSpeedRef.current
      curStateRef.current = lerpState(cur, tgt, speed)
      curStateRef.current.blush += (tgt.blush ?? 0) > cur.blush ? speed * 2 : -speed * 2
      curStateRef.current.blush = Math.max(0, Math.min(1, curStateRef.current.blush))
      curStateRef.current.swordGlow += (tgt.swordGlow ?? 0) > cur.swordGlow ? speed : -speed
      curStateRef.current.swordGlow = Math.max(0, Math.min(1, curStateRef.current.swordGlow))

      // ── 呼吸 / 头发 ──
      curStateRef.current.breathY = Math.sin(t * 2.1) * 2
      curStateRef.current.hairSway = Math.sin(t * 1.5) * 0.3

      // ── 眨眼 ──
      const blinkTimer = (t * 1000) % (3000 + Math.random() * 2000)
      curStateRef.current.blink = blinkTimer > 2800 && blinkTimer < 2900

      // ── Decay transition flash ──
      if (transitionRef.current > 0) {
        transitionRef.current += (0 - transitionRef.current) * 0.06
        if (transitionRef.current < 0.01) transitionRef.current = 0
      }

      // ── Update particles ──
      const particles = particlesRef.current
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i]
        p.x += p.vx; p.y += p.vy
        p.vy += 0.02
        p.life++
        if (p.life >= p.maxLife) particles.splice(i, 1)
      }

      // ── Draw ──
      ctx.clearRect(0, 0, size, size)

      // 飞行倾斜（全局叠加）
      if (isFlyingRef.current) {
        ctx.save()
        ctx.translate(size / 2, size / 2)
        ctx.rotate(-0.08)
        ctx.translate(-size / 2, -size / 2)
      }

      drawXiaoWen(ctx, curStateRef.current, sc)

      if (isFlyingRef.current) {
        ctx.restore()
      }

      // 飞行拖尾线
      if (isFlyingRef.current) {
        ctx.strokeStyle = 'rgba(200, 230, 255, 0.15)'
        ctx.lineWidth = 1
        for (let i = 0; i < 3; i++) {
          const ly = size * 0.3 + i * size * 0.15
          ctx.beginPath()
          ctx.moveTo(size * 0.8, ly)
          ctx.lineTo(size * 0.95, ly)
          ctx.stroke()
        }
      }

      // Transition flash
      if (transitionRef.current > 0.01) {
        ctx.fillStyle = `rgba(255, 255, 255, ${transitionRef.current * 0.15})`
        ctx.fillRect(0, 0, size, size)
      }

      // ── 粒子 ──
      for (const p of particles) {
        const alpha = p.opacity * (1 - p.life / p.maxLife)
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.size * (1 - p.life / p.maxLife * 0.5), 0, Math.PI * 2)
        ctx.fillStyle = p.color.replace(/[\d.]+\)$/, `${alpha})`)
        ctx.fill()
      }

      rafRef.current = requestAnimationFrame(animate)
    }

    rafRef.current = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(rafRef.current)
  }, [size, mood, spawnMist])

  return (
    <canvas
      ref={canvasRef}
      width={size}
      height={size}
      style={{
        cursor: 'pointer',
        display: 'block',
        opacity: active ? 1 : 0.4,
        transition: 'opacity 0.3s',
      }}
      onClick={handleClick}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    />
  )
}

export default PetCanvasAvatar
