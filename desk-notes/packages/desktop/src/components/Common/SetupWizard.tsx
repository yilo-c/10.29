import React, { useEffect, useRef, useState } from 'react'
import { useTranslation } from '@desk-notes/shared'
import { getWsClient } from '../../utils/ai'
import Icon from './Icon'

/**
 * 桌面端首次配置向导。
 * 手机端 Onboarding 只做产品介绍；桌面端此前完全缺失首次配置，
 * 导致新用户不知道要去设置里填 API Key。这里补齐首次启动引导。
 *
 * 触发：localStorage['siyi-onboarding-done'] 不存在时强制展示。
 * 落盘：通过 settings.set 写入 llm_api_key / llm_model / device_id /
 *       sync_token / desknotes_bridge，并由后端自动重建 AI 客户端。
 */

const ONBOARD_KEY = 'siyi-onboarding-done'

type Step = 'welcome' | 'ai' | 'sync' | 'done'

const MODEL_OPTIONS = [
  { value: 'deepseek-chat', label: 'DeepSeek Chat（默认）' },
  { value: 'deepseek-reasoner', label: 'DeepSeek Reasoner' },
  { value: 'moonshot-v1-8k', label: 'Moonshot（路由）' },
  { value: 'qwen3-4b', label: 'Ollama 本地 qwen3-4b' },
]

/** 测试连接时按所选模型推断 provider 的 API 地址 */
const MODEL_BASE_URLS: Record<string, string> = {
  'deepseek-chat': 'https://api.deepseek.com/v1',
  'deepseek-reasoner': 'https://api.deepseek.com/v1',
  'moonshot-v1-8k': 'https://api.moonshot.cn/v1',
  'qwen3-4b': 'http://localhost:11434/v1',
}

type TestStatus = 'idle' | 'testing' | 'ok' | 'fail'

const SetupWizard: React.FC = () => {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [step, setStep] = useState<Step>('welcome')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('deepseek-chat')
  const [deviceId, setDeviceId] = useState('desktop-main')
  const [syncToken, setSyncToken] = useState('')
  const [bridge, setBridge] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // UX 修复（升级方案 2.2）：密文显隐 / 格式校验 / 连通性测试
  const [showKey, setShowKey] = useState(false)
  const [showToken, setShowToken] = useState(false)
  const [keyFormatError, setKeyFormatError] = useState(false)
  const [testStatus, setTestStatus] = useState<TestStatus>('idle')
  const [testReason, setTestReason] = useState('')
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    let done = false
    try {
      done = localStorage.getItem(ONBOARD_KEY) === '1'
    } catch { /* ignore */ }
    if (!done) setOpen(true)
  }, [])

  /** apiKey 非空但不以 sk- 开头视为格式错误（留空允许） */
  const apiKeyInvalid = () => {
    const v = apiKey.trim()
    return v.length > 0 && !v.startsWith('sk-')
  }

  const goNextFromAi = () => {
    if (apiKeyInvalid()) {
      setKeyFormatError(true)
      return
    }
    setKeyFormatError(false)
    setStep('sync')
  }

  /**
   * 连通性测试：复用 ApiKeyManager 的 wsClient RPC 路径
   * （api_keys.create → api_keys.test → api_keys.delete），
   * 以临时 disabled provider 做点对点探测，不污染正式配置。
   */
  const testConnection = async () => {
    if (apiKeyInvalid()) {
      setKeyFormatError(true)
      return
    }
    const key = apiKey.trim()
    if (!key) return
    setTestStatus('testing')
    setTestReason('')
    const tempName = `siyi-setup-test-${Date.now()}`
    let createdId: string | null = null
    try {
      const client = getWsClient()
      const created = await client.call<{ id?: string; error?: string }>('api_keys.create', {
        name: tempName,
        key_value: key,
        base_url: MODEL_BASE_URLS[model] || 'https://api.deepseek.com/v1',
        model,
        is_provider: true,
        enabled: false,
      })
      if (!created?.id) {
        if (mountedRef.current) {
          // A-4：api_keys.create 是 R4 方法，后端返回 {pending:true,...} 表示
          // 已入审批队列而非失败——旧逻辑落到这里会误报"创建失败"，诱导
          // 用户重复提交并烧配额。识别 pending 并如实告知。
          if ((created as { pending?: boolean })?.pending) {
            setTestStatus('fail')
            setTestReason(t('ux.apiKeyPendingApproval'))
          } else {
            setTestStatus('fail')
            setTestReason(created?.error || 'api_keys.create failed')
          }
        }
        return
      }
      createdId = created.id
      const result = await client.call<{ success: boolean; error?: string; message?: string }>(
        'api_keys.test', { key_id: createdId },
      )
      if (!mountedRef.current) return
      if (result?.success) {
        setTestStatus('ok')
      } else {
        setTestStatus('fail')
        setTestReason(result?.error || result?.message || 'unknown')
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return
      setTestStatus('fail')
      setTestReason(e instanceof Error ? e.message : 'unknown')
    } finally {
      if (createdId) {
        try { await getWsClient().call('api_keys.delete', { key_id: createdId }) } catch { /* ignore */ }
      }
    }
  }

  const finish = async () => {
    if (apiKeyInvalid()) {
      setKeyFormatError(true)
      setError(t('ux.invalidApiKeyFormat'))
      setStep('ai')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const client = getWsClient()
      const writes: Array<{ key: string; value: string }> = [
        { key: 'llm_api_key', value: apiKey.trim() },
        { key: 'llm_model', value: model },
        { key: 'device_id', value: deviceId.trim() || 'desktop-main' },
        { key: 'sync_token', value: syncToken.trim() },
        { key: 'desknotes_bridge', value: bridge ? '1' : '0' },
      ]
      for (const w of writes) {
        const ok = await client.call<boolean>('settings.set', { key: w.key, value: w.value })
        if (ok === false) throw new Error(`保存 ${w.key} 失败`)
      }
      try { localStorage.setItem(ONBOARD_KEY, '1') } catch { /* ignore */ }
      setStep('done')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '保存失败，请检查后端连接')
    } finally {
      setSaving(false)
    }
  }

  const close = () => {
    try { localStorage.setItem(ONBOARD_KEY, '1') } catch { /* ignore */ }
    setOpen(false)
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[99998] flex items-center justify-center"
      style={{ background: 'rgba(8,8,12,0.78)', backdropFilter: 'blur(6px)' }}
    >
      <div
        className="w-[420px] max-w-[92vw] rounded-2xl border border-white/10 p-6 shadow-2xl"
        style={{ background: 'var(--panel-bg)', color: 'var(--text-primary)' }}
      >
        {/* Step indicator */}
        <div className="flex items-center gap-2 mb-4">
          {(['welcome', 'ai', 'sync', 'done'] as Step[]).map((s) => (
            <div
              key={s}
              className="h-1.5 flex-1 rounded-full"
              style={{ background: step === s || (step === 'done') ? 'var(--brand)' : 'rgba(255,255,255,0.12)' }}
            />
          ))}
        </div>

        {step === 'welcome' && (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <Icon icon="fa-solid fa-brain text-2xl" />
              <h2 className="text-lg font-semibold">欢迎使用 思忆集</h2>
            </div>
            <p className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
              几步完成基础配置，即可开始使用 AI 助手。你也可以稍后在「设置」中随时修改。
            </p>
            <ul className="text-xs space-y-1.5" style={{ color: 'var(--text-secondary)' }}>
              <li>· 配置大模型 API Key（不填则使用本地模型或离线能力）</li>
              <li>· 设置设备标识与云端同步（可选）</li>
            </ul>
            <button
              onClick={() => setStep('ai')}
              className="mt-2 px-4 py-2 rounded-lg text-sm font-medium transition-all"
              style={{ background: 'var(--brand)', color: '#fff' }}
            >
              开始配置
            </button>
          </div>
        )}

        {step === 'ai' && (
          <div className="flex flex-col gap-4">
            <h2 className="text-base font-semibold">大模型配置</h2>
            <div className="flex flex-col gap-1.5">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>API Key（DeepSeek / Moonshot 等）</span>
              <div className="flex items-center gap-2">
                <div className="relative flex-1">
                  <input
                    value={apiKey}
                    onChange={(e) => { setApiKey(e.target.value); setKeyFormatError(false); setTestStatus('idle') }}
                    placeholder="sk-... （可留空）"
                    type={showKey ? 'text' : 'password'}
                    className="w-full rounded-lg px-3 py-2 pr-9 text-sm outline-none border border-white/10"
                    style={{ background: 'var(--input-bg)', color: 'var(--text-primary)' }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowKey((v) => !v)}
                    aria-label={showKey ? '隐藏 API Key' : '显示 API Key'}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-sm"
                    style={{ color: 'var(--text-secondary)', background: 'transparent', border: 'none', cursor: 'pointer' }}
                  >
                    <Icon icon={showKey ? 'fa-solid fa-eye-slash' : 'fa-solid fa-eye'} />
                  </button>
                </div>
                <button
                  type="button"
                  onClick={testConnection}
                  disabled={testStatus === 'testing' || !apiKey.trim()}
                  className="px-3 py-2 rounded-lg text-xs whitespace-nowrap disabled:opacity-50"
                  style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--text-primary)' }}
                >
                  {testStatus === 'testing'
                    ? <span className="flex items-center gap-1"><Icon icon="fa-solid fa-spinner fa-spin" />{t('ux.testConnection')}</span>
                    : t('ux.testConnection')}
                </button>
              </div>
              {keyFormatError && (
                <div className="text-xs" style={{ color: 'var(--danger)' }}>{t('ux.invalidApiKeyFormat')}</div>
              )}
              {testStatus === 'ok' && (
                <div className="text-xs" style={{ color: 'var(--success)' }}>{t('ux.connectionOk')}</div>
              )}
              {testStatus === 'fail' && (
                <div className="text-xs" style={{ color: 'var(--danger)' }}>{t('ux.connectionFail', { reason: testReason })}</div>
              )}
            </div>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>默认模型</span>
              <select
                value={model}
                onChange={(e) => { setModel(e.target.value); setTestStatus('idle') }}
                className="rounded-lg px-3 py-2 text-sm outline-none border border-white/10"
                style={{ background: 'var(--input-bg)', color: 'var(--text-primary)' }}
              >
                {MODEL_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>设备 ID</span>
              <input
                value={deviceId}
                onChange={(e) => setDeviceId(e.target.value)}
                className="rounded-lg px-3 py-2 text-sm outline-none border border-white/10"
                style={{ background: 'var(--input-bg)', color: 'var(--text-primary)' }}
              />
            </label>
            <div className="flex justify-between mt-1">
              <button
                onClick={() => setStep('welcome')}
                className="px-3 py-2 rounded-lg text-sm"
                style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--text-primary)' }}
              >
                上一步
              </button>
              <button
                onClick={goNextFromAi}
                className="px-4 py-2 rounded-lg text-sm font-medium"
                style={{ background: 'var(--brand)', color: '#fff' }}
              >
                下一步
              </button>
            </div>
          </div>
        )}

        {step === 'sync' && (
          <div className="flex flex-col gap-4">
            <h2 className="text-base font-semibold">同步与桥接（可选）</h2>
            <div className="flex flex-col gap-1.5">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>云端同步 Token</span>
              <div className="relative">
                <input
                  value={syncToken}
                  onChange={(e) => setSyncToken(e.target.value)}
                  placeholder="SIYI_SYNC_TOKEN（可留空）"
                  type={showToken ? 'text' : 'password'}
                  className="w-full rounded-lg px-3 py-2 pr-9 text-sm outline-none border border-white/10"
                  style={{ background: 'var(--input-bg)', color: 'var(--text-primary)' }}
                />
                <button
                  type="button"
                  onClick={() => setShowToken((v) => !v)}
                  aria-label={showToken ? '隐藏同步 Token' : '显示同步 Token'}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-sm"
                  style={{ color: 'var(--text-secondary)', background: 'transparent', border: 'none', cursor: 'pointer' }}
                >
                  <Icon icon={showToken ? 'fa-solid fa-eye-slash' : 'fa-solid fa-eye'} />
                </button>
              </div>
            </div>
            <label className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-primary)' }}>
              <input
                type="checkbox"
                checked={bridge}
                onChange={(e) => setBridge(e.target.checked)}
              />
              启用 desk-notes 桥接（与桌面端通讯）
            </label>
            {error && (
              <div className="text-xs" style={{ color: 'var(--danger)' }}>{error}</div>
            )}
            <div className="flex justify-between mt-1">
              <button
                onClick={() => setStep('ai')}
                className="px-3 py-2 rounded-lg text-sm"
                style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--text-primary)' }}
              >
                上一步
              </button>
              <button
                onClick={finish}
                disabled={saving}
                className="px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
                style={{ background: 'var(--brand)', color: '#fff' }}
              >
                {saving ? '保存中…' : '完成'}
              </button>
            </div>
          </div>
        )}

        {step === 'done' && (
          <div className="flex flex-col items-center gap-3 py-2">
            <Icon icon="fa-solid fa-circle-check text-3xl text-green-400" />
            <h2 className="text-base font-semibold">配置完成</h2>
            <div className="text-xs" style={{ color: 'var(--success)' }}>{t('ux.settingsSaved')}</div>
            <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>可以开始使用了。</p>
            <button
              onClick={close}
              className="mt-2 px-4 py-2 rounded-lg text-sm font-medium"
              style={{ background: 'var(--brand)', color: '#fff' }}
            >
              进入思忆集
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default SetupWizard
