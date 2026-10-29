import React, { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import PetXiaoWenAvatar from './PetXiaoWenAvatar'
import { usePetState } from './usePetState'
import { useBackendEvents } from './useBackendEvents'
import TaskProgress, { type TaskProgressData } from './TaskProgress'
import { useOrbVoiceInput } from './useOrbVoiceInput'
import { useDesktopNotifications } from './useDesktopNotifications'

/**
 * FloatingOrbApp — standalone pet window.
 *
 * Draggable transparent window containing the XiaoWen pet avatar.
 * Click opens the floating chat window via IPC.
 */
const FloatingOrbApp: React.FC = () => {
  const ei = window.electronAPI as ElectronAPI | undefined
  const { mood, setMood } = usePetState()
  const { lastEvent } = useBackendEvents()

  // 第二档 #6：长按录音 STT。短按/拖动保持原手势；长按超过阈值才进入录音。
  const LONGPRESS_MS = 400
  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const voiceActiveRef = useRef(false)
  const { recording, startVoice, stopVoice } = useOrbVoiceInput({
    transcribe: ei?.transcribe?.transcribeBase64,
    onVoiceResult: (text: string) => {
      // 跨窗口把转写文本投递到聊天输入框（经主进程 IPC → 聊天窗 siyi:chat-draft）
      ei?.openFloatingChat?.()
      ei?.sendVoiceDraft?.(text)
    },
  })

  // Desktop notifications for backend events
  useDesktopNotifications()

  // Track task.progress events
  const prevSeqRef = useRef(0)
  const [currentProgress, setCurrentProgress] = useState<TaskProgressData | null>(null)
  const progressTimerRef = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => {
    const ev = lastEvent('task.progress')
    const payload = ev?.payload as TaskProgressData | undefined
    if (!payload) return
    // Only update if seq is newer (prevents stale events from overwriting)
    if (payload.seq && prevSeqRef.current && payload.seq <= prevSeqRef.current) return
    prevSeqRef.current = payload.seq ?? 0
    setCurrentProgress(payload)
    // Clear progress 3s after completion or error
    if (payload.step >= payload.total || payload.step <= 0) {
      clearTimeout(progressTimerRef.current)
      progressTimerRef.current = setTimeout(() => setCurrentProgress(null), 3000)
    }
    return () => { clearTimeout(progressTimerRef.current) }
  }, [lastEvent])

  // Sync orb mood with backend mood.changed events — 后端 mood 持久生效，不再 6 秒重置
  const prevMoodRef = useRef(mood)
  useEffect(() => {
    const ev = lastEvent('mood.changed')
    const newMood = ev?.payload?.mood as string | undefined
    const VALID_MOODS: readonly string[] = ['idle', 'listening', 'thinking', 'done', 'happy', 'alert', 'notification', 'sleeping']
    if (newMood && (VALID_MOODS as readonly string[]).includes(newMood) && newMood !== prevMoodRef.current) {
      setMood(newMood as typeof mood)
      prevMoodRef.current = newMood as typeof mood
    }
  }, [lastEvent, setMood, mood])

  // Clipboard monitoring — alert when text copied in any app
  const clipboardTimerRef = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => {
    if (!ei?.onClipboardChange) return
    const cleanup = ei.onClipboardChange((data: { text: string }) => {
      const text = data.text?.trim()
      if (!text || text.length < 5) return
      setMood('alert')
      clearTimeout(clipboardTimerRef.current)
      clipboardTimerRef.current = setTimeout(() => setMood('idle'), 5000)
    })
    return () => {
      cleanup()
      clearTimeout(clipboardTimerRef.current)
    }
  }, [ei, setMood])

  // Drag state
  const [dragging, setDragging] = useState(false)
  const dragStartPos = useRef<{ screenX: number; screenY: number } | null>(null)
  const DRAG_THRESHOLD = 5 // pixels — beyond this it's a drag, not a click

  const handleOrbClick = useCallback(() => {
    ei?.openFloatingChat?.()
  }, [ei])

  const eventHandlers = useMemo(() => ({
    onClick: handleOrbClick,
  }), [handleOrbClick])

  const phase2UpRef = useRef<((e: MouseEvent) => void) | null>(null)

  // Cleanup phase-2 listener on unmount
  useEffect(() => {
    return () => {
      if (phase2UpRef.current) {
        window.removeEventListener('mouseup', phase2UpRef.current)
        phase2UpRef.current = null
      }
    }
  }, [])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    // Record start position for drag detection
    dragStartPos.current = { screenX: e.screenX, screenY: e.screenY }

    const beginVoiceCapture = () => {
      void startVoice().then((ok) => {
        if (ok) voiceActiveRef.current = true
      })
    }

    const onMove = (move: MouseEvent) => {
      const dx = Math.abs(move.screenX - dragStartPos.current!.screenX)
      const dy = Math.abs(move.screenY - dragStartPos.current!.screenY)
      if (dx < DRAG_THRESHOLD && dy < DRAG_THRESHOLD) return

      // 拖动判定优先：取消长按计时（即便已开始录音也中止，避免误触）
      if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current)
        longPressTimerRef.current = null
      }
      if (voiceActiveRef.current) {
        voiceActiveRef.current = false
        void stopVoice()
        return
      }

      if (!ei?.dragStart) return
      const offsetX = window.screenX - move.screenX
      const offsetY = window.screenY - move.screenY
      ei.dragStart(offsetX, offsetY)
      setDragging(true)
      window.removeEventListener('mousemove', onMove)

      // Remove previous phase-2 listener if any
      if (phase2UpRef.current) {
        window.removeEventListener('mouseup', phase2UpRef.current)
      }
      const onUp = (_up: MouseEvent) => {
        setDragging(false)
        ei.dragEnd()
        window.removeEventListener('mouseup', onUp)
        phase2UpRef.current = null
        dragStartPos.current = null
      }
      phase2UpRef.current = onUp
      window.addEventListener('mouseup', onUp)
    }

    const onUp = (up: MouseEvent) => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current)
        longPressTimerRef.current = null
      }

      // 长按已进入录音 → 停止并转写交付，不再触发点击/拖动
      if (voiceActiveRef.current) {
        voiceActiveRef.current = false
        void stopVoice()
        return
      }

      // If mouse barely moved → it's a click, open chat
      const start = dragStartPos.current
      if (start && Math.abs(up.screenX - start.screenX) < DRAG_THRESHOLD &&
          Math.abs(up.screenY - start.screenY) < DRAG_THRESHOLD) {
        handleOrbClick()
      }
      dragStartPos.current = null
    }

    // 第二档 #6：长按计时——按下不动超过阈值即进入录音（短按/拖动走原路径）
    longPressTimerRef.current = setTimeout(beginVoiceCapture, LONGPRESS_MS)

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [ei, handleOrbClick, startVoice, stopVoice])

  // 卸载时清理长按计时器
  useEffect(() => {
    return () => {
      if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current)
        longPressTimerRef.current = null
      }
    }
  }, [])

  // 录音进行中 → 宠物进入 listening 表情，停止后回到 idle
  useEffect(() => {
    if (recording) {
      setMood('listening')
    } else if (mood === 'listening') {
      setMood('idle')
    }
  }, [recording, mood, setMood])

  // Set background to transparent (only once, even under Strict Mode)
  const configuredRef = useRef(false)
  useEffect(() => {
    if (configuredRef.current) return
    configuredRef.current = true
    document.documentElement.setAttribute('data-background', 'acrylic')
    document.documentElement.setAttribute('data-theme', 'dark')
    document.body.style.background = 'transparent'
    document.documentElement.style.background = 'transparent'
    ei?.configurePet?.({
      windowWidth: 288,
      windowHeight: 316,
      hitArea: { x: 24, y: 76, width: 240, height: 240 },
    })
  }, [])

  return (
    <div
      className="w-screen h-screen"
      style={{
        background: 'transparent',
        position: 'relative',
        overflow: 'hidden',
        width: 288,
        height: 316,
      }}
    >
      <div
        style={{
          cursor: dragging ? 'grabbing' : 'grab',
          display: 'inline-block',
          width: 288,
          height: 316,
        }}
        onMouseDown={handleMouseDown}
      >
        <PetXiaoWenAvatar
          mood={mood}
          size={240}
          eventHandlers={eventHandlers}
        />
        <TaskProgress progress={currentProgress} />
      </div>
    </div>
  )
}

export default FloatingOrbApp
