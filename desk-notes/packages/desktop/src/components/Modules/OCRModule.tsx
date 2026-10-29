import { useCallback, useEffect, useState } from 'react'
import { getWsClient } from '../../utils/ai'

interface OCRStatus {
  available?: boolean
  pytesseract_version?: string | null
  tesseract_binary_path?: string | null
  tesseract_version?: string | null
  languages?: string[]
  error?: string
}

interface OCRResult {
  success?: boolean
  text?: string
  error?: string
}

interface TranscribeStatus {
  available?: boolean
  version?: string | null
  cached_models?: Record<string, string> | string[]
  error?: string
}

interface TranscribeResult {
  success?: boolean
  text?: string
  error?: string
  duration_seconds?: number
}

const MAX_AUDIO_BYTES = 50 * 1024 * 1024

type OcrTab = 'ocr' | 'transcribe'

const MAX_IMAGE_BYTES = 10 * 1024 * 1024
const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/bmp', 'image/tiff', 'image/webp'])

function isOCRResult(value: unknown): value is OCRResult {
  if (!value || typeof value !== 'object') return false
  const result = value as Record<string, unknown>
  return (result.success === undefined || typeof result.success === 'boolean')
    && (result.text === undefined || typeof result.text === 'string')
    && (result.error === undefined || typeof result.error === 'string')
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('无法读取图片'))
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(file)
  })
}

export function OCRModule() {
  const [status, setStatus] = useState<OCRStatus | null>(null)
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(true)
  const [extracting, setExtracting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const result = await client.call<OCRStatus>('ocr.status', {})
      if (!result || typeof result !== 'object') throw new Error('OCR 状态响应无效')
      setStatus(result)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取 OCR 状态失败')
    } finally {
      setLoading(false)
    }
  }, [])

  // ── 转录 ──
  const [_tab, _setTab] = useState<OcrTab>('ocr')
  const [transcribeStatus, setTranscribeStatus] = useState<TranscribeStatus | null>(null)
  const [transcribeText, setTranscribeText] = useState('')
  const [transcribing, setTranscribing] = useState(false)

  const loadTranscribeStatus = useCallback(async () => {
    try {
      const client = getWsClient()
      if (!client) return
      const result = await client.call<TranscribeStatus>('transcribe.status', {})
      if (result && typeof result === 'object') setTranscribeStatus(result)
    } catch { /* 探测性调用，静默失败 */ }
  }, [])

  const tab = _tab
  const setTab = (t: OcrTab) => { _setTab(t); if (t === 'transcribe' && !transcribeStatus) void loadTranscribeStatus() }

  const transcribeAudio = async (file: File) => {
    if (file.size > MAX_AUDIO_BYTES) { setError('音频不能超过 50 MB'); return }
    setTranscribing(true)
    setError(null)
    setTranscribeText('')
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const dataUrl = await readAsDataUrl(file)
      const result = await client.call<TranscribeResult>('transcribe.transcribe_base64', {
        data: dataUrl.replace(/^data:[^;]+;base64,/, ''),
        format: file.name.split('.').pop() || '',
      })
      if (!result || typeof result !== 'object') throw new Error('转写响应无效')
      if (!result.success) throw new Error(result.error || '转写失败')
      if (!result.text?.trim()) throw new Error('未识别到语音内容')
      setTranscribeText(result.text)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '转写失败')
    } finally { setTranscribing(false) }
  }

  useEffect(() => { void loadStatus() }, [loadStatus])

  const extract = async (file: File) => {
    if (!IMAGE_TYPES.has(file.type)) {
      setError('请选择 PNG、JPG、GIF、BMP、TIFF 或 WebP 图片')
      return
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setError('图片不能超过 10 MB')
      return
    }
    setExtracting(true)
    setError(null)
    setText('')
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const data = await readAsDataUrl(file)
      const result = await client.call<unknown>('ocr.extract_base64', { data })
      if (!isOCRResult(result)) throw new Error('OCR 响应无效')
      if (!result?.success) throw new Error(result?.error || '未识别到文字')
      if (!result.text?.trim()) throw new Error('未识别到文字')
      setText(result.text)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'OCR 识别失败')
    } finally {
      setExtracting(false)
    }
  }

  // ── T3.4：桌面截图 → 直接 OCR（复用 extract 流程）─────────────────
  const dataUrlToFile = (dataUrl: string, filename: string): File => {
    const [header, base64] = dataUrl.split(',')
    const mime = /:(.*?);/.exec(header)?.[1] || 'image/png'
    const bin = atob(base64)
    const bytes = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
    return new File([bytes], filename, { type: mime })
  }

  const captureAndOcr = async () => {
    const api = (window as unknown as { electronAPI?: { captureScreen?: () => Promise<{ success?: boolean; dataUrl?: string; error?: string }> } }).electronAPI
    if (!api?.captureScreen) {
      setError('当前环境不支持桌面截图（仅桌面端可用）')
      return
    }
    setExtracting(true)
    setError(null)
    try {
      const shot = await api.captureScreen()
      if (!shot.success || !shot.dataUrl) throw new Error(shot.error || '截图失败')
      const file = dataUrlToFile(shot.dataUrl, `screenshot-${Date.now()}.png`)
      await extract(file)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '截图识别失败')
    } finally {
      setExtracting(false)
    }
  }

  return (
    <section data-testid="ocr-transcribe-workspace" className="flex min-h-0 flex-1 flex-col overflow-auto bg-[var(--body-bg)] p-6" aria-labelledby="ocr-heading">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--separator)] pb-5">
        <div>
          <span className="agent-kicker">OCR / TRANSCRIPTION</span>
          <h1 id="ocr-heading" className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">{tab === 'ocr' ? '图片文字识别' : '音频转文字'}</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">{tab === 'ocr' ? '使用本机 Tesseract 从图片中提取文字，数据不会离开本机。' : '使用本地 faster-whisper 将音频转为文字，数据不会离开本机。'}</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 rounded-lg p-0.5 border self-start" style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}>
            <button onClick={() => setTab('ocr')} className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${tab === 'ocr' ? 'text-fluent-blue bg-fluent-blue/10' : 'text-[var(--text-muted)]'}`}>OCR</button>
            <button onClick={() => setTab('transcribe')} className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${tab === 'transcribe' ? 'text-fluent-blue bg-fluent-blue/10' : 'text-[var(--text-muted)]'}`}>转录</button>
          </div>
          <button type="button" onClick={() => { if (tab === 'ocr') void loadStatus(); else void loadTranscribeStatus() }} disabled={loading} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">
            {loading ? '读取中...' : '刷新状态'}
          </button>
        </div>
      </header>

      {error && <div role="alert" className="mt-5 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-4 text-sm text-[var(--danger)]">{error}</div>}

      <div className="mt-5 grid gap-4 md:grid-cols-3">
        {tab === 'ocr' ? (
          <>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4"><span className="text-xs text-[var(--text-muted)]">OCR 引擎</span><strong className="mt-2 block text-lg text-[var(--text-primary)]">{status?.available ? '可用' : '不可用'}</strong></div>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4"><span className="text-xs text-[var(--text-muted)]">Tesseract</span><strong className="mt-2 block text-sm text-[var(--text-primary)]">{status?.tesseract_version || '未检测'}</strong></div>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4"><span className="text-xs text-[var(--text-muted)]">语言包</span><strong className="mt-2 block text-sm text-[var(--text-primary)]">{status?.languages?.join(', ') || '未检测'}</strong></div>
          </>
        ) : (
          <>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4"><span className="text-xs text-[var(--text-muted)]">Whisper 引擎</span><strong className="mt-2 block text-lg text-[var(--text-primary)]">{transcribeStatus?.available ? '可用' : '不可用'}</strong></div>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4"><span className="text-xs text-[var(--text-muted)]">版本</span><strong className="mt-2 block text-sm text-[var(--text-primary)]">{transcribeStatus?.version || '未检测'}</strong></div>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4"><span className="text-xs text-[var(--text-muted)]">已缓存模型</span><strong className="mt-2 block text-sm text-[var(--text-primary)]">{Array.isArray(transcribeStatus?.cached_models) ? (transcribeStatus.cached_models as string[]).join(', ') : (transcribeStatus?.cached_models ? String(Object.keys(transcribeStatus.cached_models).length) + ' 个' : '无')}</strong></div>
          </>
        )}
      </div>

      <main className="mt-5 grid min-h-0 flex-1 gap-5 lg:grid-cols-[minmax(260px,0.8fr)_minmax(360px,1.2fr)]">
        {tab === 'ocr' ? (
          <>
            <section className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">选择图片</h2>
              <p className="mt-2 text-sm text-[var(--text-muted)]">支持 PNG、JPG、GIF、BMP、TIFF 和 WebP。</p>
              <div className="mt-5 flex flex-col gap-3">
                <label className="flex cursor-pointer items-center justify-center rounded-md border border-dashed border-[var(--separator)] px-4 py-8 text-sm text-[var(--text-secondary)] hover:border-[var(--accent)]">
                  {extracting ? '识别中...' : '选择图片开始识别'}
                  <input type="file" accept="image/png,image/jpeg,image/gif,image/bmp,image/tiff,image/webp" className="sr-only" disabled={extracting} onChange={(event) => { const file = event.target.files?.[0]; if (file) void extract(file); event.target.value = '' }} />
                </label>
                <button type="button" onClick={() => void captureAndOcr()} disabled={extracting} className="rounded-md border border-[var(--separator)] px-4 py-3 text-sm text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] disabled:opacity-50">
                  {extracting ? '识别中...' : '📷 截图并识别'}
                </button>
              </div>
              {status?.tesseract_binary_path && <p className="mt-4 break-all text-xs text-[var(--text-muted)]">引擎路径：{status.tesseract_binary_path}</p>}
            </section>
            <section className="min-h-[260px] rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">识别结果</h2>
              <pre aria-live="polite" className="mt-4 min-h-[190px] whitespace-pre-wrap text-sm leading-7 text-[var(--text-secondary)]">{text || '识别结果将在这里显示。'}</pre>
            </section>
          </>
        ) : (
          <>
            <section className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">选择音频</h2>
              <p className="mt-2 text-sm text-[var(--text-muted)]">支持 WAV、MP3、M4A、FLAC、OGG，最大 50 MB。</p>
              <label className="mt-5 flex cursor-pointer items-center justify-center rounded-md border border-dashed border-[var(--separator)] px-4 py-8 text-sm text-[var(--text-secondary)] hover:border-[var(--accent)]">
                {transcribing ? '转写中...' : '选择音频文件开始转写'}
                <input type="file" accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.webm" className="sr-only" disabled={transcribing} onChange={(event) => { const file = event.target.files?.[0]; if (file) void transcribeAudio(file); event.target.value = '' }} />
              </label>
            </section>
            <section className="min-h-[260px] rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
              <h2 className="text-base font-semibold text-[var(--text-primary)]">转写结果</h2>
              <pre aria-live="polite" className="mt-4 min-h-[190px] whitespace-pre-wrap text-sm leading-7 text-[var(--text-secondary)]">{transcribeText || '转写结果将在这里显示。'}</pre>
            </section>
          </>
        )}
      </main>
    </section>
  )
}