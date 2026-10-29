"""L1 RPC 全方法普查（T3, 2026-08-30）。

对后端全部注册 JSON-RPC 方法逐一 benign 调用（params={}）并分类：
    ok / param_error / broken / timeout / not_applicable（需审批/需 key 拒答，不计 broken）。

口径与实现:
    - 后端为 AUTOSTART 临时实例（随机端口 + 临时数据目录 + SIYI_TEST_OFFLINE=1），
      复用 思忆集app/tests/conftest.py 的 _maybe_autostart_backend() / _auth_headers()。
    - WS 错误帧对通用异常只回 "Internal error"；本脚本经 PYTHONPATH sitecustomize
      把临时后端进程的 WebSocketServer debug 强制为 True，使 -32603 附带
      exception 类型 + detail 首行，供分类启发式与 broken/timeout 清单输出。
    - 方法真源: 调 system.status 取 registry.list_methods()（运行时真值，非静态扫描）。
    - docstring 首行来自 backend/**/*.py 注册点静态扫描（动态注册的方法留空）。
    - 全串行 + 滚动限速 <=180 req/60s（服务端 RateLimiter 200/60s）；单方法 15s 超时。
    - 流式方法: 非流式调用报 send_chunk 缺参时自动以 stream:true 重试一次。

分类映射（服务端错误码 -> 桶）:
    -32602 / -32001 / -32000 / -32002            -> param_error（结构化参数/业务校验=健康）
    -32006 / 客户端 15s 超时                      -> timeout
    -32003                                        -> rate_limited（休眠 61s 重试一次）
    R4 result(pending/requires_approval)          -> not_applicable
    -32004 / -32005 / -3210x + NA 关键词启发式     -> not_applicable（需审批/需 key/预算/权限）
    其余 -32603 等                                -> broken（结构性异常名直接判 broken）

输出:
    - deliverables/rpc-census-2026-08-30.md（分域x分类矩阵 + 全量明细 + broken/timeout 清单）
    - %TEMP%/siyi-rpc-census-raw-<ts>.json

用法:
    .venv3.11/Scripts/python.exe scripts/rpc_census.py [--limit N] [--list-only] [--no-md]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "思忆集app"
BASELINE_FILE = REPO_ROOT / "scripts" / "undocumented_methods_baseline.txt"
DEFAULT_MD = REPO_ROOT / "deliverables" / "rpc-census-2026-08-30.md"

CAT_OK = "ok"
CAT_PARAM = "param_error"
CAT_BROKEN = "broken"
CAT_TIMEOUT = "timeout"
CAT_NA = "not_applicable"
CAT_RATE = "rate_limited"
CATS = [CAT_OK, CAT_PARAM, CAT_BROKEN, CAT_TIMEOUT, CAT_NA]

RATE_WINDOW = 60.0
RATE_MAX = 180  # 服务端 RateLimiter 200/60s，留 10% 余量
PER_CALL_TIMEOUT = 15.0

# 结构化参数/业务校验错误码 = 健康（handler 优雅拒绝；code 记录进明细可复核）
PARAM_CODES = {-32602, -32001, -32000, -32002}
# 结构化拒答（未授权/禁止/AI 服务错误/预算耗尽）= 需 key 或权限，不算 broken
NA_CODES = {-32004, -32005, -32100, -32101, -32102}

# 需审批/需 key/外部配置缺失类拒答 -> not_applicable 的关键词（小写匹配 message+detail）
NA_PATTERNS = (
    "api key",
    "api_key",
    "apikey",
    "not configured",
    "未配置",
    "请先配置",
    "密钥",
    "requires key",
    "missing key",
    "token not",
    "transfer token",
    "ai 不可用",
    "ai不可用",
    "ai unavailable",
    "ai_client",
    "ai client",
    "llm 不可用",
    "llm不可用",
    "llm unavailable",
    "not enabled",
    "未启用",
    "审批",
    "approval",
    "需要用户批准",
    "sandbox",
    "unauthorized",
    "forbidden",
    "ollama",
)
# 参数缺失/非法类关键词 -> param_error（仅在未命中 NA 时）
PARAM_KEYWORDS = ("required", "必填", "缺少", "missing", "must be", "不能为空")
# 结构性异常一律 broken（哪怕消息里含 key/配置字样）
BROKEN_EXCEPTIONS = {
    "KeyError",
    "IndexError",
    "AttributeError",
    "TypeError",
    "NameError",
    "ZeroDivisionError",
    "ImportError",
    "ModuleNotFoundError",
    "FileNotFoundError",
    "NotADirectoryError",
    "IsADirectoryError",
    "PermissionError",
    "JSONDecodeError",
    "UnboundLocalError",
    "RecursionError",
    "NotImplementedError",
    "StopIteration",
    "OSError",
}
# 结构化参数校验异常（registry jsonschema 校验层 / handler 参数校验抛出）
PARAM_EXCEPTIONS = {"InvalidParamsError", "JsonSchemaValidationError", "ValidationError"}

REG_RE = re.compile(r"\bregister(?:_with_schema)?\(\s*[\"']([a-zA-Z][a-zA-Z0-9_.]+)[\"']\s*,\s*([A-Za-z_][A-Za-z0-9_]*)")
DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# sitecustomize：仅注入 AUTOSTART 临时后端子进程（PYTHONPATH 前置），强制 WS debug=True
SITECUSTOMIZE = """
import os as _os
import sys as _sys

try:
    _app = _os.environ.get("SIYI_CENSUS_APP_DIR", "")
    if _app and _app not in _sys.path:
        _sys.path.insert(0, _app)
    import backend.ws_server as _ws

    _cls = getattr(_ws, "WebSocketServer", None)
    if _cls is not None and not getattr(_cls, "_siyi_census_debug", False):
        _orig = _cls.__init__

        def _patched(self, *a, **kw):
            kw["debug"] = True
            return _orig(self, *a, **kw)

        _cls.__init__ = _patched
        _cls._siyi_census_debug = True
except Exception:
    pass
"""

_CT = None  # conftest 模块引用（用于显式回收临时后端进程）


def cut(s: str, n: int) -> str:
    """表格安全截断：去换行、转竖线、截断。"""
    return str(s or "").replace("|", "/").replace("\r", " ").replace("\n", " ")[:n].strip()


def domain_of(method: str) -> str:
    return method.split(".")[0] if "." in method else method


def load_baseline() -> set[str]:
    if not BASELINE_FILE.exists():
        return set()
    return {
        ln.strip()
        for ln in BASELINE_FILE.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }


def scan_docs() -> dict[str, dict[str, str]]:
    """静态扫描 backend/**/*.py 注册点：方法名 -> {file, doc}（docstring 首行，尽力而为）。"""
    docs: dict[str, dict[str, str]] = {}
    for py in (APP_DIR / "backend").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            lines = py.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        fn_doc: dict[str, str] = {}
        for idx, line in enumerate(lines):
            dm = DEF_RE.match(line)
            if not dm:
                continue
            blob = "\n".join(lines[idx + 1 : idx + 10])
            q = blob.find('"""')
            if q < 0:
                q = blob.find("'''")
                if q < 0:
                    continue
            quote = blob[q : q + 3]
            rest = blob[q + 3 :]
            end = rest.find(quote)
            seg = rest if end < 0 else rest[:end]
            first = seg.strip().splitlines()
            if first:
                fn_doc[dm.group(1)] = cut(first[0], 80)
        text = "\n".join(lines)
        for m in REG_RE.finditer(text):
            docs[m.group(1)] = {
                "file": str(py.relative_to(REPO_ROOT)),
                "doc": fn_doc.get(m.group(2), ""),
            }
    return docs


def classify_result(result: object) -> tuple[str, str]:
    """正常返回帧 -> (category, detail)。R4 审批队列拒答归 not_applicable。"""
    if isinstance(result, dict) and (result.get("pending") or result.get("requires_approval")):
        msg = result.get("message") or result.get("preview") or "R4 审批队列"
        return CAT_NA, cut(str(msg), 120)
    return CAT_OK, ""


def classify_error(err: dict) -> tuple[str, str]:
    """错误帧 -> (category, detail)。启发式顺序：码 > 异常名 > 关键词。"""
    code = err.get("code")
    message = str(err.get("message") or "")
    data = err.get("data") if isinstance(err.get("data"), dict) else {}
    exc = str(data.get("exception") or "")
    det = str(data.get("detail") or "")
    detail = " | ".join(x for x in (message, exc, det) if x)
    if code == -32003:
        return CAT_RATE, detail
    if code in PARAM_CODES or exc in PARAM_EXCEPTIONS:
        return CAT_PARAM, f"({code}) {detail}".strip()
    if code == -32006 or exc == "RPCTimeoutError":
        return CAT_TIMEOUT, f"({code}) {detail}".strip()
    if code == -32601:
        return CAT_BROKEN, f"(-32601) {detail}".strip()
    if code in NA_CODES:
        return CAT_NA, f"({code}) {detail}".strip()
    if exc in BROKEN_EXCEPTIONS:
        return CAT_BROKEN, detail or f"({code})"
    blob = f"{message} {exc} {det}".lower()
    hit = next((p for p in NA_PATTERNS if p in blob), None)
    na_by_name = "AI" in exc or "Budget" in exc or exc in {"UnauthorizedError", "ForbiddenError"}
    if na_by_name or hit:
        tag = exc if exc else f"kw:{hit}"
        return CAT_NA, f"[{tag}] ({code}) {detail}".strip()
    if exc == "ValueError" or any(k in blob for k in PARAM_KEYWORDS):
        return CAT_PARAM, f"({code}) {detail}".strip()
    return CAT_BROKEN, detail or f"({code}) no-debug Internal error"


class WsRpc:
    """单连接串行 WS JSON-RPC 客户端（限速 + 事件帧跳过 + 掉线重连 + 超时复位）。"""

    def __init__(self, ws_url: str, headers: dict[str, str]) -> None:
        self.ws_url = ws_url
        self.headers = headers
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 1
        self._send_times: deque[float] = deque()

    async def _ensure(self) -> None:
        if self._ws is not None and not self._ws.closed:
            return
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        timeout = aiohttp.ClientWSTimeout(ws_receive=PER_CALL_TIMEOUT + 10, ws_close=10)
        self._ws = await self._session.ws_connect(self.ws_url, timeout=timeout, headers=self.headers)

    async def _reset(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._ws = None

    async def close(self) -> None:
        await self._reset()
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _pace(self) -> None:
        now = time.monotonic()
        while self._send_times and now - self._send_times[0] >= RATE_WINDOW:
            self._send_times.popleft()
        if len(self._send_times) >= RATE_MAX:
            wait = RATE_WINDOW - (now - self._send_times[0]) + 0.5
            if wait > 0:
                await asyncio.sleep(wait)
        self._send_times.append(time.monotonic())

    @staticmethod
    def _res(cat: str, code: object, detail: str, t0: float) -> dict:
        return {
            "category": cat,
            "code": code,
            "detail": detail,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }

    async def call(
        self,
        method: str,
        params: dict | None = None,
        *,
        stream: bool = False,
        timeout: float = PER_CALL_TIMEOUT,
    ) -> tuple[dict, object]:
        """发起一次 JSON-RPC 调用，返回 (分类结果 dict, 原始 result)。"""
        mid = self._next_id
        self._next_id += 1
        payload: dict = {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}
        if stream:
            payload["stream"] = True
        await self._pace()
        t0 = time.monotonic()
        try:
            try:
                await self._ensure()
                await self._ws.send_str(json.dumps(payload, ensure_ascii=False))
            except Exception:  # noqa: BLE001 连接可能已失效：重连一次重发
                await self._reset()
                await self._ensure()
                await self._ws.send_str(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            await self._reset()
            return self._res(CAT_BROKEN, "ws_transport", cut(f"WS 连接/发送失败: {exc}", 200), t0), None

        chunks = 0
        try:
            async with asyncio.timeout(timeout):
                while True:
                    msg = await self._ws.receive()
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            raise ConnectionError(f"ws closed: {msg.type}")
                        continue
                    data = json.loads(msg.data)
                    if not isinstance(data, dict) or data.get("id") != mid:
                        continue  # 事件推送 / 迟到的旧响应
                    err = data.get("error")
                    if err:
                        err_d = err if isinstance(err, dict) else {"message": str(err)}
                        cat, det = classify_error(err_d)
                        return self._res(cat, err_d.get("code", ""), det, t0), None
                    result = data.get("result")
                    if isinstance(result, dict) and result.get("type") in ("chunk", "done"):
                        chunks += 1
                        if result.get("type") == "done":
                            return self._res(CAT_OK, "stream", f"{chunks} chunks", t0), result
                        continue
                    cat, det = classify_result(result)
                    return self._res(cat, "", det, t0), result
        except (TimeoutError, asyncio.TimeoutError):
            await self._reset()
            return self._res(CAT_TIMEOUT, "client_timeout", f"客户端 {timeout}s 超时", t0), None
        except (ConnectionError, aiohttp.ClientError) as exc:
            await self._reset()
            return self._res(CAT_BROKEN, "ws_transport", cut(f"WS 传输失败: {exc}", 200), t0), None
        except json.JSONDecodeError as exc:
            return self._res(CAT_BROKEN, "bad_json", cut(f"非 JSON 帧: {exc}", 200), t0), None
        except Exception as exc:  # noqa: BLE001 兜底：不让单帧异常中断普查
            return self._res(CAT_BROKEN, "client_error", cut(f"{type(exc).__name__}: {exc}", 200), t0), None


def _install_debug_patch() -> None:
    """经 PYTHONPATH sitecustomize 给 AUTOSTART 子进程注入 WS debug=True。"""
    hook_dir = Path(tempfile.mkdtemp(prefix="siyi-census-hook-"))
    (hook_dir / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    os.environ["SIYI_CENSUS_APP_DIR"] = str(APP_DIR)
    pp = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(hook_dir) + (os.pathsep + pp if pp else "")


def ensure_backend() -> tuple[str, dict[str, str]]:
    """置 AUTOSTART 并复用 conftest 幂等自启临时后端；返回 (ws_url, ws_headers)。"""
    global _CT
    os.environ["SIYI_INTEGRATION_AUTOSTART"] = "1"
    os.environ["SIYI_DEBUG"] = "1"  # 子进程日志级别；WS debug 由 sitecustomize 注入
    _install_debug_patch()
    # 2026-09-18：父进程也要能 import 后端包。原实现只插了 tests 目录
    # （APP_DIR 仅经 sitecustomize 注入到**子**进程），于是 `import conftest`
    # 一触即炸 `ModuleNotFoundError: No module named 'core.isolation'`。
    for p in (str(APP_DIR), str(APP_DIR / "tests")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import conftest as ct

    _CT = ct
    if not ct._maybe_autostart_backend():
        raise RuntimeError("AUTOSTART 临时后端启动失败（conftest._maybe_autostart_backend()==False）")
    ws_url: str = ct.SIYI_WS_URL
    if any(f":{p}/" in ws_url for p in ("18765", "18902", "11434")):
        raise RuntimeError(f"安全检查失败：临时后端 URL 指向受保护端口 {ws_url}")
    headers = ct._auth_headers()
    if not headers.get("Authorization", "").removeprefix("Bearer ").strip():
        raise RuntimeError("未取得 WS 认证 token（SIYI_DIAG_TOKEN 为空）")
    proc = ct._AUTOSTART_STATE.get("proc")
    print(f"[backend] pid={getattr(proc, 'pid', '?')} url={ws_url} debug_hook=installed", flush=True)
    return ws_url, headers


def write_raw_json(rows: list[dict], methods: list[str], ws_url: str, elapsed: float, partial: bool) -> Path:
    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo": str(REPO_ROOT),
            "ws_url": ws_url,
            "mode": "autostart temp backend (random port + temp data dir) + SIYI_TEST_OFFLINE=1 + ws debug via sitecustomize",
            "transport": "WS JSON-RPC params={} serial, per-call 15s client timeout, pace<=180/60s",
            "methods_total": len(methods),
            "partial": partial,
            "elapsed_s": round(elapsed, 1),
            "counts": {c: sum(1 for r in rows if r["category"] == c) for c in CATS},
        },
        "rows": rows,
    }
    p = Path(tempfile.gettempdir()) / f"siyi-rpc-census-raw-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def write_md(path: Path, rows: list[dict], methods: list[str], elapsed: float, raw_path: Path) -> Path:
    counts = {c: sum(1 for r in rows if r["category"] == c) for c in CATS}
    domains: dict[str, dict[str, int]] = {}
    for r in rows:
        d = domains.setdefault(r["domain"], {})
        d[r["category"]] = d.get(r["category"], 0) + 1
    dom_order = sorted(
        domains.items(),
        key=lambda kv: (-(kv[1].get(CAT_BROKEN, 0) + kv[1].get(CAT_TIMEOUT, 0)), -sum(kv[1].values()), kv[0]),
    )
    bad = sorted(
        (r for r in rows if r["category"] in (CAT_BROKEN, CAT_TIMEOUT)),
        key=lambda r: (r["category"], r["method"]),
    )
    undoc_bad = [r for r in bad if r["undocumented"]]
    n_doc = sum(1 for r in rows if r["doc"])
    param_codes: dict[str, int] = {}
    for r in rows:
        if r["category"] == CAT_PARAM:
            k = str(r["code"])
            param_codes[k] = param_codes.get(k, 0) + 1

    L: list[str] = []
    L.append("# L1 RPC 全方法普查（2026-08-30，T3）")
    L.append("")
    L.append(f"- 方法真源：`system.status -> registry.list_methods()`，实测 **{len(methods)}** 个")
    L.append(
        "- 后端：AUTOSTART 临时实例（随机端口 + 临时数据目录，SIYI_TEST_OFFLINE=1）；"
        "WS debug=True 经 PYTHONPATH sitecustomize 注入临时进程（仓库代码零改动），-32603 携带异常类型/详情"
    )
    L.append(
        "- 调用方式：WS JSON-RPC，params={}，全串行，单方法 15s 客户端超时，滚动限速 <=180 req/60s；"
        "流式方法报 send_chunk 缺参时自动 stream:true 重试一次"
    )
    L.append(
        "- 分类口径：ok=正常返回；param_error=结构化参数/业务校验拒绝（-32602/-32001/-32000/-32002，健康）；"
        "broken=未捕获异常（-32603 等）；timeout=15s 超时；"
        "not_applicable=R4 审批队列拒答 / 需 key·权限·AI 未配置类拒答（-32004/-32005/-3210x + 关键词启发式，命中项写入明细）"
    )
    L.append(f"- docstring 静态可提取：{n_doc}/{len(rows)}（动态注册方法留空）；原始 JSON：`{raw_path}`；总耗时 {elapsed:.0f}s")
    L.append("")
    L.append("## 总体计数")
    L.append("")
    L.append("| 分类 | 数量 |")
    L.append("|---|---|")
    for c in CATS:
        L.append(f"| {c} | {counts.get(c, 0)} |")
    if counts.get(CAT_RATE):
        L.append(f"| rate_limited（限速重试后仍限流，异常信号） | {counts[CAT_RATE]} |")
    L.append(f"| 合计 | {len(rows)} |")
    L.append("")
    L.append("param_error 细分（按错误码）：" + ("，".join(f"{k}x{v}" for k, v in sorted(param_codes.items())) or "—"))
    L.append("")
    L.append("## 分域 x 分类矩阵")
    L.append("")
    L.append("| 域 | ok | param_error | broken | timeout | not_applicable | 合计 |")
    L.append("|---|---|---|---|---|---|---|")
    for name, d in dom_order:
        L.append(
            f"| {name} | {d.get(CAT_OK, 0)} | {d.get(CAT_PARAM, 0)} | {d.get(CAT_BROKEN, 0)} "
            f"| {d.get(CAT_TIMEOUT, 0)} | {d.get(CAT_NA, 0)} | {sum(d.values())} |"
        )
    L.append("")
    L.append(f"## broken / timeout 清单（{len(bad)} 项，含错误首行）")
    L.append("")
    L.append("| 方法 | 分类 | 错误首行 | 未文档化基线 |")
    L.append("|---|---|---|---|")
    for r in bad:
        L.append(f"| `{r['method']}` | {r['category']} | {cut(r['detail'], 110)} | {'是' if r['undocumented'] else ''} |")
    if not bad:
        L.append("| — | — | 无 | — |")
    L.append("")
    L.append(f"broken/timeout 与未文档化基线交集：{len(undoc_bad)} 项")
    if undoc_bad:
        L.append("")
        L.append("、".join(f"`{r['method']}`" for r in undoc_bad))
    L.append("")
    L.append(f"## 全量明细（{len(rows)} 行）")
    L.append("")
    L.append("| # | 方法 | 域 | docstring 首行 | 分类 | 说明/错误首行 | 未文档化基线 |")
    L.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        L.append(
            f"| {i} | `{r['method']}` | {r['domain']} | {cut(r['doc'], 40)} | {r['category']} "
            f"| {cut(r['detail'], 90)} | {'是' if r['undocumented'] else ''} |"
        )
    L.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path


def print_summary(
    rows: list[dict],
    counts: dict[str, int],
    methods: list[str],
    elapsed: float,
    rate_waits: int,
    raw_path: Path,
    md_path: Path | None,
) -> None:
    print("=" * 64, flush=True)
    print(
        f"CENSUS SUMMARY methods_total={len(methods)} rows={len(rows)} elapsed={elapsed:.0f}s rate_waits={rate_waits}",
        flush=True,
    )
    for c in CATS:
        print(f"  {c}={counts.get(c, 0)}", flush=True)
    if counts.get(CAT_RATE):
        print(f"  rate_limited(after-retry)={counts[CAT_RATE]}", flush=True)
    no_debug = sum(1 for r in rows if "no-debug" in r["detail"])
    dbg = "ok" if no_debug == 0 else f"MISSING({no_debug})"
    print(f"  ws_debug_data={dbg}", flush=True)
    broken = [r["method"] for r in rows if r["category"] == CAT_BROKEN]
    tos = [r["method"] for r in rows if r["category"] == CAT_TIMEOUT]
    undoc_bad = [r["method"] for r in rows if r["category"] in (CAT_BROKEN, CAT_TIMEOUT) and r["undocumented"]]
    print(f"  broken∩undocumented_baseline={len(undoc_bad)}", flush=True)
    head_b = ", ".join(broken[:40]) + (f" …(+{len(broken) - 40})" if len(broken) > 40 else "")
    print(f"broken({len(broken)}): {head_b}", flush=True)
    head_t = ", ".join(tos[:20]) + (f" …(+{len(tos) - 20})" if len(tos) > 20 else "")
    print(f"timeout({len(tos)}): {head_t}", flush=True)
    doms: dict[str, dict[str, int]] = {}
    for r in rows:
        d = doms.setdefault(r["domain"], {})
        d[r["category"]] = d.get(r["category"], 0) + 1
    top = sorted(doms.items(), key=lambda kv: -(kv[1].get(CAT_BROKEN, 0) + kv[1].get(CAT_TIMEOUT, 0)))[:12]
    print("top domains by broken+timeout:", flush=True)
    for name, d in top:
        print(f"  {name}: " + " ".join(f"{c}={d.get(c, 0)}" for c in CATS), flush=True)
    print(f"raw_json={raw_path}", flush=True)
    print(f"markdown={md_path}", flush=True)


async def run_census(args: argparse.Namespace) -> int:
    t_start = time.monotonic()
    ws_url, headers = ensure_backend()
    caller = WsRpc(ws_url, headers)
    rows: list[dict] = []
    try:
        res, _ = await caller.call("ping", timeout=10)
        if res["category"] != CAT_OK:
            print(f"[FATAL] ping 探活失败: {res['detail']}", flush=True)
            return 2
        res, raw = await caller.call("system.status", timeout=30)
        if res["category"] != CAT_OK or not isinstance(raw, dict):
            print(f"[FATAL] system.status 失败: {res['detail']}", flush=True)
            return 2
        methods: list[str] = list(raw.get("methods") or [])
        if not methods:
            print("[FATAL] system.status 未返回 methods 列表", flush=True)
            return 2
        docs = scan_docs()
        baseline = load_baseline()
        partial = bool(args.limit)
        if partial:
            methods = methods[: args.limit]
        print(f"[enum] methods={len(methods)} docs_static={len(docs)} baseline={len(baseline)}", flush=True)
        if args.list_only:
            doms: dict[str, int] = {}
            for m in methods:
                doms[domain_of(m)] = doms.get(domain_of(m), 0) + 1
            covered = sum(1 for m in methods if docs.get(m, {}).get("doc"))
            print("domains=" + json.dumps(doms, ensure_ascii=False), flush=True)
            print(f"docstring_static_coverage={covered}/{len(methods)}", flush=True)
            return 0
        counts: dict[str, int] = {}
        rate_waits = 0
        for i, m in enumerate(methods, 1):
            res, _ = await caller.call(m, {})
            if res["category"] == CAT_RATE:
                rate_waits += 1
                await asyncio.sleep(61)
                res, _ = await caller.call(m, {})
            if res["category"] == CAT_BROKEN and "send_chunk" in res.get("detail", ""):
                res, _ = await caller.call(m, {}, stream=True)
                res["detail"] = f"[stream-retry->{res['category']}] {res['detail']}"
            counts[res["category"]] = counts.get(res["category"], 0) + 1
            rows.append(
                {
                    "method": m,
                    "domain": domain_of(m),
                    "category": res["category"],
                    "code": res.get("code", ""),
                    "detail": res.get("detail", ""),
                    "doc": docs.get(m, {}).get("doc", ""),
                    "file": docs.get(m, {}).get("file", ""),
                    "undocumented": m in baseline,
                    "elapsed_ms": res.get("elapsed_ms", 0),
                }
            )
            if i % 20 == 0 or i == len(methods):
                print(
                    f"[{i}/{len(methods)}] " + " ".join(f"{c}={counts.get(c, 0)}" for c in CATS),
                    flush=True,
                )
        elapsed = time.monotonic() - t_start
        raw_path = write_raw_json(rows, methods, ws_url, elapsed, partial)
        md_path: Path | None = None
        if not args.no_md and not partial:
            # 2026-09-18：默认路径是 2026-08-30 的**历史基线**，重跑会覆盖。
            # 加 --out-md 让每轮普查各写各的报告，基线得以保留做对照。
            out_md = Path(args.out_md) if getattr(args, "out_md", None) else DEFAULT_MD
            md_path = write_md(out_md, rows, methods, elapsed, raw_path)
        print_summary(rows, counts, methods, elapsed, rate_waits, raw_path, md_path)
        return 0
    finally:
        await caller.close()
        if _CT is not None:
            try:
                _CT._shutdown_autostart()
            except Exception:  # noqa: BLE001
                pass
        print("[cleanup] temp backend shutdown requested", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="L1 RPC 全方法普查（benign 调用四分类）")
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 个方法（调试用，输出标记 partial）")
    ap.add_argument("--list-only", action="store_true", help="只枚举方法清单不逐调")
    ap.add_argument("--no-md", action="store_true", help="不写 deliverables MD")
    ap.add_argument(
        "--out-md",
        default=None,
        help="自定义 MD 报告路径（默认写 2026-08-30 基线；重跑普查时务必另指定，别覆盖历史基线）",
    )
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    return asyncio.run(run_census(args))


if __name__ == "__main__":
    raise SystemExit(main())
