import { useState } from 'react'
import { useStore } from '@desk-notes/shared'
import { SettingsDataSections } from './SettingsDataSections'

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

const REFERENCE_INTENSITIES = [
  { value: 'hidden', label: '隐藏', desc: '不显示任何命理参考' },
  { value: 'simple', label: '精简', desc: '仅黄历宜忌' },
  { value: 'full', label: '完整', desc: '全部参考信息' },
]

function dateStrToTs(s: string): number | null {
  if (!s) return null
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d.getTime()
}

function tsToDateStr(ts: number | null): string {
  if (!ts) return ''
  const d = new Date(ts)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function SectionTitle({ text }: { text: string }) {
  return (
    <div style={{
      fontSize: 'var(--text-small)', fontWeight: 600,
      color: 'var(--text-tertiary)', marginBottom: 12,
      letterSpacing: 0.5, marginTop: 4,
    }}>
      {text}
    </div>
  )
}

export function SettingsCard({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)',
      border: '1px solid var(--border-card)', overflow: 'hidden',
    }}>
      {children}
    </div>
  )
}

export function SettingsRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
      <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)', marginBottom: 6 }}>
        {label}
      </div>
      {children}
    </div>
  )
}

export default function SettingsView() {
  const userProfile = useStore(s => s.userProfile)
  const setUserProfile = useStore(s => s.setUserProfile)
  const aiEnabledStore = useStore(s => s.aiEnabled)
  const setAIEnabled = useStore(s => s.setAIEnabled)
  const theme = useStore(s => s.theme)
  const setTheme = useStore(s => s.setTheme)

  // Profile state
  const [birthDate, setBirthDate] = useState(tsToDateStr(userProfile?.birthDate ?? null))
  const [birthHour, setBirthHour] = useState<number | null>(userProfile?.birthHour ?? null)
  const [gender, setGender] = useState<'male' | 'female' | null>(userProfile?.gender ?? null)
  const [refIntensity, setRefIntensity] = useState<'hidden' | 'simple' | 'full'>('full')
  const [showCompleted, setShowCompleted] = useState(true)
  const [aiEnabled, setAiEnabled] = useState(aiEnabledStore ?? false)
  const [aiUrl, setAiUrl] = useState(localStorage?.getItem('siyi_ai_url') ?? '')
  const [aiModel, setAiModel] = useState(localStorage?.getItem('siyi_ai_model') ?? '')
  const [aiUrlError, setAiUrlError] = useState('')
  const [aiSaveMsg, setAiSaveMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [profileMsg, setProfileMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const initialDateStr = tsToDateStr(userProfile?.birthDate ?? null)
  const hasUnsaved = birthDate !== initialDateStr ||
    birthHour !== (userProfile?.birthHour ?? null) ||
    gender !== (userProfile?.gender ?? null)

  const handleSaveProfile = () => {
    // 非法日期不再静默转为 null，给出可见提示并阻止保存
    if (birthDate && dateStrToTs(birthDate) === null) {
      setProfileMsg({ text: '日期格式无效，请使用 年-月-日', ok: false })
      return
    }
    setUserProfile({
      birthDate: dateStrToTs(birthDate),
      birthHour,
      gender,
    })
    setProfileMsg({ text: '已保存', ok: true })
    setTimeout(() => setProfileMsg(null), 2000)
  }

  const handleSaveAi = () => {
    const url = aiUrl.trim()
    if (url && !/^https?:\/\//i.test(url)) {
      setAiUrlError('地址需以 http:// 或 https:// 开头')
      return
    }
    setAiUrlError('')
    setAIEnabled(aiEnabled)
    try {
      localStorage?.setItem('siyi_ai_url', url)
      localStorage?.setItem('siyi_ai_model', aiModel)
      setAiSaveMsg({ text: '已保存', ok: true })
      setTimeout(() => setAiSaveMsg(null), 2000)
    } catch {
      setAiSaveMsg({ text: '保存失败，存储空间可能已满', ok: false })
      setTimeout(() => setAiSaveMsg(null), 4000)
    }
  }

  // Toggle switch component
  const Toggle = ({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) => (
    <button
      onClick={() => onChange(!checked)}
      style={{
        width: 44,
        height: 26,
        borderRadius: 13,
        border: 'none',
        background: checked ? 'var(--gold-primary)' : 'var(--text-disabled)',
        cursor: 'pointer',
        position: 'relative',
        transition: 'background var(--duration-fast) var(--ease-out)',
        padding: 0,
        flexShrink: 0,
      }}
    >
      <span style={{
        display: 'block',
        width: 20,
        height: 20,
        borderRadius: '50%',
        background: '#fff',
        position: 'absolute',
        top: 3,
        left: checked ? 21 : 3,
        transition: 'left var(--duration-fast) var(--ease-out)',
      }} />
    </button>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: 'var(--space-4) var(--space-4) 0', flexShrink: 0 }}>
        <div style={{ fontSize: 'var(--text-h1)', fontWeight: 600 }}>设置</div>
      </div>

      <div style={{
        flex: 1, overflowY: 'auto', padding: 'var(--space-4)',
        WebkitOverflowScrolling: 'touch',
      }}>
        {/* 个人信息 */}
        <SectionTitle text="个人信息" />
        <SettingsCard>
          <SettingsRow label="出生日期">
            <input
              type="date"
              value={birthDate}
              onChange={e => setBirthDate(e.target.value)}
              style={{
                width: '100%', padding: '8px 0', border: 'none',
                background: 'transparent', color: 'var(--text-primary)',
                fontSize: 'var(--text-body)', outline: 'none',
              }}
            />
          </SettingsRow>
          <SettingsRow label="出生时辰">
            <select
              value={birthHour ?? ''}
              onChange={e => setBirthHour(e.target.value ? Number(e.target.value) : null)}
              style={{
                width: '100%', padding: '8px 0', border: 'none',
                background: 'transparent', color: 'var(--text-primary)',
                fontSize: 'var(--text-body)', outline: 'none', cursor: 'pointer',
              }}
            >
              <option value="">未选择</option>
              {HOURS.map(h => (
                <option key={h.value} value={h.value}>{h.label}</option>
              ))}
            </select>
          </SettingsRow>
          <SettingsRow label="性别">
            <div style={{ display: 'flex', gap: 8 }}>
              {[
                { value: 'male' as const, label: '男' },
                { value: 'female' as const, label: '女' },
              ].map(g => (
                <button
                  key={g.value}
                  onClick={() => setGender(g.value)}
                  style={{
                    flex: 1, padding: '8px 0', borderRadius: 'var(--radius-sm)',
                    border: `1px solid ${gender === g.value ? 'var(--gold-border)' : 'var(--border-card)'}`,
                    background: gender === g.value ? 'var(--gold-surface)' : 'var(--bg-tertiary)',
                    color: gender === g.value ? 'var(--gold-primary)' : 'var(--text-secondary)',
                    fontSize: 'var(--text-small)', cursor: 'pointer',
                  }}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </SettingsRow>
        </SettingsCard>
        {hasUnsaved && (
          <button onClick={handleSaveProfile}
            style={{
              marginTop: 12, padding: '10px', width: '100%',
              borderRadius: 'var(--radius-sm)', border: 'none',
              background: 'var(--gold-primary)', color: '#0a0a12',
              fontSize: 'var(--text-body)', fontWeight: 600, cursor: 'pointer',
            }}
          >
            保存个人信息
          </button>
        )}
        {profileMsg && (
          <div style={{
            marginTop: 8, fontSize: 'var(--text-small)',
            color: profileMsg.ok ? 'var(--tone-smooth)' : 'var(--danger)',
            textAlign: 'center',
          }}>
            {profileMsg.text}
          </div>
        )}

        {/* 参考信息强度 */}
        <div style={{ marginTop: 24 }}>
          <SectionTitle text="参考信息强度" />
          <div style={{
            background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-card)', padding: 'var(--space-4)',
          }}>
            <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
              {REFERENCE_INTENSITIES.map(r => (
                <button
                  key={r.value}
                  onClick={() => setRefIntensity(r.value as typeof refIntensity)}
                  style={{
                    flex: 1, padding: '8px 4px', borderRadius: 'var(--radius-sm)',
                    border: `1px solid ${refIntensity === r.value ? 'var(--gold-border)' : 'var(--border-card)'}`,
                    background: refIntensity === r.value ? 'var(--gold-surface)' : 'var(--bg-tertiary)',
                    color: refIntensity === r.value ? 'var(--gold-primary)' : 'var(--text-secondary)',
                    fontSize: 'var(--text-tiny)', cursor: 'pointer',
                  }}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <div style={{ fontSize: 'var(--text-tiny)', color: 'var(--text-tertiary)' }}>
              {REFERENCE_INTENSITIES.find(r => r.value === refIntensity)?.desc}
            </div>
          </div>
        </div>

        {/* 主题 */}
        <div style={{ marginTop: 24 }}>
          <SectionTitle text="主题" />
          <div style={{
            background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-card)', padding: 'var(--space-4)',
          }}>
            <div style={{ display: 'flex', gap: 8 }}>
              {[
                { value: 'dark' as const, label: '深色', desc: '默认暗色质感' },
                { value: 'black' as const, label: '纯黑', desc: 'OLED 纯黑背景' },
                { value: 'light' as const, label: '纯白', desc: '明亮主题' },
              ].map(t => (
                <button
                  key={t.value}
                  onClick={() => setTheme(t.value)}
                  style={{
                    flex: 1, padding: '8px 4px', borderRadius: 'var(--radius-sm)',
                    border: `1px solid ${theme === t.value ? 'var(--gold-border)' : 'var(--border-card)'}`,
                    background: theme === t.value ? 'var(--gold-surface)' : 'var(--bg-tertiary)',
                    color: theme === t.value ? 'var(--gold-primary)' : 'var(--text-secondary)',
                    fontSize: 'var(--text-tiny)', cursor: 'pointer',
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div style={{ fontSize: 'var(--text-tiny)', color: 'var(--text-tertiary)', marginTop: 12 }}>
              {theme === 'dark' ? '默认暗色背景，适合大多数环境' :
               theme === 'black' ? '纯黑背景，OLED 屏幕更省电，熄屏状态下内容与屏幕边框自然融为一体' :
               '白色背景，适合明亮环境'}
            </div>
          </div>
        </div>

        {/* 显示偏好 */}
        <div style={{ marginTop: 24 }}>
          <SectionTitle text="显示偏好" />
          <SettingsCard>
            <SettingsRow label="显示已完成待办">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 'var(--text-small)', color: 'var(--text-secondary)' }}>
                  在待办列表中保留已完成的待办
                </span>
                <Toggle checked={showCompleted} onChange={setShowCompleted} />
              </div>
            </SettingsRow>
          </SettingsCard>
        </div>

        {/* AI 辅助 */}
        <div style={{ marginTop: 24 }}>
          <SectionTitle text="AI 辅助" />
          <SettingsCard>
            <SettingsRow label="启用">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 'var(--text-small)', color: 'var(--text-secondary)' }}>
                  开启 AI 辅助建议
                </span>
                <Toggle checked={aiEnabled} onChange={setAiEnabled} />
              </div>
            </SettingsRow>
            {aiEnabled && (
              <>
                <SettingsRow label="API 地址">
                  <input
                    value={aiUrl}
                    onChange={e => { setAiUrl(e.target.value); if (aiUrlError) setAiUrlError('') }}
                    placeholder="http://localhost:11434"
                    style={{
                      width: '100%', padding: '8px 0', border: 'none',
                      background: 'transparent', color: 'var(--text-primary)',
                      fontSize: 'var(--text-body)', outline: 'none',
                    }}
                  />
                  {aiUrlError && (
                    <div style={{ marginTop: 6, fontSize: 'var(--text-tiny)', color: 'var(--danger)' }}>
                      {aiUrlError}
                    </div>
                  )}
                </SettingsRow>
                <SettingsRow label="模型名称">
                  <input
                    value={aiModel}
                    onChange={e => setAiModel(e.target.value)}
                    placeholder="qwen2.5-7b:latest"
                    style={{
                      width: '100%', padding: '8px 0', border: 'none',
                      background: 'transparent', color: 'var(--text-primary)',
                      fontSize: 'var(--text-body)', outline: 'none',
                    }}
                  />
                </SettingsRow>
              </>
            )}
          </SettingsCard>
          {aiEnabled && (
            <button onClick={handleSaveAi}
              style={{
                marginTop: 12, padding: '10px', width: '100%',
                borderRadius: 'var(--radius-sm)', border: 'none',
                background: 'var(--gold-primary)', color: '#0a0a12',
                fontSize: 'var(--text-body)', fontWeight: 600, cursor: 'pointer',
              }}
            >
              保存 AI 配置
            </button>
          )}
          {aiSaveMsg && (
            <div style={{
              marginTop: 8, fontSize: 'var(--text-small)',
              color: aiSaveMsg.ok ? 'var(--tone-smooth)' : 'var(--danger)',
              textAlign: 'center',
            }}>
              {aiSaveMsg.text}
            </div>
          )}
        </div>

        {/* 数据管理 + 关于 */}
        <SettingsDataSections />
      </div>
    </div>
  )
}
