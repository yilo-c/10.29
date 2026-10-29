import { useState, useEffect } from 'react'
import { useStore } from '@desk-notes/shared'
import { Button } from '../../ui'
import SettingsDeviceCard from './SettingsDeviceCard'
import SyncStatusView from './SyncStatusView'
import SettingsAppearance from './SettingsAppearance'
import SettingsSecurity from './SettingsSecurity'
import SettingsAIConfig from './SettingsAIConfig'
import SettingsDataManagement from './SettingsDataManagement'
import SettingsAbout from './SettingsAbout'
import FloatingBallToggle from './FloatingBallToggle'
import { useAutoSync } from '../../../hooks/useAutoSync'
import { tokenManager } from '../../../lib/tokenManager'
import { inputStyle } from '../../../lib/styles'
import { SectionTitle, SettingsCard, SettingsRow } from './SettingsShared'
import { isCloudSyncConfigured, configureCloudSync, pushLocalData, pullRemoteData, registerDevice } from '../../../lib/cloudSync'
import { secureGet } from '../../../lib/secureStore'
import { getSyncStatus, configureSync, syncNow, type SyncStatusRemote } from '../../../lib/bridges/syncBridge'

const HOURS = [
  { value: 0, label: '子时 (23:00-00:59)' },
  { value: 1, label: '丑时 (01:00-02:59)' },
  { value: 2, label: '寅时 (03:00-04:59)' },
  { value: 3, label: '卯时 (05:00-06:59)' },
  { value: 4, label: '辰时 (07:00-08:59)' },
  { value: 5, label: '巳时 (09:00-10:59)' },
  { value: 6, label: '午时 (11:00-12:59)' },
  { value: 7, label: '未时 (13:00-14:59)' },
  { value: 8, label: '申时 (15:00-16:59)' },
  { value: 9, label: '酉时 (17:00-18:59)' },
  { value: 10, label: '戌时 (19:00-20:59)' },
  { value: 11, label: '亥时 (21:00-22:59)' },
]

function dateStrToTs(s: string): number | null {
  if (!s) return null
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d.getTime()
}

function tsToDateStr(ts: number | null): string {
  if (!ts) return ''
  const d = new Date(ts)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function SettingsView() {
  const userProfile = useStore(s => s.userProfile)
  const setUserProfile = useStore(s => s.setUserProfile)
  const { syncStatus, syncMessage, triggerSync } = useAutoSync()
  const [cloudWorkerUrl, setCloudWorkerUrl] = useState(localStorage.getItem('siyi_worker_url') || '')
  // P1-1: sync_token 与 register_secret 是凭据，从 secureStore 读取（加密存储）
  const [cloudSyncToken, setCloudSyncToken] = useState(secureGet('siyi_sync_token') || '')
  const [cloudRegisterSecret, setCloudRegisterSecret] = useState(secureGet('siyi_register_secret') || '')
  const [cloudSyncStatus, setCloudSyncStatus] = useState('')
  const [cloudRemoteStatus, setCloudRemoteStatus] = useState<SyncStatusRemote | null>(null)

  useEffect(() => {
    if (!tokenManager.isPaired) return
    getSyncStatus().then(s => s && setCloudRemoteStatus(s)).catch(() => {})
  }, [])

  const refreshCloudStatus = async () => {
    if (!tokenManager.isPaired) return
    const s = await getSyncStatus()
    if (s) setCloudRemoteStatus(s)
  }

  const [birthDate, setBirthDate] = useState(tsToDateStr(userProfile?.birthDate ?? null))
  const [birthHour, setBirthHour] = useState<number | null>(userProfile?.birthHour ?? null)
  const [gender, setGender] = useState<'male' | 'female' | null>(userProfile?.gender ?? null)

  const initialDateStr = tsToDateStr(userProfile?.birthDate ?? null)
  const hasUnsaved = birthDate !== initialDateStr ||
    birthHour !== (userProfile?.birthHour ?? null) ||
    gender !== (userProfile?.gender ?? null)

  const handleSaveProfile = () => {
    setUserProfile({ birthDate: dateStrToTs(birthDate), birthHour, gender })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '16px 20px 0', flexShrink: 0 }}>
        <div style={{ fontSize: 'var(--text-large-title)', fontWeight: 700, letterSpacing: -0.5 }}>设置</div>
      </div>
      <div style={{
        flex: 1, overflowY: 'auto', padding: '16px 20px',
        WebkitOverflowScrolling: 'touch',
      }}>
        {/* 个人信息 */}
        <SectionTitle text="个人信息" />
        <SettingsCard>
          <SettingsRow label="出生日期">
            <input type="date" value={birthDate} onChange={e => setBirthDate(e.target.value)} style={inputStyle} />
          </SettingsRow>
          <SettingsRow label="出生时辰" last={!gender}>
            <select value={birthHour ?? ''} onChange={e => setBirthHour(e.target.value ? Number(e.target.value) : null)} style={{ ...inputStyle, cursor: 'pointer' }}>
              <option value="">未选择</option>
              {HOURS.map(h => <option key={h.value} value={h.value}>{h.label}</option>)}
            </select>
          </SettingsRow>
          <SettingsRow label="性别" last>
            <div style={{ display: 'flex', gap: 8 }}>
              {([{ value: 'male' as const, label: '男' }, { value: 'female' as const, label: '女' }]).map(g => (
                <button key={g.value} onClick={() => setGender(g.value)} style={{
                  flex: 1, padding: '8px 0', borderRadius: 'var(--radius-md)',
                  border: `1.5px solid ${gender === g.value ? 'var(--accent)' : 'var(--border-default)'}`,
                  background: gender === g.value ? 'var(--accent-surface)' : 'transparent',
                  color: gender === g.value ? 'var(--accent)' : 'var(--text-secondary)',
                  fontSize: 'var(--text-footnote)', fontWeight: 500, cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}>{g.label}</button>
              ))}
            </div>
          </SettingsRow>
        </SettingsCard>
        {hasUnsaved && (
          <div style={{ marginTop: 12 }}>
            <Button variant="primary" onClick={handleSaveProfile} style={{ width: '100%' }}>保存个人信息</Button>
          </div>
        )}

        <SettingsAppearance />
        <SettingsSecurity />
        <SettingsAIConfig />
        <SettingsDataManagement />

        {/* 设备与服务 */}
        <div style={{ marginTop: 28 }}>
          <SettingsDeviceCard onPairingChange={() => {}} />
        </div>

        {/* 同步状态 */}
        <SyncStatusView />

        {/* 悬浮球 */}
        <div style={{ marginTop: 28 }}>
          <SectionTitle text="悬浮球" />
          <SettingsCard>
            <div style={{ padding: '12px 16px' }}>
              <SettingsRow label="启用悬浮球">
                <FloatingBallToggle />
              </SettingsRow>
              <div style={{
                padding: '0 16px 12px',
                fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
              }}>
                在其他 App 上层显示思忆集入口，支持拖拽吸附和情绪状态
              </div>
            </div>
          </SettingsCard>
        </div>

        {/* WiFi LAN 同步 */}
        <div style={{ marginTop: 28 }}>
          <SectionTitle text="同步" />
          <SettingsCard>
            <div style={{ padding: 16 }}>
              <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', marginBottom: 10 }}>
                同步笔记、任务、翻译、词本（需先配对桌面端）
              </div>
              <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
                <button onClick={() => triggerSync(false)} disabled={syncStatus === 'loading' || !tokenManager.isPaired} style={{
                  width: '100%', padding: '10px 0', borderRadius: 'var(--radius-md)', border: 'none',
                  background: syncStatus === 'loading' ? 'var(--bg-tertiary)' : (tokenManager.isPaired ? 'var(--accent)' : 'var(--bg-tertiary)'),
                  color: syncStatus === 'loading' ? 'var(--text-disabled)' : (tokenManager.isPaired ? '#fff' : 'var(--text-disabled)'),
                  fontSize: 'var(--text-footnote)', fontWeight: 600,
                  cursor: syncStatus === 'loading' || !tokenManager.isPaired ? 'default' : 'pointer',
                }}>
                  {syncStatus === 'loading' ? '同步中...' : '拉取数据'}
                </button>
              </div>
              {syncMessage && (
                <div style={{ fontSize: 'var(--text-caption-2)', color: syncStatus === 'success' ? 'var(--tone-smooth)' : syncStatus === 'error' ? 'var(--danger)' : 'var(--text-tertiary)' }}>
                  {syncMessage}
                </div>
              )}
            </div>
          </SettingsCard>
        </div>

        {/* 云同步（Cloudflare Worker） */}
        <div style={{ marginTop: 28 }}>
          <SectionTitle text="云同步（外网）" />
          <SettingsCard>
            <div style={{ padding: 16 }}>
              <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', marginBottom: 10 }}>
                通过 Cloudflare Worker 在外网同步笔记/待办/设置（端到端加密）
              </div>
              <input value={cloudWorkerUrl} onChange={e => setCloudWorkerUrl(e.target.value)}
                placeholder="Worker URL (如 https://siyi-sync.xxx.workers.dev)"
                style={{ ...inputStyle, marginBottom: 8 }} />
              <input value={cloudSyncToken} onChange={e => setCloudSyncToken(e.target.value)}
                placeholder="同步 Token（与桌面端相同）" type="password"
                style={{ ...inputStyle, marginBottom: 8 }} />
              <input value={cloudRegisterSecret} onChange={e => setCloudRegisterSecret(e.target.value)}
                placeholder="注册密钥 register_secret（与桌面端相同）" type="password"
                style={{ ...inputStyle, marginBottom: 8 }} />

              {cloudRemoteStatus && tokenManager.isPaired && (
                <div style={{ marginBottom: 10, padding: '8px 10px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-tertiary)', fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span>桌面端云同步</span>
                    <span style={{ color: cloudRemoteStatus.enabled ? 'var(--tone-smooth)' : 'inherit' }}>
                      {cloudRemoteStatus.enabled ? '已启用' : '未启用'}
                    </span>
                  </div>
                  {cloudRemoteStatus.last_sync_at && (
                    <div>桌面端上次同步: {new Date(cloudRemoteStatus.last_sync_at).toLocaleString('zh-CN')}</div>
                  )}
                </div>
              )}

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button onClick={async () => {
                  if (!cloudWorkerUrl || !cloudSyncToken || !cloudRegisterSecret) return
                  configureCloudSync(cloudWorkerUrl, cloudSyncToken, cloudRegisterSecret)
                  const ok = await registerDevice('手机端', cloudSyncToken)
                  // 同时把相同配置推给桌面端，让双端共用同一个 Worker
                  if (tokenManager.isPaired) {
                    configureSync({
                      worker_url: cloudWorkerUrl,
                      token: cloudSyncToken,
                      register_secret: cloudRegisterSecret,
                    }).then(() => refreshCloudStatus()).catch(() => {})
                  }
                  setCloudSyncStatus(ok ? '✅ 已配置' : '❌ 注册失败')
                  setTimeout(() => setCloudSyncStatus(''), 3000)
                }} style={{ flex: 1, padding: '8px 0', borderRadius: 'var(--radius-sm)', border: 'none', background: 'var(--accent)', color: '#fff', fontSize: 'var(--text-footnote)', fontWeight: 600, cursor: 'pointer' }}>
                  配置
                </button>
                <button onClick={async () => {
                  setCloudSyncStatus('推送中...')
                  const [ok] = await Promise.all([
                    pushLocalData(),
                    tokenManager.isPaired ? syncNow() : Promise.resolve(false),
                  ])
                  await refreshCloudStatus()
                  setCloudSyncStatus(ok ? '✅ 已推送' : '❌ 推送失败')
                  setTimeout(() => setCloudSyncStatus(''), 3000)
                }} disabled={!isCloudSyncConfigured()} style={{ flex: 1, padding: '8px 0', borderRadius: 'var(--radius-sm)', border: 'none', background: isCloudSyncConfigured() ? 'var(--accent)' : 'var(--bg-tertiary)', color: isCloudSyncConfigured() ? '#fff' : 'var(--text-disabled)', fontSize: 'var(--text-footnote)', fontWeight: 600, cursor: isCloudSyncConfigured() ? 'pointer' : 'default' }}>
                  推送
                </button>
                <button onClick={async () => {
                  setCloudSyncStatus('拉取中...')
                  const [ok] = await Promise.all([
                    pullRemoteData(),
                    tokenManager.isPaired ? syncNow() : Promise.resolve(false),
                  ])
                  await refreshCloudStatus()
                  setCloudSyncStatus(ok ? '✅ 已拉取' : '❌ 拉取失败')
                  setTimeout(() => setCloudSyncStatus(''), 3000)
                }} disabled={!isCloudSyncConfigured()} style={{ flex: 1, padding: '8px 0', borderRadius: 'var(--radius-sm)', border: 'none', background: isCloudSyncConfigured() ? 'var(--accent)' : 'var(--bg-tertiary)', color: isCloudSyncConfigured() ? '#fff' : 'var(--text-disabled)', fontSize: 'var(--text-footnote)', fontWeight: 600, cursor: isCloudSyncConfigured() ? 'pointer' : 'default' }}>
                  拉取
                </button>
                {tokenManager.isPaired && (
                  <button onClick={async () => {
                    setCloudSyncStatus('刷新中...')
                    await refreshCloudStatus()
                    setCloudSyncStatus('')
                  }} style={{ flex: 1, padding: '8px 0', borderRadius: 'var(--radius-sm)', border: '0.5px solid var(--border-default)', background: 'transparent', color: 'var(--text-secondary)', fontSize: 'var(--text-footnote)', fontWeight: 600, cursor: 'pointer' }}>
                    刷新状态
                  </button>
                )}
              </div>
              {cloudSyncStatus && (
                <div style={{ marginTop: 8, fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>{cloudSyncStatus}</div>
              )}
            </div>
          </SettingsCard>
        </div>

        <SettingsAbout />
      </div>
    </div>
  )
}
