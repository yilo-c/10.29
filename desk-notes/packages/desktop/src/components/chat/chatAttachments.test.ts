// Task #9 chatAttachments 纯逻辑单测（与后端 attachments.py 守卫口径对齐）。
import { describe, it, expect } from 'vitest'
import {
  collectAttachments,
  toWireParam,
  isAcceptable,
  isTextLike,
  formatSize,
  MAX_ATTACHMENTS,
  MAX_IMAGES,
} from './chatAttachments'

function mkFile(name: string, type: string, content = 'hello'): File {
  return new File([content], name, { type })
}

function mkBigFile(name: string, type: string, approxRawBytes: number): File {
  const blob = new Blob([new Uint8Array(approxRawBytes)], { type })
  return new File([blob], name, { type })
}

describe('chatAttachments 类型判定', () => {
  it('text/* 与常见后缀判为文本，图片判可收，二进制拒', () => {
    expect(isTextLike('a.md', 'text/markdown')).toBe(true)
    expect(isTextLike('main.py', '')).toBe(true)
    expect(isTextLike('x.json', 'application/json')).toBe(true)
    expect(isAcceptable('cat.png', 'image/png')).toBe(true)
    expect(isAcceptable('book.pdf', 'application/pdf')).toBe(false)
    expect(isAcceptable('bin.exe', 'application/octet-stream')).toBe(false)
  })
})

describe('collectAttachments', async () => {
  it('文本文件 base64 无 dataURL 前缀', async () => {
    const { accepted, rejected } = await collectAttachments([mkFile('n.txt', 'text/plain', '你好')])
    expect(rejected).toEqual([])
    expect(accepted).toHaveLength(1)
    expect(accepted[0].name).toBe('n.txt')
    expect(accepted[0].isImage).toBe(false)
    expect(accepted[0].dataBase64).not.toMatch(/^data:/)
    // 契约 = base64 为 UTF-8 字节（后端 decode("utf-8") 消化）；atob 是 latin1，须转码
    const bytes = Uint8Array.from(atob(accepted[0].dataBase64), (c) => c.charCodeAt(0))
    expect(new TextDecoder().decode(bytes)).toBe('你好')
  })

  it('不支持的类型逐条给出原因不静默丢', async () => {
    const { accepted, rejected } = await collectAttachments([mkFile('a.pdf', 'application/pdf')])
    expect(accepted).toHaveLength(0)
    expect(rejected[0].reason).toContain('暂只支持')
  })

  it('图片数量超上限拒绝', async () => {
    const files = Array.from({ length: MAX_IMAGES + 2 }, (_, i) => mkFile(`i${i}.png`, 'image/png'))
    const { accepted, rejected } = await collectAttachments(files)
    expect(accepted).toHaveLength(MAX_IMAGES)
    expect(rejected.length).toBe(2)
    expect(rejected[0].reason).toContain('图片数量已达上限')
  })

  it('总数量超上限拒绝', async () => {
    const files = Array.from({ length: MAX_ATTACHMENTS + 2 }, (_, i) => mkFile(`t${i}.txt`, 'text/plain'))
    const { accepted, rejected } = await collectAttachments(files)
    expect(accepted).toHaveLength(MAX_ATTACHMENTS)
    expect(rejected[0].reason).toContain('数量已达上限')
  })

  it('过大文件（编码 >6MB）拒收', async () => {
    const big = mkBigFile('big.txt', 'text/plain', 5 * 1024 * 1024 + 1024 * 1024) // 5.01MB raw → ~6.7MB b64
    const { accepted, rejected } = await collectAttachments([big])
    expect(accepted).toHaveLength(0)
    expect(rejected[0].reason).toContain('文件过大')
  })

  it('已有附件计数叠加生效（追加时按 existing 起算）', async () => {
    const first = await collectAttachments(Array.from({ length: MAX_IMAGES }, (_, i) => mkFile(`f${i}.png`, 'image/png')))
    expect(first.accepted).toHaveLength(MAX_IMAGES)
    const second = await collectAttachments([mkFile('x.png', 'image/png')], first.accepted)
    expect(second.accepted).toHaveLength(0)
    expect(second.rejected[0].reason).toContain('图片数量已达上限')
  })
})

describe('toWireParam', () => {
  it('无附件 → undefined（省字段，不发空数组）', () => {
    expect(toWireParam([])).toBeUndefined()
  })
  it('映射为后端契约 {name, mime, data_base64}', () => {
    const wire = toWireParam([{ id: 'a', name: 'n.txt', mime: 'text/plain', sizeBytes: 5, dataBase64: 'aGVsbG8=', isImage: false }])
    expect(wire).toEqual([{ name: 'n.txt', mime: 'text/plain', data_base64: 'aGVsbG8=' }])
  })
})

describe('formatSize', () => {
  it('三档格式化', () => {
    expect(formatSize(900)).toBe('900B')
    expect(formatSize(2048)).toBe('2.0KB')
    expect(formatSize(3.5 * 1024 * 1024)).toBe('3.5MB')
  })
})
