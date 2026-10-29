import React, { useState, useCallback, useEffect, useMemo } from 'react'
import Icon from './Icon'

import { motion } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { ModelSelector } from '../Modules/ModelSelector'
import ApiKeyManager from '../Common/ApiKeyManager'
import { getWsClient } from '../../utils/ai'
import { useTranslation } from '../../i18n'
import type { ModelInfo } from '@desk-notes/shared'

interface ProviderEntry {
  name: string
  api_base: string
  api_key: string
  model: string
  models?: string[]
  timeout: number
  enabled: boolean
  is_fallback: boolean
  is_local: boolean
  description?: string
}

interface Props {
  onClose: () => void
}

// ── AI 权限（B1/B2，2026-09-11）──────────────────────────────────────────
// 后端五键的 UI 钥匙孔：此前 sandbox_level / approval_policy / always_allow
// 等只能手改数据库。即改即存（选档立即 settings.set），名单移除走
// confirmation.revoke_always（授予/收回对称）。
const SANDBOX_LEVELS = [
  { value: 'read-only', label: 'aiSettings.sandboxReadOnly' },
  { value: 'workspace-write', label: 'aiSettings.sandboxWorkspaceWrite' },
  { value: 'full-access', label: 'aiSettings.sandboxFullAccess' },
]
const APPROVAL_POLICIES = [
  { value: 'never', label: 'aiSettings.approvalNever' },
  { value: 'on-risk', label: 'aiSettings.approvalOnRisk' },
  { value: 'always', label: 'aiSettings.approvalAlways' },
]

const AIPermissionsCard: React.FC = () => {
  const { t } = useTranslation()
  const [sandbox, setSandbox] = useState('full-access')
  const [approval, setApproval] = useState('on-risk')
  const [alwaysAllow, setAlwaysAllow] = useState<string[]>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const ws = getWsClient()
    if (ws.status !== 'connected') return
    Promise.all([
      ws.call('settings.get', { key: 'assistant_sandbox_level' }),
      ws.call('settings.get', { key: 'assistant_approval_policy' }),
      ws.call('settings.get', { key: 'assistant_confirm_always_allow' }),
    ])
      .then(([sb, ap, aa]: [unknown, unknown, unknown]) => {
        if (typeof sb === 'string' && sb) setSandbox(sb)
        if (typeof ap === 'string' && ap) setApproval(ap)
        if (Array.isArray(aa)) setAlwaysAllow(aa.filter((x): x is string => typeof x === 'string'))
      })
      .catch(() => {})
      .finally(() => setLoaded(true))
  }, [])

  const persist = useCallback((key: string, value: string) => {
    const ws = getWsClient()
    if (ws.status !== 'connected') return
    ws.call('settings.set', { key, value }).catch(() => {})
  }, [])

  const pickSandbox = useCallback((v: string) => { setSandbox(v); persist('assistant_sandbox_level', v) }, [persist])
  const pickApproval = useCallback((v: string) => { setApproval(v); persist('assistant_approval_policy', v) }, [persist])

  const revoke = useCallback((action: string) => {
    const ws = getWsClient()
    if (ws.status !== 'connected') return
    ws.call('confirmation.revoke_always', { action_id: action })
      .then(() => setAlwaysAllow(list => list.filter(a => a !== action)))
      .catch(() => {})
  }, [])

  const segButton = (active: boolean) =>
    `flex-1 px-2 py-1.5 rounded-md text-xs font-medium transition-all ${active ? 'bg-fluent-blue/20' : 'hover:bg-white/[0.05]'}`
  const segStyle = (active: boolean) => ({ color: active ? 'var(--brand)' : 'var(--text-muted)' })

  return (
    <div className="flex flex-col gap-2 px-3 py-2.5 rounded-xl" style={{ background: 'var(--hover-bg)' }}>
      <div className="flex items-center gap-2">
        <Icon icon="fa-solid fa-shield-halved text-xs" style={{ color: 'var(--text-secondary)' }} />
        <span className="text-xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('aiSettings.aiPermsBadge')}</span>
      </div>

      {/* Sandbox level: what it may do */}
      <div className="flex flex-col gap-1">
        <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('aiSettings.sandboxLabel')}</span>
        <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'rgba(255,255,255,0.04)' }}>
          {SANDBOX_LEVELS.map(o => (
            <button key={o.value} onClick={() => pickSandbox(o.value)}
              className={segButton(sandbox === o.value)} style={segStyle(sandbox === o.value)}>
              {t(o.label)}
            </button>
          ))}
        </div>
      </div>

      {/* Approval policy: when to ask */}
      <div className="flex flex-col gap-1">
        <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('aiSettings.approvalLabel')}</span>
        <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'rgba(255,255,255,0.04)' }}>
          {APPROVAL_POLICIES.map(o => (
            <button key={o.value} onClick={() => pickApproval(o.value)}
              className={segButton(approval === o.value)} style={segStyle(approval === o.value)}>
              {t(o.label)}
            </button>
          ))}
        </div>
      </div>

      {/* Always-allow list: view + revoke */}
      <div className="flex flex-col gap-1">
        <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {t('aiSettings.alwaysAllowTitle', { count: loaded && alwaysAllow.length > 0 ? ` · ${alwaysAllow.length}` : '' })}
        </span>
        {loaded && alwaysAllow.length === 0 ? (
          <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('aiSettings.alwaysAllowEmpty')}</span>
        ) : (
          <div className="space-y-1">
            {alwaysAllow.map(a => (
              <div key={a} className="flex items-center justify-between px-2 py-1 rounded-lg" style={{ background: 'rgba(255,255,255,0.03)' }}>
                <span className="text-xs font-mono truncate" style={{ color: 'var(--text-primary)' }}>{a}</span>
                <button onClick={() => revoke(a)} title={t('aiSettings.revokeTitle')}
                  className="w-4 h-4 flex items-center justify-center rounded text-[9px] hover:bg-white/[0.08] flex-shrink-0"
                  style={{ color: 'var(--text-muted)' }}>
                  <Icon icon="fa-solid fa-xmark" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

const AISettingsPanel: React.FC<Props> = ({ onClose }) => {
  const aiEnabled = useStore(s => s.aiEnabled)
  const aiAutoTagging = useStore(s => s.aiAutoTagging)
  const preferredModelProvider = useStore(s => s.preferredModelProvider)
  const preferredModelName = useStore(s => s.preferredModelName)
  const setPreferredModel = useStore(s => s.setPreferredModel)
  const { t } = useTranslation()

  const [enabled, setEnabled] = useState(aiEnabled)
  const [autoTagging, setAutoTagging] = useState(aiAutoTagging)
  const [showApiKeys, setShowApiKeys] = useState(false)
  const [providers, setProviders] = useState<ProviderEntry[]>([])
  const [loadingProviders, setLoadingProviders] = useState(true)

  // ESC 关闭（2026-10-16 E2E sweep 缺陷修复：modal 家族共性——无 role=dialog /
  // 不响应 ESC，sweep 三级兜底关不掉只能刷新重进）。与 QuickCapture 样板一致。
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  // Load provider_configs when connected
  const loadProviderConfigs = useCallback(() => {
    const ws = getWsClient()
    if (ws.status !== 'connected') return
    setLoadingProviders(true)
    ws.call('settings.get', { key: 'provider_configs' })
      .then((data: unknown) => {
        if (Array.isArray(data)) setProviders(data as ProviderEntry[])
      })
      .catch(() => {})
      .finally(() => setLoadingProviders(false))
  }, [])

  useEffect(() => {
    loadProviderConfigs()
    const unsub = getWsClient().onStatusChange((s) => {
      if (s === 'connected') loadProviderConfigs()
    })
    return unsub
  }, [loadProviderConfigs])

  // Build the selected default model info from store + providers list
  const [allModels, setAllModels] = useState<ModelInfo[]>([])
  useEffect(() => {
    // Collect models from the loaded provider configs (use full models list when available)
    if (!loadingProviders && providers.length > 0) {
      const list: ModelInfo[] = []
      for (const p of providers) {
        const models = Array.isArray(p.models) && p.models.length > 0 ? p.models : (p.model ? [p.model] : [])
        for (const m of models) {
          list.push({ provider: p.name, model: m, api_base: p.api_base })
        }
      }
      setAllModels(list)
    }
  }, [providers, loadingProviders])

  const defaultModel = useMemo<ModelInfo | null>(() => {
    if (!preferredModelProvider || !preferredModelName) return null
    return allModels.find(m => m.provider === preferredModelProvider && m.model === preferredModelName) ?? null
  }, [allModels, preferredModelProvider, preferredModelName])

  const handleSetDefaultModel = useCallback((model: ModelInfo | null) => {
    setPreferredModel(model?.provider ?? null, model?.model ?? null)
  }, [setPreferredModel])

  const handleSave = useCallback(() => {
    useStore.getState().setAIEnabled(enabled)
    useStore.getState().setAIAutoTagging(autoTagging)
    onClose()
  }, [enabled, autoTagging, onClose])

  // Build summary from provider configs
  const primaryProvider = providers.find(p => p.enabled && !p.is_fallback && !p.is_local)
  const localProvider = providers.find(p => p.is_local)
  const enabledCount = providers.filter(p => p.enabled).length

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[10002] flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={t('ai.settings.title')}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 10 }}
        transition={{ duration: 0.15 }}
        className="relative w-[420px] max-w-[90vw] flex flex-col rounded-2xl shadow-2xl overflow-hidden border"
        style={{
          background: 'var(--panel-bg-solid)',
          borderColor: 'var(--panel-border-accent)',
          backdropFilter: 'blur(24px)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--separator)' }}>
          <div className="flex items-center gap-2">
            <Icon icon="fa-solid fa-robot text-xs" style={{ color: 'var(--text-secondary)' }} />
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{t('ai.settings.title')}</span>
          </div>
          <button onClick={onClose}
            aria-label={t('ai.settings.close')}
            className="w-6 h-6 flex items-center justify-center rounded-lg transition-all text-xs"
            style={{ color: 'var(--text-muted)' }}>
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        {/* Content */}
        <div className="flex flex-col gap-3 p-4 overflow-y-auto" style={{ maxHeight: '65vh' }}>
          {/* AI Enable Toggle */}
          <div className="flex items-center justify-between px-3 py-2 rounded-xl" style={{ background: 'var(--hover-bg)' }}>
            <span className="text-xs" style={{ color: 'var(--text-primary)' }}>{t('ai.settings.enable')}</span>
            <button
              onClick={() => setEnabled(!enabled)}
              className={`relative w-9 h-5 rounded-full transition-all ${enabled ? 'bg-fluent-blue' : 'bg-white/[0.10]'}`}
            >
              <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${enabled ? 'left-4' : 'left-0.5'}`} />
            </button>
          </div>

          {enabled && (
            <>
              {/* Provider summary card (read-only) */}
              <div className="flex flex-col gap-2 px-3 py-2.5 rounded-xl" style={{ background: 'var(--hover-bg)' }}>
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('aiSettings.currentConfig')}</span>
                  <span className="text-2xs px-1.5 py-0.5 rounded" style={{ background: 'rgba(96,165,250,0.1)', color: 'var(--brand)' }}>
                    {t('aiSettings.providerCount', { count: enabledCount })}
                  </span>
                </div>

                {loadingProviders ? (
                  <div className="flex items-center gap-2 py-2">
                    <div className="w-3 h-3 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
                    <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('aiSettings.loading')}</span>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {/* Primary (cloud) provider */}
                    {primaryProvider ? (
                      <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg" style={{ background: 'rgba(255,255,255,0.03)' }}>
                        <div className="w-1.5 h-1.5 rounded-full bg-green-400/70 flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                              {primaryProvider.name}
                            </span>
                            <span className="text-[8px] px-1 py-0.5 rounded bg-white/[0.04]" style={{ color: 'var(--text-muted)' }}>{t('aiSettings.cloudBadge')}</span>
                          </div>
                          <div className="text-2xs truncate" style={{ color: 'var(--text-muted)' }}>
                            {primaryProvider.model} · {primaryProvider.api_base?.replace(/^https?:\/\//, '').replace(/\/v1\/?$/, '')}
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg" style={{ background: 'rgba(255,255,255,0.03)' }}>
                        <div className="w-1.5 h-1.5 rounded-full bg-amber-400/70 flex-shrink-0" />
                        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('aiSettings.cloudNotConfigured')}</span>
                      </div>
                    )}

                    {/* Local provider */}
                    {localProvider && (
                      <div className="flex items-center gap-2 px-2 py-1.5 rounded-lg" style={{ background: 'rgba(255,255,255,0.03)' }}>
                        <div className="w-1.5 h-1.5 rounded-full bg-blue-400/70 flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                              {localProvider.name}
                            </span>
                            <span className="text-[8px] px-1 py-0.5 rounded bg-white/[0.04]" style={{ color: 'var(--text-muted)' }}>{t('aiSettings.localBadge')}</span>
                          </div>
                          <div className="text-2xs truncate" style={{ color: 'var(--text-muted)' }}>
                            {localProvider.model} · {localProvider.api_base?.replace(/^https?:\/\//, '').replace(/\/v1\/?$/, '')}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Navigate to ApiKeyManager */}
                <button
                  onClick={() => setShowApiKeys(true)}
                  className="flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-all mt-1 border border-white/[0.06] hover:bg-white/[0.04]"
                  style={{ color: 'var(--brand)' }}
                >
                  <Icon icon="fa-solid fa-key text-xs" />
                  {t('aiSettings.manageApiKeys')}
                  <Icon icon="fa-solid fa-chevron-right text-[8px] ml-auto opacity-50" />
                </button>
              </div>

              {/* Auto Tagging Toggle */}
              <div className="flex items-center justify-between px-3 py-2 rounded-xl" style={{ background: 'var(--hover-bg)' }}>
                <span className="text-xs" style={{ color: 'var(--text-primary)' }}>{t('ai.settings.autoTagging')}</span>
                <button
                  onClick={() => setAutoTagging(!autoTagging)}
                  className={`relative w-9 h-5 rounded-full transition-all ${autoTagging ? 'bg-fluent-blue' : 'bg-white/[0.10]'}`}
                >
                  <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${autoTagging ? 'left-4' : 'left-0.5'}`} />
                </button>
              </div>

              {/* Default Model Selector */}
              <div className="flex items-center justify-between px-3 py-2 rounded-xl" style={{ background: 'var(--hover-bg)' }}>
                <div className="flex flex-col gap-0.5">
                  <span className="text-xs" style={{ color: 'var(--text-primary)' }}>{t('aiSettings.defaultModel')}</span>
                  <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('aiSettings.defaultModelHint')}</span>
                </div>
                <ModelSelector selected={defaultModel} onSelect={handleSetDefaultModel} />
              </div>

              {/* AI permissions (B1/B2): sandbox / approval / always-allow */}
              <AIPermissionsCard />
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t" style={{ borderColor: 'var(--separator)' }}>
          <button onClick={onClose}
            className="px-4 py-1.5 rounded-lg text-xs transition-colors"
            style={{ color: 'var(--text-muted)' }}>
            {t('common.close')}
          </button>
          <button onClick={handleSave}
            className="px-4 py-1.5 rounded-lg text-xs bg-fluent-blue/15 text-fluent-blue hover:bg-fluent-blue/20 transition-colors">
            {t('common.save')}
          </button>
        </div>
      </motion.div>

      {showApiKeys && <ApiKeyManager onClose={() => setShowApiKeys(false)} />}
    </motion.div>
  )
}

export default AISettingsPanel
