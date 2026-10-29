import { useRef, useCallback } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'

interface UseVirtualListOptions {
  count: number
  estimateSize?: number
  overscan?: number
}

export function useVirtualList<T extends HTMLElement>({ count, estimateSize = 52, overscan = 5 }: UseVirtualListOptions) {
  const parentRef = useRef<T>(null)

  const virtualizer = useVirtualizer({
    count,
    getScrollElement: useCallback(() => parentRef.current, []),
    estimateSize: useCallback(() => estimateSize, [estimateSize]),
    overscan,
  })

  return { parentRef, virtualizer }
}
