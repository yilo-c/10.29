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

/**
 * 给「模型名」输入框一个贴合当前 API 地址的占位提示。
 * 只是提示，不是默认值——留空时由 cloudAiClient 按地址推断。
 */
function inferPlaceholder(baseUrl: string): string {
  const u = (baseUrl || '').toLowerCase()
  if (u.includes('deepseek')) return 'deepseek-chat（留空则按地址推断）'
  if (u.includes('openai')) return 'gpt-4o-mini（留空则按地址推断）'
  if (u.includes('moonshot') || u.includes('kimi')) return 'moonshot-v1-8k（留空则按地址推断）'
  return '如 deepseek-chat、gpt-4o-mini（留空则按地址推断）'
}

export default function SettingsAIConfig() {
  const [cloudKey, setCloudKey] = useState(tokenManager.cloudApiKey)
  const [cloudEnabled, setCloudEnabled] = useState(tokenManager.cloudAiEnabled)
  const [cloudBaseUrl, setCloudBaseUrl] = useState(tokenManager.cloudApiBaseUrl)
  const [cloudModel, setCloudModel] = useState(tokenManager.cloudModel)
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
              /*
               * 模型必须用用户配置的那个（留空才回落到地址推断）。
               * 此前这里硬编码 'deepseek-v4-flash'：测试用的是写死的模型，
               * 实际对话用的是另一个，于是出现「测试成功但对话失败」。
               * 两边必须用同一个来源，否则测试毫无意义。
               */
              const configured = (cloudModel || '').trim()
              const isOpenAi = (cloudBaseUrl || '').toLowerCase().includes('openai')
              const testModel = configured || (isOpenAi ? 'gpt-4o-mini' : 'deepseek-chat')
              const res = await fetch(url, {
                headers: { 'Authorization': `Bearer ${cloudKey}`, 'Content-Type': 'application/json' },
                method: 'POST',
                body: JSON.stringify({ model: testModel, messages: [{ role: 'user', content: 'hi' }], max_tokens: 5 }),
                signal: AbortSignal.timeout(15000),
              })
              if (res.ok) {
                await res.json()
                setTestStatus(`✅ 连接成功（模型 ${testModel}）`)
              } else {
                const text = await res.text().catch(() => '')
                // 把服务商返回的真实原因显示出来，而不是只给一个状态码
                setTestStatus(`❌ ${res.status}: ${text.slice(0, 160)}`)
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
            {/*
              模型名：此前完全由 API 地址推断（含 deepseek → deepseek-v4-flash），
              用户既看不到也改不了。地址对了但模型名不被服务商接受时，
              只会收到一个 400，界面上无从判断。这里让用户能显式填写。
              留空 = 继续按地址推断，保持旧行为。
            */}
            <SettingsRow label="模型名">
              <input
                value={cloudModel}
                onChange={e => { setCloudModel(e.target.value); tokenManager.cloudModel = e.target.value || null }}
                placeholder={inferPlaceholder(cloudBaseUrl)}
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
