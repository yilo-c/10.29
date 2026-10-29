import { useState, useCallback, useEffect, useRef } from 'react'
import { useStore } from '@desk-notes/shared'
import { haptic } from '../../../lib/haptic'

interface Props {
  open: boolean
  onClose: () => void
}

export default function QuickNoteSheet({ open, onClose }: Props) {
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (open) {
      setText('')
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [open])

  const handleSave = useCallback(() => {
    const content = text.trim()
    if (!content) return
    haptic.light()
    useStore.getState().addFloatingNote({
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      title: content.slice(0, 40),
      content,
      type: 'text',
      x: 0, y: 0, zIndex: 1, width: 320, height: 200,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      tags: [],
      editMode: 'markdown',
    })
    setText('')
    onClose()
  }, [text, onClose])

  if (!open) return null

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 2000,
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
      background: 'rgba(0,0,0,0.4)',
    }} onClick={onClose}>
      <div style={{
        width: '100%', maxWidth: 400,
        padding: 'var(--space-4)',
        paddingBottom: 'calc(var(--space-4) + env(safe-area-inset-bottom))',
        background: 'var(--bg-secondary)',
        borderTopLeftRadius: 'var(--radius-lg)',
        borderTopRightRadius: 'var(--radius-lg)',
      }} onClick={e => e.stopPropagation()}>
        <div style={{
          fontSize: 'var(--text-headline)', fontWeight: 600,
          marginBottom: 12, color: 'var(--text-primary)',
        }}>
          快速笔记
        </div>
        <textarea
          ref={inputRef}
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="写点什么..."
          rows={4}
          style={{
            width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-sm)',
            border: '0.5px solid var(--border-default)', outline: 'none', resize: 'none',
            fontSize: 'var(--text-body)', background: 'var(--bg-primary)',
            color: 'var(--text-primary)', boxSizing: 'border-box',
            fontFamily: 'inherit',
          }}
        />
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button
            onClick={onClose}
            style={{
              flex: 1, padding: '10px', borderRadius: 'var(--radius-sm)',
              border: 'none', fontSize: 'var(--text-body)',
              background: 'var(--bg-tertiary)', color: 'var(--text-secondary)',
            }}
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={!text.trim()}
            style={{
              flex: 1, padding: '10px', borderRadius: 'var(--radius-sm)',
              border: 'none', fontSize: 'var(--text-body)',
              background: 'var(--accent)', color: '#fff',
              fontWeight: 600, opacity: !text.trim() ? 0.5 : 1,
            }}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  )
}
