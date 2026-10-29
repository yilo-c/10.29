/**
 * A1②（两入口内核词汇收敛）契约测试：
 * 主窗 ChatView/ChatComposer 与独立窗 StandaloneChatView→ChatModule 不得再自造
 * params.kernel 词汇，必须引用 shared 单一来源
 * `@desk-notes/shared/utils/kernelVocabulary`（事实源：docs/kernel-contract.md §2.5.1）。
 *
 * 采用与 `Common/__tests__/i18n-batch1.test.ts`、`utils/__tests__/aiConfig.test.ts`
 * 相同的「源码扫描」范式：读取源文件、剥离注释行后按禁用模式匹配。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  CHAT_KERNEL_SELECTOR_OPTIONS,
  KERNEL_CLAUDE_CODE,
  KERNEL_CODEX,
  KERNEL_DSH,
  KERNEL_TOKENS,
  LEGACY_KERNEL_TOKENS,
} from '@desk-notes/shared/utils/kernelVocabulary'

const SHARED_IMPORT = /from\s+['"]@desk-notes\/shared\/utils\/kernelVocabulary['"]/

/** 非内核语义的合法 'auto'（CSS 高度自适应），不属于收敛范围 */
const ALLOW_PATTERNS: RegExp[] = [
  /\.style\.height\s*=\s*['"]auto['"]/,
]

const FORBIDDEN: Array<{ pattern: RegExp; why: string }> = [
  { pattern: /\bkernel\s*:\s*['"][^'"]+['"]/, why: 'kernel 发送值必须来自 buildKernelSendParam / KERNEL_* 常量' },
  { pattern: /\bkernel\s*(?:===|!==)\s*['"]/, why: 'kernel 比较必须使用 KERNEL_* 常量' },
  { pattern: /\bvalue\s*:\s*['"][^'"]+['"]\s*,\s*label/, why: '选择器选项集必须来自 CHAT_KERNEL_SELECTOR_OPTIONS' },
  { pattern: /['"]siyi-chat-kernel['"]/, why: '持久化键必须使用 KERNEL_STORAGE_KEY' },
]

/** 逐行剥离注释（与既有扫描测试一致的判定方式） */
function codeLines(src: string): string[] {
  return src
    .split('\n')
    .filter(line => {
      const s = line.trimStart()
      return !(s.startsWith('//') || s.startsWith('/*') || s.startsWith('*'))
    })
}

function readEntry(rel: string[]): string {
  return readFileSync(resolve(__dirname, ...rel), 'utf8')
}

function findViolations(file: string, src: string): string[] {
  const offenders: string[] = []
  codeLines(src).forEach((line, i) => {
    if (ALLOW_PATTERNS.some(p => p.test(line))) return
    for (const { pattern, why } of FORBIDDEN) {
      if (pattern.test(line)) offenders.push(`${file}:${i + 1} [${why}] ${line.trim()}`)
    }
    for (const token of [...KERNEL_TOKENS, ...LEGACY_KERNEL_TOKENS]) {
      const literal = new RegExp(`['"\`]${token}['"\`]`)
      if (literal.test(line)) offenders.push(`${file}:${i + 1} [裸 kernel 词汇] ${line.trim()}`)
    }
  })
  return offenders
}

const ENTRIES: Array<{ name: string; rel: string[]; mustImportShared: boolean }> = [
  { name: 'ChatView.tsx', rel: ['ChatView.tsx'], mustImportShared: true },
  { name: 'ChatComposer.tsx', rel: ['ChatComposer.tsx'], mustImportShared: true },
  { name: 'ChatModule.tsx', rel: ['..', 'Modules', 'ChatModule.tsx'], mustImportShared: true },
  { name: 'StandaloneChatView.tsx', rel: ['..', 'Modules', 'StandaloneChatView.tsx'], mustImportShared: false },
]

describe('A1② 入口内核词汇必须来自 shared 单一来源', () => {
  for (const entry of ENTRIES) {
    const src = readEntry(entry.rel)

    it(`${entry.name} 无自造 kernel 词汇/选项集`, () => {
      expect(findViolations(entry.name, src), '发现未收敛的 kernel 词汇').toEqual([])
    })

    if (entry.mustImportShared) {
      it(`${entry.name} 从 @desk-notes/shared/utils/kernelVocabulary 导入`, () => {
        expect(SHARED_IMPORT.test(src)).toBe(true)
      })
    }
  }
})

describe('A1② 两入口发送值构造方式', () => {
  const chatView = readEntry(['ChatView.tsx'])
  const chatModule = readEntry(['..', 'Modules', 'ChatModule.tsx'])
  const composer = readEntry(['ChatComposer.tsx'])

  it('主窗 selector 选项集直接等于共享选项集（未复制列表）', () => {
    expect(composer).toMatch(/COMPOSER_KERNEL_OPTIONS\s*=\s*CHAT_KERNEL_SELECTOR_OPTIONS/)
    expect(composer).not.toMatch(/ComposerKernelOption\s*=\s*['"]/)
  })

  it('主窗发送走 buildKernelSendParam（auto → 不发 kernel）', () => {
    expect(chatView).toMatch(/\.\.\.buildKernelSendParam\(\s*kernel\s*\)/)
  })

  it('独立窗深度模式走 buildKernelSendParam(KERNEL_DEEP)，仍发 deep', () => {
    expect(chatModule).toMatch(/buildKernelSendParam\(\s*KERNEL_DEEP\s*\)/)
  })

  it('主窗徽章沿用同一 kernel 变量（CT-13：所选 = 所发 = 徽章）', () => {
    expect(chatView).toMatch(/aiKernel=\{[^}]*\bkernel\b[^}]*\}/)
  })
})

describe('A1② selector 暴露真实内核名', () => {
  it('ChatComposer 渲染的选项值即 §2.5.1 per-request 内核名', () => {
    const values = CHAT_KERNEL_SELECTOR_OPTIONS.map(o => o.value)
    expect(values).toContain(KERNEL_DSH)
    expect(values).toContain(KERNEL_CLAUDE_CODE)
    expect(values).toContain(KERNEL_CODEX)
    expect(values).not.toContain('true')
  })
})

/** 独立窗入口是 ChatModule 的容器，内核词汇由 ChatModule 统一提供 */
describe('A1② 独立窗链路锚点仍在位', () => {
  const standalone = readEntry(['..', 'Modules', 'StandaloneChatView.tsx'])

  it('StandaloneChatView 仍复用 ChatModule', () => {
    expect(standalone).toMatch(/import\s+ChatModule\s+from\s+['"]\.\/ChatModule['"]/)
    expect(standalone).toMatch(/<ChatModule\s*\/>/)
  })
})
