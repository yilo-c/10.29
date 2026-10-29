import React, { useState, useRef, useEffect } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'

const InlineAddInput: React.FC = () => {
  const addTodo = useStore(s => s.addTodo)
  const activeCat = useStore(s => s.activeCat)
  const setAddingTodo = useStore(s => s.setAddingTodo)
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  const submit = () => {
    const v = value.trim()
    if (v) addTodo(v, activeCat === 'all' ? undefined : activeCat)
    setAddingTodo(false)
  }

  return (
    <div className="px-3 pb-2">
      <div className="flex items-center gap-2 bg-white/[0.06] rounded-xl px-3 py-2 border border-fluent-blue/30 transition-colors">
        <input
          ref={inputRef}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submit() }}
          onBlur={submit}
          placeholder={t('todo.addPlaceholder')}
          className="flex-1 bg-transparent text-xs text-text-primary placeholder:text-text-secondary outline-none"
        />
        <button onClick={submit}
          className="text-xs text-text-secondary hover:text-text-secondary transition-colors">
          <Icon icon="fa-solid fa-plus" />
        </button>
      </div>
    </div>
  )
}

export default InlineAddInput
