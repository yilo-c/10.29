/**
 * 小问 切层骨骼动画引擎
 *
 * 将角色分为 body / head_hair / arm_left / arm_right 四层，
 * 每层独立变换，实现真正的骨骼动画。
 *
 * 渲染管线:
 *   1. 骨骼状态更新（呼吸/摆臂/头发波动/眨眼）
 *   2. 按 z-order 逐层绘制（含姿态过渡淡入淡出）
 *   3. 叠加表情覆盖（脸红、剑意）
 *   4. 粒子特效（云气/剑意/爱心）
 */
import React, { useRef, useEffect, useCallback } from 'react'
import type { PetAvatarProps } from '../../types/pet'

type PoseKey = 'idle' | 'defend' | 'shy' | 'fly' | 'alert'

interface Particle {
  x: number; y: number; vx: number; vy: number
  life: number; maxLife: number; size: number
  color: string; opacity: number
}

interface BoneLayer {
  img: HTMLImageElement | null
  offsetX: number
  offsetY: number
  w: number
  h: number
  pivotX: number
  pivotY: number
  z: number
}

interface LayerAnimState {
  translateX: number
  translateY: number
  rotate: number
  scaleX: number
  scaleY: number
}

interface BoneState {
  breathePhase: number
  blinkTimer: number
  isBlinking: boolean
  poseOpacity: Record<PoseKey, number>
  targetPose: PoseKey
  transitionSpeed: number
  layerAnims: Record<string, LayerAnimState>
}

function rand(min: number, max: number) { return Math.random() * (max - min) + min }

const DBL_CLICK_MS = 280
const ACTION_HOLD_MS = 600
const TRANSITION_SPEED = 0.08

const LAYER_KEYS = ['body', 'arm_right', 'arm_left', 'head_hair'] as const

// 每层默认动画值（各姿态的目标值在 POSE_ANIMS 中定义）
// 坐标系：512x512 虚拟画布，最终缩放到实际 size
interface PoseLayerAnim {
  tx?: number; ty?: number; rot?: number; sx?: number; sy?: number
}

const POSE_ANIMS: Record<PoseKey, Record<string, PoseLayerAnim>> = {
  idle: {
    body:      { sx: 1, sy: 1 },
    arm_right: { rot: 0.0 },
    arm_left:  { rot: 0.0 },
    head_hair: { ty: 0 },
  },
  defend: {
    body:      { sx: 0.96, sy: 0.97 },
    arm_right: { rot: -0.35 },  // 剑举起
    arm_left:  { rot: 0.06 },   // 左臂护身
    head_hair: { ty: 1, rot: 0.02 },
  },
  shy: {
    body:      { rot: 0.03 },
    arm_right: { rot: 0.15, tx: -3 },  // 右臂收拢
    arm_left:  { rot: -0.1, tx: 3 },   // 左臂收拢
    head_hair: { rot: -0.04, ty: 2 },
  },
  fly: {
    body:      { rot: -0.08 },
    arm_right: { rot: 0.2, tx: 5 },    // 展开
    arm_left:  { rot: -0.15, tx: -5 }, // 展开
    head_hair: { rot: -0.03, ty: -1 },
  },
  alert: {
    body:      { sx: 1.02, sy: 1.02 },
    arm_right: { rot: -0.15 },
    arm_left:  { rot: 0.1 },
    head_hair: { rot: -0.02 },
  },
}

const PetLayerAvatar: React.FC<PetAvatarProps> = ({
  mood, size = 72, eventHandlers, onClick, active = true,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number>(0)
  const particlesRef = useRef<Particle[]>([])
  const imagesLoaded = useRef(false)

  const layerRefs = useRef<Record<string, BoneLayer>>({
    body:      { img: null, offsetX: 171, offsetY: 193, w: 317, h: 309, pivotX: 158, pivotY: 0,   z: 1 },
    arm_right: { img: null, offsetX: 339, offsetY: 188, w: 20,  h: 144, pivotX: -10, pivotY: 10,  z: 2 },
    arm_left:  { img: null, offsetX: 154, offsetY: 188, w: 165, h: 144, pivotX: 168, pivotY: 10,  z: 2 },
    head_hair: { img: null, offsetX: 156, offsetY: 53,  w: 199, h: 155, pivotX: 173, pivotY: 143, z: 3 },
  })

  const boneRef = useRef<BoneState>({
    breathePhase: 0,
    blinkTimer: 0,
    isBlinking: false,
    poseOpacity: { idle: 1, defend: 0, shy: 0, fly: 0, alert: 0 },
    targetPose: 'idle',
    transitionSpeed: TRANSITION_SPEED,
    layerAnims: {
      body:      { translateX: 0, translateY: 0, rotate: 0, scaleX: 1, scaleY: 1 },
      arm_right: { translateX: 0, translateY: 0, rotate: 0, scaleX: 1, scaleY: 1 },
      arm_left:  { translateX: 0, translateY: 0, rotate: 0, scaleX: 1, scaleY: 1 },
      head_hair: { translateX: 0, translateY: 0, rotate: 0, scaleX: 1, scaleY: 1 },
    },
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

  // Load layer images
  useEffect(() => {
    const layers = layerRefs.current
    const keys = Object.keys(layers) as Array<keyof typeof layers>
    let loaded = 0
    for (const key of keys) {
      const img = new Image()
      img.src = `/pet/layers/${key}.png`
      img.onload = () => {
        layers[key].img = img
        loaded++
        if (loaded === keys.length) imagesLoaded.current = true
      }
      img.onerror = () => {
        // 加载失败时使用占位
        console.warn(`PetLayer: ${key} failed to load`)
        loaded++
        if (loaded === keys.length) imagesLoaded.current = true
      }
    }
    return () => {
      for (const key of keys) {
        const l = layers[key]
        if (l.img) l.img.onload = null
      }
    }
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
    boneRef.current.targetPose = pose
    if (holdMs > 0) actionTimerRef.current = holdMs
  }, [])

  // ── Click handling ──
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

  // ── 绘制单层 ──
  const drawLayer = (
    ctx: CanvasRenderingContext2D,
    layer: BoneLayer,
    anim: LayerAnimState,
    scale: number,
  ) => {
    if (!layer.img) return
    const { offsetX, offsetY, w, h, pivotX, pivotY } = layer
    const { translateX, translateY, rotate, scaleX, scaleY } = anim

    ctx.save()
    ctx.translate(
      (offsetX + pivotX + translateX) * scale,
      (offsetY + pivotY + translateY) * scale,
    )
    ctx.rotate(rotate)
    ctx.scale(scaleX, scaleY)
    ctx.drawImage(
      layer.img,
      -pivotX * scale, -pivotY * scale,
      w * scale, h * scale,
    )
    ctx.restore()
  }

  // ── 更新骨骼动画目标值 ──
  const updateLayerAnimTargets = (bone: BoneState) => {
    const pose = bone.targetPose
    const anims = POSE_ANIMS[pose]
    const curAnims = bone.layerAnims

    for (const key of LAYER_KEYS) {
      const target = anims[key] || {}
      const cur = curAnims[key]
      // 呼吸叠加（仅 idle 和部分姿态）
      const breatheCycle = Math.sin(bone.breathePhase) * 0.6

      // lerp 到目标值
      const speed = 0.1
      cur.translateX += ((target.tx || 0) - cur.translateX) * speed
      cur.translateY += (((target.ty || 0) + breatheCycle) - cur.translateY) * speed
      cur.rotate    += ((target.rot || 0) - cur.rotate) * speed
      cur.scaleX    += ((target.sx || 1) - cur.scaleX) * speed
      cur.scaleY    += ((target.sy || 1) - cur.scaleY) * speed
    }
  }

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

    // 收集按 z 排序的层
    const sortedLayers = [...LAYER_KEYS].sort(
      (a, b) => layerRefs.current[a].z - layerRefs.current[b].z
    )

    const animate = (ts: number) => {
      const t = ts / 1000
      const bone = boneRef.current
      const scale = size / 512

      // ── 骨骼更新 ──
      bone.breathePhase = t * 2.1

      // Blink
      bone.blinkTimer += 16
      if (bone.blinkTimer > 3000 + Math.random() * 2000) {
        bone.isBlinking = true
        if (bone.blinkTimer > 3100) {
          bone.isBlinking = false
          bone.blinkTimer = 0
        }
      }

      // ── Pose timing ──
      const isAction = actionTimerRef.current > 0
      if (isAction) {
        actionTimerRef.current -= 16
        if (actionTimerRef.current <= 0) {
          actionTimerRef.current = 0
          if (!isFlyingRef.current) bone.targetPose = 'idle'
        }
      }

      // ── 过渡所有姿态的opacity ──
      for (const key of Object.keys(bone.poseOpacity) as PoseKey[]) {
        const target = key === bone.targetPose ? 1 : 0
        const diff = target - bone.poseOpacity[key]
        bone.poseOpacity[key] += diff * bone.transitionSpeed
        if (Math.abs(bone.poseOpacity[key]) < 0.01) bone.poseOpacity[key] = 0
        if (bone.poseOpacity[key] > 0.99) bone.poseOpacity[key] = 1
      }

      // ── 更新骨骼层动画 ──
      updateLayerAnimTargets(bone)

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

      if (imagesLoaded.current) {
        // 飞行倾斜（全局叠加）
        const flyTilt = isFlyingRef.current ? -0.08 : 0

        ctx.save()
        if (flyTilt !== 0) {
          ctx.translate(size / 2, size / 2)
          ctx.rotate(flyTilt)
          ctx.translate(-size / 2, -size / 2)
        }

        // 按 z-order 绘制各层
        for (const layerKey of sortedLayers) {
          const layer = layerRefs.current[layerKey]
          const anim = bone.layerAnims[layerKey]
          drawLayer(ctx, layer, anim, scale)
        }

        ctx.restore()

        // ── 表情覆盖（在完整画布上绘制） ──

        // 剑意覆盖 (defend 时)
        if (bone.poseOpacity['defend'] > 0.1) {
          const glowAlpha = bone.poseOpacity['defend'] * 0.1
          ctx.fillStyle = `rgba(100, 200, 255, ${glowAlpha})`
          ctx.fillRect(0, 0, size, size)

          // 剑光弧线
          ctx.strokeStyle = `rgba(180, 240, 255, ${glowAlpha * 0.6})`
          ctx.lineWidth = 2
          ctx.beginPath()
          ctx.arc(size * 0.7, size * 0.3, size * 0.3, -0.8, 0.5)
          ctx.stroke()
          ctx.beginPath()
          ctx.arc(size * 0.7, size * 0.3, size * 0.2, -0.5, 0.3)
          ctx.stroke()
        }

        // 脸红 (shy 时) — 在头部区域绘制
        if (bone.poseOpacity['shy'] > 0.1) {
          const shyAlpha = bone.poseOpacity['shy'] * 0.15
          const cheekSize = size * 0.06
          const cheekY = size * 0.3
          // 左脸颊
          ctx.fillStyle = `rgba(255, 140, 160, ${shyAlpha})`
          ctx.beginPath()
          ctx.ellipse(size * 0.32, cheekY, cheekSize * 1.2, cheekSize, 0, 0, Math.PI * 2)
          ctx.fill()
          // 右脸颊
          ctx.beginPath()
          ctx.ellipse(size * 0.62, cheekY, cheekSize * 1.2, cheekSize, 0, 0, Math.PI * 2)
          ctx.fill()
        }

        // 眨眼（在头部区域绘制半透明线）
        if (bone.isBlinking && bone.poseOpacity['idle'] > 0.3) {
          ctx.strokeStyle = 'rgba(0,0,0,0.6)'
          ctx.lineWidth = 1.5
          const eyeY = size * 0.215
          // 左眼
          ctx.beginPath()
          ctx.moveTo(size * 0.37, eyeY)
          ctx.lineTo(size * 0.43, eyeY)
          ctx.stroke()
          // 右眼
          ctx.beginPath()
          ctx.moveTo(size * 0.55, eyeY)
          ctx.lineTo(size * 0.61, eyeY)
          ctx.stroke()
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
      }

      // ── Draw particles (always) ──
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

export default PetLayerAvatar
