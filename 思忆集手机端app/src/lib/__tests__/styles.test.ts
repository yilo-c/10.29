import { describe, it, expect } from 'vitest'
import { inputStyle, linkBtnStyle, miniBtnStyle, cardStyle, scrollContainer, sectionTitleStyle } from '../styles'

describe('styles', () => {
  it('inputStyle has correct structure', () => {
    expect(inputStyle).toHaveProperty('width', '100%')
    expect(inputStyle).toHaveProperty('border', 'none')
    expect(inputStyle).toHaveProperty('background', 'transparent')
  })

  it('linkBtnStyle has accent color', () => {
    expect(linkBtnStyle).toHaveProperty('color', 'var(--accent)')
    expect(linkBtnStyle).toHaveProperty('border', 'none')
  })

  it('miniBtnStyle has tertiary color', () => {
    expect(miniBtnStyle).toHaveProperty('color', 'var(--text-tertiary)')
    expect(miniBtnStyle).toHaveProperty('border', 'none')
  })

  it('cardStyle uses secondary background', () => {
    expect(cardStyle).toHaveProperty('background', 'var(--bg-secondary)')
    expect(cardStyle).toHaveProperty('borderRadius', 'var(--radius-lg)')
  })

  it('scrollContainer has touch scrolling', () => {
    expect(scrollContainer).toHaveProperty('overflowY', 'auto')
    expect(scrollContainer).toHaveProperty('WebkitOverflowScrolling', 'touch')
  })

  it('sectionTitleStyle has uppercase text', () => {
    expect(sectionTitleStyle).toHaveProperty('textTransform', 'uppercase')
    expect(sectionTitleStyle).toHaveProperty('letterSpacing', 0.5)
  })
})
