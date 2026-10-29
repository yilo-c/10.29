import { Component, type ReactNode, type ErrorInfo } from 'react'
import Icon from './Icon'

import { errorStore } from '../../store/errorStore'
import { getTranslations } from '../../i18n'

interface Props {
  children: ReactNode
  /** Optional context label for error reporting */
  label?: string
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const label = this.props.label || 'ErrorBoundary'
    console.error(`[${label}]`, error, info.componentStack)
    errorStore.error(`${label}: ${error.message}`)
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    const tr = getTranslations() as any
    if (this.state.hasError) {
      return (
        <div
          className="flex flex-col items-center justify-center gap-3 p-6 select-none"
          style={{
            background: 'var(--panel-bg-solid, rgba(28,28,38,0.97))',
            color: 'var(--text-primary, rgba(255,255,255,0.85))',
            minHeight: 120, borderRadius: 12,
          }}
        >
          <Icon icon="fa-solid fa-circle-exclamation text-lg" style={{ color: 'var(--fluent-red, var(--danger))' }} />
          <span className="text-xs text-center leading-relaxed" style={{ color: 'var(--text-secondary, rgba(255,255,255,0.6))' }}>
            {this.props.label || tr?.errorBoundary?.label || 'Render error, please retry'}
          </span>
          <div className="text-xs opacity-50 max-w-[300px] truncate">{this.state.error?.message}</div>
          <button
            onClick={this.handleRetry}
            className="px-3 py-1.5 rounded-lg text-xs transition-all"
            style={{ background: 'var(--fluent-blue, var(--brand))', color: '#fff' }}
          >
            {tr?.errorBoundary?.retry || 'Retry'}
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
