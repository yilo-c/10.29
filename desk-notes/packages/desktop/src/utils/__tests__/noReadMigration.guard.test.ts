import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * U0 治本守卫（2026-09-19）：「读即迁移」机件已整体移除（读改写用户资产的行为禁止复活）。
 *
 * 历史三连：①子串 looksLikeHtml 误判 Typora 文档 → turndown 整篇改写并持久化；
 * ②加固为结构判定 + 长度双保险；③contentFormat provenance 闸门 + 真库 0 篇 HTML
 * → 最终裁决 = 删除整机件（迁移期早已结束，原注释自证 @deprecated M5 删）。
 * 本测试锁死「删除」终态，防任何形式的回潮。
 */

const ROOT = resolve(__dirname, '../../..')
const read = (p: string) => readFileSync(resolve(ROOT, p), 'utf-8')

describe('读即迁移机件已删除（防复活守卫）', () => {
  it('markdownConvert.ts 不再存在', () => {
    let exists = true
    try {
      read('src/utils/markdownConvert.ts')
    } catch {
      exists = false
    }
    expect(exists, 'utils/markdownConvert.ts 应已删除').toBe(false)
  })

  it('编辑器与浮窗无任何迁移调用残留', () => {
    for (const f of ['src/components/Common/NoteEditor.tsx', 'src/components/FloatingNote/FloatingNote.tsx']) {
      const src = read(f)
      for (const banned of ['migrateLegacyHtmlToMarkdown', 'looksLikeHtml', 'htmlToMarkdown', "from '../../utils/markdownConvert'"]) {
        expect(src.includes(banned), `${f} 不应再引用 ${banned}`).toBe(false)
      }
    }
  })

  it('contentFormat 字段已标注废弃（provenance 不再驱动迁移）', () => {
    const types = read('../shared/src/types/index.ts')
    expect(types).toMatch(/contentFormat/)
    expect(types).toMatch(/deprecated|废弃/)
  })
})
