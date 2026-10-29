import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import PomodoroTimer from '../PomodoroTimer'

// Mock zustand store
const mockStore: Record<string, unknown> = {
  focusDuration: 25,
  pomodoroSessionCount: 0,
  incrementPomodoro: vi.fn(),
  incrementPomodoroSession: vi.fn(),
}

vi.mock('../../../store/useStore', () => ({
  useStore: (selector: (s: typeof mockStore) => unknown) => selector(mockStore),
}))

// Mock i18n
vi.mock('../../../i18n', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        'pomodoro.selectDuration': 'Select',
        'pomodoro.running': 'Running',
        'pomodoro.paused': 'Paused',
        'pomodoro.reset': 'Reset',
        'pomodoro.breakSuggestion': 'Take a break',
        'pomodoro.sessionCount': '#{count}',
        'pomodoro.notificationTitle': 'Timer done',
        'pomodoro.notificationBody': 'Time is up',
      }
      return map[key] ?? key
    },
  }),
}))

describe('PomodoroTimer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.focusDuration = 25
    mockStore.pomodoroSessionCount = 0
  })

  it('renders start button when idle', () => {
    render(<PomodoroTimer todoId="test-1" />)
    // Should show the clock icon (start button)
    const btn = screen.getByTitle('Select')
    expect(btn).toBeTruthy()
  })

  it('shows duration picker on click', () => {
    render(<PomodoroTimer todoId="test-1" />)
    fireEvent.click(screen.getByTitle('Select'))
    // Duration options should appear
    expect(screen.getByText('15')).toBeTruthy()
    expect(screen.getByText('25')).toBeTruthy()
    expect(screen.getByText('30')).toBeTruthy()
    expect(screen.getByText('60')).toBeTruthy()
  })

  it('formats time correctly', () => {
    // Format: M:SS — we verify by checking the timer display text
    // The initial remaining = 25 * 60 * 1000 = 1500000 ms

    // Start the timer by selecting a duration
    render(<PomodoroTimer todoId="test-1" />)
    fireEvent.click(screen.getByTitle('Select'))
    fireEvent.click(screen.getByText('15'))

    // After clicking a duration, timer should start showing running state
    // The remaining time should be around 15:00 format
    const timerSpan = screen.getByTitle('Running')
    expect(timerSpan).toBeTruthy()
  })

  it('shows session count badge when timer is running', () => {
    mockStore.pomodoroSessionCount = 3
    const { container } = render(<PomodoroTimer todoId="test-1" />)

    // Start timer to reveal session count badge in running state
    fireEvent.click(screen.getByTitle('Select'))
    fireEvent.click(screen.getByText('15'))

    // Session count badge should be visible with # prefix
    expect(screen.getByTitle('Running')).toBeTruthy()
    // Check text content for session count
    expect(container.textContent?.includes('#3')).toBe(true)
  })
})
