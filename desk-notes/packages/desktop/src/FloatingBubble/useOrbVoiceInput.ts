// 第二档 #6：悬浮球长按录音的生命周期 hook（与鼠标手势判断解耦，便于单测）。
//
// 仅负责"开始录制 → 停止 → 转写 → 交付文本"这一段；长按定时、拖拽/点击判定留在
// FloatingOrbApp 的鼠标事件里（调用本 hook 的 startVoice/stopVoice）。

import { useCallback, useRef, useState } from 'react'
import {
  startRecording as defaultStartRecording,
  blobToBase64 as defaultBlobToBase64,
  normalizeTranscript,
  type RecorderHandle,
  type TranscriptResult,
} from '../utils/voiceInput'

export interface OrbVoiceDeps {
  /** 开始录制（默认走真实麦克风，测试注入假 recorder）。 */
  startRecording?: () => Promise<RecorderHandle>
  /** Blob → base64。 */
  blobToBase64?: (blob: Blob) => Promise<string>
  /** 调后端转写，返回 {success,text,error}。 */
  transcribe?: (dataBase64: string, format: string) => Promise<TranscriptResult | null>
  /** 转写成功后的交付（通常是注入聊天输入框）。 */
  onVoiceResult?: (text: string) => void
}

export function useOrbVoiceInput(deps: OrbVoiceDeps = {}) {
  const [recording, setRecording] = useState(false)
  const recorderRef = useRef<RecorderHandle | null>(null)

  const startRecording = deps.startRecording ?? defaultStartRecording
  const blobToBase64 = deps.blobToBase64 ?? defaultBlobToBase64
  const transcribe = deps.transcribe
  const onResultRef = useRef(deps.onVoiceResult)
  onResultRef.current = deps.onVoiceResult

  const startVoice = useCallback(async (): Promise<boolean> => {
    try {
      recorderRef.current = await startRecording()
      setRecording(true)
      return true
    } catch {
      // 麦克风权限被拒 / 设备不存在 → 静默失败，降级为普通点击
      setRecording(false)
      recorderRef.current = null
      return false
    }
  }, [startRecording])

  const stopVoice = useCallback(async (): Promise<void> => {
    const rec = recorderRef.current
    recorderRef.current = null
    setRecording(false)
    if (!rec) return
    try {
      const blob = await rec.stop()
      const dataBase64 = await blobToBase64(blob)
      const format = (blob.type || '').includes('wav') ? 'wav' : 'webm'
      const result = transcribe ? await transcribe(dataBase64, format) : null
      const text = normalizeTranscript(result)
      if (text.trim() && onResultRef.current) onResultRef.current(text.trim())
    } catch {
      /* 转写失败静默：交互信号不可阻断 UX */
    }
  }, [blobToBase64, transcribe])

  return { recording, startVoice, stopVoice }
}
