import React, { useState, useMemo, useEffect } from 'react'
import Icon from '../Common/Icon'

import { useProviderManager, useTranslation, type ProviderConfig } from '@desk-notes/shared'
import PageHeader from '../Common/PageHeader'
import RelayGatewayPanel from './RelayGatewayPanel'

const PURPOSE_COLORS: Record<string, string> = {
  primary: 'rgba(96,165,250,0.15)',
  fallback: 'rgba(251,146,60,0.15)',
  local: 'rgba(255,255,255,0.06)',
  translation: 'rgba(52,211,153,0.15)',
  summarization: 'rgba(167,139,250,0.15)',
  coding: 'rgba(248,113,113,0.15)',
  general: 'rgba(255,255,255,0.06)',
}

const AIManagementModule: React.FC = () => {
  const { t } = useTranslation()
  const {
    providers, kiroStatus, loading, error,
    addProvider, removeProvider, checkHealth, refreshAllHealth,
  } = useProviderManager()

  const [showAddForm, setShowAddForm] = useState(false)
  const [healthChecking, setHealthChecking] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  // ESC 关闭删除确认弹窗（2026-10-16 E2E sweep 缺陷修复：z-50 modal 家族漏网）
  useEffect(() => {
    if (!confirmDelete) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setConfirmDelete(null)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [confirmDelete])
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const purposeLabels: Record<string, string> = useMemo(() => ({
    primary: t('aiMgmt.purposePrimary'),
    fallback: t('aiMgmt.purposeFallback'),
    local: t('aiMgmt.purposeLocal'),
    translation: t('aiMgmt.purposeTranslation'),
    summarization: t('aiMgmt.purposeSummarization'),
    coding: t('aiMgmt.purposeCoding'),
    general: t('aiMgmt.purposeGeneral'),
  }), [t])

  const handleCheckHealth = async (name: string) => {
    setHealthChecking(name)
    try {
      await checkHealth(name)
    } finally {
      setHealthChecking(null)
    }
  }

  const handleRefreshAll = async () => {
    await refreshAllHealth()
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-4 h-4 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
      </div>
    )
  }

  if (error === 'Not connected') {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8 text-center">
        <div className="text-2xl opacity-25"><Icon icon="fa-solid fa-plug" /></div>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('aiMgmt.backendDisconnected')}</div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 p-5 gap-4 overflow-y-auto">
      {/* Header */}
      <PageHeader
        title={t('aiMgmt.title')}
        actions={(
          <>
            <button
              onClick={handleRefreshAll}
              className="text-xs px-3 py-1.5 rounded-lg border transition-colors"
              style={{
                borderColor: 'var(--panel-border)',
                color: 'var(--text-muted)',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
            >
              <Icon icon="fa-solid fa-rotate mr-1" />
              {t('aiMgmt.refreshHealth')}
            </button>
            <button
              onClick={() => setShowAddForm(true)}
              className="text-xs px-3 py-1.5 rounded-lg transition-all font-medium"
              style={{ background: 'var(--brand-gradient)', color: '#fff' }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.filter = 'brightness(1.15)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.filter = '' }}
            >
              <Icon icon="fa-solid fa-plus mr-1" />
              {t('aiMgmt.addProvider')}
            </button>
          </>
        )}
      />

      {/* Kiro status panel */}
      {kiroStatus && (
        <div
          className="rounded-xl border p-3"
          style={{
            borderColor: kiroStatus.running ? 'rgba(52,211,153,0.2)' : 'var(--panel-border)',
            background: kiroStatus.running ? 'rgba(52,211,153,0.04)' : undefined,
          }}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${kiroStatus.running ? 'bg-emerald-400' : 'bg-gray-500'}`} />
              <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>Kiro-gateway</span>
              <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>{t('aiMgmt.kiroClaudeProxy')}</span>
            </div>
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {kiroStatus.running ? t('aiMgmt.running') : t('aiMgmt.offline')}
            </span>
          </div>
          {kiroStatus.running && (
            <div className="mt-2 flex gap-3 text-xs" style={{ color: 'var(--text-muted)' }}>
              <span>{t('aiMgmt.modelFmt', { models: kiroStatus.models?.join(', ') || t('aiMgmt.unknown') })}</span>
              <span>{t('aiMgmt.accountsFmt', { count: kiroStatus.accounts || 0 })}</span>
            </div>
          )}
          {!kiroStatus.running && (
            <div className="mt-1 text-xs" style={{ color: '#fb923c' }}>
              {t('aiMgmt.kiroNotRunning')}
            </div>
          )}
        </div>
      )}

      {/* 对外 LLM 中继网关（OpenAI / Anthropic 兼容端点） */}
      <RelayGatewayPanel />

      {/* Provider list */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span
            className="text-xs font-medium uppercase tracking-wider"
            style={{ color: 'var(--text-muted)' }}
          >
            {t('aiMgmt.providerCountFmt', { count: providers.length })}
          </span>
        </div>

        {providers.length === 0 && (
          <div className="text-center py-8 text-xs" style={{ color: 'var(--text-dim)' }}>
            {t('aiMgmt.noProviders')}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {providers.map((p: ProviderConfig) => (
            <div
              key={p.name}
              className="rounded-xl border p-3 transition-colors"
              style={{
                borderColor: 'var(--panel-border)',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'rgba(255,255,255,0.15)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--panel-border)' }}
            >
              {/* Card header */}
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <div className={`w-2 h-2 rounded-full ${
                    p.health?.available ? 'bg-emerald-400' :
                    p.health?.available === false ? 'bg-red-400' :
                    p.enabled ? 'bg-yellow-400' : 'bg-gray-600'
                  }`} />
                  <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{p.name}</span>
                  {p.is_local && (
                    <span className="text-[8px] px-1.5 py-0.5 rounded" style={{ background: 'rgba(255,255,255,0.06)', color: 'var(--text-dim)' }}>
                      {t('aiMgmt.local')}
                    </span>
                  )}
                  {p.is_fallback && (
                    <span className="text-[8px] px-1.5 py-0.5 rounded" style={{ background: 'rgba(251,146,60,0.15)', color: '#fb923c' }}>
                      {t('aiMgmt.fallback')}
                    </span>
                  )}
                  {p.purpose && p.purpose !== 'general' && (
                    <span
                      className="text-[8px] px-1.5 py-0.5 rounded"
                      style={{
                        background: PURPOSE_COLORS[p.purpose] || 'rgba(255,255,255,0.06)',
                        color: 'var(--text-dim)',
                      }}
                    >
                      {purposeLabels[p.purpose] || p.purpose}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>
                    {p.enabled ? t('aiMgmt.enabled') : t('aiMgmt.disabled')}
                  </span>
                  {!p.is_local && (
                    <button
                      onClick={() => handleCheckHealth(p.name)}
                      disabled={healthChecking === p.name}
                      className="text-2xs px-2 py-0.5 rounded border transition-colors disabled:opacity-50"
                      style={{ borderColor: 'var(--panel-border)', color: 'var(--text-muted)' }}
                      onMouseEnter={e => { if (!(e.currentTarget as HTMLButtonElement).disabled) (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)' }}
                      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
                    >
                      {healthChecking === p.name ? t('aiMgmt.checking') : t('aiMgmt.check')}
                    </button>
                  )}
                  {!p.is_local && (
                    <button
                      onClick={() => setConfirmDelete(p.name)}
                      className="text-2xs px-2 py-0.5 rounded transition-colors"
                      style={{ color: '#f87171' }}
                      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'rgba(248,113,113,0.1)' }}
                      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
                    >
                      {t('aiMgmt.delete')}
                    </button>
                  )}
                </div>
              </div>

              {/* API Base */}
              <div className="text-xs font-mono truncate mb-1" style={{ color: 'var(--text-dim)' }}>
                {p.api_base}
              </div>

              {/* Model */}
              <div className="flex items-center gap-1 flex-wrap">
                <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>{t('aiMgmt.modelLabel')}</span>
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{p.model}</span>
                {p.models.length > 1 && (
                  <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>
                    +{p.models.length - 1}
                  </span>
                )}
              </div>

              {/* Health info */}
              {p.health && (
                <div
                  className="mt-1 text-2xs"
                  style={{ color: p.health.available ? 'var(--text-muted)' : '#f87171' }}
                >
                  {p.health.message}
                </div>
              )}

              {/* has_api_key indicator */}
              {!p.health && (
                <div className="mt-1 text-2xs" style={{ color: 'var(--text-dim)' }}>
                  {p.has_api_key ? t('aiMgmt.hasApiKey') : t('aiMgmt.noApiKey')}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Confirm delete dialog */}
      {confirmDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          role="dialog"
          aria-modal="true"
          aria-label={t('aiMgmt.confirmDeleteTitle')}
        >
          <div
            className="rounded-xl border p-4 w-72 shadow-xl"
            style={{ borderColor: 'var(--panel-border)', background: 'var(--bg-card, #1e1e2a)' }}
          >
            <div className="mb-3">
              <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{t('aiMgmt.confirmDeleteTitle')}</span>
            </div>
            <p
              className="text-xs mb-4"
              style={{ color: 'var(--text-muted)' }}
              dangerouslySetInnerHTML={{ __html: t('aiMgmt.confirmDeleteBodyFmt', { name: confirmDelete }) }}
            />
            {deleteError && (
              <div className="mb-3 text-xs p-2 rounded" style={{ background: 'rgba(248,113,113,0.1)', color: '#f87171' }}>
                {deleteError}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="text-xs px-3 py-1.5 rounded-lg border transition-colors"
                style={{ borderColor: 'var(--panel-border)', color: 'var(--text-muted)' }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)' }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
              >
                {t('aiMgmt.cancel')}
              </button>
              <button
                onClick={async () => {
                  setDeleting(true)
                  setDeleteError(null)
                  try {
                    await removeProvider(confirmDelete)
                    setConfirmDelete(null)
                  } catch (e) {
                    setDeleteError(e instanceof Error ? e.message : t('aiMgmt.deleteFailed'))
                  } finally {
                    setDeleting(false)
                  }
                }}
                disabled={deleting}
                className="text-xs px-3 py-1.5 rounded-lg transition-colors text-white disabled:opacity-50"
                style={{ background: 'var(--danger)' }}
                onMouseEnter={e => { if (!(e.currentTarget as HTMLButtonElement).disabled) (e.currentTarget as HTMLButtonElement).style.background = '#dc2626' }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'var(--danger)' }}
              >
                {deleting ? t('aiMgmt.deleting') : t('aiMgmt.confirmDeleteBtn')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add provider form */}
      {showAddForm && (
        <AddProviderForm
          onClose={() => setShowAddForm(false)}
          onAdd={async (config) => {
            await addProvider(config)
            setShowAddForm(false)
          }}
        />
      )}
    </div>
  )
}

// Add provider form subcomponent
interface AddProviderFormProps {
  onClose: () => void
  onAdd: (config: Record<string, unknown>) => Promise<void>
}

function AddProviderForm({ onClose, onAdd }: AddProviderFormProps) {
  // ESC 关闭表单（2026-10-16 E2E sweep 缺陷修复）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [apiBase, setApiBase] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleSubmit = async () => {
    if (!name || !apiBase) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      await onAdd({
        name,
        api_base: apiBase,
        api_key: apiKey || undefined,
        model: model || 'gpt-4o',
        enabled: true,
      })
    } catch (err) {
      // 添加失败：留在表单内展示错误（后端未连接 / RPC 失败 / 校验失败）
      setSubmitError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-label={t('aiMgmt.addFormTitle')}
    >
      <div
        className="rounded-xl border p-4 w-80 shadow-xl"
        style={{ borderColor: 'var(--panel-border)', background: 'var(--bg-card, #1e1e2a)' }}
      >
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{t('aiMgmt.addFormTitle')}</span>
          <button onClick={onClose} aria-label={t('aiMgmt.close')} className="text-xs" style={{ color: 'var(--text-dim)' }}>
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        <div className="space-y-2">
          <input
            placeholder={t('aiMgmt.namePlaceholder')}
            value={name}
            onChange={e => setName(e.target.value)}
            className="w-full text-xs px-2 py-1.5 rounded-lg border outline-none"
            style={{
              borderColor: 'var(--panel-border)',
              background: 'rgba(0,0,0,0.2)',
              color: 'var(--text-primary)',
            }}
            onFocus={e => { (e.currentTarget as HTMLElement).style.borderColor = 'rgba(96,165,250,0.5)' }}
            onBlur={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--panel-border)' }}
          />
          <input
            placeholder={t('aiMgmt.apiBasePlaceholder')}
            value={apiBase}
            onChange={e => setApiBase(e.target.value)}
            className="w-full text-xs px-2 py-1.5 rounded-lg border outline-none"
            style={{
              borderColor: 'var(--panel-border)',
              background: 'rgba(0,0,0,0.2)',
              color: 'var(--text-primary)',
            }}
            onFocus={e => { (e.currentTarget as HTMLElement).style.borderColor = 'rgba(96,165,250,0.5)' }}
            onBlur={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--panel-border)' }}
          />
          <input
            placeholder={t('aiMgmt.apiKeyPlaceholder')}
            type="password"
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            className="w-full text-xs px-2 py-1.5 rounded-lg border outline-none"
            style={{
              borderColor: 'var(--panel-border)',
              background: 'rgba(0,0,0,0.2)',
              color: 'var(--text-primary)',
            }}
            onFocus={e => { (e.currentTarget as HTMLElement).style.borderColor = 'rgba(96,165,250,0.5)' }}
            onBlur={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--panel-border)' }}
          />
          <input
            placeholder={t('aiMgmt.modelPlaceholder')}
            value={model}
            onChange={e => setModel(e.target.value)}
            className="w-full text-xs px-2 py-1.5 rounded-lg border outline-none"
            style={{
              borderColor: 'var(--panel-border)',
              background: 'rgba(0,0,0,0.2)',
              color: 'var(--text-primary)',
            }}
            onFocus={e => { (e.currentTarget as HTMLElement).style.borderColor = 'rgba(96,165,250,0.5)' }}
            onBlur={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--panel-border)' }}
          />
        </div>

        {submitError && (
          <div
            className="text-2xs px-2 py-1.5 rounded-lg mt-2 break-words"
            style={{ color: 'var(--danger)', background: 'rgba(239,68,68,0.08)' }}
          >
            {t('aiMgmt.addFailed')}: {submitError}
          </div>
        )}

        <div className="flex justify-end gap-2 mt-3">
          <button
            onClick={onClose}
            className="text-xs px-3 py-1.5 rounded-lg border transition-colors"
            style={{ borderColor: 'var(--panel-border)', color: 'var(--text-muted)' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-primary)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)' }}
          >
            {t('aiMgmt.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !name || !apiBase}
            className="text-xs px-3 py-1.5 rounded-lg text-white disabled:opacity-50 transition-colors"
            style={{ background: submitting || !name || !apiBase ? 'rgba(96,165,250,0.3)' : 'rgba(96,165,250,0.8)' }}
            onMouseEnter={e => { if (!(e.currentTarget as HTMLButtonElement).disabled) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(96,165,250,1)' }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = submitting || !name || !apiBase ? 'rgba(96,165,250,0.3)' : 'rgba(96,165,250,0.8)' }}
          >
            {submitting ? t('aiMgmt.adding') : t('aiMgmt.addBtn')}
          </button>
        </div>
      </div>
    </div>
  )
}

export default AIManagementModule