import { useState, useEffect, useCallback, useMemo } from 'react'
import Icon from './Icon'

import { useTranslation } from '@desk-notes/shared'
import { getWsClient } from '../../utils/ai'
import { errorStore } from '../../store/errorStore'

interface ApiKeyEntry {
  id: string
  name: string
  category: string
  key_value: string
  base_url: string
  model: string
  is_provider: boolean
  enabled: boolean
  is_local: boolean
  is_fallback: boolean
  description: string
  purpose: string
  from_env: boolean
  env_var_names: string[]
}

interface ProviderFormData {
  name: string
  base_url: string
  model: string
  is_local: boolean
  is_fallback: boolean
  purpose: string
  description: string
  key_value?: string
}

const PURPOSE_COLORS: Record<string, string> = {
  primary: '#3b82f6',
  fallback: '#f97316',
  local: 'var(--success)',
  translation: '#a855f7',
  summarization: '#06b6d4',
  coding: 'var(--danger)',
  general: '#6b7280',
}

const CATEGORY_COLORS: Record<string, string> = {
  llm: '#3b82f6',
  translation: 'var(--success)',
  search: '#f97316',
  task: '#a855f7',
  automation: '#06b6d4',
  other: '#6b7280',
}

interface ApiKeyManagerProps {
  onClose: () => void
}

export default function ApiKeyManager({ onClose }: ApiKeyManagerProps) {
  const { t } = useTranslation()
  const [keys, setKeys] = useState<ApiKeyEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [editingKey, setEditingKey] = useState<ApiKeyEntry | null>(null)
  const [showAddProvider, setShowAddProvider] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [modalError, setModalError] = useState('')
  const wsClient = getWsClient()

  const purposeLabels = useMemo<Record<string, { label: string; color: string }>>(() => {
    const out: Record<string, { label: string; color: string }> = {}
    for (const k of Object.keys(PURPOSE_COLORS)) {
      out[k] = { label: t(`apiKeys.purpose.${k}`), color: PURPOSE_COLORS[k] }
    }
    return out
  }, [t])

  const categoryConfig = useMemo<Record<string, { label: string; color: string }>>(() => {
    const out: Record<string, { label: string; color: string }> = {}
    for (const k of Object.keys(CATEGORY_COLORS)) {
      out[k] = { label: t(`apiKeys.category.${k}`), color: CATEGORY_COLORS[k] }
    }
    return out
  }, [t])

  const formatError = useCallback((e: unknown, fallbackMsg: string): string => {
    const msg = e instanceof Error ? e.message : ''
    if (/fetch\s*failed|failed\s*to\s*fetch|networkerror|ERR_CONNECTION_REFUSED|ECONNREFUSED/i.test(msg)) {
      return t('apiKeys.backendNoResponse')
    }
    return msg || fallbackMsg
  }, [t])

  const purposeBadge = useCallback((purpose: string) => {
    const def = purposeLabels[purpose]
    if (def) {
      return (
        <span className="text-[7px] px-1 py-0.5 rounded"
          style={{ background: def.color + '18', color: def.color }}>
          {def.label}
        </span>
      )
    }
    return (
      <span className="text-[7px] px-1 py-0.5 rounded" style={{ background: '#6b728018', color: '#6b7280' }}>
        {purpose}
      </span>
    )
  }, [purposeLabels])

  const fetchKeys = useCallback(async () => {
    if (wsClient.status !== 'connected') {
      setLoading(false)
      setError(t('apiKeys.backendNotConnected'))
      return
    }
    try {
      const items = await wsClient.call<ApiKeyEntry[]>('api_keys.list')
      setKeys(Array.isArray(items) ? items : [])
      setError('')
    } catch (e: unknown) {
      setError(formatError(e, t('apiKeys.fetchFailed')))
    } finally {
      setLoading(false)
    }
  }, [wsClient, t, formatError])

  useEffect(() => { fetchKeys() }, [fetchKeys])

  const handleTest = async (id: string) => {
    setTestingId(id)
    try {
      const result = await wsClient.call<{ success: boolean; error?: string }>('api_keys.test', { key_id: id })
      if (result.success) {
        errorStore.success(t('apiKeys.testSuccess'))
      } else {
        errorStore.error(result.error || t('apiKeys.testFailed'))
      }
    } catch (e: unknown) {
      errorStore.error(formatError(e, t('apiKeys.testFailed')))
    } finally {
      setTestingId(null)
    }
  }

  const handleDelete = async (id: string) => {
    const target = keys.find(k => k.id === id)
    const name = target?.name || id
    if (!window.confirm(t('apiKeys.deleteConfirmFmt', { name }))) {
      return
    }
    try {
      await wsClient.call('api_keys.delete', { key_id: id })
      setKeys(prev => prev.filter(k => k.id !== id))
    } catch (e: unknown) {
      setError(formatError(e, t('apiKeys.deleteFailed')))
    }
  }

  const groupedKeys = (category: string) => keys.filter(k => k.category === category)

  const renderKeyRow = (key: ApiKeyEntry) => (
    <div key={key.id}
      className="group flex items-center gap-3 px-3 py-2.5 rounded-xl border transition-all hover:bg-white/[0.03]"
      style={{ borderColor: 'var(--panel-border)', opacity: key.enabled ? 1 : 0.5 }}
    >
      {/* Status dot */}
      <div className="w-2 h-2 rounded-full flex-shrink-0"
        style={{ background: key.from_env ? '#eab308' : (key.key_value ? 'var(--success)' : '#6b7280') }}
        aria-hidden="true"
      />

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>
            {key.name}
          </span>
          {key.from_env && (
            <span className="text-[7px] px-1 py-0.5 rounded" style={{ background: '#eab30818', color: '#eab308' }}>ENV</span>
          )}
          {key.is_local && (
            <span className="text-[7px] px-1 py-0.5 rounded" style={{ background: '#3b82f618', color: '#3b82f6' }}>{t('apiKeys.badgeLocal')}</span>
          )}
          {key.is_fallback && (
            <span className="text-[7px] px-1 py-0.5 rounded" style={{ background: '#f9731618', color: '#f97316' }}>{t('apiKeys.badgeFallback')}</span>
          )}
          {key.is_provider && key.purpose && purposeBadge(key.purpose)}
        </div>
        <div className="flex items-center gap-2 text-[8px] mt-0.5" style={{ color: 'var(--text-dim)' }}>
          {key.is_provider && key.base_url && <span className="truncate max-w-[200px]">{key.base_url}</span>}
          {key.is_provider && key.model && <span>· {key.model}</span>}
          <span>{key.key_value || t('apiKeys.unconfigured')}</span>
          {key.env_var_names.length > 0 && <span>· {t('apiKeys.envVarFmt', { names: key.env_var_names.join(', ') })}</span>}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
        {key.id.startsWith('provider:') && (
          <button onClick={() => { setEditingKey(key); setShowAddProvider(true) }}
            className="w-6 h-6 flex items-center justify-center rounded text-text-muted hover:text-text-secondary hover:bg-white/[0.06] transition-all text-2xs"
            aria-label={t('apiKeys.edit')} title={t('apiKeys.edit')}>
            <Icon icon="fa-solid fa-pen" aria-hidden="true" />
          </button>
        )}
        {!key.from_env && key.key_value && (
          <button onClick={() => handleTest(key.id)}
            disabled={testingId === key.id}
            className="w-6 h-6 flex items-center justify-center rounded text-text-muted hover:text-cyan-400 hover:bg-cyan-400/[0.08] transition-all text-2xs"
            aria-label={t('apiKeys.testConnection')} title={t('apiKeys.testConnection')}>
            <Icon icon={`fa-solid ${testingId === key.id ? 'fa-spinner animate-spin' : 'fa-plug'}`} aria-hidden="true" />
          </button>
        )}
        {!key.from_env && !key.is_local && (
          <button onClick={() => handleDelete(key.id)}
            className="w-6 h-6 flex items-center justify-center rounded text-text-muted hover:text-red-400 hover:bg-red-400/[0.08] transition-all text-2xs"
            aria-label={t('apiKeys.delete')} title={t('apiKeys.delete')}>
            <Icon icon="fa-solid fa-trash-can" aria-hidden="true" />
          </button>
        )}
        {key.from_env && (
          <span className="text-[7px] px-1" style={{ color: 'var(--text-dim)' }}>{t('apiKeys.readOnly')}</span>
        )}
      </div>
    </div>
  )

  const renderProviderModal = () => (
    <div className="fixed inset-0 z-[10004] flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.5)' }}
      onClick={e => { if (e.target === e.currentTarget) { setShowAddProvider(false); setEditingKey(null) }}}>
      <div className="w-[420px] rounded-2xl border shadow-xl" style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}>
        <div className="flex items-center justify-between px-4 h-10 border-b" style={{ borderColor: 'var(--separator)' }}>
          <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
            {editingKey ? t('apiKeys.editProvider') : t('apiKeys.addProvider')}
          </span>
          <button onClick={() => { setShowAddProvider(false); setEditingKey(null) }}
            className="w-5 h-5 flex items-center justify-center rounded text-text-muted hover:text-text-secondary hover:bg-white/[0.06] text-2xs"
            aria-label={t('apiKeys.close')} title={t('apiKeys.close')}>
            <Icon icon="fa-solid fa-xmark" aria-hidden="true" />
          </button>
        </div>
        {modalError && (
          <div className="mx-3 mt-2 px-2 py-1 rounded-lg text-2xs" style={{ background: 'var(--danger)18', color: 'var(--danger)' }}>
            {modalError}
          </div>
        )}
        <ProviderForm
          initial={editingKey}
          purposeLabels={purposeLabels}
          onSave={async (data) => {
            setModalError('')
            try {
              if (editingKey) {
                await wsClient.call('api_keys.update', { key_id: editingKey.id, ...data })
              } else {
                await wsClient.call('api_keys.create', { ...data, is_provider: true })
              }
              setShowAddProvider(false)
              setEditingKey(null)
              fetchKeys()
            } catch (e: unknown) {
              setModalError(formatError(e, t('apiKeys.saveFailed')))
            }
          }}
          onCancel={() => { setShowAddProvider(false); setEditingKey(null) }}
        />
      </div>
    </div>
  )

  return (
    <div className="fixed inset-0 z-[10003] flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.4)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="w-[640px] h-[560px] rounded-2xl border shadow-xl flex flex-col overflow-hidden"
        style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 h-10 border-b flex-shrink-0" style={{ borderColor: 'var(--separator)' }}>
          <div className="flex items-center gap-2">
            <Icon icon="fa-solid fa-key text-xs" style={{ color: 'var(--text-primary)' }} aria-hidden="true" />
            <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{t('apiKeys.title')}</span>
            <span className="text-[8px] px-1.5 py-0.5 rounded-full" style={{ background: 'var(--hover-bg)', color: 'var(--text-dim)' }}>
              {keys.length}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={() => { setEditingKey(null); setShowAddProvider(true) }}
              className="flex items-center gap-1 px-2 h-6 rounded-lg text-2xs font-medium transition-all"
              style={{ background: 'var(--hover-bg)', color: 'var(--text-primary)' }}>
              <Icon icon="fa-solid fa-plus text-[8px]" aria-hidden="true" />
              {t('apiKeys.addProvider')}
            </button>
            <button onClick={onClose}
              className="w-6 h-6 flex items-center justify-center rounded text-text-muted hover:text-text-secondary hover:bg-white/[0.06] text-xs"
              aria-label={t('apiKeys.close')} title={t('apiKeys.close')}>
              <Icon icon="fa-solid fa-xmark" aria-hidden="true" />
            </button>
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="mx-3 mt-2 px-2 py-1 rounded-lg text-2xs" style={{ background: 'var(--danger)18', color: 'var(--danger)' }}>
            {error}
          </div>
        )}

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-3 space-y-4">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-4 h-4 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
            </div>
          ) : keys.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 gap-2">
              <Icon icon="fa-solid fa-key text-2xl" style={{ color: 'var(--text-dim)' }} aria-hidden="true" />
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('apiKeys.empty')}</div>
            </div>
          ) : (
            Object.entries(categoryConfig).map(([cat, cfg]) => {
              const items = groupedKeys(cat)
              if (items.length === 0) return null
              return (
                <div key={cat}>
                  <div className="flex items-center gap-1.5 mb-1.5 px-0.5">
                    <div className="w-1.5 h-1.5 rounded-full" style={{ background: cfg.color }} />
                    <span className="text-2xs font-medium" style={{ color: 'var(--text-secondary)' }}>{cfg.label}</span>
                    <span className="text-[8px]" style={{ color: 'var(--text-dim)' }}>({items.length})</span>
                  </div>
                  <div className="space-y-1">
                    {items.map(renderKeyRow)}
                  </div>
                </div>
              )
            })
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-2 px-3 h-8 border-t flex-shrink-0" style={{ borderColor: 'var(--separator)' }}>
          <div className="w-2 h-2 rounded-full" style={{ background: 'var(--success)' }} />
          <span className="text-[7px]" style={{ color: 'var(--text-dim)' }}>{t('apiKeys.statusConfigured')}</span>
          <div className="w-2 h-2 rounded-full" style={{ background: '#6b7280' }} />
          <span className="text-[7px]" style={{ color: 'var(--text-dim)' }}>{t('apiKeys.statusUnset')}</span>
          <div className="w-2 h-2 rounded-full" style={{ background: '#eab308' }} />
          <span className="text-[7px]" style={{ color: 'var(--text-dim)' }}>{t('apiKeys.statusEnv')}</span>
        </div>
      </div>

      {showAddProvider && renderProviderModal()}
    </div>
  )
}

// ── Provider Form ──

interface ProviderFormProps {
  initial: ApiKeyEntry | null
  purposeLabels: Record<string, { label: string; color: string }>
  onSave: (data: ProviderFormData) => Promise<void>
  onCancel: () => void
}

function ProviderForm({ initial, purposeLabels, onSave, onCancel }: ProviderFormProps) {
  const { t } = useTranslation()
  const [name, setName] = useState(initial?.name || '')
  const [apiBase, setApiBase] = useState(initial?.base_url || '')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState(initial?.model || '')
  const [purpose, setPurpose] = useState(initial?.purpose || 'general')
  const [isLocal, setIsLocal] = useState(initial?.is_local || false)
  const [isFallback, setIsFallback] = useState(initial?.is_fallback || false)
  const [description, setDescription] = useState(initial?.description || '')
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      const payload: ProviderFormData = {
        name: name.trim(),
        base_url: apiBase.trim(),
        model: model.trim(),
        is_local: isLocal,
        is_fallback: isFallback,
        purpose: purpose,
        description: description.trim(),
      }
      if (apiKey.trim()) {
        payload.key_value = apiKey.trim()
      } else if (!initial) {
        payload.key_value = ''
      }
      await onSave(payload)
    } finally {
      setSaving(false)
    }
  }

  const fieldStyle: React.CSSProperties = {
    background: 'var(--input-bg)',
    borderColor: 'var(--panel-border)',
    color: 'var(--text-primary)',
  }

  return (
    <div className="p-3 space-y-2.5">
      <div className="flex gap-2">
        <div className="flex-1">
          <label className="text-[8px] font-medium block mb-0.5" style={{ color: 'var(--text-muted)' }}>{t('apiKeys.form.name')}</label>
          <input value={name} onChange={e => setName(e.target.value)}
            placeholder="DeepSeek, OpenAI..."
            className="w-full px-2 py-1.5 rounded-lg border text-xs outline-none"
            style={fieldStyle} />
        </div>
        <div className="flex-1">
          <label className="text-[8px] font-medium block mb-0.5" style={{ color: 'var(--text-muted)' }}>{t('apiKeys.form.model')}</label>
          <input value={model} onChange={e => setModel(e.target.value)}
            placeholder="deepseek-chat"
            className="w-full px-2 py-1.5 rounded-lg border text-xs outline-none"
            style={fieldStyle} />
        </div>
      </div>
      <div>
        <label className="text-[8px] font-medium block mb-0.5" style={{ color: 'var(--text-muted)' }}>{t('apiKeys.form.apiUrl')}</label>
        <input value={apiBase} onChange={e => setApiBase(e.target.value)}
          placeholder="https://api.deepseek.com/v1"
          className="w-full px-2 py-1.5 rounded-lg border text-xs outline-none"
          style={fieldStyle} />
      </div>
      <div>
        <label className="text-[8px] font-medium block mb-0.5" style={{ color: 'var(--text-muted)' }}>
          {t('apiKeys.form.apiKey')} {initial ? t('apiKeys.form.apiKeyKeepHint') : ''}
        </label>
        <input value={apiKey} onChange={e => setApiKey(e.target.value)}
          type="password" placeholder={initial ? t('apiKeys.form.apiKeyReplacePlaceholder') : 'sk-...'}
          className="w-full px-2 py-1.5 rounded-lg border text-xs outline-none"
          style={fieldStyle} />
      </div>
      <div className="flex gap-2">
        <div className="flex-1">
          <label className="text-[8px] font-medium block mb-0.5" style={{ color: 'var(--text-muted)' }}>{t('apiKeys.form.purpose')}</label>
          <select value={purpose} onChange={e => setPurpose(e.target.value)}
            className="w-full px-2 py-1.5 rounded-lg border text-xs outline-none appearance-none"
            style={fieldStyle}>
            {Object.entries(purposeLabels).map(([val, { label }]) => (
              <option key={val} value={val}>{label}</option>
            ))}
          </select>
        </div>
        <div className="flex-1">
          <label className="text-[8px] font-medium block mb-0.5" style={{ color: 'var(--text-muted)' }}>{t('apiKeys.form.note')}</label>
          <input value={description} onChange={e => setDescription(e.target.value)}
            placeholder={t('apiKeys.form.notePlaceholder')}
            className="w-full px-2 py-1.5 rounded-lg border text-xs outline-none"
            style={fieldStyle} />
        </div>
      </div>
      <div className="flex items-center gap-4">
        <label className="flex items-center gap-1.5 text-2xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={isLocal} onChange={e => setIsLocal(e.target.checked)} className="w-3 h-3 rounded" />
          {t('apiKeys.form.isLocal')}
        </label>
        <label className="flex items-center gap-1.5 text-2xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={isFallback} onChange={e => setIsFallback(e.target.checked)} className="w-3 h-3 rounded" />
          {t('apiKeys.form.isFallback')}
        </label>
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onCancel}
          className="px-3 h-6 rounded-lg text-2xs transition-all"
          style={{ background: 'var(--hover-bg)', color: 'var(--text-muted)' }}>
          {t('apiKeys.form.cancel')}
        </button>
        <button onClick={handleSave} disabled={!name.trim() || saving}
          className="px-3 h-6 rounded-lg text-2xs font-medium transition-all disabled:opacity-40"
          style={{ background: '#3b82f618', color: '#3b82f6' }}>
          {saving ? t('apiKeys.form.saving') : (initial ? t('apiKeys.form.update') : t('apiKeys.form.add'))}
        </button>
      </div>
    </div>
  )
}
