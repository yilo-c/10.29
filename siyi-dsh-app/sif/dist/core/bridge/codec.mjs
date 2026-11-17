// SIF Bridge Codec — UTF-8 字节级 NDJSON 分帧
//
// 设计要点（对应规范 §5.1 / §5.2）：
//   1. 分帧按 UTF-8 字节计数，不按 JS UTF-16 length。中文一个字符 3 字节，
//      用 string.length 判断会让 1MiB 上限实际放大到约 3MiB，且截断会切碎多字节字符。
//   2. 半包、粘包、空行、CRLF 都必须正确处理。
//   3. 超限必须立刻拒绝并断开，不能等下一个 data 事件。
//   4. 多字节字符被 chunk 边界切开时，用 StringDecoder 保持跨块状态。
//   5. 写端必须处理 write() 返回 false 的背压。

import { StringDecoder } from 'node:string_decoder'

export const DEFAULT_MAX_FRAME_BYTES = 1024 * 1024      // 单帧上限 1 MiB
export const DEFAULT_MAX_BUFFER_BYTES = 8 * 1024 * 1024 // 单连接缓冲上限

export class FrameTooLargeError extends Error {
  constructor(bytes, limit) {
    super(`frame exceeds ${limit} bytes (saw ${bytes})`)
    this.name = 'FrameTooLargeError'
    this.code = 'FRAME_TOO_LARGE'
    this.bytes = bytes
    this.limit = limit
  }
}

export class CodecError extends Error {
  constructor(message, code = 'CODEC_ERROR') {
    super(message)
    this.name = 'CodecError'
    this.code = code
  }
}

/**
 * 增量解码器：把任意切分的字节流还原成一行行 JSON 文本。
 * push(chunk) 返回本轮完整帧的字符串数组（不含换行）。
 */
export class LineDecoder {
  #decoder = new StringDecoder('utf8')
  #buffer = ''
  #bufferedBytes = 0
  #maxFrameBytes
  #maxBufferBytes

  constructor({ maxFrameBytes = DEFAULT_MAX_FRAME_BYTES, maxBufferBytes = DEFAULT_MAX_BUFFER_BYTES } = {}) {
    this.#maxFrameBytes = maxFrameBytes
    this.#maxBufferBytes = maxBufferBytes
  }

  /** 当前未成帧的缓冲字节数。 */
  get pendingBytes() {
    return this.#bufferedBytes
  }

  /**
   * 喂入一块字节。返回完整帧（去掉行尾换行）。
   * 抛 FrameTooLargeError 表示必须断开连接。
   */
  push(chunk) {
    if (!(chunk instanceof Uint8Array)) throw new CodecError('push expects a Uint8Array', 'BAD_INPUT')
    const text = this.#decoder.write(Buffer.from(chunk))
    if (text.length === 0) return []
    this.#buffer += text
    this.#bufferedBytes += Buffer.byteLength(text, 'utf8')

    const frames = []
    let idx
    while ((idx = this.#buffer.indexOf('\n')) >= 0) {
      let line = this.#buffer.slice(0, idx)
      // 统计本帧真实字节数（含被消费掉的部分）
      const consumed = this.#buffer.slice(0, idx + 1)
      this.#bufferedBytes -= Buffer.byteLength(consumed, 'utf8')
      this.#buffer = this.#buffer.slice(idx + 1)

      if (line.endsWith('\r')) line = line.slice(0, -1)
      const bytes = Buffer.byteLength(line, 'utf8')
      if (bytes > this.#maxFrameBytes) throw new FrameTooLargeError(bytes, this.#maxFrameBytes)
      if (line.trim() === '') continue   // 空行不算帧
      frames.push(line)
    }

    // 没有换行但已超单帧上限 → 立刻失败，不等后续数据
    if (this.#bufferedBytes > this.#maxFrameBytes) {
      throw new FrameTooLargeError(this.#bufferedBytes, this.#maxFrameBytes)
    }
    if (this.#bufferedBytes > this.#maxBufferBytes) {
      throw new FrameTooLargeError(this.#bufferedBytes, this.#maxBufferBytes)
    }
    return frames
  }

  /** 流结束时取残留（无换行结尾的最后一帧）。 */
  end() {
    const rest = this.#buffer + this.#decoder.end()
    this.#buffer = ''
    this.#bufferedBytes = 0
    const line = rest.endsWith('\r') ? rest.slice(0, -1) : rest
    if (line.trim() === '') return []
    const bytes = Buffer.byteLength(line, 'utf8')
    if (bytes > this.#maxFrameBytes) throw new FrameTooLargeError(bytes, this.#maxFrameBytes)
    return [line]
  }
}

/** 编码一条消息为单行 UTF-8 字节（含结尾换行）。 */
export function encodeFrame(message, { maxFrameBytes = DEFAULT_MAX_FRAME_BYTES } = {}) {
  let text
  try {
    text = JSON.stringify(message)
  } catch (e) {
    throw new CodecError('message is not JSON-serializable: ' + e.message, 'NOT_SERIALIZABLE')
  }
  if (typeof text !== 'string') throw new CodecError('message serialized to undefined', 'NOT_SERIALIZABLE')
  const bytes = Buffer.byteLength(text, 'utf8')
  if (bytes > maxFrameBytes) throw new FrameTooLargeError(bytes, maxFrameBytes)
  return Buffer.from(text + '\n', 'utf8')
}

/** 解析一帧为对象；非法 JSON 抛 CodecError。 */
export function decodeFrame(line) {
  let value
  try {
    value = JSON.parse(line)
  } catch {
    throw new CodecError('frame is not valid JSON', 'BAD_JSON')
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new CodecError('frame must be a JSON object', 'BAD_SHAPE')
  }
  return value
}

/**
 * 背压写入：等待 drain。返回 Promise，写完 resolve。
 * 对应规范「write() 返回 false 时必须等待 drain，不无限堆积」。
 */
export function writeWithBackpressure(stream, buffer) {
  return new Promise((resolve, reject) => {
    let settled = false
    const done = (err) => {
      if (settled) return
      settled = true
      stream.off?.('error', onError)
      if (err) reject(err)
      else resolve()
    }
    const onError = (err) => done(err)
    stream.once?.('error', onError)
    const ok = stream.write(buffer, (err) => {
      if (err) done(err)
    })
    if (ok === false) {
      stream.once('drain', () => done())
    } else {
      // 已进入内核缓冲，下一 tick 视为完成
      queueMicrotask(() => done())
    }
  })
}
