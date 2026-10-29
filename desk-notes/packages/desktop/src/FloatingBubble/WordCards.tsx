import React, { useState, useCallback } from 'react'
import Icon from '../components/Common/Icon'

import { getWsClient } from '@desk-notes/shared'
import SpeakButton from '../components/Common/SpeakButton'
import { swallowError } from '../utils/swallowError'
import { errorStore } from '../store/errorStore'

export interface WordEntry {
  word: string
  phonetic: string
  meaning: string
  context: string
  examples?: string[]
}

interface WordCardsProps {
  words: WordEntry[]
  /** 保存成功后回调 */
  onSaved?: (count: number) => void
  /** 一键保存模式：显示大按钮直接保存全部，不展示选择 UI */
  quickSave?: boolean
}

/** 可复用词卡组件 — 勾选后批量加入单词本 */
const WordCards: React.FC<WordCardsProps> = ({ words, onSaved, quickSave }) => {
  const [selected, setSelected] = useState<Set<number>>(() => new Set(words.map((_, i) => i)))
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const toggleSelect = useCallback((idx: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }, [])

  const toggleAll = useCallback(() => {
    setSelected(prev =>
      prev.size === words.length ? new Set() : new Set(words.map((_, i) => i))
    )
  }, [words.length])

  const doSave = useCallback(async (ids: Set<number>) => {
    if (ids.size === 0) return 0
    setSaving(true)
    let savedCount = 0

    try {
      const client = getWsClient()
      let wbId: number
      try {
        const wordbooks = await client.call<Array<{ id: number; name: string }>>('wordbook.list', {})
        wbId = wordbooks?.[0]?.id ?? (
          await client.call<{ id: number }>('wordbook.create', { name: '默认单词本' })
        ).id
      } catch {
        errorStore.error('无法连接后端')
        return 0
      }

      for (const idx of ids) {
        const w = words[idx]
        if (!w) continue
        try {
          const wordParams: Record<string, unknown> = {
            wordbook_id: wbId,
            word: w.word,
            meaning: w.meaning,
            example: w.context,
          }
          if (w.examples?.length) {
            wordParams.examples = w.examples
          }
          await client.call('word.add', wordParams)
          savedCount++
        } catch (e) {
          // 单词已存在等, 跳过
          swallowError('WordCards:77', e)
        }
      }

      setSaved(true)
      errorStore.success(`已保存 ${savedCount} 个词汇`)
      onSaved?.(savedCount)
    } catch {
      errorStore.error('保存失败')
    } finally {
      setSaving(false)
    }
    return savedCount
  }, [words, onSaved])

  const handleSave = useCallback(async () => {
    if (quickSave) {
      const allIds = new Set(words.map((_, i) => i))
      await doSave(allIds)
    } else {
      await doSave(selected)
    }
  }, [quickSave, words, selected, doSave])

  if (words.length === 0) return null

  if (quickSave) {
    return (
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium" style={{ color: 'var(--text-dim)' }}>
            📝 词汇提取 ({words.length})
          </span>
          <button
            onClick={handleSave}
            disabled={saving || saved}
            className="flex items-center gap-1.5 px-3 py-1 rounded-lg text-[11px] font-medium transition-all disabled:opacity-40 ml-auto"
            style={{ background: saving ? 'rgba(168,85,247,0.08)' : saved ? 'rgba(52,211,153,0.12)' : 'rgba(168,85,247,0.12)', color: saved ? '#34d399' : saving ? 'var(--text-muted)' : '#a855f7' }}
          >
            <Icon icon={`fa-solid ${saving ? 'fa-spinner animate-spin' : saved ? 'fa-check' : 'fa-book'} text-[10px]`} />
            {saved ? '已加入单词本' : saving ? '保存中…' : '一键保存全部'}
          </button>
        </div>
      </div>
    )
  }

  const allSelected = selected.size === words.length

  return (
    <div className="flex flex-col gap-1.5">
      {/* 头部: 全选 + 保存按钮 */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium" style={{ color: 'var(--text-dim)' }}>
          📝 词汇提取 ({words.length})
        </span>
        <button
          onClick={toggleAll}
          className="text-[10px] px-1.5 py-0.5 rounded transition-colors"
          style={{ color: 'var(--text-muted)', background: 'rgba(255,255,255,0.04)' }}
        >
          {allSelected ? '取消全选' : '全选'}
        </button>
        <button
          onClick={handleSave}
          disabled={saving || saved || selected.size === 0}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium transition-all disabled:opacity-40 ml-auto"
          style={{ background: 'rgba(168,85,247,0.12)', color: '#a855f7' }}
        >
          <Icon icon={`fa-solid ${saving ? 'fa-spinner animate-spin' : saved ? 'fa-check' : 'fa-book'} text-[8px]`} />
          {saved ? '已保存' : saving ? '保存中…' : `保存 (${selected.size})`}
        </button>
      </div>

      {/* 词卡列表 */}
      <div className="flex flex-wrap gap-1.5">
        {words.map((w, idx) => {
          const isSelected = selected.has(idx)
          return (
            <button
              key={`${w.word}-${idx}`}
              onClick={() => toggleSelect(idx)}
              className={`flex flex-col gap-0.5 px-2 py-1.5 rounded-lg text-left transition-all border ${
                isSelected
                  ? 'border-purple-400/30 bg-purple-400/[0.06]'
                  : 'border-white/[0.04] bg-white/[0.01] opacity-50'
              }`}
              style={{ minWidth: 80, maxWidth: 200 }}
            >
              <div className="flex items-center gap-1">
                <span
                  className="text-[8px] shrink-0"
                  style={{ color: isSelected ? '#a855f7' : 'transparent' }}
                >
                  <Icon icon={`fa-solid ${isSelected ? 'fa-check-square' : 'fa-square'}`} />
                </span>
                <span className="text-[11px] font-medium" style={{ color: 'var(--text-primary)' }}>
                  {w.word}
                </span>
                <SpeakButton text={w.word} lang="en" variant="icon-sm" />
              </div>
              {w.phonetic && (
                <div className="text-[10px] ml-[18px]" style={{ color: 'var(--text-muted)' }}>
                  {w.phonetic}
                </div>
              )}
              <div className="text-xs ml-[18px]" style={{ color: 'var(--text-secondary)' }}>
                {w.meaning}
              </div>
              {w.examples && w.examples.length > 0 && (
                <div className="ml-[18px] mt-0.5 space-y-0.5">
                  {w.examples.slice(0, 2).map((ex, ei) => (
                    <div key={ei} className="text-[8px] italic opacity-60" style={{ color: 'var(--text-muted)' }}>
                      "{ex}"
                    </div>
                  ))}
                </div>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default WordCards
