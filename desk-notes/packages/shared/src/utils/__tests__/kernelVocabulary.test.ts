/**
 * A1②：params.kernel 统一词汇共享模块测试。
 * 断言基准来自 docs/kernel-contract.md §2.5.1（v1.2.1，唯一事实源）。
 */
import { describe, it, expect } from 'vitest'
import {
  CHAT_KERNEL_SELECTOR_OPTIONS,
  DEFAULT_KERNEL_SELECTION,
  DSH_PROFILE_KERNELS,
  KERNEL_AUTO,
  KERNEL_CLAUDE_CODE,
  KERNEL_CODEX,
  KERNEL_DEEP,
  KERNEL_DSH,
  KERNEL_FAST,
  KERNEL_STORAGE_KEY,
  KERNEL_TOKENS,
  LEGACY_KERNEL_TOKENS,
  PER_REQUEST_KERNELS,
  buildKernelSendParam,
  isKernelSelection,
  isKernelToken,
  isLegacyKernelToken,
  kernelDisplayLabel,
  normalizeKernelSelection,
} from '../kernelVocabulary'

describe('§2.5.1 词汇全集与常量', () => {
  it('token 全集为 auto/fast/deep/dsh/claude_code/codex', () => {
    expect([...KERNEL_TOKENS].sort()).toEqual(
      ['auto', 'claude_code', 'codex', 'deep', 'dsh', 'fast'].sort(),
    )
  })

  it('逐 token 常量与字面量一致（徽章回显与后端路由依赖这些字符串）', () => {
    expect(KERNEL_AUTO).toBe('auto')
    expect(KERNEL_FAST).toBe('fast')
    expect(KERNEL_DEEP).toBe('deep')
    expect(KERNEL_DSH).toBe('dsh')
    expect(KERNEL_CLAUDE_CODE).toBe('claude_code')
    expect(KERNEL_CODEX).toBe('codex')
    expect(DEFAULT_KERNEL_SELECTION).toBe(KERNEL_AUTO)
  })

  it('成本档 = fast/deep；per-request 内核 = dsh/claude_code/codex（对齐 _PER_REQUEST_KERNELS）', () => {
    expect([...DSH_PROFILE_KERNELS].sort()).toEqual(['deep', 'fast'])
    expect([...PER_REQUEST_KERNELS].sort()).toEqual(['claude_code', 'codex', 'dsh'])
  })

  it('历史词汇 shell/legacy 不属于有效 token，也不在 per-request / 成本档内', () => {
    expect([...LEGACY_KERNEL_TOKENS].sort()).toEqual(['legacy', 'shell'])
    for (const legacy of LEGACY_KERNEL_TOKENS) {
      expect(isKernelToken(legacy)).toBe(false)
      expect(isLegacyKernelToken(legacy)).toBe(true)
      expect(PER_REQUEST_KERNELS).not.toContain(legacy)
      expect(DSH_PROFILE_KERNELS).not.toContain(legacy)
      expect(CHAT_KERNEL_SELECTOR_OPTIONS.map(o => o.value)).not.toContain(legacy)
    }
  })

  it('所有导出 token 都是字符串且互不重复', () => {
    expect(new Set(KERNEL_TOKENS).size).toBe(KERNEL_TOKENS.length)
    for (const token of KERNEL_TOKENS) expect(typeof token).toBe('string')
  })
})

describe('选择器选项集（两入口共用）', () => {
  it('选项为 auto/dsh/claude_code/codex，顺序稳定且标签非空', () => {
    expect(CHAT_KERNEL_SELECTOR_OPTIONS.map(o => o.value)).toEqual([
      'auto',
      'dsh',
      'claude_code',
      'codex',
    ])
    expect(CHAT_KERNEL_SELECTOR_OPTIONS.map(o => o.label)).toEqual([
      '自动内核',
      'DSH 执行内核',
      'Claude Code',
      'Codex',
    ])
    for (const option of CHAT_KERNEL_SELECTOR_OPTIONS) {
      expect(option.label.trim().length).toBeGreaterThan(0)
      expect(isKernelSelection(option.value)).toBe(true)
    }
  })

  it('选项值不含成本档 fast/deep（它们由深度模式等入口直接发送）', () => {
    const values = CHAT_KERNEL_SELECTOR_OPTIONS.map(o => o.value)
    expect(values).not.toContain(KERNEL_FAST)
    expect(values).not.toContain(KERNEL_DEEP)
  })

  it('持久化键为 siyi-chat-kernel（两入口共用同一键）', () => {
    expect(KERNEL_STORAGE_KEY).toBe('siyi-chat-kernel')
  })
})

describe('normalizeKernelSelection 归一', () => {
  it('有效 token 原样保留', () => {
    for (const token of KERNEL_TOKENS) {
      expect(normalizeKernelSelection(token)).toBe(token)
    }
  })

  it('空值 / 非字符串 / 未知值 → auto', () => {
    expect(normalizeKernelSelection('')).toBe(KERNEL_AUTO)
    expect(normalizeKernelSelection('   ')).toBe(KERNEL_AUTO)
    expect(normalizeKernelSelection(null)).toBe(KERNEL_AUTO)
    expect(normalizeKernelSelection(undefined)).toBe(KERNEL_AUTO)
    expect(normalizeKernelSelection('turbo')).toBe(KERNEL_AUTO)
    expect(normalizeKernelSelection(123)).toBe(KERNEL_AUTO)
  })

  it('历史词汇 shell/legacy → auto（§2.5.1：行为等同缺省）', () => {
    expect(normalizeKernelSelection('shell')).toBe(KERNEL_AUTO)
    expect(normalizeKernelSelection('legacy')).toBe(KERNEL_AUTO)
  })

  it('忽略首尾空白', () => {
    expect(normalizeKernelSelection('  claude_code  ')).toBe(KERNEL_CLAUDE_CODE)
  })

  it('大小写不敏感不适用：非规范写法视为未知 → auto', () => {
    expect(normalizeKernelSelection('Auto')).toBe(KERNEL_AUTO)
    expect(normalizeKernelSelection('CLAUDE_CODE')).toBe(KERNEL_AUTO)
  })
})

describe('buildKernelSendParam 发送参数构造', () => {
  it('auto / 空 / 历史词汇 / 未知值 → 不发 kernel（后端走 L2/L3 决策）', () => {
    expect(buildKernelSendParam(KERNEL_AUTO)).toEqual({})
    expect(buildKernelSendParam('')).toEqual({})
    expect(buildKernelSendParam(undefined)).toEqual({})
    expect(buildKernelSendParam(null)).toEqual({})
    expect(buildKernelSendParam('shell')).toEqual({})
    expect(buildKernelSendParam('legacy')).toEqual({})
    expect(buildKernelSendParam('nonsense')).toEqual({})
    expect('kernel' in buildKernelSendParam(KERNEL_AUTO)).toBe(false)
  })

  it('selector 发真实内核名', () => {
    expect(buildKernelSendParam(KERNEL_DSH)).toEqual({ kernel: 'dsh' })
    expect(buildKernelSendParam(KERNEL_CLAUDE_CODE)).toEqual({ kernel: 'claude_code' })
    expect(buildKernelSendParam(KERNEL_CODEX)).toEqual({ kernel: 'codex' })
  })

  it('独立窗深度模式仍发 deep，主窗成本档 fast 亦照发', () => {
    expect(buildKernelSendParam(KERNEL_DEEP)).toEqual({ kernel: 'deep' })
    expect(buildKernelSendParam(KERNEL_FAST)).toEqual({ kernel: 'fast' })
  })

  it('构造结果是 §2.5.1 有效 token', () => {
    for (const token of KERNEL_TOKENS) {
      const param = buildKernelSendParam(token)
      if (token === KERNEL_AUTO) {
        expect(param).toEqual({})
      } else {
        expect(param).toHaveProperty('kernel')
        expect(isKernelToken((param as { kernel: unknown }).kernel)).toBe(true)
      }
    }
  })
})

describe('守卫与标签', () => {
  it('isKernelToken 只认 §2.5.1 全集', () => {
    expect(isKernelToken('deep')).toBe(true)
    expect(isKernelToken('shell')).toBe(false)
    expect(isKernelToken('')).toBe(false)
    expect(isKernelToken(undefined)).toBe(false)
  })

  it('isKernelSelection 只认选择器可暴露的值', () => {
    for (const option of CHAT_KERNEL_SELECTOR_OPTIONS) {
      expect(isKernelSelection(option.value)).toBe(true)
    }
    expect(isKernelSelection(KERNEL_FAST)).toBe(false)
    expect(isKernelSelection(KERNEL_DEEP)).toBe(false)
    expect(isKernelSelection('shell')).toBe(false)
    expect(isKernelSelection(null)).toBe(false)
  })

  it('kernelDisplayLabel：选择器值用中文标签，成本档回退 token，未知回退原样', () => {
    expect(kernelDisplayLabel(KERNEL_DSH)).toBe('DSH 执行内核')
    expect(kernelDisplayLabel(KERNEL_AUTO)).toBe('自动内核')
    expect(kernelDisplayLabel(KERNEL_DEEP)).toBe('deep')
    expect(kernelDisplayLabel('whatever')).toBe('whatever')
    expect(kernelDisplayLabel(undefined)).toBe('')
  })
})
