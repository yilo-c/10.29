import { useState, useCallback, useEffect, useRef } from 'react'
import type { PetMood } from '../types/pet'

const ALL_MOODS: PetMood[] = ['idle', 'listening', 'thinking', 'done', 'happy', 'alert', 'notification', 'sleeping']
const IDLE_ROTATE_MIN_MS = 30000
const IDLE_ROTATE_MAX_MS = 90000
const AUTO_SLEEP_MS = 900000 // 15min idle → sleeping

const REVERTABLE_MOODS: PetMood[] = ['thinking', 'done', 'happy', 'alert', 'notification']

export interface PetState {
  mood: PetMood
  isPanelOpen: boolean
  cycleMood: () => void
  setMood: (mood: PetMood) => void
  openPanel: () => void
  closePanel: () => void
  togglePanel: () => void
}

export function usePetState(): PetState {
  const [mood, setMood] = useState<PetMood>('idle')
  const [isPanelOpen, setPanelOpen] = useState(false)

  // Idle mood rotation timer
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const revertTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const sleepTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastActiveTimeRef = useRef(Date.now())

  const scheduleRevert = useCallback(() => {
    if (revertTimerRef.current) clearTimeout(revertTimerRef.current)
    revertTimerRef.current = setTimeout(() => setMood('idle'), 4000)
  }, [])

  const scheduleIdleRotate = useCallback(() => {
    if (idleTimerRef.current) clearTimeout(idleTimerRef.current)
    const delay = IDLE_ROTATE_MIN_MS + Math.random() * (IDLE_ROTATE_MAX_MS - IDLE_ROTATE_MIN_MS)
    idleTimerRef.current = setTimeout(() => {
      setMood(prev => {
        if (prev === 'idle') return 'happy'
        return prev
      })
      scheduleRevert()
    }, delay)
  }, [scheduleRevert])

  // Auto-sleep when idle for 15min
  useEffect(() => {
    if (sleepTimerRef.current) clearTimeout(sleepTimerRef.current)
    if (mood === 'idle' || mood === 'sleeping') {
      sleepTimerRef.current = setTimeout(() => {
        setMood('sleeping')
      }, AUTO_SLEEP_MS)
    }
    return () => {
      if (sleepTimerRef.current) clearTimeout(sleepTimerRef.current)
    }
  }, [mood])

  useEffect(() => {
    if (mood === 'idle' && !isPanelOpen) {
      scheduleIdleRotate()
    }
    return () => {
      if (idleTimerRef.current) clearTimeout(idleTimerRef.current)
      if (revertTimerRef.current) clearTimeout(revertTimerRef.current)
    }
  }, [mood, isPanelOpen, scheduleIdleRotate])

  const handleSetMood = useCallback((newMood: PetMood) => {
    // If sleeping, any non-sleeping mood wakes it
    if (mood === 'sleeping' && newMood !== 'sleeping') {
      lastActiveTimeRef.current = Date.now()
    }
    setMood(newMood)
    // Auto-revert transient moods back to idle
    if (REVERTABLE_MOODS.includes(newMood)) {
      scheduleRevert()
    }
  }, [mood, scheduleRevert])

  const cycleMood = useCallback(() => {
    const idx = ALL_MOODS.indexOf(mood)
    const next = ALL_MOODS[(idx + 1) % ALL_MOODS.length]
    handleSetMood(next)
  }, [mood, handleSetMood])

  const openPanel = useCallback(() => setPanelOpen(true), [])
  const closePanel = useCallback(() => setPanelOpen(false), [])
  const togglePanel = useCallback(() => setPanelOpen(p => !p), [])

  return {
    mood,
    isPanelOpen,
    cycleMood,
    setMood: handleSetMood,
    openPanel,
    closePanel,
    togglePanel,
  }
}
