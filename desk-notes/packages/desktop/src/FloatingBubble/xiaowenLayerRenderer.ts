/**
 * XiaoWen procedural layer renderer (方案三 v2)
 *
 * Composites pre-cut layers with 60fps procedural animation:
 *   - breathing bob + gentle body sway
 *   - ponytail spring sway (follows breath velocity)
 *   - blink via aligned closed-eye patches (crossfade, no painted eyelids)
 *   - orbiting ice-blue droplets with correct front/back occlusion
 *   - occasional sword-aura sparkles
 *
 * Used for idle-equivalent poses; action poses still use frame clips.
 */

export interface LayerEye {
  cx: number
  cy: number
  w: number
  h: number
}

export interface ClosedPatchSpec {
  file: string
  x: number
  y: number
  w: number
  h: number
}

export interface LayerManifest {
  sourceSize: [number, number]
  hairPivot: [number, number]
  eyes: LayerEye[]
  eyesClosed: ClosedPatchSpec[]
  swordZone: { x0: number; y0: number; x1: number; y1: number } | null
  physics: {
    breath: { amplitudePx: number; periodSec: number }
    hairSway: { stiffness: number; damping: number; maxAngleRad: number }
    blink: { intervalMs: [number, number]; closeDurationMs: number; doubleBlinkChance: number }
    sway: { maxAngleRad: number; periodSec: [number, number] }
    droplets: {
      count: number
      orbitPeriodSec: [number, number]
      fadeInMs: number
      holdMs: [number, number]
      fadeOutMs: number
      restMs: [number, number]
    }
    sparkle: { intervalMs: [number, number]; perBurst: [number, number]; lifeMs: number }
  }
}

interface ClosedPatch {
  img: HTMLImageElement
  x: number
  y: number
  w: number
  h: number
}

export interface LayerAssets {
  body: HTMLImageElement
  hairBack: HTMLImageElement
  closedPatches: ClosedPatch[]
  manifest: LayerManifest
}

interface Sparkle {
  x: number
  y: number
  vx: number
  vy: number
  life: number
  maxLife: number
  size: number
}

type DropletPhase = 'rest' | 'fadein' | 'hold' | 'fadeout'

interface Droplet {
  phase0: number   // initial orbit angle
  speed: number    // rad/s
  rJitter: number  // radius multiplier
  size: number     // base radius px (source space)
}

/** Mutable per-instance animation state */
export interface LayerAnimState {
  now0: number          // first-frame timestamp (0 = unset)
  // hair spring
  theta: number
  omega: number
  // blink
  nextBlink: number
  blinkStart: number
  blinksRemaining: number
  // body sway
  swayPeriod: number
  swayPhase: number
  // droplets
  droplets: Droplet[]
  dropPhase: DropletPhase
  dropPhaseStart: number
  dropHoldDur: number
  // sparkles
  sparkles: Sparkle[]
  nextSparkle: number
}

function randBetween(min: number, max: number): number {
  return min + Math.random() * (max - min)
}

export function createLayerAnimState(): LayerAnimState {
  return {
    now0: 0,
    theta: 0, omega: 0,
    nextBlink: 0, blinkStart: 0, blinksRemaining: 0,
    swayPeriod: 7.5, swayPhase: Math.random() * Math.PI * 2,
    droplets: [],
    dropPhase: 'rest', dropPhaseStart: 0, dropHoldDur: 10000,
    sparkles: [], nextSparkle: 0,
  }
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => resolve(img)
    img.onerror = reject
    img.src = src
  })
}

export async function loadLayerAssets(): Promise<LayerAssets | null> {
  try {
    const res = await fetch('pet/xiaowen/layers/xiaowen_layers.json')
    if (!res.ok) throw new Error(`manifest ${res.status}`)
    const manifest = (await res.json()) as LayerManifest
    const [body, hairBack] = await Promise.all([
      loadImage('pet/xiaowen/layers/body.png'),
      loadImage('pet/xiaowen/layers/hair_back.png'),
    ])
    // closed-eye patches are optional: blink degrades gracefully without them
    const closedPatches: ClosedPatch[] = []
    await Promise.all((manifest.eyesClosed ?? []).map(async spec => {
      try {
        const img = await loadImage(`pet/xiaowen/${spec.file}`)
        closedPatches.push({ img, x: spec.x, y: spec.y, w: spec.w, h: spec.h })
      } catch {
        console.warn(`[XiaoWen Layers] missing patch ${spec.file}, blink disabled`)
      }
    }))
    closedPatches.sort((a, b) => a.x - b.x)
    return { body, hairBack, closedPatches, manifest }
  } catch (e) {
    console.warn('[XiaoWen Layers] unavailable, falling back to frames:', e)
    return null
  }
}

function initDroplets(state: LayerAnimState, manifest: LayerManifest): void {
  if (state.droplets.length) return
  const cfg = manifest.physics.droplets
  for (let i = 0; i < cfg.count; i++) {
    state.droplets.push({
      phase0: (i / cfg.count) * Math.PI * 2 + randBetween(-0.2, 0.2),
      speed: (Math.PI * 2) / randBetween(cfg.orbitPeriodSec[0], cfg.orbitPeriodSec[1]),
      rJitter: randBetween(0.88, 1.12),
      size: randBetween(7, 12),
    })
  }
}

/** Droplet envelope 0..1 driven by the rest→fadein→hold→fadeout cycle */
function dropletEnvelope(state: LayerAnimState, manifest: LayerManifest, now: number): number {
  const cfg = manifest.physics.droplets
  if (state.dropPhaseStart === 0) {
    state.dropPhaseStart = now + randBetween(cfg.restMs[0] * 0.4, cfg.restMs[0])
  }
  const el = now - state.dropPhaseStart
  switch (state.dropPhase) {
    case 'rest':
      if (el >= 0) { state.dropPhase = 'fadein'; state.dropPhaseStart = now }
      return 0
    case 'fadein': {
      const t = el / cfg.fadeInMs
      if (t >= 1) {
        state.dropPhase = 'hold'
        state.dropPhaseStart = now
        state.dropHoldDur = randBetween(cfg.holdMs[0], cfg.holdMs[1])
        return 1
      }
      return t * t * (3 - 2 * t)  // smoothstep
    }
    case 'hold':
      if (el >= state.dropHoldDur) { state.dropPhase = 'fadeout'; state.dropPhaseStart = now }
      return 1
    case 'fadeout': {
      const t = el / cfg.fadeOutMs
      if (t >= 1) {
        state.dropPhase = 'rest'
        state.dropPhaseStart = now + randBetween(cfg.restMs[0], cfg.restMs[1])
        return 0
      }
      const u = 1 - t
      return u * u * (3 - 2 * u)
    }
  }
}

function dropletPos(
  d: Droplet, tSec: number, cx: number, cy: number,
  rx: number, ry: number, tilt: number,
): { x: number; y: number; depth: number } {
  const a = tSec * d.speed + d.phase0
  const ex = Math.cos(a) * rx * d.rJitter
  const ey = Math.sin(a) * ry * d.rJitter
  return {
    x: cx + ex * Math.cos(tilt) - ey * Math.sin(tilt),
    y: cy + ex * Math.sin(tilt) + ey * Math.cos(tilt),
    depth: Math.sin(a),  // >0 front, <0 back
  }
}

function drawDroplet(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, alpha: number, front: boolean): void {
  const g = ctx.createRadialGradient(x - r * 0.25, y - r * 0.3, r * 0.1, x, y, r)
  g.addColorStop(0, `rgba(235, 250, 255, ${0.9 * alpha})`)
  g.addColorStop(0.45, `rgba(150, 215, 255, ${0.55 * alpha})`)
  g.addColorStop(1, `rgba(110, 190, 250, 0)`)
  ctx.fillStyle = g
  ctx.beginPath()
  ctx.arc(x, y, r, 0, Math.PI * 2)
  ctx.fill()
  if (front) {
    ctx.fillStyle = `rgba(255, 255, 255, ${0.85 * alpha})`
    ctx.beginPath()
    ctx.arc(x - r * 0.3, y - r * 0.35, r * 0.22, 0, Math.PI * 2)
    ctx.fill()
  }
}

/** Blink closure 0 (open) → 1 (closed), with occasional double blink */
function blinkClosure(state: LayerAnimState, manifest: LayerManifest, now: number): number {
  const cfg = manifest.physics.blink
  if (state.nextBlink === 0) {
    state.nextBlink = now + randBetween(cfg.intervalMs[0], cfg.intervalMs[1])
    return 0
  }
  if (now < state.nextBlink) return 0
  if (state.blinkStart === 0) {
    state.blinkStart = now
    state.blinksRemaining = Math.random() < cfg.doubleBlinkChance ? 1 : 0
  }
  const t = (now - state.blinkStart) / cfg.closeDurationMs
  if (t >= 1) {
    if (state.blinksRemaining > 0) {
      state.blinksRemaining -= 1
      state.blinkStart = now
      return 0
    }
    state.blinkStart = 0
    state.nextBlink = now + randBetween(cfg.intervalMs[0], cfg.intervalMs[1])
    return 0
  }
  return t < 0.5 ? t * 2 : (1 - t) * 2
}

function spawnSparkles(state: LayerAnimState, manifest: LayerManifest, now: number): void {
  const cfg = manifest.physics.sparkle
  const zone = manifest.swordZone
  if (!zone) return
  if (state.nextSparkle === 0) {
    state.nextSparkle = now + randBetween(cfg.intervalMs[0], cfg.intervalMs[1])
    return
  }
  if (now < state.nextSparkle) return
  state.nextSparkle = now + randBetween(cfg.intervalMs[0], cfg.intervalMs[1])
  const count = Math.round(randBetween(cfg.perBurst[0], cfg.perBurst[1]))
  for (let i = 0; i < count; i++) {
    state.sparkles.push({
      x: randBetween(zone.x0 + 10, zone.x1 - 10),
      y: randBetween(zone.y0, zone.y1),
      vx: randBetween(-6, 6),
      vy: randBetween(-55, -30),   // source px/s, drift upward
      life: 0,
      maxLife: cfg.lifeMs * randBetween(0.7, 1.3),
      size: randBetween(3, 6),
    })
  }
}

/**
 * Draw the procedural idle composite. All manifest coordinates live in
 * source-image space and are scaled to fit `size` (0.96 fit, centered),
 * matching drawFrame().
 */
export function drawLayerIdle(
  ctx: CanvasRenderingContext2D,
  size: number,
  assets: LayerAssets,
  state: LayerAnimState,
  now: number,
): void {
  const { body, hairBack, closedPatches, manifest } = assets
  const { sourceSize, hairPivot, physics } = manifest
  if (state.now0 === 0) state.now0 = now
  const tSec = (now - state.now0) / 1000

  const s = (size * 0.96) / sourceSize[1]
  const dw = sourceSize[0] * s
  const dh = sourceSize[1] * s
  const dx = (size - dw) / 2
  const dy = (size - dh) / 2

  // ── breathing ──
  const w = (2 * Math.PI) / physics.breath.periodSec
  const ampPx = physics.breath.amplitudePx * s
  const bob = Math.sin(tSec * w) * ampPx
  const bobV = Math.cos(tSec * w) * ampPx * w

  // ── hair spring follows breath velocity ──
  const maxA = physics.hairSway.maxAngleRad
  const target = Math.max(-maxA, Math.min(maxA, -bobV * 0.0035))
  state.omega = state.omega * physics.hairSway.damping
    + (target - state.theta) * physics.hairSway.stiffness
  state.theta += state.omega

  // ── body sway ──
  const sway = physics.sway
  const swayAngle = sway.maxAngleRad * Math.sin((2 * Math.PI * tSec) / state.swayPeriod + state.swayPhase)

  // ── droplets & sparkles bookkeeping ──
  initDroplets(state, manifest)
  const env = dropletEnvelope(state, manifest, now)
  spawnSparkles(state, manifest, now)
  const dt = 1 / 60
  for (let i = state.sparkles.length - 1; i >= 0; i--) {
    const sp = state.sparkles[i]
    sp.life += dt * 1000
    sp.x += sp.vx * dt
    sp.y += sp.vy * dt
    if (sp.life >= sp.maxLife) state.sparkles.splice(i, 1)
  }

  const lid = closedPatches.length ? blinkClosure(state, manifest, now) : 0

  // droplet orbit geometry (canvas space)
  const dcx = dx + dw * 0.5
  const dcy = dy + dh * 0.42
  const drx = dw * 0.62
  const dry = dw * 0.19
  const tilt = 0.16

  ctx.save()

  // whole-body transform: sway around feet + breathing bob
  const footX = dx + dw * 0.5
  const footY = dy + dh * 0.985
  ctx.translate(footX, footY + bob)
  ctx.rotate(swayAngle)
  ctx.translate(-footX, -footY)

  // back-half droplets (behind hair & body)
  if (env > 0.01) {
    for (const d of state.droplets) {
      const p = dropletPos(d, tSec, dcx, dcy, drx, dry, tilt)
      if (p.depth >= 0) continue
      const depthN = (p.depth + 1) / 2   // 0 far .. 0.5 middle
      drawDroplet(ctx, p.x, p.y, d.size * s * (0.55 + 0.3 * depthN), env * (0.3 + 0.3 * depthN), false)
    }
  }

  // hair back — rotated around the hair-tie pivot
  const px = dx + hairPivot[0] * s
  const py = dy + hairPivot[1] * s
  ctx.save()
  ctx.translate(px, py)
  ctx.rotate(state.theta)
  ctx.translate(-px, -py)
  ctx.drawImage(hairBack, dx, dy, dw, dh)
  ctx.restore()

  // body
  ctx.drawImage(body, dx, dy, dw, dh)

  // closed-eye patches (crossfade)
  if (lid > 0.02) {
    ctx.save()
    ctx.globalAlpha = lid
    for (const p of closedPatches) {
      ctx.drawImage(p.img, dx + p.x * s, dy + p.y * s, p.w * s, p.h * s)
    }
    ctx.restore()
  }

  // front-half droplets
  if (env > 0.01) {
    for (const d of state.droplets) {
      const p = dropletPos(d, tSec, dcx, dcy, drx, dry, tilt)
      if (p.depth < 0) continue
      const depthN = (p.depth + 1) / 2   // 0.5 middle .. 1 near
      drawDroplet(ctx, p.x, p.y, d.size * s * (0.6 + 0.4 * depthN), env * (0.35 + 0.45 * depthN), true)
    }
  }

  // sword-aura sparkles
  for (const sp of state.sparkles) {
    const k = 1 - sp.life / sp.maxLife
    const a = Math.sin(Math.PI * (sp.life / sp.maxLife))  // ease in-out
    const x = dx + sp.x * s
    const y = dy + sp.y * s + bob
    const r = sp.size * s * (0.6 + 0.4 * k)
    const g = ctx.createRadialGradient(x, y, 0, x, y, r * 2)
    g.addColorStop(0, `rgba(220, 245, 255, ${0.9 * a})`)
    g.addColorStop(0.5, `rgba(140, 210, 255, ${0.45 * a})`)
    g.addColorStop(1, 'rgba(120, 200, 255, 0)')
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.arc(x, y, r * 2, 0, Math.PI * 2)
    ctx.fill()
  }

  ctx.restore()
}
