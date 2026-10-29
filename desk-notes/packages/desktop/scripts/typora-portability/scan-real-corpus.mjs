#!/usr/bin/env node
/* U0 战役 · Typora 可移植性对账（真库扫描，只读）。
 * 扫描真实笔记库（siyi-notes/notes/*.md）：
 *  1. 图片引用四形态分类 + 断链统计（siyi-file / 相对 / 绝对 / 远程）
 *  2. Typora 特有标记分布（公式/Mermaid/[TOC]/高亮/脚注/front matter）
 *  3. HTML 标签携带情况（读即迁移风险面）
 * 输出：D:/h/3.25/typora-audit/corpus-report.json + 控制台摘要。绝不写笔记库。 */
import { readFileSync, readdirSync, statSync, existsSync, writeFileSync, mkdirSync } from 'node:fs'
import { join, dirname, resolve, basename } from 'node:path'
import { homedir } from 'node:os'

const NOTES_DIR = join(process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'), 'desk-notes', 'siyi-notes', 'notes')
const TRANSFER_ROOTS = [
  resolve('D:/h/3.25/思忆集/思忆集app/data/transfer_files'),
  resolve('D:/h/3.25/思忆集/思忆集app/data/data/transfer_files'),
]

function walkMd(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) out.push(...walkMd(p))
    else if (name.toLowerCase().endsWith('.md')) out.push(p)
  }
  return out
}

function walkAll(dir, acc = []) {
  if (!existsSync(dir)) return acc
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) walkAll(p, acc)
    else acc.push(p)
  }
  return acc
}

const transferIndex = new Map() // basename -> full path
for (const root of TRANSFER_ROOTS) for (const f of walkAll(root)) transferIndex.set(basename(f), f)

const notes = existsSync(NOTES_DIR) ? walkMd(NOTES_DIR) : []
const IMG_RE = /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g
const stats = {
  notesTotal: notes.length,
  notesWithImages: 0, notesWithTyporaMarkers: 0, notesWithHtmlTags: 0,
  imageRefs: { 'siyi-file': 0, relative: 0, absolute: 0, remote: 0, 'data-uri': 0, broken: 0 },
  brokenSamples: [],
  markers: { inlineMath: 0, blockMath: 0, mermaid: 0, toc: 0, highlight: 0, footnotes: 0, frontMatter: 0 },
  markerNotes: { inlineMath: 0, blockMath: 0, mermaid: 0, toc: 0, highlight: 0, footnotes: 0, frontMatter: 0 },
}
let htmlTagNotes = 0

for (const file of notes) {
  const raw = readFileSync(file, 'utf-8')
  const noteDir = dirname(file)
  let hasImg = false
  for (const m of raw.matchAll(IMG_RE)) {
    hasImg = true
    const src = m[2]
    let kind, exists = null
    if (src.startsWith('siyi-file://')) {
      kind = 'siyi-file'
      const p = src.slice('siyi-file://'.length).replace(/\//g, '\\')
      exists = existsSync(join(TRANSFER_ROOTS[0], p)) || existsSync(join(TRANSFER_ROOTS[1], p)) || transferIndex.has(basename(p))
    } else if (/^https?:\/\//.test(src)) kind = 'remote'
    else if (/^data:/.test(src)) kind = 'data-uri'
    else if (/^[A-Za-z]:[\\/]/.test(src) || src.startsWith('/')) {
      kind = 'absolute'
      exists = existsSync(src)
    } else {
      kind = 'relative'
      exists = existsSync(resolve(noteDir, src)) || existsSync(resolve(noteDir, 'assets', basename(src))) || existsSync(resolve(NOTES_DIR, 'assets', basename(src)))
    }
    stats.imageRefs[kind]++
    if (exists === false) {
      stats.imageRefs.broken++
      if (stats.brokenSamples.length < 12) stats.brokenSamples.push({ note: basename(file), src: src.slice(0, 100), kind })
    }
  }
  if (hasImg) stats.notesWithImages++

  // Typora 标记
  const mk = {
    inlineMath: /\$[^$\n]+\$/, blockMath: /\$\$[\s\S]+?\$\$/, mermaid: /```mermaid/,
    toc: /^\s*\[TOC\]\s*$/m, highlight: /==[^=\n]+==/, footnotes: /\[\^[^\]\s]+\]:?/,
  }
  let anyMarker = false
  for (const [k, re] of Object.entries(mk)) {
    if (re.test(raw)) { stats.markers[k]++; stats.markerNotes[k]++; anyMarker = true }
  }
  if (/^---\s*\n[\s\S]*?\n---\s*\n/.test(raw.slice(0, 400))) { stats.markers.frontMatter++; stats.markerNotes.frontMatter++; anyMarker = true }
  if (anyMarker) stats.notesWithTyporaMarkers++
  if (/<\/?(?:p|div|span|u|font|sub|sup|details|center|br|table)\b/i.test(raw)) htmlTagNotes++
}
stats.notesWithHtmlTags = htmlTagNotes

mkdirSync('D:/h/3.25/typora-audit', { recursive: true })
writeFileSync('D:/h/3.25/typora-audit/corpus-report.json', JSON.stringify(stats, null, 1), 'utf-8')
console.log('=== Typora 可移植性真库扫描 ===')
console.log(`笔记总数: ${stats.notesTotal}`)
console.log(`含图片笔记: ${stats.notesWithImages} | 含 Typora 标记: ${stats.notesWithTyporaMarkers} | 含 HTML 标签: ${stats.notesWithHtmlTags}`)
console.log('图片引用分布:', JSON.stringify(stats.imageRefs))
console.log('Typora 标记分布:', JSON.stringify(stats.markers))
if (stats.brokenSamples.length) console.log('断链示例:', JSON.stringify(stats.brokenSamples.slice(0, 6), null, 1))
