/* ── AI 配置：云端 Key + 后端 Provider 管理 ── */

import { useState, useEffect, useCallback } from 'react'
import { tokenManager } from '../../../lib/tokenManager'
import { inputStyle, miniBtnStyle, switchStyle, switchThumbStyle } from '../../../lib/styles'
import { SectionTitle, SettingsCard, SettingsRow } from './SettingsShared'
import { swallowError } from '../../../lib/swallowError'

interface ProviderInfo {
  name: string
  api_base: string
  model: string
  enabled: boolean
  is_local: boolean
  description?: string
}

export default function SettingsAIConfig() {
  const [cloudKey, setCloudKey] = useState(tokenManager.cloudApiKey)
  const [cloudEnabled, setCloudEnabled] = useState(tokenManager.cloudAiEnabled)
  const [cloudBaseUrl, setCloudBaseUrl] = useState(tokenManager.cloudApiBaseUrl)
  const [preferDesktop, setPreferDesktop] = useState(tokenManager.preferDesktopAi)
  const [promptCloud, setPromptCloud] = useState(tokenManager.promptBeforeCloud)
  const [testStatus, setTestStatus] = useState('')

  // Provider list from backend
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const fetchProviders = useCallback(async () => {
    try {
      const { listProviders } = await import('../../../lib/bridges')
      const result = await listProviders()
      if (Array.isArray(result)) setProviders(result as unknown as ProviderInfo[])
    } catch (e) { /* offline */ swallowError('SettingsAIConfig:33', e) }
  }, [])

  useEffect(() => { fetchProviders() }, [fetchProviders])

  const testProviderHealth = useCallback(async (name: string) => {
    try {
      const { providerHealth } = await import('../../../lib/bridges')
      const result: any = await providerHealth(name)
      return result?.ok || result?.status === 'ok' ? '🟢' : '🔴'
    } catch { return '❓' }
  }, [])

  return (
    <div style={{ marginTop: 28 }}>
      <SectionTitle text="AI 配置" />

      {/* ── 后端 Provider 列表 ── */}
      {providers.length > 0 && (
        <SettingsCard>
          <div style={{ fontSize: 'var(--text-footnote)', fontWeight: 600, marginBottom: 8, color: 'var(--text-secondary)' }}>
            后端 AI 提供方
          </div>
          {providers.map(p => (
            <div key={p.name} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '6px 0', borderBottom: '0.5px solid var(--border-subtle)',
              fontSize: 'var(--text-caption-2)', color: 'var(--text-primary)',
            }}>
              <span style={{
                width: 8, height: 8, borderRadius: 4,
                background: p.enabled ? '#34C759' : '#8E8E93',
                flexShrink: 0,
              }} />
              <span style={{ flex: 1 }}>{p.name}</span>
              <span style={{ color: 'var(--text-tertiary)', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {p.model}
              </span>
              <HealthIndicator name={p.name} onCheck={testProviderHealth} />
            </div>
          ))}
        </SettingsCard>
      )}

      {/* ── Cloud API Key ── */}
      <SettingsCard>
        <SettingsRow label="云端 API Key">
          <input
            value={cloudKey}
            onChange={e => { setCloudKey(e.target.value); tokenManager.cloudApiKey = e.target.value || null }}
            placeholder="sk-... 或 DeepSeek API Key"
            type="password"
            style={{ ...inputStyle, fontSize: 'var(--text-caption-1)' }}
          />
        </SettingsRow>
        <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', marginTop: 4, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span>手机端直连云端（不经过桌面后端）</span>
          <button onClick={async () => {
            if (!cloudKey) { setTestStatus('请先输入 API Key'); return }
            setTestStatus('测试中...')
            try {
              const url = `${(cloudBaseUrl || 'https://api.deepseek.com').replace(/\/+$/, '')}/v1/chat/completions`
              const res = await fetch(url, {
                headers: { 'Authorization': `Bearer ${cloudKey}`, 'Content-Type': 'application/json' },
                method: 'POST',
                body: JSON.stringify({ model: 'deepseek-v4-flash', messages: [{ role: 'user', content: 'hi' }], max_tokens: 5 }),
                signal: AbortSignal.timeout(10000),
              })
              if (res.ok) {
                await res.json()
                setTestStatus('✅ 连接成功')
              } else {
                const text = await res.text().catch(() => '')
                setTestStatus(`❌ ${res.status}: ${text.slice(0, 100)}`)
              }
            } catch (err) {
              setTestStatus(`❌ 连接失败: ${err instanceof Error ? err.message : '未知错误'}`)
            }
            setTimeout(() => setTestStatus(''), 5000)
          }} style={{ ...miniBtnStyle, fontSize: 10, padding: '2px 8px', whiteSpace: 'nowrap' }}>
            测试
          </button>
          {testStatus && (
            <span style={{ fontSize: 10, color: testStatus.startsWith('✅') ? '#34C759' : testStatus.startsWith('❌') ? '#FF3B30' : 'var(--text-tertiary)' }}>
              {testStatus}
            </span>
          )}
        </div>
      </SettingsCard>

      {/* ── 路由偏好 ── */}
      <SettingsCard>
        <SettingsRow label="启用云端 AI">
          <button
            onClick={() => { const next = !cloudEnabled; setCloudEnabled(next); tokenManager.cloudAiEnabled = next }}
            role="switch" aria-checked={cloudEnabled}
            style={switchStyle(cloudEnabled)}
          >
            <div style={switchThumbStyle(cloudEnabled)} />
          </button>
        </SettingsRow>
        {cloudEnabled && (
          <>
            <SettingsRow label="API 地址">
              <input
                value={cloudBaseUrl}
                onChange={e => { setCloudBaseUrl(e.target.value); tokenManager.cloudApiBaseUrl = e.target.value || null }}
                placeholder="https://api.deepseek.com"
                style={{ ...inputStyle, fontSize: 'var(--text-caption-1)' }}
              />
            </SettingsRow>
            <SettingsRow label="优先桌面端 AI">
              <button
                onClick={() => { const next = !preferDesktop; setPreferDesktop(next); tokenManager.preferDesktopAi = next }}
                role="switch" aria-checked={preferDesktop}
                style={switchStyle(preferDesktop)}
              >
                <div style={switchThumbStyle(preferDesktop)} />
              </button>
            </SettingsRow>
            <SettingsRow label="云端调用前确认" last>
              <button
                onClick={() => { const next = !promptCloud; setPromptCloud(next); tokenManager.promptBeforeCloud = next }}
                role="switch" aria-checked={promptCloud}
                style={switchStyle(promptCloud)}
              >
                <div style={switchThumbStyle(promptCloud)} />
              </button>
            </SettingsRow>
          </>
        )}
      </SettingsCard>
    </div>
  )
}

/** 后端 Provider 健康指示器 */
function HealthIndicator({ name, onCheck }: { name: string; onCheck: (name: string) => Promise<string> }) {
  const [icon, setIcon] = useState<string | null>(null)

  return (
    <span
      onClick={async () => setIcon(await onCheck(name))}
      style={{ cursor: 'pointer', fontSize: 12, color: 'var(--text-tertiary)' }}
      title="点击测试连接"
    >
      {icon || '🔌'}
    </span>
  )
}
