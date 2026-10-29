/**
 * 小问 Canvas 绘制定制版 — 纯代码绘制 Q 版中国女剑士
 *
 * 所有数值基于 512×512 虚拟画布，最终缩放到实际 size。
 * 每帧调用 drawXiaoWen(ctx, state)，state 可插值实现丝滑动画。
 */
// ─── 可调色板 ───────────────────────────────────────────
const C = {
  skin:      '#FDDCB5',
  skinShd:   '#E8C49A',
  hair:      '#2C1810',
  hairShd:   '#1A0E08',
  hairHl:    '#4A3028',
  eyeW:      '#FFFFFF',
  eyePupil:  '#1A1A2E',
  eyeHl:     '#FFFFFF',
  robe:      '#D4456A',
  robeShd:   '#B83250',
  robeHl:    '#E86080',
  robeEdge:  '#FFD700',
  collar:    '#F0E6D3',
  belt:      '#8B4513',
  pants:     '#4A3728',
  boot:      '#2C1810',
  bootShd:   '#1A0E08',
  sword:     '#D0D0D0',
  swordHl:   '#F0F0F0',
  swordShd:  '#A0A0A0',
  tsuka:     '#3D2B1F',
  tsuba:     '#8B6914',
  blush:     '#FF8CA0',
  mouth:     '#C44A6A',
  ribbon:    '#D4456A',
}

/** 运行时骨骼状态（可插值） */
export interface XiaoWenState {
  /** 姿态名 */
  pose: 'idle' | 'defend' | 'shy' | 'fly' | 'alert'
  /** 身体旋转 (rad) */
  bodyTilt: number
  /** 左臂角度: -PI/2(上举) ~ PI/2(放下) */
  armL: number
  /** 右臂角度 */
  armR: number
  /** 头倾斜 (rad) */
  headTilt: number
  /** 呼吸浮动偏移 (px) */
  breathY: number
  /** 头发摆动 (px) */
  hairSway: number
  /** 是否眨眼 */
  blink: boolean
  /** 脸红 0-1 */
  blush: number
  /** 剑光 0-1 */
  swordGlow: number
  /** 透明度 0-1 */
  alpha: number
}

export function defaultState(pose: XiaoWenState['pose'] = 'idle'): XiaoWenState {
  return {
    pose,
    bodyTilt: 0, armL: 0.3, armR: 0.3,
    headTilt: 0, breathY: 0, hairSway: 0,
    blink: false, blush: 0, swordGlow: 0,
    alpha: 1,
  }
}

/** 姿态 → 目标骨骼值 */
export function poseTargets(pose: XiaoWenState['pose']): Partial<XiaoWenState> {
  switch (pose) {
    case 'idle':   return { armL: 0.3, armR: 0.3, headTilt: 0, blush: 0, swordGlow: 0, bodyTilt: 0 }
    case 'defend': return { armL: -0.6, armR: -1.0, headTilt: 0.03, blush: 0, swordGlow: 0.6, bodyTilt: -0.03 }
    case 'shy':    return { armL: 0.8, armR: -0.2, headTilt: -0.06, blush: 1, swordGlow: 0, bodyTilt: 0.04 }
    case 'fly':    return { armL: -0.8, armR: -0.5, headTilt: -0.02, blush: 0, swordGlow: 0, bodyTilt: -0.1 }
    case 'alert':  return { armL: -0.4, armR: -0.8, headTilt: -0.04, blush: 0, swordGlow: 0.3, bodyTilt: 0.02 }
  }
}

/** 两状态插值 */
export function lerpState(a: XiaoWenState, b: Partial<XiaoWenState>, t: number): XiaoWenState {
  const L = (va: number, vb: number) => va + (vb - va) * t
  return {
    pose: t < 0.5 ? a.pose : (b.pose ?? a.pose),
    bodyTilt: L(a.bodyTilt, b.bodyTilt ?? a.bodyTilt),
    armL: L(a.armL, b.armL ?? a.armL),
    armR: L(a.armR, b.armR ?? a.armR),
    headTilt: L(a.headTilt, b.headTilt ?? a.headTilt),
    breathY: L(a.breathY, b.breathY ?? a.breathY),
    hairSway: L(a.hairSway, b.hairSway ?? a.hairSway),
    blush: L(a.blush, b.blush ?? a.blush),
    swordGlow: L(a.swordGlow, b.swordGlow ?? a.swordGlow),
    alpha: L(a.alpha, b.alpha ?? a.alpha),
    blink: t < 0.5 ? a.blink : (b.blink ?? a.blink),
  }
}

// ─── 绘制函数 ───────────────────────────────────────────

function drawEllipse(ctx: CanvasRenderingContext2D, cx: number, cy: number, rx: number, ry: number) {
  ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2); ctx.fill()
}

export function drawXiaoWen(ctx: CanvasRenderingContext2D, s: XiaoWenState, scale = 1) {
  const sc = (n: number) => n * scale
  ctx.save()
  ctx.globalAlpha = s.alpha
  ctx.scale(scale, scale)
  ctx.translate(256, 256)
  ctx.rotate(s.bodyTilt)
  ctx.translate(-256, -256)

  // ── 身体 ──
  drawBody(ctx, sc, s)

  // ── 左臂（在身体后面）─
  drawArm(ctx, sc, s.armL, true, s)

  // ── 右臂 + 剑 ──
  drawArm(ctx, sc, s.armR, false, s)

  // ── 头 + 头发（在身体/手臂之上）─
  drawHead(ctx, sc, s)

  // ── 腿 ──
  drawLegs(ctx, sc, s)

  ctx.restore()
}

// ──────── Body ────────
function drawBody(ctx: CanvasRenderingContext2D, _sc: (n: number) => number, _s: XiaoWenState) {
  const cx = 256
  const topY = 210, botY = 370
  const topW = 60, botW = 50

  // 袍子主体
  ctx.fillStyle = C.robe
  ctx.beginPath()
  ctx.moveTo(cx - topW, topY)
  ctx.lineTo(cx + topW, topY)
  ctx.lineTo(cx + botW, botY)
  ctx.lineTo(cx - botW, botY)
  ctx.closePath()
  ctx.fill()

  // 衣领
  ctx.fillStyle = C.collar
  ctx.beginPath()
  ctx.moveTo(cx - 18, topY)
  ctx.lineTo(cx, topY + 25)
  ctx.lineTo(cx + 18, topY)
  ctx.closePath()
  ctx.fill()

  // 金色镶边
  ctx.fillStyle = C.robeEdge
  ctx.fillRect(cx - 3, topY + 20, 6, 8)

  // 腰带
  ctx.fillStyle = C.belt
  ctx.fillRect(cx - 40, 290, 80, 12)
  // 腰带装饰
  ctx.fillStyle = C.robeEdge
  ctx.fillRect(cx - 4, 288, 8, 16)
}

// ──────── Legs ────────
function drawLegs(ctx: CanvasRenderingContext2D, _sc: (n: number) => number, _s: XiaoWenState) {
  const cx = 256
  // 裤腿
  ctx.fillStyle = C.pants
  ctx.fillRect(cx - 38, 370, 30, 70)
  ctx.fillRect(cx + 8, 370, 30, 70)

  // 靴子
  ctx.fillStyle = C.boot
  ctx.beginPath()
  ctx.roundRect(cx - 42, 435, 38, 25, 4)
  ctx.fill()
  ctx.beginPath()
  ctx.roundRect(cx + 4, 435, 38, 25, 4)
  ctx.fill()
  // 靴筒
  ctx.fillStyle = C.bootShd
  ctx.fillRect(cx - 40, 425, 34, 12)
  ctx.fillRect(cx + 6, 425, 34, 12)
}

// ──────── Head ────────
function drawHead(ctx: CanvasRenderingContext2D, _sc: (n: number) => number, s: XiaoWenState) {
  const cx = 256 + s.headTilt * 20
  const cy = 155
  const faceR = 50

  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate(s.headTilt)

  // 后层头发
  ctx.fillStyle = C.hair
  drawHairBack(ctx, faceR, s)

  // 脸
  ctx.fillStyle = C.skin
  drawEllipse(ctx, 0, 0, faceR, faceR * 1.05)
  // 下巴
  ctx.fillStyle = C.skin
  ctx.beginPath()
  ctx.arc(0, faceR * 0.2, faceR * 0.85, 0.15, Math.PI - 0.15)
  ctx.fill()

  // 眼睛
  drawEyes(ctx, faceR, s)

  // 脸红
  if (s.blush > 0.01) {
    ctx.fillStyle = `rgba(255, 140, 160, ${s.blush * 0.4})`
    drawEllipse(ctx, -28, 10, 14, 10)
    drawEllipse(ctx, 28, 10, 14, 10)
  }

  // 嘴巴
  if (s.pose === 'shy' || s.pose === 'fly') {
    ctx.strokeStyle = C.mouth
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.arc(0, 22, 8, 0.1, Math.PI - 0.1)
    ctx.stroke()
  } else {
    ctx.fillStyle = C.mouth
    ctx.beginPath()
    ctx.arc(0, 22, 4, 0, Math.PI)
    ctx.fill()
  }

  // 前层头发（刘海）
  ctx.fillStyle = C.hair
  drawBangs(ctx, faceR, s)

  // 发饰 — 红色发带
  ctx.fillStyle = C.ribbon
  ctx.fillRect(-28, -50, 8, 14)
  ctx.fillRect(20, -50, 8, 14)

  ctx.restore()
}

function drawHairBack(ctx: CanvasRenderingContext2D, fr: number, _s: XiaoWenState) {
  // 后侧头发轮廓
  ctx.beginPath()
  ctx.ellipse(0, -fr * 0.3, fr * 1.1, fr * 1.2, 0, 0, Math.PI * 2)
  ctx.fill()
  // 长马尾/长发
  const sway = _s.hairSway * 5
  ctx.beginPath()
  ctx.moveTo(-fr * 0.2 + sway, fr * 0.5)
  ctx.quadraticCurveTo(-fr * 0.5 + sway, fr * 1.8, 0 + sway * 0.5, fr * 2.6)
  ctx.quadraticCurveTo(fr * 0.5 + sway, fr * 1.8, fr * 0.2 + sway, fr * 0.5)
  ctx.fill()
}

function drawBangs(ctx: CanvasRenderingContext2D, fr: number, _s: XiaoWenState) {
  // 刘海 - 几束弧形
  ctx.beginPath()
  ctx.moveTo(-fr * 0.9, -fr * 0.3)
  ctx.quadraticCurveTo(-fr * 0.7, -fr * 1.1, -fr * 0.3, -fr * 0.9)
  ctx.quadraticCurveTo(0, -fr * 1.2, fr * 0.3, -fr * 0.9)
  ctx.quadraticCurveTo(fr * 0.7, -fr * 1.1, fr * 0.9, -fr * 0.3)
  ctx.lineTo(fr * 0.85, -fr * 0.1)
  ctx.quadraticCurveTo(fr * 0.5, fr * 0.15, fr * 0.3, -fr * 0.1)
  ctx.quadraticCurveTo(0, fr * 0.2, -fr * 0.3, -fr * 0.1)
  ctx.quadraticCurveTo(-fr * 0.5, fr * 0.15, -fr * 0.85, -fr * 0.1)
  ctx.closePath()
  ctx.fill()

  // 鬓发
  ctx.beginPath()
  ctx.moveTo(-fr * 0.9, -fr * 0.1)
  ctx.quadraticCurveTo(-fr * 1.0, fr * 0.3, -fr * 0.85, fr * 0.6)
  ctx.quadraticCurveTo(-fr * 0.75, fr * 0.4, -fr * 0.8, 0)
  ctx.fill()
  ctx.beginPath()
  ctx.moveTo(fr * 0.9, -fr * 0.1)
  ctx.quadraticCurveTo(fr * 1.0, fr * 0.3, fr * 0.85, fr * 0.6)
  ctx.quadraticCurveTo(fr * 0.75, fr * 0.4, fr * 0.8, 0)
  ctx.fill()
}

function drawEyes(ctx: CanvasRenderingContext2D, _fr: number, s: XiaoWenState) {
  const eyeY = -3
  const eyeSpacing = 22

  for (const side of [-1, 1]) {
    const ex = side * eyeSpacing

    // 眼白
    ctx.fillStyle = C.eyeW
    if (s.blink) {
      // 眨眼：一条弧线
      ctx.strokeStyle = C.hair
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(ex - 9, eyeY)
      ctx.lineTo(ex + 9, eyeY)
      ctx.stroke()
    } else {
      drawEllipse(ctx, ex, eyeY, 9, 10)
      // 瞳孔
      ctx.fillStyle = C.eyePupil
      drawEllipse(ctx, ex + 2, eyeY + 1, 5, 6)
      // 高光
      ctx.fillStyle = C.eyeHl
      drawEllipse(ctx, ex + 4, eyeY - 3, 2.5, 3)
      // 上睫毛
      ctx.strokeStyle = C.hair
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.arc(ex, eyeY - 2, 10, Math.PI * 1.1, Math.PI * 1.9)
      ctx.stroke()
    }
  }
}

// ──────── Arms ────────
function drawArm(ctx: CanvasRenderingContext2D, _sc: (n: number) => number,
                 angle: number, isLeft: boolean, s: XiaoWenState) {
  const shoulder = { x: isLeft ? 210 : 302, y: 230 }
  const side = isLeft ? -1 : 1
  const len = 80

  // 袖子角度随手臂角度变化
  const ax = shoulder.x + Math.sin(angle) * len * side
  const ay = shoulder.y + Math.cos(angle) * len

  ctx.save()

  // 袖子（宽大）
  ctx.fillStyle = C.robe
  ctx.beginPath()
  ctx.moveTo(shoulder.x - 12, shoulder.y)
  ctx.quadraticCurveTo(
    shoulder.x + (ax - shoulder.x) * 0.5 - side * 15,
    shoulder.y + (ay - shoulder.y) * 0.5,
    ax + side * 8, ay
  )
  ctx.lineTo(ax, ay + 5)
  ctx.quadraticCurveTo(
    shoulder.x + (ax - shoulder.x) * 0.5 + side * 10,
    shoulder.y + (ay - shoulder.y) * 0.5 + 5,
    shoulder.x + 12, shoulder.y + 5
  )
  ctx.closePath()
  ctx.fill()

  // 手（从袖口伸出）
  const handX = ax + side * 3
  const handY = ay + 12
  ctx.fillStyle = C.skin
  drawEllipse(ctx, handX, handY, 8, 10)

  ctx.restore()

  // 右臂持剑
  if (!isLeft) {
    drawSword(ctx, ax + side * 3, ay + 12, angle, s)
  }
}

// ──────── Sword ────────
function drawSword(ctx: CanvasRenderingContext2D, hx: number, hy: number,
                   _armAngle: number, s: XiaoWenState) {
  ctx.save()
  ctx.translate(hx, hy)

  // 剑的方向：根据手臂角度和姿态
  let swordAngle: number
  if (s.pose === 'defend' || s.pose === 'alert') {
    // 举剑/指向前方
    swordAngle = -Math.PI * 0.6
  } else if (s.pose === 'fly') {
    swordAngle = -Math.PI * 0.3
  } else {
    // idle/shy — 剑垂在身侧
    swordAngle = Math.PI * 0.7
  }
  ctx.rotate(swordAngle)

  const bladeLen = 130
  const bladeW = 6

  // 剑刃
  ctx.fillStyle = C.sword
  ctx.fillRect(0, -bladeW / 2, bladeLen, bladeW)
  // 刃白
  ctx.strokeStyle = C.swordHl
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(0, -bladeW / 2)
  ctx.lineTo(bladeLen, -bladeW / 2)
  ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(0, bladeW / 2)
  ctx.lineTo(bladeLen, bladeW / 2)
  ctx.stroke()
  // 剑尖
  ctx.beginPath()
  ctx.moveTo(bladeLen, -bladeW / 2)
  ctx.lineTo(bladeLen + 8, 0)
  ctx.lineTo(bladeLen, bladeW / 2)
  ctx.fillStyle = C.swordShd
  ctx.fill()

  // 剑镡（护手）
  ctx.fillStyle = C.tsuba
  ctx.fillRect(-8, -10, 12, 20)

  // 剑柄
  ctx.fillStyle = C.tsuka
  ctx.fillRect(-8, -35, 12, 25)

  // 剑穗
  ctx.strokeStyle = C.ribbon
  ctx.lineWidth = 3
  ctx.beginPath()
  ctx.moveTo(-2, -35)
  ctx.quadraticCurveTo(-10, -50, -5, -60)
  ctx.stroke()

  // 剑光（defend/alert 时）
  if (s.swordGlow > 0.01) {
    ctx.strokeStyle = `rgba(100, 200, 255, ${s.swordGlow * 0.3})`
    ctx.lineWidth = s.swordGlow * 3
    ctx.beginPath()
    ctx.arc(bladeLen * 0.5, 0, bladeLen * 0.6, -0.3, 0.3)
    ctx.stroke()
    ctx.beginPath()
    ctx.arc(bladeLen * 0.5, 0, bladeLen * 0.4, -0.2, 0.2)
    ctx.stroke()
  }

  ctx.restore()
}
