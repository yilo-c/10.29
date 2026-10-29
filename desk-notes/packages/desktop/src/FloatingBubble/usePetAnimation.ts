import { useCallback } from 'react'
import type { PetActivity, PetAnimationState } from '../types/pet'
import { usePetState } from './usePetState'

/**
 * usePetAnimation — animation state machine for the desk pet.
 *
 * Extends the base mood system (usePetState) with richer animation dimensions:
 *   - mood: emotional state (idle / thinking / happy / alert)
 *   - activity: behavioral state (idling / speaking / sleeping / playing)
 *   - expression: momentary face override (normal / blink / surprise)
 *
 * Future: This hook can drive Live2D blend-shapes, sprite frame selection,
 * CSS animation classes, or any other pet rendering engine.
 */
export function usePetAnimation() {
  const { mood, setMood } = usePetState()

  const getAnimationState = useCallback((): PetAnimationState => ({
    mood,
    activity: 'idling',
    expression: 'normal',
  }), [mood])

  /** Transition the pet into a speaking state */
  const startSpeaking = useCallback(() => {
    setMood('thinking')
  }, [setMood])

  /** Transition the pet out of speaking */
  const stopSpeaking = useCallback(() => {
    setMood('idle')
  }, [setMood])

  /** Set activity level */
  const setActivity = useCallback((_activity: PetActivity) => {
    // Future: wire up to activity-specific animation loops
  }, [])

  /** Set momentary expression override */
  const setExpression = useCallback((_expression: PetAnimationState['expression']) => {
    // Future: trigger blink, surprise, etc.
  }, [])

  return {
    mood,
    setMood,
    animationState: getAnimationState(),
    startSpeaking,
    stopSpeaking,
    setActivity,
    setExpression,
  }
}
