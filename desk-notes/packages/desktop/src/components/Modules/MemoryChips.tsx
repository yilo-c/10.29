import { useState, useEffect, useRef } from 'react'
import Icon from '../Common/Icon'

import { getWsClient } from '../../utils/ai'

interface SurfaceMemory {
  id: string
  summary: string
  score: number
  timestamp: number
  source_type?: string
  title?: string
}

interface Props {
  text: string
  onSelectMemory: (summary: string) => void
}

export default function MemoryChips({ text, onSelectMemory }: Props) {
  const [chips, setChips] = useState<SurfaceMemory[]>([])
  const [loading, setLoading] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const seqRef = useRef(0)

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current)

    const trimmed = text.trim()
    if (!trimmed || trimmed.length < 3) {
      setChips([])
      setLoading(false)
      return
    }

    // 800ms debounce — only query after user stops typing
    timerRef.current = setTimeout(async () => {
      const seq = ++seqRef.current
      setLoading(true)

      try {
        const client = getWsClient()
        if (client.status !== 'connected') {
          setLoading(false)
          return
        }
        const results = await client.call<SurfaceMemory[]>('memory.surface_for_input', {
          text: trimmed.slice(0, 200),
        })
        if (seq !== seqRef.current) return
        setChips(Array.isArray(results) ? results.slice(0, 3) : [])
      } catch {
        if (seq !== seqRef.current) return
        setChips([])
      }
      setLoading(false)
    }, 800)

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [text])

  if (chips.length === 0) return null

  return (
    <div className="flex items-center gap-1.5 px-3 pb-1 overflow-x-auto">
      {loading && (
        <div className="flex items-center gap-1 text-2xs" style={{ color: 'var(--text-dim)' }}>
          <Icon icon="fa-solid fa-circle-notch text-[8px] animate-spin" />
        </div>
      )}
      {chips.map((mem, i) => (
        <button
          key={mem.id || i}
          onClick={() => onSelectMemory(mem.summary)}
          className="flex items-center gap-1 px-2 py-0.5 rounded-full text-2xs whitespace-nowrap transition-all hover:opacity-80 border shrink-0"
          style={{
            background: 'rgba(96,165,250,0.08)',
            borderColor: 'rgba(96,165,250,0.2)',
            color: '#93bbfc',
          }}
          title={`${mem.title || ''} (${mem.score})`}
        >
          <Icon icon="fa-regular fa-clock text-[7px]" />
          <span className="max-w-[120px] truncate">{mem.summary}</span>
        </button>
      ))}
    </div>
  )
}
