/**
 * AI 对话编排器 / AI Chat Orchestrator
 *
 * 背景：手机端原本有两条互不相通的链路 ——
 *   1. `assistantRuntime.ts`（意图分类 + 隐私分级 + 路由 + 离线降级）写得很完整但**没有任何调用点**；
 *   2. `mobileStore.sendMessage` 里又手写了一套简化的「工具匹配 → 云 AI / 桌面 AI」。
 *
 * 结果：隐私分级形同虚设 —— 用户只要开启云 AI，日记、密码、私密内容会直接发到云端。
 *
 * 本模块把两条合并成一条真实生效的链路：
 *
 *   用户输入
 *     ↓
 *   隐私分级（classifyPrivacy）        ← 敏感内容强制本地，不上云
 *     ↓
 *   意图分类（classifyIntent）
 *     ↓
 *   本地工具命中？  ── 是 ──→ 直接执行，零网络
 *     ↓ 否
 *   路由（resolveTarget）
 *     ├─ 云 AI / 桌面 AI ──→ 流式输出，成功则写入离线缓存
 *     ├─ 本地模型（不可用时降级）
 *     └─ 无可用通道 ──→ 查缓存 → 命中直接返回；未命中则排队等重试
 *
 * 关键设计取舍：
 *  - 隐私优先于可用性：LOCAL_ONLY 的内容即使云端可用也不发，宁可提示用户。
 *  - 缓存按 query 哈希，7 天过期；命中时标注「来自缓存」，不冒充实时回答。
 *  - 排队请求保留原因，UI 能告诉用户「等联网后自动重试」而不是静默丢弃。
 */

import {
  IntentType,
  AiTarget,
  classifyIntent,
  classifyPrivacy,
  resolveTarget,
  PrivacyLevel,
  type PrivacyLevelType,
  type UserAiPreference,
} from './aiRouter'
import { ToolRegistry } from './toolRegistry'
import { OfflineAiFallback } from './offlineFallback'

/* ── 类型 ── */

export type RouteChannel = 'tool' | 'local_cache' | 'desktop' | 'cloud' | 'queued' | 'blocked'

export interface OrchestratorRequest {
  text: string
  sessionId?: string
  /** 调用方显式指定的隐私等级（如「私密模式」开关），否则按内容自动判定 */
  privacyOverride?: PrivacyLevelType
}

export interface OrchestratorContext {
  isDesktopConnected: boolean
  userPref: UserAiPreference
  /** 云 AI 是否配置了可用凭据（有 key 或已选 profile） */
  cloudConfigured: boolean
  /** 流式回调 */
  onChunk?: (chunk: string) => void
  /** 云 AI 流式发送 */
  sendCloud: (text: string, onChunk: (c: string) => void, sessionId?: string) => Promise<string>
  /** 桌面 AI 流式发送 */
  sendDesktop: (text: string, onChunk: (c: string) => void, sessionId?: string) => Promise<string>
}

export interface OrchestratorResult {
  channel: RouteChannel
  /** 最终文本（工具结果 / 缓存 / AI 回复） */
  text: string
  /** 实际采用的隐私等级，UI 可据此显示「本内容未离开设备」 */
  privacy: PrivacyLevelType
  intent: IntentType
  target: AiTarget
  /** 是否来自缓存 / 是否已排队 */
  fromCache: boolean
  queued: boolean
  error?: string
}

/* ── 敏感内容人类可读说明 ── */

export const PRIVACY_LABEL: Record<PrivacyLevelType, string> = {
  [PrivacyLevel.LOCAL_ONLY]: '仅本机',
  [PrivacyLevel.SYNCABLE]: '可同步',
  [PrivacyLevel.SHAREABLE]: '可外发',
  [PrivacyLevel.MODEL_EGRESS_ALLOWED]: '可发送给模型',
}

/* ── 编排器 ── */

export class ChatOrchestrator {
  constructor(
    private tools: ToolRegistry,
    private fallback: OfflineAiFallback,
  ) {}

  /** 只做判定，不执行 —— UI 可以在用户打字时实时提示「这段内容不会上传」 */
  inspect(text: string, privacyOverride?: PrivacyLevelType) {
    const intent = classifyIntent(text)
    const privacy = privacyOverride ?? classifyPrivacy(text)
    return { intent, privacy, sensitive: privacy === PrivacyLevel.LOCAL_ONLY }
  }

  async run(req: OrchestratorRequest, ctx: OrchestratorContext): Promise<OrchestratorResult> {
    const text = req.text.trim()
    const intent = classifyIntent(text)
    const privacy = req.privacyOverride ?? classifyPrivacy(text)
    const isLocalOnly = privacy === PrivacyLevel.LOCAL_ONLY

    /* 1. 本地工具优先 —— 零网络、零延迟，且天然不泄露
     *
     * 注意：不能只在 intent === LOCAL_CRUD 时才试工具。
     * 意图分类用的是「创建/新建/添加」这类动词，而工具标签是「记下来/提醒我」这类
     * 口语说法，两套词表不重合，会导致「帮我把这段记下来」被漏掉。
     * 工具匹配本身就是关键词匹配，直接让它先跑，命中即用。 */
    const matched = this.tools.matchTools(text)
    if (matched.length > 0) {
      try {
        const r = await matched[0].execute({ text })
        if (r.success) {
          return {
            channel: 'tool',
            text: r.data ?? '完成',
            privacy,
            intent,
            target: AiTarget.LOCAL_DATABASE,
            fromCache: false,
            queued: false,
          }
        }
      } catch {
        /* 工具失败继续走 AI，不让用户卡住 */
      }
    }

    /* 2. 隐私闸门：LOCAL_ONLY 内容禁止上云 / 上桌面 */
    if (isLocalOnly) {
      const cached = await this.fallback.tryCached(text)
      if (cached) {
        return {
          channel: 'local_cache',
          text: cached.response,
          privacy,
          intent,
          target: AiTarget.OFFLINE_CACHE,
          fromCache: true,
          queued: false,
        }
      }
      // 明确告知用户为什么没回答，而不是发出去或假装失败
      return {
        channel: 'blocked',
        text: '',
        privacy,
        intent,
        target: AiTarget.QUEUED_PENDING,
        fromCache: false,
        queued: false,
        error: '这段内容包含敏感信息，已按隐私设置阻止上传。如需云端回答，可在设置里关闭「隐私保护」或改用桌面端处理。',
      }
    }

    /* 3. 正常路由 */
    const target = resolveTarget(intent, privacy, ctx.isDesktopConnected, ctx.userPref)

    const tryDesktop = async (): Promise<string | null> => {
      if (!ctx.isDesktopConnected) return null
      try {
        return await ctx.sendDesktop(text, ctx.onChunk ?? (() => {}), req.sessionId)
      } catch {
        return null
      }
    }

    const tryCloud = async (): Promise<string | null> => {
      if (!ctx.cloudConfigured || !ctx.userPref.cloudAiEnabled) return null
      try {
        return await ctx.sendCloud(text, ctx.onChunk ?? (() => {}), req.sessionId)
      } catch {
        return null
      }
    }

    let reply: string | null = null
    let channel: RouteChannel = 'queued'

    if (target === AiTarget.DESKTOP_LOCAL_AI) {
      reply = await tryDesktop()
      if (reply != null) channel = 'desktop'
      // 桌面不可达且内容允许外发 → 降级到云，避免明明能用却排队
      if (reply == null && ctx.cloudConfigured && ctx.userPref.cloudAiEnabled
          && privacy === PrivacyLevel.MODEL_EGRESS_ALLOWED) {
        reply = await tryCloud()
        if (reply != null) channel = 'cloud'
      }
    } else if (target === AiTarget.CLOUD_AI) {
      reply = await tryCloud()
      if (reply != null) channel = 'cloud'
      if (reply == null && ctx.isDesktopConnected) {
        reply = await tryDesktop()
        if (reply != null) channel = 'desktop'
      }
    } else if (target === AiTarget.NETWORK_API) {
      // 纯信息查询：桌面代理优先（可复用桌面抓取能力），否则云
      reply = await tryDesktop()
      if (reply != null) channel = 'desktop'
      if (reply == null) {
        reply = await tryCloud()
        if (reply != null) channel = 'cloud'
      }
    }

    if (reply != null && reply.length > 0) {
      this.fallback.cacheResponse(text, reply)
      return {
        channel,
        text: reply,
        privacy,
        intent,
        target,
        fromCache: false,
        queued: false,
      }
    }

    /* 4. 全部通道失败：查缓存 → 命中就用；否则排队 */
    const cached = await this.fallback.tryCached(text)
    if (cached) {
      return {
        channel: 'local_cache',
        text: cached.response,
        privacy,
        intent,
        target: AiTarget.OFFLINE_CACHE,
        fromCache: true,
        queued: false,
      }
    }

    this.fallback.enqueueForRetry(text, req.sessionId ?? null, target)
    /*
     * 排队原因要说清「缺哪一项」，否则用户只知道「用不了」，
     * 无法判断该去开开关、填 Key，还是该连桌面端。
     * 之前这里只有一句笼统的「桌面端未连接且未配置云 AI」，
     * 即使云 AI 开了、Key 也填了，只要有一项没到位就仍是这句，
     * 排查时完全没有指向性。
     */
    let reason: string
    if (ctx.isDesktopConnected) {
      reason = 'AI 服务暂时不可用，已排队，联网后会自动重试'
    } else if (!ctx.userPref.cloudAiEnabled) {
      reason = '⏳ 云端 AI 未启用。请到 设置 → AI 配置 打开「启用云端 AI」，并填入 API Key'
    } else if (!ctx.cloudConfigured) {
      reason = '⏳ 云端 AI 缺少 API Key。请到 设置 → AI 配置 填入 API Key'
    } else if (ctx.userPref.promptBeforeCloud) {
      reason = '⏳ 已开启「云端调用前确认」，该设置会阻止自动上云。可在 设置 → AI 配置 关闭它'
    } else {
      reason = '⏳ 桌面端未连接，云端 AI 也不可用，已排队等待'
    }
    return {
      channel: 'queued',
      text: '',
      privacy,
      intent,
      target,
      fromCache: false,
      queued: true,
      error: reason,
    }
  }
}
