// 第二档 #6：录音 → 转写 的纯逻辑模块（悬浮球长按录音 STT 的底层工具）。
//
// 与 UI 解耦、依赖可注入，便于单测：
// - startRecording：拉起麦克风并录制，返回可停止的 handle（默认用 MediaRecorder，
//   测试可注入假 Recorder）。
// - blobToBase64：音频 Blob → base64（供 transcribe.transcribe_base64 使用）。
// - normalizeTranscript：把后端 RPC 返回 {success,text,error} 规整为文本或抛错。

export interface RecorderHandle {
  /** 停止录制并返回音频 Blob。 */
  stop: () => Promise<Blob>
  /** 中止录制且不返回（错误/取消路径）。 */
  cancel: () => void
}

export interface MinimalRecorder {
  start: (timeslice?: number) => void
  stop: () => void
  ondataavailable: ((e: { data: Blob }) => void) | null
  onstop: (() => void) | null
  state: string
  mimeType: string
}

export type RecorderFactory = (stream: MediaStream) => MinimalRecorder

export interface RecordingDeps {
  getUserMedia?: (constraints: MediaStreamConstraints) => Promise<MediaStream>
  createRecorder?: RecorderFactory
}

export async function startRecording(deps: RecordingDeps = {}): Promise<RecorderHandle> {
  const getUserMedia =
    deps.getUserMedia ??
    ((c: MediaStreamConstraints) => navigator.mediaDevices.getUserMedia(c))
  const createRecorder =
    deps.createRecorder ??
    ((stream: MediaStream) => new MediaRecorder(stream) as unknown as MinimalRecorder)

  const stream = await getUserMedia({ audio: true })
  const recorder = createRecorder(stream)
  const chunks: Blob[] = []
  let resolveBlob!: (b: Blob) => void
  const blobPromise = new Promise<Blob>((res) => {
    resolveBlob = res
  })
  recorder.ondataavailable = (e: { data: Blob }) => {
    if (e.data && e.data.size > 0) chunks.push(e.data)
  }
  recorder.onstop = () => {
    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' })
    resolveBlob(blob)
  }
  recorder.start()
  return {
    async stop() {
      recorder.stop()
      const blob = await blobPromise
      stream.getTracks().forEach((t) => t.stop())
      return blob
    },
    cancel() {
      try {
        recorder.stop()
      } catch {
        /* ignore */
      }
      stream.getTracks().forEach((t) => t.stop())
    },
  }
}

/** 音频 Blob → dataURL 去掉前缀的纯 base64 串。 */
export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result || '')
      const i = dataUrl.indexOf(',')
      resolve(i >= 0 ? dataUrl.slice(i + 1) : dataUrl)
    }
    reader.onerror = () => reject(reader.error || new Error('read blob failed'))
    reader.readAsDataURL(blob)
  })
}

export interface TranscriptResult {
  success: boolean
  text?: string
  error?: string
}

/** 把后端 transcribe 返回规整为文本；失败抛错（由调用方决定是否提示）。 */
export function normalizeTranscript(result: TranscriptResult | null | undefined): string {
  if (!result || !result.success) {
    throw new Error(result?.error || '转写失败')
  }
  return result.text ?? ''
}
