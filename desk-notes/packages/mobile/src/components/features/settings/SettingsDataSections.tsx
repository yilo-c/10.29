import React from 'react'
import { SectionTitle, SettingsCard, SettingsRow } from './SettingsView'

const linkBtnStyle: React.CSSProperties = {
  background: 'none', border: 'none', color: 'var(--gold-primary)',
  fontSize: 'var(--text-small)', cursor: 'pointer', padding: '4px 0',
}

export const SettingsDataSections: React.FC = () => {
  return (
    <>
      {/* 数据管理 */}
      <div style={{ marginTop: 24 }}>
        <SectionTitle text="数据管理" />
        <SettingsCard>
          <SettingsRow label="导出数据">
            <button style={linkBtnStyle}>
              导出为 JSON
            </button>
          </SettingsRow>
          <SettingsRow label="清除数据">
            <button
              onClick={() => {
                if (window.confirm('确定清除所有数据？此操作不可撤销。')) {
                  localStorage.clear()
                  window.location.reload()
                }
              }}
              style={{ ...linkBtnStyle, color: 'var(--danger)' }}
            >
              清除所有本地数据
            </button>
          </SettingsRow>
        </SettingsCard>
      </div>

      {/* 关于 */}
      <div style={{ marginTop: 24, marginBottom: 40 }}>
        <SectionTitle text="关于" />
        <SettingsCard>
          <div style={{ padding: 'var(--space-4)', fontSize: 'var(--text-small)', color: 'var(--text-tertiary)' }}>
            思忆集 · 移动端
            <div style={{ marginTop: 4 }}>版本 2.0.0</div>
            <div style={{ marginTop: 8, fontSize: 'var(--text-tiny)', color: 'var(--text-disabled)' }}>
              辅助决策工具 · 传统历法参考
            </div>
          </div>
        </SettingsCard>
      </div>
    </>
  )
}

export default SettingsDataSections
