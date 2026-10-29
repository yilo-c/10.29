# -*- coding: utf-8 -*-
"""R2 批次（2026-09-17）独立对抗验证 —— r2-verify 子代理产出。

本文件**不是**实现者的回归测试，而是以证伪视角对 R2 批次做边界/绕过复核。
每条测试的断言都对应团队 lead 下达的 9 个验证点之一；对"与题目口径不符"
的实测结果，测试名与 docstring 显式记录实测真相（而非改成期望值掩盖）。

证明层级（如实标注，逐条）：
- file.write R4 链：**进程内端到端**（真 MethodRegistry + register_all + 真
  SandboxManager + 假 capability_router，不含 WS 传输层/前端）；
- 隔离正向探针：**进程内真实写盘**（SIYI_DATA_DIR→临时目录），并对生产
  data/ 下同名文件做 md5 前后比对；
- reminder / info.items / rss / notification / weather：**组件级真跑**
  （真仓储 / 真 dispatcher / 真 registry，无活后端）；
- scrape 诚实性：**组件级真跑**（真 JobHunterService + 假 scraper，不出网）；
- 插件路由契约：**源码级 + 临时目录副本实验**（不触碰生产插件源码）。

纪律：不修改任何生产代码；不跑任何 git 命令；SIYI_DATA_DIR 全程指向
``D:/h/3.25/r2verify-tmp``（由 autouse fixture 强制安装并在用例后还原）。
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# ── 目录常量 ──────────────────────────────────────────────────────────
VERIFY_TMP_ROOT = Path("D:/h/3.25/r2verify-tmp")
REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = Path(__file__).resolve().parents[1]
PROD_DATA_ROOT = APP_ROOT / "data"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


@pytest.fixture(autouse=True)
def _force_verify_data_dir(monkeypatch):
    """全程把 SIYI_DATA_DIR 指向验证临时目录，绝不写生产 data/。"""
    VERIFY_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SIYI_DATA_DIR", str(VERIFY_TMP_ROOT))


def _md5(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()


# ═══════════════════════════════════════════════════════════════
# 验证点 1：file.write 的 R4 审批链（最高风险）
# ═══════════════════════════════════════════════════════════════


def _fake_capability_router(tmp_path: Path):
    """假 capability_router：真 FileCapabilityDomain（同一份写路径白名单）+ 调用记录。

    与 tests/test_r2b_transfer_r4_20260917.py 同构，但额外记录 ``approved`` 以便
    验证"重放标记不可由 params 伪造"。
    """
    from assistant.capability._file_ops import FileCapabilityDomain

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    calls: list[dict] = []

    async def _execute_direct(action_id: str, args=None, approved: bool = False):
        calls.append({"action_id": action_id, "args": dict(args or {}), "approved": approved})
        # 真实落盘一份，用于"未审批不得产生副作用"的强断言
        target = data_root / os.path.basename(str((args or {}).get("path") or "x"))
        target.write_text(str((args or {}).get("content") or ""), encoding="utf-8")
        return {"success": True, "path": str(target), "approved": approved}

    router = SimpleNamespace(
        shell_execution=SimpleNamespace(workspace_root=str(ws_root)),
        data_store=SimpleNamespace(base_dir=str(data_root)),
        write_guard=None,
        _DEFAULT_DOWNLOAD_DIRNAME="assistant_downloads",
    )
    router.file_domain = FileCapabilityDomain(router)
    router.execute_direct = _execute_direct
    return router, calls, data_root


async def _make_registry(tmp_path: Path, router=None):
    from backend.handlers import register_all
    from backend.method_registry import MethodRegistry
    from unittest.mock import MagicMock

    ctx = SimpleNamespace(
        local_data_store=SimpleNamespace(base_dir=str(tmp_path)),
        settings_repository=MagicMock(),
    )
    if router is not None:
        ctx.capability_router = router
    reg = MethodRegistry()
    await register_all(reg, ctx, ws_port=18765)
    return reg


class TestV1FileWriteApprovalChain:
    def test_r4_list_has_at_least_40_and_contains_file_write(self):
        from _r4_baseline import assert_r4_not_shrunk

        from backend.handlers import _R4_METHODS

        assert "file.write" in _R4_METHODS
        # 批4（2026-09-18）：原为 `len(_R4_METHODS) == 40` 的硬编码等号。等号写法
        # 会让每次**合法新增** R4 方法都四处同时假红（本批新增
        # knowledge.entity.delete 即 40→41）。真实意图只是"不得移出成员"，故改用
        # tests/_r4_baseline.py 的单点下界断言（新增不假红，移出即红）。
        assert_r4_not_shrunk(_R4_METHODS)

    def test_shrinking_below_baseline_would_turn_existing_tests_red(self):
        """只读推演：把 file.write 从 _R4_METHODS 删掉会让哪些既有断言变红。

        证据：全仓调用 ``assert_r4_not_shrunk(_R4_METHODS)`` 的既有测试有 ≥2 处，
        删项即 len=39 < 基线 40 → 全部 FAIL。另有 ``"file.write" in _R4_METHODS``
        一处 membership 断言。

        批4（2026-09-18）：本测试原先扫描字面量 ``len(_R4_METHODS) == 40``；该等号
        写法已收敛为 ``tests/_r4_baseline.py`` 的下界断言，故扫描目标随之改为调用点。
        """
        pkg = APP_ROOT / "tests"
        len_asserts = []
        membership_asserts = []
        for py in pkg.glob("test_*.py"):
            text = py.read_text(encoding="utf-8")
            if "assert_r4_not_shrunk" in text:
                len_asserts.append(py.name)
            if '"file.write" in _R4_METHODS' in text:
                membership_asserts.append(py.name)
        assert len_asserts, "推演前提失效：找不到 assert_r4_not_shrunk 的调用点"
        assert membership_asserts, "推演前提失效：找不到 file.write ∈ _R4_METHODS 的断言"
        # 结论：删项 → 上述 len_asserts 全部变红（≥2 个文件）
        assert len(len_asserts) >= 2, len_asserts

    def test_skip_approval_is_keyword_only_and_never_from_params(self):
        """静态证据：skip_approval 是 dispatch/dispatch_stream 的 keyword-only 形参，
        且唯一带 True 的调用点在 _sandbox.py（审批重放），ws_server 不转发客户端字段。"""
        from backend.method_registry import MethodRegistry

        for fn in (MethodRegistry.dispatch, MethodRegistry.dispatch_stream):
            sig = inspect.signature(fn)
            p = sig.parameters["skip_approval"]
            assert p.kind is inspect.Parameter.KEYWORD_ONLY, (fn.__name__, p.kind)
            assert p.default is False

        ws_src = (APP_ROOT / "backend" / "ws_server.py").read_text(encoding="utf-8")
        # ws_server 两处 dispatch 调用都不得出现 skip_approval
        calls = re.findall(r"\.dispatch\(\s*\n?.*?\)", ws_src, flags=re.S)
        assert calls, "未解析到 ws_server 的 dispatch 调用"
        for c in calls:
            assert "skip_approval" not in c, f"ws_server 转发 skip_approval：{c[:200]}"
        assert 'body.get("skip_approval")' not in ws_src

        # 全仓唯一 True 来源（AST：只认真实的关键字实参，不误伤注释/docstring）
        import ast

        true_sites = []
        for py in sorted((APP_ROOT / "backend").rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if kw.arg == "skip_approval" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        true_sites.append(f"{py.relative_to(APP_ROOT).as_posix()}:{node.lineno}")
        # 内容锚定（原为硬编码行号 _sandbox.py:127，波次①-b/补删波后真实位置已漂到 :176 附近）。
        # 不变量本身是「全仓唯一 skip_approval=True 来源 = _sandbox.py 审批重放 dispatch」，
        # 行号只是它的偶然投影，硬编码会随每次上方注释增删而假红。故改为按源码文本特征定位：
        #   (1) 全仓唯一 True 来源；(2) 落在 _sandbox.py；(3) 该行确为审批重放的
        #       registry.dispatch(..., skip_approval=True) 调用（正则锚源码文本，与行号解耦）。
        assert len(true_sites) == 1, f"skip_approval=True 来源不再唯一：{true_sites}"
        site_file, _, site_line = true_sites[0].rpartition(":")
        assert site_file == "backend/handlers/_sandbox.py", true_sites
        site_src = (APP_ROOT / site_file).read_text(encoding="utf-8").splitlines()
        assert re.search(r"registry\.dispatch\([^)]*\bskip_approval\s*=\s*True\b", site_src[int(site_line) - 1]), (
            f"_sandbox.py:{site_line} 不是审批重放 dispatch：{site_src[int(site_line) - 1]!r}"
        )

    async def test_unapproved_returns_pending_envelope_and_no_side_effect(self, tmp_path):
        router, calls, data_root = _fake_capability_router(tmp_path)
        reg = await _make_registry(tmp_path, router)

        res = await reg.dispatch("file.write", {"path": "ok.txt", "content": "hello"})
        assert res.get("pending") is True, res
        assert res.get("requires_approval") is True, res
        assert isinstance(res.get("pending_id"), str) and res["pending_id"], res
        assert res.get("action_id") == "file.write", res
        assert calls == [], f"未审批就执行了写操作：{calls}"
        assert not (data_root / "ok.txt").exists(), "未审批就产生了磁盘副作用"

    async def test_client_params_cannot_forge_skip_or_replay_marker(self, tmp_path):
        """攻击面：客户端在 params 里塞 skip_approval / _r4_approved_replay /
        approved / __approved__ 等伪造标记，必须依旧入队 pending 且零执行。"""
        router, calls, data_root = _fake_capability_router(tmp_path)
        reg = await _make_registry(tmp_path, router)

        for evil_key in ("skip_approval", "_r4_approved_replay", "approved", "replay", "_skip_approval"):
            res = await reg.dispatch(
                "file.write",
                {"path": f"evil_{evil_key}.txt", "content": "x", evil_key: True},
            )
            assert res.get("pending") is True, f"{evil_key} 竟绕过审批：{res}"
        assert calls == [], f"伪造参数触发了执行：{calls}"
        assert list(data_root.glob("evil_*.txt")) == [], "伪造参数产生了磁盘副作用"

    async def test_direct_handler_call_cannot_forge_replay_marker(self, tmp_path):
        """即使绕过 dispatch 直调 handler，params 也造不出重放标记：
        handler 据 is_r4_approved_replay()（contextvar）决定 approved，而该
        contextvar 只由 dispatch(skip_approval=True) 置位。"""
        from backend.method_registry import is_r4_approved_replay

        router, calls, _ = _fake_capability_router(tmp_path)
        reg = await _make_registry(tmp_path, router)

        await reg._handlers["file.write"](path="direct.txt", content="x", skip_approval=True, _r4_approved_replay=True)
        assert is_r4_approved_replay() is False
        assert calls and calls[-1]["approved"] is False, f"直调竟拿到人工授权：{calls}"

    async def test_replay_sets_marker_true(self, tmp_path):
        """正向：审批重放时 approved=True 才被传给 broker（否则写面整体坏死）。"""
        router, calls, _ = _fake_capability_router(tmp_path)
        reg = await _make_registry(tmp_path, router)

        res = await reg.dispatch("file.write", {"path": "ok.txt", "content": "hello"})
        from backend.handlers import _sandbox

        approved = await _sandbox._handle_sandbox_approve(pending_id=res["pending_id"], registry=reg)
        assert approved.get("success") is True, approved
        assert len(calls) == 1 and calls[0]["approved"] is True, calls

    async def test_out_of_bounds_path_REJECTED_BEFORE_ENQUEUE(self, tmp_path):
        """【裁决 2（2026-09-17 F-P2-①）同批反转 · 新契约】旧行为（本测试原名
        ``test_out_of_bounds_path_STILL_ENTERS_APPROVAL_QUEUE`` 钉死的实测真相）：
        越界路径先入审批队列、重放时才被 handler 内守卫拒 → 审批面板垃圾条目 +
        反复提交刷满 _MAX_PENDING=200 驱逐最旧合法条目（DoS 面）。

        新契约：``_enqueue_r4_approval`` 在 sandbox.add **之前**跑注册级静态预检
        （register_precheck → file.write 复用 _write_path_rejection），判定
        「注定被执行层守卫拒绝」→ 直接抛 InvalidParamsError(-32602)：
          ① 不入队（list_pending 计数不涨）；
          ② 无 pending 信封（不返回 dict，而是 raise）；
          ③ 错误形态与 N-E 分页棘轮的正常参数拒绝同形
             （message="Field 'path': <reason>"）。
        """
        from core.errors import InvalidParamsError
        from services.sandbox_manager import get_sandbox_manager

        router, calls, _ = _fake_capability_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        mgr = get_sandbox_manager()
        before = len(mgr.list_pending())

        with pytest.raises(InvalidParamsError) as ei:
            await reg.dispatch("file.write", {"path": "C:/Windows/__r2v__.txt", "content": "x"})
        after = len(mgr.list_pending())
        assert after == before, f"越界路径仍入队：before={before} after={after}"
        assert calls == [], "越界路径被送入执行层"
        err = ei.value
        assert err.code == -32602, err.code
        assert "path_outside_allowed_root" in str(err.message), err.message
        assert err.message.startswith("Field 'path':"), err.message
        assert err.details.get("method") == "file.write", err.details
        assert err.details.get("reason") == "path_outside_allowed_root", err.details

    async def test_relative_out_of_write_root_rejected_before_enqueue(self, tmp_path):
        """裁决 2 覆盖面：解析后落在写根（data_root/download_root）外的路径
        （含指向 workspace 的相对/绝对路径）同样在入队前拒。

        注：`_resolve_write_path` 先跑 `_assert_within_roots(write_roots)`，
        而 workspace 不在 write_roots 内 → workspace 路径先触发
        `path_outside_allowed_root`（而非 `workspace_write_blocked`）。二者都属
        「注定被执行层守卫拒」的静态可判越界，新契约一视同仁：入队前 -32602。
        """
        from core.errors import InvalidParamsError
        from services.sandbox_manager import get_sandbox_manager

        router, calls, _ = _fake_capability_router(tmp_path)
        ws = Path(router.shell_execution.workspace_root) / "blocked.txt"
        reg = await _make_registry(tmp_path, router)
        mgr = get_sandbox_manager()
        before = len(mgr.list_pending())

        with pytest.raises(InvalidParamsError) as ei:
            await reg.dispatch("file.write", {"path": str(ws), "content": "x"})
        assert len(mgr.list_pending()) == before, "workspace 越界路径仍入队"
        assert calls == []
        assert ei.value.code == -32602, ei.value.code
        assert "Field 'path':" in str(ei.value.message), ei.value.message

    async def test_out_of_bounds_path_rejected_on_replay(self, tmp_path):
        """重放（skip_approval=True）时，越界路径被路径守卫硬拒且不进入执行层。"""
        router, calls, _ = _fake_capability_router(tmp_path)
        reg = await _make_registry(tmp_path, router)

        out = await reg.dispatch("file.write", {"path": "C:/Windows/__r2v__.txt", "content": "x"}, skip_approval=True)
        assert isinstance(out, dict) and out.get("success") is False, out
        assert out.get("reason") == "path_outside_allowed_root", out
        assert calls == [], "越界路径不得进入执行层"

    def test_real_router_exposes_file_domain(self):
        """守卫依赖 router.file_domain._resolve_write_path；若真 router 不挂
        file_domain，_write_path_rejection 会静默返回 None（守卫空转）。
        证实：AssistantCapabilityRouter.__init__ 第 120 行挂载 FileCapabilityDomain。"""
        from assistant.capability_router import AssistantCapabilityRouter

        assert hasattr(AssistantCapabilityRouter, "execute_direct")
        src = (APP_ROOT / "assistant" / "capability_router.py").read_text(encoding="utf-8")
        assert "self.file_domain = FileCapabilityDomain(self)" in src
        # _capability_router() 的真实挂载点（autonomous_assistant_manager.router）
        with open(APP_ROOT / "backend" / "handlers" / "__init__.py", encoding="utf-8") as fh:
            handlers_src = fh.read()
        assert '"autonomous_assistant_manager"' in handlers_src and ".router" in handlers_src

    async def test_self_approval_from_same_connection_is_possible_for_trusted_ws(self, tmp_path):
        """【实测真相 · 设计性缺口，非本批次回归】sandbox.approve 自身不是 R4，
        且"同连接自批"拦截以 ``not trusted`` 为条件；ws_server 对一切通过
        Origin/鉴权的 WS 连接置 trusted=True（ws_server.py:1036）→ 已建连客户端
        可自己入队、自己批准，人工介入在 WS 面上不构成强制。

        本测试用真 SandboxManager + 真 request-connection contextvar 复现：
          · trusted=True（WS 现实）→ 同连接自批**成功**；
          · trusted=False（匿名注入帧）→ 同连接自批被拒。
        """
        from backend.handlers import _sandbox
        from services.sandbox_manager import get_sandbox_manager, reset_request_connection, set_request_connection

        router, calls, _ = _fake_capability_router(tmp_path)
        reg = await _make_registry(tmp_path, router)
        mgr = get_sandbox_manager()

        # ① trusted=True：同一 conn 先入队，再自批
        tok = set_request_connection("r2v-conn", True)
        try:
            res = await reg.dispatch("file.write", {"path": "self.txt", "content": "x"})
            assert res.get("pending") is True, res
            assert mgr.get(res["pending_id"])["requester_conn"] == "r2v-conn"
            out = await _sandbox._handle_sandbox_approve(pending_id=res["pending_id"], registry=reg)
        finally:
            reset_request_connection(tok)
        assert out.get("success") is True, f"trusted 同连接自批未成功（与实测不符）：{out}"
        assert len(calls) == 1, calls

        # ② trusted=False：同一 conn 自批被拒
        tok = set_request_connection("r2v-conn2", False)
        try:
            res2 = await reg.dispatch("file.write", {"path": "self2.txt", "content": "x"})
            out2 = await _sandbox._handle_sandbox_approve(pending_id=res2["pending_id"], registry=reg)
        finally:
            reset_request_connection(tok)
        assert out2.get("success") is False, out2
        assert len(calls) == 1, f"未可信连接竟批准成功：{calls}"
        mgr.reject(res2["pending_id"])


# ═══════════════════════════════════════════════════════════════
# 验证点 2：隔离根治（正向探针 —— git status 看不见这类写入）
# ═══════════════════════════════════════════════════════════════


class TestV2DataDirIsolation:
    def test_graph_store_default_path_honors_env(self, tmp_path):
        from knowledge.graph_store import KnowledgeGraphStore as GraphStore

        before = _md5(PROD_DATA_ROOT / "knowledge_graph.json")
        store = GraphStore()
        assert Path(store.path).is_relative_to(VERIFY_TMP_ROOT), store.path
        store.add_entity(entity_id="r2v-1", name="__r2v_verify__", entity_type="concept")
        store.save()
        written = VERIFY_TMP_ROOT / "knowledge_graph.json"
        assert written.exists(), "图谱未落到临时目录"
        assert _md5(PROD_DATA_ROOT / "knowledge_graph.json") == before, "生产图谱被写穿！"

    def test_info_collector_cache_path_honors_env(self):
        from services import _info_collector_utils as u

        before = _md5(PROD_DATA_ROOT / "info_cache.json")
        p = Path(u._resolve_persist_path())
        assert p.is_relative_to(VERIFY_TMP_ROOT), p
        u._save_persisted_cache()
        assert p.exists(), "info 缓存未落到临时目录"
        assert _md5(PROD_DATA_ROOT / "info_cache.json") == before, "生产 info 缓存被写穿！"

    def test_job_hunter_storage_path_honors_env(self):
        from services.job_hunter.storage import JobStorage

        prod_jobs = PROD_DATA_ROOT / "job_hunter" / "jobs.json"
        before = _md5(prod_jobs)
        st = JobStorage()
        assert Path(st.data_dir).is_relative_to(VERIFY_TMP_ROOT), st.data_dir
        assert _md5(prod_jobs) == before, "生产 job_hunter 被写穿！"

    def test_headless_main_passes_data_base_dir_from_env(self):
        """装配点证实：headless 启动把 SIYI_DATA_DIR 显式传给记忆层 base_dir。"""
        src = (APP_ROOT / "backend" / "headless_main.py").read_text(encoding="utf-8")
        assert 'data_base_dir=os.environ.get("SIYI_DATA_DIR") or None' in src

    def test_memory_context_resolution_source(self):
        """记忆层 base_dir 解析：显式参数 > SIYI_DATA_DIR > 'data'（源码级证实）。"""
        src = (APP_ROOT / "memory" / "context.py").read_text(encoding="utf-8")
        assert 'base_dir = str(data_base_dir or os.environ.get("SIYI_DATA_DIR", "").strip() or "data")' in src


# ═══════════════════════════════════════════════════════════════
# 验证点 3：reminder 写后读 + 时间归一
# ═══════════════════════════════════════════════════════════════


class TestV3Reminder:
    @staticmethod
    def _reg(tmp_path):
        from data.local_storage import LocalDataStore
        from repositories._task import LocalPersonalTaskRepository as PersonalTaskRepository
        from backend.method_registry import MethodRegistry
        from backend.handlers._reminder import register_reminder

        ds = LocalDataStore(base_dir=str(tmp_path))
        repo = PersonalTaskRepository(ds)
        reg = MethodRegistry()
        register_reminder(reg, SimpleNamespace(personal_task_repository=repo))
        return reg, repo

    async def test_create_then_list_roundtrip_with_ms_timestamp(self, tmp_path):
        reg, _ = self._reg(tmp_path)
        import time

        past_ms = int((time.time() - 3600) * 1000)
        created = await reg.dispatch("reminder.create", {"title": "r2v-past", "reminder_time": past_ms, "due_date": "2026-01-01"})
        assert created.get("success") is True, created
        items = await reg.dispatch("reminder.list", {})
        assert isinstance(items, list), items
        hit = [i for i in items if i.get("title") == "r2v-past"]
        assert len(hit) == 1, f"写后读 MISS：{items}"
        assert hit[0]["due_date"] == "2026-01-01"
        assert isinstance(hit[0]["reminder_time"], int)

    async def test_create_then_list_roundtrip_with_string_time(self, tmp_path):
        """仓储把时间序列化成 'yyyy-MM-dd HH:mm' 字符串（local_storage.py:662），
        list 侧必须能把字符串归一成毫秒再与 now 比较。"""
        reg, _ = self._reg(tmp_path)
        created = await reg.dispatch("reminder.create", {"title": "r2v-str", "reminder_time": "2020-01-01 08:00"})
        assert created.get("success") is True, created
        store = reg._handlers["reminder.list"]
        items = await store()
        assert [i["title"] for i in items] == ["r2v-str"], items

    async def test_future_reminder_not_listed(self, tmp_path):
        """未来时间不应出现在"到期"列表（列表语义=已到期提醒）。"""
        reg, _ = self._reg(tmp_path)
        await reg.dispatch("reminder.create", {"title": "r2v-future", "reminder_time": "2099-01-01 00:00"})
        items = await reg.dispatch("reminder.list", {})
        assert [i["title"] for i in items] == [], items

    async def test_no_reminder_time_not_listed(self, tmp_path):
        reg, _ = self._reg(tmp_path)
        await reg.dispatch("reminder.create", {"title": "r2v-none", "due_date": "2026-01-01"})
        items = await reg.dispatch("reminder.list", {})
        assert items == [], items

    async def test_illegal_time_string_is_rejected_not_persisted(self, tmp_path):
        """reminder_time 传非 None 但无法解析的值 → create 如实拒绝、不落库。

        旧现状（已修 PD-H-2 2026-09-17）：原样落库 + success=True，list 侧
        _to_ms 解析失败静默跳过 → "创建成功但提醒永不触发"的静默假成功。
        新契约：success=False + code=invalid_params + 不落库 + list 不可见。
        None/不传 = 合法（不设提醒），见 test_no_reminder_time_not_listed。
        """
        reg, repo = self._reg(tmp_path)
        created = await reg.dispatch("reminder.create", {"title": "r2v-bad", "reminder_time": "not-a-time"})
        assert created.get("success") is False, f"非法时间竟创建成功（PD-H-2 回归）：{created}"
        assert created.get("code") == "invalid_params", created
        assert "not-a-time" in str(created.get("error", "")), created
        raw = repo.list_tasks()
        assert not any(t.get("title") == "r2v-bad" for t in raw), "非法时间条目竟落了库"
        items = await reg.dispatch("reminder.list", {})
        assert [i["title"] for i in items] == [], f"非法时间条目竟被视为到期：{items}"

    async def test_illegal_time_variants_all_rejected(self, tmp_path):
        """空串/纯空白/bool/容器/NaN/inf 等"传了值但无法解析或语义非法"形态统一拒绝；None 合法。"""
        reg, repo = self._reg(tmp_path)
        for bad in ("", "   ", "abc", "2026-09-17 10:00:xx", [], {}, True, "NaN", "nan", "inf", float("nan")):
            out = await reg.dispatch("reminder.create", {"title": f"r2v-bad-{type(bad).__name__}", "reminder_time": bad})
            assert out.get("success") is False and out.get("code") == "invalid_params", (bad, out)
        # None / 不传 = 合法（不设提醒的普通任务），不落非法拒绝路径
        ok = await reg.dispatch("reminder.create", {"title": "r2v-plain", "reminder_time": None})
        assert ok.get("success") is True, ok
        # bool 特判说明：_to_ms 对 True/False 返回 None（bool 不是时间戳）→ 拒绝，
        # 与 test_to_ms_edge_matrix 一致。
        raw = repo.list_tasks()
        titles = {t.get("title") for t in raw}
        assert "r2v-plain" in titles and not any(str(x).startswith("r2v-bad") for x in titles), titles

    async def test_legal_time_forms_still_create(self, tmp_path):
        """正向契约：合法时间形态（毫秒 int / 字符串毫秒 / 'yyyy-MM-dd HH:mm' /
        ISO）照常创建成功（PD-H-2 守卫不误伤）。"""
        reg, _ = self._reg(tmp_path)
        import time as _t

        past_ms = int((_t.time() - 3600) * 1000)
        for i, legal in enumerate((past_ms, str(past_ms), "2020-01-01 08:00", "2020-01-01T08:00:00")):
            out = await reg.dispatch("reminder.create", {"title": f"r2v-ok-{i}", "reminder_time": legal})
            assert out.get("success") is True, (legal, out)
        items = await reg.dispatch("reminder.list", {})
        assert len(items) == 4, items

    async def test_delete_removes_from_list(self, tmp_path):
        reg, _ = self._reg(tmp_path)
        created = await reg.dispatch("reminder.create", {"title": "r2v-del", "reminder_time": "2020-01-01 08:00"})
        assert (await reg.dispatch("reminder.list", {}))[0]["title"] == "r2v-del"
        # reminder.delete ∈ R4：直调 handler 验写后读（dispatch 面走审批另测）
        dele = await reg._handlers["reminder.delete"](id=created["id"])
        assert dele.get("success") is True, dele
        assert await reg.dispatch("reminder.list", {}) == []

    def test_to_ms_edge_matrix(self):
        from backend.handlers._reminder import _to_ms
        from datetime import datetime

        assert _to_ms(None) is None
        assert _to_ms(True) is None and _to_ms(False) is None  # bool 不是时间戳
        assert _to_ms(0) == 0.0
        assert _to_ms("1758000000000") == 1758000000000.0
        assert _to_ms(" 1758000000000 ") == 1758000000000.0
        assert _to_ms("2026-09-17 10:00") is not None
        assert _to_ms("2026-09-17 10:00:00") is not None
        assert _to_ms("2026-09-17T10:00:00") is not None
        assert _to_ms("2026-09-17T10:00:00+08:00") is not None
        assert _to_ms(datetime(2026, 9, 17, 10, 0)) is not None
        for bad in ("not-a-time", "", "   ", "abc", "2026/09/17 10:00"):
            assert _to_ms(bad) is None, bad


# ═══════════════════════════════════════════════════════════════
# 验证点 4：info.items offset 边界（N-E 棘轮 + 分页不重复不越界）
# ═══════════════════════════════════════════════════════════════


class TestV4InfoItemsOffset:
    @staticmethod
    async def _reg(tmp_path, n_items: int = 5):
        from data.local_storage import LocalDataStore
        from repositories._info_item import LocalInfoItemRepository
        from backend.method_registry import MethodRegistry
        from backend.handlers import _info_items

        ds = LocalDataStore(base_dir=str(tmp_path))
        repo = LocalInfoItemRepository(ds)
        repo.upsert_items(
            [
                {
                    "url": f"https://example.com/{i}",
                    "title": f"item-{i}",
                    "collected_at": f"2026-09-{10 + i:02d}T00:00:00",
                    "id": 1000 + i,
                }
                for i in range(n_items)
            ]
        )
        reg = MethodRegistry()
        await _info_items.register(reg, SimpleNamespace(info_item_repository=repo))
        return reg

    async def test_offset_zero_returns_full_page(self, tmp_path):
        reg = await self._reg(tmp_path, 5)
        out = await reg.dispatch("info.items.list", {"offset": 0, "limit": 3})
        assert len(out["items"]) == 3 and out["total"] == 5, out

    async def test_offset_equal_total_returns_empty(self, tmp_path):
        reg = await self._reg(tmp_path, 5)
        out = await reg.dispatch("info.items.list", {"offset": 5, "limit": 3})
        assert out["items"] == [] and out["total"] == 5, out

    async def test_offset_beyond_total_returns_empty(self, tmp_path):
        reg = await self._reg(tmp_path, 5)
        out = await reg.dispatch("info.items.list", {"offset": 9999, "limit": 3})
        assert out["items"] == [] and out["total"] == 5, out

    async def test_pagination_no_overlap_no_gap(self, tmp_path):
        reg = await self._reg(tmp_path, 5)
        p1 = await reg.dispatch("info.items.list", {"offset": 0, "limit": 2})
        p2 = await reg.dispatch("info.items.list", {"offset": 2, "limit": 2})
        p3 = await reg.dispatch("info.items.list", {"offset": 4, "limit": 2})
        ids = [tuple(p1["items"]), tuple(p2["items"]), tuple(p3["items"])]
        assert len({i["title"] for i in p1["items"] + p2["items"] + p3["items"]}) == 5, ids
        assert len(p3["items"]) == 1, p3

    @pytest.mark.parametrize(
        "params",
        [
            {"offset": -1},
            {"offset": "abc"},
            {"offset": 1.5},
            {"offset": True},
            {"offset": []},
            {"offset": 10_000_001},
            {"limit": -1},
            {"limit": "abc"},
            {"limit": 1.5},
            {"limit": True},
            {"limit": 10_001},
        ],
    )
    async def test_illegal_paging_params_give_minus_32602(self, tmp_path, params):
        from core.errors import InvalidParamsError

        reg = await self._reg(tmp_path, 3)
        with pytest.raises(InvalidParamsError) as ei:
            await reg.dispatch("info.items.list", params)
        assert ei.value.code == -32602, ei.value

    async def test_string_numeric_paging_is_accepted_and_normalized(self, tmp_path):
        reg = await self._reg(tmp_path, 5)
        out = await reg.dispatch("info.items.list", {"offset": " 2 ", "limit": "2"})
        assert len(out["items"]) == 2 and out["total"] == 5, out


# ═══════════════════════════════════════════════════════════════
# 验证点 5：rss.list 契约（feeds / total / 空态）
# ═══════════════════════════════════════════════════════════════


class _FakeSettings:
    def __init__(self):
        self._d: dict = {}

    def get_setting(self, key, default=None):
        return self._d.get(key, default)

    def set_setting(self, key, value):
        self._d[key] = value
        return True


class TestV5RssList:
    @staticmethod
    async def _reg(subscriber=None):
        from backend.method_registry import MethodRegistry
        from backend.handlers._rss import register

        reg = MethodRegistry()
        ctx = SimpleNamespace(settings_repository=_FakeSettings())
        if subscriber is not None:
            ctx.rss_subscriber = subscriber
        await register(reg, ctx)
        return reg

    async def test_empty_subscriptions_shape(self):
        reg = await self._reg()
        out = await reg.dispatch("rss.list", {})
        assert out["feeds"] == [], out
        assert out["total"] == 0, out
        assert "subscriptions" not in out, f"旧键未清理：{out}"

    async def test_feeds_key_and_total_consistency(self):
        from services.rss_subscriber import RSSSubscriber

        sub = RSSSubscriber(settings_repository=_FakeSettings())
        sub._subscriptions = {
            "f1": {"id": "f1", "url": "https://a/feed.xml", "name": "A"},
            "f2": {"id": "f2", "url": "https://b/feed.xml", "name": "B"},
        }
        reg = await self._reg(sub)
        out = await reg.dispatch("rss.list", {})
        assert set(out.keys()) >= {"feeds", "total"}, out
        assert isinstance(out["feeds"], list)
        assert out["total"] == len(out["feeds"]) == 2, out
        assert {f["id"] for f in out["feeds"]} == {"f1", "f2"}, out


# ═══════════════════════════════════════════════════════════════
# 验证点 6：notification.test 条件落库
# ═══════════════════════════════════════════════════════════════


class _FakeBus:
    def __init__(self):
        self.events: list = []

    def emit(self, event):
        self.events.append(event)


class _FailingChannel:
    name = "websocket"

    async def send(self, notification):
        return False

    def health(self):
        return {"name": self.name, "healthy": False}


class TestV6NotificationTestChannel:
    @staticmethod
    async def _reg(tmp_path, bus=None):
        from data.local_storage import LocalDataStore
        from services.notification_dispatcher import NotificationDispatcher
        from backend.method_registry import MethodRegistry
        from backend.handlers._notifications import register_notifications

        ds = LocalDataStore(base_dir=str(tmp_path))
        disp = NotificationDispatcher(data_store=ds, settings_repository=_FakeSettings(), event_bus=bus or _FakeBus())
        reg = MethodRegistry()
        register_notifications(reg, SimpleNamespace(local_data_store=ds, settings_repository=_FakeSettings(), notification_dispatcher=disp))
        return reg, disp

    async def test_test_then_list_mark_read_delete(self, tmp_path):
        reg, _ = await self._reg(tmp_path)
        res = await reg.dispatch("notification.test", {"channel": "websocket"})
        assert res.get("ok") is True, res
        nid = res["notification"]["id"]

        listed = await reg.dispatch("notification.list", {})
        ids = [n["id"] for n in listed["items"]]
        assert nid in ids, f"test 通知未落库：{listed}"

        mr = await reg.dispatch("notification.mark_read", {"notification_id": nid, "read": True})
        assert mr.get("updated") is True, mr
        after = await reg.dispatch("notification.list", {})
        assert next(n for n in after["items"] if n["id"] == nid)["read"] is True

        dele = await reg.dispatch("notification.delete", {"notification_id": nid})
        assert dele.get("deleted") is True, dele
        final = await reg.dispatch("notification.list", {})
        assert nid not in [n["id"] for n in final["items"]]

    async def test_send_failure_does_not_persist(self, tmp_path):
        """推送失败时不得落库（否则制造"已送达"假记录）。"""
        reg, disp = await self._reg(tmp_path)
        disp._channels["websocket"] = _FailingChannel()
        res = await reg.dispatch("notification.test", {"channel": "websocket"})
        assert res.get("ok") is False, res
        listed = await reg.dispatch("notification.list", {})
        assert listed["items"] == [], f"发送失败却落库：{listed}"

    async def test_unknown_channel_rejected(self, tmp_path):
        reg, _ = await self._reg(tmp_path)
        res = await reg.dispatch("notification.test", {"channel": "carrier-pigeon"})
        assert res.get("ok") is False and "unknown channel" in str(res.get("error")), res


# ═══════════════════════════════════════════════════════════════
# 验证点 7：scrape 诚实性
# ═══════════════════════════════════════════════════════════════


class _Listing:
    def __init__(self, url: str, title: str):
        self._d = {"url": url, "title": title, "platform": "boss", "company": "co"}

    def to_dict(self):
        return dict(self._d)


class _Scraper:
    def __init__(self, *, results=None, exc: Exception | None = None):
        self._results = results
        self._exc = exc

    async def search(self, keywords, city="", page=1):
        if self._exc is not None:
            raise self._exc
        return list(self._results or [])


def _make_hunter(tmp_path, scrapers):
    from services.job_hunter import JobHunterService
    from services.job_hunter.storage import JobStorage

    return JobHunterService(
        ai_client=None,
        settings_repository=_FakeSettings(),
        storage=JobStorage(data_dir=str(tmp_path / "jh")),
        scrapers=scrapers,
    )


class TestV7ScrapeHonesty:
    async def test_all_platforms_failed_reports_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr("services.job_hunter._PLATFORM_RETRY_DELAY", 0.0)
        svc = _make_hunter(
            tmp_path,
            {"boss": _Scraper(exc=RuntimeError("boom")), "lagou": _Scraper(exc=RuntimeError("boom2"))},
        )
        out = await svc.search(platforms=["boss", "lagou"], keywords=["python"])
        assert out["success"] is False, out
        assert out.get("reason") == "all_platforms_failed", out
        assert out["added"] == 0 and out["failed_platforms"] == 2, out

    async def test_partial_failure_keeps_success_but_reports_errors(self, monkeypatch, tmp_path):
        monkeypatch.setattr("services.job_hunter._PLATFORM_RETRY_DELAY", 0.0)
        svc = _make_hunter(
            tmp_path,
            {
                "boss": _Scraper(results=[_Listing("https://x/1", "t1")]),
                "lagou": _Scraper(exc=RuntimeError("boom")),
            },
        )
        out = await svc.search(platforms=["boss", "lagou"], keywords=["python"])
        assert out["success"] is True, out
        assert out["failed_platforms"] == 1 and out["errors"], out
        assert out["added"] == 1, out

    async def test_no_scrapers_available_reports_failure(self, tmp_path):
        svc = _make_hunter(tmp_path, {})
        out = await svc.search(platforms=["boss"], keywords=["python"])
        assert out["success"] is False and out["error"] == "no available scrapers", out

    async def test_all_duplicates_boundary_observation(self, monkeypatch, tmp_path):
        """【边界实测】errors 为空、added=0（全重复）时的判定。

        结论：**success=True**（不偏严，而是偏宽）。理由：无任何平台报错，
        "抓到了但都是已入库条目"确是成功语义。真正偏宽的是下面
        test_all_platforms_return_zero_boundary_observation。
        """
        monkeypatch.setattr("services.job_hunter._PLATFORM_RETRY_DELAY", 0.0)
        svc = _make_hunter(tmp_path, {"boss": _Scraper(results=[_Listing("https://x/1", "t1")])})
        first = await svc.search(platforms=["boss"], keywords=["python"])
        assert first["success"] is True and first["added"] == 1, first
        second = await svc.search(platforms=["boss"], keywords=["python"])
        assert second["errors"] == [] and second["added"] == 0, second
        assert second["duplicates"] == 1, second
        assert second["success"] is True, second

    async def test_all_platforms_return_zero_boundary_observation(self, monkeypatch, tmp_path):
        """全平台"成功但零结果"（errors=[]、total_fetched=0、added=0）→ degraded。

        这是 D-PG-2 诚实性修复未覆盖的边界（反爬/风控页静默返回空列表最易命中）。
        2026-09-17 PD-H-1 已修：success 仍 True（无平台报错、任务语义完成），但
        追加 ``degraded: True`` + 提示 message，不再静默假成功。全重复边界
        （total_fetched>0）不算 degraded —— 见 test_all_duplicates_boundary_observation。
        """
        monkeypatch.setattr("services.job_hunter._PLATFORM_RETRY_DELAY", 0.0)
        svc = _make_hunter(tmp_path, {"boss": _Scraper(results=[]), "lagou": _Scraper(results=[])})
        out = await svc.search(platforms=["boss", "lagou"], keywords=["python"])
        assert out["errors"] == [] and out["total_fetched"] == 0 and out["added"] == 0, out
        assert out["success"] is True, out
        assert out.get("degraded") is True, f"零结果未标 degraded（PD-H-1 回归）：{out}"
        assert out.get("message"), f"degraded 缺提示文案：{out}"


# ═══════════════════════════════════════════════════════════════
# 验证点 8：weather 未知参数 / 正常传参 / 默认城市
# ═══════════════════════════════════════════════════════════════


class _FakeWeather:
    def __init__(self):
        self._default_city = "beijing"
        self.calls: list[tuple] = []

    def _fetch_live_weather(self, city, label):
        self.calls.append((city, label))
        return {"title": f"{city} 晴", "value": "25", "meta": "x", "tip": "y"}


class TestV8Weather:
    @staticmethod
    async def _reg(tmp_path):
        from backend.method_registry import MethodRegistry
        from backend.handlers._weather import register_weather

        svc = _FakeWeather()
        reg = MethodRegistry()
        register_weather(reg, SimpleNamespace(weather_service=svc))
        return reg, svc

    async def test_unknown_param_gives_minus_32602(self, tmp_path):
        from core.errors import InvalidParamsError

        reg, svc = await self._reg(tmp_path)
        for bad in ({"location": "shanghai"}, {"citx": "x"}, {"city": "北京", "extra": 1}):
            with pytest.raises(InvalidParamsError) as ei:
                await reg.dispatch("weather.current", bad)
            assert ei.value.code == -32602, (bad, ei.value)
        assert svc.calls == [], svc.calls

    async def test_city_param_still_works(self, tmp_path):
        reg, svc = await self._reg(tmp_path)
        out = await reg.dispatch("weather.current", {"city": "shanghai"})
        assert out["success"] is True, out
        assert svc.calls == [("shanghai", "shanghai")], svc.calls

    async def test_omitted_city_falls_back_to_default(self, tmp_path):
        reg, svc = await self._reg(tmp_path)
        out = await reg.dispatch("weather.current", {})
        assert out["success"] is True, out
        assert svc.calls == [("beijing", "beijing")], svc.calls

    async def test_empty_string_city_falls_back_to_default(self, tmp_path):
        reg, svc = await self._reg(tmp_path)
        out = await reg.dispatch("weather.current", {"city": ""})
        assert out["success"] is True and svc.calls == [("beijing", "beijing")], (out, svc.calls)

    async def test_direct_handler_rejects_unknown_param(self, tmp_path):
        from core.errors import InvalidParamsError

        reg, _ = await self._reg(tmp_path)
        with pytest.raises(InvalidParamsError):
            await reg._handlers["weather.current"](location="shanghai")


# ═══════════════════════════════════════════════════════════════
# 验证点 9：插件路由契约 12⊆15 是否真能抓到漂移
# ═══════════════════════════════════════════════════════════════


class TestV9PluginRouteContract:
    EXT_REL = Path("browser-extension") / "siyi-browser-assistant"
    TEST_REL = EXT_REL / "tests" / "backend-route-contract.test.js"

    def _prod_paths(self):
        test_js = REPO_ROOT / self.TEST_REL
        ext = REPO_ROOT / self.EXT_REL
        bridge = REPO_ROOT / "思忆集app" / "browser" / "assistant_bridge.py"
        return test_js, ext, bridge

    def test_contract_test_exists_and_bridge_dispatch_has_add_word(self):
        test_js, ext, bridge = self._prod_paths()
        assert test_js.exists() and ext.is_dir() and bridge.exists()
        src = bridge.read_text(encoding="utf-8")
        assert '"/api/browser-assistant/add-word"' in src
        assert re.search(r'route == "/api/browser-assistant/add-word"', src)

    def test_plugin_route_count_and_subset_static(self):
        _, ext, bridge = self._prod_paths()
        pat = re.compile(r"/api/browser-assistant/[a-z0-9-]+")
        backend = set(pat.findall(bridge.read_text(encoding="utf-8")))
        plugin: set[str] = set()
        for name in ("background.js", "bridge-auth.js", "bridge-utils.js", "content-script.js", "options.js", "sidepanel.js"):
            plugin |= set(pat.findall((ext / name).read_text(encoding="utf-8")))
        assert len(plugin) == 12, sorted(plugin)
        assert len(backend) == 15, sorted(backend)
        assert plugin <= backend, sorted(plugin - backend)

    def _replica(self, tmp_path: Path) -> Path:
        """把生产插件源码 + 后端桥源码复制成一份可破坏的等价树（不动生产）。"""
        import shutil

        root = tmp_path / "replica"
        (root / "思忆集app" / "browser").mkdir(parents=True)
        (root / self.EXT_REL / "tests").mkdir(parents=True)
        _, ext, bridge = self._prod_paths()
        shutil.copy2(bridge, root / "思忆集app" / "browser" / "assistant_bridge.py")
        for js in ext.glob("*.js"):
            shutil.copy2(js, root / self.EXT_REL / js.name)
        shutil.copy2(REPO_ROOT / self.TEST_REL, root / self.TEST_REL)
        return root

    @staticmethod
    def _run_node(root: Path):
        import subprocess

        proc = subprocess.run(
            ["node", "tests/backend-route-contract.test.js"],
            cwd=str(root / TestV9PluginRouteContract.EXT_REL),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def test_replica_baseline_green(self, tmp_path):
        root = self._replica(tmp_path)
        code, out = self._run_node(root)
        assert code == 0, out
        assert "12 个，后端路由 15 个" in out, out

    def test_drift_plugin_route_rename_is_caught(self, tmp_path):
        """漂移实验 1（插件侧改名）：把 background.js 的 add-word 改成 add-worx，
        子集断言必须变红——证明 12⊆15 不是空转。"""
        root = self._replica(tmp_path)
        bg = root / self.EXT_REL / "background.js"
        bg.write_text(bg.read_text(encoding="utf-8").replace("add-word", "add-worx"), encoding="utf-8")
        code, out = self._run_node(root)
        assert code != 0, "插件侧路由漂移竟未被抓到（契约测试失效）"
        assert "幽灵调用" in out and "add-worx" in out, out

    def test_drift_backend_route_removed_is_caught(self, tmp_path):
        """漂移实验 2（后端侧改名/删路由）：桥接源码里 add-word → xxx，必须红。"""
        root = self._replica(tmp_path)
        bridge = root / "思忆集app" / "browser" / "assistant_bridge.py"
        bridge.write_text(
            bridge.read_text(encoding="utf-8").replace("/api/browser-assistant/add-word", "/api/browser-assistant/gone"),
            encoding="utf-8",
        )
        code, out = self._run_node(root)
        assert code != 0, "后端路由被删竟未被抓到"
        assert "2 test(s) failed" in out, out

    def test_dead_dispatch_branch_is_NOT_caught(self, tmp_path):
        """【实测真相 · 第二断言的局限】把分发表分支改成恒假（字面量仍在），
        契约测试**仍然全绿** —— 第二条断言是纯文本正则，抓不到"路由字面量
        存在但分支已死"。真正的漂移护栏是第 1 条子集断言（实验 1/2 已证）。
        """
        root = self._replica(tmp_path)
        bridge = root / "思忆集app" / "browser" / "assistant_bridge.py"
        text = bridge.read_text(encoding="utf-8")
        mutated = text.replace(
            'if method == "POST" and route == "/api/browser-assistant/add-word":',
            'if False and route == "/api/browser-assistant/add-word":',
        )
        assert mutated != text, "变异未生效（实现已变，请更新本实验）"
        bridge.write_text(mutated, encoding="utf-8")
        code, out = self._run_node(root)
        assert code == 0, f"预期未被抓到（局限性），实际却红了：{out}"
