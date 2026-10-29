import { swallowError } from './swallowError'
/**
 * Thin wrapper over Web Speech API (SpeechSynthesis) for TTS read-aloud.
 * Electron/Chromium has built-in voices for en + zh; no external dep needed.
 */

const RATE_STORAGE_KEY = 'siyi_tts_rate'

export function getSavedRate(): number {
  try {
    return parseFloat(localStorage.getItem(RATE_STORAGE_KEY) ?? '') || 1.0
  } catch {
    return 1.0
  }
}

export function saveRate(rate: number): void {
  try {
    localStorage.setItem(RATE_STORAGE_KEY, String(rate))
  } catch (e) { /* quota exceeded etc */ swallowError('speech:19', e) }
}

function pickVoice(langHint: string): SpeechSynthesisVoice | null {
  const voices = speechSynthesis.getVoices()
  if (voices.length === 0) return null

  // Prefer exact match
  const exact = voices.find(v => v.lang.startsWith(langHint))
  if (exact) return exact

  // Fallback: any voice containing the lang code
  const partial = voices.find(v => v.lang.includes(langHint.split('-')[0]))
  if (partial) return partial

  return voices[0]
}

export function speak(
  text: string,
  lang: 'en' | 'zh',
  rate: number = 1.0,
  onEnd?: () => void,
): void {
  if (!text?.trim()) return

  speechSynthesis.cancel()

  const utterance = new SpeechSynthesisUtterance(text.trim())
  utterance.rate = Math.max(0.5, Math.min(2.0, rate))
  utterance.pitch = 1.0
  utterance.volume = 0.9

  const hint = lang === 'zh' ? 'zh-CN' : 'en-US'
  const voice = pickVoice(hint)
  if (voice) utterance.voice = voice
  utterance.lang = hint

  if (onEnd) {
    utterance.onend = onEnd
    utterance.onerror = onEnd
  }

  speechSynthesis.speak(utterance)
}

export function stopSpeech(): void {
  speechSynthesis.cancel()
}

export function isSpeaking(): boolean {
  return speechSynthesis.speaking
}
