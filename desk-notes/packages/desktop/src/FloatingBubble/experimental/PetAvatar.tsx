import React, { useRef, useEffect, useCallback } from 'react'
import type { PetAvatarProps } from '../../types/pet'

type SpriteKey = 'idle' | 'click_defend' | 'shy' | 'flying' | 'alert'

const SPRITES: Record<SpriteKey, string> = {
  idle: '/pet/sprite_idle.png',
  click_defend: '/pet/sprite_click_defend.png',
  shy: '/pet/sprite_happy_shy.png',
  flying: '/pet/sprite_fly.png',
  alert: '/pet/sprite_alert.png',
}

interface Particle {
  x: number; y: number; vx: number; vy: number
  life: number; maxLife: number; size: number
  color: string; opacity: number
}

function rand(min: number, max: number) { return Math.random() * (max - min) + min }

const DBL_CLICK_MS = 280
const ACTION_HOLD_MS = 600

const PetAvatar: React.FC<PetAvatarProps> = ({
  mood, size = 72, eventHandlers, onClick, active = true,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imagesRef = useRef<Partial<Record<SpriteKey, HTMLImageElement>>>({})
  const rafRef = useRef<number>(0)
  const particlesRef = useRef<Particle[]>([])
  const currentSpriteRef = useRef<SpriteKey>('idle')
  const prevMoodRef = useRef(mood)
  const transitionRef = useRef(0)
  const actionTimerRef = useRef(0)
  const isFlyingRef = useRef(false)
  const comboCountRef = useRef(0)
  const lastClickTimeRef = useRef(0)
  const resolvedOnClick = eventHandlers?.onClick ?? onClick
  const resolvedOnDblClick = eventHandlers?.onDoubleClick

  // Drag / flying tracking
  const mouseDownRef = useRef(false)
  const mousePosRef = useRef({ x: 0, y: 0, startX: 0, startY: 0 })
  const flyParticleTimerRef = useRef(0)

  // Load all sprites
  useEffect(() => {
    for (const entry of Object.entries(SPRITES)) {
      const key = entry[0] as SpriteKey
      const src = entry[1]
      const img = new Image()
      img.src = src
      img.onload = () => { imagesRef.current[key] = img }
    }
    return () => {
      for (const key of Object.keys(SPRITES)) {
        const img = imagesRef.current[key as SpriteKey]
        if (img) img.onload = null
      }
    }
  }, [])

  // ── Spawners ──
  const spawnCloudParticle = useCallback((x: number, y: number) => {
    particlesRef.current.push({
      x, y,
      vx: rand(-0.3, 0.3),
      vy: rand(-0.5, -0.1),
      life: 0,
      maxLife: rand(20, 50),
      size: rand(3, 8),
      color: 'rgba(200, 230, 255, 0.5)',
      opacity: 0.6,
    })
  }, [])

  const spawnSwordArc = useCallback((cx: number, cy: number) => {
    for (let i = 0; i < 3; i++) {
      const angle = rand(-0.6, 0.6) - Math.PI / 2
      particlesRef.current.push({
        x: cx, y: cy,
        vx: Math.cos(angle) * rand(1, 2.5),
        vy: Math.sin(angle) * rand(1, 2.5),
        life: 0,
        maxLife: rand(15, 30),
        size: rand(2, 4),
        color: 'rgba(100, 200, 255, 0.8)',
        opacity: 1,
      })
    }
  }, [])

  const spawnBlushHearts = useCallback((cx: number, cy: number) => {
    for (let i = 0; i < 5; i++) {
      const angle = rand(-1, 1) - Math.PI / 2
      particlesRef.current.push({
        x: cx + rand(-8, 8), y: cy,
        vx: Math.cos(angle) * rand(0.3, 0.8),
        vy: Math.sin(angle) * rand(0.5, 1.5),
        life: 0,
        maxLife: rand(30, 50),
        size: rand(2, 4),
        color: 'rgba(255, 150, 180, 0.8)',
        opacity: 1,
      })
    }
  }, [])

  // ── Click / Double-click handling ──
  const handleSingleClick = useCallback(() => {
    // 受惊拔剑: switch sprite briefly + spawn sword arcs
    currentSpriteRef.current = 'click_defend'
    actionTimerRef.current = ACTION_HOLD_MS
    spawnSwordArc(size / 2, size / 2)
    spawnSwordArc(size * 0.3, size * 0.4)
    resolvedOnClick?.()
  }, [resolvedOnClick, spawnSwordArc, size])

  const handleDoubleClick = useCallback(() => {
    // 害羞捂脸: switch sprite + blush hearts
    currentSpriteRef.current = 'shy'
    actionTimerRef.current = ACTION_HOLD_MS + 200
    spawnBlushHearts(size / 2, size * 0.3)
    spawnBlushHearts(size * 0.4, size * 0.25)
    spawnBlushHearts(size * 0.6, size * 0.25)
    resolvedOnDblClick?.()
  }, [resolvedOnDblClick, spawnBlushHearts, size])

  const handleCombo = useCallback(() => {
    // 三连击: fast sprite flash + burst
    comboCountRef.current = 0
    currentSpriteRef.current = 'click_defend'
    actionTimerRef.current = 400
    for (let i = 0; i < 8; i++) {
      const angle = rand(0, Math.PI * 2)
      const speed = rand(1.5, 4)
      particlesRef.current.push({
        x: size / 2, y: size / 2,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 0,
        maxLife: rand(20, 40),
        size: rand(2.5, 5),
        color: 'rgba(100, 200, 255, 0.9)',
        opacity: 1,
      })
    }
  }, [size])

  const handleClick = useCallback(() => {
    if (isFlyingRef.current) return
    const now = Date.now()
    const timeSinceLast = now - lastClickTimeRef.current

    if (timeSinceLast < DBL_CLICK_MS) {
      // Double-click detected
      comboCountRef.current = 0
      handleDoubleClick()
      lastClickTimeRef.current = 0
    } else {
      // First click — set timer to detect double-click
      lastClickTimeRef.current = now
      setTimeout(() => {
        if (lastClickTimeRef.current === now) {
          // No second click within the window → single click
          comboCountRef.current++
          if (comboCountRef.current >= 5) {
            handleCombo()
          } else {
            handleSingleClick()
          }
          lastClickTimeRef.current = 0
        }
      }, DBL_CLICK_MS)
    }
  }, [handleSingleClick, handleDoubleClick, handleCombo])

  // ── Mouse event handlers for drag→fly ──
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    mouseDownRef.current = true
    mousePosRef.current = {
      x: e.clientX, y: e.clientY,
      startX: e.clientX, startY: e.clientY,
    }
  }, [])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!mouseDownRef.current || imagesRef.current['flying'] === undefined) return
    const dx = e.clientX - mousePosRef.current.startX
    const dy = e.clientY - mousePosRef.current.startY
    const dist = Math.sqrt(dx * dx + dy * dy)

    if (dist > 8 && !isFlyingRef.current) {
      isFlyingRef.current = true
      currentSpriteRef.current = 'flying'
    }

    if (isFlyingRef.current) {
      mousePosRef.current.x = e.clientX
      mousePosRef.current.y = e.clientY
      // Spawn cloud trail along the path
      flyParticleTimerRef.current++
      if (flyParticleTimerRef.current % 2 === 0) {
        spawnCloudParticle(
          rand(size * 0.2, size * 0.8),
          size + rand(0, 4),
        )
      }
    }
  }, [size, spawnCloudParticle])

  const handleMouseUp = useCallback(() => {
    mouseDownRef.current = false
    if (isFlyingRef.current) {
      isFlyingRef.current = false
      currentSpriteRef.current = 'idle'
      // Landing puff
      for (let i = 0; i < 5; i++) {
        spawnCloudParticle(rand(size * 0.3, size * 0.7), size * 0.9)
      }
    }
  }, [size, spawnCloudParticle])

  // ── Main animation loop ──
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

      // Decay action timer
      if (actionTimerRef.current > 0) {
        actionTimerRef.current -= 16
        if (actionTimerRef.current <= 0) {
          actionTimerRef.current = 0
          if (!isFlyingRef.current) {
            currentSpriteRef.current = 'idle'
          }
        }
      }

      // Decay transition
      if (transitionRef.current > 0) {
        transitionRef.current += (0 - transitionRef.current) * 0.06
        if (transitionRef.current < 0.01) transitionRef.current = 0
      }

      // Update particles
      const particles = particlesRef.current
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i]
        p.x += p.vx; p.y += p.vy
        p.vy += 0.02
        p.life++
        if (p.life >= p.maxLife) { particles.splice(i, 1) }
      }

      // Determine sprite key
      let spriteKey = currentSpriteRef.current
      if (!isFlyingRef.current && actionTimerRef.current <= 0) {
        // Fall back to mood-based sprite if no action is playing
        if (mood === 'alert' && spriteKey !== 'click_defend') {
          spriteKey = 'alert'
        }
      }
      const sprite = imagesRef.current[spriteKey]

      // ── Draw ──
      ctx.clearRect(0, 0, size, size)

      // Draw sprite
      if (sprite) {
        ctx.save()

        if (mood === 'happy' && !isFlyingRef.current && actionTimerRef.current <= 0) {
          const bounce = Math.abs(Math.sin(t * 5)) * 3
          ctx.drawImage(sprite, 0, -bounce, size, size)
        } else if (mood === 'thinking' && !isFlyingRef.current && actionTimerRef.current <= 0) {
          const tilt = Math.sin(t * 1.5) * 0.05
          ctx.translate(size / 2, size / 2)
          ctx.rotate(tilt)
          ctx.drawImage(sprite, -size / 2, -size / 2, size, size)
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
        } else if (isFlyingRef.current) {
          // Flying: slight forward tilt
          ctx.translate(size / 2, size / 2)
          ctx.rotate(-0.08)
          ctx.drawImage(sprite, -size / 2, -size / 2, size, size)
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
        } else {
          // Idle breath
          const breath = Math.sin(t * 2.1) * 0.6
          ctx.drawImage(sprite, 0, breath, size, size)
        }

        // Transition flash
        if (transitionRef.current > 0.01) {
          ctx.fillStyle = `rgba(255, 255, 255, ${transitionRef.current * 0.15})`
          ctx.fillRect(0, 0, size, size)
        }

        ctx.restore()
      }

      // Draw particles
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
  }, [size, mood, spawnCloudParticle])

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

export default PetAvatar
