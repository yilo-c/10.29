# -*- coding: utf-8 -*-
"""R2-E 流式 handler 形态契约棘轮（2026-09-17）。

**缺陷家族**：``MethodRegistry.dispatch_stream``（backend/method_registry.py:515-533）
拿到 ``handler(**params)`` 的返回值后直接 ``await stream.__anext__()``（``finally`` 里
再 ``stream.aclose()``），因此"会被 stream:true 调用"的 handler 必须让**返回值本身**
是 async generator。而 ``dispatch``（无 stream:true）对 async generator 返回值会降级为
"收集 chunk 列表"（method_registry.py:428-448）—— 两个契约互斥。

已知两个成员（今天都已修）：
  * ``backend/handlers/_job_hunter.py:249 analyze_stream`` —— 普通协程改 async generator；
  * ``backend/handlers/_inbox_info.py info_brief`` —— 原实现把 async generator 定义成
    内层 ``_stream()`` 再 ``return _stream()``，于是 stream:true 拿到的是**协程**，
    ``__anext__`` 必抛 AttributeError（一条 chunk 都到不了客户端）。现改为 sync 双形态
    入口：不注入 send_chunk → 协程（dict 契约不变）；注入 send_chunk →
    ``_info_brief_stream`` async generator。

**本棘轮能抓住什么**（含负控，见 test_negative_control_*）：
  L1 静态（全部 ``backend/handlers/**/*.py``）
     - 带 ``send_chunk`` 形参且"已被 registry.register 注册或名字以 ``_stream`` 结尾"
       的 ``async def``，必须自身层级含 ``yield``（即 async generator 函数）；
     - 同口径的 sync ``def``（双形态入口）必须至少 return 一个本地 async generator 调用。
  L2 静态反例模式：非 asyncgen 的 ``async def`` 直接 ``return <内层 asyncgen>()``
     —— 正是原 info_brief 的崩法，与命名无关（覆盖"不知道名字"的第三个成员）。
  L3 注册面交叉（运行时，真实注册中心）：名字以 ``_stream`` 结尾的注册方法逐个断言
     ``inspect.isasyncgenfunction``；单方法名双形态方法用"注入 dummy send_chunk 调用"
     探针断言返回 async generator（生成器体不执行，无副作用，随即 aclose）。
  L4 端到端代码路径冒烟：真实注册中心 + 真实 ``dispatch_stream`` 跑 ``info.brief``
     （采集器 collect 打桩为本地假实现，不触外网/不写库）与 ``job_hunter.analyze_stream``。

口径说明：L3 只把 ``_inbox_info`` / ``_job_hunter`` 两个域真接线进一次性注册中心
（全量 ``register_all`` 需要重型 AppContext，隔离环境不起后端）；其余 handler 模块
的流式形态由 L1/L2 的 AST 扫描对 ``backend/handlers/**`` 全量兜底。
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.handlers import _inbox_info, _job_hunter
from backend.method_registry import MethodRegistry

APP_ROOT = Path(__file__).resolve().parents[1]
HANDLER_DIR = APP_ROOT / "backend" / "handlers"

# 已知家族成员（防棘轮空转）：(相对 handlers 的路径, 函数名) -> 期望形态
KNOWN_MEMBERS: dict[tuple[str, str], str] = {
    ("_job_hunter.py", "analyze_stream"): "asyncgen",
    ("_inbox_info.py", "_info_brief_stream"): "asyncgen",
    ("_inbox_info.py", "info_brief"): "trampoline",
}

# 单方法名双形态（收 send_chunk 才走流式），需运行时探针而非 isasyncgenfunction
DUAL_MODE_METHODS = frozenset({"info.brief"})

_REGISTER_CALLS = frozenset({"register", "register_with_schema"})


# ── AST 工具 ──────────────────────────────────────────────────────────
def _iter_handler_files(root: Path = HANDLER_DIR):
    return sorted(root.rglob("*.py"))


def _own_level_yield(node: ast.AST) -> bool:
    """自身层级是否含 yield（**不含**嵌套函数体内的 yield）。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            return True
        if _own_level_yield(child):
            return True
    return False


def _param_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = node.args
    return {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}


def _module_asyncgens(tree: ast.AST) -> set[str]:
    """模块内（含任意嵌套层级）async generator 函数名集合。"""
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and _own_level_yield(n)}


def _registered_func_names(tree: ast.AST) -> set[str]:
    """``registry.register("m", f)`` / ``register_with_schema("m", f, ...)`` 里的 f 名。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if attr not in _REGISTER_CALLS or len(node.args) < 2:
            continue
        target = node.args[1]
        if isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _returns_local_asyncgen(node: ast.AST, asyncgens: set[str]) -> bool:
    """函数体内是否存在 ``return <本地 asyncgen>(...)``（双形态入口的判据）。"""
    for st in ast.walk(node):
        if isinstance(st, ast.Return) and isinstance(st.value, ast.Call):
            fn = st.value.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if name in asyncgens:
                return True
    return False


def _scan_family(root: Path) -> list[str]:
    """反例模式：非 asyncgen 的 async def 直接 return 内层 async generator 调用。

    该形态在 ``dispatch_stream`` 下必然 ``AttributeError: 'coroutine' object has no
    attribute '__anext__'``（finally 的 ``aclose`` 亦不存在），与函数命名无关。
    """
    hits: list[str] = []
    for path in _iter_handler_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        except SyntaxError:  # pragma: no cover - 语法错误由其它门禁负责
            continue
        asyncgens = _module_asyncgens(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or _own_level_yield(node):
                continue
            for st in ast.walk(node):
                if isinstance(st, ast.Return) and isinstance(st.value, ast.Call):
                    fn = st.value.func
                    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                    if name in asyncgens:
                        rel = path.relative_to(root)
                        hits.append(f"{rel}:{node.lineno} async def {node.name} -> return {name}()")
    return hits


def _scan_stream_shapes(root: Path) -> dict[tuple[str, str], str]:
    """扫描 (相对路径, 函数名) -> 形态（asyncgen / trampoline / other）。"""
    shapes: dict[tuple[str, str], str] = {}
    for path in _iter_handler_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        asyncgens = _module_asyncgens(tree)
        rel = str(path.relative_to(root))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.AsyncFunctionDef) and _own_level_yield(node):
                shapes[(rel, node.name)] = "asyncgen"
            elif _returns_local_asyncgen(node, asyncgens):
                shapes[(rel, node.name)] = "trampoline"
            else:
                shapes[(rel, node.name)] = "other"
    return shapes


def _iter_shaped_functions(root: Path = HANDLER_DIR):
    """产出 (path, lineno, node, asyncgens, registered_names)。"""
    for path in _iter_handler_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        asyncgens = _module_asyncgens(tree)
        registered = _registered_func_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path, node, asyncgens, registered


# ── L1：静态形态棘轮 ──────────────────────────────────────────────────
def test_send_chunk_handlers_have_stream_compatible_shape():
    """带 send_chunk 且"可被 stream:true 命中"的 handler 必须是流式兼容形态。

    - ``async def``：必须自身含 yield（async generator 函数）——否则 dispatch_stream
      拿到的是协程，``__anext__`` 必炸；
    - ``def``（双形态入口）：必须至少 return 一个本地 async generator 调用。
    """
    problems: list[str] = []
    checked: list[str] = []
    for path, node, asyncgens, registered in _iter_shaped_functions():
        if "send_chunk" not in _param_names(node):
            continue
        name_hints_stream = node.name.endswith("_stream")
        if node.name not in registered and not name_hints_stream:
            continue  # 非注册的普通带参助手（_safe_chunk 等），不属 RPC 面
        checked.append(f"{path.relative_to(HANDLER_DIR)}:{node.name}")
        if isinstance(node, ast.AsyncFunctionDef):
            if not _own_level_yield(node):
                problems.append(
                    f"{path.relative_to(HANDLER_DIR)}:{node.lineno} async def {node.name}(send_chunk...)"
                    " 自身无 yield → dispatch_stream 会拿到协程，__anext__ 必 AttributeError"
                )
        elif not _returns_local_asyncgen(node, asyncgens):
            problems.append(
                f"{path.relative_to(HANDLER_DIR)}:{node.lineno} def {node.name}(send_chunk...)"
                " 未 return 本地 async generator → 流式通道拿不到 async generator"
            )
    assert checked, "扫描未命中任何 send_chunk handler，棘轮口径失效（检查 HANDLER_DIR / 判定条件）"
    assert not problems, "流式 handler 形态违反契约：\n" + "\n".join(problems)


def test_no_coroutine_wrapping_asyncgen_in_handlers():
    """L2 反例模式：禁止"非 asyncgen 的 async def 把内层 async generator 直接 return"。"""
    hits = _scan_family(HANDLER_DIR)
    assert not hits, "检测到 coroutine-wrapping-asyncgen（stream:true 下必 AttributeError）:\n" + "\n".join(hits)


def test_known_family_members_are_covered():
    """防空转：两个已知成员必须在扫描覆盖内，且形态符合预期。"""
    shapes = _scan_stream_shapes(HANDLER_DIR)
    for (rel, name), expected in KNOWN_MEMBERS.items():
        key = (rel, name)
        assert key in shapes, f"棘轮未覆盖已知成员 {rel}:{name}（扫描口径失效）"
        assert shapes[key] == expected, f"{rel}:{name} 形态={shapes[key]}，期望 {expected}"


# ── L3/L4：注册面 + dispatch_stream 真实路径 ──────────────────────────
@pytest.fixture
def registry() -> MethodRegistry:
    """一次性真实注册中心：只接线 info / job_hunter 两个域（口径见模块 docstring）。"""
    reg = MethodRegistry()
    ctx = SimpleNamespace(
        ai_client=None,
        info_item_repository=None,
        info_source_registry=None,
        settings_repository=None,
        agent_gateway=None,
    )
    _inbox_info.register_inbox_info(reg, ctx)
    _job_hunter.register_job_hunter(reg, SimpleNamespace())
    return reg


async def _noop_send_chunk(chunk: str, chunk_type: str = "chunk") -> None:  # pragma: no cover
    pass


def test_registered_stream_methods_are_asyncgen(registry: MethodRegistry):
    """名字以 ``_stream`` 结尾的注册方法必须以 async generator 函数注册。"""
    stream_named = [m for m in registry.list_methods() if m.endswith("_stream")]
    assert "job_hunter.analyze_stream" in stream_named, "注册面未接线 analyze_stream，棘轮空转"
    handlers = registry._handlers
    bad = [m for m in stream_named if not inspect.isasyncgenfunction(handlers[m])]
    assert not bad, f"以下流式方法不是 async generator 函数（dispatch_stream 必炸）：{bad}"


async def test_dual_mode_handler_returns_asyncgen_on_stream_channel(registry: MethodRegistry):
    """双形态方法：注入 send_chunk 必须拿到 async generator；不注入则不是（避免误判）。"""
    dual = sorted(set(registry.list_methods()) & DUAL_MODE_METHODS)
    assert dual, "注册面未接线 info.brief，棘轮空转"
    handlers = registry._handlers
    for method in dual:
        handler = handlers[method]
        assert not inspect.isasyncgenfunction(handler), f"{method} 若本身是 asyncgen 函数则非流式调用会被 registry 降级为 chunk 列表，破坏 dict 契约"
        gen = handler(send_chunk=_noop_send_chunk)
        assert inspect.isasyncgen(gen), f"{method} stream 通道未返回 async generator"
        await gen.aclose()  # 生成器体尚未执行，直接关闭，零副作用


async def test_info_brief_non_stream_keeps_dict_contract(registry, monkeypatch):
    """非流式（dispatch）契约守卫：主调用面必须仍是 ``{brief, items, errors}`` dict。"""
    from services.info_collector_service import InfoCollectorService

    async def fake_collect(
        self,
        topics=None,
        generate_brief=True,
        progress_callback=None,
        return_early=False,
        sources=None,
    ):
        return {"brief": "fake", "items": [{"title": "t"}], "errors": []}

    monkeypatch.setattr(InfoCollectorService, "collect", fake_collect)
    handler = registry._handlers["info.brief"]
    pending = handler(no_ai=True, sources=[])
    assert inspect.iscoroutine(pending), "非流式入口必须返回协程（asyncgen 会被 dispatch 降级为列表）"
    result = await pending
    assert isinstance(result, dict), f"非流式返回类型被改变：{type(result).__name__}"
    assert {"brief", "items", "errors"} <= set(result)


async def test_dispatch_stream_info_brief_smoke(registry, monkeypatch):
    """L4 端到端代码路径冒烟：真实 dispatch_stream 拿到结果帧，而非 AttributeError。

    采集器 ``collect`` 打桩为本地假实现（不触外网、不写库），只验证 dispatch_stream ↔
    handler 的形态契约与 chunk 形状。
    """
    from services.info_collector_service import InfoCollectorService

    sent: list[tuple[str, str]] = []

    async def fake_collect(
        self,
        topics=None,
        generate_brief=True,
        progress_callback=None,
        return_early=False,
        sources=None,
    ):
        if progress_callback is not None:
            await progress_callback("collecting", {"phase": "start", "sources": ["fake"]})
        return {"brief": "fake brief", "items": [{"title": "t1"}], "errors": []}

    async def send_chunk(chunk: str, chunk_type: str = "chunk") -> None:
        sent.append((chunk_type, chunk))

    monkeypatch.setattr(InfoCollectorService, "collect", fake_collect)
    chunks = [c async for c in registry.dispatch_stream("info.brief", {"no_ai": True, "sources": []}, send_chunk=send_chunk)]
    assert len(chunks) == 1, f"info.brief 流式应产出 1 条结果帧，实际 {len(chunks)}"
    body = json.loads(chunks[0])
    assert body["brief"] == "fake brief" and body["items"], f"结果帧内容异常：{body}"
    assert any(t == "info_progress" for t, _ in sent), "采集进度 chunk 形状被改变（info_progress 丢失）"


async def test_dispatch_stream_job_hunter_analyze_stream_smoke(registry):
    """第二个已知成员的同样冒烟：空 job_id 走早退分支，首帧应是明确文本。"""
    sent: list[tuple[str, str]] = []

    async def send_chunk(chunk: str, chunk_type: str = "chunk") -> None:
        sent.append((chunk_type, chunk))

    chunks = [c async for c in registry.dispatch_stream("job_hunter.analyze_stream", {}, send_chunk=send_chunk)]
    assert chunks == ["job_id required"], f"analyze_stream 首帧异常：{chunks}"
    assert ("error", "job_id required") in sent


# ── 负控：证明棘轮不空转 ──────────────────────────────────────────────
_LEGACY_SHAPE_SNIPPET = '''\
async def info_brief(send_chunk=None, **kwargs):
    """改造前形态：普通协程内定义 async generator 再 return 它。"""

    async def _stream():
        yield "x"

    return _stream()
'''


def test_negative_control_static_scanner_flags_legacy_shape(tmp_path: Path):
    """负控（静态）：把改造前的 info_brief 形态落成临时文件，L2 扫描必须报红。"""
    legacy = tmp_path / "legacy_handler.py"
    legacy.write_text(_LEGACY_SHAPE_SNIPPET, encoding="utf-8")
    hits = _scan_family(tmp_path)
    assert len(hits) == 1 and "info_brief" in hits[0], f"静态扫描漏报反例：{hits}"


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
async def test_negative_control_legacy_shape_raises_attributeerror():
    """负控（运行时）：同一形态经真实 dispatch_stream 必抛 AttributeError。"""
    registry = MethodRegistry()

    async def legacy_stream(send_chunk=None, **params):
        async def _stream():
            yield "x"

        return _stream()

    registry.register("demo.legacy_stream", legacy_stream)
    # 形态确证：旧写法返回的是协程，没有 __anext__（dispatch_stream 依赖的协议）
    stream = registry._handlers["demo.legacy_stream"](send_chunk=_noop_send_chunk)
    assert inspect.iscoroutine(stream) and not hasattr(stream, "__anext__")
    stream.close()
    with pytest.raises(AttributeError, match=r"__anext__|aclose"):
        async for _ in registry.dispatch_stream("demo.legacy_stream", {}, send_chunk=_noop_send_chunk):
            pass  # pragma: no cover - 首帧即抛
