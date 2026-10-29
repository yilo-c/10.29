/**
 * 小问 精灵帧动画组件 — 加载帧序列，管理交互与动画循环
 */
import React, { useRef, useEffect, useCallback } from 'react'
import type { PetAvatarProps } from '../types/pet'
import { XIAOWEN_FALLBACK_FRAMES, XIAOWEN_POSE_CLIPS } from './xiaowenClips'
import { useXiaoWenPetMachine } from './useXiaoWenPetMachine'
import type { Particle, XiaoWenPose } from './xiaowenTypes'
import { decideXiaoWenAction } from './xiaowenDecisionEngine'
import {
  rand,
  spawnSwordSpark, spawnMist,
  updateParticles, drawParticles, drawFrame, drawFlightTrail, drawTransitionFlash,
} from './xiaowenRender'
import {
  loadLayerAssets, drawLayerIdle, createLayerAnimState,
  type LayerAssets,
} from './xiaowenLayerRenderer'

const PetXiaoWenAvatar: React.FC<PetAvatarProps> = ({
  mood, size = 240, eventHandlers, onClick, active = true,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number>(0)
  const particlesRef = useRef<Particle[]>([])
  const imagesRef = useRef<Record<string, HTMLImageElement>>({})
  const warnedFrameKeysRef = useRef<Set<string>>(new Set())
  const imagesLoadedRef = useRef(false)

  const prevMoodRef = useRef(mood)
  const transitionRef = useRef(0)
  const frameIndexRef = useRef(0)
  const frameTimerRef = useRef(0)
  const flyParticleTimerRef = useRef(0)
  const prevPoseRef = useRef<XiaoWenPose>('idle')
  const dragJustEndedRef = useRef(false)
  const resolvedOnClick = eventHandlers?.onClick ?? onClick

  // Procedural layer renderer (方案三): used for idle-equivalent poses
  const layerAssetsRef = useRef<LayerAssets | null>(null)
  const layerAnimRef = useRef(createLayerAnimState())
  useEffect(() => {
    let cancelled = false
    loadLayerAssets().then(assets => { if (!cancelled) layerAssetsRef.current = assets })
    return () => { cancelled = true }
  }, [])

  const pet = useXiaoWenPetMachine()
  const { refs } = pet

  // Map PetMood → XiaoWenAction for state machine triggers
  const prevMoodRef2 = useRef(mood)
  useEffect(() => {
    if (mood === prevMoodRef2.current) return
    prevMoodRef2.current = mood
    const decision = decideXiaoWenAction({ mood })
    if (decision.action) pet.trigger(decision.action)
  }, [mood, pet])

  // Load all sprite images
  useEffect(() => {
    const allFrames = [
      ...Object.values(XIAOWEN_POSE_CLIPS).flatMap(clip => clip.frames),
      ...XIAOWEN_FALLBACK_FRAMES,
    ]
    const uniqueFrames = Array.from(new Map(allFrames.map(frame => [frame.key, frame])).values())
    const images: HTMLImageElement[] = []
    let loaded = 0
    for (const { src, key } of uniqueFrames) {
      const img = new Image()
      img.src = src
      img.onload = () => {
        imagesRef.current[key] = img
        loaded++
        if (loaded >= uniqueFrames.length) imagesLoadedRef.current = true
      }
      img.onerror = () => {
        if (!warnedFrameKeysRef.current.has(key)) {
          warnedFrameKeysRef.current.add(key)
          console.warn(`[XiaoWen QA] Missing or invalid pet frame: ${src}`)
        }
        loaded++
        if (loaded >= uniqueFrames.length) imagesLoadedRef.current = true
      }
      images.push(img)
    }
    return () => {
      for (const img of images) {
        img.onload = null
        img.onerror = null
      }
    }
  }, [])

  // ── Particles ──
  const pSwordSpark = useCallback((x: number, y: number) => spawnSwordSpark(particlesRef.current, x, y), [])
  const pMist = useCallback((x: number, y: number) => spawnMist(particlesRef.current, x, y), [])

  // ── Click → state machine + particles ──
  const handleClick = useCallback(() => {
    const wasDrag = dragJustEndedRef.current
    dragJustEndedRef.current = false

    pet.onClick()
    if (wasDrag) return  // don't open chat after a drag

    pSwordSpark(size * 0.62, size * 0.35)
    pSwordSpark(size * 0.68, size * 0.5)
    resolvedOnClick?.()
  }, [pet, pSwordSpark, size, resolvedOnClick])

  // ── Drag → fly ──
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    pet.onMouseDown(e)
  }, [pet])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!refs.mouseDownRef.current) return
    const dx = e.clientX - refs.mouseStartRef.current.x
    const dy = e.clientY - refs.mouseStartRef.current.y

    if (dx * dx + dy * dy > 64 && !refs.isFlyingRef.current) {
      refs.isFlyingRef.current = true
      pet.trigger('dragFly')
    }
    if (refs.isFlyingRef.current) {
      flyParticleTimerRef.current++
      if (flyParticleTimerRef.current % 2 === 0) {
        pMist(rand(size * 0.2, size * 0.8), size)
      }
    }
  }, [pet, refs, pMist, size])

  const handleMouseUp = useCallback(() => {
    const wasFlying = refs.isFlyingRef.current
    pet.onMouseUp()
    if (wasFlying) {
      dragJustEndedRef.current = true
      for (let i = 0; i < 4; i++) pMist(rand(size * 0.3, size * 0.7), size * 0.9)
    }
  }, [pet, refs, pMist, size])

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

    let lastTime = 0

    const animate = (timestamp: number) => {
      const dt = lastTime ? Math.min(timestamp - lastTime, 50) : 16
      lastTime = timestamp
      const dtSec = dt / 1000

      // State machine tick (hold timer decay, auto idle)
      pet.tick(dt)

      // Reset frame index when pose changes
      if (refs.curPoseRef.current !== prevPoseRef.current) {
        prevPoseRef.current = refs.curPoseRef.current
        frameIndexRef.current = 0
        frameTimerRef.current = 0
      }

      // Frame animation
      const clip = XIAOWEN_POSE_CLIPS[refs.curPoseRef.current]
      if (clip) {
        const durations = Array.isArray(clip.durations) ? clip.durations : Array(clip.frames.length).fill(clip.durations)
        const currentDuration = durations[frameIndexRef.current] ?? durations[0]
        frameTimerRef.current += dt
        if (frameTimerRef.current >= currentDuration) {
          frameTimerRef.current = 0
          frameIndexRef.current++
          if (frameIndexRef.current >= clip.frames.length) {
            frameIndexRef.current = clip.loop ? 0 : clip.frames.length - 1
          }
        }
      }

      // Update particles
      updateParticles(particlesRef.current, dtSec)

      // Decay transition flash
      if (transitionRef.current > 0) {
        transitionRef.current += (0 - transitionRef.current) * 0.06
        if (transitionRef.current < 0.01) transitionRef.current = 0
      }

      // Draw
      ctx.clearRect(0, 0, size, size)

      if (refs.isFlyingRef.current) {
        ctx.save()
        ctx.translate(size / 2, size / 2)
        ctx.rotate(-0.08)
        ctx.translate(-size / 2, -size / 2)
      }

      // Frame with fallback
      if (clip) {
        // 图层渲染器(方案三)停用：待机改用视频抽帧序列 idle_ns
        const useLayers = false && layerAssetsRef.current
        if (useLayers && layerAssetsRef.current) {
          drawLayerIdle(ctx, size, layerAssetsRef.current, layerAnimRef.current, timestamp)
        } else {
          const frame = clip.frames[frameIndexRef.current] ?? clip.frames[0]
          const frameKey = frame?.key ?? ''
          const primaryImg = imagesRef.current[frameKey]
          if (!primaryImg && frameKey && !warnedFrameKeysRef.current.has(`fallback:${frameKey}`)) {
            warnedFrameKeysRef.current.add(`fallback:${frameKey}`)
            console.warn(`[XiaoWen QA] Falling back for missing pet frame: ${frameKey}`)
          }
          const img = primaryImg || imagesRef.current[clip.fallbackKey]
            || imagesRef.current.sprite_idle
            || imagesRef.current.xiaowen_fullbody
          if (img && imagesLoadedRef.current) {
            drawFrame(ctx, img, size)
          }
        }
      }

      if (refs.isFlyingRef.current) ctx.restore()

      // Flight trail
      if (refs.isFlyingRef.current) {
        drawFlightTrail(ctx, size)
      }

      // Transition flash
      drawTransitionFlash(ctx, transitionRef.current, size)

      // Particles
      drawParticles(ctx, particlesRef.current)

      if (document.hidden) {
        // 窗口隐藏时暂停动画，避免 ORB 宠物窗在后台持续烧 CPU
        cancelAnimationFrame(rafRef.current)
        return
      }
      rafRef.current = requestAnimationFrame(animate)
    }

    const onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(rafRef.current)
      } else if (canvas && !rafRef.current) {
        // 重新可见时恢复动画循环（仅当当前没有排帧时重启，防止重复）
        rafRef.current = requestAnimationFrame(animate)
      }
    }
    document.addEventListener('visibilitychange', onVisibility)

    // 若在 effect 挂载时窗口已隐藏（如从不显式 show 过），则不起循环
    if (!document.hidden) {
      rafRef.current = requestAnimationFrame(animate)
    }

    return () => {
      cancelAnimationFrame(rafRef.current)
      document.removeEventListener('visibilitychange', onVisibility)
    }
    // Stable deps: size and mood can change; refs are stable ref objects
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, mood])

  return (
    <div
      style={{
        width: size + 48,
        height: size + 76,
        position: 'relative',
        opacity: active ? 1 : 0.4,
        transition: 'opacity 0.3s',
      }}
    >
      {pet.line && (
        <div
          style={{
            position: 'absolute',
            left: 12,
            top: 0,
            width: size + 24,
            minHeight: 40,
            padding: '8px 10px',
            borderRadius: 10,
            border: '1px solid rgba(121, 207, 255, 0.34)',
            background: 'rgba(9, 18, 29, 0.78)',
            color: 'rgba(239, 248, 255, 0.95)',
            fontSize: 12,
            lineHeight: 1.45,
            boxShadow: '0 10px 28px rgba(0,0,0,0.28), 0 0 18px rgba(90, 190, 255, 0.12)',
            backdropFilter: 'blur(14px)',
            pointerEvents: 'none',
          }}
        >
          {pet.line}
        </div>
      )}
      <canvas
        ref={canvasRef}
        style={{
          cursor: 'pointer',
          display: 'block',
          position: 'absolute',
          left: 24,
          bottom: 0,
        }}
        onClick={handleClick}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      />
    </div>
  )
}

export default PetXiaoWenAvatar
