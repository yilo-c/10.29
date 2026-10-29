import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import type { GraphNode } from '../../../types'

// KG-CLICK（2026-09-26）可点性回归。
// 治的三件事：① 可点面只有 r≤7 的圆点；② 标签 pointer-events:none 完全点不动；
// ③ 按下左键就平移，与节点点击同一手势（微抖即丢选中）。另加 ④ 超过 MAX_NODES
// 时按度数采样，而真实图谱 79% 是孤立点 —— 被选中/搜索命中的节点可能压根没渲染。

import ForceGraph from '../ForceGraph'

const nodeA: GraphNode = { id: 'bk-a', label: '蓝鲸计划', type: 'project' }
const nodeB: GraphNode = { id: 'bk-b', label: '王小明', type: 'person' }

function nodeGroups(container: HTMLElement) {
  return Array.from(container.querySelectorAll('svg > g > g'))
}

describe('ForceGraph 可点性', () => {
  it('每个节点都有撑大的透明命中圈（不再只有 8px 圆点）', () => {
    const { container } = render(
      <ForceGraph nodes={[nodeA, nodeB]} edges={[]} width={800} height={600} onNodeClick={() => {}} />,
    )
    const groups = nodeGroups(container)
    expect(groups.length).toBe(2)
    for (const g of groups) {
      const circles = Array.from(g.querySelectorAll('circle'))
      const hit = circles.find(c => (c.getAttribute('fill') || '') === 'rgba(0,0,0,0)')
      expect(hit, '缺少透明命中圈').toBeTruthy()
      expect(Number(hit!.getAttribute('r'))).toBeGreaterThanOrEqual(11)
    }
  })

  it('标签文字可点（不再 pointer-events:none），点击标签即选中该节点', () => {
    const onNodeClick = vi.fn()
    const { container } = render(
      <ForceGraph nodes={[nodeA, nodeB]} edges={[]} width={800} height={600} onNodeClick={onNodeClick} />,
    )
    const texts = Array.from(container.querySelectorAll('text'))
    expect(texts.length).toBe(2)
    expect(texts.every(t => !/pointer-events/i.test(t.getAttribute('style') || ''))).toBe(true)
    fireEvent.click(texts[0])
    expect(onNodeClick).toHaveBeenCalledTimes(1)
    expect(onNodeClick.mock.calls[0][0].id).toBe('bk-a')
  })

  it('未超过拖拽阈值的按下+抬起算点击；越阈值的拖拽不算点击', () => {
    const onNodeClick = vi.fn()
    const { container } = render(
      <ForceGraph nodes={[nodeA, nodeB]} edges={[]} width={800} height={600} onNodeClick={onNodeClick} />,
    )
    const svg = container.querySelector('svg')!
    const group = nodeGroups(container)[0]

    // 真点击：原地按下抬起
    fireEvent.mouseDown(group, { button: 0, clientX: 100, clientY: 100 })
    fireEvent.mouseUp(svg)
    fireEvent.click(group)
    expect(onNodeClick).toHaveBeenCalledTimes(1)

    // 拖动画布：越过 3px 阈值后抬手，不得误选/误取消
    onNodeClick.mockClear()
    fireEvent.mouseDown(svg, { button: 0, clientX: 100, clientY: 100 })
    fireEvent.mouseMove(svg, { clientX: 180, clientY: 160 })
    fireEvent.mouseUp(svg)
    fireEvent.click(group)
    expect(onNodeClick).not.toHaveBeenCalled()
  })

  it('拖拽确实平移画布（阈值不是把平移也禁掉了）', () => {
    const { container } = render(
      <ForceGraph nodes={[nodeA]} edges={[]} width={800} height={600} onNodeClick={() => {}} />,
    )
    const svg = container.querySelector('svg')!
    const before = container.querySelector('svg > g')!.getAttribute('transform')
    fireEvent.mouseDown(svg, { button: 0, clientX: 50, clientY: 50 })
    fireEvent.mouseMove(svg, { clientX: 150, clientY: 120 })
    const after = container.querySelector('svg > g')!.getAttribute('transform')
    expect(before).not.toBe(after)
  })

  it('pinnedIds 保证被选中的孤立点在超量采样后仍在画布上', () => {
    // 301 个 0 度孤立点：MAX_NODES=300 → 不钉住就一定有一个渲染不出来。
    // 只数节点组内的标签（超量时还有一条“图谱过大”提示 banner，也是 <text>）。
    const nodeLabels = (container: HTMLElement) =>
      nodeGroups(container).map(g => g.querySelector('text')?.textContent ?? null)
    const many: GraphNode[] = Array.from({ length: 301 }, (_, i) => ({
      id: `n-${i}`,
      label: `节点${i}`,
      type: 'concept',
    }))
    const pinned = many[many.length - 1]
    const { container, rerender } = render(
      <ForceGraph nodes={many} edges={[]} width={900} height={600} onNodeClick={() => {}} />,
    )
    const labelsBefore = nodeLabels(container)
    expect(labelsBefore).not.toContain(pinned.label)
    expect(labelsBefore.length).toBe(300)

    rerender(
      <ForceGraph nodes={many} edges={[]} width={900} height={600} onNodeClick={() => {}} pinnedIds={[pinned.id]} />,
    )
    const labelsAfter = nodeLabels(container)
    expect(labelsAfter).toContain(pinned.label)
    // 总量不因钉住而上涨（挤掉的是同为 0 度的末尾项）
    expect(labelsAfter.length).toBe(300)
  })
})
