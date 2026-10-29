import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { useOrbVoiceInput, type OrbVoiceDeps } from './useOrbVoiceInput'
import type { RecorderHandle } from '../utils/voiceInput'

interface Api {
  recording: boolean
  startVoice: () => Promise<boolean>
  stopVoice: () => Promise<void>
}

function Harness({ deps, api }: { deps: OrbVoiceDeps; api: { current: Api | null } }) {
  const v = useOrbVoiceInput(deps)
  api.current = v as Api
  return null
}

/** 造一个假录音 handle：stop 产出一个 webm blob。 */
function fakeHandle(): RecorderHandle {
  return {
    stop: vi.fn().mockResolvedValue(new Blob(['a'], { type: 'audio/webm' })),
    cancel: vi.fn(),
  }
}

describe('useOrbVoiceInput hook', () => {
  let api: { current: Api | null }

  beforeEach(() => {
    api = { current: null }
  })
  afterEach(() => {
    api.current = null
  })

  it('startVoice 成功 → recording=true 且返回 true', async () => {
    const startRecording = vi.fn().mockResolvedValue(fakeHandle())
    const deps: OrbVoiceDeps = { startRecording }
    render(<Harness deps={deps} api={api} />)

    let ok = false
    await act(async () => {
      ok = await api.current!.startVoice()
    })
    expect(ok).toBe(true)
    expect(api.current!.recording).toBe(true)
  })

  it('startVoice 麦克风不可用 → recording=false 且返回 false（静默降级）', async () => {
    const startRecording = vi.fn().mockRejectedValue(new Error('denied'))
    const deps: OrbVoiceDeps = { startRecording }
    render(<Harness deps={deps} api={api} />)

    let ok = true
    await act(async () => {
      ok = await api.current!.startVoice()
    })
    expect(ok).toBe(false)
    expect(api.current!.recording).toBe(false)
  })

  it('stopVoice 转写成功 → 交付 trimmed 文本给 onVoiceResult', async () => {
    const startRecording = vi.fn().mockResolvedValue(fakeHandle())
    const blobToBase64 = vi.fn().mockResolvedValue('BASE64DATA')
    const transcribe = vi.fn().mockResolvedValue({ success: true, text: '  语音内容  ' })
    const onVoiceResult = vi.fn()
    const deps: OrbVoiceDeps = { startRecording, blobToBase64, transcribe, onVoiceResult }
    render(<Harness deps={deps} api={api} />)

    await act(async () => {
      await api.current!.startVoice()
    })
    await act(async () => {
      await api.current!.stopVoice()
    })
    expect(transcribe).toHaveBeenCalledWith('BASE64DATA', 'webm')
    expect(onVoiceResult).toHaveBeenCalledTimes(1)
    expect(onVoiceResult).toHaveBeenCalledWith('语音内容')
    expect(api.current!.recording).toBe(false)
  })

  it('stopVoice 未先 start 时安全空转，不投递', async () => {
    const transcribe = vi.fn()
    const onVoiceResult = vi.fn()
    const deps: OrbVoiceDeps = { transcribe, onVoiceResult }
    render(<Harness deps={deps} api={api} />)

    await act(async () => {
      await api.current!.stopVoice()
    })
    expect(transcribe).not.toHaveBeenCalled()
    expect(onVoiceResult).not.toHaveBeenCalled()
  })

  it('stopVoice 转写失败 → 静默，不投递结果', async () => {
    const startRecording = vi.fn().mockResolvedValue(fakeHandle())
    const transcribe = vi.fn().mockResolvedValue({ success: false, error: 'bad' })
    const onVoiceResult = vi.fn()
    const deps: OrbVoiceDeps = { startRecording, transcribe, onVoiceResult }
    render(<Harness deps={deps} api={api} />)

    await act(async () => {
      await api.current!.startVoice()
    })
    await act(async () => {
      await api.current!.stopVoice()
    })
    expect(transcribe).toHaveBeenCalledTimes(1)
    expect(onVoiceResult).not.toHaveBeenCalled()
    expect(api.current!.recording).toBe(false)
  })
})
