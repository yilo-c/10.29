import { describe, it, expect, beforeEach } from 'vitest'
import { applyCustomTheme } from '../customTheme'
import type { CustomTheme } from '../../types'

describe('applyCustomTheme', () => {
  const mockTheme: CustomTheme = {
    id: 'test-1',
    name: '测试主题',
    createdAt: Date.now(),
    colors: {
      panelBg: '#1e1e2e',
      panelBgSolid: '#2a2a3e',
      textPrimary: '#ffffff',
      textSecondary: '#a0a0b0',
      accent: '#7c3aed',
      borderColor: '#3a3a4e',
      bodyBg: '#0f0f1a',
    },
  }

  beforeEach(() => {
    document.documentElement.style.cssText = ''
  })

  it('sets CSS variables on document root when theme is provided', () => {
    applyCustomTheme(mockTheme)

    const root = document.documentElement
    expect(root.style.getPropertyValue('--panel-bg')).toBe('#1e1e2e')
    expect(root.style.getPropertyValue('--panel-bg-solid')).toBe('#2a2a3e')
    expect(root.style.getPropertyValue('--text-primary')).toBe('#ffffff')
    expect(root.style.getPropertyValue('--text-secondary')).toBe('#a0a0b0')
    expect(root.style.getPropertyValue('--panel-border')).toBe('#3a3a4e')
    expect(root.style.getPropertyValue('--panel-border-accent')).toBe('#3a3a4e')
    expect(root.style.getPropertyValue('--body-bg')).toBe('#0f0f1a')
  })

  it('sets derived CSS variables', () => {
    applyCustomTheme(mockTheme)

    const root = document.documentElement
    expect(root.style.getPropertyValue('--input-bg')).toBe('#3a3a4e')
    expect(root.style.getPropertyValue('--hover-bg')).toBe('#3a3a4e')
    expect(root.style.getPropertyValue('--separator')).toBe('#3a3a4e')
  })

  it('clears all overrides when theme is null', () => {
    // Set some variables first
    const root = document.documentElement
    root.style.setProperty('--panel-bg', '#ff0000')
    root.style.setProperty('--text-primary', '#ff0000')
    root.style.setProperty('--body-bg', '#ff0000')

    applyCustomTheme(null)

    expect(root.style.getPropertyValue('--panel-bg')).toBe('')
    expect(root.style.getPropertyValue('--text-primary')).toBe('')
    expect(root.style.getPropertyValue('--text-secondary')).toBe('')
    expect(root.style.getPropertyValue('--panel-border')).toBe('')
    expect(root.style.getPropertyValue('--panel-border-accent')).toBe('')
    expect(root.style.getPropertyValue('--body-bg')).toBe('')
    expect(root.style.getPropertyValue('--input-bg')).toBe('')
    expect(root.style.getPropertyValue('--hover-bg')).toBe('')
    expect(root.style.getPropertyValue('--separator')).toBe('')
  })

  it('does not throw when called with null on clean root', () => {
    expect(() => applyCustomTheme(null)).not.toThrow()
  })

  it('injects optional semantic colors when provided', () => {
    const richTheme: CustomTheme = {
      id: 'test-rich',
      name: '13字段主题',
      createdAt: Date.now(),
      colors: {
        ...mockTheme.colors,
        success: '#22c55e',
        warning: '#f59e0b',
        danger: '#ef4444',
        accentWeak: '#7c3aed33',
        hoverBg: '#26263a',
        overlayBg: 'rgba(0,0,0,0.6)',
      },
    }

    applyCustomTheme(richTheme)

    const root = document.documentElement
    expect(root.style.getPropertyValue('--success')).toBe('#22c55e')
    expect(root.style.getPropertyValue('--warning')).toBe('#f59e0b')
    expect(root.style.getPropertyValue('--danger')).toBe('#ef4444')
    expect(root.style.getPropertyValue('--accent-soft')).toBe('#7c3aed33')
    expect(root.style.getPropertyValue('--overlay-bg')).toBe('rgba(0,0,0,0.6)')
    // Explicit hoverBg wins over the borderColor-derived default
    expect(root.style.getPropertyValue('--hover-bg')).toBe('#26263a')
  })

  it('skips optional variables and keeps derived fallback when fields absent', () => {
    applyCustomTheme(mockTheme)

    const root = document.documentElement
    expect(root.style.getPropertyValue('--success')).toBe('')
    expect(root.style.getPropertyValue('--warning')).toBe('')
    expect(root.style.getPropertyValue('--danger')).toBe('')
    expect(root.style.getPropertyValue('--accent-soft')).toBe('')
    expect(root.style.getPropertyValue('--overlay-bg')).toBe('')
    expect(root.style.getPropertyValue('--hover-bg')).toBe('#3a3a4e')
  })

  it('clears optional overrides when theme is null', () => {
    const root = document.documentElement
    root.style.setProperty('--success', '#ff0000')
    root.style.setProperty('--warning', '#ff0000')
    root.style.setProperty('--danger', '#ff0000')
    root.style.setProperty('--accent-soft', '#ff0000')
    root.style.setProperty('--overlay-bg', '#ff0000')

    applyCustomTheme(null)

    expect(root.style.getPropertyValue('--success')).toBe('')
    expect(root.style.getPropertyValue('--warning')).toBe('')
    expect(root.style.getPropertyValue('--danger')).toBe('')
    expect(root.style.getPropertyValue('--accent-soft')).toBe('')
    expect(root.style.getPropertyValue('--overlay-bg')).toBe('')
  })

  it('handles theme with empty color strings', () => {
    const emptyTheme: CustomTheme = {
      id: 'empty',
      name: '空主题',
      createdAt: Date.now(),
      colors: {
        panelBg: '',
        panelBgSolid: '',
        textPrimary: '',
        textSecondary: '',
        accent: '',
        borderColor: '',
        bodyBg: '',
      },
    }

    expect(() => applyCustomTheme(emptyTheme)).not.toThrow()
    const root = document.documentElement
    expect(root.style.getPropertyValue('--panel-bg')).toBe('')
  })
})
