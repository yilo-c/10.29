import { describe, it, expect, afterEach } from 'vitest'
import fs from 'fs'
import path from 'path'

/**
 * AG-C2①：client-version.cjs 单测。
 *
 * backend-relay.cjs 经此模块在中继连接上声明 client_version——
 * 后端 M2.1 起未声明的连接判 legacy_client，R4 方法直接 -32010。
 * 覆盖：FALLBACK 兜底常量、package.json 版本读取路径、优先级链、query/帧注入行为。
 */
const {
  FALLBACK_CLIENT_VERSION,
  resolveClientVersion,
  withClientVersionQuery,
  withClientVersionFrame,
} = await import('../electron/client-version.cjs')

afterEach(() => {
  delete (globalThis as { __SIYI_CLIENT_VERSION__?: string }).__SIYI_CLIENT_VERSION__
})

describe('FALLBACK_CLIENT_VERSION 兜底常量', () => {
  it('与 shared 渲染侧真源同值（1.2.0）', () => {
    expect(FALLBACK_CLIENT_VERSION).toBe('1.2.0')
  })

  it('与 packages/desktop/package.json version 同值（两侧同源不漂移）', () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '../package.json'), 'utf-8'))
    expect(pkg.version).toBe(FALLBACK_CLIENT_VERSION)
  })
})

describe('resolveClientVersion 优先级链', () => {
  it('显式 override 优先，且 trim 空白', () => {
    expect(resolveClientVersion('  9.9.9-rc1 ')).toBe('9.9.9-rc1')
  })

  it('override 为空白串时跳过，回落运行时全局注入', () => {
    ;(globalThis as { __SIYI_CLIENT_VERSION__?: string }).__SIYI_CLIENT_VERSION__ = '  2.0.0-global  '
    expect(resolveClientVersion('   ')).toBe('2.0.0-global')
  })

  it('无 override / 无全局 → 读 package.json 版本（非空、SemVer 形状）', () => {
    const v = resolveClientVersion()
    expect(v).toBe('1.2.0')
    expect(v).toMatch(/^\d+\.\d+\.\d+/)
  })

  it('永不返回空串（后端只做非空即新客户端的最小识别）', () => {
    expect(resolveClientVersion('').length).toBeGreaterThan(0)
    expect(resolveClientVersion(undefined).length).toBeGreaterThan(0)
  })
})

describe('withClientVersionQuery 握手注入', () => {
  it('ws:// URL 追加 client_version query', () => {
    const url = withClientVersionQuery('ws://127.0.0.1:18765/ws', '1.2.0')
    expect(url).toBe('ws://127.0.0.1:18765/ws?client_version=1.2.0')
  })

  it('已有 query 时用 & 追加，不破坏原参数', () => {
    const url = withClientVersionQuery('ws://h/ws?token=abc', '1.2.0')
    expect(url).toContain('token=abc')
    expect(url).toContain('client_version=1.2.0')
    expect(url).toMatch(/token=abc&client_version=1\.2\.0/)
  })

  it('非法 URL 退回手工拼接，声明不丢', () => {
    expect(withClientVersionQuery('not a url', '1.2.0')).toBe('not a url?client_version=1.2.0')
  })

  it('query 值经 URL 编码（空格 → +）', () => {
    expect(withClientVersionQuery('ws://h/ws', '1.0.0 beta')).toBe('ws://h/ws?client_version=1.0.0+beta')
  })

  it('省略 version → 自动解析（非空）', () => {
    expect(withClientVersionQuery('ws://h/ws').length).toBeGreaterThan('ws://h/ws'.length)
  })
})

describe('withClientVersionFrame 帧级声明', () => {
  it('保留原有 JSON-RPC 字段并追加 client_version', () => {
    const frame = withClientVersionFrame({ jsonrpc: '2.0', id: 7, method: 'm', params: {} }, '1.2.0')
    expect(frame).toEqual({ jsonrpc: '2.0', id: 7, method: 'm', params: {}, client_version: '1.2.0' })
  })

  it('不修改原帧对象（返回副本）', () => {
    const original = { jsonrpc: '2.0', id: 1 }
    const frame = withClientVersionFrame(original, '1.2.0')
    expect(original).not.toHaveProperty('client_version')
    expect(frame).toHaveProperty('client_version')
  })

  it('省略 version → 自动解析非空声明', () => {
    const frame = withClientVersionFrame({ jsonrpc: '2.0', id: 1 })
    expect(typeof frame.client_version).toBe('string')
    expect((frame.client_version as string).length).toBeGreaterThan(0)
  })
})
