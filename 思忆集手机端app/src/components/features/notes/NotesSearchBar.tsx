import { IconSearch, IconX } from '../../ui'

interface NotesSearchBarProps {
  value: string
  onChange: (v: string) => void
  onClear: () => void
}

export default function NotesSearchBar({ value, onChange, onClear }: NotesSearchBarProps) {
  return (
    <div style={{ padding: '10px 20px 0', flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', padding: '0 12px', minHeight: 38 }}>
        <span style={{ color: 'var(--text-disabled)', display: 'flex' }}><IconSearch size={16} /></span>
        <input value={value} onChange={e => onChange(e.target.value)} placeholder="搜索笔记..." style={{ flex: 1, border: 'none', background: 'transparent', color: 'var(--text-primary)', fontSize: 'var(--text-footnote)', outline: 'none', padding: '8px 0' }} />
        {value && (
          <button onClick={onClear} aria-label="清除搜索" style={{ background: 'none', border: 'none', color: 'var(--text-disabled)', cursor: 'pointer', display: 'flex', padding: 4 }}>
            <IconX size={14} />
          </button>
        )}
      </div>
    </div>
  )
}
