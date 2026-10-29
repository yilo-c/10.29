import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import MdPreview from '../MdPreview'
import { useStore } from '../../../store/useStore'

// mermaid 库整体桩化：jsdom 下真实渲染依赖 DOM 布局，测试只需验证接线与降级
vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async () => ({ svg: '<svg data-testid="mm-svg"><g><rect/></g></svg>' })),
  },
}))

beforeEach(() => {
  cleanup()
  useStore.setState({ floatingNotes: [] })
})

describe('MdPreview · mermaid 渲染接线', () => {
  it('```mermaid 代码块渲染为 SVG 图，并把定义透传给 mermaid.render', async () => {
    const mermaid = (await import('mermaid')).default as unknown as {
      render: ReturnType<typeof vi.fn>
    }
    render(<MdPreview content={'```mermaid\ngraph TD; A-->B;\n```'} />)

    expect(await screen.findByTestId('mm-svg')).toBeTruthy()
    expect(mermaid.render).toHaveBeenCalled()
    expect(String(mermaid.render.mock.calls[0][1])).toContain('graph TD')
  })

  it('mermaid 渲染抛错时降级为原始代码块，不丢内容', async () => {
    const mermaid = (await import('mermaid')).default as unknown as { render: ReturnType<typeof vi.fn> }
    mermaid.render = vi.fn(async () => {
      throw new Error('parse error')
    })
    const { container } = render(<MdPreview content={'```mermaid\nBAD >>>> SYNTAX\n```'} />)

    await waitFor(() => expect(container.querySelector('.mermaid pre')).not.toBeNull())
    expect(container.textContent).toContain('BAD >>>> SYNTAX')
  })

  it('普通 ```js 代码块仍走高亮、不走 mermaid', async () => {
    const { container } = render(<MdPreview content={'```js\nconst x = 1\n```'} />)
    await waitFor(() => expect(container.textContent).toContain('const x = 1'))
    expect(container.querySelector('.mermaid')).toBeNull()
  })
})
