/**
 * Media helpers — handles saving images/videos to Electron assets/ directory
 * and provides fallback for browser mode.
 *
 * In Electron mode, assets:// protocol is registered in main.cjs so that
 * <img src="assets://xxx.png"> and <video src="assets://xxx.mp4"> load
 * directly without base64 conversion (streaming support for large files).
 */

const ei = (globalThis as { electronAPI?: ElectronAPI | undefined }).electronAPI
const isElectron = !!ei?.imageStore

/** Max video file size: 500MB */
export const MAX_VIDEO_SIZE = 500 * 1024 * 1024

/** Max image file size: 10MB (kept in sync with FloatingNote) */
export const MAX_IMAGE_SIZE = 10 * 1024 * 1024

/**
 * Save a media data URL to the assets directory (Electron) or return as-is.
 * Returns the display URL to use in <img>/<video> tags.
 * Handles both image/ and video/ MIME types.
 */
export async function saveMedia(dataUrl: string, filename?: string): Promise<string> {
  if (isElectron) {
    const result = await ei.imageStore.save({ dataUrl, name: filename || 'media.bin' })
    if (result.path) return result.path
  }
  return dataUrl
}

/** @deprecated Use saveMedia instead — kept for backward compatibility */
export const saveImage = saveMedia

/**
 * Resolve an image source URL to a displayable data URL.
 * Handles assets:// protocol — reads the file via IPC.
 * Note: After protocol registration, assets:// URLs work natively in <img>/<video>,
 *       so this is only needed for backward compatibility with existing notes.
 */
export async function resolveImageSrc(src: string): Promise<string> {
  if (!src.startsWith('assets://')) return src // data URL or other protocol
  if (!ei?.imageStore?.read) return src // No Electron API available (browser mode)
  const filename = src.replace('assets://', '')
  const dataUrl = await ei.imageStore.read(filename)
  return dataUrl || src
}
