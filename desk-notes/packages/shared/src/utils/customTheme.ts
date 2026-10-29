import type { CustomTheme } from '../types'

/** Apply a custom theme by overriding standard CSS variables on document root */
export function applyCustomTheme(theme: CustomTheme | null) {
  const root = document.documentElement

  if (!theme) {
    // Remove overrides so standard theme/background rules take effect
    clearOverrides(root)
    return
  }

  const { colors } = theme
  root.style.setProperty('--panel-bg', colors.panelBg)
  root.style.setProperty('--panel-bg-solid', colors.panelBgSolid)
  root.style.setProperty('--text-primary', colors.textPrimary)
  root.style.setProperty('--text-secondary', colors.textSecondary)
  root.style.setProperty('--panel-border', colors.borderColor)
  root.style.setProperty('--panel-border-accent', colors.borderColor)
  root.style.setProperty('--body-bg', colors.bodyBg)
  root.style.setProperty('--accent', colors.accent)

  // Derived colors (explicit skin fields win over derived defaults)
  root.style.setProperty('--input-bg', colors.borderColor)
  root.style.setProperty('--hover-bg', colors.hoverBg || colors.borderColor)
  root.style.setProperty('--separator', colors.borderColor)

  // Optional semantic colors, aligned with the existing global var system
  if (colors.success) root.style.setProperty('--success', colors.success)
  if (colors.warning) root.style.setProperty('--warning', colors.warning)
  if (colors.danger) root.style.setProperty('--danger', colors.danger)
  if (colors.accentWeak) root.style.setProperty('--accent-soft', colors.accentWeak)
  if (colors.overlayBg) root.style.setProperty('--overlay-bg', colors.overlayBg)
}

function clearOverrides(root: HTMLElement) {
  const vars = [
    '--panel-bg', '--panel-bg-solid', '--text-primary', '--text-secondary',
    '--panel-border', '--panel-border-accent', '--body-bg',
    '--input-bg', '--hover-bg', '--separator',
    '--success', '--warning', '--danger', '--accent-soft', '--overlay-bg',
  ]
  for (const v of vars) {
    root.style.removeProperty(v)
  }
}
