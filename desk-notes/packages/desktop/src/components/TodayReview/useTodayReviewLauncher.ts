import { useCallback, useEffect, useRef } from 'react'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { registerShortcut } from '../../utils/shortcutRegistry'
import type { ActiveModule } from '@desk-notes/shared'

/**
 * V1-6「今日回顾」的可达入口：只注册一条快捷键（alt+r），不改侧边栏、不改导航数量。
 *
 * 由 ContentRouter 顶层调用（常驻挂载），因此视图切换时注册始终有效；
 * 再次触发回到进入前的模块，行为等同「面板开关」。
 */

export const TODAY_REVIEW_MODULE: ActiveModule = 'today-review'
export const TODAY_REVIEW_COMBO = 'alt+r'

/** 在「今日回顾」与上一模块之间切换，返回切换后的模块。 */
export function toggleTodayReview(prevModule: ActiveModule): ActiveModule {
  const current = useStore.getState().activeModule
  const next = current === TODAY_REVIEW_MODULE ? prevModule : TODAY_REVIEW_MODULE
  useStore.getState().setActiveModule(next)
  return next
}

export function useTodayReviewLauncher(): { openTodayReview: () => void } {
  const { t } = useTranslation()
  const prevModuleRef = useRef<ActiveModule>('dashboard')
  const activeModule = useStore(s => s.activeModule)

  useEffect(() => {
    if (activeModule !== TODAY_REVIEW_MODULE) prevModuleRef.current = activeModule
  }, [activeModule])

  useEffect(() => {
    const off = registerShortcut({
      id: 'nav.todayReview',
      combo: TODAY_REVIEW_COMBO,
      label: t('todayReview.title'),
      group: t('shortcuts.nav'),
      handler: () => { toggleTodayReview(prevModuleRef.current) },
    })
    return () => off()
  }, [t])

  const openTodayReview = useCallback(() => {
    useStore.getState().setActiveModule(TODAY_REVIEW_MODULE)
  }, [])

  return { openTodayReview }
}
