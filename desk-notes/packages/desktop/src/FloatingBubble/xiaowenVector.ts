/**
 * 小问矢量渲染核心 — 纯代码绘制 Q 版中国女剑士
 *
 * 角色完全由 Canvas 路径构成：任何姿态、任何帧都是同一份绘制代码，
 * 一致性由构造保证；动作 = 参数插值，天然 60fps 丝滑。
 *
 * 坐标空间：512×512 虚拟画布（调用方负责缩放/平移）。
 */

// ─── 调色板（按小问设定锁）──────────────────────────────
export const VW = {
  skin: '#FDE3C8', skinShd: '#F3CDa4', skinLine: '#8a6a4a',
  hair: '#5D6E86', hairShd: '#47546B', hairHl: '#93A5BD', hairLine: '#333C4E',
  irisTop: '#A6D8F7', irisBot: '#4A8FD9', pupil: '#1E2A44',
  eyeLine: '#2A2E3A', eyeWhite: '#FFFFFF',
  top: '#BFE0F5', topShd: '#9CC8E8', topLine: '#5B7A94', trim: '#FFFFFF',
  skirt: '#3A4260', skirtShd: '#2C3350', skirtLine: '#232941',
  sash: '#2E6FB8', sashShd: '#245C9C',
  socks: '#FFFFFF', socksShd: '#E3E6F0',
  shoes: '#26262E', shoesHl: '#4A4A56',
  blade: '#1B1E26', bladeHl: '#4A5266', hilt: '#2B3550', guard: '#8B6914',
  tassel: '#3E8FD6',
  bear: '#C9A06A', bearShd: '#A9834F',
  blush: '#FF9DB0',
  line: '#2A2E3A',
  ice: '#9FDBFF',
}

/** 全数值姿态参数（可插值） */
export interface VState {
  bodyTilt: number      // rad，整体倾斜
  bodyBob: number       // px，垂直浮动
  squash: number        // 1 正常；<1 压扁；>1 拉伸
  headTilt: number      // rad
  armLAngle: number     // rad，0=垂下，+=向外抬起
  armLFore: number      // rad，前臂相对上臂的弯折（向身体内侧为正）
  armRAngle: number
  armRFore: number
  swordAngle: number    // 相对右手的剑角度（0=沿手臂向下）
  swordMode: number     // 0 手持 | 1 脚下 | 2 膝上 | 3 不画
  legs: number          // 0 站立 | 1 盘坐 | 2 飞行屈腿
  eyeOpen: number       // 0-1
  gazeX: number         // -1..1
  gazeY: number
  browRaise: number     // -1..1
  mouthOpen: number     // 0-1
  mouthSmile: number    // 0-1（嘴角上扬程度）
  blush: number         // 0-1
  ponySway: number      // rad，马尾摆角（物理驱动）
  skirtSway: number     // rad
  sashSway: number      // rad
  slash: number         // 0-1，剑弧特效进度（0=无）
}

export function vDefaults(): VState {
  return {
    bodyTilt: 0, bodyBob: 0, squash: 1, headTilt: 0,
    armLAngle: 0.18, armLFore: 0.15, armRAngle: 0.14, armRFore: 0.1,
    swordAngle: 0.15, swordMode: 0, legs: 0,
    eyeOpen: 1, gazeX: 0, gazeY: 0, browRaise: 0,
    mouthOpen: 0, mouthSmile: 0.5, blush: 0,
    ponySway: 0, skirtSway: 0, sashSway: 0, slash: 0,
  }
}

const NUMERIC_KEYS: (keyof VState)[] = [
  'bodyTilt', 'bodyBob', 'squash', 'headTilt',
  'armLAngle', 'armLFore', 'armRAngle', 'armRFore',
  'swordAngle', 'swordMode', 'legs',
  'eyeOpen', 'gazeX', 'gazeY', 'browRaise', 'mouthOpen', 'mouthSmile',
  'blush', 'ponySway', 'skirtSway', 'sashSway', 'slash',
]

export function lerpVState(a: VState, b: VState, t: number): VState {
  const out = { ...a }
  for (const k of NUMERIC_KEYS) {
    out[k] = a[k] + (b[k] - a[k]) * t
  }
  return out
}

export function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
}

// ─── 绘制入口 ────────────────────────────────────────────

export function drawVXiaoWen(ctx: CanvasRenderingContext2D, s: VState): void {
  ctx.save()
  // 整体：以脚底 (256, 486) 为变换中心
  ctx.translate(256, 486 + s.bodyBob)
  ctx.rotate(s.bodyTilt)
  ctx.scale(2 - s.squash, s.squash)  // squash/stretch 保面积
  ctx.translate(-256, -486)

  drawPonytail(ctx, s)
  if (s.legs === 1) drawSitLegs(ctx, s)
  else if (s.legs === 2) drawFlyLegs(ctx, s)
  else drawStandLegs(ctx, s)
  if (s.swordMode === 2) drawSwordAcross(ctx, 256, 396, 0)      // 膝上剑（裙后手前）
  drawSkirt(ctx, s)
  drawTorso(ctx, s)
  drawArm(ctx, s, true)
  if (s.swordMode === 1) drawSwordAcross(ctx, 256, 468, -0.06)  // 脚下御剑
  if (s.swordMode === 0) { /* 手持剑随右臂绘制 */ }
  drawArm(ctx, s, false)
  drawHead(ctx, s)
  if (s.slash > 0.01) drawSlash(ctx, s)

  ctx.restore()
}

// ─── 马尾 ────────────────────────────────────────────────

function drawPonytail(ctx: CanvasRenderingContext2D, s: VState): void {
  const sway = s.ponySway
  ctx.save()
  // 发结位于头顶后侧 (285, 128)
  ctx.translate(285, 128)
  ctx.rotate(0.35 + sway)

  // 马尾主体：渐细贝塞尔
  const grad = ctx.createLinearGradient(0, 0, 60, 210)
  grad.addColorStop(0, VW.hair)
  grad.addColorStop(1, VW.hairShd)
  ctx.fillStyle = grad
  ctx.beginPath()
  ctx.moveTo(-14, -6)
  ctx.bezierCurveTo(46, 20, 66, 90, 52, 170)
  ctx.bezierCurveTo(48, 200, 30, 218, 18, 224)
  ctx.bezierCurveTo(26, 190, 24, 150, 14, 110)
  ctx.bezierCurveTo(4, 70, -12, 30, -22, 8)
  ctx.closePath()
  ctx.fill()

  // 发丝高光
  ctx.strokeStyle = VW.hairHl
  ctx.lineWidth = 3
  ctx.lineCap = 'round'
  ctx.beginPath()
  ctx.moveTo(8, 26)
  ctx.bezierCurveTo(34, 60, 42, 120, 32, 180)
  ctx.stroke()

  // 发尾尖
  ctx.fillStyle = VW.hair
  ctx.beginPath()
  ctx.moveTo(18, 224)
  ctx.quadraticCurveTo(20, 244, 10, 258)
  ctx.quadraticCurveTo(12, 236, 6, 224)
  ctx.closePath()
  ctx.fill()

  ctx.restore()

  // 小熊发饰（马尾根部）
  drawBear(ctx, 288, 120)
}

function drawBear(ctx: CanvasRenderingContext2D, x: number, y: number): void {
  ctx.save()
  ctx.translate(x, y)
  ctx.fillStyle = VW.bear
  // 耳朵
  ctx.beginPath(); ctx.arc(-8, -8, 4.5, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.arc(8, -8, 4.5, 0, Math.PI * 2); ctx.fill()
  // 头
  ctx.beginPath(); ctx.arc(0, 0, 9, 0, Math.PI * 2); ctx.fill()
  // 口鼻
  ctx.fillStyle = VW.bearShd
  ctx.beginPath(); ctx.arc(0, 3, 3.5, 0, Math.PI * 2); ctx.fill()
  // 眼
  ctx.fillStyle = '#3A2E1E'
  ctx.beginPath(); ctx.arc(-3.5, -1, 1.2, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.arc(3.5, -1, 1.2, 0, Math.PI * 2); ctx.fill()
  ctx.restore()
}

// ─── 腿（三种模式）──────────────────────────────────────

function drawStandLegs(ctx: CanvasRenderingContext2D, _s: VState): void {
  for (const side of [-1, 1]) {
    const x = 256 + side * 24
    // 过膝袜
    ctx.fillStyle = VW.socks
    ctx.beginPath()
    ctx.moveTo(x - 13, 412)
    // 波浪袜口
    ctx.quadraticCurveTo(x - 6, 406, x, 411)
    ctx.quadraticCurveTo(x + 6, 406, x + 13, 412)
    ctx.lineTo(x + 12, 462)
    ctx.lineTo(x - 12, 462)
    ctx.closePath()
    ctx.fill()
    // 袜阴影
    ctx.fillStyle = VW.socksShd
    ctx.fillRect(x + 6, 414, 5, 46)
    // 鞋
    ctx.fillStyle = VW.shoes
    ctx.beginPath()
    ctx.roundRect(x - 14, 460, 30, 22, [4, 4, 9, 9])
    ctx.fill()
    ctx.fillStyle = VW.shoesHl
    ctx.beginPath()
    ctx.roundRect(x - 14, 474, 30, 8, 4)
    ctx.fill()
  }
}

function drawSitLegs(ctx: CanvasRenderingContext2D, _s: VState): void {
  // 盘坐：交叠的小腿
  ctx.fillStyle = VW.socks
  ctx.beginPath()
  ctx.roundRect(196, 428, 120, 34, 16)
  ctx.fill()
  ctx.fillStyle = VW.socksShd
  ctx.beginPath()
  ctx.roundRect(196, 446, 120, 16, 8)
  ctx.fill()
  ctx.fillStyle = VW.shoes
  ctx.beginPath(); ctx.ellipse(208, 452, 15, 10, 0.3, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.ellipse(304, 452, 15, 10, -0.3, 0, Math.PI * 2); ctx.fill()
}

function drawFlyLegs(ctx: CanvasRenderingContext2D, _s: VState): void {
  // 御剑：双腿微屈向后
  for (const side of [-1, 1]) {
    const x = 256 + side * 20
    ctx.save()
    ctx.translate(x, 420)
    ctx.rotate(side * 0.12 - 0.1)
    ctx.fillStyle = VW.socks
    ctx.beginPath()
    ctx.roundRect(-12, -6, 24, 46, 10)
    ctx.fill()
    ctx.fillStyle = VW.shoes
    ctx.beginPath()
    ctx.roundRect(-14, 36, 30, 18, [4, 4, 8, 8])
    ctx.fill()
    ctx.restore()
  }
}

// ─── 裙子 ────────────────────────────────────────────────

function drawSkirt(ctx: CanvasRenderingContext2D, s: VState): void {
  const topY = s.legs === 1 ? 358 : 352
  const botY = s.legs === 1 ? 428 : 424
  const sway = s.skirtSway * 18

  ctx.save()
  const grad = ctx.createLinearGradient(0, topY, 0, botY)
  grad.addColorStop(0, VW.skirt)
  grad.addColorStop(1, VW.skirtShd)
  ctx.fillStyle = grad
  ctx.beginPath()
  ctx.moveTo(256 - 46, topY)
  ctx.lineTo(256 + 46, topY)
  ctx.quadraticCurveTo(256 + 66, (topY + botY) / 2, 256 + 74 + sway, botY)
  // 裙摆锯齿
  for (let i = 3; i >= -3; i--) {
    const bx = 256 + i * 21 + sway * (0.6 + 0.4 * Math.abs(i) / 3)
    ctx.quadraticCurveTo(bx + 10, botY + 8, bx, botY)
  }
  ctx.quadraticCurveTo(256 - 66, (topY + botY) / 2, 256 - 46, topY)
  ctx.closePath()
  ctx.fill()

  // 褶线
  ctx.strokeStyle = VW.skirtLine
  ctx.lineWidth = 2
  for (const fx of [-30, -10, 10, 30]) {
    ctx.beginPath()
    ctx.moveTo(256 + fx, topY + 6)
    ctx.quadraticCurveTo(256 + fx * 1.4 + sway * 0.5, (topY + botY) / 2, 256 + fx * 1.9 + sway, botY - 4)
    ctx.stroke()
  }
  ctx.restore()
}

// ─── 躯干 ────────────────────────────────────────────────

function drawTorso(ctx: CanvasRenderingContext2D, s: VState): void {
  const sitting = s.legs === 1
  const topY = sitting ? 306 : 300
  const waistY = sitting ? 362 : 356

  // 上衣
  const grad = ctx.createLinearGradient(0, topY, 0, waistY)
  grad.addColorStop(0, VW.top)
  grad.addColorStop(1, VW.topShd)
  ctx.fillStyle = grad
  ctx.beginPath()
  ctx.moveTo(256 - 52, topY + 8)
  ctx.quadraticCurveTo(256, topY - 6, 256 + 52, topY + 8)
  ctx.lineTo(256 + 46, waistY)
  ctx.lineTo(256 - 46, waistY)
  ctx.closePath()
  ctx.fill()

  // 交领：左右两片 V 形
  ctx.fillStyle = VW.trim
  ctx.beginPath()
  ctx.moveTo(256 - 26, topY + 4)
  ctx.lineTo(256 + 6, topY + 30)
  ctx.lineTo(256 + 14, topY + 24)
  ctx.lineTo(256 - 14, topY + 2)
  ctx.closePath()
  ctx.fill()
  ctx.beginPath()
  ctx.moveTo(256 + 26, topY + 4)
  ctx.lineTo(256 - 2, topY + 32)
  ctx.lineTo(256 - 10, topY + 26)
  ctx.lineTo(256 + 14, topY + 2)
  ctx.closePath()
  ctx.fill()

  // 盘扣
  ctx.fillStyle = VW.line
  for (const by of [topY + 38, topY + 48]) {
    ctx.beginPath(); ctx.arc(256, by, 2.6, 0, Math.PI * 2); ctx.fill()
  }

  // 袖口云纹（肩部白色饰带）
  ctx.strokeStyle = VW.trim
  ctx.lineWidth = 3
  ctx.beginPath()
  ctx.moveTo(256 - 50, topY + 12)
  ctx.quadraticCurveTo(256 - 56, topY + 22, 256 - 50, topY + 30)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(256 + 50, topY + 12)
  ctx.quadraticCurveTo(256 + 56, topY + 22, 256 + 50, topY + 30)
  ctx.stroke()

  // 腰带 + 蝴蝶结 + 飘带
  ctx.fillStyle = VW.sash
  ctx.fillRect(256 - 47, waistY - 10, 94, 14)
  drawSashBow(ctx, 256, waistY - 2, s.sashSway)
}

function drawSashBow(ctx: CanvasRenderingContext2D, x: number, y: number, sway: number): void {
  ctx.save()
  ctx.translate(x, y)
  ctx.fillStyle = VW.sash
  // 左右蝶翅
  ctx.beginPath()
  ctx.moveTo(0, 0)
  ctx.quadraticCurveTo(-24, -12, -26, 2)
  ctx.quadraticCurveTo(-22, 12, 0, 4)
  ctx.closePath()
  ctx.fill()
  ctx.beginPath()
  ctx.moveTo(0, 0)
  ctx.quadraticCurveTo(24, -12, 26, 2)
  ctx.quadraticCurveTo(22, 12, 0, 4)
  ctx.closePath()
  ctx.fill()
  // 中心白珠
  ctx.fillStyle = VW.trim
  ctx.beginPath(); ctx.arc(0, 1, 5.5, 0, Math.PI * 2); ctx.fill()
  // 飘带
  ctx.fillStyle = VW.sashShd
  for (const side of [-1, 1]) {
    ctx.save()
    ctx.rotate(sway * side * 0.5)
    ctx.beginPath()
    ctx.moveTo(side * 5, 4)
    ctx.quadraticCurveTo(side * 10, 26, side * 7 + sway * 8, 44)
    ctx.lineTo(side * 13 + sway * 8, 42)
    ctx.quadraticCurveTo(side * 15, 22, side * 10, 3)
    ctx.closePath()
    ctx.fill()
    // 飘带末端白珠
    ctx.fillStyle = VW.trim
    ctx.beginPath(); ctx.arc(side * 10 + sway * 8, 44, 3, 0, Math.PI * 2); ctx.fill()
    ctx.fillStyle = VW.sashShd
    ctx.restore()
  }
  ctx.restore()
}

// ─── 手臂 ────────────────────────────────────────────────

function drawArm(ctx: CanvasRenderingContext2D, s: VState, isLeft: boolean): void {
  const side = isLeft ? -1 : 1
  const angle = isLeft ? s.armLAngle : s.armRAngle
  const fore = isLeft ? s.armLFore : s.armRFore
  const sx = 256 + side * 54
  const sy = 312

  const upperLen = 44
  const foreLen = 38
  const a0 = angle * side + Math.PI / 2   // 0 rad = 向下
  const ex = sx + Math.cos(a0) * upperLen
  const ey = sy + Math.sin(a0) * upperLen
  // 前臂向内侧弯折
  const a1 = a0 - fore * side * 1.6
  const hx = ex + Math.cos(a1) * foreLen
  const hy = ey + Math.sin(a1) * foreLen

  ctx.save()
  // 宽袖：肩→腕的锥形
  const grad = ctx.createLinearGradient(sx, sy, hx, hy)
  grad.addColorStop(0, VW.top)
  grad.addColorStop(1, VW.topShd)
  ctx.fillStyle = grad
  const nx = Math.cos(a0 + Math.PI / 2)
  const ny = Math.sin(a0 + Math.PI / 2)
  ctx.beginPath()
  ctx.moveTo(sx + nx * 15, sy + ny * 15)
  ctx.quadraticCurveTo((sx + hx) / 2 + nx * 22, (sy + hy) / 2 + ny * 22, hx + nx * 13, hy + ny * 13)
  ctx.lineTo(hx + Math.cos(a0) * 6 - nx * 11, hy + Math.sin(a0) * 6 - ny * 11)
  ctx.quadraticCurveTo((sx + hx) / 2 - nx * 16, (sy + hy) / 2 - ny * 16, sx - nx * 13, sy - ny * 13)
  ctx.closePath()
  ctx.fill()
  // 袖口白色云纹带
  ctx.strokeStyle = VW.trim
  ctx.lineWidth = 4
  ctx.beginPath()
  ctx.moveTo(hx + nx * 12, hy + ny * 12)
  ctx.lineTo(hx + Math.cos(a0) * 5 - nx * 10, hy + Math.sin(a0) * 5 - ny * 10)
  ctx.stroke()

  // 手
  ctx.fillStyle = VW.skin
  ctx.beginPath()
  ctx.arc(hx + Math.cos(a1) * 6, hy + Math.sin(a1) * 6, 8, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()

  // 右手持剑（手持模式）
  if (!isLeft && s.swordMode === 0) {
    drawSwordInHand(ctx, hx + Math.cos(a1) * 6, hy + Math.sin(a1) * 6, a1 + s.swordAngle)
  }
}

// ─── 剑 ──────────────────────────────────────────────────

function drawSwordInHand(ctx: CanvasRenderingContext2D, hx: number, hy: number, angle: number): void {
  ctx.save()
  ctx.translate(hx, hy)
  ctx.rotate(angle)
  // 剑身（向下）
  ctx.fillStyle = VW.blade
  ctx.beginPath()
  ctx.roundRect(-4, 10, 8, 130, 3)
  ctx.fill()
  ctx.fillStyle = VW.bladeHl
  ctx.fillRect(-1, 12, 2, 124)
  // 剑镡
  ctx.fillStyle = VW.guard
  ctx.beginPath()
  ctx.roundRect(-11, 4, 22, 7, 3)
  ctx.fill()
  // 剑柄
  ctx.fillStyle = VW.hilt
  ctx.beginPath()
  ctx.roundRect(-3.5, -16, 7, 22, 3)
  ctx.fill()
  // 蓝流苏剑穗
  ctx.strokeStyle = VW.tassel
  ctx.lineWidth = 2.5
  ctx.lineCap = 'round'
  ctx.beginPath()
  ctx.moveTo(0, -14)
  ctx.quadraticCurveTo(10, -22, 14, -34)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(14, -34)
  ctx.moveTo(12, -32)
  ctx.lineTo(18, -44)
  ctx.moveTo(14, -34)
  ctx.lineTo(10, -46)
  ctx.moveTo(14, -34)
  ctx.lineTo(16, -48)
  ctx.stroke()
  ctx.restore()
}

function drawSwordAcross(ctx: CanvasRenderingContext2D, x: number, y: number, angle: number): void {
  ctx.save()
  ctx.translate(x, y)
  ctx.rotate(angle)
  ctx.fillStyle = VW.blade
  ctx.beginPath()
  ctx.roundRect(-120, -4, 240, 8, 4)
  ctx.fill()
  ctx.fillStyle = VW.bladeHl
  ctx.fillRect(-116, -2, 228, 2)
  // 剑镡 + 剑柄在一端
  ctx.fillStyle = VW.guard
  ctx.beginPath(); ctx.roundRect(96, -9, 8, 18, 3); ctx.fill()
  ctx.fillStyle = VW.hilt
  ctx.beginPath(); ctx.roundRect(104, -4, 30, 8, 3); ctx.fill()
  // 剑穗
  ctx.strokeStyle = VW.tassel
  ctx.lineWidth = 2.5
  ctx.beginPath()
  ctx.moveTo(134, 0)
  ctx.quadraticCurveTo(146, 10, 144, 24)
  ctx.stroke()
  ctx.restore()
}

// ─── 头部 ────────────────────────────────────────────────

function drawHead(ctx: CanvasRenderingContext2D, s: VState): void {
  const cx = 256
  const cy = 200
  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate(s.headTilt)

  // 鬓角后发（脸侧）
  ctx.fillStyle = VW.hairShd
  ctx.beginPath()
  ctx.ellipse(0, -8, 96, 98, 0, 0, Math.PI * 2)
  ctx.fill()

  // 脸
  ctx.fillStyle = VW.skin
  ctx.beginPath()
  ctx.ellipse(0, 4, 88, 90, 0, 0, Math.PI * 2)
  ctx.fill()
  // 脸颊阴影
  ctx.fillStyle = 'rgba(240, 190, 150, 0.25)'
  ctx.beginPath()
  ctx.ellipse(0, 46, 66, 34, 0, 0, Math.PI)
  ctx.fill()

  drawFace(ctx, s)
  drawBangs(ctx, s)
  ctx.restore()
}

function drawFace(ctx: CanvasRenderingContext2D, s: VState): void {
  const eyeY = 16
  for (const side of [-1, 1]) {
    const ex = side * 38
    // 眉毛
    ctx.strokeStyle = VW.hairShd
    ctx.lineWidth = 3.5
    ctx.lineCap = 'round'
    ctx.beginPath()
    const browY = -14 - s.browRaise * 6
    ctx.moveTo(ex - 12, browY + side * s.browRaise * 2)
    ctx.quadraticCurveTo(ex, browY - 4, ex + 12, browY)
    ctx.stroke()

    const open = Math.max(0.05, s.eyeOpen)
    if (open < 0.15) {
      // 闭眼弧线
      ctx.strokeStyle = VW.eyeLine
      ctx.lineWidth = 3.5
      ctx.beginPath()
      ctx.arc(ex, eyeY + 2, 13, Math.PI * 0.15, Math.PI * 0.85)
      ctx.stroke()
      continue
    }
    // 眼白
    ctx.fillStyle = VW.eyeWhite
    ctx.beginPath()
    ctx.ellipse(ex, eyeY, 17, 21 * open, 0, 0, Math.PI * 2)
    ctx.fill()
    // 虹膜
    const gx = s.gazeX * 5
    const gy = s.gazeY * 4
    const irisGrad = ctx.createLinearGradient(0, eyeY - 16, 0, eyeY + 16)
    irisGrad.addColorStop(0, VW.irisTop)
    irisGrad.addColorStop(1, VW.irisBot)
    ctx.fillStyle = irisGrad
    ctx.beginPath()
    ctx.ellipse(ex + gx, eyeY + gy + 1, 12.5, 16 * open, 0, 0, Math.PI * 2)
    ctx.fill()
    // 瞳孔
    ctx.fillStyle = VW.pupil
    ctx.beginPath()
    ctx.ellipse(ex + gx, eyeY + gy + 2, 6, 8.5 * open, 0, 0, Math.PI * 2)
    ctx.fill()
    // 高光
    ctx.fillStyle = VW.eyeWhite
    ctx.beginPath()
    ctx.ellipse(ex + gx - 4, eyeY + gy - 6 * open, 3.6, 4.4 * open, 0, 0, Math.PI * 2)
    ctx.fill()
    ctx.beginPath()
    ctx.ellipse(ex + gx + 5, eyeY + gy + 6 * open, 1.8, 2.2 * open, 0, 0, Math.PI * 2)
    ctx.fill()
    // 上睫毛
    ctx.strokeStyle = VW.eyeLine
    ctx.lineWidth = 4
    ctx.beginPath()
    ctx.ellipse(ex, eyeY - 2, 18, 21 * open, 0, Math.PI * 1.05, Math.PI * 1.95)
    ctx.stroke()
  }

  // 腮红
  if (s.blush > 0.02) {
    ctx.fillStyle = `rgba(255, 157, 176, ${0.45 * s.blush})`
    ctx.beginPath(); ctx.ellipse(-52, 40, 13, 8, 0, 0, Math.PI * 2); ctx.fill()
    ctx.beginPath(); ctx.ellipse(52, 40, 13, 8, 0, 0, Math.PI * 2); ctx.fill()
  }

  // 嘴
  const my = 56
  if (s.mouthOpen > 0.1) {
    ctx.fillStyle = '#B8505E'
    ctx.beginPath()
    ctx.ellipse(0, my, 9, 7 * s.mouthOpen + 3, 0, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#E8808E'
    ctx.beginPath()
    ctx.ellipse(0, my + 3, 5, 3 * s.mouthOpen + 1, 0, 0, Math.PI)
    ctx.fill()
  } else {
    ctx.strokeStyle = '#B8505E'
    ctx.lineWidth = 3
    ctx.lineCap = 'round'
    ctx.beginPath()
    const smile = s.mouthSmile
    ctx.moveTo(-9, my - smile * 2)
    ctx.quadraticCurveTo(0, my + 6 * smile, 9, my - smile * 2)
    ctx.stroke()
  }
}

function drawBangs(ctx: CanvasRenderingContext2D, _s: VState): void {
  ctx.fillStyle = VW.hair
  // 顶部发盖 + 锯齿刘海
  ctx.beginPath()
  ctx.moveTo(-92, 6)
  ctx.quadraticCurveTo(-100, -66, -46, -92)
  ctx.quadraticCurveTo(0, -108, 46, -92)
  ctx.quadraticCurveTo(100, -66, 92, 6)
  // 刘海下缘尖角
  ctx.lineTo(74, -8)
  ctx.quadraticCurveTo(66, -30, 54, -34)
  ctx.lineTo(44, -2)
  ctx.quadraticCurveTo(34, -34, 18, -40)
  ctx.lineTo(6, 2)
  ctx.quadraticCurveTo(-6, -36, -22, -40)
  ctx.lineTo(-34, -4)
  ctx.quadraticCurveTo(-46, -32, -58, -32)
  ctx.lineTo(-70, -4)
  ctx.quadraticCurveTo(-80, -24, -92, 6)
  ctx.closePath()
  ctx.fill()

  // 侧发绺（脸侧垂落）
  for (const side of [-1, 1]) {
    ctx.beginPath()
    ctx.moveTo(side * 88, -20)
    ctx.quadraticCurveTo(side * 100, 30, side * 84, 76)
    ctx.quadraticCurveTo(side * 78, 88, side * 70, 92)
    ctx.quadraticCurveTo(side * 80, 50, side * 74, 8)
    ctx.closePath()
    ctx.fill()
  }

  // 头发高光
  ctx.strokeStyle = VW.hairHl
  ctx.lineWidth = 4
  ctx.lineCap = 'round'
  ctx.beginPath()
  ctx.arc(0, -60, 52, Math.PI * 1.25, Math.PI * 1.55)
  ctx.stroke()
  ctx.beginPath()
  ctx.arc(0, -60, 52, Math.PI * 1.65, Math.PI * 1.75)
  ctx.stroke()

  // 头顶发结（马尾束起处）
  ctx.fillStyle = VW.hairShd
  ctx.beginPath()
  ctx.ellipse(30, -88, 16, 12, 0.5, 0, Math.PI * 2)
  ctx.fill()
}

// ─── 剑弧特效 ────────────────────────────────────────────

function drawSlash(ctx: CanvasRenderingContext2D, s: VState): void {
  const p = s.slash
  const alpha = Math.sin(Math.PI * Math.min(1, p)) * 0.8
  ctx.save()
  ctx.translate(256, 300)
  ctx.strokeStyle = `rgba(159, 219, 255, ${alpha})`
  ctx.lineCap = 'round'
  // 三段渐细弧线
  const sweep = -1.6 + p * 2.6
  for (let i = 0; i < 3; i++) {
    ctx.lineWidth = 7 - i * 2
    ctx.globalAlpha = alpha * (1 - i * 0.25)
    ctx.beginPath()
    ctx.arc(0, 0, 120 + i * 14, sweep - 0.9 + i * 0.08, sweep + 0.25)
    ctx.stroke()
  }
  ctx.restore()
}
