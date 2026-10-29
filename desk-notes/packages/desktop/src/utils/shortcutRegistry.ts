/**
 * B2（前后端对齐规划）：统一快捷键注册中心。
 *
 * 背景：快捷键散落 App/MainWindow/NoteEditor 等多处 keydown 监听，
 * 组合冲突不可发现，ShortcutsModal 展示与实际处理器不一致。
 * 本模块提供单一事实源：注册（冲突检测）+ 全局单监听分发 + 可发现列表。
 *
 * 用法：
 *   useEffect(() => {
 *     const off1 = registerShortcut({ id: 'nav.chat', combo: 'ctrl+1', label: '聊天', group: '导航', handler: ... })
 *     const offListener = installGlobalShortcutListener()
 *     return () => { off1(); offListener() }
 *   }, [])
 */

export interface ShortcutDef {
  /** 唯一 id（如 nav.chat / global.undo） */
  id: string
  /** 组合键，标准化格式：修饰键+键，如 'ctrl+shift+l'、'alt+t'、'ctrl+1' */
  combo: string
  /** 展示名称（注册时已本地化） */
  label: string
  /** 分组（ShortcutsModal 分组展示） */
  group: string
  handler: (e: KeyboardEvent) => void
}

const registry = new Map<string, ShortcutDef>()

const MODIFIER_ORDER = ['ctrl', 'shift', 'alt', 'meta']

/** 标准化组合键：小写、修饰键按 ctrl+shift+alt+meta 排序、键在末尾。 */
export function normalizeCombo(combo: string): string {
  const parts = combo.toLowerCase().split('+').map((p) => p.trim()).filter(Boolean)
  const modifiers = MODIFIER_ORDER.filter((m) => parts.includes(m))
  const keys = parts.filter((p) => !MODIFIER_ORDER.includes(p))
  return [...modifiers, ...keys].join('+')
}

/** 判断键盘事件是否匹配组合键（ctrl 兼容 mac meta）。 */
export function matchesCombo(e: KeyboardEvent, combo: string): boolean {
  const normalized = normalizeCombo(combo)
  const parts = normalized.split('+')
  const key = parts[parts.length - 1]
  const wantCtrl = parts.includes('ctrl') || parts.includes('meta')
  const wantShift = parts.includes('shift')
  const wantAlt = parts.includes('alt')

  if ((e.ctrlKey || e.metaKey) !== wantCtrl) return false
  if (e.shiftKey !== wantShift) return false
  if (e.altKey !== wantAlt) return false
  return e.key.toLowerCase() === key
}

/**
 * 注册快捷键。返回注销函数。
 * 冲突检测：同组合键已被其他 id 注册时告警并替换（后注册者生效）。
 */
export function registerShortcut(def: ShortcutDef): () => void {
  const normalized: ShortcutDef = { ...def, combo: normalizeCombo(def.combo) }
  for (const existing of registry.values()) {
    if (existing.combo === normalized.combo && existing.id !== normalized.id) {
       
      console.warn(
        `[shortcutRegistry] combo conflict: '${normalized.combo}' already registered by '${existing.id}', replaced by '${normalized.id}'`,
      )
      registry.delete(existing.id)
    }
  }
  registry.set(normalized.id, normalized)
  return () => unregisterShortcut(normalized.id)
}

export function unregisterShortcut(id: string): void {
  registry.delete(id)
}

/** 按分组返回全部快捷键（展示用，组内按 combo 排序）。 */
export function listShortcuts(): ShortcutDef[] {
  return [...registry.values()].sort((a, b) =>
    a.group === b.group ? a.combo.localeCompare(b.combo) : a.group.localeCompare(b.group),
  )
}

const MODIFIER_DISPLAY: Record<string, string> = { ctrl: 'Ctrl', shift: 'Shift', alt: 'Alt', meta: 'Meta' }

/** 展示格式：'ctrl+shift+l' → 'Ctrl+Shift+L'。 */
export function formatComboDisplay(combo: string): string {
  return normalizeCombo(combo)
    .split('+')
    .map((p) => MODIFIER_DISPLAY[p] ?? p.toUpperCase())
    .join('+')
}

/** 测试辅助：清空注册表。 */
export function clearShortcuts(): void {
  registry.clear()
  listenerRefCount = 0
  if (globalListener) {
    document.removeEventListener('keydown', globalListener)
    globalListener = null
  }
}

// ── 全局监听器（引用计数，多组件共享单监听） ──────────────────

let globalListener: ((e: KeyboardEvent) => void) | null = null
let listenerRefCount = 0

function dispatch(e: KeyboardEvent): void {
  for (const def of registry.values()) {
    if (matchesCombo(e, def.combo)) {
      e.preventDefault()
      try {
        def.handler(e)
      } catch (err) {
         
        console.error(`[shortcutRegistry] handler '${def.id}' failed:`, err)
      }
      return // 首个匹配生效，避免组合键重叠双触发
    }
  }
}

/**
 * 安装全局 keydown 监听（引用计数）。返回释放函数；
 * 引用归零时移除监听。
 */
export function installGlobalShortcutListener(): () => void {
  if (!globalListener) {
    globalListener = dispatch
    document.addEventListener('keydown', globalListener)
  }
  listenerRefCount += 1
  return () => {
    listenerRefCount -= 1
    if (listenerRefCount <= 0 && globalListener) {
      document.removeEventListener('keydown', globalListener)
      globalListener = null
      listenerRefCount = 0
    }
  }
}
