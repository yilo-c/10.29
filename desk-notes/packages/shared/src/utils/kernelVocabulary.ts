/**
 * chat.send `params.kernel` 统一词汇表（前端单一来源）
 *
 * 唯一事实源：`docs/kernel-contract.md` §2.5.1「params.kernel 统一词汇映射表」（v1.2.1）。
 * 后端实现：`services/agent_gateway/routing.py` 的 `get_gateway()` + `resolve_dsh_profile()`。
 *
 * 三级路由（§2.5）下，本模块只负责 L1「用户指定」这一层的词汇与构造：
 * - `auto` / 空          → 不发 kernel，后端注入网关（网关再按 chat_route / 自动分流决策）
 * - `fast` / `deep`      → 强制 DSH profile（siyi-fast / siyi-deep），不依赖开关
 * - `dsh` / `claude_code` / `codex` → per-request 内核选择，依赖 `agent_kernel.per_request_selection`
 * - `shell` / `legacy`   → 历史词汇，不在 `_PER_REQUEST_KERNELS`，行为等同缺省；本模块一律归一为 `auto`
 *
 * 两个 chat 入口（主窗 ChatView/ChatComposer、独立窗 StandaloneChatView→ChatModule）
 * 都必须从这里取常量，禁止在组件内写裸 kernel 字符串，以保证 CT-13
 * 「UI 所选 = 后端实际执行 = 响应 kernel 徽章」三处一致。
 */

/** §2.5.1 有效 kernel token 全集（含成本档与 per-request 内核，不含历史词汇） */
export const KERNEL_TOKENS = ['auto', 'fast', 'deep', 'dsh', 'claude_code', 'codex'] as const

export type KernelToken = (typeof KERNEL_TOKENS)[number]

/** §2.5.1 逐 token 常量 */
export const KERNEL_AUTO: KernelToken = 'auto'
export const KERNEL_FAST: KernelToken = 'fast'
export const KERNEL_DEEP: KernelToken = 'deep'
export const KERNEL_DSH: KernelToken = 'dsh'
export const KERNEL_CLAUDE_CODE: KernelToken = 'claude_code'
export const KERNEL_CODEX: KernelToken = 'codex'

/** 成本档：直连 DSH profile（`resolve_dsh_profile`），不依赖 per-request 开关 */
export const DSH_PROFILE_KERNELS = [KERNEL_FAST, KERNEL_DEEP] as const
export type DshProfileKernel = (typeof DSH_PROFILE_KERNELS)[number]

/** per-request 内核：需 `agent_kernel.per_request_selection=true`（对应后端 `_PER_REQUEST_KERNELS`） */
export const PER_REQUEST_KERNELS = [KERNEL_DSH, KERNEL_CLAUDE_CODE, KERNEL_CODEX] as const
export type PerRequestKernel = (typeof PER_REQUEST_KERNELS)[number]

/** 历史词汇：行为等同缺省，UI 与发送参数中都不应再出现 */
export const LEGACY_KERNEL_TOKENS = ['shell', 'legacy'] as const
export type LegacyKernelToken = (typeof LEGACY_KERNEL_TOKENS)[number]

/** 选择器可选项：auto 之外只暴露 per-request 真实内核名（§2.5.1） */
export type KernelSelectorValue = Exclude<KernelToken, DshProfileKernel>

export interface KernelSelectorOption {
  value: KernelSelectorValue
  label: string
}

/**
 * 两个入口共用的内核选择器选项集。
 * 标签为既有中文文案，改动会牵动 UI 验收，故保持原样。
 */
export const CHAT_KERNEL_SELECTOR_OPTIONS: KernelSelectorOption[] = [
  { value: KERNEL_AUTO, label: '自动内核' },
  { value: KERNEL_DSH, label: 'DSH 执行内核' },
  { value: KERNEL_CLAUDE_CODE, label: 'Claude Code' },
  { value: KERNEL_CODEX, label: 'Codex' },
]

/** 默认选择：不指定内核 */
export const DEFAULT_KERNEL_SELECTION: KernelSelectorValue = KERNEL_AUTO

/** localStorage 持久化键（两个入口共用同一键，保证词汇与状态一致） */
export const KERNEL_STORAGE_KEY = 'siyi-chat-kernel'

/** 类型守卫：是否为 §2.5.1 有效 token */
export function isKernelToken(raw: unknown): raw is KernelToken {
  return typeof raw === 'string' && (KERNEL_TOKENS as readonly string[]).includes(raw)
}

/** 是否为历史词汇（`shell` / `legacy`） */
export function isLegacyKernelToken(raw: unknown): raw is LegacyKernelToken {
  return typeof raw === 'string' && (LEGACY_KERNEL_TOKENS as readonly string[]).includes(raw)
}

/**
 * 归一任意来源（localStorage / URL / 旧数据）的取值：
 * 有效 token 原样返回；历史词汇与未知值统一落到 `auto`（§2.5.1「行为等同缺省」）。
 */
export function normalizeKernelSelection(raw: unknown): KernelToken {
  if (typeof raw !== 'string') return DEFAULT_KERNEL_SELECTION
  const value = raw.trim()
  if (!value || isLegacyKernelToken(value)) return DEFAULT_KERNEL_SELECTION
  if (isKernelToken(value)) return value
  return DEFAULT_KERNEL_SELECTION
}

/** 选择器取值是否合法（用于持久化前的写侧校验） */
export function isKernelSelection(raw: unknown): raw is KernelSelectorValue {
  return (CHAT_KERNEL_SELECTOR_OPTIONS as readonly { value: string }[]).some(o => o.value === raw)
}

/**
 * 把选择器/深度模式的取值构造成 `chat.send` 的 params 片段。
 *
 * - `auto` / 空 / 历史词汇 / 未知值 → `{}`：不发 kernel，让后端走 L2/L3 决策
 * - 其余有效 token → `{ kernel: token }`：selector 发真实内核名，deepMode 发 `deep`
 */
export function buildKernelSendParam(selection: unknown): { kernel: KernelToken } | Record<string, never> {
  const token = normalizeKernelSelection(selection)
  if (token === KERNEL_AUTO) return {}
  return { kernel: token }
}

/** 选择器标签查询：命中选项集则用其标签，否则回退为 token 本身（徽章/回显用） */
export function kernelDisplayLabel(token: unknown): string {
  if (!isKernelToken(token)) return typeof token === 'string' ? token : ''
  const match = CHAT_KERNEL_SELECTOR_OPTIONS.find(o => o.value === token)
  return match ? match.label : token
}
