import { applyCustomTheme, getWsClient, type CustomTheme } from '@desk-notes/shared'

/** 后端皮肤定义（与 services/skin_service.py 的 SkinDefinition.to_dict 对齐） */
export interface SkinDefinition {
  id: string
  name: string
  version: string
  description: string
  colors: CustomTheme['colors']
}

const SKIN_COLOR_FIELDS = [
  'panelBg',
  'panelBgSolid',
  'textPrimary',
  'textSecondary',
  'accent',
  'borderColor',
  'bodyBg',
  'success',
  'warning',
  'danger',
  'accentWeak',
  'hoverBg',
  'overlayBg',
] as const

function toCustomTheme(skin: SkinDefinition): CustomTheme {
  return {
    id: skin.id,
    name: skin.name,
    createdAt: 0,
    colors: skin.colors,
  }
}

/** 拉取所有可用皮肤 */
export async function loadSkins(): Promise<SkinDefinition[]> {
  const client = getWsClient()
  const res = await client.call<{ success: boolean; skins?: SkinDefinition[]; error?: string }>(
    'skin.list',
  )
  if (!res?.success || !res.skins) throw new Error(res?.error || 'skin.list failed')
  return res.skins
}

/** 获取当前激活皮肤（未激活返回 null） */
export async function getActiveSkin(): Promise<SkinDefinition | null> {
  const client = getWsClient()
  const res = await client.call<{ success: boolean; skin?: SkinDefinition | null; error?: string }>(
    'skin.active',
  )
  if (!res?.success) throw new Error(res?.error || 'skin.active failed')
  return res.skin ?? null
}

/** 激活皮肤：调用后端持久化，并立即应用 CSS 变量覆盖 */
export async function activateSkin(skinId: string, skin?: SkinDefinition): Promise<SkinDefinition | null> {
  const client = getWsClient()
  const res = await client.call<{ success: boolean; skin?: SkinDefinition | null; error?: string }>(
    'skin.activate',
    { skin_id: skinId },
  )
  if (!res?.success) throw new Error(res?.error || 'skin.activate failed')

  const applied = res.skin ?? skin ?? null
  if (applied) {
    applyCustomTheme(toCustomTheme(applied))
  } else {
    applyCustomTheme(null)
  }
  return applied
}

/** 关闭皮肤（还原默认外观） */
export async function clearSkin(): Promise<void> {
  applyCustomTheme(null)
}

export { toCustomTheme, SKIN_COLOR_FIELDS }
