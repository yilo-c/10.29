import React, { useState, useRef, useEffect } from 'react'
import Icon from '../Common/Icon'

import { motion, AnimatePresence } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { uid } from '../../utils/helpers'
import { useTranslation } from '../../i18n'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

const CategoryPills: React.FC = () => {
  const activeCat = useStore(s => s.activeCat)
  const setCategory = useStore(s => s.setCategory)
  const categories = useStore(s => s.categories)
  const addCategory = useStore(s => s.addCategory)
  const removeCategory = useStore(s => s.removeCategory)
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const nextZ = useStore(s => s.nextZ)
  const todos = useStore(s => s.todos)
  const { t } = useTranslation()
  const [showAdd, setShowAdd] = useState(false)
  const [newLabel, setNewLabel] = useState('')
  const [dragGhost, setDragGhost] = useState<{ label: string; color: string; x: number; y: number } | null>(null)
  const addRef = useRef<HTMLDivElement>(null)
  const cleanupDragRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    return () => { cleanupDragRef.current?.() }
  }, [])

  useEffect(() => {
    if (!showAdd) return
    const handler = (e: MouseEvent) => {
      if (addRef.current && !addRef.current.contains(e.target as Node)) {
        setShowAdd(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showAdd])

  const handleCatMouseDown = (e: React.MouseEvent, cat: typeof categories[0]) => {
    if ((e.target as HTMLElement).closest('.no-drag')) return
    const startX = e.clientX
    const startY = e.clientY
    let moved = false

    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX
      const dy = ev.clientY - startY
      if (Math.sqrt(dx * dx + dy * dy) > 8 && !moved) {
        moved = true
        setDragGhost({ label: cat.label, color: cat.color, x: ev.clientX, y: ev.clientY })
      }
      if (moved) {
        setDragGhost(g => g ? { ...g, x: ev.clientX, y: ev.clientY } : null)
      }
    }

    const onUp = (ev: MouseEvent) => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      cleanupDragRef.current = null
      setDragGhost(null)

      const dx = ev.clientX - startX
      const dy = ev.clientY - startY
      if (Math.sqrt(dx * dx + dy * dy) > 15) {
        // Dragged out → create floating note for this category
        const catTodos = todos.filter(t => t.category === cat.id && !t.done)
        const id = uid()
        addFloatingNote({
          id, type: 'todo', title: cat.label,
          content: '', todos: catTodos,
          x: ev.clientX - 130, y: ev.clientY - 30,
          width: 260, height: 240, zIndex: nextZ, floated: true,
        })
        if (isElectron) {
          const note = useStore.getState().floatingNotes.find(n => n.id === id)
          ei.createFloatingWindow({ id, screenX: ev.clientX - 130, screenY: ev.clientY - 30, width: 260, height: 240, noteData: note })
        }
      } else {
        // Click → filter
        setCategory(cat.id)
      }
    }

    cleanupDragRef.current = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const handleAdd = () => {
    const v = newLabel.trim()
    if (!v) return
    addCategory({ id: uid(), label: v, icon: '📋', color: randomColor() })
    setNewLabel('')
    setShowAdd(false)
  }

  const handleDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    if (categories.length <= 1) return
    removeCategory(id)
  }

  return (
    <div className="w-12 flex flex-col items-center gap-1 py-2 border-r border-white/[0.04] relative">
      {/* 全部 */}
      <PillBtn
        active={activeCat === 'all'}
        color="#94a3b8"
        onClick={() => setCategory('all')}
        title={t('category.all')}
      >
        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
          <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" />
        </svg>
      </PillBtn>

      <div className="w-5 h-px bg-white/[0.04] my-0.5" />

      {/* Dynamic categories */}
      {categories.map(c => {
        const active = activeCat === c.id
        return (
          <div key={c.id} className="relative group">
            <PillBtn
              active={active}
              color={c.color}
              onMouseDown={(e) => handleCatMouseDown(e, c)}
              title={c.label}
            >
              <span className="text-xs font-medium">{c.label.slice(0, 2)}</span>
            </PillBtn>
            <button
              onClick={(e) => handleDelete(e, c.id)}
              className="absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full border border-white/[0.08] flex items-center justify-center text-text-secondary hover:text-red-400/70 text-[6px] opacity-0 group-hover:opacity-100 transition-opacity"
              style={{ background: 'var(--panel-bg-solid)' }}
            >
              <Icon icon="fa-solid fa-xmark" />
            </button>
          </div>
        )
      })}

      {/* Add button */}
      <div className="relative" ref={addRef}>
        <motion.button
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          onClick={() => setShowAdd(!showAdd)}
          className="w-7 h-7 rounded-xl flex items-center justify-center text-text-secondary hover:text-text-secondary hover:bg-white/[0.04] transition-all text-xs"
          title={t('category.newCategory')}
        >
          <Icon icon="fa-solid fa-plus" />
        </motion.button>

        <AnimatePresence>
          {showAdd && (
            <motion.div
              initial={{ opacity: 0, x: -4, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: -4, scale: 0.95 }}
              transition={{ duration: 0.15 }}
              className="absolute left-full ml-2 top-0 z-50 border border-white/[0.08] rounded-xl p-3 shadow-2xl w-44"
              onMouseDown={e => e.stopPropagation()}
              style={{ background: 'var(--panel-bg-solid)' }}
            >
              <div className="text-xs text-text-secondary font-medium mb-2">{t('category.newCategory')}</div>
              <div className="flex items-center gap-2 mb-2">
                <input value={newLabel} onChange={e => setNewLabel(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleAdd()}
                  className="flex-1 bg-white/[0.06] text-xs text-text-primary placeholder:text-text-secondary px-2 py-1.5 rounded-lg outline-none border border-white/[0.06]"
                  placeholder={t('category.categoryName')}
                  autoFocus
                />
              </div>
              <div className="flex justify-end gap-1">
                <button onClick={() => setShowAdd(false)}
                  className="text-2xs px-2 py-1 rounded text-white/65 hover:text-text-secondary transition-colors">{t('common.cancel')}</button>
                <button onClick={handleAdd}
                  className="text-2xs px-2 py-1 rounded bg-fluent-blue/15 text-fluent-blue/60 hover:bg-fluent-blue/20 transition-colors">{t('category.addCategory')}</button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Drag ghost */}
      {dragGhost && (
        <div
          className="fixed pointer-events-none z-[9999] text-xs px-2.5 py-1.5 rounded-lg shadow-2xl"
          style={{
            left: dragGhost.x - 20,
            top: dragGhost.y - 16,
            background: `${dragGhost.color}20`,
            border: `1px solid ${dragGhost.color}30`,
            color: dragGhost.color,
          }}
        >
          {dragGhost.label}
        </div>
      )}
    </div>
  )
}

const PillBtn: React.FC<{
  active: boolean; children: React.ReactNode; color: string;
  onClick?: () => void; onMouseDown?: (e: React.MouseEvent) => void; title: string
}> = ({ active, children, color, onClick, onMouseDown, title }) => (
  <motion.button
    whileHover={{ scale: 1.1 }}
    whileTap={{ scale: 0.9 }}
    onMouseDown={onMouseDown}
    onClick={onClick}
    className="relative w-9 h-9 rounded-xl flex items-center justify-center text-sm transition-all"
    style={{
      background: active ? `${color}15` : 'transparent',
      color: active ? color : 'var(--text-muted)',
    }}
    title={title}
  >
    {active && (
      <motion.div
        layoutId="pill-bg"
        className="absolute inset-0 rounded-xl"
        style={{ background: `${color}12`, border: `1px solid ${color}20` }}
        transition={{ type: 'spring', damping: 25, stiffness: 300 }}
      />
    )}
    <span className="relative">{children}</span>
  </motion.button>
)

const colors = ['#60a5fa', '#f87171', '#34d399', '#fbbf24', '#a78bfa', '#fb923c', '#f472b6', '#22d3ee', '#818cf8', '#e879f9']
const randomColor = () => colors[Math.floor(Math.random() * colors.length)]

export default CategoryPills
