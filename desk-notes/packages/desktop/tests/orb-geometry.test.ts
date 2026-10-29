import { describe, it, expect } from 'vitest'

/**
 * AG-C2②（C-32④）：orb-geometry.cjs 纯函数单测。
 *
 * orb-chat.cjs 的窗口创建（window-prefs orbX/orbY）、pet:configure 调整、
 * 问答面板定位等所有坐标路径现已接入本模块 clamp。本文件覆盖交接单要求的
 * 四类用例：NaN / 负坐标 / 超大值 / 拔掉副屏（prefs 记的是外屏绝对坐标）。
 */
const {
  MIN_SIZE,
  MAX_SIZE,
  finiteNumber,
  clampNumber,
  normalizeWorkArea,
  clampAnchorToWorkArea,
  clampHitArea,
  sanitizePetGeometry,
  isPetGeometryValid,
  fitsWorkArea,
} = await import('../electron/orb-geometry.cjs')

/** 主屏工作区基准（1920x1080，原点 0,0） */
const WA = { x: 0, y: 0, width: 1920, height: 1080 }
const ORB = { width: 288, height: 316 }

describe('finiteNumber / clampNumber', () => {
  it('NaN / Infinity / 非数字 → fallback', () => {
    expect(finiteNumber(NaN, 5)).toBe(5)
    expect(finiteNumber(Infinity, 5)).toBe(5)
    expect(finiteNumber('abc', 5)).toBe(5)
    expect(finiteNumber(undefined, 5)).toBe(5)
    expect(finiteNumber('42', 5)).toBe(42)
  })

  it('clampNumber 夹回区间，非法值取 fallback 后再夹', () => {
    expect(clampNumber(-10, 0, 0, 100)).toBe(0)
    expect(clampNumber(999, 0, 0, 100)).toBe(100)
    expect(clampNumber(NaN, 50, 0, 100)).toBe(50)
  })
})

describe('normalizeWorkArea', () => {
  it('缺字段/NaN → 兜底 1920x1080 原点 0,0（0 值高度按最小合法值处理）', () => {
    expect(normalizeWorkArea(undefined)).toEqual({ x: 0, y: 0, width: 1920, height: 1080 })
    expect(normalizeWorkArea({ width: NaN, height: 0 })).toEqual({ x: 0, y: 0, width: 1920, height: MIN_SIZE })
  })

  it('合法工作区原样保留（含多屏负原点）', () => {
    expect(normalizeWorkArea({ x: -1920, y: 0, width: 1920, height: 1040 }))
      .toEqual({ x: -1920, y: 0, width: 1920, height: 1040 })
  })
})

describe('clampAnchorToWorkArea（创建窗口的锚点路径）', () => {
  it('NaN 坐标 → 回落到工作区左上角，绝不写 NaN', () => {
    const r = clampAnchorToWorkArea({ x: NaN, y: NaN }, ORB, WA)
    expect(r).toEqual({ x: 0, y: 0 })
  })

  it('负坐标 → 夹回工作区内', () => {
    expect(clampAnchorToWorkArea({ x: -500, y: -800 }, ORB, WA)).toEqual({ x: 0, y: 0 })
  })

  it('超大值 → 夹回工作区右下边界内（整窗可见）', () => {
    const r = clampAnchorToWorkArea({ x: 99999, y: 99999 }, ORB, WA)
    expect(r.x).toBe(1920 - 288)
    expect(r.y).toBe(1080 - 316)
  })

  it('拔掉副屏：prefs 记的外屏绝对坐标（2560+）夹回主屏', () => {
    const r = clampAnchorToWorkArea({ x: 2560, y: 200 }, ORB, WA)
    expect(r.x).toBe(1920 - 288)
    expect(r.y).toBe(200)
  })

  it('工作区带非零原点时相对边界夹紧', () => {
    const wa = { x: 100, y: 50, width: 800, height: 600 }
    expect(clampAnchorToWorkArea({ x: 0, y: 0 }, ORB, wa)).toEqual({ x: 100, y: 50 })
  })

  it('窗口比工作区还大 → 贴工作区左上角（无负区间抖动）', () => {
    expect(clampAnchorToWorkArea({ x: 50, y: 50 }, { width: 4000, height: 4000 }, WA))
      .toEqual({ x: 0, y: 0 })
  })

  it('非法 size（NaN/负）不放大错误：NaN 宽按工作区宽兜底 → 锚点夹到贴边', () => {
    // width NaN → 兜底 1920（=工作区宽）→ maxX=0；height -5 → 兜底 1 → maxY 不受限
    expect(clampAnchorToWorkArea({ x: 10, y: 10 }, { width: NaN, height: -5 }, WA))
      .toEqual({ x: 0, y: 10 })
  })

  it('结果取整（无小数像素）', () => {
    const r = clampAnchorToWorkArea({ x: 10.4, y: 20.6 }, ORB, WA)
    expect(Number.isInteger(r.x)).toBe(true)
    expect(Number.isInteger(r.y)).toBe(true)
  })
})

describe('sanitizePetGeometry（pet:configure 上报路径）', () => {
  it('NaN 宽高 → 回落 current 值，绝不写 NaN', () => {
    const cur = { windowWidth: 300, windowHeight: 320, hitArea: { x: 24, y: 76, width: 240, height: 240 } }
    const r = sanitizePetGeometry({ windowWidth: NaN, windowHeight: 'abc', hitArea: NaN }, cur)
    expect(r.windowWidth).toBe(300)
    expect(r.windowHeight).toBe(320)
    expect(r.hitArea).toEqual(cur.hitArea)
  })

  it('负尺寸/负命中区 → 夹回合法区间', () => {
    const r = sanitizePetGeometry({ windowWidth: -100, windowHeight: -1, hitArea: { x: -5, y: -5, width: -10, height: -10 } }, {})
    expect(r.windowWidth).toBe(MIN_SIZE)
    expect(r.windowHeight).toBe(MIN_SIZE)
    expect(r.hitArea).toEqual({ x: 0, y: 0, width: 1, height: 1 })
  })

  it('超大尺寸 → 夹到 MAX_SIZE 上限', () => {
    const r = sanitizePetGeometry({ windowWidth: 1e9, windowHeight: 1e9 }, {})
    expect(r.windowWidth).toBe(MAX_SIZE)
    expect(r.windowHeight).toBe(MAX_SIZE)
  })

  it('命中区超出窗口 → 夹进窗口尺寸内', () => {
    const r = sanitizePetGeometry(
      { windowWidth: 200, windowHeight: 200, hitArea: { x: 0, y: 0, width: 5000, height: 5000 } },
      {},
    )
    expect(r.hitArea.width).toBeLessThanOrEqual(200)
    expect(r.hitArea.height).toBeLessThanOrEqual(200)
  })

  it('部分字段缺省时逐字段回落 current（保留旧 hitArea 宽高）', () => {
    const cur = { windowWidth: 288, windowHeight: 316, hitArea: { x: 24, y: 76, width: 240, height: 240 } }
    const r = sanitizePetGeometry({ hitArea: { x: 30 } }, cur)
    expect(r.windowWidth).toBe(288)
    expect(r.hitArea.x).toBe(30)
    expect(r.hitArea.width).toBe(240)
    expect(r.hitArea.height).toBe(240)
  })
})

describe('isPetGeometryValid', () => {
  it('NaN / 越界尺寸 → false', () => {
    expect(isPetGeometryValid({ windowWidth: NaN, windowHeight: 300 })).toBe(false)
    expect(isPetGeometryValid({ windowWidth: 10, windowHeight: 300 })).toBe(false)
    expect(isPetGeometryValid({ windowWidth: 99999, windowHeight: 300 })).toBe(false)
    expect(isPetGeometryValid(null)).toBe(false)
  })

  it('负命中区坐标 → false，合法配置 → true', () => {
    expect(isPetGeometryValid({ windowWidth: 288, windowHeight: 316, hitArea: { x: -1, y: 0, width: 10, height: 10 } })).toBe(false)
    expect(isPetGeometryValid({ windowWidth: 288, windowHeight: 316, hitArea: { x: 24, y: 76, width: 240, height: 240 } })).toBe(true)
  })
})

describe('fitsWorkArea（问答面板候选位试探，不硬夹）', () => {
  const CHAT = { width: 340, height: 440 }

  it('完全在工作区内 → true', () => {
    expect(fitsWorkArea({ x: 100, y: 100 }, CHAT, WA)).toBe(true)
  })

  it('右溢出/下溢出/负坐标 → false', () => {
    expect(fitsWorkArea({ x: 1920 - 100, y: 100 }, CHAT, WA)).toBe(false)
    expect(fitsWorkArea({ x: 100, y: 1080 - 100 }, CHAT, WA)).toBe(false)
    expect(fitsWorkArea({ x: -1, y: 100 }, CHAT, WA)).toBe(false)
  })

  it('NaN 锚点判定为不放得下（NaN 比较为 false）→ 交调用方换向/兜底 clamp', () => {
    expect(fitsWorkArea({ x: NaN, y: NaN }, CHAT, WA)).toBe(false)
  })
})

describe('clampHitArea', () => {
  it('NaN 命中区 → 逐字段回落 fallback', () => {
    const r = clampHitArea(NaN, { width: 288, height: 316 }, { x: 24, y: 76, width: 240, height: 240 })
    expect(r).toEqual({ x: 24, y: 76, width: 240, height: 240 })
  })

  it('无 fallback 时使用内置默认 240x240', () => {
    const r = clampHitArea(undefined, { width: 288, height: 316 })
    expect(r.width).toBe(240)
    expect(r.height).toBe(240)
  })
})
