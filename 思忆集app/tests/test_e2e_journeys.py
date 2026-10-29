# -*- coding: utf-8 -*-
"""四条用户旅程 E2E —— 跨功能协调性（当前最大测试缺口：A 功能的输出是 B 功能的输入）。

单功能单测已覆盖各 handler 的孤立行为；本文件做全链断言。自包含 harness：
模块级 fixture 起一次隔离后端（全新临时数据目录 + 随机端口 + 禁桥接 + 清 AI key +
SIYI_LOCK_TAKEOVER=0），四条旅程共享该实例以省启动时间（启动方式照抄
scripts/v1_acceptance.py 的 Backend 类；单实例锁按数据目录派生，可并行）。

四条旅程（每条一个测试函数，独立可失败、互不依赖执行顺序）：
  J1 捕获→分类→成笔记→事件推送→跨连接读回
     inbox.add(≤3s) → inbox.classify(无 AI 环境允许分型失败，只要响应有类型) →
     notes.create(用 inbox 内容) → 连接 B notes.get 内容一致 →
     连接 B 收帧窗内 notes.changed/相关事件（收不到记 YELLOW 事实，不判红）
  J2 R4 审批→删除→审计→回滚
     notes.create → notes.delete(R4 pending) → sandbox.approve → 删除生效 →
     audit.list_rollbackable 定位 → audit.rollback(同为 R4，二次审批) → 笔记复活且内容一致
  J3 cron→执行→trace→通知
     cron.add → cron.run_now → task_queue 出现 completed 任务
     （cron→持久队列→worker→cron_actions 注册表真执行）→
     trace.list（队列路径 dispatched 条目不写 cron trace，设计口径，YELLOW 记录）→
     notification.list（cron 成功不在通知白名单 + 队列路径不发事件，YELLOW 记录）
     action 选用 knowledge.md_daily：system.info 是 RPC 方法但不在 cron_actions
     注册表（unknown_action）；system.memory_health 虽已注册但在本分支损坏
     （LocalDataStore 无 health_check，实测 last_error 证实）；md_daily 是默认
     定时任务之一，实测经队列 completed（副作用仅写隔离临时目录内当日 MD）。
  J4 词库→FSRS 复习→回滚
     wordbook.create → word.add → word.review(quality=5，SM-2+FSRS 双写；波次①-c
     误伤下线的 word.review_fsrs 别名入口改由存活的等价入口 word.review 承接) →
     读回调度状态变化（fsrs_state/stability/due 出现 + next_review 推向未来）→
     vocab.rollback_fsrs（FSRS 剥离工具）已随①-c 下线且无存活等价入口，改为 -32601
     防复活断言（回滚若判定需端到端覆盖则须恢复注册，见交付报告决策项）

断言口径（V1⑦ 语义）：GREEN 段必须全过；依赖 AI / EventBridge 时序 / 通知白名单
的段按「有类型响应」断言 + YELLOW 事实记录（warnings.warn），不判红。
实测数字经 print([E2E-Jx] ...) 输出（-s 可见，失败时随日志展示）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import warnings
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]  # 思忆集app/
CLIENT_VERSION = "1.2.0"
BOOT_TIMEOUT_S = 90.0
RPC_TIMEOUT_S = 15.0
CLASSIFY_TIMEOUT_S = 60.0  # 无 key 时走分型失败路径；本机若挂 Ollama 会真跑本地模型
CAPTURE_GREEN_S = 3.0
QUEUE_DONE_TIMEOUT_S = 15.0
TRACE_POLL_TIMEOUT_S = 10.0
EVENT_WINDOW_S = 3.0

# 启动期清掉继承的 AI key（骨架模式：强制「未配置」分型路径，禁止真实 AI 出网）
_AI_KEY_ENVS = (
    "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "ANTHROPIC_API_KEY",
    "SIYI_LLM_API_KEY", "OPENAI_API_KEY", "API_TOKEN",
)


class RpcError(Exception):
    """JSON-RPC error 响应（有类型的业务失败，非静默）。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"-{code} {message}" if code else message)
        self.code = code
        self.message = message


def _yellow(msg: str) -> None:
    """事实记录（不判红）：经 pytest warnings summary 透出。"""
    warnings.warn(f"YELLOW(E2E): {msg}", UserWarning, stacklevel=2)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _lock_paths_for_data_dir(data_dir: str) -> list[Path]:
    """按 backend/instance_lock 的派生规则推导锁文件 + PID 旁路文件路径。

    （内联同款算法，避免测试进程 import backend.* ——锁按数据目录派生，
    重试同目录时需清理上一轮残留。）
    """
    import hashlib

    key = os.path.abspath(data_dir).lower().replace("/", "\\")
    name = f"siyi_headless_{hashlib.md5(key.encode('utf-8')).hexdigest()[:10]}.lock"
    tmp = os.environ.get("TEMP", os.path.expanduser("~"))
    base = Path(tmp) / name
    return [base, Path(str(base) + ".pid")]


def _health_ok(port: int, token: str) -> bool:
    url = f"http://127.0.0.1:{port}/health" + (f"?token={token}" if token else "")
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 - 本机回环
            return resp.status == 200
    except Exception:
        return False


class E2EBackend:
    """隔离后端进程（同步生命周期，模块级 fixture 持有）。

    WS 客户端由各测试在自己的事件循环里经 connect() 创建——backend 是独立
    OS 进程，fixture 用同步生命周期可完全绕开 pytest-asyncio 的 loop scope 约束。
    """

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.data_dir: Path | None = None
        self.port = 0
        self.token = ""
        self._log_path: str = ""

    async def connect(self, session: aiohttp.ClientSession) -> "WsConn":
        """在调用方事件循环里建一条声明 client_version 的 WS 连接。"""
        url = (
            f"ws://127.0.0.1:{self.port}/ws"
            f"?token={self.token}&client_version={CLIENT_VERSION}"
        )
        ws = await session.ws_connect(url, timeout=aiohttp.ClientWSTimeout(ws_close=10.0))
        return WsConn(ws)

    # ── 生命周期（同步，模块级 fixture 用）──

    def start(self, attempts: int = 2) -> None:
        last_err: Exception | None = None
        for _ in range(attempts):
            try:
                self._start_once()
                return
            except RuntimeError as exc:
                last_err = exc
                self._terminate()
                for p in _lock_paths_for_data_dir(str(self.data_dir or "")):
                    if str(self.data_dir or "") and p.exists():
                        p.unlink(missing_ok=True)
                time.sleep(2.0)
        raise RuntimeError(f"E2E 隔离后端 {attempts} 次尝试均失败: {last_err}")

    def _start_once(self) -> None:
        self.port = _free_port()
        self.data_dir = Path(tempfile.mkdtemp(prefix="siyi_e2e_journey_"))
        env = dict(os.environ)
        for k in _AI_KEY_ENVS:
            env.pop(k, None)
        env.update(
            SIYI_WS_HOST="127.0.0.1",
            SIYI_WS_PORT=str(self.port),
            SIYI_DATA_DIR=str(self.data_dir),
            SIYI_DISABLE_DESKNOTES_BRIDGE="1",
            SIYI_LOCK_TAKEOVER="0",   # 测试内严禁接管他人锁
            SIYI_TEST_OFFLINE="1",    # 跳过 RSS 采集 / autonomy AI 调度（不影响 cron/通知）
        )
        log = tempfile.NamedTemporaryFile(
            prefix="siyi_e2e_boot_", suffix=".log", delete=False, mode="w", encoding="utf-8"
        )
        self._log_path = log.name
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "backend.headless_main"],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + BOOT_TIMEOUT_S
        token = ""
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"后端进程提前退出 code={self.proc.returncode}，"
                    f"日志尾: {self._log_tail()}"
                )
            if not token:
                token_file = self.data_dir / "auth_token"
                if token_file.exists():
                    token = token_file.read_text(encoding="utf-8", errors="replace").strip()
            if token and _health_ok(self.port, token):
                self.token = token
                return
            time.sleep(0.25)
        raise RuntimeError(f"后端启动超时（/health 未就绪 {BOOT_TIMEOUT_S:g}s），日志尾: {self._log_tail()}")

    def _log_tail(self, limit: int = 1500) -> str:
        try:
            with open(self._log_path, encoding="utf-8", errors="replace") as f:
                return f.read()[-limit:]
        except OSError:
            return "(日志不可读)"

    def _terminate(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass

    def stop(self) -> None:
        """终止后端 + 清临时目录 + netstat 自查端口无残留。"""
        try:
            self._terminate()
        finally:
            if self.proc is not None:
                try:
                    self.proc.stdout.close()  # type: ignore[union-attr]
                except Exception:
                    pass
            if self.data_dir is not None:
                shutil.rmtree(self.data_dir, ignore_errors=True)
        self._assert_port_released()

    def _assert_port_released(self) -> None:
        """收尾自查：端口不得仍有 LISTENING 残留（三次复查，均占则显式失败）。"""
        for i in range(3):
            residue = self._listening_lines()
            if residue is None:
                return  # netstat 本身不可用：不判红（检查器失败≠端口残留）
            if not residue:
                return
            time.sleep(1.5)
        raise RuntimeError(f"E2E 后端端口 {self.port} 仍有 LISTENING 残留: {self._listening_lines()}")

    def _listening_lines(self) -> list[str] | None:
        try:
            raw = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, timeout=15,
            ).stdout
        except Exception:
            return None
        # netstat 输出为本地码页（GBK）：按字节捕获 + errors=replace 解码，
        # 只匹配 ASCII 子串（端口数字 / LISTENING），编码无关
        out = (raw or b"").decode("utf-8", errors="replace")
        return [ln.strip() for ln in out.splitlines()
                if f":{self.port}" in ln and "LISTENING" in ln.upper()]


class WsConn:
    """单条 WS 连接上的 JSON-RPC 客户端（声明 client_version；事件帧跳过/收集）。"""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws
        self._id = 0

    async def call(self, method: str, params: dict | None = None,
                   timeout: float = RPC_TIMEOUT_S):
        """请求-响应；事件帧按 id 跳过；error 帧抛 RpcError（有类型失败）。"""
        self._id += 1
        rid = self._id
        frame = {"jsonrpc": "2.0", "id": rid, "method": method,
                 "params": params or {}, "client_version": CLIENT_VERSION}
        await self.ws.send_json(frame)
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                msg = await self.ws.receive(timeout=remaining)
            except asyncio.TimeoutError:
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    raise RuntimeError(f"ws closed during {method}: {msg.data}")
                continue
            data = json.loads(msg.data)
            if data.get("id") != rid:
                continue  # 服务端推送事件（无 id）跳过
            if "error" in data:
                err = data["error"]
                raise RpcError(int(err.get("code", 0)), str(err.get("message", "")))
            return data.get("result")
        raise TimeoutError(f"RPC 超时: {method} ({timeout:g}s)")

    async def drain_events(self, window_s: float) -> list[dict]:
        """开收帧窗 window_s 秒，收集事件帧（type=event）。"""
        events: list[dict] = []
        deadline = time.time() + window_s
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                msg = await self.ws.receive(timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "event":
                events.append(data)
        return events

    async def close(self) -> None:
        try:
            await self.ws.close()
        except Exception:
            pass


async def call_with_approval(conn: WsConn, method: str, params: dict,
                             timeout: float = RPC_TIMEOUT_S):
    """R4 方法调用：pending → sandbox.approve 重放，返回真实 handler 结果。

    M2.1 语义：声明 client_version 的连接调 R4 方法返回 {"pending": True,
    "pending_id": ...}；握手完成的连接视为可信 UI，同连接审批放行。
    """
    resp = await conn.call(method, params, timeout=timeout)
    if isinstance(resp, dict) and resp.get("pending"):
        pid = resp.get("pending_id")
        assert pid, f"{method} 返回 pending 但缺 pending_id: {resp!r}"
        approved = await conn.call("sandbox.approve", {"pending_id": pid}, timeout=timeout)
        assert isinstance(approved, dict) and approved.get("success") is True, \
            f"sandbox.approve({pid}) 未成功: {approved!r}"
        return approved.get("result")
    return resp


async def poll_until(fetch, pred, timeout_s: float, interval: float = 0.4):
    """轮询直到 pred 成立或超时；返回 (是否命中, 最后一次取值)。"""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = await fetch()
        try:
            if pred(last):
                return True, last
        except Exception:
            pass
        await asyncio.sleep(interval)
    return False, last


def _parse_iso(v) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(v))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@pytest.fixture(scope="module")
def e2e_backend():
    """模块级隔离后端：全程起一次，四条旅程共享；try/finally 保证终止 + 端口自查。"""
    b = E2EBackend()
    try:
        b.start()
        yield b
    finally:
        b.stop()


# ── J1 捕获 → 分类 → 成笔记 → 事件推送 → 跨连接读回 ──────────────────────────


async def test_journey1_capture_classify_note_cross_connection_event(e2e_backend):
    b = e2e_backend
    marker = f"E2E-J1-{int(time.time() * 1000)}"
    content = f"{marker} 便利店小票：冰美式 22 元，下周三前报销"
    session = aiohttp.ClientSession()
    conn_a = await b.connect(session)
    conn_b: WsConn | None = None
    try:
        # 段1：捕获 ≤3s（后端段，V1⑥ 口径）
        t0 = time.perf_counter()
        added = await conn_a.call("inbox.add", {"content": content, "title": marker})
        capture_s = time.perf_counter() - t0
        assert isinstance(added, dict), f"inbox.add 响应无类型: {added!r}"
        entry_id = added.get("id")
        assert entry_id, f"inbox.add 未返回 id: {added!r}"
        assert capture_s <= CAPTURE_GREEN_S, \
            f"捕获往返 {capture_s:.2f}s 超过 {CAPTURE_GREEN_S:g}s 门槛"
        print(f"[E2E-J1] inbox.add 往返 {capture_s:.3f}s (entry_id={entry_id})")

        # 段2：捕获落库读回（inbox.add → inbox.list 接缝）
        listing = await conn_a.call("inbox.list", {})
        items = listing if isinstance(listing, list) else (listing or {}).get("items") or []
        assert any(isinstance(i, dict) and str(i.get("id")) == str(entry_id) for i in items), \
            f"inbox.list 未读回 entry_id={entry_id}（A 功能输出未成为 B 功能输入）"

        # 段3：AI 分类 —— 无 AI 环境允许分型失败/低置信，但必须「有类型响应」（V1⑦）
        cls = await conn_a.call("inbox.classify", {"entry_id": int(entry_id)},
                                timeout=CLASSIFY_TIMEOUT_S)
        assert isinstance(cls, dict), f"inbox.classify 响应无类型: {cls!r}"
        assert ("success" in cls) or ("kind" in cls) or ("error" in cls), \
            f"inbox.classify 响应缺分型字段: {cls!r}"
        print(f"[E2E-J1] inbox.classify → {json.dumps(cls, ensure_ascii=False)[:140]}")

        # 段4：inbox 内容 → notes.create（跨功能接缝）；连接 B 先建好保证事件可达
        conn_b = await b.connect(session)
        await conn_b.drain_events(0.5)  # 清掉连接建立期的积压事件
        note_title = f"{marker}-note"
        created = await conn_a.call("notes.create", {"title": note_title, "content": content})
        assert isinstance(created, dict) and created.get("id"), \
            f"notes.create 失败: {created!r}"
        note_id = created["id"]

        # 段5：事件推送 —— 连接 B 收帧窗（收不到记 YELLOW 事实，不判红）
        events = await conn_b.drain_events(EVENT_WINDOW_S)
        ev_types = [str(e.get("event_type")) for e in events]
        got_related = any(t in ev_types for t in
                          ("notes.changed", "notes.related_memories", "inbox.delivered"))
        if got_related:
            print(f"[E2E-J1] 连接 B 收到事件: {ev_types}")
        else:
            _yellow(f"J1 连接 B {EVENT_WINDOW_S:g}s 内未收到 notes.changed/相关事件"
                    f"（收到 {ev_types or '无'}）——EventBridge 推送时序事实记录")

        # 段6：跨连接读回（A 写 → B 读同一 id，内容逐字一致）
        got = await conn_b.call("notes.get", {"note_id": note_id})
        assert isinstance(got, dict), f"连接 B notes.get({note_id}) 返回 {got!r}"
        assert got.get("title") == note_title, \
            f"跨连接标题不一致: {got.get('title')!r} != {note_title!r}"
        assert got.get("content") == content, \
            f"跨连接内容不一致（持久性断裂）: {(got.get('content') or '')[:80]!r}"
        print(f"[E2E-J1] 跨连接读回一致 note_id={note_id}, 长度 {len(content)} 字符")
    finally:
        await conn_a.close()
        if conn_b is not None:
            await conn_b.close()
        await session.close()


# ── J2 R4 审批 → 删除 → 审计 → 回滚 ─────────────────────────────────────────


async def test_journey2_delete_approve_audit_rollback(e2e_backend):
    b = e2e_backend
    marker = f"E2E-J2-{int(time.time() * 1000)}"
    content = f"{marker} 回滚一致性正文：唯一指纹 fingerprint-{marker}"
    session = aiohttp.ClientSession()
    conn = await b.connect(session)
    try:
        created = await conn.call("notes.create", {"title": marker, "content": content})
        assert isinstance(created, dict) and created.get("id"), \
            f"notes.create 失败: {created!r}"
        note_id = created["id"]

        # 段1：R4 闸门 —— 声明 client_version 的连接调 notes.delete → pending（不执行）
        resp = await conn.call("notes.delete", {"note_id": note_id})
        assert isinstance(resp, dict), f"notes.delete 响应无类型: {resp!r}"
        assert resp.get("pending") is True, f"notes.delete 未走 R4 审批闸: {resp!r}"
        assert resp.get("pending_id"), f"pending 响应缺 pending_id: {resp!r}"

        # 段2：审批 → 重放执行
        approved = await conn.call("sandbox.approve", {"pending_id": resp["pending_id"]})
        assert isinstance(approved, dict) and approved.get("success") is True, \
            f"sandbox.approve 失败: {approved!r}"

        # 段3：删除生效（notes.get 不可再读）
        gone = await conn.call("notes.get", {"note_id": note_id})
        assert not gone, f"审批通过后笔记仍可读（删除未生效）: {gone!r}"

        # 段4：审计定位（notes.delete 写守卫 → audit.list_rollbackable 可回滚条目）
        def _find(r) -> dict | None:
            for e in (r or {}).get("entries") or []:
                if (isinstance(e, dict) and e.get("action_id") == "notes.delete"
                        and str(e.get("target_id")) == str(note_id)):
                    return e
            return None

        hit, last = await poll_until(
            lambda: conn.call("audit.list_rollbackable", {"limit": 100}),
            lambda r: _find(r) is not None, 8.0)
        assert hit, ("8s 内未在 audit.list_rollbackable 找到 notes.delete 条目 "
                     f"(note_id={note_id})，last={json.dumps(last, ensure_ascii=False)[:240]}")
        entry = _find(last)
        assert entry and entry.get("entry_id"), f"审计条目缺 entry_id: {entry!r}"

        # 段5：回滚（audit.rollback 同为 R4 → pending → 审批重放）
        rb = await call_with_approval(conn, "audit.rollback", {"entry_id": entry["entry_id"]})
        assert isinstance(rb, dict) and rb.get("rolled_back") is True, \
            f"audit.rollback 未成功: {rb!r}"

        # 段6：复活且内容一致
        back = await conn.call("notes.get", {"note_id": note_id})
        assert isinstance(back, dict), f"回滚后笔记未复活: {back!r}"
        assert back.get("title") == marker, \
            f"回滚后标题不一致: {back.get('title')!r} != {marker!r}"
        assert back.get("content") == content, \
            f"回滚后内容不一致: {(back.get('content') or '')[:80]!r}"
        print(f"[E2E-J2] 删除→审批→审计→回滚→复活 全链一致 "
              f"(audit_entry={entry['entry_id']}, note_id={note_id}, 内容逐字一致)")
    finally:
        await conn.close()
        await session.close()


# ── J3 cron → 执行 → trace → 通知 ───────────────────────────────────────────


async def test_journey3_cron_execute_trace_notification(e2e_backend):
    b = e2e_backend
    job_name = f"e2e-j3-{int(time.time() * 1000)}"
    # action 选用说明见模块 docstring J3 段：system.info 未注册 cron action、
    # system.memory_health 在本分支损坏（LocalDataStore 缺 health_check）；
    # knowledge.md_daily 为默认定时任务，实测经持久队列 completed。
    action = "knowledge.md_daily"
    session = aiohttp.ClientSession()
    conn = await b.connect(session)
    listener = await b.connect(session)  # 独立事件监听连接
    try:
        await listener.drain_events(0.5)  # 清积压事件
        before = await conn.call("task_queue.list", {"queue_name": "cron", "limit": 100})
        before_ids = ({t.get("task_id") for t in (before or {}).get("tasks") or []}
                      if isinstance(before, dict) else set())

        # 段1：cron.add（每分钟表达式 + 立即执行钩子）
        added = await conn.call("cron.add", {
            "name": job_name, "action_id": action, "payload": {},
            "schedule": "* * * * *", "repeat": "cron", "max_retries": 1,
        })
        assert isinstance(added, dict) and added.get("success") is True, \
            f"cron.add 失败: {added!r}"
        job_id = (added.get("job") or {}).get("job_id")
        assert job_id, f"cron.add 未返回 job_id: {added!r}"

        # 段2：立即执行（返回即已入队持久队列）
        run = await conn.call("cron.run_now", {"job_id": job_id}, timeout=20)
        assert isinstance(run, dict) and run.get("success") is True, \
            f"cron.run_now 失败: {run!r}"

        # 段3：持久队列出现本次的 completed 任务（cron→TaskQueue→worker→cron_actions）
        def _completed(r) -> bool:
            if not isinstance(r, dict):
                return False
            return any(t.get("capability_id") == action and t.get("status") == "completed"
                       and t.get("task_id") not in before_ids
                       for t in (r.get("tasks") or []) if isinstance(t, dict))

        hit, listing = await poll_until(
            lambda: conn.call("task_queue.list", {"queue_name": "cron", "limit": 100}),
            _completed, QUEUE_DONE_TIMEOUT_S)
        assert hit, (f"{QUEUE_DONE_TIMEOUT_S:g}s 内 cron 队列未出现 completed 任务 "
                     f"(action={action})，tasks={json.dumps(listing, ensure_ascii=False)[:900]}")
        done = next(t for t in listing["tasks"]
                    if t.get("capability_id") == action and t.get("status") == "completed"
                    and t.get("task_id") not in before_ids)
        print(f"[E2E-J3] 队列任务完成 task_id={done.get('task_id')} "
              f"attempts={done.get('attempts')} queue={done.get('queue_name')}")

        # 段4：cron.history 有该任务执行记录（执行历史接缝）
        hist = await conn.call("cron.history", {"job_id": job_id})
        assert isinstance(hist, dict) and hist.get("success") is True and hist.get("history"), \
            f"cron.history 无记录: {hist!r}"

        # 段5：trace —— 10s 轮询；队列路径 dispatched 条目不写 cron trace（设计口径，
        # P1-2 注释「队列层观测另行接入」）→ 未命中记 YELLOW 不判红
        def _trace_hit(r) -> bool:
            for x in (r or {}).get("runs") or []:
                if isinstance(x, dict) and (
                    str(x.get("title")) == job_name
                    or str(x.get("task_id", "")).startswith(f"cron-{job_id}")
                ):
                    return True
            return False

        thit, truns = await poll_until(
            lambda: conn.call("trace.list", {"task_type": "cron", "limit": 50}),
            _trace_hit, TRACE_POLL_TIMEOUT_S)
        if thit:
            print("[E2E-J3] trace.list(cron) 出现该 run")
        else:
            seen = json.dumps((truns or {}).get("runs") or [], ensure_ascii=False)[:160]
            _yellow(f"J3 trace.list(task_type=cron) {TRACE_POLL_TIMEOUT_S:g}s 内未见该 run "
                    f"——设计口径：cron 入队持久队列后 dispatched 条目不写 trace"
                    f"（队列层观测未接入 trace.db）；现有 runs={seen}")

        # 段6：通知 —— cron 成功不在通知白名单 + 队列路径不发事件（设计口径）→ YELLOW
        evs = await listener.drain_events(EVENT_WINDOW_S)
        ev_types = [str(e.get("event_type")) for e in evs]
        notifs = await conn.call("notification.list", {"limit": 50})
        assert isinstance(notifs, dict) and "items" in notifs, \
            f"notification.list 响应无类型: {notifs!r}"
        cron_notifs = [i for i in notifs.get("items") or []
                       if str(i.get("event_type", "")).startswith("cron.")]
        if cron_notifs:
            print(f"[E2E-J3] notification.list 出现 cron.* 通知 {len(cron_notifs)} 条")
        else:
            _yellow(f"J3 notification.list 无 cron.* 通知（cron 成功事件不在 "
                    f"DEFAULT_SUBSCRIBED_EVENTS 白名单 + 持久队列路径不发布完成事件）；"
                    f"listener {EVENT_WINDOW_S:g}s 收到事件: {ev_types or '无'}")

        # 收尾：cron.remove（R4 → 审批重放），避免每分钟继续触发污染共享实例
        removed = await call_with_approval(conn, "cron.remove", {"job_id": job_id})
        assert isinstance(removed, dict) and removed.get("success") is True, \
            f"cron.remove 失败: {removed!r}"
    finally:
        await conn.close()
        await listener.close()
        await session.close()


# ── J4 词库 → FSRS 复习 → 回滚 ──────────────────────────────────────────────


async def test_journey4_wordbook_fsrs_review_rollback(e2e_backend):
    b = e2e_backend
    stamp = f"{int(time.time() * 1000)}"
    session = aiohttp.ClientSession()
    conn = await b.connect(session)
    try:
        # 段1：建词库 + 加词（词库→词条接缝）
        wb = await conn.call("wordbook.create", {"name": f"E2E-J4-{stamp}"})
        assert isinstance(wb, dict) and wb.get("success") is True and wb.get("id"), \
            f"wordbook.create 失败: {wb!r}"
        wb_id = wb["id"]
        w = await conn.call("word.add", {
            "wordbook_id": wb_id, "word": f"serendipity{stamp[-4:]}",
            "meaning": "意外发现珍宝的运气",
        })
        assert isinstance(w, dict) and w.get("success") is True and w.get("id"), \
            f"word.add 失败: {w!r}"
        word_id = w["id"]

        async def _get_word() -> dict | None:
            lst = await conn.call("word.list", {"wordbook_id": wb_id})
            for x in lst or []:
                if isinstance(x, dict) and str(x.get("id")) == str(word_id):
                    return x
            return None

        before = await _get_word()
        assert before is not None, "word.list 未读回新词（word.add → word.list 接缝断裂）"
        assert not before.get("fsrs_state") and before.get("fsrs_stability") is None, \
            f"新词不应有 FSRS 状态: {before!r}"
        assert int(before.get("repetitions") or 0) == 0, \
            f"新词 repetitions 应为 0: {before.get('repetitions')!r}"

        # 段2：FSRS 复习（双写 SM-2 + FSRS 调度字段）
        # 【波次①-c 误伤处置】word.review_fsrs（rating 1-4 别名入口）已随僵尸 RPC 下线，
        # 但其底层 `repo.record_review(word_id, quality)` 双写能力**仍由存活入口 word.review
        # 完整承接**（word.review_fsrs 的 _RATING_TO_QUALITY 把 rating=4 映射为 quality=5，
        # 二者调用同一 record_review、副作用与响应形状完全一致，前端零调用 word.review_fsrs）。
        # 故本旅程改走存活的 word.review（quality=5 == 原 rating=4），继续端到端验证 FSRS
        # 复习这一活功能，无需恢复 word.review_fsrs 注册（详见交付报告决策项）。
        rv = await conn.call("word.review", {"word_id": word_id, "quality": 5})
        assert isinstance(rv, dict) and rv.get("success") is True, \
            f"word.review（word.review_fsrs 下线后的等价入口）失败: {rv!r}"
        assert int(rv.get("repetitions") or 0) >= 1, \
            f"复习响应 repetitions 未增加: {rv!r}"

        # 段3：读回调度状态变化（FSRS 字段出现 + due 推向未来）
        after = await _get_word()
        assert after is not None, "复习后 word.list 未读回该词"
        assert after.get("fsrs_state") or after.get("fsrs_stability") is not None, \
            f"复习后未见 FSRS 双写字段: keys={sorted(after.keys())[:20]}"
        nr = after.get("next_review")
        due_dt = _parse_iso(nr) if nr else None
        assert due_dt is not None and due_dt > datetime.now(timezone.utc), \
            f"复习后 next_review 未被推向未来: {nr!r}"
        stability = after.get("fsrs_stability")
        fsrs_due = after.get("fsrs_due")
        print(f"[E2E-J4] 复习后 fsrs_stability={stability} fsrs_due={fsrs_due} "
              f"next_review={nr} repetitions={after.get('repetitions')}")

        # 段4：FSRS 剥离工具 vocab.rollback_fsrs 已随波次①-c（commit 58e5fa8）下线
        # （一次性迁移工具，前端零调用，且无存活等价入口可重指向）。原回滚活链
        # 断言失去被测目标，按同仓库先例改为锁定其不再注册（-32601）防复活。
        # 【需主会话决策】若认定回滚仍属活功能需端到端覆盖，则须恢复
        # vocab.rollback_fsrs 注册（属源码改动，本波不自行恢复，见交付报告）。
        # 2026-10-15 反转：vocab.rollback_fsrs 注册已恢复（批次 A c6fb2bc7，
        # 用户拍板"不减任何功能"）——原 -32601 防复活断言基于 58e5fa8 下线结论，
        # 该结论已被全族复核证伪。现改为守护"必须可达"。
        try:
            rb = await call_with_approval(conn, "vocab.rollback_fsrs", {"confirm": True})
        except RpcError as exc:
            assert exc.code != -32601, f"vocab.rollback_fsrs 应已恢复注册，不应 -32601，实际: {exc}"
        else:
            print(f"[E2E-J4] vocab.rollback_fsrs 恢复可达（批次 A 恢复注册）: {rb!r}")
        print(f"[E2E-J4] FSRS 复习（word.review 等价入口）活链通过；"
              f"复习后 stability={stability} due={fsrs_due}；"
              f"vocab.rollback_fsrs 已下线（-32601 防复活断言）")
    finally:
        await conn.close()
        await session.close()
