import { describe, it, expect, vi } from 'vitest'
import {
  startRecording,
  blobToBase64,
  normalizeTranscript,
  type MinimalRecorder,
} from './voiceInput'

/** 造一个行为接近 MediaRecorder 的假 recorder：stop() 时同步吐一块数据并触发 onstop。 */
function makeFakeRecorder(mimeType = 'audio/webm'): MinimalRecorder & {
  start: () => void
  stop: () => void
} {
  const rec = {
    mimeType,
    state: 'inactive',
    ondataavailable: null as ((e: { data: Blob }) => void) | null,
    onstop: null as (() => void) | null,
    start() {
      this.state = 'recording'
    },
    stop() {
      this.state = 'inactive'
      const blob = new Blob(['chunk'], { type: mimeType })
      this.ondataavailable?.({ data: blob })
      this.onstop?.()
    },
  }
  return rec
}

describe('voiceInput 纯函数', () => {
  it('startRecording 拉起麦克风并返回可停止的 handle', async () => {
    const tracks = [{ stop: vi.fn() }]
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => tracks })
    const createRecorder = vi.fn(() => makeFakeRecorder())

    const handle = await startRecording({ getUserMedia, createRecorder })

    expect(getUserMedia).toHaveBeenCalledWith({ audio: true })
    expect(createRecorder).toHaveBeenCalledTimes(1)
    const blob = await handle.stop()
    expect(blob).toBeInstanceOf(Blob)
    // 停止后轨道必须被释放
    expect(tracks[0].stop).toHaveBeenCalledTimes(1)
  })

  it('startRecording 在 getUserMedia 失败时向上抛', async () => {
    const getUserMedia = vi.fn().mockRejectedValue(new Error('no mic'))
    await expect(startRecording({ getUserMedia })).rejects.toThrow('no mic')
  })

  it('cancel 停止录制并释放轨道，不 resolve blob', async () => {
    const tracks = [{ stop: vi.fn() }]
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => tracks })
    const createRecorder = vi.fn(() => makeFakeRecorder())
    const handle = await startRecording({ getUserMedia, createRecorder })
    handle.cancel()
    expect(tracks[0].stop).toHaveBeenCalledTimes(1)
  })

  it('blobToBase64 去掉 dataURL 前缀', async () => {
    const blob = new Blob(['hello'], { type: 'audio/webm' })
    const b64 = await blobToBase64(blob)
    // 'hello' 的 base64 是 aGVsbG8=
    expect(b64).toBe('aGVsbG8=')
  })

  it('normalizeTranscript 成功返回文本', () => {
    expect(normalizeTranscript({ success: true, text: '你好' })).toBe('你好')
  })

  it('normalizeTranscript 失败抛错（带后端错误文案）', () => {
    expect(() => normalizeTranscript({ success: false, error: '坏掉了' })).toThrow('坏掉了')
  })

  it('normalizeTranscript 入参异常抛默认错误', () => {
    expect(() => normalizeTranscript(null)).toThrow('转写失败')
    expect(() => normalizeTranscript(undefined)).toThrow('转写失败')
  })
})
