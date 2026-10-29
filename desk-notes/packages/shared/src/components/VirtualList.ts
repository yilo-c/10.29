/** @deprecated 2026-08-31 Wave-A 核验：零调用方死代码（实际在用 @tanstack/react-virtual），待清理删除 */
/**
 * 虚拟滚动列表
 * 
 * 功能：
 * - 高效渲染长列表（只渲染可见区域）
 * - 支持动态高度
 * - 支持滚动到指定位置
 * - 支持键盘导航
 * 
 * 使用示例：
 * ```typescript
 * const virtualList = new VirtualList({
 *   container: document.getElementById('list'),
 *   items: largeDataSet,
 *   itemHeight: 50,
 *   renderItem: (item, index) => createItemElement(item, index),
 * });
 * ```
 */

// 配置选项
export interface VirtualListOptions<T> {
  /** 容器元素 */
  container: HTMLElement
  /** 数据项数组 */
  items: T[]
  /** 固定行高（如果所有项高度相同） */
  itemHeight?: number
  /** 估算行高（动态高度时使用） */
  estimatedItemHeight?: number
  /** 缓冲区大小（额外渲染的行数） */
  overscan?: number
  /** 渲染单项的函数 */
  renderItem: (item: T, index: number) => HTMLElement
  /** 滚动回调 */
  onScroll?: (scrollTop: number) => void
  /** 可见范围变化回调 */
  onRangeChange?: (startIndex: number, endIndex: number) => void
}

// 测量缓存
interface MeasurementCache {
  heights: Map<number, number>
  positions: Map<number, number>
  totalHeight: number
}

/**
 * 虚拟滚动列表类
 */
export class VirtualList<T> {
  private container: HTMLElement
  private items: T[]
  private itemHeight: number | null
  private estimatedHeight: number
  private overscan: number
  private renderItem: (item: T, index: number) => HTMLElement
  private onScroll?: (scrollTop: number) => void
  private onRangeChange?: (startIndex: number, endIndex: number) => void

  // DOM 元素
  private spacer: HTMLElement | null = null
  private content: HTMLElement | null = null
  private renderedItems: Map<number, HTMLElement> = new Map()

  // 状态
  private scrollTop = 0
  private viewportHeight = 0
  private startIndex = 0
  private endIndex = 0
  private measurements: MeasurementCache

  // 事件处理器
  private resizeObserver: ResizeObserver | null = null
  private scrollHandler: (() => void) | null = null

  constructor(options: VirtualListOptions<T>) {
    this.container = options.container
    this.items = options.items
    this.itemHeight = options.itemHeight ?? null
    this.estimatedHeight = options.estimatedItemHeight ?? options.itemHeight ?? 50
    this.overscan = options.overscan ?? 5
    this.renderItem = options.renderItem
    this.onScroll = options.onScroll
    this.onRangeChange = options.onRangeChange

    this.measurements = {
      heights: new Map(),
      positions: new Map(),
      totalHeight: 0,
    }

    this.init()
  }

  /**
   * 初始化虚拟列表
   */
  private init(): void {
    // 设置容器样式
    this.container.style.position = 'relative'
    this.container.style.overflow = 'auto'

    // 创建滚动占位元素
    this.spacer = document.createElement('div')
    this.spacer.style.position = 'absolute'
    this.spacer.style.top = '0'
    this.spacer.style.left = '0'
    this.spacer.style.width = '100%'
    this.container.appendChild(this.spacer)

    // 创建内容容器
    this.content = document.createElement('div')
    this.content.style.position = 'relative'
    this.content.style.willChange = 'transform'
    this.container.appendChild(this.content)

    // 绑定事件
    this.scrollHandler = this.handleScroll.bind(this)
    this.container.addEventListener('scroll', this.scrollHandler, { passive: true })

    // 监听容器大小变化
    this.resizeObserver = new ResizeObserver(() => {
      this.viewportHeight = this.container.clientHeight
      this.update()
    })
    this.resizeObserver.observe(this.container)

    // 初始计算
    this.viewportHeight = this.container.clientHeight
    this.calculateTotalHeight()
    this.update()
  }

  /**
   * 处理滚动事件
   */
  private handleScroll(): void {
    this.scrollTop = this.container.scrollTop
    this.onScroll?.(this.scrollTop)
    this.update()
  }

  /**
   * 计算总高度
   */
  private calculateTotalHeight(): void {
    if (this.itemHeight) {
      // 固定高度
      this.measurements.totalHeight = this.items.length * this.itemHeight
    } else {
      // 动态高度
      let total = 0
      for (let i = 0; i < this.items.length; i++) {
        const height = this.measurements.heights.get(i) ?? this.estimatedHeight
        this.measurements.positions.set(i, total)
        total += height
      }
      this.measurements.totalHeight = total
    }

    // 更新占位元素高度
    if (this.spacer) {
      this.spacer.style.height = `${this.measurements.totalHeight}px`
    }
  }

  /**
   * 获取指定位置的索引
   */
  private getIndexAtPosition(position: number): number {
    if (this.itemHeight) {
      return Math.floor(position / this.itemHeight)
    }

    // 二分查找
    let low = 0
    let high = this.items.length - 1

    while (low <= high) {
      const mid = Math.floor((low + high) / 2)
      const midPosition = this.measurements.positions.get(mid) ?? 0

      if (midPosition < position) {
        low = mid + 1
      } else if (midPosition > position) {
        high = mid - 1
      } else {
        return mid
      }
    }

    return Math.max(0, low - 1)
  }

  /**
   * 获取指定索引的位置
   */
  private getPositionAtIndex(index: number): number {
    if (this.itemHeight) {
      return index * this.itemHeight
    }
    return this.measurements.positions.get(index) ?? 0
  }

  /**
   * 更新可见范围
   */
  private update(): void {
    // 计算可见范围
    const visibleStart = this.getIndexAtPosition(this.scrollTop)
    const visibleEnd = this.getIndexAtPosition(this.scrollTop + this.viewportHeight)

    // 添加缓冲区
    this.startIndex = Math.max(0, visibleStart - this.overscan)
    this.endIndex = Math.min(this.items.length - 1, visibleEnd + this.overscan)

    // 通知范围变化
    this.onRangeChange?.(this.startIndex, this.endIndex)

    // 渲染可见项
    this.renderVisibleItems()
  }

  /**
   * 渲染可见项
   */
  private renderVisibleItems(): void {
    if (!this.content) return

    // 移除不在可见范围的元素
    const toRemove: number[] = []
    for (const [index] of this.renderedItems) {
      if (index < this.startIndex || index > this.endIndex) {
        toRemove.push(index)
      }
    }

    for (const index of toRemove) {
      const element = this.renderedItems.get(index)
      if (element && element.parentNode) {
        element.parentNode.removeChild(element)
      }
      this.renderedItems.delete(index)
    }

    // 添加新的可见元素
    for (let index = this.startIndex; index <= this.endIndex; index++) {
      if (!this.renderedItems.has(index)) {
        const item = this.items[index]
        const element = this.renderItem(item, index)
        
        // 设置位置
        const position = this.getPositionAtIndex(index)
        element.style.position = 'absolute'
        element.style.top = `${position}px`
        element.style.left = '0'
        element.style.width = '100%'
        
        this.content.appendChild(element)
        this.renderedItems.set(index, element)

        // 测量实际高度
        if (!this.itemHeight) {
          const measuredHeight = element.offsetHeight
          if (measuredHeight !== this.measurements.heights.get(index)) {
            this.measurements.heights.set(index, measuredHeight)
            this.calculateTotalHeight()
          }
        }
      }
    }
  }

  /**
   * 更新数据
   */
  updateItems(items: T[]): void {
    this.items = items
    this.measurements.heights.clear()
    this.measurements.positions.clear()
    this.calculateTotalHeight()
    this.update()
  }

  /**
   * 追加数据
   */
  appendItems(newItems: T[]): void {
    this.items = [...this.items, ...newItems]
    this.calculateTotalHeight()
    this.update()
  }

  /**
   * 滚动到指定索引
   */
  scrollToIndex(index: number, align: 'start' | 'center' | 'end' = 'start'): void {
    let position = this.getPositionAtIndex(index)

    if (align === 'center') {
      const itemHeight = this.measurements.heights.get(index) ?? this.estimatedHeight
      position = position - (this.viewportHeight - itemHeight) / 2
    } else if (align === 'end') {
      const itemHeight = this.measurements.heights.get(index) ?? this.estimatedHeight
      position = position - this.viewportHeight + itemHeight
    }

    this.container.scrollTop = Math.max(0, position)
  }

  /**
   * 滚动到顶部
   */
  scrollToTop(): void {
    this.container.scrollTop = 0
  }

  /**
   * 滚动到底部
   */
  scrollToBottom(): void {
    this.container.scrollTop = this.measurements.totalHeight
  }

  /**
   * 获取当前可见范围
   */
  getVisibleRange(): { startIndex: number; endIndex: number } {
    return { startIndex: this.startIndex, endIndex: this.endIndex }
  }

  /**
   * 获取总高度
   */
  getTotalHeight(): number {
    return this.measurements.totalHeight
  }

  /**
   * 获取统计信息
   */
  getStats(): {
    totalItems: number
    renderedItems: number
    viewportHeight: number
    totalHeight: number
    averageItemHeight: number
  } {
    const heights = Array.from(this.measurements.heights.values())
    const avgHeight = heights.length > 0
      ? heights.reduce((a, b) => a + b, 0) / heights.length
      : this.estimatedHeight

    return {
      totalItems: this.items.length,
      renderedItems: this.renderedItems.size,
      viewportHeight: this.viewportHeight,
      totalHeight: this.measurements.totalHeight,
      averageItemHeight: avgHeight,
    }
  }

  /**
   * 销毁虚拟列表
   */
  destroy(): void {
    // 移除事件监听
    if (this.scrollHandler) {
      this.container.removeEventListener('scroll', this.scrollHandler)
    }

    // 断开 ResizeObserver
    if (this.resizeObserver) {
      this.resizeObserver.disconnect()
    }

    // 清理 DOM
    if (this.spacer && this.spacer.parentNode) {
      this.spacer.parentNode.removeChild(this.spacer)
    }
    if (this.content && this.content.parentNode) {
      this.content.parentNode.removeChild(this.content)
    }

    // 清理引用
    this.renderedItems.clear()
    this.measurements.heights.clear()
    this.measurements.positions.clear()
  }
}

/**
 * React Hook 风格的虚拟滚动（用于框架集成）
 */
export interface UseVirtualListReturn<T> {
  containerRef: (el: HTMLElement | null) => void
  virtualList: VirtualList<T> | null
  scrollToIndex: (index: number) => void
  getStats: () => ReturnType<VirtualList<T>['getStats']> | null
}

/**
 * 创建虚拟列表的工厂函数
 */
export function createVirtualList<T>(
  options: Omit<VirtualListOptions<T>, 'container'>
): {
  mount: (container: HTMLElement) => VirtualList<T>
  unmount: () => void
} {
  let list: VirtualList<T> | null = null

  return {
    mount: (container: HTMLElement) => {
      if (list) {
        list.destroy()
      }
      list = new VirtualList({ ...options, container })
      return list
    },
    unmount: () => {
      if (list) {
        list.destroy()
        list = null
      }
    },
  }
}

/**
 * 简单的虚拟滚动渲染器（用于 SSR 或简单场景）
 */
export function renderVirtualList<T>(
  items: T[],
  options: {
    scrollTop: number
    viewportHeight: number
    itemHeight: number
    overscan?: number
    renderItem: (item: T, index: number) => string
  }
): { html: string; totalHeight: number; startIndex: number; endIndex: number } {
  const { scrollTop, viewportHeight, itemHeight, overscan = 5, renderItem } = options

  const startIndex = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan)
  const endIndex = Math.min(
    items.length - 1,
    Math.ceil((scrollTop + viewportHeight) / itemHeight) + overscan
  )

  const totalHeight = items.length * itemHeight
  let html = ''

  for (let i = startIndex; i <= endIndex; i++) {
    const top = i * itemHeight
    html += `<div style="position:absolute;top:${top}px;left:0;width:100%">${renderItem(items[i], i)}</div>`
  }

  return { html, totalHeight, startIndex, endIndex }
}