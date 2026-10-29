import React, { useState, useCallback } from 'react'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'

interface BaziSettingsProps {
  onClose: () => void
}

const BaziSettings: React.FC<BaziSettingsProps> = ({ onClose }) => {
  const userProfile = useStore(s => s.userProfile)
  const setUserProfile = useStore(s => s.setUserProfile)
  const { t } = useTranslation()

  const [birthDate, setBirthDate] = useState(() => {
    if (userProfile.birthDate) {
      const d = new Date(userProfile.birthDate)
      return d.toISOString().split('T')[0]
    }
    return ''
  })
  const [birthHour, setBirthHour] = useState<number | null>(userProfile.birthHour)
  const [gender, setGender] = useState<'male' | 'female' | null>(userProfile.gender)

  const handleSave = useCallback(() => {
    if (!birthDate) {
      setUserProfile({ birthDate: null, birthHour: null, gender: null })
    } else {
      setUserProfile({
        birthDate: new Date(birthDate).getTime(),
        birthHour,
        gender,
      })
    }
    onClose()
  }, [birthDate, birthHour, gender, setUserProfile, onClose])

  const hourOptions = [
    { value: null, label: t('guidance.hourUnknown') },
    { value: 0, label: t('guidance.hour0') },
    { value: 1, label: t('guidance.hour1') },
    { value: 2, label: t('guidance.hour2') },
    { value: 3, label: t('guidance.hour3') },
    { value: 4, label: t('guidance.hour4') },
    { value: 5, label: t('guidance.hour5') },
    { value: 6, label: t('guidance.hour6') },
    { value: 7, label: t('guidance.hour7') },
    { value: 8, label: t('guidance.hour8') },
    { value: 9, label: t('guidance.hour9') },
    { value: 10, label: t('guidance.hour10') },
    { value: 11, label: t('guidance.hour11') },
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.5)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="rounded-xl p-4 w-80 max-w-[90vw] shadow-2xl"
        style={{ background: 'var(--panel-bg)', border: '1px solid var(--panel-border)' }}
      >
        <h3 className="text-xs font-medium mb-3" style={{ color: 'var(--text-primary)' }}>
          {t('guidance.baziSettings')}
        </h3>

        {/* 出生日期 */}
        <div className="mb-3">
          <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>
            {t('guidance.birthDate')}
          </label>
          <input
            type="date"
            value={birthDate}
            onChange={e => setBirthDate(e.target.value)}
            className="w-full px-2 py-1.5 rounded-lg text-xs outline-none"
            style={{
              background: 'var(--hover-bg)',
              color: 'var(--text-primary)',
              border: '1px solid var(--panel-border)',
            }}
          />
        </div>

        {/* 出生时辰 */}
        <div className="mb-3">
          <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>
            {t('guidance.birthHour')}
          </label>
          <select
            value={birthHour ?? ''}
            onChange={e => setBirthHour(e.target.value ? Number(e.target.value) : null)}
            className="w-full px-2 py-1.5 rounded-lg text-xs outline-none"
            style={{
              background: 'var(--hover-bg)',
              color: 'var(--text-primary)',
              border: '1px solid var(--panel-border)',
            }}
          >
            {hourOptions.map(opt => (
              <option key={String(opt.value)} value={opt.value ?? ''}>{opt.label}</option>
            ))}
          </select>
        </div>

        {/* 性别 */}
        <div className="mb-4">
          <label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>
            {t('guidance.gender')}
          </label>
          <div className="flex gap-2">
            {(['male', 'female'] as const).map(g => (
              <button
                key={g}
                onClick={() => setGender(g)}
                className="flex-1 py-1.5 rounded-lg text-xs transition-all"
                style={{
                  background: gender === g ? 'var(--brand)' : 'var(--hover-bg)',
                  color: gender === g ? '#fff' : 'var(--text-secondary)',
                  border: '1px solid var(--panel-border)',
                }}
              >
                {g === 'male' ? t('guidance.genderMale') : t('guidance.genderFemale')}
              </button>
            ))}
          </div>
        </div>

{/* 按钮 */}
        <div className="flex gap-2">
          <button
            onClick={onClose}
            className="flex-1 py-1.5 rounded-lg text-xs transition-all"
            style={{ background: 'var(--hover-bg)', color: 'var(--text-secondary)' }}
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSave}
            className="flex-1 py-1.5 rounded-lg text-xs font-medium transition-all"
            style={{ background: 'var(--brand)', color: '#fff' }}
          >
            {t('guidance.saveProfile')}
          </button>
        </div>
      </div>
    </div>
  )
}

export default BaziSettings
