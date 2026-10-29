/**
 * 小问 骨骼动画渲染引擎
 *
 * 方案：全图渲染 + Canvas 2D 变形 + 多姿态交叉淡入淡出
 *
 * 每帧渲染管线:
 *   1. 更新骨骼状态（呼吸/头发摆动/剑的倾斜）
 *   2. 绘制当前姿态图（带淡入淡出过渡）
 *   3. 叠加粒子特效（云气/剑意/爱心）
 *   4. 物理动效（头发摇摆、衣摆飘动是基于函数的伪物理）
 */
import React, { useRef, useEffect, useCallback } from 'react'
import type { PetAvatarProps } from '../../types/pet'

type PoseKey = 'idle' | 'defend' | 'shy' | 'fly' | 'alert'

interface Particle {
  x: number; y: number; vx: number; vy: number
  life: number; maxLife: number; size: number
  color: string; opacity: number
}

interface BoneState {
  breathePhase: number      // 呼吸相位 0-2PI
  hairSway: number          // 头发摆动偏移
  swordTilt: number         // 剑的倾斜角
  blinkTimer: number        // 眨眼计时
  isBlinking: boolean
  poseOpacity: Record<PoseKey, number>  // 姿态淡入淡出
  currentPose: PoseKey
  targetPose: PoseKey
  transitionSpeed: number
}

function rand(min: number, max: number) { return Math.random() * (max - min) + min }

const DBL_CLICK_MS = 280
const ACTION_HOLD_MS = 600
const TRANSITION_SPEED = 0.08  // 每帧淡入淡出速度

const PetBoneAvatar: React.FC<PetAvatarProps> = ({
  mood, size = 72, eventHandlers, onClick, active = true,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const rafRef = useRef<number>(0)
  const particlesRef = useRef<Particle[]>([])
  const boneRef = useRef<BoneState>({
    breathePhase: 0,
    hairSway: 0,
    swordTilt: 0,
    blinkTimer: 0,
    isBlinking: false,
    poseOpacity: { idle: 1, defend: 0, shy: 0, fly: 0, alert: 0 },
    currentPose: 'idle',
    targetPose: 'idle',
    transitionSpeed: TRANSITION_SPEED,
  })
  const prevMoodRef = useRef(mood)
  const transitionRef = useRef(0)
  const actionTimerRef = useRef(0)
  const isFlyingRef = useRef(false)
  const comboCountRef = useRef(0)
  const lastClickTimeRef = useRef(0)
  const resolvedOnClick = eventHandlers?.onClick ?? onClick
  const resolvedOnDblClick = eventHandlers?.onDoubleClick

  // Drag tracking
  const mouseDownRef = useRef(false)
  const mouseStartRef = useRef({ x: 0, y: 0 })
  const flyParticleTimerRef = useRef(0)

  // Multi-pose sprite images
  const poseImagesRef = useRef<Record<PoseKey, HTMLImageElement | null>>({
    idle: null, defend: null, shy: null, fly: null, alert: null,
  })
  const imagesLoadedRef = useRef(false)

  // Load all pose sprites
  useEffect(() => {
    const poseFiles: Record<PoseKey, string> = {
      idle:   '/pet/sprite_idle.png',
      defend: '/pet/sprite_defend.png',
      shy:    '/pet/sprite_shy.png',
      fly:    '/pet/sprite_fly.png',
      alert:  '/pet/sprite_alert.png',
    }
    let loaded = 0
    const total = Object.keys(poseFiles).length
    for (const [key, src] of Object.entries(poseFiles)) {
      const img = new Image()
      img.src = src
      img.onload = () => {
        poseImagesRef.current[key as PoseKey] = img
        loaded++
        if (loaded === total) {
          imagesLoadedRef.current = true
          imgRef.current = img  // Set imgRef for backward compatibility
        }
      }
      img.onerror = () => {
        // Fallback: use xiaowen_fullbody for any failed pose
        loaded++
        if (loaded >= total) imagesLoadedRef.current = true
      }
    }
    // Always load xiaowen_fullbody as global fallback
    const fallback = new Image()
    fallback.src = '/pet/xiaowen_fullbody.png'
    fallback.onload = () => { if (!imgRef.current) imgRef.current = fallback }
    return () => { fallback.onload = null }
  }, [])

  // Get the best available image for a pose
  const getPoseImage = useCallback((pose: PoseKey): HTMLImageElement | null => {
    return poseImagesRef.current[pose] || imgRef.current
  }, [])

  // ── Particle spawners ──
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

  // ── Pose transition ──
  const setPose = useCallback((pose: PoseKey, holdMs: number = 0) => {
    const bone = boneRef.current
    bone.targetPose = pose
    if (holdMs > 0) {
      actionTimerRef.current = holdMs
    }
  }, [])

  // ── Click handling ──
  const handleSingleClick = useCallback(() => {
    setPose('defend', ACTION_HOLD_MS)
    spawnSwordSpark(size * 0.6, size * 0.3)
    spawnSwordSpark(size * 0.7, size * 0.4)
    resolvedOnClick?.()
  }, [resolvedOnClick, spawnSwordSpark, size, setPose])

  const handleDoubleClick = useCallback(() => {
    setPose('shy', ACTION_HOLD_MS + 200)
    spawnHearts(size * 0.4, size * 0.2)
    spawnHearts(size * 0.6, size * 0.25)
    resolvedOnDblClick?.()
  }, [resolvedOnDblClick, spawnHearts, size, setPose])

  const handleCombo = useCallback(() => {
    comboCountRef.current = 0
    setPose('alert', 400)
    for (let i = 0; i < 10; i++) {
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

  // ── Mouse drag → flying ──
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

  // ── Main render loop ──
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
      const bone = boneRef.current

      // ── 骨骼更新 ──
      bone.breathePhase = t * 2.1
      bone.hairSway = Math.sin(t * 1.8) * Math.PI * 0.03
      bone.swordTilt = Math.sin(t * 0.5) * 0.02

      // Blink
      bone.blinkTimer += 16
      if (bone.blinkTimer > 3000 + Math.random() * 2000) {
        bone.isBlinking = true
        if (bone.blinkTimer > 3100) {
          bone.isBlinking = false
          bone.blinkTimer = 0
        }
      }

      // ── Pose transition (淡入淡出) ──
      const isAction = actionTimerRef.current > 0
      if (isAction) {
        actionTimerRef.current -= 16
        if (actionTimerRef.current <= 0) {
          actionTimerRef.current = 0
          if (!isFlyingRef.current) bone.targetPose = 'idle'
        }
      }

      // 过渡所有姿态的opacity
      for (const key of Object.keys(bone.poseOpacity) as PoseKey[]) {
        const target = key === bone.targetPose ? 1 : 0
        const diff = target - bone.poseOpacity[key]
        bone.poseOpacity[key] += diff * bone.transitionSpeed
        if (Math.abs(bone.poseOpacity[key]) < 0.01) bone.poseOpacity[key] = 0
        if (bone.poseOpacity[key] > 0.99) bone.poseOpacity[key] = 1
      }
      bone.currentPose = bone.targetPose

      // ── Decay mood transition flash ──
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

      const sprite = imgRef.current
      if (sprite) {
        // 呼吸浮动
        const breathOffset = Math.sin(bone.breathePhase) * 0.8
        // 飞行倾斜
        const flyTilt = isFlyingRef.current ? -0.08 : 0

        // 小尺寸（<=100 Orb 模式）裁剪到上半身，否则全身
        const c = size <= 100
          ? { sx: 120, sy: 20, sw: 270, sh: 240 }
          : { sx: 0, sy: 0, sw: 512, sh: 512 }

        // 按姿态合成（多姿态淡入淡出）
        const activePoses = (Object.keys(bone.poseOpacity) as PoseKey[])
          .filter(k => bone.poseOpacity[k] > 0.01)

        for (const pose of activePoses) {
          const opacity = bone.poseOpacity[pose]
          const poseImg = getPoseImage(pose)
          if (!poseImg) continue
          ctx.save()
          ctx.globalAlpha = opacity

          if (pose === 'fly') {
            ctx.translate(size / 2, size / 2)
            ctx.rotate(flyTilt)
            ctx.drawImage(poseImg, c.sx, c.sy, c.sw, c.sh, -size / 2, -size / 2, size, size)
          } else if (pose === 'defend') {
            // 受惊：稍微后缩
            ctx.translate(size / 2, size / 2)
            ctx.scale(0.95, 0.95)
            ctx.drawImage(poseImg, c.sx, c.sy, c.sw, c.sh, -size / 2, -size / 2, size, size)
          } else if (pose === 'shy') {
            // 害羞：微微侧倾
            ctx.translate(size / 2, size / 2)
            ctx.rotate(0.04)
            ctx.drawImage(poseImg, c.sx, c.sy, c.sw, c.sh, -size / 2, -size / 2, size, size)
          } else {
            // idle / alert — 标准呼吸
            ctx.drawImage(poseImg, c.sx, c.sy, c.sw, c.sh, 0, breathOffset, size, size)
          }

          ctx.restore()
        }

        // 表情覆盖（通过Canvas绘图模拟眨眼/脸红）
        // 脸红 (shy 时)
        if (bone.poseOpacity['shy'] > 0.1) {
          const shyAlpha = bone.poseOpacity['shy'] * 0.12
          ctx.fillStyle = `rgba(255, 140, 160, ${shyAlpha})`
          ctx.fillRect(size * 0.15, size * 0.25, size * 0.3, size * 0.15)
        }

        // 剑意覆盖 (defend 时)
        if (bone.poseOpacity['defend'] > 0.1) {
          const glowAlpha = bone.poseOpacity['defend'] * 0.08
          ctx.fillStyle = `rgba(100, 200, 255, ${glowAlpha})`
          ctx.fillRect(0, 0, size, size)
        }
      }

      // ── 绘制粒子 ──
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

export default PetBoneAvatar
