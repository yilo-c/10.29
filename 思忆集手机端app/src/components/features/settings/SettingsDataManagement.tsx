import { useCallback, useRef } from 'react'
import { linkBtnStyle } from '../../../lib/styles'
import { SectionTitle, SettingsCard, SettingsRow } from './SettingsShared'
import { swallowError } from '../../../lib/swallowError'

export default function SettingsDataManagement() {
  const importRef = useRef<HTMLInputElement>(null)

  const handleExport = useCallback(() => {
    try {
      const raw = localStorage.getItem('desk-notes-storage')
      if (!raw) { window.confirm('没有数据可导出'); return }
      const blob = new Blob([raw], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `siyi-backup-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) { /* ignore */ swallowError('SettingsDataManagement:21', e) }
  }, [])

  const handleImport = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const raw = ev.target?.result as string
        JSON.parse(raw)
        if (window.confirm('导入将覆盖当前所有数据，确定继续？')) {
          localStorage.setItem('desk-notes-storage', raw)
          window.location.reload()
        }
      } catch {
        window.confirm('文件格式无效，请选择有效的 JSON 备份文件')
      }
    }
    reader.readAsText(file)
    e.target.value = ''
  }, [])

  return (
    <div style={{ marginTop: 28 }}>
      <SectionTitle text="数据管理" />
      <SettingsCard>
        <SettingsRow label="导出数据">
          <button onClick={handleExport} style={linkBtnStyle}>导出为 JSON</button>
        </SettingsRow>
        <SettingsRow label="导入数据">
          <button onClick={() => importRef.current?.click()} style={linkBtnStyle}>从 JSON 恢复</button>
          <input ref={importRef} type="file" accept=".json" style={{ display: 'none' }} onChange={handleImport} />
        </SettingsRow>
        <SettingsRow label="清除数据" last>
          <button onClick={() => { if (window.confirm('确定清除所有数据？此操作不可撤销。')) { localStorage.clear(); window.location.reload() } }} style={{ ...linkBtnStyle, color: 'var(--danger)' }}>清除所有本地数据</button>
        </SettingsRow>
      </SettingsCard>
    </div>
  )
}
