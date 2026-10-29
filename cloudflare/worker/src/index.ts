/**
 * 思忆集 Cloudflare Worker — 路由入口
 * 二期：同步 (D1)
 * 三期：文件存储 (R2)
 */

import { handleSyncRequest } from './sync'

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)
    // H3 修复：收窄 CORS — 仅允许思忆集客户端来源
    const allowedOrigins = (env.ALLOWED_ORIGINS || '').split(',').map(s => s.trim())
    const requestOrigin = request.headers.get('Origin') || ''
    const corsOrigin = allowedOrigins.includes(requestOrigin) ? requestOrigin : ''
    const corsHeaders = {
      'Access-Control-Allow-Origin': corsOrigin,
      'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      'Vary': 'Origin',
    }

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders })
    }

    try {
      // Phase 2: Sync endpoints
      if (url.pathname.startsWith('/api/sync/')) {
        return await handleSyncRequest(request, env, corsHeaders)
      }

      // Phase 3: Storage endpoints (future)
      // if (url.pathname.startsWith('/api/storage/')) {
      //   return await handleStorageRequest(request, env, corsHeaders)
      // }

      return new Response(JSON.stringify({ error: 'not found' }), {
        status: 404,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'internal error'
      return new Response(JSON.stringify({ error: msg }), {
        status: 500,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      })
    }
  },
}

export interface Env {
  SYNC_DB: D1Database
  // SIYI_FILES: R2Bucket  // Phase 3
  ALLOWED_ORIGINS: string  // H3: 逗号分隔的允许来源列表
  REGISTER_SECRET: string  // H1: 设备注册预共享密钥
  MIN_CLIENT_VERSION: string  // V3-05：同步最低兼容客户端版本
}
