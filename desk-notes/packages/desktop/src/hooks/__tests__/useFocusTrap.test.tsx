import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import React from 'react'
import { render, act } from '@testing-library/react'
import { useFocusTrap } from '../useFocusTrap'

// ── P2-13 a11y regression: focus trap keeps Tab inside the modal ─────────

interface ModalProps {
  active: boolean
  buttonCount?: number
}

const TrapModal: React.FC<ModalProps> = ({ active, buttonCount = 3 }) => {
  const ref = React.useRef<HTMLDivElement>(null)
  useFocusTrap(ref, active)
  return (
    <div ref={ref} role="dialog" aria-modal="true" tabIndex={-1} data-testid="dialog">
      {Array.from({ length: buttonCount }, (_, i) => (
        <button key={i} data-testid={`btn-${i}`}>btn{i}</button>
      ))}
    </div>
  )
}

describe('useFocusTrap', () => {
  let outerButton: HTMLButtonElement

  beforeEach(() => {
    vi.useFakeTimers()
    outerButton = document.createElement('button')
    outerButton.textContent = 'outside'
    outerButton.setAttribute('data-testid', 'outside')
    document.body.appendChild(outerButton)
    outerButton.focus()
  })

  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('moves focus into the dialog when activated', () => {
    expect(document.activeElement).toBe(outerButton)

    const { getByTestId } = render(<TrapModal active={true} />)
    act(() => { vi.runAllTimers() })

    // First focusable inside the dialog gets focus
    expect(document.activeElement).toBe(getByTestId('btn-0'))
  })

  it('restores focus to the previously focused element on deactivation', () => {
    const { rerender } = render(<TrapModal active={true} />)
    act(() => { vi.runAllTimers() })

    rerender(<TrapModal active={false} />)
    expect(document.activeElement).toBe(outerButton)
  })

  it('Tab on the last focusable cycles back to the first', () => {
    const { getByTestId } = render(<TrapModal active={true} buttonCount={3} />)
    act(() => { vi.runAllTimers() })

    const last = getByTestId('btn-2') as HTMLButtonElement
    last.focus()
    expect(document.activeElement).toBe(last)

    const evt = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    act(() => { document.dispatchEvent(evt) })

    expect(document.activeElement).toBe(getByTestId('btn-0'))
  })

  it('Shift+Tab on the first focusable cycles to the last', () => {
    const { getByTestId } = render(<TrapModal active={true} buttonCount={3} />)
    act(() => { vi.runAllTimers() })

    const first = getByTestId('btn-0') as HTMLButtonElement
    first.focus()

    const evt = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
    act(() => { document.dispatchEvent(evt) })

    expect(document.activeElement).toBe(getByTestId('btn-2'))
  })

  it('falls back to the container itself when no focusable descendants exist', () => {
    const { getByTestId } = render(<TrapModal active={true} buttonCount={0} />)
    act(() => { vi.runAllTimers() })

    expect(document.activeElement).toBe(getByTestId('dialog'))
  })
})
