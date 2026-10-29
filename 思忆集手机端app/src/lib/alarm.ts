import { swallowError } from './swallowError'
let ctx: AudioContext | null = null
let osc: OscillatorNode | null = null
let interval: ReturnType<typeof setInterval> | null = null

export function playAlarm(): () => void {
  try {
    ctx = new AudioContext()
    osc = ctx.createOscillator()
    const gain = ctx.createGain()

    osc.connect(gain)
    gain.connect(ctx.destination)

    osc.type = 'sine'
    osc.frequency.value = 440
    gain.gain.value = 0.2

    let high = false
    interval = setInterval(() => {
      if (osc) {
        osc.frequency.value = high ? 440 : 660
        high = !high
      }
    }, 500)

    osc.start()

    if (navigator.vibrate) {
      navigator.vibrate([200, 100, 200, 100, 200, 100, 500])
    }

    return stopAlarm
  } catch {
    return () => {}
  }
}

export function stopAlarm() {
  try {
    osc?.stop()
    ctx?.close()
  } catch (e) { /* ignore */ swallowError('alarm:42', e) }
  osc = null
  ctx = null
  if (interval !== null) {
    clearInterval(interval)
    interval = null
  }
}
