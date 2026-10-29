/**
 * AG-C2⑤（C-32④）：悬浮 orb / 浮动问答窗的几何校验与 workArea clamp。
 *
 * 现状：orb-chat.cjs 直接把 window-prefs 里的 orbX/orbY 与 pet:configure 上报的
 * windowWidth/windowHeight 交给 BrowserWindow，既无 NaN/上限校验，也不夹回工作区——
 * 一旦持久化里存了负坐标、或拔掉了副屏（prefs 记的是 2560+ 的绝对坐标）、或渲染层
 * 传了 NaN/超大值，窗口就会开到屏幕外再也拖不回来。
 *
 * 纯函数、无 electron 依赖，便于 vitest 直接单测（同 relay-backoff.cjs / path-guard.cjs 模式）。
 * 术语用中性词（workArea / anchor / size），不绑定「orb」这一具体窗型，便于 pet 侧复用。
 */

/** 窗口尺寸合法区间：低于 MIN_SIZE 无法承载内容，高于 MAX_SIZE 视为渲染层算错。 */
const MIN_SIZE = 40
const MAX_SIZE = 4000

function finiteNumber(value, fallback) {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : fallback
}

function clampNumber(value, fallback, min, max) {
  const n = finiteNumber(value, fallback)
  return Math.min(Math.max(n, min), max)
}

/** 兜底一个合法工作区（缺字段/全零时按 1920x1080 原点 0,0 处理，绝不再放大 bug）。 */
function normalizeWorkArea(area) {
  const a = area && typeof area === 'object' ? area : {}
  const width = clampNumber(a.width, 1920, MIN_SIZE, MAX_SIZE * 4)
  const height = clampNumber(a.height, 1080, MIN_SIZE, MAX_SIZE * 4)
  return {
    x: finiteNumber(a.x, 0),
    y: finiteNumber(a.y, 0),
    width,
    height,
  }
}

/**
 * 把「左上角锚点」夹回工作区：整窗尽量可见；
 * 窗口比工作区还大时贴工作区左上角（不产生负区间的抖动）。
 */
function clampAnchorToWorkArea(anchor, size, area) {
  const wa = normalizeWorkArea(area)
  const w = clampNumber(size && size.width, wa.width, 1, MAX_SIZE * 4)
  const h = clampNumber(size && size.height, wa.height, 1, MAX_SIZE * 4)
  const maxX = wa.x + Math.max(0, wa.width - w)
  const maxY = wa.y + Math.max(0, wa.height - h)
  const x = clampNumber(anchor && anchor.x, wa.x, wa.x, maxX)
  const y = clampNumber(anchor && anchor.y, wa.y, wa.y, maxY)
  return { x: Math.round(x), y: Math.round(y) }
}

/** 命中区（透明窗内可点区域）夹进窗口尺寸内，负/NaN 回落默认值。 */
function clampHitArea(hit, size, fallback) {
  const w = clampNumber(size && size.width, MAX_SIZE, MIN_SIZE, MAX_SIZE)
  const h = clampNumber(size && size.height, MAX_SIZE, MIN_SIZE, MAX_SIZE)
  const fb = hit && typeof hit === 'object' ? hit : {}
  const d = fallback && typeof fallback === 'object' ? fallback : {}
  const width = clampNumber(fb.width, d.width ?? 240, 1, w)
  const height = clampNumber(fb.height, d.height ?? 240, 1, h)
  const x = clampNumber(fb.x, d.x ?? 0, 0, Math.max(0, w - 1))
  const y = clampNumber(fb.y, d.y ?? 0, 0, Math.max(0, h - 1))
  return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) }
}

/**
 * 渲染层上报的桌宠几何（pet:configure）整体消毒：尺寸夹合法区间、命中区夹进窗口。
 * 非法/缺字段一律保留 current，绝不写入 NaN。
 */
function sanitizePetGeometry(raw, current) {
  const cur = current && typeof current === 'object' ? current : {}
  const src = raw && typeof raw === 'object' ? raw : {}
  const width = clampNumber(src.windowWidth, cur.windowWidth ?? 288, MIN_SIZE, MAX_SIZE)
  const height = clampNumber(src.windowHeight, cur.windowHeight ?? 316, MIN_SIZE, MAX_SIZE)
  const curHit = cur.hitArea || {}
  return {
    windowWidth: Math.round(width),
    windowHeight: Math.round(height),
    hitArea: clampHitArea(src.hitArea, { width, height }, curHit),
  }
}

/** 是否全部合法（用于「无需改写」判定，避免无谓 setBounds）。 */
function isPetGeometryValid(cfg) {
  if (!cfg || typeof cfg !== 'object') return false
  const w = cfg.windowWidth
  const h = cfg.windowHeight
  if (!Number.isFinite(w) || !Number.isFinite(h)) return false
  if (w < MIN_SIZE || w > MAX_SIZE || h < MIN_SIZE || h > MAX_SIZE) return false
  const hit = cfg.hitArea
  if (!hit) return true
  return [hit.x, hit.y, hit.width, hit.height].every((n) => Number.isFinite(n) && n >= 0)
}

/**
 * 问答面板候选位：先按锚点摆放，只要整窗不出工作区就采纳，否则返回 null 交调用方换向。
 * 与 clampAnchorToWorkArea 的区别是「不硬夹」——用于多方案试探（右/左/上/下）。
 */
function fitsWorkArea(anchor, size, area) {
  const wa = normalizeWorkArea(area)
  const w = clampNumber(size && size.width, 0, 0, MAX_SIZE * 4)
  const h = clampNumber(size && size.height, 0, 0, MAX_SIZE * 4)
  return anchor.x >= wa.x && anchor.y >= wa.y
    && anchor.x + w <= wa.x + wa.width && anchor.y + h <= wa.y + wa.height
}

module.exports = {
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
}
