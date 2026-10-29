import { useEffect } from 'react'
import SiyiNative, { withNativeTimeout } from '../lib/native/siyiNative'

/**
 * 系统栏避让 hook / Safe-area insets bridge
 *
 * 问题背景（Redmi 三键导航实测重叠）：
 *   targetSdk 36 起 Android 强制 edge-to-edge，WebView 内容延伸到系统导航栏下方。
 *   Capacitor 的 WebView 默认不向 CSS 分发 inset，导致 `env(safe-area-inset-bottom)`
 *   在三键导航模式下为 0 —— App 自己的底部 Tab 栏画进系统导航键区域，
 *   系统方块键盖住中间 AI 按钮，触控热区也冲突。
 *
 * 与原生层的分工（避免重复留白）：
 *   MainActivity 已给 WebView 容器加系统栏 padding，此时 WebView 的可用高度
 *   本身就不含系统栏，CSS **不该**再叠加 --safe-bottom（否则双倍空白）。
 *   插件 getInsets 返回 handledByNative 标志，本 hook 据此决定是否写入变量。
 *
 *   只有在原生避让未生效（老系统 / 插件异常 / 浏览器预览）时，
 *   才用 WindowInsets 或 CSS env() 兜底。
 */
export function useSafeAreaInsets(): void {
  useEffect(() => {
    let cancelled = false

    const setVars = (topCss: number, bottomCss: number, source: string) => {
      const root = document.documentElement
      root.style.setProperty('--safe-top', `${Math.round(topCss)}px`)
      root.style.setProperty('--safe-bottom', `${Math.round(bottomCss)}px`)
      root.dataset.safeAreaSource = source
    }

    /** 用 CSS env() 探测，部分 ROM 在手势导航下有效 */
    const readCssEnv = (): { top: number; bottom: number } => {
      const probe = document.createElement('div')
      probe.style.cssText = [
        'position:fixed', 'top:0', 'left:0', 'width:0', 'height:0',
        'visibility:hidden', 'pointer-events:none',
        'padding-top:env(safe-area-inset-top, 0px)',
        'padding-bottom:env(safe-area-inset-bottom, 0px)',
      ].join(';')
      document.body.appendChild(probe)
      const cs = getComputedStyle(probe)
      const top = parseFloat(cs.paddingTop) || 0
      const bottom = parseFloat(cs.paddingBottom) || 0
      document.body.removeChild(probe)
      return { top, bottom }
    }

    const sync = async () => {
      try {
        const r = await withNativeTimeout(SiyiNative.getInsets(), 3000, 'getInsets')
        if (cancelled) return

        // 原生已经用 padding 避让过 → CSS 一律置 0，否则双倍留白
        if (r && r.handledByNative === true) {
          setVars(0, 0, 'native-padding')
          return
        }

        const density = r?.density && r.density > 0 ? r.density : (window.devicePixelRatio || 1)
        const nativeTop = (r?.top || 0) / density
        const nativeBottom = (r?.bottom || 0) / density

        if (nativeBottom > 0 || nativeTop > 0) {
          const env = readCssEnv()
          setVars(Math.max(nativeTop, env.top), Math.max(nativeBottom, env.bottom), 'window-insets')
          return
        }

        // 原生值也为 0 → 用 CSS env()
        const env = readCssEnv()
        if (env.top > 0 || env.bottom > 0) {
          setVars(env.top, env.bottom, 'css-env')
          return
        }

        // 两路都拿不到：若在原生壳内且看起来是三键导航，给保守估算值
        if (isNativeShell()) {
          setVars(0, estimateBottomInset(), 'estimate')
        } else {
          setVars(0, 0, 'none')
        }
      } catch {
        // 插件不可用（浏览器预览 / 未注册）→ 只用 CSS env()
        if (cancelled) return
        const env = readCssEnv()
        setVars(env.top, env.bottom, 'css-env')
      }
    }

    void sync()

    const onResize = () => { void sync() }
    const onVisible = () => { if (!document.hidden) void sync() }
    window.addEventListener('resize', onResize)
    window.addEventListener('orientationchange', onResize)
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      cancelled = true
      window.removeEventListener('resize', onResize)
      window.removeEventListener('orientationchange', onResize)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])
}

/** 是否运行在 Capacitor 原生壳内（而非浏览器预览） */
function isNativeShell(): boolean {
  const w = window as unknown as { Capacitor?: { isNativePlatform?: () => boolean } }
  try {
    return w.Capacitor?.isNativePlatform?.() === true || window.location.protocol === 'capacitor:'
  } catch {
    return false
  }
}

/**
 * 三键导航栏高度的保守估算。
 * 48dp 是 Android 导航栏的标称高度；换算成 CSS px 需要设备像素比。
 * 仅在原生和 CSS 都拿不到值时使用，宁可多留一点也不要被遮挡。
 */
function estimateBottomInset(): number {
  const dpr = window.devicePixelRatio || 1
  // 常见三键导航栏 48dp；CSS px 与 dp 在当前 WebView 配置下近似 1:1
  const guess = 48
  // 若屏幕很矮（横屏）则减半，避免占掉过多空间
  const shortSide = Math.min(window.screen.width, window.screen.height)
  return shortSide < 400 ? Math.round(guess / 2) : Math.min(guess, Math.round(64 / Math.max(dpr / 3, 1)))
}

export default useSafeAreaInsets
