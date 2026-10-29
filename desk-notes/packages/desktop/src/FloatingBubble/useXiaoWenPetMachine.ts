import { useRef, useState, useCallback, useEffect } from 'react'
import { getFallbackLine, getXiaoWenLine } from './xiaowenDialogue'
import type { XiaoWenAction, XiaoWenPose, PetMachine } from './xiaowenTypes'

// ── Constants ──
const DBL_CLICK_MS = 280
const COMBO_TIMEOUT_MS = 900
const HOLD_CLICK_DEFEND = 600
const HOLD_SHY = 800
const HOLD_COMBO = 400
const HOLD_LAND = 1200
const IDLE_DIALOGUE_INTERVAL = 24000
const IDLE_INTERLUDE_MIN_MS = 9000
const IDLE_INTERLUDE_MAX_MS = 18000
/** 待机随机插曲动作（眨眼由图层渲染器程序化处理，这里只做帧序列插曲） */
const IDLE_INTERLUDES: readonly XiaoWenAction[] = ['glance', 'hmph']
/** 眨眼专属节拍（真人眨眼约 2~10s 一次；桌宠恬静档取 5~10s，与偷瞄/甩发互不挤占） */
const BLINK_MIN_MS = 5000
const BLINK_MAX_MS = 10000

/** Priority: lower number = higher priority */
const ACTION_PRIORITY: Record<XiaoWenAction, number> = {
  dragFly: 1,
  combo5: 2,
  combo3: 3,
  clickDefend: 4,
  shy: 4,
  land: 5,
  glance: 5,
  hmph: 5,
  blink: 5,
  chatOpen: 6,
  notice: 7,
  sleepy: 8,
  startup: 9,
  idle: 10,
}

/** Map XiaoWenAction → XiaoWenPose */
const ACTION_TO_POSE: Record<XiaoWenAction, XiaoWenPose> = {
  idle: 'idle',
  clickDefend: 'clickDefend',
  shy: 'shy',
  dragFly: 'dragFly',
  land: 'land',
  combo3: 'combo3',
  combo5: 'combo3',
  chatOpen: 'chatOpen',
  notice: 'notice',
  glance: 'glance',
  hmph: 'hmph',
  blink: 'blink',
  sleepy: 'idle',
  startup: 'idle',
}

/** Hold duration for non-looping poses before auto-return to idle */
const ACTION_HOLD_MS: Partial<Record<XiaoWenAction, number>> = {
  clickDefend: HOLD_CLICK_DEFEND,
  shy: HOLD_SHY,
  land: HOLD_LAND,
  combo3: HOLD_COMBO,
  combo5: HOLD_COMBO,
  glance: 1700,
  hmph: 1800,
  blink: 550,
}

export function useXiaoWenPetMachine(): PetMachine {
  const [line, setLine] = useState<string | null>(() => getFallbackLine('startup'))

  const curActionRef = useRef<XiaoWenAction>('idle')
  const curPoseRef = useRef<XiaoWenPose>('idle')
  const holdTimerRef = useRef(0)
  const isFlyingRef = useRef(false)
  const mouseDownRef = useRef(false)
  const mouseStartRef = useRef({ x: 0, y: 0 })

  // Combo tracking
  const comboCountRef = useRef(0)
  const lastClickTimeRef = useRef(0)
  const comboActiveRef = useRef(false)

  // Idle interlude countdown (glance / hmph)
  const interludeAtRef = useRef(
    IDLE_INTERLUDE_MIN_MS + Math.random() * (IDLE_INTERLUDE_MAX_MS - IDLE_INTERLUDE_MIN_MS),
  )
  // Blink-only countdown（独立节拍，5~10s 随机）
  const blinkAtRef = useRef(
    BLINK_MIN_MS + Math.random() * (BLINK_MAX_MS - BLINK_MIN_MS),
  )

  // Dismiss timer for dialogue
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout>>()

  const say = useCallback((action: XiaoWenAction) => {
    getXiaoWenLine(action).then(text => {
      setLine(text)
      clearTimeout(dismissTimerRef.current)
      const duration = text.length > 25 ? 4500 : text.length > 15 ? 3500 : 2400
      dismissTimerRef.current = setTimeout(() => setLine(null), duration)
    })
  }, [])

  const go = useCallback((action: XiaoWenAction) => {
    if (ACTION_PRIORITY[action] > ACTION_PRIORITY[curActionRef.current]) return

    curActionRef.current = action
    curPoseRef.current = ACTION_TO_POSE[action]
    holdTimerRef.current = ACTION_HOLD_MS[action] ?? 0
    say(action)
  }, [say])

  // Idle dialogue timer
  useEffect(() => {
    const timer = setInterval(() => {
      if (curActionRef.current === 'idle' && !isFlyingRef.current) {
        say('idle')
      }
    }, IDLE_DIALOGUE_INTERVAL)
    return () => clearInterval(timer)
  }, [say])

  // Cleanup
  useEffect(() => {
    return () => clearTimeout(dismissTimerRef.current)
  }, [])

  // Called from animation loop each frame
  const tick = useCallback((dt: number) => {
    if (holdTimerRef.current > 0) {
      holdTimerRef.current -= dt
      if (holdTimerRef.current <= 0) {
        holdTimerRef.current = 0
        if (!isFlyingRef.current) {
          curActionRef.current = 'idle'
          curPoseRef.current = 'idle'
        }
      }
    }

    // Idle interlude: 偷瞄/甩发（直接切状态不刷台词，保持轻描淡写）
    if (interludeAtRef.current > 0) {
      interludeAtRef.current -= dt
      if (interludeAtRef.current <= 0) {
        interludeAtRef.current =
          IDLE_INTERLUDE_MIN_MS + Math.random() * (IDLE_INTERLUDE_MAX_MS - IDLE_INTERLUDE_MIN_MS)
        if (curActionRef.current === 'idle' && !isFlyingRef.current) {
          const pick = IDLE_INTERLUDES[Math.floor(Math.random() * IDLE_INTERLUDES.length)] ?? 'glance'
          curActionRef.current = pick
          curPoseRef.current = ACTION_TO_POSE[pick]
          holdTimerRef.current = ACTION_HOLD_MS[pick] ?? 0
        }
      }
    }

    // Blink 独立节拍：仅待机时触发眨眼插曲（不挤占 glance/hmph）
    if (blinkAtRef.current > 0) {
      blinkAtRef.current -= dt
      if (blinkAtRef.current <= 0) {
        blinkAtRef.current = BLINK_MIN_MS + Math.random() * (BLINK_MAX_MS - BLINK_MIN_MS)
        if (curActionRef.current === 'idle' && !isFlyingRef.current) {
          curActionRef.current = 'blink'
          curPoseRef.current = 'blink'
          holdTimerRef.current = ACTION_HOLD_MS.blink ?? 0
        }
      }
    }
  }, [])

  // ── Click ──
  const handleComboAction = useCallback((count: number) => {
    if (count >= 5) go('combo5')
    else if (count >= 3) go('combo3')
    else go('clickDefend')
  }, [go])

  const onClick = useCallback(() => {
    if (isFlyingRef.current) return
    const now = Date.now()
    const dt = now - lastClickTimeRef.current

    if (dt < DBL_CLICK_MS) {
      comboCountRef.current = 0
      comboActiveRef.current = true
      go('shy')
      lastClickTimeRef.current = 0
    } else {
      lastClickTimeRef.current = now
      setTimeout(() => {
        if (lastClickTimeRef.current === now && !comboActiveRef.current) {
          comboCountRef.current++
          handleComboAction(comboCountRef.current)
          setTimeout(() => { comboCountRef.current = 0 }, COMBO_TIMEOUT_MS)
        }
        comboActiveRef.current = false
        lastClickTimeRef.current = 0
      }, DBL_CLICK_MS)
    }
  }, [go, handleComboAction])

  const onDoubleClick = useCallback(() => {
    go('shy')
  }, [go])

  // ── Drag ──
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    mouseDownRef.current = true
    mouseStartRef.current = { x: e.clientX, y: e.clientY }
  }, [])

  const onMouseMove = useCallback(() => {
    // Drag threshold check handled by component (needs canvas ref)
  }, [])

  const onMouseUp = useCallback(() => {
    mouseDownRef.current = false
    if (isFlyingRef.current) {
      isFlyingRef.current = false
      // Bypass go() priority check — dragFly has highest priority (1),
      // so go('land') (priority 5) would be blocked.
      curActionRef.current = 'land'
      curPoseRef.current = 'land'
      holdTimerRef.current = HOLD_LAND
      say('land')
      comboCountRef.current = 0
    }
  }, [say])

  // External trigger (mood events, notifications)
  const trigger = useCallback((action: XiaoWenAction) => {
    go(action)
  }, [go])

  return {
    refs: {
      curPoseRef,
      isFlyingRef,
      holdTimerRef,
      mouseDownRef,
      mouseStartRef,
    },
    line,
    tick,
    onClick,
    onDoubleClick,
    onMouseDown,
    onMouseMove,
    onMouseUp,
    trigger,
  }
}
