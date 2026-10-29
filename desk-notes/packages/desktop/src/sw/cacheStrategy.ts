/**
 * Service Worker 缓存策略
 * 
 * 功能：
 * - 多种缓存策略（Cache First / Network First / Stale While Revalidate）
 * - 静态资源预缓存
 * - 动态请求缓存
 * - 离线回退
 * - 缓存清理与版本管理
 */

// 缓存版本 - 构建时注入，格式: {version}-{commitHash}
const CACHE_VERSION = '__CACHE_VERSION__'
const STATIC_CACHE = `static-${CACHE_VERSION}`
const DYNAMIC_CACHE = `dynamic-${CACHE_VERSION}`

// 缓存版本前缀（用于识别旧版本）
const CACHE_PREFIX = 'siyi-'

// 缓存策略
export type CacheStrategy = 
  | 'cache-first'      // 缓存优先
  | 'network-first'    // 网络优先
  | 'stale-while-revalidate'  // 过期时重新验证
  | 'network-only'     // 仅网络
  | 'cache-only'       // 仅缓存

// 缓存配置
export interface CacheConfig {
  /** 静态资源缓存策略 */
  staticStrategy: CacheStrategy
  /** API 请求缓存策略 */
  apiStrategy: CacheStrategy
  /** 图片缓存策略 */
  imageStrategy: CacheStrategy
  /** 预缓存 URL 列表 */
  precacheUrls: string[]
  /** 离线回退 URL */
  offlineFallback?: string
  /** 缓存过期时间（毫秒） */
  maxAge?: number
  /** 最大缓存条目数 */
  maxEntries?: number
}

// 默认配置
const DEFAULT_CONFIG: CacheConfig = {
  staticStrategy: 'cache-first',
  apiStrategy: 'network-first',
  imageStrategy: 'cache-first',
  precacheUrls: [
    '/',
    '/index.html',
    '/manifest.json',
  ],
  offlineFallback: '/offline.html',
  maxAge: 24 * 60 * 60 * 1000, // 24 小时
  maxEntries: 500,
}

// 缓存条目元数据
interface CacheEntry {
  timestamp: number
  url: string
}

/**
 * Service Worker 缓存管理器
 */
export class CacheManager {
  private config: CacheConfig

  constructor(config: Partial<CacheConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config }
  }

  /**
   * 安装时预缓存
   */
  async precache(): Promise<void> {
    const cache = await caches.open(STATIC_CACHE)
    const urls = this.config.precacheUrls
    
    console.log(`[SW] Precaching ${urls.length} URLs`)
    
    await cache.addAll(urls)
  }

  /**
   * 处理 fetch 事件
   */
  async handleFetch(event: { request: Request }): Promise<Response> {
    const request = event.request
    const url = new URL(request.url)

    // 跳过非 GET 请求
    if (request.method !== 'GET') {
      return fetch(request)
    }

    // 跳过跨域请求（除了 API）
    if (url.origin !== location.origin && !url.pathname.startsWith('/api/')) {
      return fetch(request)
    }

    // 根据请求类型选择策略
    if (this.isStaticAsset(url)) {
      return this.handleStaticAsset(request)
    }

    if (this.isApiRequest(url)) {
      return this.handleApiRequest(request)
    }

    if (this.isImageRequest(url)) {
      return this.handleImageRequest(request)
    }

    // 默认使用网络优先
    return this.networkFirst(request, DYNAMIC_CACHE)
  }

  /**
   * 判断是否为静态资源
   */
  private isStaticAsset(url: URL): boolean {
    return /\.(js|css|woff2?|ttf|eot)(\?.*)?$/.test(url.pathname)
  }

  /**
   * 判断是否为 API 请求
   */
  private isApiRequest(url: URL): boolean {
    return url.pathname.startsWith('/api/')
  }

  /**
   * 判断是否为图片请求
   */
  private isImageRequest(url: URL): boolean {
    return /\.(png|jpg|jpeg|gif|svg|webp|ico)(\?.*)?$/.test(url.pathname)
  }

  /**
   * 处理静态资源
   */
  private async handleStaticAsset(request: Request): Promise<Response> {
    switch (this.config.staticStrategy) {
      case 'cache-first':
        return this.cacheFirst(request, STATIC_CACHE)
      case 'network-first':
        return this.networkFirst(request, STATIC_CACHE)
      case 'stale-while-revalidate':
        return this.staleWhileRevalidate(request, STATIC_CACHE)
      default:
        return this.cacheFirst(request, STATIC_CACHE)
    }
  }

  /**
   * 处理 API 请求
   */
  private async handleApiRequest(request: Request): Promise<Response> {
    switch (this.config.apiStrategy) {
      case 'cache-first':
        return this.cacheFirst(request, DYNAMIC_CACHE)
      case 'network-first':
        return this.networkFirst(request, DYNAMIC_CACHE)
      case 'stale-while-revalidate':
        return this.staleWhileRevalidate(request, DYNAMIC_CACHE)
      default:
        return this.networkFirst(request, DYNAMIC_CACHE)
    }
  }

  /**
   * 处理图片请求
   */
  private async handleImageRequest(request: Request): Promise<Response> {
    switch (this.config.imageStrategy) {
      case 'cache-first':
        return this.cacheFirst(request, DYNAMIC_CACHE)
      case 'network-first':
        return this.networkFirst(request, DYNAMIC_CACHE)
      default:
        return this.cacheFirst(request, DYNAMIC_CACHE)
    }
  }

  /**
   * 缓存优先策略
   */
  private async cacheFirst(request: Request, cacheName: string): Promise<Response> {
    const cached = await this.getFromCache(request, cacheName)
    if (cached) {
      return cached
    }

    try {
      const response = await fetch(request)
      if (response.ok) {
        await this.putInCache(request, response.clone(), cacheName)
      }
      return response
    } catch (error) {
      return this.getOfflineFallback()
    }
  }

  /**
   * 网络优先策略
   */
  private async networkFirst(request: Request, cacheName: string): Promise<Response> {
    try {
      const response = await fetch(request)
      if (response.ok) {
        await this.putInCache(request, response.clone(), cacheName)
      }
      return response
    } catch (error) {
      const cached = await this.getFromCache(request, cacheName)
      if (cached) {
        return cached
      }
      return this.getOfflineFallback()
    }
  }

  /**
   * 过期时重新验证策略
   */
  private async staleWhileRevalidate(request: Request, cacheName: string): Promise<Response> {
    const cached = await this.getFromCache(request, cacheName)
    
    // 后台更新缓存
    const fetchPromise = fetch(request).then(async response => {
      if (response.ok) {
        await this.putInCache(request, response.clone(), cacheName)
      }
      return response
    }).catch(() => null)

    // 立即返回缓存（如果有）
    if (cached) {
      return cached
    }

    // 等待网络响应
    const result = await fetchPromise
    if (result) {
      return result
    }
    return this.getOfflineFallback()
  }

  /**
   * 从缓存获取
   */
  private async getFromCache(request: Request, cacheName: string): Promise<Response | null> {
    const cache = await caches.open(cacheName)
    const response = await cache.match(request)
    
    if (!response) {
      return null
    }

    // 检查是否过期
    if (this.config.maxAge) {
      const entry = await this.getCacheEntry(cacheName, request.url)
      if (entry && Date.now() - entry.timestamp > this.config.maxAge) {
        // 已过期，删除缓存
        await cache.delete(request)
        return null
      }
    }

    return response
  }

  /**
   * 存入缓存
   */
  private async putInCache(request: Request, response: Response, cacheName: string): Promise<void> {
    const cache = await caches.open(cacheName)
    
    // 存储响应
    await cache.put(request, response)
    
    // 存储元数据
    const entry: CacheEntry = {
      timestamp: Date.now(),
      url: request.url,
    }
    await this.setCacheEntry(cacheName, entry)
    
    // 清理旧缓存
    await this.cleanOldEntries(cacheName)
  }

  /**
   * 获取缓存条目元数据
   */
  private async getCacheEntry(cacheName: string, url: string): Promise<CacheEntry | null> {
    const metaCache = await caches.open(`${cacheName}-meta`)
    const response = await metaCache.match(url)
    if (response) {
      return response.json()
    }
    return null
  }

  /**
   * 设置缓存条目元数据
   */
  private async setCacheEntry(cacheName: string, entry: CacheEntry): Promise<void> {
    const metaCache = await caches.open(`${cacheName}-meta`)
    const response = new Response(JSON.stringify(entry), {
      headers: { 'Content-Type': 'application/json' },
    })
    await metaCache.put(entry.url, response)
  }

  /**
   * 清理旧缓存条目
   */
  private async cleanOldEntries(cacheName: string): Promise<void> {
    if (!this.config.maxEntries) return

    const metaCache = await caches.open(`${cacheName}-meta`)
    const keys = await metaCache.keys()
    
    if (keys.length <= this.config.maxEntries) return

    // 获取所有条目并按时间排序
    const entries: Array<{ url: string; timestamp: number }> = []
    for (const request of keys) {
      const response = await metaCache.match(request)
      if (response) {
        const entry = await response.json()
        entries.push({ url: request.url, timestamp: entry.timestamp })
      }
    }

    entries.sort((a, b) => a.timestamp - b.timestamp)

    // 删除最旧的条目
    const toDelete = entries.slice(0, entries.length - this.config.maxEntries)
    const cache = await caches.open(cacheName)
    
    for (const entry of toDelete) {
      await cache.delete(entry.url)
      await metaCache.delete(entry.url)
    }
  }

  /**
   * 获取离线回退
   */
  private async getOfflineFallback(): Promise<Response> {
    if (this.config.offlineFallback) {
      const cached = await caches.match(this.config.offlineFallback)
      if (cached) {
        return cached
      }
    }

    return new Response('Offline', {
      status: 503,
      statusText: 'Service Unavailable',
      headers: { 'Content-Type': 'text/plain' },
    })
  }

  /**
   * 清理所有缓存
   */
  async clearAll(): Promise<void> {
    const keys = await caches.keys()
    await Promise.all(keys.map(key => caches.delete(key)))
  }

  /**
   * 清理旧版本缓存（增强版）
   * - 清理非当前版本的缓存
   * - 清理无版本号的遗留缓存
   * - 返回清理的缓存数量
   */
  async cleanupOldVersions(): Promise<{ deleted: number; kept: number }> {
    const keys = await caches.keys()
    const currentCaches = new Set([
      STATIC_CACHE, 
      DYNAMIC_CACHE, 
      `${STATIC_CACHE}-meta`, 
      `${DYNAMIC_CACHE}-meta`,
    ])
    
    let deleted = 0
    let kept = 0
    
    for (const key of keys) {
      // 保留当前版本的缓存
      if (currentCaches.has(key)) {
        kept++
        continue
      }
      
      // 删除属于本应用的旧版本缓存
      if (key.startsWith(CACHE_PREFIX) || 
          key.startsWith('static-') || 
          key.startsWith('dynamic-')) {
        await caches.delete(key)
        deleted++
        console.log(`[SW] Deleted old cache: ${key}`)
      } else {
        // 不属于本应用的缓存，保持不动
        kept++
      }
    }
    
    console.log(`[SW] Cache cleanup: deleted=${deleted}, kept=${kept}`)
    return { deleted, kept }
  }

  /**
   * 强制刷新缓存（用于版本更新）
   */
  async forceRefresh(): Promise<void> {
    console.log('[SW] Force refreshing all caches')
    
    // 清理所有旧缓存
    await this.cleanupOldVersions()
    
    // 重新预缓存
    await this.precache()
  }

  /**
   * 获取当前缓存版本
   */
  getVersion(): string {
    return CACHE_VERSION
  }

  /**
   * 获取缓存统计
   */
  async getStats(): Promise<{
    staticCacheSize: number
    dynamicCacheSize: number
    totalEntries: number
  }> {
    const staticCache = await caches.open(STATIC_CACHE)
    const dynamicCache = await caches.open(DYNAMIC_CACHE)
    
    const staticKeys = await staticCache.keys()
    const dynamicKeys = await dynamicCache.keys()

    return {
      staticCacheSize: staticKeys.length,
      dynamicCacheSize: dynamicKeys.length,
      totalEntries: staticKeys.length + dynamicKeys.length,
    }
  }
}

/**
 * 注册 Service Worker
 */
export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) {
    console.warn('Service Worker not supported')
    return null
  }

  try {
    const registration = await navigator.serviceWorker.register('/sw.js', {
      scope: '/',
    })

    console.log('Service Worker registered:', registration.scope)

    // 监听更新
    registration.addEventListener('updatefound', () => {
      const newWorker = registration.installing
      if (newWorker) {
        newWorker.addEventListener('statechange', () => {
          if (newWorker.state === 'activated') {
            console.log('New Service Worker activated')
            // 可以在这里提示用户刷新页面
          }
        })
      }
    })

    return registration
  } catch (error) {
    console.error('Service Worker registration failed:', error)
    return null
  }
}

/**
 * 注销 Service Worker
 */
export async function unregisterServiceWorker(): Promise<boolean> {
  if (!('serviceWorker' in navigator)) {
    return false
  }

  const registrations = await navigator.serviceWorker.getRegistrations()
  const results = await Promise.all(registrations.map(reg => reg.unregister()))
  return results.every(r => r)
}

// 导出默认实例
export const cacheManager = new CacheManager()