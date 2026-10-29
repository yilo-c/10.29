import type { Particle } from './xiaowenTypes'

export function rand(min: number, max: number) {
  return Math.random() * (max - min) + min
}

export function createParticle(
  x: number, y: number, vx: number, vy: number,
  maxLife: number, size: number, color: string, opacity = 1,
): Particle {
  return { x, y, vx, vy, life: 0, maxLife, size, color, opacity }
}

export function spawnSwordSpark(particles: Particle[], x: number, y: number) {
  for (let i = 0; i < 3; i++) {
    const angle = rand(-0.8, 0.8) - Math.PI / 2
    particles.push(createParticle(
      x, y,
      Math.cos(angle) * rand(0.5, 2), Math.sin(angle) * rand(0.5, 2),
      rand(15, 30), rand(1.5, 3),
      'rgba(100, 200, 255, 0.8)',
    ))
  }
}

export function spawnHearts(particles: Particle[], x: number, y: number) {
  for (let i = 0; i < 4; i++) {
    const angle = rand(-1, 1) - Math.PI / 2
    particles.push(createParticle(
      x + rand(-5, 5), y,
      Math.cos(angle) * rand(0.2, 0.6), Math.sin(angle) * rand(0.4, 1.0),
      rand(25, 45), rand(2, 4),
      'rgba(255, 140, 180, 0.8)',
    ))
  }
}

export function spawnMist(particles: Particle[], x: number, y: number) {
  particles.push(createParticle(
    x, y, rand(-0.2, 0.2), rand(-0.4, -0.1),
    rand(30, 60), rand(3, 7),
    'rgba(200, 230, 255, 0.4)', 0.6,
  ))
}

export function spawnComboBurst(particles: Particle[], cx: number, cy: number) {
  for (let i = 0; i < 12; i++) {
    const angle = rand(0, Math.PI * 2)
    particles.push(createParticle(
      cx, cy,
      Math.cos(angle) * rand(1.5, 4), Math.sin(angle) * rand(1.5, 4),
      rand(20, 40), rand(2, 5),
      'rgba(100, 200, 255, 0.9)',
    ))
  }
}

const MAX_PARTICLES = 200

export function updateParticles(particles: Particle[], dtSec: number) {
  // Cap particle count to prevent memory accumulation during long drags
  while (particles.length > MAX_PARTICLES) particles.shift()

  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i]
    p.x += p.vx * dtSec * 60
    p.y += p.vy * dtSec * 60
    p.vy += 0.02
    p.life += dtSec * 1000
    if (p.life >= p.maxLife) particles.splice(i, 1)
  }
}

export function drawParticles(ctx: CanvasRenderingContext2D, particles: Particle[]) {
  for (const p of particles) {
    const alpha = p.opacity * (1 - p.life / p.maxLife)
    ctx.beginPath()
    ctx.arc(p.x, p.y, p.size * (1 - p.life / p.maxLife * 0.5), 0, Math.PI * 2)
    ctx.fillStyle = p.color.replace(/[\d.]+\)$/, `${alpha})`)
    ctx.fill()
  }
}

export function drawFrame(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  size: number,
) {
  const iw = img.naturalWidth || img.width
  const ih = img.naturalHeight || img.height
  const fitSize = size * 0.96
  const scale = Math.min(fitSize / iw, fitSize / ih)
  const displayW = iw * scale
  const displayH = ih * scale
  const dx = (size - displayW) / 2
  const dy = (size - displayH) / 2

  ctx.shadowColor = 'rgba(0,0,0,0.25)'
  ctx.shadowBlur = 6
  ctx.shadowOffsetX = 0
  ctx.shadowOffsetY = 2
  ctx.drawImage(img, dx, dy, displayW, displayH)
  ctx.shadowColor = 'transparent'
}

export function drawFlightTrail(ctx: CanvasRenderingContext2D, size: number) {
  ctx.strokeStyle = 'rgba(200, 230, 255, 0.15)'
  ctx.lineWidth = 1
  for (let i = 0; i < 3; i++) {
    const ly = size * 0.3 + i * size * 0.15
    ctx.beginPath()
    ctx.moveTo(size * 0.8, ly)
    ctx.lineTo(size * 0.95, ly)
    ctx.stroke()
  }
}

export function drawTransitionFlash(ctx: CanvasRenderingContext2D, transition: number, size: number) {
  if (transition > 0.01) {
    ctx.fillStyle = `rgba(255, 255, 255, ${transition * 0.15})`
    ctx.fillRect(0, 0, size, size)
  }
}
