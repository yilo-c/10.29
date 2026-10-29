import React, { useState, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'
import { useProviderManager, useTranslation, getWsClient } from '@desk-notes/shared'

/** 对外 LLM 中继网关（OpenAI / Anthropic 兼容端点）配置面板。
 *
 * 复用后端统一的 provider 池与回退链，给外部 agent 暴露 OpenAI `/v1/chat/completions`
 * 与 Anthropic `/v1/messages` 双协议端点（new-api 风格模型别名重定向）。
 * 运行信息来自 `relay.status`；模型映射与 key 通过 `settings.set` 持久化，需重启后端生效。
 */
const RelayGatewayPanel: React.FC = () => {
  const { t } = useTranslation()
  const { providers } = useProviderManager()

  interface RelayStatus {
    enabled?: boolean
    has_key?: boolean
    api_key_masked?: string
    model_map?: Record<string, string>
    base_url?: string
    endpoints?: { openai_chat?: string; anthropic_messages?: string }
  }

  const [status, setStatus] = useState<RelayStatus | null>(null)
  const [rows, setRows] = useState<Array<{ id: number; alias: string; target: string }>>([])
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedMsg, setSavedMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [generatedKey, setGeneratedKey] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)

  const load = useCallback(async () => {
    const client = getWsClient()
    if (!client || client.status !== 'connected') return
    try {
      const st = await client.call<RelayStatus>('relay.status').catch(() => null)
      setStatus(st)
      let map: Record<string, string> = {}
      if (st?.model_map && typeof st.model_map === 'object' && !Array.isArray(st.model_map)) {
        map = st.model_map
      } else {
        map = await client.call<Record<string, string>>('settings.get', { key: 'relay_model_map' }).catch(() => ({}))
      }
      const entries: Array<[string, string]> = Object.entries(map).filter(
        (a): a is [string, string] => typeof a[1] === 'string'
      )
      setRows(entries.map(([k, v], i) => ({ id: i, alias: k, target: v })))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => {
    load()
    const client = getWsClient()
    const off = client.onStatusChange((s) => { if (s === 'connected') load() })
    return off
  }, [load])

  const addRow = () => setRows(r => [...r, { id: Date.now(), alias: '', target: '' }])
  const updateRow = (id: number, patch: Partial<{ alias: string; target: string }>) =>
    setRows(r => r.map(x => (x.id === id ? { ...x, ...patch } : x)))
  const removeRow = (id: number) => setRows(r => r.filter(x => x.id !== id))

  const handleRegenerate = async () => {
    const client = getWsClient()
    if (!client) return
    setGenerating(true)
    setError(null)
    try {
      const res = await client.call<{ relay_api_key: string; restart_required?: boolean }>('relay.regenerate_key')
      setGeneratedKey(res.relay_api_key)
      setStatus(s => s ? { ...s, has_key: true, api_key_masked: `${res.relay_api_key.slice(0, 8)}…` } : s)
      setSavedMsg(t('aiMgmt.relay.savedRestart'))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setGenerating(false)
    }
  }

  const handleSave = async () => {
    const client = getWsClient()
    if (!client) return
    setSaving(true)
    setError(null)
    setSavedMsg(null)
    try {
      const map: Record<string, string> = {}
      for (const r of rows) {
        const alias = r.alias.trim()
        if (alias) map[alias] = r.target.trim()
      }
      await client.call('settings.set', { key: 'relay_model_map', value: map })
      setSavedMsg(t('aiMgmt.relay.savedRestart'))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setSaving(false)
    }
  }

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {})
    setSavedMsg(t('aiMgmt.relay.copied'))
  }

  const endpoints = status?.endpoints || {}

  return (
    <div className="rounded-xl border overflow-hidden" style={{ borderColor: 'var(--panel-border)' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b" style={{ borderColor: 'var(--panel-border)' }}>
        <div className="flex items-center gap-2">
          <Icon icon="fa-solid fa-network-wired text-xs" style={{ color: 'var(--text-secondary)' }} />
          <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{t('aiMgmt.relay.title')}</span>
          <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>{t('aiMgmt.relay.subtitle')}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${status?.enabled ? 'bg-emerald-400' : 'bg-gray-500'}`} />
          <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
            {status?.enabled ? t('aiMgmt.relay.enabled') : t('aiMgmt.relay.offline')}
          </span>
        </div>
      </div>

      <div className="p-3 flex flex-col gap-3">
        {/* Endpoints */}
        {status?.enabled ? (
          <div className="space-y-1.5">
            <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('aiMgmt.relay.endpointsLabel')}</span>
            <EndpointRow label="OpenAI" value={endpoints.openai_chat || ''} onCopy={copy} />
            <EndpointRow label="Anthropic" value={endpoints.anthropic_messages || ''} onCopy={copy} />
          </div>
        ) : (
          <div className="text-2xs" style={{ color: 'var(--text-dim)' }}>{t('aiMgmt.relay.notConfigured')}</div>
        )}

        {/* API key */}
        <div className="flex flex-col gap-1.5">
          <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('aiMgmt.relay.apiKeyLabel')}</span>
          <div className="flex items-center gap-2">
            <div
              className="flex-1 px-2 py-1.5 rounded-lg border font-mono text-2xs truncate"
              style={{ borderColor: 'var(--panel-border)', background: 'rgba(0,0,0,0.2)', color: 'var(--text-dim)' }}
            >
              {generatedKey || status?.api_key_masked || t('aiMgmt.relay.noKey')}
            </div>
            <button
              onClick={handleRegenerate}
              disabled={generating}
              className="text-2xs px-2.5 py-1.5 rounded-lg border transition-colors whitespace-nowrap disabled:opacity-50"
              style={{ borderColor: 'var(--panel-border)', color: 'var(--text-muted)' }}
              onMouseEnter={e => { if (!(e.currentTarget as HTMLButtonElement).disabled) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
            >
              <Icon icon={`fa-solid fa-${generating ? 'spinner fa-spin' : 'rotate'} mr-1`} />
              {generating ? t('aiMgmt.relay.generating') : t('aiMgmt.relay.regenerate')}
            </button>
            {generatedKey && (
              <button
                onClick={() => copy(generatedKey)}
                className="text-2xs px-2.5 py-1.5 rounded-lg hover:bg-white/[0.06] transition-colors whitespace-nowrap"
                style={{ color: 'var(--text-muted)' }}
              >
                <Icon icon="fa-solid fa-copy mr-1" />{t('aiMgmt.relay.copy')}
              </button>
            )}
          </div>
          {generatedKey && (
            <span className="text-2xs break-all font-mono" style={{ color: 'var(--text-dim)' }}>
              {t('aiMgmt.relay.newKeyHint')}: {generatedKey}
            </span>
          )}
        </div>

        {/* Model map */}
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('aiMgmt.relay.modelMapLabel')}</span>
            <button onClick={addRow} className="text-2xs px-2 py-0.5 rounded hover:bg-white/[0.06] transition-colors" style={{ color: 'var(--brand)' }}>
              <Icon icon="fa-solid fa-plus mr-1" />{t('aiMgmt.relay.addRow')}
            </button>
          </div>
          <span className="text-2xs leading-relaxed" style={{ color: 'var(--text-dim)' }}>{t('aiMgmt.relay.modelMapHint')}</span>

          {rows.length === 0 ? (
            <div className="text-2xs py-2 text-center rounded-lg" style={{ color: 'var(--text-dim)', background: 'rgba(255,255,255,0.03)' }}>
              {t('aiMgmt.relay.noMap')}
            </div>
          ) : (
            <div className="space-y-1.5">
              {rows.map(r => (
                <div key={r.id} className="flex items-center gap-1.5">
                  <input
                    placeholder={t('aiMgmt.relay.aliasPh')}
                    value={r.alias}
                    onChange={e => updateRow(r.id, { alias: e.target.value })}
                    className="flex-1 text-2xs px-2 py-1 rounded-lg border outline-none"
                    style={{ borderColor: 'var(--panel-border)', background: 'rgba(0,0,0,0.2)', color: 'var(--text-primary)' }}
                  />
                  <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>→</span>
                  <input
                    placeholder={t('aiMgmt.relay.targetPh')}
                    value={r.target}
                    list="relay-providers"
                    onChange={e => updateRow(r.id, { target: e.target.value })}
                    className="flex-1 text-2xs px-2 py-1 rounded-lg border outline-none"
                    style={{ borderColor: 'var(--panel-border)', background: 'rgba(0,0,0,0.2)', color: 'var(--text-primary)' }}
                  />
                  <button onClick={() => removeRow(r.id)} className="text-2xs px-1.5 py-1 rounded hover:bg-red-500/10 transition-colors" style={{ color: '#f87171' }}>
                    <Icon icon="fa-solid fa-xmark" />
                  </button>
                </div>
              ))}
              <datalist id="relay-providers">
                {providers.map(p => <option key={p.name} value={p.name} />)}
              </datalist>
            </div>
          )}
        </div>

        <div className="text-2xs" style={{ color: '#fb923c' }}>
          <Icon icon="fa-solid fa-triangle-exclamation mr-1" />{t('aiMgmt.relay.restartHint')}
        </div>

        {error && (
          <div className="text-2xs px-2 py-1.5 rounded-lg" style={{ color: 'var(--danger)', background: 'rgba(239,68,68,0.08)' }}>
            {error}
          </div>
        )}
        {savedMsg && (
          <div className="text-2xs px-2 py-1.5 rounded-lg" style={{ color: 'var(--brand)', background: 'rgba(96,165,250,0.08)' }}>
            {savedMsg}
          </div>
        )}

        <div className="flex justify-end">
          <button onClick={handleSave} disabled={saving || !loaded}
            className="text-xs px-3 py-1.5 rounded-lg text-white disabled:opacity-50 transition-colors"
            style={{ background: saving ? 'rgba(96,165,250,0.3)' : 'rgba(96,165,250,0.8)' }}
            onMouseEnter={e => { if (!(e.currentTarget as HTMLButtonElement).disabled) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(96,165,250,1)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = saving ? 'rgba(96,165,250,0.3)' : 'rgba(96,165,250,0.8)' }}
          >
            {saving ? t('aiMgmt.relay.saving') : t('aiMgmt.relay.save')}
          </button>
        </div>
      </div>
    </div>
  )
}

const EndpointRow: React.FC<{ label: string; value: string; onCopy: (v: string) => void }> = ({ label, value, onCopy }) => (
  <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg" style={{ background: 'rgba(255,255,255,0.03)' }}>
    <span className="text-2xs font-medium flex-shrink-0" style={{ color: 'var(--text-muted)' }}>{label}</span>
    <span className="text-2xs font-mono truncate flex-1" style={{ color: 'var(--text-dim)' }}>{value}</span>
    <button onClick={() => onCopy(value)} className="text-2xs px-1.5 py-0.5 rounded hover:bg-white/[0.06] transition-colors flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
      <Icon icon="fa-solid fa-copy" />
    </button>
  </div>
)

export default RelayGatewayPanel