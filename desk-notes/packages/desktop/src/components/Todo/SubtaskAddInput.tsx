import React, { useState, useRef, useEffect } from 'react'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'

interface SubtaskAddInputProps {
  parentId: string
  depth: number
}

const SubtaskAddInput: React.FC<SubtaskAddInputProps> = ({ parentId, depth }) => {
  const addSubtask = useStore(s => s.addSubtask)
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  const submit = () => {
    const v = value.trim()
    if (v) { addSubtask(parentId, v); setValue('') }
  }

  return (
    <div style={{ paddingLeft: `${28 + (depth + 1) * 20}px` }} className="pr-2 pb-1">
      <input
        ref={inputRef}
        value={value}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') setValue('') }}
        onBlur={() => { if (!value.trim()) setValue('') }}
        placeholder={t('todo.addSubtaskPlaceholder')}
        className="w-full bg-transparent text-xs text-white/55 placeholder:text-text-dim outline-none border-b border-white/[0.04] focus:border-white/[0.12] transition-colors pb-0.5"
      />
    </div>
  )
}

export default SubtaskAddInput
