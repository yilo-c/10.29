import React from 'react'
import { swallowError } from '../../lib/swallowError'

interface Props {
  /** 出错的区域名，用于定位（如 'todo' / 'notes' / 'ai'） */
  context?: string
  /** 自定义降级 UI；不传则用内置的 */
  fallback?: React.ReactNode
  children: React.ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: string | null
}

/**
 * 页面级错误边界。
 *
 * 为什么必须存在：手机端每个 Tab 在 App.tsx 里常驻挂载，
 * 任何一个 Tab 渲染抛错都会让整棵树卸载 → 白屏，而且用户无法切到别的 Tab 自救。
 * 用 ErrorBoundary 包住每个 Tab，单个功能崩溃只降级它自己。
 *
 * 恢复策略：
 *  - 「重试」→ 重置 hasError，重新挂载子树（适用于瞬时状态错误）
 *  - 「复制错误」→ 便于贴给我排查
 *  - 不自动上报网络，避免把用户数据带出去
 */
export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = { hasError: false, error: null, errorInfo: null }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    this.setState({ errorInfo: errorInfo.componentStack ?? null })
    // 走项目统一的错误吞咽口径，避免 console 噪音失控
    swallowError(`ErrorBoundary:${this.props.context ?? 'unknown'}`, error)
  }

  private handleRetry = (): void => {
    this.setState({ hasError: false, error: null, errorInfo: null })
  }

  private handleCopy = async (): Promise<void> => {
    const { error, errorInfo } = this.state
    const text = [
      `[${this.props.context ?? 'unknown'}] ${error?.name}: ${error?.message}`,
      error?.stack ?? '',
      errorInfo ?? '',
    ].join('\n')
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      /* 剪贴板不可用就静默失败，不影响用户继续操作 */
    }
  }

  render(): React.ReactNode {
    if (!this.state.hasError) return this.props.children
    if (this.props.fallback) return this.props.fallback

    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 'var(--space-4)',
          padding: 'var(--space-8) var(--space-6)',
          height: '100%',
          minHeight: 280,
          textAlign: 'center',
        }}
        role="alert"
      >
        <div style={{ fontSize: 40, lineHeight: 1 }} aria-hidden="true">😵</div>

        <div>
          <div style={{
            fontSize: 'var(--text-headline)',
            fontWeight: 'var(--font-weight-semibold)' as unknown as number,
            color: 'var(--text-primary)',
            marginBottom: 'var(--space-2)',
          }}>
            这块页面出了点问题
          </div>
          <div style={{
            fontSize: 'var(--text-footnote)',
            color: 'var(--text-secondary)',
            lineHeight: 1.5,
            maxWidth: 300,
          }}>
            {this.props.context ? `「${this.props.context}」` : ''}
            其它页面还能正常使用，你可以重试或者先切换别的地方。
          </div>
        </div>

        {this.state.error?.message && (
          <code style={{
            fontSize: 'var(--text-caption-2)',
            color: 'var(--text-tertiary)',
            background: 'var(--bg-secondary)',
            padding: 'var(--space-2) var(--space-3)',
            borderRadius: 'var(--radius-sm)',
            maxWidth: '100%',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            display: 'block',
          }}>
            {this.state.error.message}
          </code>
        )}

        <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
          <button
            onClick={this.handleRetry}
            style={{
              padding: '10px 20px',
              minHeight: 44,
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--accent)',
              color: '#fff',
              fontSize: 'var(--text-callout)',
              fontWeight: 'var(--font-weight-semibold)' as unknown as number,
              cursor: 'pointer',
            }}
          >
            重试
          </button>
          <button
            onClick={() => { void this.handleCopy() }}
            style={{
              padding: '10px 20px',
              minHeight: 44,
              border: '0.5px solid var(--border-default)',
              borderRadius: 'var(--radius-sm)',
              background: 'transparent',
              color: 'var(--text-secondary)',
              fontSize: 'var(--text-callout)',
              cursor: 'pointer',
            }}
          >
            复制错误
          </button>
        </div>
      </div>
    )
  }
}
