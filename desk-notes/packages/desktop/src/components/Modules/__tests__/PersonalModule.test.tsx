import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { PersonalModule } from '../PersonalModule'

const callMock = vi.fn()
vi.mock('../../../utils/ai', () => ({
  getWsClient: () => ({ call: callMock }),
}))

beforeEach(() => { callMock.mockReset() })

describe('PersonalModule', () => {
  it('渲染工作台并显示四个 Tab', async () => {
    render(<PersonalModule />)
    expect(screen.getByText('个人知识体系')).toBeTruthy()
    expect(screen.getByText('信息导入')).toBeTruthy()
    expect(screen.getByText('个人画像')).toBeTruthy()
    expect(screen.getByText('个人回顾')).toBeTruthy()
    expect(screen.getByText('信息库')).toBeTruthy()
  })

  it('导入 Tab 展示各来源说明', () => {
    render(<PersonalModule />)
    expect(screen.getByText('微信聊天记录')).toBeTruthy()
    expect(screen.getByText('ChatGPT')).toBeTruthy()
    expect(screen.getByText('Kimi')).toBeTruthy()
    expect(screen.getByText('选择聊天记录文件导入')).toBeTruthy()
  })

  it('画像 Tab 读取并展示关注主题', async () => {
    callMock.mockResolvedValueOnce({
      success: true,
      profile: {
        long_term: { focus_topics: ['爬山', '编程'], key_people: ['张三'] },
        context: { current_focus: '投资理财' },
      },
    })
    render(<PersonalModule />)
    fireEvent.click(screen.getByText('个人画像'))
    await waitFor(() => expect(screen.getByText('爬山')).toBeTruthy())
    expect(screen.getByText('编程')).toBeTruthy()
    expect(screen.getByText('张三')).toBeTruthy()
    // current_focus 渲染在「当前专注：xxx」复合文本中，用正则匹配
    expect(screen.getByText(/投资理财/)).toBeTruthy()
  })

  it('画像 Tab 提取按钮触发 personal.profile.extract', async () => {
    callMock
      .mockResolvedValueOnce({ success: true, scanned: 3, contacts_added: 1, interests_added: 2, focus_set: '爬山', processed: 3 })
      .mockResolvedValueOnce({ success: true, profile: { long_term: { focus_topics: ['爬山'], key_people: [] } } })
    render(<PersonalModule />)
    fireEvent.click(screen.getByText('个人画像'))
    fireEvent.click(screen.getByText('从导入内容提取画像'))
    await waitFor(() => expect(callMock).toHaveBeenCalledWith('personal.profile.extract', {}))
  })

  it('回顾 Tab 显示统计与高亮', async () => {
    callMock.mockResolvedValueOnce({
      success: true,
      period: 'week',
      days: 7,
      stats: { total: 3, by_source: { wechat: 3 }, by_sensitivity: { sensitive: 3 }, contacts: ['张三'] },
      summary: '',
      highlights: ['周末一起去爬山吗？'],
    })
    render(<PersonalModule />)
    fireEvent.click(screen.getByText('个人回顾'))
    await waitFor(() => expect(screen.getByText('条目总数')).toBeTruthy())
    // 总数与敏感条目两个统计卡都显示 3，用 getAllByText
    expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('周末一起去爬山吗？')).toBeTruthy()
    expect(callMock).toHaveBeenCalledWith('personal.review', { period: 'week' })
  })

  it('信息库 Tab 展示条目并可切换筛选', async () => {
    callMock
      .mockResolvedValueOnce({ success: true, items: [{ id: 'a1', source: 'wechat', sensitivity: 'sensitive', content: '私聊内容', meta: { sender: '张三' } }], count: 1 })
      .mockResolvedValueOnce({ success: true, stats: { total: 1, processed: 1 } })
      .mockResolvedValueOnce({ success: true, items: [], count: 0 })
      .mockResolvedValueOnce({ success: true, stats: { total: 0, processed: 0 } })
    render(<PersonalModule />)
    fireEvent.click(screen.getByText('信息库'))
    await waitFor(() => expect(screen.getByText('私聊内容')).toBeTruthy())
    expect(screen.getByText('张三')).toBeTruthy()
    // 「敏感」同时出现在敏感度 badge 与筛选下拉 option 中
    expect(screen.getAllByText('敏感').length).toBeGreaterThanOrEqual(1)

    // 切换敏感度筛选触发重新加载（两个下拉：敏感度 + 来源，取第一个）
    fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'personal' } })
    await waitFor(() => expect(callMock).toHaveBeenCalledWith('personal.raw_items.list', { source: undefined, sensitivity: 'personal', limit: 100 }))
  })
})
