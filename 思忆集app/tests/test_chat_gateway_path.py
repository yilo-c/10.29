"""M2b 契约测试：chat 切网关灰度（gateway_path + 三级路由）。

验证：
- run_gateway_path 把 AgentRunEvent 增量流映射为前端 chunk（文本 / 错误）。
- 网关不可用（None / type=none）/ 空响应时不产出 → dispatcher 自然回落。
- 三级路由 _use_gateway_route：L1 params.kernel 强制 / L2 chat_route=gateway
  灰度 / L3 缺省 legacy（Flip 回翻后缺省走壳内管家路径，仅显式 gateway 走内核）。
- run_gateway_resume 走 gw.resume 产出文本；内核未声明 resume 能力时不伪造续跑
  （批5 单一真源：判据 = ``capabilities()["resume"]``）。

隔离说明：本测试不依赖真实内核（DSH/ClaudeCode），用 FakeGateway 直接驱动
protocol.AgentRunEvent 流，验证功能路径层与网关端口的契约。
"""

from __future__ import annotations

import json
import types

from _fake_settings import FakeSettingsRepository
from core.settings_keys import KEY_LLM_API_KEY


from backend.handlers.chat_paths import (
    _use_gateway_route,
    run_gateway_path,
    run_gateway_path_progressive,
    run_gateway_resume,
)
from backend.handlers.chat_paths.gateway_path import _event_to_chunk
from services.agent_gateway.protocol import AgentRunEvent
from services.agent_gateway.routing import resolve_dsh_profile


class _Collector:
    def __init__(self):
        self.chunks: list[str] = []
        self.errors: list[str] = []

    async def __call__(self, text, chunk_type=None):
        if chunk_type == "error":
            self.errors.append(text)
        else:
            self.chunks.append(text)


class _FakeGateway:
    """按预置事件列表驱动 submit_stream / resume 的桩。

    批5（2026-09-18）：续跑能力改为**显式声明**。``run_gateway_resume`` 现以
    ``capabilities()["resume"]`` 为唯一真源（与真实适配器一致），因此桩必须
    声明能力，否则会被判为"不能续跑"而回落。默认 True 保持本文件既有
    resume 用例的语义（它们验证的就是"可续跑内核"的路径）。
    """

    def __init__(self, events, name="dsh", resume_capable=True):
        self._events = events
        self.name = name
        self._resume_capable = resume_capable
        self.last_prompt = None
        self.last_session = None

    def capabilities(self):
        return {"stream": False, "tool": False, "confirm": False, "resume": self._resume_capable, "cancel": False}

    def submit_stream(self, prompt, timeout_s=None, profile=None):
        self.last_prompt = prompt
        self.last_profile = profile
        for ev in self._events:
            yield ev

    def resume(self, session_ref, prompt, timeout_s=None, profile=None):
        self.last_session = session_ref
        self.last_prompt = prompt
        self.last_profile = profile
        for ev in self._events:
            yield ev


def _ctx(agent_gateway=None, chat_route="legacy"):
    # 2026-09-18：原来是 `SimpleNamespace(get_setting=lambda ...)`——**只有读、
    # 没有 set_setting**。后果：`load_providers` 播种默认 provider 时
    # `set_setting` 抛 AttributeError → 被 except 吞掉并记"播种失败" → provider
    # 列表为空 → chat_paths/helpers.py 预检判定"未配置 AI 服务" → 本文件
    # test_chat_send_implicit_downgrade_no_notice 收到一条 not_configured notice 而红。
    # 改用共享假仓储（回落 SETTINGS_DEFAULTS 真源，且读写齐备），并显式声明
    # 已配置 llm_api_key，让预检继续正常路由。
    settings = FakeSettingsRepository({"chat_route": chat_route, KEY_LLM_API_KEY: "test-llm-key-0000"})
    return types.SimpleNamespace(agent_gateway=agent_gateway, settings_repository=settings)


def _text_events(text="你好，这是内核回复"):
    return [
        AgentRunEvent(kind="status", payload={"phase": "started"}),
        AgentRunEvent(kind="delta", payload={"text": text}),
        AgentRunEvent(kind="result", payload={"text": text, "usage": {"total": 1}}),
    ]


def _error_events():
    return [
        AgentRunEvent(kind="status", payload={"phase": "started"}),
        AgentRunEvent(kind="error", payload={"error_kind": "unavailable", "message": "内核不可用"}),
    ]


def _fatal_subscription_events():
    """致命内核错误：火山 AgentPlan 订阅过期（2026-09-09 实际案例）。"""
    return [
        AgentRunEvent(kind="status", payload={"phase": "started"}),
        AgentRunEvent(
            kind="error",
            payload={
                "error_kind": "internal",
                "message": "(2124449892) does not have a valid AgentPlan subscription, or your subscription has expired. Please visit https://console.volcengine.com",
            },
        ),
    ]


async def test_gateway_path_emits_text():
    gw = _FakeGateway(_text_events())
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    handled = await run_gateway_path(ctx, "hi", {}, c)
    assert handled is True
    # P0 渐进式修复后 status(started) 也 emit 为 notice（即时反馈 + 重置空闲计时器）
    # 2026-09-11：notice 载荷补 status/message 字段供前端渲染可读文本，
    # 故按语义断言（解析 JSON 取 phase），不再绑定序列化细节。
    assert json.loads(c.chunks[0])["phase"] == "started"
    assert c.chunks[1] == "你好，这是内核回复"
    assert c.errors == []
    assert gw.last_prompt == "hi"


async def test_gateway_path_error_chunk():
    gw = _FakeGateway(_error_events())
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    handled = await run_gateway_path(ctx, "hi", {}, c)
    assert handled is True
    assert c.errors and "unavailable" in c.errors[0]


async def test_gateway_path_none_gateway_yields_nothing():
    ctx = _ctx(agent_gateway=None)
    c = _Collector()
    handled = await run_gateway_path(ctx, "hi", {}, c)
    assert handled is False
    assert c.chunks == [] and c.errors == []


class _TypedCollector:
    def __init__(self):
        self.items: list[tuple[str, str | None]] = []  # (text, chunk_type)

    async def __call__(self, text, chunk_type=None):
        self.items.append((text, chunk_type))


async def test_emit_kernel_degraded_notice_sends_notice():
    # 显式降级（用户主动要求内核却落空）→ chat 内必发 notice（D5 / 契约 §2.5）
    from backend.handlers._chat_memory import _emit_kernel_degraded_notice, _KERNEL_NOTICE_THROTTLE

    _KERNEL_NOTICE_THROTTLE.clear()
    c = _TypedCollector()
    await _emit_kernel_degraded_notice(c, "dsh_missing")
    notices = [it for it in c.items if it[1] == "notice"]
    assert notices, "显式降级必须发 notice chunk"
    payload = json.loads(notices[0][0])
    assert payload["event"] == "kernel_degraded"
    assert payload["from"] == "gateway" and payload["to"] == "legacy"
    assert payload["reason"] == "dsh_missing"


async def test_emit_kernel_degraded_notice_throttled():
    # 同原因 5 分钟内只发一次，避免每次请求刷屏
    from backend.handlers._chat_memory import _emit_kernel_degraded_notice, _KERNEL_NOTICE_THROTTLE

    _KERNEL_NOTICE_THROTTLE.clear()
    c = _TypedCollector()
    await _emit_kernel_degraded_notice(c, "dsh_missing")
    await _emit_kernel_degraded_notice(c, "dsh_missing")
    notices = [it for it in c.items if it[1] == "notice"]
    assert len(notices) == 1


async def test_chat_send_explicit_kernel_downgrade_emits_notice(monkeypatch):
    # 分发级：params.kernel 显式降级 → chat 内出现降级 notice
    import backend.handlers._chat_memory as cm

    async def _fake_gw_false(ctx, msg, params, sc):
        return False

    async def _noop_messages(ctx, msg, params):
        return []

    async def _noop_gen(*a, **k):
        return
        yield

    class _Reg:
        def __init__(self):
            self.handlers = {}

        def register(self, name, fn):
            self.handlers[name] = fn

        def register_with_schema(self, name, fn, schema=None, **kw):
            # S2 批次1 契约同步：memory.* 已升级带 schema 注册，桩须同调用面
            self.handlers[name] = fn

    monkeypatch.setattr(cm, "run_gateway_path", _fake_gw_false)
    monkeypatch.setattr(cm, "build_messages", _noop_messages)
    monkeypatch.setattr(cm, "run_pydantic_agent_path", _noop_gen)
    monkeypatch.setattr(cm, "run_simple_stream_path", _noop_gen)
    cm._KERNEL_NOTICE_THROTTLE.clear()
    c = _TypedCollector()
    ctx = _ctx(agent_gateway=None, chat_route="gateway")
    reg = _Reg()
    cm.register_chat_memory(reg, ctx)
    handler = reg.handlers["chat.send"]
    gen = handler(send_chunk=c, message="hi", kernel="deep")
    async for _ in gen:
        pass
    notices = [it for it in c.items if it[1] == "notice"]
    assert notices, "params.kernel 显式降级必须发 notice"


async def test_chat_send_implicit_downgrade_no_notice(monkeypatch):
    # 分发级：默认路由降级（无 params.kernel）→ 不发 chat notice，仅徽章（隐式降级）
    import backend.handlers._chat_memory as cm

    async def _fake_gw_false(ctx, msg, params, sc):
        return False

    async def _noop_messages(ctx, msg, params):
        return []

    async def _noop_gen(*a, **k):
        return
        yield

    class _Reg:
        def __init__(self):
            self.handlers = {}

        def register(self, name, fn):
            self.handlers[name] = fn

        def register_with_schema(self, name, fn, schema=None, **kw):
            # S2 批次1 契约同步：memory.* 已升级带 schema 注册，桩须同调用面
            self.handlers[name] = fn

    monkeypatch.setattr(cm, "run_gateway_path", _fake_gw_false)
    monkeypatch.setattr(cm, "build_messages", _noop_messages)
    monkeypatch.setattr(cm, "run_pydantic_agent_path", _noop_gen)
    monkeypatch.setattr(cm, "run_simple_stream_path", _noop_gen)
    cm._KERNEL_NOTICE_THROTTLE.clear()
    c = _TypedCollector()
    ctx = _ctx(agent_gateway=None, chat_route="gateway")
    reg = _Reg()
    cm.register_chat_memory(reg, ctx)
    handler = reg.handlers["chat.send"]
    gen = handler(send_chunk=c, message="hi")
    async for _ in gen:
        pass
    notices = [it for it in c.items if it[1] == "notice"]
    assert not notices, "隐式降级（默认路由）不应发 chat notice"


async def test_gateway_path_type_none_yields_nothing():
    gw = _FakeGateway(_text_events(), name="none")
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    handled = await run_gateway_path(ctx, "hi", {}, c)
    assert handled is False
    assert c.chunks == [] and c.errors == []


async def test_gateway_path_empty_events_yields_nothing():
    gw = _FakeGateway([])
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    handled = await run_gateway_path(ctx, "hi", {}, c)
    assert handled is False
    assert c.chunks == [] and c.errors == []


async def test_gateway_path_resume_emits_text():
    gw = _FakeGateway(_text_events("续跑结果"))
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    params = {"session_id": "s1", "message": "继续"}
    handled = await run_gateway_resume(ctx, params, c)
    assert handled is True
    assert json.loads(c.chunks[0])["phase"] == "started"
    assert c.chunks[1] == "续跑结果"
    assert gw.last_session == "s1"


async def test_gateway_path_resume_declined_when_kernel_lacks_resume_capability():
    """批5 单一真源：内核未声明 resume 能力 → run_gateway_resume 不伪造续跑。

    红→绿 PoC（2026-09-18）：
      红 —— 修复前 run_gateway_resume 无条件调 ``gw.resume``（DSH 的 resume 实为
            全新 submit 会话），本断言 ``handled is False`` 失败；
      绿 —— 修复后读 ``capabilities()["resume"]`` 判 False 即返回 False，
            让 chat.resume 回落 butler 续跑（无 checkpoint 时如实回 no_pending_task）。

    历史事实（反转前勘察结论）：旧 docstring 声称"在既有会话上续跑（无会话能力的
    网关退化为 submit）"，把"退化为 submit"当作可接受的续跑——与前端 ChatView
    "DSH 无法续跑"的硬编码结论互相矛盾，且会让一次续跑变成新会话重跑（重复执行）。
    """
    gw = _FakeGateway(_text_events("不该出现的续跑结果"), resume_capable=False)
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    handled = await run_gateway_resume(ctx, {"session_id": "s1", "message": "继续"}, c)
    assert handled is False, "未声明 resume 能力的内核不得被当作可续跑"
    assert c.chunks == [], "不得产出任何 chunk（不得伪造一次续跑）"
    assert gw.last_session is None, "不得调用 gw.resume（否则实为新会话重跑）"


async def test_gateway_path_resume_missing_capabilities_is_conservative():
    """无 ``capabilities()`` 的裸适配器 → 保守判"不可续跑"，而非乐观放行。"""

    class _Bare:
        name = "bare"

        def resume(self, session_ref, prompt, timeout_s=None, profile=None):  # pragma: no cover
            raise AssertionError("未声明 resume 能力时不得调用 resume")

    handled = await run_gateway_resume(_ctx(agent_gateway=_Bare()), {"session_id": "s", "message": "m"}, _Collector())
    assert handled is False


def test_resolve_dsh_profile_default_fallback():
    # M4：params.kernel 成本分级映射到 profile（契约 §2.4）
    cfg = json.dumps({"dsh": {"profile": "headless"}})
    settings = types.SimpleNamespace(get_setting=lambda key, default=None: cfg if key == "agent_kernel" else default)
    ctx = types.SimpleNamespace(settings_repository=settings)
    assert resolve_dsh_profile(ctx, {}) == "headless"
    assert resolve_dsh_profile(ctx, {"kernel": "deep"}) == "siyi-deep"
    assert resolve_dsh_profile(ctx, {"kernel": "fast"}) == "siyi-fast"
    # 非成本分级键回默认 dsh.profile，不改动观察期既有路径
    assert resolve_dsh_profile(ctx, {"kernel": "shell"}) == "headless"
    assert resolve_dsh_profile(ctx, {"kernel": "legacy"}) == "headless"
    assert resolve_dsh_profile(ctx, {"kernel": "nonsense"}) == "headless"


def test_resolve_dsh_profile_from_settings_override():
    cfg = json.dumps(
        {
            "dsh": {"profile": "custom-default"},
            "profiles": {"deep": {"dsh_profile": "my-deep"}},
        }
    )
    settings = types.SimpleNamespace(get_setting=lambda key, default=None: cfg if key == "agent_kernel" else default)
    ctx = types.SimpleNamespace(settings_repository=settings)
    assert resolve_dsh_profile(ctx, {"kernel": "deep"}) == "my-deep"
    assert resolve_dsh_profile(ctx, {}) == "custom-default"


async def test_gateway_path_passes_kernel_profile():
    # M4：params.kernel=deep → submit_stream 收到 profile="siyi-deep"；默认 → headless
    agent_kernel_cfg = json.dumps(
        {
            "dsh": {"profile": "headless"},
            "profiles": {
                "fast": {"dsh_profile": "siyi-fast"},
                "deep": {"dsh_profile": "siyi-deep"},
            },
        }
    )
    settings = types.SimpleNamespace(get_setting=lambda key, default=None: agent_kernel_cfg if key == "agent_kernel" else default)

    gw = _FakeGateway(_text_events())
    ctx = types.SimpleNamespace(agent_gateway=gw, settings_repository=settings)
    c = _Collector()
    handled = await run_gateway_path(ctx, "hi", {"kernel": "deep"}, c)
    assert handled is True
    assert gw.last_profile == "siyi-deep"

    gw2 = _FakeGateway(_text_events())
    ctx2 = types.SimpleNamespace(agent_gateway=gw2, settings_repository=settings)
    await run_gateway_path(ctx2, "hi", {}, c)
    assert gw2.last_profile == "headless"  # 默认回 dsh.profile，不引入行为变化


async def test_gateway_path_resume_passes_kernel_profile():
    agent_kernel_cfg = json.dumps(
        {
            "dsh": {"profile": "headless"},
            "profiles": {"deep": {"dsh_profile": "siyi-deep"}},
        }
    )
    settings = types.SimpleNamespace(get_setting=lambda key, default=None: agent_kernel_cfg if key == "agent_kernel" else default)
    gw = _FakeGateway(_text_events("续跑结果"))
    ctx = types.SimpleNamespace(agent_gateway=gw, settings_repository=settings)
    c = _Collector()
    params = {"session_id": "s1", "message": "继续", "kernel": "deep"}
    handled = await run_gateway_resume(ctx, params, c)
    assert handled is True
    assert gw.last_profile == "siyi-deep"
    assert gw.last_session == "s1"


def test_use_gateway_route_kernel_forced():
    gw = _FakeGateway(_text_events())
    ctx = _ctx(agent_gateway=gw, chat_route="legacy")
    assert _use_gateway_route(ctx, {"kernel": "dsh"}) is True


def test_use_gateway_route_setting_gateway():
    gw = _FakeGateway(_text_events())
    ctx = _ctx(agent_gateway=gw, chat_route="gateway")
    assert _use_gateway_route(ctx, {}) is True


def test_use_gateway_route_default_legacy():
    gw = _FakeGateway(_text_events())
    ctx = _ctx(agent_gateway=gw, chat_route="legacy")
    assert _use_gateway_route(ctx, {}) is False


def test_use_gateway_route_no_settings_repo():
    ctx = types.SimpleNamespace(agent_gateway=_FakeGateway(_text_events()))
    # Flip 回翻（2026-09）：无 settings_repository 时缺省 legacy（不崩溃）
    assert _use_gateway_route(ctx, {}) is False
    # 但显式 kernel 仍强制
    assert _use_gateway_route(ctx, {"kernel": True}) is True


def test_use_gateway_route_no_gateway_is_safe():
    # 路由判定只看 chat_route 设置（与网关可用性解耦）；网关为 None 时
    # 路由仍按设置返回 True（不崩溃），实际可用性由 run_gateway_path 兜底。
    ctx = _ctx(agent_gateway=None, chat_route="gateway")
    assert _use_gateway_route(ctx, {}) is True


# ── P0 渐进式管道（2026-08-16 修复 chat.send 空闲超时）─────────────


class _SlowFakeGateway(_FakeGateway):
    """事件间隔超 GATEWAY_HEARTBEAT_S 的慢内核（模拟 DSH 56s+）。"""

    def submit_stream(self, prompt, timeout_s=None, profile=None):
        self.last_prompt = prompt
        import time

        for ev in self._events:
            yield ev
            time.sleep(0.05)  # 短间隔即可触发渐进式管道（真实场景 56s+）


async def test_progressive_path_emits_incrementally():
    """渐进式：逐事件 emit 信号 + handled 终止信号。"""
    from backend.handlers.chat_paths import run_gateway_path_progressive

    gw = _FakeGateway(_text_events())
    ctx = _ctx(agent_gateway=gw)
    signals = []
    async for sig in run_gateway_path_progressive(ctx, "hi", {}):
        signals.append(sig)
    # emit: 入口 starting notice + 内核 status(started) notice + delta 文本；handled: True
    emits = [s for s in signals if s[0] == "emit"]
    notice_emits = [s for s in emits if s[2] == "notice"]
    assert notice_emits, "应发出内核状态 notice"
    # 2026-09-15（V1-3）：入口新增一条 phase="starting" 的可见反馈（首帧 ≤1s，
    # 修 V1 一键验收的唯一 RED「8s 零帧静默」），故**有意**变更信号顺序：
    # 不再假定「首个 notice 即 started」，改按 phase 语义查找。
    phases = [json.loads(s[1])["phase"] for s in notice_emits]
    assert phases[0] == "starting", f"入口应最先发 starting notice（首帧可见反馈），实际={phases}"
    assert "started" in phases, f"内核 status 事件的 started notice 仍须发出，实际={phases}"
    assert ("emit", "你好，这是内核回复", "chunk") in emits
    assert signals[-1] == ("handled", True, None)


async def test_progressive_path_none_gateway_signals_unhandled():
    from backend.handlers.chat_paths import run_gateway_path_progressive

    ctx = _ctx(agent_gateway=None)
    signals = [sig async for sig in run_gateway_path_progressive(ctx, "hi", {})]
    assert signals == [("handled", False, None)]


async def test_progressive_path_error_event_emits_error():
    from backend.handlers.chat_paths import run_gateway_path_progressive

    gw = _FakeGateway(_error_events())
    ctx = _ctx(agent_gateway=gw)
    signals = [sig async for sig in run_gateway_path_progressive(ctx, "hi", {})]
    emits = [s for s in signals if s[0] == "emit"]
    error_emits = [s for s in emits if s[2] == "error"]
    assert error_emits and "unavailable" in error_emits[0][1]
    assert signals[-1] == ("handled", True, None)


async def test_progressive_path_heartbeat_on_idle(monkeypatch):
    """内核长时间无事件时 yield 心跳信号（重置 dispatch_stream 空闲计时器）。"""
    import backend.handlers.chat_paths.gateway_path as gp
    from backend.handlers.chat_paths import run_gateway_path_progressive

    class _IdleGateway(_FakeGateway):
        def submit_stream(self, prompt, timeout_s=None, profile=None):
            import time

            self.last_prompt = prompt
            # 先静默 > 心跳间隔，再产出结果
            time.sleep(gp.GATEWAY_HEARTBEAT_S + 0.3)
            yield AgentRunEvent(kind="delta", payload={"text": "late"})

    # 缩短心跳间隔加速测试
    monkeypatch.setattr(gp, "GATEWAY_HEARTBEAT_S", 0.1)
    gw = _IdleGateway([])
    ctx = _ctx(agent_gateway=gw)
    signals = [sig async for sig in run_gateway_path_progressive(ctx, "hi", {})]
    heartbeats = [s for s in signals if s[0] == "heartbeat"]
    assert len(heartbeats) >= 1, "静默期必须产出心跳信号"
    assert ("emit", "late", "chunk") in signals
    assert signals[-1] == ("handled", True, None)


async def test_progressive_path_waiting_notice_survives_started(monkeypatch):
    """回归（2026-09-16 真跑实测 P0）：内核先吐 status(started)，再长时间静默，
    最后才出 delta —— 即 dsh 伪流式的真实节奏（started→77~124s→正文）。

    旧实现用 `if not handled` 守卫等待提示，而 status(started) 事件会把 handled
    提前置真 → 执行期的「内核仍在思考」notice 全被短路 → 用户再次体感断联。
    修复后等待提示改由 content_started 守卫：只有 delta/tool/confirm 才算实质进展，
    故 started 之后的静默期必须**持续**发出 waiting notice。
    """
    import backend.handlers.chat_paths.gateway_path as gp

    class _StartedThenSilentGateway(_FakeGateway):
        def submit_stream(self, prompt, timeout_s=None, profile=None):
            import time

            self.last_prompt = prompt
            # 1) started 先到（旧代码在这里就把 handled 置真）
            yield AgentRunEvent(kind="status", payload={"phase": "started"})
            # 2) 静默 > 心跳间隔：模拟内核长执行
            time.sleep(gp.GATEWAY_HEARTBEAT_S + 0.3)
            # 3) 最后才出正文
            yield AgentRunEvent(kind="delta", payload={"text": "终于有结果了"})

    monkeypatch.setattr(gp, "GATEWAY_HEARTBEAT_S", 0.1)
    gw = _StartedThenSilentGateway([])
    ctx = _ctx(agent_gateway=gw)
    signals = [sig async for sig in run_gateway_path_progressive(ctx, "hi", {})]
    emits = [s for s in signals if s[0] == "emit"]
    # started/starting notice 本身不算 waiting notice
    waiting = [s for s in emits if s[2] == "notice" and json.loads(s[1]).get("phase") not in ("started", "starting")]
    assert waiting, "started 之后的静默期必须继续下发 waiting notice（旧 handled 守卫会误短路）"
    # waiting notice 文案带累计等待秒数（_kernel_waiting_notice）
    assert "仍在执行" in json.loads(waiting[0][1])["message"], waiting[0]
    # 心跳信号同样存在（重置后端空闲计时器）
    assert [s for s in signals if s[0] == "heartbeat"], "静默期必须产出心跳信号"
    assert ("emit", "终于有结果了", "chunk") in emits
    assert signals[-1] == ("handled", True, None)


async def test_progressive_path_waiting_notice_stops_after_content(monkeypatch):
    """实质正文到达后，waiting notice 不再刷屏（content_started 守卫的另一侧）。"""
    import backend.handlers.chat_paths.gateway_path as gp

    class _FastThenIdleGateway(_FakeGateway):
        def submit_stream(self, prompt, timeout_s=None, profile=None):
            import time

            self.last_prompt = prompt
            yield AgentRunEvent(kind="status", payload={"phase": "started"})
            yield AgentRunEvent(kind="delta", payload={"text": "第一段"})
            # 正文后静默：应有心跳重置计时器，但不再发 waiting notice
            time.sleep(gp.GATEWAY_HEARTBEAT_S + 0.3)
            yield AgentRunEvent(kind="delta", payload={"text": "第二段"})

    monkeypatch.setattr(gp, "GATEWAY_HEARTBEAT_S", 0.1)
    gw = _FastThenIdleGateway([])
    ctx = _ctx(agent_gateway=gw)
    signals = [sig async for sig in run_gateway_path_progressive(ctx, "hi", {})]
    first_chunk_idx = next(i for i, s in enumerate(signals) if s[0] == "emit" and s[2] == "chunk")
    waiting_after = [s for s in signals[first_chunk_idx + 1 :] if s[0] == "emit" and s[2] == "notice" and json.loads(s[1]).get("phase") == "waiting"]
    assert not waiting_after, f"正文产出后不应再发 waiting notice：{waiting_after}"
    assert [s for s in signals if s[0] == "heartbeat"], "正文后静默期仍需心跳重置计时器"
    assert signals[-1] == ("handled", True, None)


# ── 致命内核错误回落（2026-09-09：dsh AgentPlan 订阅过期事故）──────────


async def test_fatal_subscription_error_falls_back_with_notice():
    """订阅过期类致命错误：error 改以 notice 下发（不终止前端流），handled=False 回落管家。"""
    from backend.handlers.chat_paths import run_gateway_path_progressive

    gw = _FakeGateway(_fatal_subscription_events())
    ctx = _ctx(agent_gateway=gw)
    signals = [sig async for sig in run_gateway_path_progressive(ctx, "hi", {})]
    emits = [s for s in signals if s[0] == "emit"]
    # 不得出现 error chunk（前端视作流级终止，降级回复将无法显示）
    assert not [s for s in emits if s[2] == "error"], "致命错误必须以 notice 下发，不得发 error chunk"
    # 错误以 notice 形式进入事件时间线，内容含订阅过期信息
    fatal_notices = [s for s in emits if s[2] == "notice" and "kernel_error" in s[1]]
    assert fatal_notices and "AgentPlan subscription" in fatal_notices[0][1]
    # 终止信号：未处理 + 携带 kernel_error 原因（供降级 notice 使用）
    assert signals[-1] == ("handled", False, "kernel_error")


async def test_fatal_error_with_result_stays_handled():
    """致命错误特征但已有成功 result（如中途报错后仍产出结果）：不回落。"""
    from backend.handlers.chat_paths import run_gateway_path_progressive

    events = _fatal_subscription_events() + [AgentRunEvent(kind="result", payload={"text": "ok"})]
    gw = _FakeGateway(events)
    ctx = _ctx(agent_gateway=gw)
    signals = [sig async for sig in run_gateway_path_progressive(ctx, "hi", {})]
    assert signals[-1] == ("handled", True, None)


async def test_gateway_path_fatal_error_returns_false():
    """非渐进式入口同样回落：订阅过期 → False（dispatcher 落到管家）。"""
    gw = _FakeGateway(_fatal_subscription_events())
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    handled = await run_gateway_path(ctx, "hi", {}, c)
    assert handled is False
    # 不发 error chunk；notice 携带 kernel_error 事件
    assert c.errors == []
    fatal_notices = [x for x in c.chunks if "kernel_error" in x]
    assert fatal_notices, "致命错误应以 notice 下发（含 kernel_error 事件）"


async def test_gateway_resume_fatal_error_returns_false():
    """chat.resume 网关版同样回落。"""
    gw = _FakeGateway(_fatal_subscription_events())
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    handled = await run_gateway_resume(ctx, {"session_id": "s1", "message": "hi"}, c)
    assert handled is False
    assert c.errors == []


# ── 跨轮上下文注入（2026-09-11）──────────────────────────────────────────────
# DSH 每次都是全新会话（DshGateway.resume 退化为 submit），网关路径此前只提交
# 原始 message → 跟进语在内核侧完全失忆。前端一直传着 context_messages
# （useChatStream.ts:73），此处验证它确实被拼进内核 prompt。


async def test_gateway_path_injects_conversation_history():
    """有 context_messages 时，内核 prompt 应包含历史与当前消息。"""
    gw = _FakeGateway(_text_events())
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    params = {
        "context_messages": [
            {"role": "user", "content": "帮我在桌面创建 txt 文件"},
            {"role": "assistant", "content": "需要先确认路径"},
        ]
    }
    await run_gateway_path(ctx, "再改一下", params, c)
    assert gw.last_prompt is not None
    assert "帮我在桌面创建 txt 文件" in gw.last_prompt
    assert "需要先确认路径" in gw.last_prompt
    assert "再改一下" in gw.last_prompt


async def test_gateway_path_prompt_unchanged_without_history():
    """无 context_messages 时保持原样提交，不引入任何包装。"""
    gw = _FakeGateway(_text_events())
    ctx = _ctx(agent_gateway=gw)
    c = _Collector()
    await run_gateway_path(ctx, "你好", {}, c)
    assert gw.last_prompt == "你好"


async def test_progressive_path_injects_conversation_history():
    """渐进式路径（chat.send 实际走的这条）同样注入历史。"""
    gw = _FakeGateway(_text_events())
    ctx = _ctx(agent_gateway=gw, chat_route="gateway")
    params = {"context_messages": [{"role": "user", "content": "第一条消息"}]}
    async for _ in run_gateway_path_progressive(ctx, "跟进消息", params):
        pass
    assert gw.last_prompt is not None
    assert "第一条消息" in gw.last_prompt
    assert "跟进消息" in gw.last_prompt


def test_build_kernel_prompt_filters_invalid_entries():
    """非法 role / 空内容的历史条目应被跳过，且不因脏数据抛异常。"""
    from backend.handlers.chat_paths.gateway_path import _build_kernel_prompt

    prompt = _build_kernel_prompt(
        "当前问题",
        {
            "context_messages": [
                {"role": "tool", "content": "工具输出（不应进入）"},
                {"role": "user", "content": "   "},
                "not-a-dict",
                {"role": "user", "content": "有效历史"},
            ]
        },
    )
    assert "有效历史" in prompt
    assert "工具输出" not in prompt
    assert "当前问题" in prompt


def test_build_kernel_prompt_tolerates_bad_param_types():
    """context_messages 非列表时直接返回原 message（降级安全）。"""
    from backend.handlers.chat_paths.gateway_path import _build_kernel_prompt

    assert _build_kernel_prompt("原消息", {"context_messages": "oops"}) == "原消息"
    assert _build_kernel_prompt("原消息", {}) == "原消息"


# ── 等待期进度反馈（2026-09-11）─────────────────────────────────────────────
# dsh 是伪流式（subprocess.run 阻塞到进程结束才发单个 delta），9.8~14.6s 内
# 前端毫无反馈。原心跳只重置空闲计时器、不产出可见内容。


def test_kernel_waiting_notice_carries_frontend_fields():
    """waiting notice 必须带 message/status —— 前端 parseEventData 据此渲染。

    agentEvents.ts:55 优先取 payload.message 作展示文本、:70 按 status
    决定 running 态；缺了 message 会 fallback 成显示原始 JSON。
    """
    from backend.handlers.chat_paths.gateway_path import _kernel_waiting_notice

    payload = json.loads(_kernel_waiting_notice(20.0))
    assert payload["phase"] == "waiting"
    assert payload["status"] == "running"
    assert "20" in payload["message"]


def test_status_notice_carries_readable_message():
    """status 事件同样应带可读 message（此前只发 phase，前端会显示原始 JSON）。"""
    ev = AgentRunEvent(kind="status", payload={"phase": "started"})
    chunk, _ = _event_to_chunk(ev, False)
    payload = json.loads(chunk[0])
    assert payload["phase"] == "started"
    assert payload["status"] == "running"
    assert payload["message"]


# ── L3 分层路由 auto 档（2026-09-11 点火）───────────────────────────────────
# 简单对话走壳内（~1.8s）、复杂任务走内核（~10-15s）——分流器复用
# _is_complex_task（动作动词 + 最小长度），与壳内工具触发同一判定。
# 注意：路由判定在分发层（_chat_memory），run_gateway_path 本身不含路由，
# 故本组直接断言 _use_gateway_route。


def _auto_ctx(gw=None):
    return _ctx(agent_gateway=gw, chat_route="auto")


def test_resolve_chat_route_accepts_auto():
    """resolve 层必须识别 auto——否则 L3 是死代码（本次回归实录：归一校验
    只认 gateway，auto 被静默折回 legacy）。"""
    from services.agent_gateway.routing import resolve_chat_route

    assert resolve_chat_route(_auto_ctx()) == "auto"
    assert resolve_chat_route(_ctx(chat_route="gateway")) == "gateway"
    assert resolve_chat_route(_ctx(chat_route="legacy")) == "legacy"
    assert resolve_chat_route(_ctx(chat_route="AUTO")) == "auto"


def test_auto_route_complex_message_routes_gateway():
    """auto + 含动作动词的真实请求 → 走内核。"""
    assert _use_gateway_route(_auto_ctx(), {}, "帮我在桌面创建一个txt文件") is True


def test_auto_route_simple_message_routes_butler():
    """auto + 纯聊天（无动作动词）→ 走壳内管家。"""
    assert _use_gateway_route(_auto_ctx(), {}, "你好呀") is False


def test_auto_route_missing_message_defaults_butler():
    """message 缺失时保守判为简单（走壳内），不误入内核。"""
    assert _use_gateway_route(_auto_ctx(), {}) is False


def test_auto_route_resume_message_participates():
    """resume 场景：params.message 参与复杂度分流。

    QUA-12 收紧后（阈值 4→12、剥离礼貌前缀后判），短请求"帮我修复…"已合理
    回落快档；此用例的本意是"resume 的 message 会参与分流判定"，故用一条
    真实的长复杂请求（含明确动作动词、剥离前缀后仍 ≥12）验证该链路仍然生效。
    """
    params = {"message": "帮我把刚才那段错误日志完整分析一下并给出修复方案"}
    assert _use_gateway_route(_auto_ctx(), params, str(params.get("message") or "")) is True


# ─────────────────────────────────────────────────────────────────────────────
# V1-3 回归（2026-09-15）：内核路径首帧可见反馈
# ─────────────────────────────────────────────────────────────────────────────


async def test_gateway_progressive_first_signal_is_visible_notice():
    """内核路径**首帧必须是可见 notice**，且早于一切内核事件。

    背景（V1 一键验收 2026-09-13 的唯一 RED 项）：
      dsh 内核冷启实测 9-14s，而 run_gateway_path_progressive 在首个内核事件
      到达前不 yield 任何 chunk；既有心跳（GATEWAY_HEARTBEAT_S=20s）"仅用于重置
      计时器、不发送内容"（见 gateway_path.py 模块 docstring）→ 前端表现为
      「8s 内零帧且连错误帧都没有」= 静默失败，用户体感"后端断联"。
      T1 已裁决对外承诺「首个可见反馈事件 P95 ≤1s」，该承诺此前在后端未实现。

    修法：生成器入口（gw 就绪后、produce 启动前）即
      yield ("emit", "正在启动内核并分析任务…", "notice")。

    断言：第一个信号必须形如 ("emit", 非空文本, "notice")；
    不可见信号（heartbeat 等）不得出现在它之前。
    """
    gw = _FakeGateway(_text_events())
    ctx = _ctx(agent_gateway=gw)
    agen = run_gateway_path_progressive(ctx, "hi", {})
    try:
        first = await agen.__anext__()
    finally:
        await agen.aclose()

    assert first[0] == "emit", f"首帧必须是可见 emit 信号，实际={first[0]!r}"
    assert first[2] == "notice", f"首帧 chunk_type 必须是 notice，实际={first[2]!r}"
    # 契约：notice 载荷为 JSON（phase/status/message），前端 agentEvents.parseEventData 取 message
    payload = json.loads(first[1])
    assert payload["phase"] == "starting", f"首帧 notice phase 应为 starting，实际={payload.get('phase')!r}"
    assert payload.get("message"), "首帧 notice 必须带可展示的 message 文本"


async def test_gateway_progressive_none_gateway_no_notice_and_fallback():
    """网关缺席时**不得**偷发 notice，必须保持既有回落契约。

    防止"入口即 notice"的修复误伤回落路径：gw 为 None 时应立刻
    yield ("handled", False, None)，交给 dispatcher 回落壳内路径。
    """
    ctx = _ctx(agent_gateway=None)
    agen = run_gateway_path_progressive(ctx, "hi", {})
    try:
        first = await agen.__anext__()
    finally:
        await agen.aclose()

    assert first == ("handled", False, None)
