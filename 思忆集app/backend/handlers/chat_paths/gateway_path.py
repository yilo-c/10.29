"""网关路径 — M2b / Flip（chat 切网关）。

chat.send 经 ``context.agent_gateway``（M1 执行内核端口）提交任务，
把内核产出的 ``AgentRunEvent`` 增量流映射为前端 chunk。功能路径层
只依赖 ``protocol.AgentGateway`` 抽象，换内核零改动。

分发（见 _chat_memory._chat_send_inner）：
- L1 强制：``params.kernel`` 存在 → 走网关（用户显式指定内核）。
- L2 灰度：设置 ``chat_route == "gateway"`` → 走网关。
- L3 缺省：``chat_route`` 缺失或非 ``gateway`` → ``legacy``（壳内管家）；
  仅显式 ``gateway`` 才走网关（灰度开关语义）。

Flip 回翻（2026-09）：2026-08-16 的 Flip 曾把缺省路由由 legacy 翻为
  gateway，因内核链路未全通（壳工具断线等）已回翻缺省为 legacy。
  ``_get_gateway`` 立即返回 None（type=none / 命令不在 PATH）时
  ``run_gateway_path`` 立刻 False → 零额外开销回落 butler，并据
  ``kernel.status`` / system.health 的 kernel 子系统标记 degraded。

本模块两个入口都是**协程**，直接经 ``send_chunk`` 推送 chunk，并以
返回值 ``bool`` 告知 dispatcher 是否"已处理"：
- 返回 ``True`` ：已产出文本/错误 chunk（dispatcher 结束本路径）。
- 返回 ``False``：网关不可用 / 空响应 → dispatcher 自然落到默认路径
  （降级而非崩溃）。

注意：网关内核（如 DSH）是独立进程，自带上下文；因此路由到网关时
直接以原始 ``message`` 提交，不再叠加 web_search / memory anchor /
persona 等遗留增强（这些增强属于遗留管家路径）。如需在内核侧复用，
应由内核适配器负责。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from typing import Any

logger = logging.getLogger("siyi.chat_paths.gateway_path")

# 路由解析/网关访问下沉到 services.agent_gateway.routing（Flip 共享层），
# 此处仅以 _ 别名导出，保持 chat_paths 接口与 M2b 调用点不变。
from services.agent_gateway.routing import (
    resolve_chat_route as _resolve_chat_route,
    get_gateway as _get_gateway,
    resolve_dsh_profile as _resolve_dsh_profile,
)

# 批5（2026-09-18）：续跑能力判据的**唯一入口**（单一真源）。
# 本模块此前对"内核能否在既有会话上续跑"有两套说法：run_gateway_resume 的
# docstring 说能（"在既有会话上续跑"），同文件 :503 注释又说不能（"退化为
# submit"）—— 前端 ChatView 还有第三套硬编码。现统一读内核能力声明。
from services.agent_gateway.protocol import supports_resume as _supports_resume


# ── A 项：观察期埋点（2026-08-20）─────────────────────────────
# 每次内核执行（经网关）都向 UsageTracker 写 source=kernel:<name> 的用量记录，
# 并向 TraceRecorder 登记一次内核执行 run。所有调用在本模块内 try/except 兜底，
# 观测失败绝不影响 chat 主路径行为（不改动降级链 / 回落语义）。


def _kernel_source_name(gw: Any) -> str:
    """从网关对象解析用于用量/trace 标识的内核名。

    AutoGateway 包装多候选时还原当前实际生效内核（``_active``），
    避免把所有内核执行归因到字面 'auto'；退化为 'kernel'。
    """
    if gw is None:
        return "kernel"
    active = getattr(gw, "_active", None)
    if active:
        return str(active)
    name = getattr(gw, "name", "") or ""
    return name or "kernel"


def _estimate_tokens_for_kernel(text: str) -> int:
    """字符估算 token（契约 §5.2 既有口径：CJK~2 字符/token，英文~4 字符/token，最小 1）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    non_cjk = len(text) - cjk
    return max(1, int(cjk / 2 + non_cjk / 4))


# ── C-38 记账修复（2026-09-12）：真实 provider/model 解析 ────────────────
# 旧实现把内核用量硬编码 provider="kernel"、model=name（如 "dsh"），既不是
# 真实厂商也不是真实模型，叠加 PRICING_TABLE 无 "dsh" 键 → estimate_cost 回落
# 默认单价，形成 cost 二重失真。修复思路：**只记可核实的真值，拿不到就显式
# 标 unknown，绝不编造具体付费模型**。真值来源优先级（见 _resolve_kernel_provider_model）。

_DSH_AGENT_DEFAULT_RE = re.compile(r"^agent-default-model:\s*$")
_DSH_SUB_KEY_RE = re.compile(r"^\s+(provider|model):\s*(.+?)\s*$")


def _read_dsh_default_model() -> "tuple[str, str] | None":
    """读 ``~/.dsh/settings.yaml`` 顶层 ``agent-default-model`` 的 provider/model。

    这是 dsh 内核在**没有壳侧 --patch 注入**时实际使用的默认模型（唯一可核实的
    配置真值）。用轻量文本扫描而非 yaml.safe_load：现场 settings.yaml 含畸形键
    （如 ``bailian:ilian:``）会整体破坏严格 YAML 解析，而我们只需要这一个顶层块。
    任何异常 / 找不到 → None（交回调用方走 unknown 兜底，不编造）。
    """
    try:
        path = os.path.expanduser(os.path.join("~", ".dsh", "settings.yaml"))
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines):
            if _DSH_AGENT_DEFAULT_RE.match(line):
                found: dict[str, str] = {}
                for j in range(i + 1, min(i + 6, len(lines))):
                    m = _DSH_SUB_KEY_RE.match(lines[j])
                    if m:
                        found[m.group(1)] = m.group(2)
                model = found.get("model", "")
                if model:
                    return (found.get("provider", "") or "dsh", model)
                return None
        return None
    except Exception as exc:  # noqa: BLE001 — 配置探测不得影响主路径
        logger.debug("read dsh default model failed: %s", exc)
        return None


def _resolve_kernel_provider_model(gw: Any, name: str) -> "tuple[str, str, str]":
    """解析内核执行实际使用的 (provider, model, source_tag)。

    source_tag ∈ {model_patch, adapter_config, dsh_settings, unknown}，标明真值来源，
    供排查/审计"这个 provider 是从哪来的"。**拿不到真值时 provider 记
    'kernel:<name>'、model 记 name，不编造任何具体付费模型。**
    """
    # 1) 壳侧方案 B 注入的 model_patch（形如 "provider/model"）——唯一确定的真实配置
    patch = str(getattr(gw, "model_patch", "") or "").strip()
    if "/" in patch:
        provider, _, model = patch.partition("/")
        provider, model = provider.strip(), model.strip()
        if provider and model:
            return provider, model, "model_patch"
    # 2) 适配器显式配置的 model（claude_code / codex 有 self.model）
    cfg_model = getattr(gw, "model", None)
    if cfg_model:
        return f"kernel:{name}", str(cfg_model), "adapter_config"
    # 3) AutoGateway 包装：下钻当前 active 候选，复用其解析（不污染判定）
    active = getattr(gw, "_active", None)
    pool = getattr(gw, "_pool", None)
    if active and isinstance(pool, dict) and active in pool:
        return _resolve_kernel_provider_model(pool[active], active)
    # 4) dsh 无注入：读 ~/.dsh/settings.yaml 的实际默认模型（配置真值）
    if name == "dsh":
        got = _read_dsh_default_model()
        if got:
            provider, model = got
            return provider, model, "dsh_settings"
    # 5) 兜底：无从确定真实模型 → 不编造，标 kernel:<name> / name（下游按 unknown 处理）
    return f"kernel:{name}", name, "unknown"


def _extract_kernel_usage(events: "list[Any] | None") -> "tuple[int, int] | None":
    """从事件流 result.usage 提取内核**真实返回**的 token 数（若提供）。

    DSH headless 目前不吐 usage（见 adapters：raw 仅 returncode）→ 返回 None，
    调用方回落到字符估算并标 estimated=True。未来若有内核返回真实 usage，则
    走精确值且 estimated=False，供 D20 观察窗区分精确/估算样本。
    """
    for ev in events or []:
        if getattr(ev, "kind", "") != "result":
            continue
        usage = (getattr(ev, "payload", {}) or {}).get("usage")
        if isinstance(usage, dict):
            p = usage.get("prompt_tokens", usage.get("input_tokens", usage.get("prompt")))
            c = usage.get("completion_tokens", usage.get("output_tokens", usage.get("completion")))
            if p is not None or c is not None:
                try:
                    return (int(p or 0), int(c or 0))
                except (TypeError, ValueError):
                    return None
    return None


def _collect_kernel_result_text(events: list[Any]) -> str:
    """从事件流提取内核产出文本（delta + result 拼接），用于用量估算。"""
    parts: list[str] = []
    for ev in events:
        kind = getattr(ev, "kind", "")
        payload = getattr(ev, "payload", {}) or {}
        if kind == "delta" and payload.get("text"):
            parts.append(str(payload["text"]))
        elif kind == "result" and payload.get("text"):
            parts.append(str(payload["text"]))
    return "".join(parts)


def _observe_kernel_start(name: str, context: Any) -> str:
    """内核执行起点：trace 登记；返回 run_id 供 finish 配对。"""
    try:
        recorder = getattr(context, "trace_recorder", None)
        if recorder is not None and hasattr(recorder, "start_run"):
            run_id = f"kernel:{name}:{uuid.uuid4().hex[:12]}"
            recorder.start_run(run_id, "kernel", source=f"kernel:{name}")
            return run_id
    except Exception as exc:  # noqa: BLE001 — 观测不得影响主路径
        logger.debug("kernel trace start failed: %s", exc)
    return ""


def _observe_kernel_finish(run_id: str, context: Any, status: str) -> None:
    """内核执行终点：关闭 trace run。"""
    if not run_id:
        return
    try:
        recorder = getattr(context, "trace_recorder", None)
        if recorder is not None and hasattr(recorder, "finish_run"):
            recorder.finish_run(run_id, status)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kernel trace finish failed: %s", exc)


def _record_kernel_usage(
    name: str,
    context: Any,
    message: str,
    result_text: str,
    success: bool,
    gw: Any = None,
    events: "list[Any] | None" = None,
) -> None:
    """内核执行用量记录（C-38 修复）。

    provider/model 从内核**实际配置**解析（_resolve_kernel_provider_model：
    注入的 model_patch > 适配器 model > dsh settings.yaml 默认模型 > unknown），
    不再硬编码 "kernel"/name。token 优先取内核真实 usage，取不到才字符估算并
    在记录里标 ``estimated=True``（供 D20 观察窗过滤精确值）。未知模型的成本
    由 usage_tracker 侧记 unknown（不再回落默认单价）。
    """
    try:
        from ai.usage_tracker import get_usage_tracker

        tracker = get_usage_tracker()
        if tracker is None:
            return
        provider, model, _source_tag = _resolve_kernel_provider_model(gw, name)
        real = _extract_kernel_usage(events)
        if real is not None:
            prompt_tokens, completion_tokens = real
            estimated = False
        else:
            prompt_tokens = _estimate_tokens_for_kernel(message or "")
            completion_tokens = _estimate_tokens_for_kernel(result_text or "")
            estimated = True
        tracker.record(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            success=success,
            source=f"kernel:{name}",
            estimated=estimated,
        )
    except Exception as exc:  # noqa: BLE001 — 用量追踪不得阻塞主路径
        logger.debug("kernel usage record failed: %s", exc)


async def _safe_chunk(send_chunk: Any, text: str, chunk_type: str | None = None) -> Any:
    """兼容新旧 send_chunk 签名（部分调用点为位置参数 only）。

    send_chunk 为异步（与 pydantic_agent_path 一致，由上层 await）。
    """
    if send_chunk is None:
        return None
    try:
        if chunk_type is not None:
            return await send_chunk(text, chunk_type=chunk_type)
        return await send_chunk(text)
    except TypeError:
        return await send_chunk(text)


def _record_route_decision(lane: str) -> None:
    """§9.5⑰（M1 尾）：把一次 chat 路由判定落一行决策标记到 usage_records。

    写入口径（与读数端正源 ``scripts/route_report.py`` 的
    ``source LIKE 'route:%'`` 聚合对齐）：provider='router'、model='decision'、
    source='route:fast'|'route:deep'、tokens=0、cost=0、success=1。
    provider 不以 'kernel' 开头，故不进 D20 观察窗的 kernel 样本。

    观测失败绝不影响路由结果（与 ``_record_kernel_usage`` 同纪律）：tracker 未
    初始化 / 替身对象无该方法 / SQLite 异常，一律静默降级。
    """
    try:
        from ai.usage_tracker import get_usage_tracker

        tracker = get_usage_tracker()
        if tracker is None:
            return
        record = getattr(tracker, "record_route_decision", None)
        if record is None:
            return
        record(lane)
    except Exception as exc:  # noqa: BLE001 — 埋点不得阻塞路由判定
        logger.debug("route decision record failed: %s", exc)


def _use_gateway_route(context: Any, params: dict[str, Any], message: str | None = None) -> bool:
    """四级路由判定（仅决定"是否走网关"，不校验网关可用性）。

    L1 强制：``params.kernel`` 为真 → 网关。
    L2 显式：``chat_route`` 设置为 gateway → 网关。
    L3 分层：``chat_route`` 设置为 auto → 按任务复杂度分流（2026-09-11 点火）。
       复杂任务（含动作动词的真实请求，如"帮我打开/创建/修复..."）走内核
       （~10-15s，工具齐全）；简单对话走壳内管家（~1.8s）。全切内核会让
       多数交互变慢变贵——分流器复用现成 ``_is_complex_task``（动作动词 +
       最小长度，与壳内工具触发同一判定）。message 缺失时保守判为简单。
    L4 缺省：legacy（Flip 回翻后仅显式 gateway/auto 才走，其余全否）。
    网关可用性由 run_gateway_path 内部兜底（None/type=none → 返回 False）。

    §9.5⑰ 埋点：每个判定出口各记一笔（走网关=route:deep，留壳内=route:fast），
    使 fast_ratio 从 N/A 变为可算；返回值语义与本函数改造前完全一致。
    """

    def _decide(use_gateway: bool) -> bool:
        _record_route_decision("deep" if use_gateway else "fast")
        return use_gateway

    if params.get("kernel"):
        return _decide(True)
    route = _resolve_chat_route(context)
    if route == "gateway":
        return _decide(True)
    if route == "auto":
        if not message:
            return _decide(False)
        from backend.handlers.chat_paths.helpers import _is_complex_task

        return _decide(_is_complex_task(message))
    return _decide(False)


def _drain_events(gw, message: str, timeout_s: int | None, profile: str | None = None, context: Any = None):
    """在线程内消费阻塞式 submit_stream，返回事件列表（含异常兜底）。

    A 项（观察期埋点）：内核执行前后接 trace + 用量记录（source=kernel:<name>）。
    """
    from services.agent_gateway.protocol import AgentRunEvent

    name = _kernel_source_name(gw)
    run_id = _observe_kernel_start(name, context)
    events: list[Any] = []
    success = False
    try:
        for ev in gw.submit_stream(message, timeout_s=timeout_s, profile=profile):
            events.append(ev)
            if getattr(ev, "kind", "") == "result":
                success = True
    except Exception as exc:  # 内核异常：归一化为 error 事件，不抛到事件循环
        logger.warning("gateway submit_stream failed: %s", exc, exc_info=True)
        events.append(
            AgentRunEvent(
                kind="error",
                payload={"error_kind": "internal", "message": str(exc)[:300]},
            )
        )
        success = False
    # 内核执行结束：用量记录 + trace finish
    result_text = _collect_kernel_result_text(events)
    _record_kernel_usage(name, context, message, result_text, success, gw=gw, events=events)
    _observe_kernel_finish(run_id, context, "completed" if success else "failed")
    return events


# 内核阶段 → 可读文案（2026-09-11）。前端 agentEvents.parseEventData 优先取
# payload.message 作展示文本、据 payload.status 决定 running/done 态，故 notice
# 必须同时提供这两个字段，否则会 fallback 成展示原始 JSON 字符串。
_KERNEL_PHASE_LABELS: dict[str, str] = {
    "started": "执行内核已启动，正在处理…",
    "running": "执行内核运行中…",
    "waiting": "执行内核仍在执行…",
}


def _kernel_waiting_notice(waited_s: float) -> str:
    """等待期进度提示（dsh 伪流式下，用户此前全程看不到任何反馈）。"""
    return json.dumps(
        {
            "phase": "waiting",
            "status": "running",
            "message": f"执行内核仍在执行（已等待 {int(waited_s)} 秒）…",
        },
        ensure_ascii=False,
    )


def _event_to_chunk(ev, handled: bool):
    """单事件 -> ((text, chunk_type), new_handled)；不产出时 (None, handled)。

    delta 先行去重 result；confirm/tool/error 全发，供各路径逐事件复用。
    """
    kind = getattr(ev, "kind", "")
    payload = getattr(ev, "payload", {}) or {}
    if kind == "delta":
        text = payload.get("text", "")
        if text:
            return (text, "chunk"), True
    elif kind == "result":
        text = payload.get("text", "")
        if text and not handled:
            return (text, "chunk"), True
    elif kind == "confirm":
        # 内核请求用户确认：payload 序列化为 chunk（chunk_type="confirm"），
        # 前端据此渲染审批提示；批准/拒绝经 confirmation.resolve 回写 store。
        return (json.dumps(payload, ensure_ascii=False), "confirm"), True
    elif kind == "tool":
        # 内核工具调用：结构化步骤（chunk_type="tool"），前端可展示
        # 工具步骤进度（如 "调用 siyi_notes_search"）。
        return (json.dumps(payload, ensure_ascii=False), "tool"), True
    elif kind == "error":
        ek = payload.get("error_kind", "internal")
        msg = payload.get("message", "failed")
        return (f"\n\n[执行内核错误：{ek}] {msg}", "error"), True
    elif kind == "status":
        # 阶段事件（started/running）：emit 为 notice（前端 agentEvents 渲染
        # 为"内核状态"卡片）--既给用户即时反馈，也重置流空闲计时器。
        # 2026-09-11：补 message/status 两个字段。前端 agentEvents.parseEventData
        # 优先取 message 作展示文本、据 status 决定 running/done 态；此前只发
        # {"phase": ...}，前端取不到 message 会 fallback 成展示原始 JSON。
        phase = payload.get("phase", "")
        if phase:
            label = _KERNEL_PHASE_LABELS.get(phase, f"执行内核 {phase}")
            return (
                json.dumps(
                    {"phase": phase, "status": "running", "message": label},
                    ensure_ascii=False,
                ),
                "notice",
            ), True
    return None, handled


# ── 致命内核错误识别（2026-09-09）────────────────────────────
# 订阅过期 / 鉴权失效 / 配额耗尽等"内核自身不可用"类错误：内核不可能产出
# 结果，硬失败会让整条 chat 直接报错（典型案例：dsh 火山引擎 AgentPlan
# 订阅过期 → "(2124449892) does not have a valid AgentPlan subscription"）。
# 命中时视作"网关未处理"回落壳内管家（与 dsh_missing 降级链同语义）。
# 任务级错误（工具崩溃 / 内核不可用等）不命中：保持原行为（展示错误、
# 不回落），避免管家重复执行内核已跑过、可能产生副作用的工具。
_FATAL_KERNEL_ERROR_MARKERS = (
    "subscription",  # AgentPlan subscription expired
    "agentplan",
    "unauthorized",
    "invalid api key",
    "invalid api-key",  # dsh/百炼实测：Invalid API-key provided（连字符变体）
    "invalid_api_key",  # OpenAI 风格错误码：code=invalid_api_key（下划线变体）
    "api key not valid",
    "authentication",
    "quota",  # insufficient quota（余额/配额耗尽）
    "billing",
    "payment required",
)


def _is_fatal_kernel_error(message: str) -> bool:
    text = str(message or "").lower()
    return any(marker in text for marker in _FATAL_KERNEL_ERROR_MARKERS)


def _fatal_kernel_failure(events: list[Any]) -> bool:
    """事件流是否为"致命内核错误且无成功结果"（应回落壳内管家）。"""
    if any(getattr(ev, "kind", "") == "result" for ev in events):
        return False
    for ev in events:
        if getattr(ev, "kind", "") != "error":
            continue
        payload = getattr(ev, "payload", None)
        if isinstance(payload, dict) and _is_fatal_kernel_error(payload.get("message", "")):
            return True
    return False


def _event_error_message(ev: Any) -> str:
    """提取 error 事件的 message 字段（非 error 事件返回空串）。"""
    if getattr(ev, "kind", "") != "error":
        return ""
    payload = getattr(ev, "payload", None)
    return str(payload.get("message", "")) if isinstance(payload, dict) else ""


def _fatal_error_as_notice(text: str) -> str:
    """致命内核错误文本 → notice payload（JSON）。

    error chunk 在前端是**流级终止**（ws-client onError → 生成器抛出），
    降级后管家的回复将无法显示；因此致命错误改以 notice 下发——
    进入事件时间线（用户知情），流继续，随后回落壳内管家作答。
    """
    return json.dumps({"event": "kernel_error", "message": text.strip()}, ensure_ascii=False)


# 渐进式路径：等待内核事件超过该间隔时向生成器消费方 yield 心跳，
# 防止 dispatch_stream 的空闲计时器（默认 60s）在内核长任务（DSH 56s+）期间超时。
GATEWAY_HEARTBEAT_S = 20.0

# 线程生产者结束哨兵（object 哨兵避免与事件对象混淆）
_GW_SENTINEL = object()


# ── 跨轮上下文注入（2026-09-11）──────────────────────────────────
# DSH 内核每次都是全新会话（DshGateway.resume 按能力声明退化为 submit，见
# adapters.py DshGateway.resume + protocol.BaseGateway._default_capabilities["resume"]=False），
# 而本模块又刻意只以原始 message 提交（内核是独立进程、自带上下文，不叠加壳内的
# web_search / anchor / persona 增强）。两者叠加的后果是：用户说「再改一下」
# 「执行完了吗」这类跟进语时，内核侧完全失忆。
#
# 前端 chat.send 一直传着 context_messages（useChatStream.ts:73），壳内
# build_messages 也在用它（helpers.py:54）——只是网关路径没用。此处补齐：
# 仅拼接对话历史、不叠加任何壳内增强，保持「内核自带上下文」的语义边界。
_KERNEL_HISTORY_MAX_TURNS = 10
_KERNEL_HISTORY_ITEM_MAX_CHARS = 500
# C-P1：内核 context bundle（默认 off，opt-in）。开启且命中复杂任务时，
# 复用壳内 build_anchor_async（persona+相关记忆/笔记+偏好）截断注入，治“内核失忆/数据对不上”。
_KERNEL_CONTEXT_BUNDLE_MAX = 2000


async def _kernel_context_bundle_enabled(context: Any) -> bool:
    """读 settings `kernel_context_bundle`（默认 False=off）。任何异常均视为 off，绝不阻断内核。"""
    try:
        repo = getattr(context, "settings_repository", None) or getattr(context, "settings", None)
        if repo is None:
            return False
        val = await asyncio.to_thread(repo.get_setting, "kernel_context_bundle", False)
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "on", "yes")
        return bool(val)
    except Exception:  # noqa: BLE001
        logger.debug("kernel_context_bundle 开关读取失败，视为 off", exc_info=True)
        return False


def _build_kernel_prompt(message: str, params: dict[str, Any], context_bundle: str = "") -> str:
    """拼接内核 prompt：近期历史（始终）+ 可选 context bundle（opt-in，persona/记忆/偏好）。

    默认仅历史拼接（内核自带上下文、不叠壳内增强的契约）；仅当调用方传入
    非空 `context_bundle`（由开关+复杂任务门控后才生成）时前置注入，截断至上限。
    """
    core = message
    history = params.get("context_messages")
    if isinstance(history, list) and history:
        lines: list[str] = []
        for item in history[-_KERNEL_HISTORY_MAX_TURNS:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if not content or role not in ("user", "assistant", "system"):
                continue
            label = {"user": "用户", "assistant": "助手", "system": "系统"}[role]
            lines.append(f"{label}: {content[:_KERNEL_HISTORY_ITEM_MAX_CHARS]}")
        if lines:
            core = "以下是近期对话记录（供理解上下文）：\n" + "\n".join(lines) + f"\n\n用户当前消息：\n{message}"
    if context_bundle and context_bundle.strip():
        bundle = context_bundle.strip()[:_KERNEL_CONTEXT_BUNDLE_MAX]
        return "以下是用户画像与相关记忆/笔记（壳内注入，供参考）：\n" + bundle + "\n\n" + core
    return core


async def run_gateway_path_progressive(context, message, params):
    """渐进式网关路径（async generator，P0 修复 chat.send 空闲超时）。

    旧实现（run_gateway_path）先在线程内收集**全部**内核事件（DSH 一次
    执行 56s+）再统一 emit--期间 chat.send 生成器不 yield 任何 chunk，
    dispatch_stream 的空闲计时器（60s）必然超时（2026-08-16 "帮我打开
    vs code" 事故根因）。

    本实现：线程生产者逐事件投递 asyncio.Queue，消费侧即时 emit；
    等待事件超过 GATEWAY_HEARTBEAT_S 时 yield 心跳信号。调用方对每个
    信号 yield 一次即可持续重置空闲计时器。

    yield 信号约定（tuple，首元素为信号类型）：
    - ("emit", text, chunk_type) : 调用方应 send_chunk(text, chunk_type) 并标记 handled
    - ("heartbeat", None, None)  : 仅用于重置计时器，不发送内容
    - ("handled", bool, None)    : 终止信号（是否已处理；False -> dispatcher 降级）
    """
    gw = _get_gateway(context, params)  # F 项：透传 params 供 L1 per-request 内核选择
    if gw is None:
        yield ("handled", False, None)
        return
    timeout_s = params.get("kernel_timeout_s") or None
    target_profile = _resolve_dsh_profile(context, params)

    # V1-3 修复（2026-09-15）：入口即下发可见反馈，兑现 T1 裁决「首个可见反馈事件 P95≤1s」。
    # 背景：dsh 内核冷启实测 9-14s，此前首个内核事件到达前本生成器一帧不发
    # （GATEWAY_HEARTBEAT_S=20s 的心跳仅重置后端空闲计时器、不下行，见本文件 :495 / :547-550），
    # 前端表现为"8s 内零帧且无任何错误帧" → 用户体感"后端断联"，
    # V1 一键验收（2026-09-13）据此判出唯一 RED 项。
    # 该 notice 经 ws_server.send_chunk 以 {"type":"notice","data":...} 下发，前端状态区渲染，不进正文。
    # 载荷沿用既有 notice JSON 契约（phase/status/message；前端 agentEvents.parseEventData
    # 优先取 message 作展示文本）；phase 用 "starting" 以区别于内核首个 status 事件的 "started"。
    yield (
        "emit",
        json.dumps(
            {"phase": "starting", "status": "running", "message": "正在启动内核并分析任务…"},
            ensure_ascii=False,
        ),
        "notice",
    )

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    # C-P1：opt-in context bundle（默认关）——仅复杂任务且开关开启时注入 persona/记忆/偏好
    bundle = ""
    from backend.handlers.chat_paths.helpers import _is_complex_task

    if _is_complex_task(message) and await _kernel_context_bundle_enabled(context):
        try:
            from backend.handlers._chat_context_anchor import build_anchor_async

            bundle = ((await build_anchor_async(context, message)) or "")[:_KERNEL_CONTEXT_BUNDLE_MAX]
        except Exception:  # noqa: BLE001
            logger.warning("kernel context bundle 构建失败，按无 bundle 继续", exc_info=True)
            bundle = ""
    kernel_prompt = _build_kernel_prompt(message, params, context_bundle=bundle)

    def produce() -> None:
        name = _kernel_source_name(gw)
        run_id = _observe_kernel_start(name, context)
        local_events: list[Any] = []
        success = False
        try:
            for ev in gw.submit_stream(kernel_prompt, timeout_s=timeout_s, profile=target_profile):
                local_events.append(ev)
                if getattr(ev, "kind", "") == "result":
                    success = True
                loop.call_soon_threadsafe(queue.put_nowait, ev)
        except Exception as exc:  # 内核异常：归一化为 error 事件（与 _drain_events 一致）
            logger.warning("gateway submit_stream failed: %s", exc, exc_info=True)
            from services.agent_gateway.protocol import AgentRunEvent

            err = AgentRunEvent(
                kind="error",
                payload={"error_kind": "internal", "message": str(exc)[:300]},
            )
            local_events.append(err)
            loop.call_soon_threadsafe(queue.put_nowait, err)
        finally:
            # 内核执行结束：用量记录 + trace finish
            result_text = _collect_kernel_result_text(local_events)
            _record_kernel_usage(name, context, message, result_text, success, gw=gw, events=local_events)
            _observe_kernel_finish(run_id, context, "completed" if success else "failed")
            loop.call_soon_threadsafe(queue.put_nowait, _GW_SENTINEL)

    producer_fut = loop.run_in_executor(None, produce)
    handled = False
    # content_started：是否已产出「用户可感知的实质进展帧」（delta 正文 / tool / confirm）。
    # 与 handled 分开：handled 由 _event_to_chunk 决定、用于最终回落判定（内核是否接过这轮）；
    # 而内核的 status(started) 事件只是"已启动"通知，不算实质内容。2026-09-16 真跑实测发现：
    # dsh 是伪流式，started(0.07s) 之后到正文(77~124s) 之间零事件，若用 handled 守卫等待提示，
    # started 会把 handled 提前置真 → 短路整个执行期的"内核仍在思考"notice → 用户再次体感断联。
    # 故等待提示改由 content_started 守卫：正文/工具/审批帧到达前，每心跳周期持续下发等待提示。
    content_started = False
    seen_events: list[Any] = []
    waited_s = 0.0
    try:
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=GATEWAY_HEARTBEAT_S)
            except asyncio.TimeoutError:
                # 等待期进度反馈（2026-09-11）：dsh 是伪流式（subprocess.run 阻塞
                # 到进程结束才发单个 delta），9.8~14.6s 内前端毫无反馈。原心跳只
                # 重置空闲计时器、不产出任何可见内容；此处同时下发 notice，让等待
                # 可感知。一旦已有实质内容产出（content_started）就不再刷屏。
                waited_s += GATEWAY_HEARTBEAT_S
                if not content_started:
                    yield ("emit", _kernel_waiting_notice(waited_s), "notice")
                yield ("heartbeat", None, None)
                continue
            if ev is _GW_SENTINEL:
                break
            seen_events.append(ev)
            chunk, handled = _event_to_chunk(ev, handled)
            if chunk is not None:
                # 仅「正文/工具/审批」算实质进展；status notice("notice") 与错误 notice 不计，
                # 否则内核刚发 started 就过早判定"有内容"，重新引入 77s 静默。
                if chunk[1] in ("chunk", "tool", "confirm"):
                    content_started = True
                if chunk[1] == "error" and _is_fatal_kernel_error(_event_error_message(ev)):
                    # 致命内核错误：改以 notice 下发（error chunk 会终止前端流，
                    # 见 _fatal_error_as_notice 注释），随后回落壳内管家作答
                    yield ("emit", _fatal_error_as_notice(chunk[0]), "notice")
                else:
                    yield ("emit", chunk[0], chunk[1])
        if _fatal_kernel_failure(seen_events):
            # 致命内核错误（订阅过期/鉴权失效/配额耗尽）：内核不可能成功，
            # 错误已以 notice 告知用户，此处回落壳内管家继续作答。
            yield ("handled", False, "kernel_error")
        else:
            yield ("handled", handled, None)
    finally:
        try:
            await producer_fut
        except Exception:
            logger.exception("gateway_path 等待内核生产者协程结束时失败")
            pass


async def run_gateway_path(
    context,
    message,
    params,
    send_chunk,
) -> bool:
    """网关执行路径。返回是否"已处理"（True=产出 chunk，False=回落）。

    网关不可用或空响应时返回 False → dispatcher 落到默认路径。
    """
    gw = _get_gateway(context, params)  # F 项：透传 params 供 L1 per-request 内核选择
    if gw is None:
        return False
    timeout_s = params.get("kernel_timeout_s") or None
    target_profile = _resolve_dsh_profile(context, params)  # M4：params.kernel 映射 profile（默认 headless）

    kernel_prompt = _build_kernel_prompt(message, params)
    events = await asyncio.to_thread(_drain_events, gw, kernel_prompt, timeout_s, target_profile, context)
    if not events:
        return False
    # 致命内核错误（订阅/鉴权/配额）：error 改以 notice 下发（不终止前端流），
    # 并返回 False 回落壳内管家（见 _fatal_error_as_notice 注释）
    fatal = _fatal_kernel_failure(events)
    handled = False
    try:
        for ev in events:
            chunk, handled = _event_to_chunk(ev, handled)
            if chunk is None:
                continue
            if fatal and chunk[1] == "error":
                await _safe_chunk(send_chunk, _fatal_error_as_notice(chunk[0]), chunk_type="notice")
            else:
                await _safe_chunk(send_chunk, chunk[0], chunk_type=chunk[1])
    except Exception as exc:
        logger.warning("gateway path emit failed: %s", exc, exc_info=True)
        return False
    if fatal:
        return False
    return handled


async def run_gateway_resume(context, params, send_chunk) -> bool:
    """chat.resume 网关版：**仅当内核声明 resume 能力时**才在既有会话上续跑。

    批5 单一真源（2026-09-18）：能否续跑由 ``gw.capabilities()["resume"]``
    唯一决定（经 ``protocol.supports_resume`` 读取；前端读同一处声明，见
    ``kernel.status.details.capabilities.resume``）。

    - 声明 ``resume=True``：走 ``gw.resume(session_ref, prompt)`` 真续跑。
    - 声明 ``resume=False``（如 DSH / Codex 的默认值）：**直接返回 False**，
      让 ``chat.resume`` 回落到 butler 的 ``run_pydantic_agent_resume``——
      后者对无挂起 checkpoint 的会话如实回 ``no_pending_task``。

    为什么不"退化为 submit"照样返回 True（旧行为）：那会让一次
    "续跑"实际变成**全新会话重跑**（重复执行 + 重复计费），且与 checkpoint
    语义错位；前端 ChatView 因此不得不硬编码"内核不能 resume"来绕开它。
    把判据收敛到能力声明后，两端看到的是同一个事实，无需各自推断。

    返回是否"已处理"（True=产出 chunk，False=回落默认续跑路径）。
    """
    gw = _get_gateway(context, params)  # F 项：透传 params 供 L1 per-request 内核选择
    if gw is None:
        return False
    if not _supports_resume(gw):
        logger.info(
            "gateway resume skipped: kernel %r declares no resume capability; falling back",
            getattr(gw, "name", "?"),
        )
        return False
    message = str(params.get("message") or params.get("resume_message") or "")
    session_ref = str(params.get("session_id") or "")
    timeout_s = params.get("kernel_timeout_s") or None
    target_profile = _resolve_dsh_profile(context, params)  # M4：params.kernel 映射 profile（默认 headless）

    events = await asyncio.to_thread(_resume_drain, gw, session_ref, message, timeout_s, target_profile, context)
    if not events:
        return False
    # 致命内核错误（订阅/鉴权/配额）：error 改以 notice 下发，并回落壳内管家
    fatal = _fatal_kernel_failure(events)
    handled = False
    try:
        for ev in events:
            chunk, handled = _event_to_chunk(ev, handled)
            if chunk is None:
                continue
            if fatal and chunk[1] == "error":
                await _safe_chunk(send_chunk, _fatal_error_as_notice(chunk[0]), chunk_type="notice")
            else:
                await _safe_chunk(send_chunk, chunk[0], chunk_type=chunk[1])
    except Exception as exc:
        logger.warning("gateway resume emit failed: %s", exc, exc_info=True)
        return False
    if fatal:
        return False
    return handled


def _resume_drain(gw, session_ref: str, message: str, timeout_s: int | None, profile: str | None = None, context: Any = None):
    """与 _drain_events 等价，但走 gw.resume（支持会话续跑）。

    A 项（观察期埋点）：内核执行前后接 trace + 用量记录（source=kernel:<name>）。
    """
    from services.agent_gateway.protocol import AgentRunEvent

    name = _kernel_source_name(gw)
    run_id = _observe_kernel_start(name, context)
    events: list[Any] = []
    success = False
    try:
        for ev in gw.resume(session_ref, message, timeout_s=timeout_s, profile=profile):
            events.append(ev)
            if getattr(ev, "kind", "") == "result":
                success = True
    except Exception as exc:
        logger.warning("gateway resume failed: %s", exc, exc_info=True)
        events.append(
            AgentRunEvent(
                kind="error",
                payload={"error_kind": "internal", "message": str(exc)[:300]},
            )
        )
        success = False
    # 内核执行结束：用量记录 + trace finish
    result_text = _collect_kernel_result_text(events)
    _record_kernel_usage(name, context, message, result_text, success, gw=gw, events=events)
    _observe_kernel_finish(run_id, context, "completed" if success else "failed")
    return events
