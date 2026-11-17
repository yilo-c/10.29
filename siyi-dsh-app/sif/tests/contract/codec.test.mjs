// T01 协议 golden tests：UTF-8 字节级分帧
// 覆盖：半包、粘包、多字节切分、空行、CRLF、非法 JSON、超限、背压
import { LineDecoder, encodeFrame, decodeFrame, FrameTooLargeError, CodecError } from '../../core/bridge/codec.mjs'

let pass = 0, fail = 0
const results = []
function t(name, fn) {
  try { fn(); pass++; results.push('PASS ' + name) }
  catch (e) { fail++; results.push('FAIL ' + name + ' :: ' + (e && e.message)) }
}
function eq(actual, expected, label) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected)
  if (a !== b) throw new Error(`${label}: expected ${b}, got ${a}`)
}
function throws(fn, code, label) {
  try { fn() } catch (e) {
    if (code && e.code !== code) throw new Error(`${label}: expected code ${code}, got ${e.code}`)
    return e
  }
  throw new Error(`${label}: expected throw, got none`)
}

// ── 1. 基础成帧 ────────────────────────────────────────────
t('单帧解码', () => {
  const d = new LineDecoder()
  eq(d.push(encodeFrame({ a: 1 })), ['{"a":1}'], 'frames')
})

t('粘包：两条消息一包', () => {
  const d = new LineDecoder()
  const buf = Buffer.concat([encodeFrame({ a: 1 }), encodeFrame({ b: 2 })])
  eq(d.push(buf), ['{"a":1}', '{"b":2}'], 'frames')
})

t('半包：一条消息分两块', () => {
  const d = new LineDecoder()
  const buf = encodeFrame({ hello: 'world' })
  const cut = Math.floor(buf.length / 2)
  eq(d.push(buf.subarray(0, cut)), [], 'first half')
  eq(d.push(buf.subarray(cut)), ['{"hello":"world"}'], 'second half')
})

// ── 2. 多字节字符边界 ──────────────────────────────────────
t('中文被 chunk 从字节中间切开', () => {
  const d = new LineDecoder()
  const buf = encodeFrame({ t: '思忆集笔记' })
  // 找到「思」的 UTF-8 三字节中间位置切开
  const idx = buf.indexOf(Buffer.from('思', 'utf8'))
  const cut = idx + 1   // 切在 3 字节字符的中间
  eq(d.push(buf.subarray(0, cut)), [], 'partial')
  eq(d.push(buf.subarray(cut)), ['{"t":"思忆集笔记"}'], 'rest')
})

t('中文多帧顺序不乱', () => {
  const d = new LineDecoder()
  const buf = Buffer.concat([encodeFrame({ n: '一' }), encodeFrame({ n: '二' })])
  const frames = d.push(buf)
  eq(frames.map(f => JSON.parse(f).n), ['一', '二'], 'order')
})

// ── 3. 空行与 CRLF ────────────────────────────────────────
t('空行被忽略', () => {
  const d = new LineDecoder()
  eq(d.push(Buffer.from('\n\n{"a":1}\n\n', 'utf8')), ['{"a":1}'], 'frames')
})

t('CRLF 行尾被剥离', () => {
  const d = new LineDecoder()
  eq(d.push(Buffer.from('{"a":1}\r\n', 'utf8')), ['{"a":1}'], 'frames')
})

// ── 4. 超限 ───────────────────────────────────────────────
t('无换行且超单帧上限立刻失败', () => {
  const d = new LineDecoder({ maxFrameBytes: 16 })
  throws(() => d.push(Buffer.from('x'.repeat(32), 'utf8')), 'FRAME_TOO_LARGE', 'oversize')
})

t('超限按 UTF-8 字节而非字符数计算', () => {
  // 8 个汉字 = 24 字节，字符数只有 8
  const d = new LineDecoder({ maxFrameBytes: 20 })
  throws(() => d.push(Buffer.from('思'.repeat(8), 'utf8')), 'FRAME_TOO_LARGE', 'byte-count')
})

t('恰好等于上限通过', () => {
  const payload = 'a'.repeat(10)
  const line = JSON.stringify({ s: payload })  // 长度固定
  const bytes = Buffer.byteLength(line, 'utf8')
  const d = new LineDecoder({ maxFrameBytes: bytes })
  eq(d.push(Buffer.from(line + '\n', 'utf8')).length, 1, 'at-limit')
})

// ── 5. 编码侧限制 ─────────────────────────────────────────
t('encodeFrame 拒绝超限消息', () => {
  throws(() => encodeFrame({ s: 'x'.repeat(100) }, { maxFrameBytes: 32 }), 'FRAME_TOO_LARGE', 'encode-limit')
})

t('encodeFrame 拒绝不可序列化值', () => {
  const circular = {}; circular.self = circular
  throws(() => encodeFrame(circular), 'NOT_SERIALIZABLE', 'circular')
})

// ── 6. 解码校验 ───────────────────────────────────────────
t('非法 JSON 报 BAD_JSON', () => {
  throws(() => decodeFrame('{not json}'), 'BAD_JSON', 'bad-json')
})

t('数组不是合法帧', () => {
  throws(() => decodeFrame('[1,2]'), 'BAD_SHAPE', 'array')
})

t('null 不是合法帧', () => {
  throws(() => decodeFrame('null'), 'BAD_SHAPE', 'null')
})

// ── 7. 流结束残留 ─────────────────────────────────────────
t('无换行结尾的最后一帧在 end() 取出', () => {
  const d = new LineDecoder()
  d.push(Buffer.from('{"a":1}', 'utf8'))
  eq(d.end(), ['{"a":1}'], 'tail')
})

// ── 8. 顺序与关联 ─────────────────────────────────────────
t('大量帧保持顺序', () => {
  const d = new LineDecoder()
  const bufs = []
  for (let i = 0; i < 200; i++) bufs.push(encodeFrame({ i }))
  const frames = d.push(Buffer.concat(bufs))
  eq(frames.length, 200, 'count')
  eq(frames.map(f => JSON.parse(f).i).slice(0, 3), [0, 1, 2], 'head')
  eq(frames.map(f => JSON.parse(f).i).slice(-1), [199], 'tail')
})

// ── 输出 ──────────────────────────────────────────────────
console.log(results.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
if (fail > 0) process.exit(1)
