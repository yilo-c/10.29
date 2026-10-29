"""RPC **类型边界**模糊普查（2026-09-18）。

## 为什么要有这个

`scripts/rpc_census.py` 只做**空参**调用（params={}），证明的是
"handler 不会因缺参崩"。但 2026-09-18 修 `tasks.schedule`（风险台账 #181）
时发现：``task_id = (task_id or "").strip()`` 对 int 传参必崩
AttributeError → -32603，而**默认空串不触发** → 空参普查完全看不见。

这类"入参类型假设"缺陷的特征是**静默返回 -32603**，调用方只看到内部错误，
不知道是自己传错了类型还是服务端坏了。本脚本系统性打这个面：

* AST 扫出每个注册方法的 handler 签名（参数名 + 注解）；
* 对每个**有注解**的参数，发一次"**跨类型**"值（str 参数传 int、int 参数传 str…）；
* 其余参数按注解填合法占位值，尽量让请求**走到**崩溃点而不是被缺参预检挡掉；
* -32603 / TypeError / AttributeError → **TYPE_CRASH**（本次要抓的目标）。

## 安全

与 rpc_census 同：AUTOSTART **临时后端**（随机端口 + 临时数据目录），
不碰生产数据；R4 破坏性方法进 sandbox 审批队列不会执行。

用法:
    .venv3.11/Scripts/python.exe scripts/rpc_type_fuzz.py [--limit N] [--only str|int]
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "思忆集app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(APP_DIR / "tests"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rpc_census import (  # noqa: E402
    CAT_BROKEN,
    CAT_OK,
    ensure_backend,
)

try:
    from rpc_census import CAT_RATE, CAT_TIMEOUT
except ImportError:  # pragma: no cover
    CAT_RATE, CAT_TIMEOUT = "rate_limited", "timeout"

from rpc_census import WsRpc  # noqa: E402

# 服务端 _ws_msg_limiter 120/60s；留余量（见台账"census 客户端限速伪影"）
RATE_MAX = 100
RATE_WINDOW = 60.0

OUT_JSON_DEFAULT = REPO_ROOT / "deliverables" / "rpc-type-fuzz-2026-09-18.json"
OUT_MD_DEFAULT = REPO_ROOT / "deliverables" / "rpc-type-fuzz-2026-09-18.md"


# ─────────────────────────────────────────────────────────────────────────────
# 1. AST：注册方法 -> handler 签名
# ─────────────────────────────────────────────────────────────────────────────


def _ann_str(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return ""


def scan_signatures() -> dict[str, dict]:
    """方法名 -> {file, lineno, params: [(name, annotation)]}（只收有注解的注册方法）。"""
    funcs: dict[str, dict] = {}
    methods: dict[str, dict] = {}

    for py in (APP_DIR / "backend").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        rel = str(py.relative_to(REPO_ROOT))

        # 本文件内的函数定义（含嵌套：注册点常见 register_xxx(...) 里的闭包）
        local_funcs: dict[str, dict] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                names = [x.arg for x in (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs))]
                anns = [x.annotation for x in (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs))]
                params = [(n, _ann_str(an)) for n, an in zip(names, anns) if n not in ("self", "cls")]
                local_funcs[node.name] = {"file": rel, "lineno": node.lineno, "params": params}
        funcs.update(local_funcs)

        # 注册点：X.register("<method>", <name>)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "register"):
                continue
            if len(node.args) < 2:
                continue
            m, target = node.args[0], node.args[1]
            if not isinstance(m, ast.Constant) or not isinstance(m.value, str):
                continue
            if not isinstance(target, ast.Name):
                continue
            info = local_funcs.get(target.id)
            if info:
                methods[m.value] = dict(info)

    return methods


# ─────────────────────────────────────────────────────────────────────────────
# 2. 造参：跨类型值 + 其余合法占位
# ─────────────────────────────────────────────────────────────────────────────


def _is_str(ann: str) -> bool:
    return "str" in ann and "int" not in ann


def _is_num(ann: str) -> bool:
    return ("int" in ann or "float" in ann) and "str" not in ann


def _is_bool(ann: str) -> bool:
    return "bool" in ann


def _is_container(ann: str) -> bool:
    return any(k in ann for k in ("dict", "list", "Mapping", "Sequence", "Any"))


def placeholder(ann: str):
    if _is_str(ann):
        return "1"
    if _is_num(ann):
        return 1
    if _is_bool(ann):
        return True
    if _is_container(ann):
        return {}
    return None


def fuzz_value(ann: str):
    """跨类型值：str→int / 数值→str / bool→str / 容器→str。无法判定返回 None。"""
    if _is_str(ann):
        return 5
    if _is_num(ann):
        return "not-a-number"
    if _is_bool(ann):
        return "not-a-bool"
    if _is_container(ann):
        return "not-a-dict"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. 主流程
# ─────────────────────────────────────────────────────────────────────────────


class Pacer:
    """滚动限速，避免撞服务端 _ws_msg_limiter（120/60s）。"""

    def __init__(self) -> None:
        self.hits: deque[float] = deque()

    async def wait(self) -> None:
        now = time.monotonic()
        while self.hits and now - self.hits[0] > RATE_WINDOW:
            self.hits.popleft()
        if len(self.hits) >= RATE_MAX:
            sleep_for = RATE_WINDOW - (now - self.hits[0]) + 1
            await asyncio.sleep(max(sleep_for, 1))
        self.hits.append(time.monotonic())


def is_type_crash(res: dict) -> bool:
    """-32603 且异常名属于"类型假设"家族（或 detail 直接点名类型错误）。"""
    if res.get("category") != CAT_BROKEN:
        return False
    blob = f"{res.get('detail', '')}".lower()
    markers = (
        "attributeerror",
        "typeerror",
        "valueerror",
        "no attribute",
        "not subscriptable",
        "unsupported operand",
        "must be str",
        "expected str",
    )
    return any(m in blob for m in markers)


async def run(args: argparse.Namespace) -> int:
    t0 = time.monotonic()
    ws_url, headers = ensure_backend()
    caller = WsRpc(ws_url, headers)
    pacer = Pacer()
    sigs = scan_signatures()
    print(f"[scan] 有注解签名的注册方法 = {len(sigs)}", flush=True)

    try:
        res, raw = await caller.call("system.status", timeout=30)
        if res["category"] != CAT_OK or not isinstance(raw, dict):
            print(f"[FATAL] system.status 失败: {res['detail']}", flush=True)
            return 2
        methods: list[str] = list(raw.get("methods") or [])
    finally:
        pass

    targets = [m for m in methods if m in sigs]
    if args.limit:
        targets = targets[: args.limit]
    print(f"[plan] 注册 {len(methods)} / 可模糊 {len(targets)}", flush=True)

    rows: list[dict] = []
    crashes: list[dict] = []
    n_calls = 0

    for i, m in enumerate(targets, 1):
        info = sigs[m]
        params = info["params"]
        for pname, ann in params:
            if args.only == "str" and not _is_str(ann):
                continue
            if args.only == "int" and not _is_num(ann):
                continue
            fv = fuzz_value(ann)
            if fv is None:
                continue
            payload: dict = {}
            for qname, qann in params:
                if qname == pname:
                    payload[qname] = fv
                else:
                    ph = placeholder(qann)
                    if ph is not None:
                        payload[qname] = ph
            await pacer.wait()
            try:
                res, _ = await caller.call(m, payload, timeout=args.timeout)
            except Exception as exc:  # noqa: BLE001
                res = {"category": CAT_BROKEN, "detail": f"client-exception {type(exc).__name__}: {exc}"}
            n_calls += 1
            if res.get("category") == CAT_RATE:
                await asyncio.sleep(61)
                await pacer.wait()
                try:
                    res, _ = await caller.call(m, payload, timeout=args.timeout)
                except Exception as exc:  # noqa: BLE001
                    res = {"category": CAT_BROKEN, "detail": f"client-exception {type(exc).__name__}: {exc}"}
            row = {
                "method": m,
                "param": pname,
                "annotation": ann,
                "sent": repr(fv),
                "category": res.get("category", ""),
                "detail": (res.get("detail") or "")[:300],
                "file": info["file"],
                "lineno": info["lineno"],
            }
            rows.append(row)
            if is_type_crash(res):
                crashes.append(row)
                print(f"  [TYPE_CRASH] {m} {pname}:{ann} <- {fv!r}", flush=True)
        if i % 20 == 0:
            print(f"[{i}/{len(targets)}] calls={n_calls} crashes={len(crashes)}", flush=True)

    elapsed = time.monotonic() - t0
    try:
        await caller.close()
    except Exception:  # noqa: BLE001
        pass

    out_json = Path(args.out_json) if args.out_json else OUT_JSON_DEFAULT
    out_md = Path(args.out_md) if args.out_md else OUT_MD_DEFAULT
    out_json.write_text(
        json.dumps(
            {
                "meta": {
                    "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "ws_url": ws_url,
                    "methods_registered": len(methods),
                    "methods_fuzzed": len(targets),
                    "calls": n_calls,
                    "elapsed_s": round(elapsed, 1),
                },
                "crashes": crashes,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    write_md(out_md, methods, targets, n_calls, crashes, rows, elapsed, out_json)
    print(f"\ncalls={n_calls} crashes={len(crashes)} elapsed={elapsed:.0f}s", flush=True)
    print(f"json={out_json}\nmd={out_md}", flush=True)
    return 0 if not crashes else 1


def write_md(
    out_md: Path,
    methods: list[str],
    targets: list[str],
    n_calls: int,
    crashes: list[dict],
    rows: list[dict],
    elapsed: float,
    out_json: Path,
) -> None:
    lines: list[str] = []
    lines.append("# RPC 类型边界模糊普查（2026-09-18）\n")
    lines.append(f"- 注册方法 **{len(methods)}**，有注解签名可模糊 **{len(targets)}**，实际调用 **{n_calls}** 次")
    lines.append(f"- 耗时 {elapsed:.0f}s；后端 = AUTOSTART 临时实例（不碰生产）")
    lines.append(f"- 原始明细：`{out_json.name}`\n")

    lines.append("## 结论\n")
    if not crashes:
        lines.append("**未发现**类型假设崩溃（-32603 + TypeError/AttributeError 家族）。\n")
    else:
        lines.append(f"**发现 {len(crashes)} 处**类型假设崩溃：\n")
        lines.append("| 方法 | 参数 | 注解 | 发送值 | 位置 | 错误摘要 |")
        lines.append("|---|---|---|---|---|---|")
        for c in crashes:
            det = c["detail"].replace("|", "\\|")[:120]
            lines.append(f"| `{c['method']}` | `{c['param']}` | `{c['annotation']}` | `{c['sent']}` | `{c['file']}:{c['lineno']}` | {det} |")
        lines.append("")

    # 分类分布（供对照）
    dist: dict[str, int] = {}
    for r in rows:
        dist[r["category"]] = dist.get(r["category"], 0) + 1
    lines.append("## 响应分类分布\n")
    lines.append("| 分类 | 次数 |")
    lines.append("|---|---|")
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k or '(空)'} | {v} |")
    lines.append("")
    lines.append(
        "> 口径：本脚本只打**类型边界**（str 参数传 int 等），不验证业务逻辑正确性；\n"
        "> `not_applicable` 多为 R4 方法进审批队列；`param_error` 是被缺参/校验预检挡下\n"
        "> （意味着该参数没走到崩溃点，**不代表安全**）。"
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="RPC 类型边界模糊普查（补空参普查的盲区）")
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 个可模糊方法（调试）")
    ap.add_argument("--only", choices=("str", "int"), default=None, help="只模糊 str 或数值型参数")
    ap.add_argument("--timeout", type=float, default=20.0, help="单次调用超时（秒）")
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-md", default=None)
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
