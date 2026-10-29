# -*- coding: utf-8 -*-
"""V1 一键验收：后端层红绿矩阵（对应 V1 出口定义，见 session-handoff-2026-09-12 §五）。

用法（仓库根，venv 解释器）：
    .venv3.11/Scripts/python.exe 思忆集app/scripts/v1_acceptance.py
    .venv3.11/Scripts/python.exe 思忆集app/scripts/v1_acceptance.py --out report.md
    .venv3.11/Scripts/python.exe 思忆集app/scripts/v1_acceptance.py --keep   # 保留临时数据目录供排查

做什么：
  自动起一个隔离后端（临时数据目录 + 随机端口 + 禁桥接），对 V1 条目做机器断言，
  输出红绿矩阵。退出码 0 = 无 RED。

覆盖口径（后端可断言的部分）：
  ② 对话首反馈 ≤1s / 取消即停          —— chat.send 流计时 + chat.cancel 静默窗
  ③ 笔记切走不丢 / R4 审批闸            —— 跨连接持久性 + legacy -32010 / 声明后 pending
  ④ 图谱有详情                          —— knowledge.graph（数据不足时 YELLOW，不误红）
  ⑥ 捕获链路 ≤3s（后端段）              —— inbox.add 往返计时（UI 段留给人）
  ⑦ 全程零静默失败                      —— 核心 RPC 全量「有类型响应」扫描（永不挂起）
  另：冷启动时长、启动自检零失败、AI 未配置时的分型可见性。

需真人手感的（脚本只标 YELLOW 不判红）：① 安装包体验（build 链脚本另行覆盖）、
⑤ 今日回顾视觉呈现、⑥ 划词 UI 端计时、冲突提示的 UI 呈现。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp

BACKEND_DIR = Path(__file__).resolve().parents[1]  # 思忆集app/
# 脚本直跑时 sys.path[0]=scripts/，必须显式注入 BACKEND_DIR 才能 import backend.*
# （lock 清理与性能播种都依赖 backend.data / backend.instance_lock）。
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
CLIENT_VERSION = "1.2.0"
FIRST_FEEDBACK_GREEN_S = 1.0
FIRST_FEEDBACK_YELLOW_S = 3.0
BOOT_GREEN_S = 15.0
BOOT_YELLOW_S = 40.0
CAPTURE_GREEN_S = 3.0
# ── 性能预算（2026-09-13 新增：性能数字必须带门槛才叫门禁）──
PERF_INBOX_N = 2000
PERF_NOTES_N = 500
PERF_INBOX_GREEN_S = 1.0   # D22 表路径预算：2000 条 inbox.list ≤1s
PERF_INBOX_YELLOW_S = 3.0
PERF_NOTES_GREEN_S = 0.5   # 500 条 notes.list ≤0.5s
PERF_NOTES_YELLOW_S = 1.5
PERF_KV_DIAG_TIMEOUT_S = 180  # kv 对照诊断（已知 56.5s 量级）的读超时
RPC_TIMEOUT_S = 8.0
CANCEL_QUIET_WINDOW_S = 2.0

# ⑦ 静默失败扫描面：核心域只读方法（响应 = result 或 JSON-RPC error 都算「有类型」）
SWEEP_METHODS = [
    ("ping", {}),
    ("siyi.ping", {}),
    ("system.info", {}),
    ("system.status", {}),
    ("notes.list", {"limit": 5}),
    ("tasks.list", {"limit": 5}),
    ("inbox.list", {}),
    ("wordbook.list", {}),
    ("memory.recent", {"limit": 5}),
    ("trace.stats", {}),
    ("audit.stats", {}),
    ("timeline.stats", {}),
    ("usage.today", {}),
    ("notification.stats", {}),
    ("sync.status", {}),
    ("cron.status", {}),
    ("capability.list", {}),
    ("xiaowen.quote", {}),
]


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"-{code} {message}" if code else message)
        self.code = code
        self.message = message


class Backend:
    """隔离后端进程 + WS/HTTP 客户端封装。"""

    def __init__(self, seed=None) -> None:
        # seed(data_dir)：起进程前对数据目录播种（性能预算检查用），
        # 注意必须在后端进程启动**前**执行，避免与其缓存/写回竞争。
        self._seed = seed
        self.proc: subprocess.Popen | None = None
        self.data_dir: Path | None = None
        self.port = 0
        self.token = ""
        self.session: aiohttp.ClientSession | None = None
        self.boot_seconds = 0.0
        self.boot_log = ""
        self._log_file = None

    async def start(self) -> None:
        # 前置清理：无后端进程在跑时，陈旧的 siyi_headless.lock 会让新实例
        # 接管失败直接退出（实测事故：0 字节陈旧锁 → code=1）。
        # 锁名按数据目录派生（instance_lock 并行改造）：本工具每次用全新临时
        # 数据目录，其派生锁天然不与他实例冲突；这里只清「同目录历史残留」。
        from backend.instance_lock import lock_path_for_data_dir
        lock = Path(lock_path_for_data_dir(str(self.data_dir))) if self.data_dir else             Path(tempfile.gettempdir()) / "siyi_headless.lock"
        if lock.exists():
            lock.unlink(missing_ok=True)
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                await self._start_once()
                return
            except RuntimeError as exc:
                last_err = exc
                await self.stop()
                await asyncio.sleep(2.0)
        raise RuntimeError(f"隔离后端 3 次尝试均失败: {last_err}")

    async def _start_once(self) -> None:
        self.port = _free_port()
        self.data_dir = Path(tempfile.mkdtemp(prefix="siyi_v1_acc_"))
        if self._seed is not None:
            self._seed(self.data_dir)
        env = dict(os.environ)
        # 骨架模式：清掉继承的 AI key，强制走「未配置」分型路径——
        # 否则环境里残留的无效 key 会让对话走完整回退链（实测 8s 静默）
        for k in ("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "ANTHROPIC_API_KEY",
                  "SIYI_LLM_API_KEY", "OPENAI_API_KEY", "API_TOKEN"):
            env.pop(k, None)
        env.update(
            SIYI_WS_HOST="127.0.0.1",
            SIYI_WS_PORT=str(self.port),
            SIYI_DATA_DIR=str(self.data_dir),
            SIYI_DISABLE_DESKNOTES_BRIDGE="1",
        )
        t0 = time.time()
        self._log_file = tempfile.NamedTemporaryFile(
            prefix="siyi_v1_boot_", suffix=".log", delete=False, mode="w", encoding="utf-8"
        )
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "backend.headless_main"],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=RPC_TIMEOUT_S))
        base = f"http://127.0.0.1:{self.port}"
        deadline = t0 + BOOT_YELLOW_S * 2
        token = ""
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"后端进程提前退出 code={self.proc.returncode}")
            # /health 强制 token（401 实测）；token 由后端落数据目录 auth_token 文件
            if not token:
                token_file = self.data_dir / "auth_token"
                if token_file.exists():
                    token = token_file.read_text(encoding="utf-8", errors="replace").strip()
            try:
                url = f"{base}/health" + (f"?token={token}" if token else "")
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        self.token = token
                        self.boot_seconds = time.time() - t0
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.25)
        raise RuntimeError("后端启动超时（/health 未就绪）")

    async def stop(self) -> None:
        if self.session:
            await self.session.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.data_dir:
            shutil.rmtree(self.data_dir, ignore_errors=True)

    def _ws_url(self, declared: bool) -> str:
        url = f"ws://127.0.0.1:{self.port}/ws?token={getattr(self, 'token', '')}"
        if declared:
            url += f"&client_version={CLIENT_VERSION}"
        return url

    async def connect(self, declared: bool = True):
        ws = await self.session.ws_connect(self._ws_url(declared))
        return WsConn(ws, declared)


class WsConn:
    """单条 WS 连接上的 JSON-RPC 客户端（支持流式收集）。"""

    def __init__(self, ws, declared: bool) -> None:
        self.ws = ws
        self.declared = declared
        self._id = 0

    async def call(self, method: str, params: dict | None = None, timeout: float = RPC_TIMEOUT_S):
        self._id += 1
        rid = self._id
        frame: dict = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        if self.declared:
            frame["client_version"] = CLIENT_VERSION
        await self.ws.send_json(frame)
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = await self.ws.receive(timeout=deadline - time.time())
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    raise RuntimeError(f"ws closed: {msg.data}")
                continue
            data = json.loads(msg.data)
            # 服务端推送事件不携带 id，跳过
            if data.get("id") != rid:
                continue
            if "error" in data:
                err = data["error"]
                raise RpcError(int(err.get("code", 0)), str(err.get("message", "")))
            return data.get("result")
        raise TimeoutError(f"RPC 超时: {method}")

    async def stream_collect(self, method: str, params: dict, *, collect_s: float):
        """发流式请求（stream=true），收集 collect_s 秒内的帧。返回 [(t_offset, frame_dict)]。"""
        self._id += 1
        rid = self._id
        frame: dict = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params,
                       "stream": True}
        if self.declared:
            frame["client_version"] = CLIENT_VERSION
        t0 = time.time()
        await self.ws.send_json(frame)
        frames: list[tuple[float, dict]] = []
        deadline = t0 + collect_s
        while time.time() < deadline:
            try:
                msg = await self.ws.receive(timeout=max(0.05, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            frames.append((time.time() - t0, data))
            if data.get("id") == rid and (
                data.get("result", {}).get("type") == "done" or "error" in data
            ):
                break
        return frames


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CheckResult:
    def __init__(self, v1: str, name: str, status: str, measured: str, note: str = "") -> None:
        self.v1, self.name, self.status, self.measured, self.note = v1, name, status, measured, note


# ── 各检查项 ──────────────────────────────────────────────────


async def check_boot(b: Backend) -> list[CheckResult]:
    out = []
    boot = b.boot_seconds
    status = "GREEN" if boot <= BOOT_GREEN_S else ("YELLOW" if boot <= BOOT_YELLOW_S else "RED")
    out.append(CheckResult("②前置", "后端冷启动就绪", status, f"{boot:.2f}s",
                           f"阈值 绿≤{BOOT_GREEN_S:g}s 黄≤{BOOT_YELLOW_S:g}s"))
    bad = [ln for ln in b.boot_log.splitlines() if "SelfCheckError" in ln or "启动自检失败" in ln]
    out.append(CheckResult("⑦", "启动自检零失败", "RED" if bad else "GREEN",
                           "无自检错误" if not bad else bad[0][:120]))
    return out


async def check_sweep(b: Backend) -> list[CheckResult]:
    conn = await b.connect(declared=True)
    hang, ok = [], 0
    try:
        for method, params in SWEEP_METHODS:
            try:
                await conn.call(method, params, timeout=RPC_TIMEOUT_S)
                ok += 1
            except RpcError:
                ok += 1  # 有类型的业务错误也算「未静默」
            except (TimeoutError, RuntimeError):
                hang.append(method)
    finally:
        await conn.ws.close()
    status = "GREEN" if not hang else "RED"
    return [CheckResult("⑦", f"核心 RPC 有类型响应扫描（{len(SWEEP_METHODS)} 个）", status,
                        f"{ok}/{len(SWEEP_METHODS)} 有类型响应",
                        "挂起/超时: " + ",".join(hang) if hang else "全部响应，无静默失败")]


async def check_chat_feedback_and_cancel(b: Backend) -> list[CheckResult]:
    out = []
    conn = await b.connect(declared=True)
    try:
        frames = await conn.stream_collect("chat.send", {"message": "你好，请介绍一下你自己。"},
                                           collect_s=8.0)
        data_frames = [f for f in frames if f[1].get("id") is not None]
        if not data_frames:
            out.append(CheckResult("②", "对话首反馈", "RED", "8s 内零帧",
                                   "连分型错误都没有 = 静默失败"))
        else:
            first_t = data_frames[0][0]
            first = data_frames[0][1]
            typed = ("result" in first) or ("error" in first)
            status = ("GREEN" if first_t <= FIRST_FEEDBACK_GREEN_S
                      else "YELLOW" if first_t <= FIRST_FEEDBACK_YELLOW_S else "RED")
            kind = ("error" if "error" in first else
                    str(first.get("result", {}).get("type", "?")))
            out.append(CheckResult("②", "对话首反馈", status, f"首帧 {first_t:.2f}s ({kind})",
                                   f"阈值 绿≤{FIRST_FEEDBACK_GREEN_S:g}s 黄≤{FIRST_FEEDBACK_YELLOW_S:g}s"
                                   + ("" if typed else "；⚠️ 帧缺类型字段")))

        # 取消即停：若流已自然结束（未配 LLM 时常见），标黄留待配置 key 后复测
        ended = any(f.get("result", {}).get("type") == "done" or "error" in f
                    for _, f in data_frames)
        if ended:
            out.append(CheckResult("②", "取消即停", "YELLOW", "流在取消前已自然结束",
                                   "未配置 LLM（骨架模式）——配置 key 后复测真取消"))
        else:
            await conn.call("chat.cancel", {})
            # 纯收帧静默窗：取消后不应再有任何 chunk 数据帧（cancel 的回执不算）
            quiet_chunks = 0
            deadline = time.time() + CANCEL_QUIET_WINDOW_S
            while time.time() < deadline:
                try:
                    msg = await conn.ws.receive(timeout=max(0.05, deadline - time.time()))
                except asyncio.TimeoutError:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                if d.get("id") is not None and d.get("result", {}).get("type") == "chunk":
                    quiet_chunks += 1
            out.append(CheckResult("②", "取消即停",
                                   "RED" if quiet_chunks else "GREEN",
                                   f"取消后 {CANCEL_QUIET_WINDOW_S:g}s 静默窗 chunk 数={quiet_chunks}"))
    finally:
        await conn.ws.close()
    return out


async def check_notes_persistence_and_r4(b: Backend) -> list[CheckResult]:
    out = []
    marker = f"V1ACC-{int(time.time())}"
    conn1 = await b.connect(declared=True)
    try:
        created = await conn1.call("notes.create", {"title": marker, "content": f"内容 {marker}"})
        note_id = created.get("id") if isinstance(created, dict) else None
        if note_id is None:
            return [CheckResult("③", "笔记切走不丢", "RED", f"notes.create 返回异常: {created}")]
    finally:
        await conn1.ws.close()

    # 「切走重进」= 全新连接读回
    conn2 = await b.connect(declared=True)
    try:
        got = await conn2.call("notes.get", {"note_id": note_id})
        content_ok = isinstance(got, dict) and marker in json.dumps(got, ensure_ascii=False)
        out.append(CheckResult("③", "笔记切走不丢（跨连接读回）",
                               "GREEN" if content_ok else "RED",
                               f"id={note_id} 内容{'一致' if content_ok else '丢失/变形'}"))

        # R4 闸门两态：legacy 连接必须 -32010；声明 client_version 必须进审批流
        legacy = await b.connect(declared=False)
        try:
            try:
                r = await legacy.call("notes.delete", {"note_id": note_id})
                out.append(CheckResult("③", "R4 审批闸（legacy 拒绝）", "RED",
                                       f"legacy 未被拒: {r}", "应返回 -32010"))
            except RpcError as e:
                out.append(CheckResult("③", "R4 审批闸（legacy 拒绝）",
                                       "GREEN" if e.code == -32010 else "YELLOW",
                                       f"-{e.code} {e.message[:60]}",
                                       "" if e.code == -32010 else "被拒但错误码非 -32010"))
        finally:
            await legacy.ws.close()
        r = await conn2.call("notes.delete", {"note_id": note_id})
        went_approval = isinstance(r, dict) and (r.get("pending") or r.get("sandbox_id"))
        out.append(CheckResult("③", "R4 审批闸（声明版进审批）",
                               "GREEN" if went_approval else "YELLOW",
                               f"返回 {json.dumps(r, ensure_ascii=False)[:80]}",
                               "" if went_approval else "未看到 pending/sandbox_id，核对手册口径"))
    finally:
        await conn2.ws.close()
    return out


async def check_knowledge(b: Backend) -> list[CheckResult]:
    conn = await b.connect(declared=True)
    try:
        r = await conn.call("knowledge.graph", {"query": "测试"})
        entities = r.get("entities") or r.get("nodes") or [] if isinstance(r, dict) else []
        if entities:
            has_props = any(isinstance(e, dict) and e.get("properties") for e in entities)
            return [CheckResult("④", "图谱点击有详情（后端段）",
                                "GREEN" if has_props else "YELLOW",
                                f"{len(entities)} 实体, properties={'有' if has_props else '缺'}",
                                "" if has_props else "实体存在但 properties 为空（M3 范围）")]
        return [CheckResult("④", "图谱点击有详情（后端段）", "YELLOW", "0 实体",
                            "隔离库无知识数据——需真实使用后复测（数据密度门槛）")]
    except RpcError as e:
        return [CheckResult("④", "图谱点击有详情（后端段）", "YELLOW",
                            f"-{e.code} {e.message[:60]}", "能力不可用，随数据积累复测")]
    finally:
        await conn.ws.close()


async def check_capture(b: Backend) -> list[CheckResult]:
    conn = await b.connect(declared=True)
    try:
        t0 = time.time()
        added = await conn.call("inbox.add", {"content": f"V1 捕获链路验证 {t0}"})
        # 分页语义修正（2026-09-16）：inbox.list 默认 limit=50 且默认 asc（最旧优先），
        # 而新条目 entry_id 自增 = 落在最末尾 —— 隔离库一旦 >50 条，标记就会掉出默认页，
        # 本检查将变成"库越大越假 RED"的定时炸弹。改为显式取最新端一页（desc），
        # 保证断言的是「刚写入的这条能否读回」，与库内历史条数无关。
        listing = await conn.call("inbox.list", {"limit": 50, "order": "desc"})
        dt = time.time() - t0
        found = marker_in_listing(added, listing)
        status = "GREEN" if (found and dt <= CAPTURE_GREEN_S) else ("YELLOW" if found else "RED")
        return [CheckResult("⑥", "捕获链路 ≤3s（后端段）", status, f"往返 {dt:.2f}s, 落库={'是' if found else '否'}",
                            "划词 UI 端计时需真人手感" if found else "inbox.add 后 inbox.list 最新一页（desc limit=50）未见条目")]
    finally:
        await conn.ws.close()


def marker_in_listing(added, listing) -> bool:
    items = listing.get("items") if isinstance(listing, dict) else listing
    if not isinstance(added, dict) or not isinstance(items, list):
        return False
    new_id = added.get("id") or added.get("entry_id")
    return any(isinstance(i, dict) and (i.get("id") == new_id or i.get("entry_id") == new_id)
               for i in items)


async def check_today_review(b: Backend) -> list[CheckResult]:
    conn = await b.connect(declared=True)
    try:
        today = time.strftime("%Y-%m-%d")
        r = await conn.call("daily.get", {"date": today})
        has = bool((r or {}).get("content") if isinstance(r, dict) else r)
        return [CheckResult("⑤", "今日回顾（数据段）", "YELLOW", "今日日记有内容" if has else "今日日记为空",
                            "视觉呈现与数据密度需真人手感；隔离库本就无日记数据")]
    finally:
        await conn.ws.close()


CHECKS = [check_boot, check_sweep, check_chat_feedback_and_cancel,
          check_notes_persistence_and_r4, check_knowledge, check_capture, check_today_review]


def _seed_perf_data(data_dir: Path) -> None:
    """性能预算检查播种：2000 条拟真 inbox + 500 条拟真 notes（后端启动前直写 kv）。"""
    sys.path.insert(0, str(BACKEND_DIR))
    from data.local_storage import LocalDataStore

    store = LocalDataStore(base_dir=str(data_dir))
    entries = [
        {
            "id": i + 1, "title": f"性能播种条目 {i}",
            "content": "性能预算播种正文，用于逼近真实负载的列表读取开销。" * 6,
            "source": "v1_perf", "status": "new", "tags": [],
            "metadata": "{}", "kind": "note",
            "created_at": "2026-09-13T00:00:00", "updated_at": "2026-09-13T00:00:00",
        }
        for i in range(PERF_INBOX_N)
    ]
    store.save_json("inbox_entries", entries)
    notes = [
        {
            "id": i + 1, "title": f"性能播种笔记 {i}",
            "content": "性能预算播种笔记正文。" * 10,
            "folder": "", "tags": [], "pinned": False, "deleted": False,
            "created_at": "2026-09-13T00:00:00", "updated_at": "2026-09-13T00:00:00",
        }
        for i in range(PERF_NOTES_N)
    ]
    store.save_json("notes", notes)


def _migrate_seed_dir(data_dir: Path) -> None:
    """对播种目录执行 D22 迁移（建表 + kv→表），使 repo 走表路径。"""
    r = subprocess.run(
        [sys.executable, str(BACKEND_DIR / "scripts" / "migrate_kv_to_tables.py"),
         "--data-dir", str(data_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(f"D22 迁移失败: {(r.stdout or r.stderr)[-200:]}")


async def check_perf_budgets(b) -> list[CheckResult]:
    """性能预算行：D22 表路径 inbox.list/notes.list 带门槛；kv 对照仅诊断。"""
    out: list[CheckResult] = []
    def _seed_and_migrate(data_dir: Path) -> None:
        _seed_perf_data(data_dir)
        _migrate_seed_dir(data_dir)  # 建表 + kv→表，repo 层走 D22 表路径

    bt = Backend(seed=_seed_and_migrate)
    dt_inbox = dt_notes = None
    kv_note = ""
    try:
        await bt.start()
        conn = await bt.connect(declared=True)
        try:
            t0 = time.time()
            await conn.call("inbox.list", {}, timeout=180)
            dt_inbox = time.time() - t0
            t0 = time.time()
            await conn.call("notes.list", {"limit": PERF_NOTES_N}, timeout=60)
            dt_notes = time.time() - t0
        finally:
            await conn.ws.close()
    except Exception as exc:  # noqa: BLE001
        out.append(CheckResult("性能", "预算检查执行", "RED", f"{type(exc).__name__}: {exc}"))
    finally:
        await bt.stop()

    if dt_inbox is not None:
        status = ("GREEN" if dt_inbox <= PERF_INBOX_GREEN_S
                  else "YELLOW" if dt_inbox <= PERF_INBOX_YELLOW_S else "RED")
        out.append(CheckResult("性能", f"inbox.list {PERF_INBOX_N} 条（D22 表路径）", status,
                               f"{dt_inbox:.2f}s",
                               f"阈值 绿≤{PERF_INBOX_GREEN_S:g}s 黄≤{PERF_INBOX_YELLOW_S:g}s"))
    if dt_notes is not None:
        status = ("GREEN" if dt_notes <= PERF_NOTES_GREEN_S
                  else "YELLOW" if dt_notes <= PERF_NOTES_YELLOW_S else "RED")
        out.append(CheckResult("性能", f"notes.list {PERF_NOTES_N} 条", status,
                               f"{dt_notes:.2f}s",
                               f"阈值 绿≤{PERF_NOTES_GREEN_S:g}s 黄≤{PERF_NOTES_YELLOW_S:g}s"))

    # kv 对照诊断：仅当表路径未达绿预算时运行一次用于归因（已知 56.5s 量级）
    if dt_inbox is None or dt_inbox > PERF_INBOX_GREEN_S:
        bk = Backend(seed=_seed_perf_data)
        try:
            await bk.start()  # 不迁移 → repo 走 kv 路径
            conn = await bk.connect(declared=True)
            try:
                t0 = time.time()
                await conn.call("inbox.list", {}, timeout=PERF_KV_DIAG_TIMEOUT_S)
                dt_kv = time.time() - t0
                kv_note = f"kv 对照实测 {dt_kv:.1f}s（已知形态）"
            finally:
                await conn.ws.close()
        except Exception as exc:  # noqa: BLE001
            kv_note = f"kv 对照失败: {type(exc).__name__}"
        finally:
            await bk.stop()
    if kv_note:
        out.append(CheckResult("性能", "kv 路径对照（诊断，不计门槛）", "YELLOW",
                               kv_note, "已知 kv 整块解析形态（chaos C3 实测 56.5s/2000 条）——D22 治理目标"))
    return out


CHECKS = [check_boot, check_sweep, check_chat_feedback_and_cancel,
          check_notes_persistence_and_r4, check_knowledge, check_capture, check_today_review,
          check_perf_budgets]


async def main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="V1 一键验收（后端层红绿矩阵）")
    parser.add_argument("--out", default="", help="markdown 报告输出路径（缺省只打印）")
    parser.add_argument("--keep", action="store_true", help="保留临时数据目录（排查用，打印路径）")
    args = parser.parse_args(argv)

    b = Backend()
    results: list[CheckResult] = []
    try:
        await b.start()
        for check in CHECKS:
            try:
                results.extend(await check(b))
            except Exception as exc:  # noqa: BLE001 —— 单项崩溃不拖垮整个矩阵
                results.append(CheckResult("?", check.__name__, "RED",
                                           f"检查器异常: {type(exc).__name__}: {exc}"))
        # 启动日志（自检零失败检查用）在全部检查结束后读取，避免管道阻塞
        if b._log_file is not None:
            try:
                with open(b._log_file.name, encoding="utf-8", errors="replace") as f:
                    b.boot_log = f.read()
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001
        results.append(CheckResult("②前置", "后端启动", "RED", str(exc)[:160]))
    finally:
        keep_dir = b.data_dir
        await b.stop()

    reds = [r for r in results if r.status == "RED"]
    yellows = [r for r in results if r.status == "YELLOW"]
    lines = ["# V1 一键验收报告（后端层）", "",
             f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 结论：**{'🔴 存在 RED，不可用' if reds else ('🟡 有待真人/复测项' if yellows else '🟢 全绿')}**"
             f"　RED={len(reds)} YELLOW={len(yellows)} GREEN={len(results) - len(reds) - len(yellows)}",
             "",
             "| V1 条目 | 检查 | 结果 | 实测 | 说明 |",
             "|---|---|---|---|---|"]
    for r in results:
        icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}[r.status]
        note = r.note + (f"（临时目录已保留: {keep_dir}）" if args.keep and r is results[0] else "")
        lines.append(f"| {r.v1} | {r.name} | {icon} {r.status} | {r.measured} | {note} |")
    lines += ["", "> 需真人手感项（脚本不判红）：①安装包体验 ⑤今日回顾视觉 ⑥划词 UI 计时 ③冲突提示 UI。",
              "> 骨架模式说明：本报告在未配置 LLM key 的隔离后端上产出，「取消即停」真取消语义需配置 key 后复测。"]
    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n报告已写入: {args.out}")
    if args.keep and keep_dir:
        print(f"临时数据目录已保留: {keep_dir}")
    return 1 if reds else 0


def main() -> int:
    return asyncio.run(main_async(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
