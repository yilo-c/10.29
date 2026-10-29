import { useState } from 'react'
import LegalPage from './LegalPage'
import { SectionTitle, SettingsCard } from './SettingsShared'

export default function SettingsAbout() {
  const [legalPage, setLegalPage] = useState<'privacy' | 'terms' | null>(null)

  return (
    <>
      <div style={{ marginTop: 28, marginBottom: 40 }}>
        <SectionTitle text="关于" />
        <SettingsCard>
          <div style={{ padding: 16, fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)' }}>
            思忆便签 · 手机端
            <div style={{ marginTop: 4 }}>版本 1.0.0</div>
            <div style={{ marginTop: 8, fontSize: 'var(--text-caption-2)', color: 'var(--text-disabled)' }}>
              决策辅助工具 · 传统历法参考
            </div>
          </div>
        </SettingsCard>
        <div style={{ marginTop: 12, display: 'flex', gap: 12 }}>
          <button onClick={() => setLegalPage('privacy')} style={{ flex: 1, padding: '10px 0', background: 'var(--bg-secondary)', border: 'none', borderRadius: 'var(--radius-md)', color: 'var(--accent)', fontSize: 'var(--text-footnote)', cursor: 'pointer' }}>隐私政策</button>
          <button onClick={() => setLegalPage('terms')} style={{ flex: 1, padding: '10px 0', background: 'var(--bg-secondary)', border: 'none', borderRadius: 'var(--radius-md)', color: 'var(--accent)', fontSize: 'var(--text-footnote)', cursor: 'pointer' }}>用户协议</button>
        </div>
      </div>
      {legalPage && <LegalPage type={legalPage} onClose={() => setLegalPage(null)} />}
    </>
  )
}
