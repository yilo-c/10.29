import { useState, useEffect, useRef, useCallback } from 'react'

type PermissionState = 'prompt' | 'granted' | 'denied' | 'unsupported'

export interface CompassState {
  heading: number | null
  headingLabel: string | null
  permission: PermissionState
  error: string | null
}

/** 8 directions in Chinese, matching the labels used in QimenPanel/fengshui */
const DIRECTION_LABELS = ['正北', '东北', '正东', '东南', '正南', '西南', '正西', '西北'] as const

/** Each direction spans 45°, centered on the listed heading */
function headingToLabel(deg: number): string {
  // Normalize to 0-360
  const d = ((deg % 360) + 360) % 360
  const index = Math.round(d / 45) % 8
  return DIRECTION_LABELS[index]
}

/** Get the center degree for a Chinese direction label */
export function labelToDegrees(label: string): number {
  const index = DIRECTION_LABELS.indexOf(label as typeof DIRECTION_LABELS[number])
  if (index === -1) return 0
  return index * 45
}

/** Minimal angle difference (signed, -180 to 180) */
export function angleDiff(from: number, to: number): number {
  const d = ((to - from) % 360 + 540) % 360 - 180
  return d
}

export function useCompass(): CompassState & {
  requestPermission: () => Promise<void>
} {
  const [heading, setHeading] = useState<number | null>(null)
  const [headingLabel, setHeadingLabel] = useState<string | null>(null)
  const [permission, setPermission] = useState<PermissionState>('prompt')
  const [error, setError] = useState<string | null>(null)
  const rafRef = useRef<number | null>(null)
  const lastHeadingRef = useRef<number | null>(null)

  const updateHeading = useCallback((deg: number) => {
    // Throttle via rAF — only update if heading changed meaningfully (>1°)
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      const d = ((deg % 360) + 360) % 360
      if (lastHeadingRef.current !== null && Math.abs(d - lastHeadingRef.current) < 1) return
      lastHeadingRef.current = d
      setHeading(d)
      setHeadingLabel(headingToLabel(d))
    })
  }, [])

  const startListening = useCallback(() => {
    if (!window.DeviceOrientationEvent) {
      setPermission('unsupported')
      setError('DeviceOrientation not supported')
      return
    }

    const handler = (event: DeviceOrientationEvent) => {
      // iOS provides webkitCompassHeading (true north) via alpha
      // Android provides alpha as magnetic north
      let deg: number | null = null

      const webkitHeading = (event as unknown as Record<string, unknown>).webkitCompassHeading
      if (typeof webkitHeading === 'number') {
        deg = webkitHeading
      } else if (event.alpha !== null) {
        deg = event.alpha
      }

      if (deg !== null) {
        updateHeading(deg)
      }
    }

    window.addEventListener('deviceorientation', handler, { passive: true })
    return () => window.removeEventListener('deviceorientation', handler)
  }, [updateHeading])

  const requestPermission = useCallback(async () => {
    // iOS 13+ requires permission request
    const orientationAPI = window.DeviceOrientationEvent as unknown as {
      requestPermission?: () => Promise<'granted' | 'denied'>
    }

    if (typeof orientationAPI?.requestPermission === 'function') {
      try {
        const state = await orientationAPI.requestPermission()
        setPermission(state)
        if (state === 'granted') {
          startListening()
        } else {
          setError('Permission denied by user')
        }
      } catch (e: unknown) {
        setPermission('denied')
        setError(e instanceof Error ? e.message : 'Permission request failed')
      }
    } else {
      // Android: no permission prompt needed, start listening directly
      setPermission('granted')
      startListening()
    }
  }, [startListening])

  // Auto-start on Android (no permission prompt) or when permission is already granted
  useEffect(() => {
    const orientationAPI = window.DeviceOrientationEvent as unknown as {
      requestPermission?: () => Promise<'granted' | 'denied'>
    }

    // If no requestPermission (Android), start directly
    if (typeof orientationAPI?.requestPermission !== 'function') {
      if (window.DeviceOrientationEvent) {
        setPermission('granted')
        const cleanup = startListening()
        return cleanup
      } else {
        setPermission('unsupported')
      }
    }
    // On iOS, wait for requestPermission() call from user gesture
  }, [startListening])

  // Cleanup rAF on unmount
  useEffect(() => {
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
      }
    }
  }, [])

  return { heading, headingLabel, permission, error, requestPermission }
}
