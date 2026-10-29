/**
 * 内核能力声明的**唯一读取入口**（批5 单一真源，2026-09-18）。
 *
 * 背景（F-P2「mcp:kernel 两真源矛盾」）：同一件事实——"当前执行内核能不能在
 * 既有会话上续跑"——此前被两处各算一遍且结论相反：
 *   - 后端 `backend/handlers/chat_paths/gateway_path.py::run_gateway_resume`
 *     的 docstring 声称"在既有会话上续跑"；
 *   - 前端 `components/chat/ChatView.tsx` 硬编码"DSH 内核没有断点 checkpoint
 *     → chat.resume 落到 butler 并返回 no_pending_task → 无法续跑"。
 *
 * 真源只有一个：适配器的能力声明
 * `services/agent_gateway/protocol.py::BaseGateway.capabilities()["resume"]`
 * （后端 `gateway_path.run_gateway_resume` 经 `protocol.supports_resume` 读同一处），
 * 由 `backend/handlers/_kernel.py::build_kernel_status` 透出到
 * `kernel.status.details.capabilities`。前端一律经本模块读取，不得再凭注释推断。
 *
 * 失败即"不支持"：读不到 / 调用失败 → false（保守），走不依赖续跑能力的兜底
 * 路径（主窗 `autoContinueKernel` 重发），而不是赌一次 chat.resume。
 */

/** 内核能力声明（与后端 BaseGateway.capabilities() 的键一一对应）。 */
export interface KernelCapabilities {
  /** 原生增量事件（非 submit 事后抹平）。 */
  stream?: boolean
  /** 结构化工具调用事件。 */
  tool?: boolean
  /** 请求用户确认事件。 */
  confirm?: boolean
  /** 真正在既有会话上续跑（本模块关心的那一个）。 */
  resume?: boolean
  /** 可取消运行中任务。 */
  cancel?: boolean
}

/** kernel.status 中与能力声明有关的最小子集（其余字段调用方按需自取）。 */
export interface KernelStatusCapabilityView {
  details?: { capabilities?: KernelCapabilities }
}

/** 读取能力声明所需的最小客户端面（便于测试注入桩）。 */
export interface RpcCallerLike {
  call: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
}

/** kernel.status 的方法名（真源：backend/handlers/_kernel.py:87 注册）。 */
export const KERNEL_STATUS_METHOD = 'kernel.status'

/**
 * 读当前内核的能力声明；不可得时返回 null（调用方按"能力未知"保守处理）。
 */
export async function readKernelCapabilities(client: RpcCallerLike | null | undefined): Promise<KernelCapabilities | null> {
  if (!client) return null
  try {
    const status = await client.call<KernelStatusCapabilityView>(KERNEL_STATUS_METHOD, {})
    const caps = status?.details?.capabilities
    return caps && typeof caps === 'object' ? caps : null
  } catch {
    return null
  }
}

/**
 * 当前内核是否**真正**支持在既有会话上续跑（chat.resume 会走 gw.resume）。
 *
 * 未声明 / 读不到 / 调用异常一律 false —— 与后端
 * `services/agent_gateway/protocol.py::supports_resume` 的保守缺省完全一致。
 */
export async function kernelSupportsResume(client: RpcCallerLike | null | undefined): Promise<boolean> {
  const caps = await readKernelCapabilities(client)
  return caps?.resume === true
}
