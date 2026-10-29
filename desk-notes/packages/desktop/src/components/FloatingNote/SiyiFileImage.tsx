/* ── 桌面端 siyi-file:// 图片渲染组件 ── */

import { useState, useEffect } from 'react'
import { isSiyiFileUri, resolveSiyiFile, parseSiyiFilePath, guessFileName, isRelativeAssetPath, resolveRelativeAsset } from '../../utils/siyiFileResolver'
import { useTranslation } from '../../i18n'

interface Props {
  src: string
  alt?: string
}

/**
 * 渲染 siyi-file:// 图片或普通图片。
 * 如果是 siyi-file:// URI，通过后端 transfer.download RPC 获取 data URL 后渲染。
 */
export function SiyiFileImage({ src, alt }: Props) {
  const { t } = useTranslation()
  const [dataUrl, setDataUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isRelAsset = isRelativeAssetPath(src)

  useEffect(() => {
    if (!isSiyiFileUri(src) && !isRelativeAssetPath(src)) return

    let cancelled = false
    setLoading(true)
    setError(null)

    const loader = isRelativeAssetPath(src) ? resolveRelativeAsset(src) : resolveSiyiFile(src)
    loader.then((url) => {
      if (cancelled) return
      if (url) {
        setDataUrl(url)
      } else {
        setError(t('siyiFileImage.loadError'))
      }
    }).catch(() => {
      if (!cancelled) setError(t('siyiFileImage.loadFailed'))
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })

    return () => { cancelled = true }
  }, [src])

  // 普通图片（http/data）直接渲染；相对路径资产与 siyi-file 走 RPC 取回
  if (!isSiyiFileUri(src) && !isRelAsset) {
    return (
      <img
        src={src}
        alt={alt || ''}
        loading="lazy"
        className="max-w-full h-auto rounded-lg my-1.5 border border-white/[0.06]"
      />
    )
  }

  // siyi-file:// 或 assets/ 相对路径图片
  if (loading) {
    return (
      <div className="my-1.5 p-3 rounded-lg bg-white/[0.04] text-text-muted text-xs">
        {t('siyiFileImage.loading')}
      </div>
    )
  }

  if (error || !dataUrl) {
    return (
      <SiyiFilePlaceholder uri={src} error={error || undefined} />
    )
  }

  return (
    <img
      src={dataUrl}
      alt={alt || ''}
      loading="lazy"
      className="max-w-full h-auto rounded-lg my-1.5 border border-white/[0.06]"
    />
  )
}

/**
 * 渲染 siyi-file:// 文件链接（非图片）。
 */
export function SiyiFileLink({ uri, label }: { uri: string; label: string }) {
  const path = parseSiyiFilePath(uri)
  const name = guessFileName(path || uri)

  return (
    <SiyiFilePlaceholder uri={uri} label={label || name} />
  )
}

/**
 * siyi-file:// 文件占位卡片。
 * 非图片文件显示为可点击的下载卡片。
 */
function SiyiFilePlaceholder({ uri, label, error }: { uri: string; label?: string; error?: string }) {
  const { t } = useTranslation()
  const path = parseSiyiFilePath(uri)
  const name = guessFileName(path || uri)
  const displayLabel = label || name

  const handleClick = async () => {
    try {
      const dataUrl = await resolveSiyiFile(uri)
      if (!dataUrl) return

      // 如果是图片，直接打开
      if (/\.(jpg|jpeg|png|gif|webp)$/i.test(name)) {
        window.open(dataUrl)
        return
      }

      // 触发下载
      const link = document.createElement('a')
      link.href = dataUrl
      link.download = name
      link.click()
    } catch (err) {
      console.warn('[siyi-file] Download failed:', err)
    }
  }

  return (
    <div
      onClick={handleClick}
      className="my-1.5 p-2 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] cursor-pointer flex items-center gap-2 text-xs"
      title={t('siyiFileImage.downloadTitle', { name })}
    >
      <span className="text-lg">{getFileIcon(name)}</span>
      <span className="flex-1 truncate text-text-secondary">{displayLabel}</span>
      {error ? (
        <span className="text-red-400">{error}</span>
      ) : (
        <span className="text-text-muted">{t('siyiFileImage.download')}</span>
      )}
    </div>
  )
}

function getFileIcon(name: string): string {
  if (/\.(pdf)$/i.test(name)) return '📄'
  if (/\.(doc|docx)$/i.test(name)) return '📝'
  if (/\.(mp4|mov|webm)$/i.test(name)) return '🎬'
  if (/\.(mp3|wav|ogg)$/i.test(name)) return '🎵'
  if (/\.(zip|rar|7z)$/i.test(name)) return '📦'
  return '📎'
}
