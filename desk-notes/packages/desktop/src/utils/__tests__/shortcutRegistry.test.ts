/**
 * B2（前后端对齐规划）：shortcutRegistry 测试。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  normalizeCombo,
  matchesCombo,
  registerShortcut,
  unregisterShortcut,
  listShortcuts,
  clearShortcuts,
  installGlobalShortcutListener,
  formatComboDisplay,
} from '../shortcutRegistry'

function keyEvent(key: string, mods: { ctrl?: boolean; shift?: boolean; alt?: boolean; meta?: boolean } = {}): KeyboardEvent {
  return new KeyboardEvent('keydown', {
    key,
    ctrlKey: !!mods.ctrl,
    shiftKey: !!mods.shift,
    altKey: !!mods.alt,
    metaKey: !!mods.meta,
    cancelable: true,
  })
}

describe('normalizeCombo', () => {
  it('小写并按 ctrl+shift+alt+meta 排序修饰键', () => {
    expect(normalizeCombo('Shift+Ctrl+L')).toBe('ctrl+shift+l')
    expect(normalizeCombo('ALT+T')).toBe('alt+t')
    expect(normalizeCombo('1+Ctrl')).toBe('ctrl+1')
  })
})

describe('matchesCombo', () => {
  it('精确匹配修饰键与键', () => {
    expect(matchesCombo(keyEvent('l', { ctrl: true, shift: true }), 'ctrl+shift+l')).toBe(true)
    expect(matchesCombo(keyEvent('l', { ctrl: true }), 'ctrl+shift+l')).toBe(false)
    expect(matchesCombo(keyEvent('k', { ctrl: true }), 'ctrl+k')).toBe(true)
    // 多余修饰键不匹配
    expect(matchesCombo(keyEvent('k', { ctrl: true, alt: true }), 'ctrl+k')).toBe(false)
  })

  it('ctrl 组合兼容 mac meta', () => {
    expect(matchesCombo(keyEvent('k', { meta: true }), 'ctrl+k')).toBe(true)
  })

  it('大写键名匹配（e.key 为 C）', () => {
    expect(matchesCombo(keyEvent('C', { alt: true, shift: true }), 'alt+shift+c')).toBe(true)
  })
})

describe('registry', () => {
  beforeEach(() => {
    clearShortcuts()
  })

  it('注册/注销/列表', () => {
    registerShortcut({ id: 'nav.chat', combo: 'ctrl+1', label: '聊天', group: '导航', handler: () => {} })
    registerShortcut({ id: 'global.undo', combo: 'ctrl+z', label: '撤销', group: '全局', handler: () => {} })
    expect(listShortcuts()).toHaveLength(2)

    unregisterShortcut('nav.chat')
    expect(listShortcuts()).toHaveLength(1)
    expect(listShortcuts()[0].id).toBe('global.undo')
  })

  it('同组合键冲突时后注册者替换并告警', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    registerShortcut({ id: 'a', combo: 'ctrl+9', label: 'A', group: 'g', handler: () => {} })
    registerShortcut({ id: 'b', combo: 'Ctrl+9', label: 'B', group: 'g', handler: () => {} })
    expect(warn).toHaveBeenCalled()
    const list = listShortcuts()
    expect(list).toHaveLength(1)
    expect(list[0].id).toBe('b')
    warn.mockRestore()
  })

  it('formatComboDisplay 输出展示格式', () => {
    expect(formatComboDisplay('ctrl+shift+l')).toBe('Ctrl+Shift+L')
    expect(formatComboDisplay('ctrl+1')).toBe('Ctrl+1')
  })
})

describe('global listener dispatch', () => {
  beforeEach(() => {
    clearShortcuts()
  })

  it('keydown 事件分发到匹配 handler 并 preventDefault', () => {
    const handler = vi.fn()
    registerShortcut({ id: 'x', combo: 'ctrl+shift+l', label: 'x', group: 'g', handler })
    const off = installGlobalShortcutListener()

    const e = keyEvent('L', { ctrl: true, shift: true })
    document.dispatchEvent(e)

    expect(handler).toHaveBeenCalledTimes(1)
    expect(e.defaultPrevented).toBe(true)
    off()
  })

  it('不匹配的组合不触发', () => {
    const handler = vi.fn()
    registerShortcut({ id: 'x', combo: 'ctrl+shift+l', label: 'x', group: 'g', handler })
    const off = installGlobalShortcutListener()

    document.dispatchEvent(keyEvent('l', { ctrl: true }))
    expect(handler).not.toHaveBeenCalled()
    off()
  })

  it('引用计数归零后监听器移除', () => {
    const handler = vi.fn()
    registerShortcut({ id: 'x', combo: 'ctrl+q', label: 'x', group: 'g', handler })
    const off1 = installGlobalShortcutListener()
    const off2 = installGlobalShortcutListener()

    off1()
    document.dispatchEvent(keyEvent('q', { ctrl: true }))
    expect(handler).toHaveBeenCalledTimes(1) // 仍有一个引用

    off2()
    document.dispatchEvent(keyEvent('q', { ctrl: true }))
    expect(handler).toHaveBeenCalledTimes(1) // 监听已移除
  })

  it('handler 抛错不影响后续注册与监听', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    registerShortcut({ id: 'bad', combo: 'ctrl+y', label: 'bad', group: 'g', handler: () => { throw new Error('boom') } })
    const off = installGlobalShortcutListener()

    expect(() => document.dispatchEvent(keyEvent('y', { ctrl: true }))).not.toThrow()
    off()
    err.mockRestore()
  })
})
