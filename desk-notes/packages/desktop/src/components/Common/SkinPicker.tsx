import React, { useCallback, useEffect, useState } from 'react'
import Icon from './Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import {
  loadSkins,
  getActiveSkin,
  activateSkin,
  clearSkin,
  type SkinDefinition,
} from '../../utils/skin'

const SkinPicker: React.FC = () => {
  const { t } = useTranslation()
  const setActiveTheme = useStore(s => s.setActiveTheme)

  const [skins, setSkins] = useState<SkinDefinition[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [list, active] = await Promise.all([loadSkins(), getActiveSkin()])
      setSkins(list)
      setActiveId(active?.id ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'skin load failed')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const handleActivate = async (skinId: string) => {
    try {
      await activateSkin(skinId)
      // 皮肤与自定义主题共用 CSS 变量覆盖，激活皮肤时清掉自定义主题避免冲突
      setActiveTheme(null)
      setActiveId(skinId)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'skin activate failed')
    }
  }

  const handleClear = () => {
    clearSkin()
    setActiveId(null)
  }

  return (
    <div className="border-t border-white/[0.06] pt-2">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-text-secondary">{t('settings.skin')}</span>
        {activeId && (
          <button onClick={handleClear}
            className="text-2xs px-2 py-0.5 rounded-lg text-text-muted hover:text-text-secondary hover:bg-white/[0.04] transition-colors">
            {t('settings.resetTheme')}
          </button>
        )}
      </div>

      {error && <p className="text-2xs text-red-400/80 mb-1">{error}</p>}

      {loading ? (
        <p className="text-2xs text-text-dim py-1">{t('common.loading')}</p>
      ) : (
        <div className="flex flex-col gap-1 max-h-[140px] overflow-y-auto">
          {skins.map(skin => (
            <div key={skin.id}
              className={`flex items-center gap-2 px-2 py-1 rounded-lg transition-colors cursor-pointer ${
                activeId === skin.id ? 'bg-white/[0.06]' : 'hover:bg-white/[0.03]'
              }`}
              onClick={() => handleActivate(skin.id)}>
              <span
                className="w-3 h-3 rounded-full border border-white/20 flex-shrink-0"
                style={{ background: `linear-gradient(135deg, ${skin.colors.accent}, ${skin.colors.panelBg})` }}
              />
              <span className="flex-1 text-xs text-text-secondary truncate">{skin.name}</span>
              {activeId === skin.id && (
                <Icon className="text-[8px] text-fluent-blue" icon="fa-solid fa-check" />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default SkinPicker
