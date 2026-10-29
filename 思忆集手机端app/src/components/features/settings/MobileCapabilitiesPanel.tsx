import { useState } from 'react'
import SiyiNative, {
  type AccessibilityStatus,
  type BatteryStatus,
  type InstalledAppsResult,
  type MemoryInfo,
  type NetworkStatus,
  type RedmiNotesResult,
  type RootStatus,
  type ScreenContentResult,
  type StorageInfo,
} from '../../../lib/native/siyiNative'
import { Button } from '../../ui'
import { SectionTitle, SettingsCard, SettingsRow } from './SettingsShared'

type ProbeState = 'idle' | 'loading' | 'done' | 'error'

interface ProbeResult {
  state: ProbeState
  message: string
}

function formatBytes(bytes: number | undefined): string {
  if (!bytes || bytes <= 0) return '未知'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

export default function MobileCapabilitiesPanel() {
  const [accessibility, setAccessibility] = useState<ProbeResult>({ state: 'idle', message: '未检测' })
  const [phone, setPhone] = useState<ProbeResult>({ state: 'idle', message: '未检测' })
  const [apps, setApps] = useState<ProbeResult>({ state: 'idle', message: '未检测' })
  const [redmiNotes, setRedmiNotes] = useState<ProbeResult>({ state: 'idle', message: '未检测' })

  const testAccessibility = async () => {
    setAccessibility({ state: 'loading', message: '检测中…' })
    try {
      const status: AccessibilityStatus = await SiyiNative.isAccessibilityRunning()
      if (!status.running) {
        setAccessibility({ state: 'error', message: '无障碍未开启，点击下方按钮去系统设置开启' })
        return
      }
      const screen: ScreenContentResult = await SiyiNative.readScreen()
      setAccessibility({
        state: 'done',
        message: screen.text ? `已开启，读到 ${screen.text.length} 字屏幕内容` : '已开启，但当前屏幕无可读文本',
      })
    } catch (e) {
      setAccessibility({ state: 'error', message: e instanceof Error ? e.message : '检测失败' })
    }
  }

  const openAccessibilitySettings = async () => {
    await SiyiNative.openAccessibilitySettings().catch(e => console.warn('openAccessibilitySettings failed', e))
  }

  const testPhoneStatus = async () => {
    setPhone({ state: 'loading', message: '检测中…' })
    try {
      const [battery, storage, memory, network]: [BatteryStatus, StorageInfo, MemoryInfo, NetworkStatus] = await Promise.all([
        SiyiNative.getBatteryStatus(),
        SiyiNative.getStorageInfo(),
        SiyiNative.getMemoryInfo(),
        SiyiNative.getNetworkStatus(),
      ])
      const b = battery.result
      const s = storage.result
      const m = memory.result
      const n = network.result
      setPhone({
        state: 'done',
        message: `电量 ${b?.level ?? '?'}% · 可用存储 ${formatBytes(s?.availableBytes)} · 可用内存 ${formatBytes(m?.availMem)} · 网络 ${n?.transport ?? 'unknown'}`,
      })
    } catch (e) {
      setPhone({ state: 'error', message: e instanceof Error ? e.message : '检测失败' })
    }
  }

  const testApps = async () => {
    setApps({ state: 'loading', message: '读取中…' })
    try {
      const result: InstalledAppsResult = await SiyiNative.getInstalledApps({ limit: 50 })
      if (!result.ok || !result.result) {
        setApps({ state: 'error', message: result.error || '读取失败' })
        return
      }
      const names = result.result.apps.slice(0, 5).map(a => a.name).join('、')
      setApps({ state: 'done', message: `读取到 ${result.result.total} 个应用；示例：${names}` })
    } catch (e) {
      setApps({ state: 'error', message: e instanceof Error ? e.message : '读取失败' })
    }
  }

  const testRedmiNotes = async () => {
    setRedmiNotes({ state: 'loading', message: '读取中…' })
    try {
      const root: RootStatus = await SiyiNative.isRootAvailable()
      if (!root.available) {
        setRedmiNotes({ state: 'error', message: '设备未 Root，无法直接读取 MIUI 系统便签数据库' })
        return
      }
      const result: RedmiNotesResult = await SiyiNative.readRedmiNotes()
      if (!result.rootAvailable || !result.dbFound) {
        setRedmiNotes({ state: 'error', message: result.error || '未找到 MIUI 便签数据库' })
        return
      }
      setRedmiNotes({ state: 'done', message: `读取到 ${result.noteCount} 条 MIUI 便签` })
    } catch (e) {
      setRedmiNotes({ state: 'error', message: e instanceof Error ? e.message : '读取失败' })
    }
  }

  const colorFor = (state: ProbeState) => {
    if (state === 'done') return '#34C759'
    if (state === 'error') return '#FF3B30'
    if (state === 'loading') return '#0A84FF'
    return 'var(--text-tertiary)'
  }

  const resultText = (result: ProbeResult) => (
    <div style={{ fontSize: 'var(--text-caption-2)', color: colorFor(result.state), lineHeight: 1.5, marginTop: 6 }}>
      {result.message}
    </div>
  )

  return (
    <div style={{ marginTop: 28 }}>
      <SectionTitle text="手机管家能力" />
      <SettingsCard>
        <SettingsRow label="无障碍控制">
          <Button onClick={testAccessibility}>检测</Button>
        </SettingsRow>
        <div style={{ padding: '0 16px 12px' }}>
          {resultText(accessibility)}
          {accessibility.state === 'error' && (
            <button onClick={openAccessibilitySettings} style={{ marginTop: 8, padding: '6px 10px', borderRadius: 8, border: 'none', background: 'var(--accent)', color: '#fff', fontSize: 'var(--text-caption-1)' }}>
              打开无障碍设置
            </button>
          )}
        </div>
        <SettingsRow label="系统状态">
          <Button onClick={testPhoneStatus}>检测</Button>
        </SettingsRow>
        <div style={{ padding: '0 16px 12px' }}>{resultText(phone)}</div>
        <SettingsRow label="应用列表">
          <Button onClick={testApps}>读取</Button>
        </SettingsRow>
        <div style={{ padding: '0 16px 12px' }}>{resultText(apps)}</div>
        <SettingsRow label="MIUI 便签" last>
          <Button onClick={testRedmiNotes}>读取</Button>
        </SettingsRow>
        <div style={{ padding: '0 16px 12px' }}>{resultText(redmiNotes)}</div>
      </SettingsCard>
      <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', marginTop: 8, lineHeight: 1.5 }}>
        说明：无障碍控制仅在你明确发起任务时读取屏幕或执行点击；MIUI 便签读取需要 Redmi 已 Root。
      </div>
    </div>
  )
}
