import { describe, it, expect } from 'vitest'
import path from 'path'
import os from 'os'

/**
 * V2.1 批次4 Electron 客户端安全清债验收测试。
 *
 * 覆盖：
 * - b4-e3：path-guard.cjs guardPath 用 path.relative 判越界（杜绝前缀碰撞）
 * - b4-e2：url-guard.cjs window-open/will-navigate URL 白名单（拦 file:）
 * - b4-e4：relay-backoff.cjs 重连指数退避纯函数
 *
 * 纯函数均已从 electron 主进程 CJS 模块提取为无 electron 依赖的独立模块
 * （electron 包在纯 Node 下 require 返回可执行文件路径字符串，无法直接加载）。
 */

const guard = await import('../electron/url-guard.cjs')
const { guardPath } = await import('../electron/path-guard.cjs')
const { computeBackoffDelayMs, shouldAttemptConnect } = await import('../electron/relay-backoff.cjs')

describe('b4-e3: guardPath 用 path.relative 判越界（file-store.cjs 依赖）', () => {
  const allowed = path.join(os.tmpdir(), 'siyi-guard-allowed')

  it('放行目录内合法相对路径', () => {
    const resolved = guardPath(allowed, 'notes/abc.md')
    expect(resolved.startsWith(path.resolve(allowed))).toBe(true)
  })

  it('放行单层子路径（不回退）', () => {
    expect(guardPath(allowed, 'notes')).toBe(path.resolve(allowed, 'notes'))
  })

  it('拒绝 .. 目录穿越', () => {
    expect(() => guardPath(allowed, '../evil.txt')).toThrow(/Path traversal denied/)
  })

  it('拒绝深层 .. 穿越', () => {
    expect(() => guardPath(allowed, 'a/../../evil.txt')).toThrow(/Path traversal denied/)
  })

  it('拒绝绝对路径', () => {
    expect(() => guardPath(allowed, 'C:\\Windows\\system32\\evil.exe')).toThrow(/Path traversal denied/)
  })

  it('拒绝 sibling 前缀碰撞（旧 startsWith 误放行场景）', () => {
    // allowed=/tmp/siyi-guard-allowed 时，siyi-guard-allowed-evil 是兄弟目录，
    // path.relative 会得到 ../… → 必须拒绝（旧 resolved.startsWith 会误放行）
    expect(() => guardPath(allowed, `..${path.sep}siyi-guard-allowed-evil\\x.txt`)).toThrow(/Path traversal denied/)
  })

  it('拒绝带 .. 的规范化穿越', () => {
    expect(() => guardPath(allowed, 'notes/..%2f..%2fsecret')).toThrow(/Path traversal denied/)
  })

  it('拒绝 %5c 编码的 Windows 分隔符穿越', () => {
    expect(() => guardPath(allowed, '..%5c..%5csecret')).toThrow(/Path traversal denied/)
  })

  it('拒绝双编码 %252f 穿越（防二次解码绕过）', () => {
    expect(() => guardPath(allowed, 'notes/..%252f..%252fsecret')).toThrow(/Path traversal denied/)
  })
})

describe('b4-e2: url-guard URL 白名单（window-open / will-navigate）', () => {
  describe('isSafeExternalUrl —— 仅 http/https 交系统浏览器', () => {
    it('放行 https 外链', () => {
      expect(guard.isSafeExternalUrl('https://example.com/a')).toBe('https://example.com/a')
    })
    it('放行 http 外链', () => {
      expect(guard.isSafeExternalUrl('http://example.com/a')).toBe('http://example.com/a')
    })
    it('拒绝 file: 协议（本地文件）', () => {
      expect(guard.isSafeExternalUrl('file:///C:/Windows/system32/evil.exe')).toBe('')
    })
    it('拒绝 javascript: 协议', () => {
      expect(guard.isSafeExternalUrl('javascript:alert(1)')).toBe('')
    })
    it('拒绝 data: 协议', () => {
      expect(guard.isSafeExternalUrl('data:text/html,<script>alert(1)</script>')).toBe('')
    })
    it('拒绝 ftp / gopher / 其他未知协议', () => {
      expect(guard.isSafeExternalUrl('ftp://example.com/x')).toBe('')
      expect(guard.isSafeExternalUrl('gopher://example.com/x')).toBe('')
    })
    it('拒绝畸形 URL', () => {
      expect(guard.isSafeExternalUrl('not a url')).toBe('')
    })
  })

  describe('isAllowedLocalNavigation —— 仅本机后端导航', () => {
    it('放行 http://127.0.0.1:18765', () => {
      expect(guard.isAllowedLocalNavigation('http://127.0.0.1:18765/')).toBe(true)
    })
    it('放行 http://localhost:5190（开发服务器）', () => {
      expect(guard.isAllowedLocalNavigation('http://localhost:5190/')).toBe(true)
    })
    it('放行 https://localhost', () => {
      expect(guard.isAllowedLocalNavigation('https://localhost/')).toBe(true)
    })
    it('拒绝 hostname 前缀绕过 http://127.0.0.1.evil.com', () => {
      expect(guard.isAllowedLocalNavigation('http://127.0.0.1.evil.com/')).toBe(false)
    })
    it('拒绝 http://localhost.evil.com', () => {
      expect(guard.isAllowedLocalNavigation('http://localhost.evil.com/')).toBe(false)
    })
    it('拒绝 http://127.0.0.1@evil.com（用户信息伪装）', () => {
      expect(guard.isAllowedLocalNavigation('http://127.0.0.1@evil.com/')).toBe(false)
    })
    it('拒绝 file: 本地导航', () => {
      expect(guard.isAllowedLocalNavigation('file:///C:/x.html')).toBe(false)
    })
    it('拒绝公网域名', () => {
      expect(guard.isAllowedLocalNavigation('http://example.com/')).toBe(false)
    })
    it('拒绝畸形 URL', () => {
      expect(guard.isAllowedLocalNavigation('not a url')).toBe(false)
    })
  })
})

describe('b4-e4: backend-relay 重连指数退避', () => {
  it('0 次失败 → 退避 0（立即重试）', () => {
    expect(computeBackoffDelayMs(0)).toBe(0)
  })

  it('负值/非法输入 → 退避 0', () => {
    expect(computeBackoffDelayMs(-1)).toBe(0)
    expect(computeBackoffDelayMs(Number.NaN)).toBe(0)
  })

  it('指数退避：1 次=1s, 2 次=2s, 3 次=4s, 4 次=8s, 5 次=16s→封顶 15s', () => {
    expect(computeBackoffDelayMs(1)).toBe(1000)
    expect(computeBackoffDelayMs(2)).toBe(2000)
    expect(computeBackoffDelayMs(3)).toBe(4000)
    expect(computeBackoffDelayMs(4)).toBe(8000)
    expect(computeBackoffDelayMs(5)).toBe(15000)
  })

  it('封顶后不再增长（防连接风暴）', () => {
    expect(computeBackoffDelayMs(10)).toBe(15000)
    expect(computeBackoffDelayMs(20)).toBe(15000)
  })

  it('shouldAttemptConnect：无失败立即放行', () => {
    expect(shouldAttemptConnect(Date.now(), Date.now(), 0)).toBe(true)
  })

  it('shouldAttemptConnect：退避期内拒绝，期满放行', () => {
    const last = 0
    const failures = 2  // 退避 2s
    expect(shouldAttemptConnect(last + 1000, last, failures)).toBe(false)
    expect(shouldAttemptConnect(last + 1999, last, failures)).toBe(false)
    expect(shouldAttemptConnect(last + 2000, last, failures)).toBe(true)
    expect(shouldAttemptConnect(last + 5000, last, failures)).toBe(true)
  })
})
