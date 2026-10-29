#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C-4 机器 diff 基线（只读）—— RPC / 事件 / 裸调口径 / info_items 体积 四组真数。

设计约束（务必保持）：
  * 只读：不 import 任何重型依赖（仅标准库 os/re/ast/json/sqlite3/datetime）；
    不 import 项目代码；不启动后端；不写仓库任何既有文件。
  * 唯一输出：scripts/baseline/c4_baseline.json（本脚本自己创建目录）。
  * 数据库一律 sqlite3 URI `mode=ro` 打开，绝不产生 -wal/-shm 写副作用。
  * 每个采集量都做「正则腿 + 结构腿」双采并集，两腿各自独占命中如实登记，
    便于人工复核口径，不假装单腿是真值。

四组产出：
  ① rpc     —— 后端 registry.register* 注册集 ↔ 前端调用集，孤儿/幻觉双向清单
  ② events  —— 后端 emit/broadcast ↔ 前端订阅，零订阅清单（重点）+ 零发射清单
  ③ bare_rpc—— 裸调 RPC 口径定谳（85 文件 vs 64 文件/195 处 之争）
  ④ info_items_size —— local_storage 主库 kv_store.info_items 体积定谳（15.62 vs 16.4）

用法：
    python scripts/c4_diff.py [--out scripts/baseline/c4_baseline.json] [--quiet]
退出码：0=成功；2=采集异常（关键集合为空 / 找不到主库等，宁可报错不产出假数）。
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "思忆集app"
BACKEND_DIR = APP_DIR / "backend"
PACKAGES_DIR = REPO_ROOT / "desk-notes" / "packages"
MOBILE_DIR = REPO_ROOT / "思忆集手机端app" / "src"
OUT_DEFAULT = REPO_ROOT / "scripts" / "baseline" / "c4_baseline.json"

SKIP_DIRS = {
    "node_modules", "dist", "build", ".next", "coverage", "web_dist",
    "assets", "generated-images", "__pycache__", "archive", "e2e-report",
    "font-awesome", ".venv", ".git", ".pytest_cache", ".mypy_cache", "chroma",
}
TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs", ".vue")

# 封装层（拿单例的唯一合法位置）——不计入「裸调」
WRAPPER_FILES = {
    "shared/src/sync/wsClient.ts",
    "shared/src/ws-client/ws-client.ts",
    "shared/src/utils/ai.ts",
    "shared/src/utils/syncCall.ts",
    "shared/src/sync/persistence.ts",
    "shared/src/store/backendEventBus.ts",
}
# 二次封装面（service/repository 风格文件，审计单独口径）
LAYER2_RE = re.compile(r"/services?/|Service\.ts$|graphService\.ts$")

RPC_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
EVT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")

# 尾段是文件扩展名的点号串（如 `index.ts` / `modal.json`）不是 RPC/事件名，
# 前端 `request('a.png')`、`invoke('x.js')` 这类命中必须先剔掉。
FILE_EXT_STOP = {
    "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "json", "md", "txt", "csv",
    "db", "sqlite", "log", "png", "jpg", "jpeg", "gif", "apng", "webp", "svg",
    "ico", "html", "htm", "css", "scss", "yml", "yaml", "toml", "lock", "bak",
    "tmp", "woff", "woff2", "ttf", "otf", "exe", "dll", "sh", "bat", "ps1",
    "map", "d", "test", "spec", "vue", "wasm", "zip", "pdf", "mp3", "wav",
}


def plausible_name(s: str) -> bool:
    return bool(RPC_NAME_RE.match(s)) and s.split(".")[-1].lower() not in FILE_EXT_STOP


# ── 前端调用/订阅 API 面（实测来自 desk-notes/packages 实地勘察）────────
RPC_CALLEES = ("call", "callRpc", "rpcCall", "sendRequest", "request", "invoke",
               "stream", "streamTyped", "notify")
EVT_SUB_CALLEES = ("on", "once", "onEvent", "subscribe", "onBackendEvent",
                   "addEventListenerBackend", "onWsEvent")
GW_TOKEN_RE = re.compile(r"\bgetWsClient\b")

warnings: list[str] = []


# ══════════════════════════ 通用文件遍历 ══════════════════════════
def walk_files(root: Path, exts: tuple[str, ...]):
    if not root.is_dir():
        return
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        for f in fn:
            if f.endswith(exts):
                yield Path(dp) / f


def read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        warnings.append(f"unreadable {p}: {exc}")
        return None


def is_testish(p: Path) -> bool:
    name = p.name
    return (".test." in name or ".spec." in name or "__tests__" in p.parts
            or name.endswith(".d.ts") or ".bak" in name.lower())


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


# ══════════════════ 结构腿：注释剥离 + 参数位 0 判定 ══════════════════
def strip_comments(src: str) -> str:
    """去掉 // 与 /* */ 注释，保留字符串/模板字面量与行号结构。

    结构腿与正则腿的本质差别：正则腿会命中注释里的示例代码，结构腿不会。
    """
    out: list[str] = []
    i, n = 0, len(src)
    state = "code"  # code | line | block | sq | dq | tpl
    tpl_depth = 0
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line"
                out.append("  ")
                i += 2
                continue
            if c == "/" and nxt == "*":
                state = "block"
                out.append("  ")
                i += 2
                continue
            if c == "'":
                state = "sq"
            elif c == '"':
                state = "dq"
            elif c == "`":
                state = "tpl"
            out.append(c)
            i += 1
            continue
        if state == "line":
            if c == "\n":
                state = "code"
                out.append("\n")
            else:
                out.append(" ")
            i += 1
            continue
        if state == "block":
            if c == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
            continue
        # 字符串态：处理转义；模板串处理 ${...}
        if c == "\\":
            out.append(src[i:i + 2])
            i += 2
            continue
        if state == "sq" and c == "'":
            state = "code"
        elif state == "dq" and c == '"':
            state = "code"
        elif state == "tpl":
            if c == "`":
                state = "code"
            elif c == "$" and nxt == "{":
                out.append("${")
                i += 2
                tpl_depth += 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


CLOSERS = {")": "(", "]": "[", "}": "{", "`": None}


def first_string_arg(src: str, open_idx: int) -> tuple[str, int] | None:
    """从 open_idx（'(' 的位置）起，取参数位 0 的字符串字面量（内容, 字面量起始偏移）。

    字面量起始偏移用作调用点唯一身份，使正则腿与结构腿可以精确去重。
    要求参数 0 整体是一个 string literal；否则返回 None（避免把
    `foo(bar, 'x.y')` 这类非首参误计为 RPC 调用点）。
    """
    n = len(src)
    j = open_idx + 1
    while j < n and src[j] in " \t\r\n":
        j += 1
    q = src[j] if j < n else ""
    if q not in "'\"`":
        return None
    k = j + 1
    buf: list[str] = []
    while k < n:
        ch = src[k]
        if ch == "\\":
            k += 2
            continue
        if ch == q:
            break
        if q == "`" and ch == "$" and k + 1 < n and src[k + 1] == "{":
            return None  # 模板插值 → 名字非纯静态
        buf.append(ch)
        k += 1
    else:
        return None
    rest = src[k + 1:k + 40]
    if not re.match(r"\s*[,)]", rest):
        return None
    # j 是引号位置；+1 对齐到字面量内容首字符，使正则腿的 m.start(1) 可比可去重
    return "".join(buf), j + 1


def structural_calls(text: str, callees: tuple[str, ...]):
    """结构腿：在剥注释后的源码里找 `.callee(` 且参数位 0 为字符串字面量。"""
    hits: list[tuple[int, str]] = []
    alt = "|".join(sorted(callees, key=len, reverse=True))
    pat = re.compile(r"(?:\.\s*)?(?<![A-Za-z0-9_$])(?:%s)\s*(?:<[^()<>\n]*>)?\s*\(" % alt)
    for m in pat.finditer(text):
        got = first_string_arg(text, m.end() - 1)
        if got:
            hits.append((got[1], got[0]))
    return hits


# ══════════════════════════ ① RPC 采集 ══════════════════════════
REGEX_REGISTER = re.compile(
    r"\bregister(?:_with_schema)?\s*\(\s*[\"']([A-Za-z][A-Za-z0-9_.]+)[\"']")


def parse_py(text: str):
    """解析 Python 源码；BOM 导致的首行失败先剥 BOM 重试（实测 2 个文件带 U+FEFF）。"""
    for candidate in (text, text.lstrip("\ufeff")):
        try:
            return ast.parse(candidate)
        except SyntaxError:
            continue
    return None


def collect_registered() -> tuple[dict[str, list[str]], dict[str, list[str]], list[dict]]:
    """后端注册集。

    AST 腿是判定真源（docstring/注释里的示例 `registry.register("chat.send", …)`
    会被正则腿命中，不能进集合）；正则腿仅对 AST 解析失败的文件兜底，
    其独占命中单列登记供人工复核。返回 (names->files, regex_only, dynamic_sites)。
    """
    ast_map: dict[str, list[str]] = {}
    dynamic: list[dict] = []
    regex_only: dict[str, list[str]] = {}
    for p in walk_files(BACKEND_DIR, (".py",)):
        text = read_text(p)
        if text is None:
            continue
        tree = parse_py(text)
        if tree is None:
            warnings.append(f"ast-parse-failed {rel(p)}（退化为正则腿）")
            for m in REGEX_REGISTER.finditer(text):
                nm = m.group(1)
                if RPC_NAME_RE.match(nm):
                    regex_only.setdefault(nm, []).append(
                        f"{rel(p)}:{text[:m.start()].count(chr(10)) + 1}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            if attr not in ("register", "register_with_schema") or not node.args:
                continue
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                if RPC_NAME_RE.match(a0.value):
                    ast_map.setdefault(a0.value, []).append(f"{rel(p)}:{node.lineno}")
            else:
                prefix = ""
                if isinstance(a0, ast.JoinedStr):
                    for v in a0.values:
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            prefix += v.value
                        else:
                            break
                elif isinstance(a0, ast.BinOp) and isinstance(a0.left, ast.Constant):
                    prefix = str(a0.left.value)
                dynamic.append({"file": rel(p), "line": node.lineno,
                                "static_prefix": prefix})
    return ast_map, regex_only, dynamic


RPC_REGEX_LEGS = [
    re.compile(r"\.\s*(?:call|stream|streamTyped|notify)\s*(?:<[^()<>\n]*>)?\s*\(\s*[\"']([A-Za-z][A-Za-z0-9_]+\.[A-Za-z0-9_.]+)[\"']"),
    re.compile(r"\b(?:syncCall|callRpc|rpcCall|sendRequest|invoke|request)\s*(?:<[^()<>\n]*>)?\s*\(\s*[\"']([A-Za-z][A-Za-z0-9_]+\.[A-Za-z0-9_.]+)[\"']"),
]


def line_of(src: str, off: int) -> int:
    """偏移 → 行号（1 起）。用作两腿命中对齐的稳定身份，容忍 ±2 字符的偏移差。"""
    return src.count("\n", 0, off) + 1


def collect_frontend_calls(roots: list[Path], registered_domains: set[str]) -> dict:
    """前端 RPC 调用集（正则腿 + 结构腿并集），按根目录分组统计。

    调用点身份 = 字符串字面量起始偏移，两腿精确去重后并集，不重复计数。
    两腿都在**剥注释后**的源码上跑（差别只剩模式强弱），注释里的示例代码
    单独登记为 comment_mentions，不进调用集。
    命名空间接地：首段必须命中后端已注册域（或动态注册前缀），否则记入
    unknown_namespace_hits（i18n key / 文件名 / capacitor 插件等同形串）。
    """
    names: dict[str, list[str]] = {}
    sites: dict[str, int] = {}
    regex_only: dict[str, int] = {}
    struct_only: dict[str, int] = {}
    comment_mentions: dict[str, int] = {}
    unknown_ns: dict[str, int] = {}
    per_root: dict[str, dict[str, int]] = {rel(r): {"files": 0, "sites": 0} for r in roots}
    for root in roots:
        if not root.is_dir():
            continue
        for p in walk_files(root, TS_EXT):
            text = read_text(p)
            if text is None:
                continue
            code = strip_comments(text)
            struct = {(nm, line_of(code, off))
                      for off, nm in structural_calls(code, RPC_CALLEES)
                      if plausible_name(nm)}
            regex = {(m.group(1), line_of(code, m.start(1))) for pat in RPC_REGEX_LEGS
                     for m in pat.finditer(code) if plausible_name(m.group(1))}
            raw = {(m.group(1), line_of(text, m.start(1))) for pat in RPC_REGEX_LEGS
                   for m in pat.finditer(text) if plausible_name(m.group(1))}
            for nm, _ln in raw - regex:
                comment_mentions[nm] = comment_mentions.get(nm, 0) + 1
            merged = struct | regex
            grounded = {hit for hit in merged if hit[0].split(".")[0] in registered_domains}
            for nm, _ln in merged - grounded:
                unknown_ns[nm] = unknown_ns.get(nm, 0) + 1
            if not grounded:
                continue
            r = rel(p)
            for nm, _ln in grounded:
                lst = names.setdefault(nm, [])
                if r not in lst:
                    lst.append(r)
            g_struct = {nm for nm, _ in struct if nm.split(".")[0] in registered_domains}
            g_regex = {nm for nm, _ in regex if nm.split(".")[0] in registered_domains}
            for nm in g_struct - g_regex:
                struct_only[nm] = struct_only.get(nm, 0) + 1
            for nm in g_regex - g_struct:
                regex_only[nm] = regex_only.get(nm, 0) + 1
            for nm, _ln in grounded:
                sites[nm] = sites.get(nm, 0) + 1
            pr = per_root[rel(root)]
            pr["files"] += 1
            pr["sites"] += len(grounded)
    return {
        "names": names,
        "sites": sites,
        "regex_only_names": regex_only,
        "structural_only_names": struct_only,
        "comment_only_mentions": comment_mentions,
        "unknown_namespace_hits": unknown_ns,
        "per_root": per_root,
        "distinct_methods": len(names),
        "total_sites": sum(sites.values()),
    }


PYTHON_DISPATCH_RE = re.compile(r"\.\s*dispatch(?:_stream)?\s*\(\s*[\"']([A-Za-z][A-Za-z0-9_.]+)[\"']")


def collect_backend_internal_refs() -> set[str]:
    """后端自身对 RPC 名的引用（R4 重放、内部转调），用于区分「真孤儿」。"""
    refs: set[str] = set()
    for p in walk_files(APP_DIR, (".py",)):
        if p.parts[-2:-1] == ("tests",) or "tests" in p.parts:
            continue
        text = read_text(p)
        if not text:
            continue
        for m in PYTHON_DISPATCH_RE.finditer(text):
            if RPC_NAME_RE.match(m.group(1)):
                refs.add(m.group(1))
    return refs


# ══════════════════════════ ② 事件采集 ══════════════════════════
APP_EVENT_RE = re.compile(r"AppEvent\(\s*[\"']([A-Za-z][A-Za-z0-9_.]*)[\"']")
# 发射系函数名：允许项目自定义包装（_emit_event / _emit_sync_event / fire / broadcast …），
# 实测 `self._emit_sync_event("sync.conflict", ...)`、`_emit_event(bus, "notification.toast", ...)`
# 这类“第二个参数才是事件名”的 helper 是静态扫描的主要漏点。
EMIT_NAME_TOKEN = re.compile(
    r"(emit|fire|publish|broadcast|notify|send_event|dispatch_event|push_event)", re.I)
EMIT_REGEX = re.compile(
    r"\b[\w.]*?(?:emit|fire|publish|broadcast|notify|send_event|dispatch_event|push_event)\w*"
    r"\s*\(\s*(?:[A-Za-z_][\w.]*\s*,\s*)?[\"']([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+)[\"']")
BUS_ON_RE = re.compile(r"\.\s*on\s*\(\s*[\"']([A-Za-z*][A-Za-z0-9_.]*)[\"']")
EVT_NAME_KW = {"event_type", "type", "event", "name"}


def const_event_names(node: ast.AST, lineno: int):
    """从表达式节点取事件名常量；支持三元表达式（实测 `emit("a" if ok else "b", …)`）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) \
            and EVT_NAME_RE.match(node.value):
        return [(node.value, getattr(node, "lineno", lineno))]
    if isinstance(node, ast.IfExp):
        out = []
        for side in (node.body, node.orelse):
            out.extend(const_event_names(side, lineno))
        return out
    return []


def collect_backend_events() -> dict:
    """后端事件发射/内部订阅集。

    Python 侧以 AST 腿为判定真源：docstring 里的示例（`core/events.py` 头部的
    `event_bus.emit(AppEvent("browser.summarized", …))`）会被正则腿命中，若不
    加以区分就是**假发射点**。正则腿只在 AST 解析失败的文件上兜底，其独占
    命中登记进 regex_only（供人工复核，不进集合）。
    """
    emitted: dict[str, list[str]] = {}
    sites: dict[str, int] = {}
    subs: dict[str, list[str]] = {}
    wildcards: list[str] = []
    regex_only: dict[str, int] = {}
    for p in walk_files(APP_DIR, (".py",)):
        if "tests" in p.parts:
            continue
        text = read_text(p)
        if not text:
            continue
        r = rel(p)
        ast_hits: set[tuple[str, int]] = set()
        ast_subs: dict[str, list[str]] = {}
        ast_wild: list[str] = []
        tree = parse_py(text)
        if tree is None:
            warnings.append(f"ast-parse-failed {r}（退化为正则腿）")
        else:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fname = node.func.attr if isinstance(node.func, ast.Attribute) else (
                    node.func.id if isinstance(node.func, ast.Name) else "")
                if fname in ("on", "once", "subscribe") and node.args:
                    a0 = node.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        v = a0.value
                        if v == "*":
                            ast_wild.append(r)
                        elif EVT_NAME_RE.match(v):
                            ast_subs.setdefault(v, []).append(r)
                    continue
                if fname == "AppEvent" or (fname and EMIT_NAME_TOKEN.search(fname)):
                    for a in node.args[:2]:
                        ast_hits.update(const_event_names(a, node.lineno))
                    for kw in node.keywords:
                        if kw.arg in EVT_NAME_KW:
                            ast_hits.update(const_event_names(kw.value, node.lineno))
        rx_hits: set[tuple[str, int]] = set()
        for m in APP_EVENT_RE.finditer(text):
            if EVT_NAME_RE.match(m.group(1)):
                rx_hits.add((m.group(1), line_of(text, m.start(1))))
        for m in EMIT_REGEX.finditer(text):
            if EVT_NAME_RE.match(m.group(1)):
                rx_hits.add((m.group(1), line_of(text, m.start(1))))
        rx_subs: dict[str, list[str]] = {}
        rx_wild: list[str] = []
        for m in BUS_ON_RE.finditer(text):
            v = m.group(1)
            if v == "*":
                rx_wild.append(r)
            elif EVT_NAME_RE.match(v):
                rx_subs.setdefault(v, []).append(r)
        if tree is None:
            hits, sub_src, wild = rx_hits, rx_subs, rx_wild
        else:
            hits, sub_src, wild = ast_hits, ast_subs, ast_wild
            for nm, _ln in rx_hits - ast_hits:
                regex_only[nm] = regex_only.get(nm, 0) + 1
        for nm, _ln in hits:
            sites[nm] = sites.get(nm, 0) + 1
            lst = emitted.setdefault(nm, [])
            if r not in lst:
                lst.append(r)
        for nm, fl in sub_src.items():
            tgt = subs.setdefault(nm, [])
            tgt.extend(x for x in fl if x not in tgt)
        wildcards.extend(wild)
    return {"emitted": emitted, "emit_sites": sites, "py_subscribers": subs,
            "py_wildcard_subscribers": sorted(set(wildcards)),
            "regex_only_mentions": regex_only,
            "distinct_emitted": len(emitted), "total_emit_sites": sum(sites.values())}


FE_SUB_REGEX_API = [
    re.compile(r"\.\s*(?:on|once)\s*(?:<[^()<>\n]*>)?\s*\(\s*[\"']([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+)[\"']"),
    re.compile(r"\b(?:onBackendEvent|onEvent|addEventListenerBackend|onWsEvent)\s*\(\s*[\"']([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+)[\"']"),
]
# 启发式腿：switch/case 与 === 比较（后端事件经统一分发口后进 switch 的形态）；
# 只用于「抑制假零订阅」，不参与反向 diff（否则 i18n key 会污染清单）。
FE_SUB_REGEX_HEUR = [
    re.compile(r"(?:case|===|==)\s*[\"']([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)[\"']"),
]


def collect_frontend_events(roots: list[Path]) -> dict:
    api: dict[str, list[str]] = {}
    heur: dict[str, list[str]] = {}
    test: dict[str, list[str]] = {}
    wildcards: list[str] = []

    def add(bucket: dict[str, list[str]], nm: str, r: str) -> None:
        lst = bucket.setdefault(nm, [])
        if r not in lst:
            lst.append(r)

    for root in roots:
        if not root.is_dir():
            continue
        for p in walk_files(root, TS_EXT):
            text = read_text(p)
            if text is None:
                continue
            code = strip_comments(text)
            r = rel(p)
            bucket = test if is_testish(p) else api
            for _, nm in structural_calls(code, EVT_SUB_CALLEES):
                if nm == "*":
                    wildcards.append(r)
                elif plausible_name(nm):
                    add(bucket, nm, r)
            for pat in FE_SUB_REGEX_API:
                for m in pat.finditer(code):
                    nm = m.group(1)
                    if plausible_name(nm):
                        add(bucket, nm, r)
            if bucket is api:
                for pat in FE_SUB_REGEX_HEUR:
                    for m in pat.finditer(code):
                        nm = m.group(1)
                        if plausible_name(nm):
                            add(heur, nm, r)
    prod_api = {k: v for k, v in api.items()}
    return {"subscribed": prod_api,
            "subscribed_heuristic_only": {
                k: v for k, v in heur.items() if k not in prod_api},
            "subscribed_in_tests_only": {
                k: v for k, v in test.items() if k not in prod_api},
            "wildcard_subscribers": sorted(set(wildcards)),
            "distinct_subscribed": len(prod_api)}


DYN_CALLEE_RE = re.compile(
    r"(?:\.\s*)?(?<![A-Za-z0-9_$])(?:call|callRpc|rpcCall|sendRequest|syncCall|stream|streamTyped)"
    r"\s*(?:<[^()<>\n]*>)?\s*\(")


def dynamic_rpc_sinks(roots: list[Path]) -> list[dict]:
    """首参不是字符串字面量的 RPC 调用点（方法名运行期拼接/透传）。

    这些「动态转发汇点」使静态孤儿清单只能是**上界**——经它们可达的方法
    静态扫不出来，必须交人工/运行时（rpc_census 的 system.status）复核。
    """
    out: list[dict] = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in walk_files(root, TS_EXT):
            text = read_text(p)
            if text is None or is_testish(p):
                continue
            code = strip_comments(text)
            for m in DYN_CALLEE_RE.finditer(code):
                j = m.end()
                while j < len(code) and code[j] in " \t\r\n":
                    j += 1
                if j >= len(code) or code[j] in "'\"`)":
                    continue
                out.append({
                    "file": rel(p),
                    "line": line_of(code, m.start()),
                    "snippet": re.sub(r"\s+", " ", code[m.start():m.start() + 60]).strip(),
                })
    return out


# ══════════════════════ ③ 裸调 RPC 口径定谳 ══════════════════════
CALLSITE_RE = re.compile(
    r"\.\s*(?:call|stream|streamTyped|notify)\s*(?:<[^()<>\n]*>)?\s*\(\s*[\"']([A-Za-z][A-Za-z0-9_]+\.[A-Za-z0-9_.]+)[\"']")
WRAPPED_RE = re.compile(
    r"\b(?:syncCall|callRpc|rpcCall|sendRequest)\s*(?:<[^()<>\n]*>)?\s*\(\s*[\"']([A-Za-z][A-Za-z0-9_]+\.[A-Za-z0-9_.]+)[\"']")


def bare_rpc_census(root: Path, grounding: set[str]) -> dict:
    """同一批源码上并列给出 6 种口径，指出 85 与 64/195 分别对应哪一栏。

    调用点串必须先过 plausible_name（剔 `x.ts` 之类文件名）与命名空间接地
    （首段须为后端已注册域），使 6 栏的「调用点」定义完全一致。
    """
    v1_files = v1_sites = 0          # 标识符 getWsClient 出现面（含 .bak）
    v2_files = 0                     # 同上，排除 .bak / test
    v3_files = v3_sites = 0          # 含 RPC 调用点文件/处（排除封装层）
    v4_files = v4_sites = 0          # V3 ∩ 该文件直接引用 getWsClient  → 裸调
    v5_files = v5_sites = 0          # V4 且仅 desktop 包
    v6_files = v6_sites = 0          # V4 且排除 service 二次封装
    v7_files = v7_sites = 0          # 经封装层（syncCall/callRpc…）调用点（对照组）
    v4_names: set[str] = set()
    bare_list: list[dict] = []
    for p in walk_files(root, TS_EXT):
        text = read_text(p)
        if text is None:
            continue
        r = rel(p)
        code = strip_comments(text)
        gw_all = len(GW_TOKEN_RE.findall(text))
        if gw_all:
            v1_files += 1
            v1_sites += gw_all
        if gw_all and not is_testish(p) and ".bak" not in p.name.lower():
            v2_files += 1
        is_bak = ".bak" in p.name.lower()
        if any(r.endswith(w) for w in WRAPPER_FILES) or is_bak:
            continue
        sites = {m.start(1): m.group(1) for m in CALLSITE_RE.finditer(code)
                 if plausible_name(m.group(1))
                 and m.group(1).split(".")[0] in grounding}
        wrapped = {m.start(1) for m in WRAPPED_RE.finditer(code)
                   if plausible_name(m.group(1))
                   and m.group(1).split(".")[0] in grounding}
        if wrapped and not is_testish(p):
            v7_files += 1
            v7_sites += len(wrapped)
        if not sites:
            continue
        v3_files += 1
        v3_sites += len(sites)
        if GW_TOKEN_RE.search(code) and not is_testish(p):
            v4_files += 1
            v4_sites += len(sites)
            v4_names |= set(sites.values())
            bare_list.append({"file": r, "sites": len(sites)})
            if "/desktop/" in f"/{r}":
                v5_files += 1
                v5_sites += len(sites)
            if not LAYER2_RE.search(r):
                v6_files += 1
                v6_sites += len(sites)
    bare_list.sort(key=lambda d: (-d["sites"], d["file"]))
    return {
        "scope": rel(root),
        "V1_symbol_grep_files_incl_bak": {"files": v1_files, "occurrences": v1_sites},
        "V2_symbol_grep_files_excl_bak_test": {"files": v2_files},
        "V3_callsite_files_excl_wrapper": {"files": v3_files, "sites": v3_sites},
        "V4_bare_callsite_files_FINAL": {
            "files": v4_files, "sites": v4_sites, "distinct_methods": len(v4_names)},
        "V5_bare_desktop_only": {"files": v5_files, "sites": v5_sites},
        "V6_bare_excl_layer2_service": {"files": v6_files, "sites": v6_sites},
        "V7_wrapped_callsite_control": {"files": v7_files, "sites": v7_sites},
        "top_bare_files": bare_list[:15],
        "verdict": "唯一口径 = V4（裸调 RPC 文件数 / 调用点数 / 去重方法数）",
        "reconciliation": {
            "为何历史上出现 85 文件 与 64 文件/195 处 两个数": (
                "两者口径不同且都不可直接引用：①「85 文件」来自 `grep getWsClient` 的**标识符出现"
                "文件面**（含 import 行、类型注解、注释、.bak 目录、非调用用途），对应本表 V1/V2，"
                "不是裸调文件数；②「64 文件/195 处」混用了两个作用域——文件数取自**全 packages 含"
                "调用点的文件**（本表 V3），处数取自**仅 desktop 包**的调用点（本表 V5），拼接后"
                "成为一个既不存在的分子也不存在的分母的复合数。③此外源码自审计当时又有改动，"
                "同一规则今天重跑本身就会漂移。定谳：一律引用 V4，并附作用域=desk-notes/packages、"
                "排除封装层与 .bak/测试、命名空间接地四条规则。"),
        },
    }


# ══════════════════════ ④ info_items 体积定谳 ══════════════════════
KV_KEY = "info_items"


def measure_info_items(candidates: list[Path]) -> dict:
    probes = []
    for db in candidates:
        if not db.is_file():
            probes.append({"db": rel(db), "error": "not-found"})
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            probes.append({"db": rel(db), "error": f"open-failed: {exc}"})
            continue
        try:
            tables = [r[0] for r in con.execute(
                "select name from sqlite_master where type='table'")]
            if "kv_store" not in tables:
                probes.append({"db": rel(db), "error": "no-kv_store-table",
                               "tables": tables})
                continue
            cols = [r[1] for r in con.execute("pragma table_info(kv_store)")]
            row = con.execute(
                "select length(value), length(cast(value as blob)) from kv_store where key=?",
                (KV_KEY,)).fetchone()
            agg = con.execute(
                "select count(*), sum(length(value)), sum(length(cast(value as blob))) from kv_store"
            ).fetchone()
            def _pragma(name: str) -> int | None:
                try:
                    row = con.execute(f"select * from pragma_{name}()").fetchone()
                    return int(row[0]) if row and row[0] is not None else None
                except sqlite3.Error:
                    return None

            pages = _pragma("page_count")
            size = _pragma("page_size")
            free = _pragma("freelist")
            user_version = _pragma("user_version")
            probes.append({
                "db": rel(db),
                "file_bytes": db.stat().st_size,
                "kv_columns": cols,
                "keys_total": agg[0],
                "value_chars_total": agg[1],
                "value_bytes_total": agg[2],
                "pages": pages, "page_size": size, "free_pages": free,
                "user_version": user_version,
                "free_pages_ratio": (round(free / pages, 4) if pages and free is not None else None),
                KV_KEY: None if row is None else {
                    "chars_length_value": row[0],
                    "bytes_utf8": row[1],
                    "MiB_from_chars": round(row[0] / 1048576, 3),
                    "MB_decimal_from_chars": round(row[0] / 1_000_000, 3),
                    "MiB_from_bytes": round(row[1] / 1048576, 3),
                    "MB_decimal_from_bytes": round(row[1] / 1000000, 3),
                    "share_of_db_file": round(row[1] / max(1, db.stat().st_size), 4),
                },
            })
        finally:
            con.close()
    picked = None
    for pr in probes:
        if pr.get(KV_KEY):
            if picked is None or pr[KV_KEY]["chars_length_value"] > picked[KV_KEY]["chars_length_value"]:
                picked = pr
    return {"probes": probes,
            "authoritative_db": picked["db"] if picked else None,
            "result": picked[KV_KEY] if picked else None,
            "picked_rule": "取真正含 info_items 键且 value 最长的 kv_store 库（运行时主库）"}


# ══════════════════════════ main ══════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description="C-4 只读机器 diff 基线")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    registered, reg_regex_only, dyn_sites = collect_registered()
    reg_names = set(registered) | set(reg_regex_only)
    dyn_prefixes = sorted({d["static_prefix"] for d in dyn_sites if d["static_prefix"]})
    grounding = {n.split(".")[0] for n in reg_names} | {
        p.strip(".").split(".")[0] for p in dyn_prefixes}
    fe = collect_frontend_calls([PACKAGES_DIR, MOBILE_DIR], grounding)
    dyn_sinks = dynamic_rpc_sinks([PACKAGES_DIR, MOBILE_DIR])
    internal_refs = collect_backend_internal_refs()
    backend_ev = collect_backend_events()
    frontend_ev = collect_frontend_events([PACKAGES_DIR, MOBILE_DIR])
    bare = bare_rpc_census(PACKAGES_DIR, grounding)
    info = measure_info_items([
        APP_DIR / "data" / "data" / "local_storage.db",
        APP_DIR / "data" / "local_storage.db",
    ])

    called = set(fe["names"])
    orphans_frontend = sorted(n for n in reg_names if n not in called)
    orphans_effective = sorted(
        n for n in orphans_frontend if n not in internal_refs)
    hallucinations = sorted(
        n for n in called
        if n not in reg_names and not any(n.startswith(p) for p in dyn_prefixes))
    called_unmatched_dynamic = sorted(
        n for n in called if n not in reg_names and any(n.startswith(p) for p in dyn_prefixes))

    emitted = set(backend_ev["emitted"])
    fsub = set(frontend_ev["subscribed"])
    heur = set(frontend_ev["subscribed_heuristic_only"])
    psub = set(backend_ev["py_subscribers"])
    fevt = set(frontend_ev["subscribed_in_tests_only"])
    zero_frontend_api = sorted(n for n in emitted if n not in fsub)
    zero_frontend = sorted(n for n in zero_frontend_api if n not in heur)
    heuristic_only = sorted(n for n in zero_frontend_api if n in heur)
    zero_any = sorted(n for n in zero_frontend
                      if n not in psub and n not in fevt)
    never_emitted = sorted(n for n in fsub if n not in emitted)

    payload = {
        "meta": {
            "task": "C-4 机器 diff 基线（唯一真源）",
            "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "python": sys.version.split()[0],
            "read_only": True,
            "script": rel(Path(__file__)),
            "scopes": {
                "backend_rpc_registry": rel(BACKEND_DIR / "**/*.py"),
                "backend_events": rel(APP_DIR / "**/*.py (排除 tests)"),
                "frontend": [rel(PACKAGES_DIR), rel(MOBILE_DIR)],
                "ts_exclude_dirs": sorted(SKIP_DIRS),
                "wrapper_layer_files": sorted(WRAPPER_FILES),
            },
            "rules": {
                "rpc_registered": "AST 腿为判定真源：Call(func.attr ∈ {register,register_with_schema}) 且 args[0] 为字符串常量并匹配 ^[a-z]\\w*(\\.\\w+)+$；docstring/注释里的示例注册点不计入（正则腿只在 AST 解析失败的文件上兜底，独占命中入 registered_regex_only_extra）；args[0] 非纯静态的注册点记入 dynamic_registration_sites，其静态前缀用于豁免「幻觉」误报",
                "rpc_called": "两腿都在剥注释后的源码上跑：正则腿 `.call/.stream/.streamTyped/.notify('域.方法')` 与 `syncCall/callRpc/rpcCall/sendRequest/invoke/request('域.方法')`；结构腿自实现 TS 注释/字符串剥离 + callee 后括号参数位 0 必须整体是字符串字面量（模板插值串判非静态即弃）。两腿并集；仅注释里出现的串登记为 comment_only_mentions；首段不在后端已注册域/动态前缀内的串判为同形噪声入 unknown_namespace_hits（i18n key、文件名、capacitor 插件名）。`.bak`/`__tests__`/`*.test.*`/`*.spec.*`/`*.d.ts` 不计入生产口径",
                "events_emitted": "Python 侧以 AST 腿为真源（callee 名含 emit/fire/publish/broadcast/notify/send_event/dispatch_event/push_event 或 AppEvent(...)，取参数位 0-1 的点号常量串与 event_type/type 关键字），docstring 里的示例发射不计入；正则腿仅对 AST 解析失败文件兜底，其独占命中入 regex_only_mentions 供复核；tests 目录不扫",
                "events_subscribed": "API 腿（判定订阅的唯一依据）：结构腿 callee ∈ {on,once,onEvent,subscribe,onBackendEvent,onWsEvent} + 正则腿 `.on/.once('域.事件')`/`onBackendEvent('域.事件')`；启发式腿 `case/===('域.事件')` 仅用于**抑制假零订阅**（单列 heuristic_only），不参与反向 diff；测试文件订阅单列 test_only",
                "bare_rpc": "裸调 = 非封装层文件（WRAPPER_FILES 之外）+ 该文件自身直接引用 getWsClient() 单例 + 至少 1 个剥注释后且通过命名空间接地的 `('域.方法')` RPC 调用点；V1~V7 七种口径并列给出以定位历史争议数字来源",
                "info_items": "sqlite3 URI mode=ro；表 kv_store（列实地核对见 kv_columns）；SQL length(value) 返回**字符数**，length(cast(value as blob)) 才是 **UTF-8 字节数**——争议数字源于此二者与 MiB/十进制 MB 的单位混用",
            },
            "warnings": warnings,
        },
        "rpc": {
            "registered_total": len(reg_names),
            "registered_ast_only": len(registered),
            "registered_regex_only_extra": sorted(reg_regex_only),
            "dynamic_registration_sites": dyn_sites,
            "dynamic_prefixes": dyn_prefixes,
            "frontend_called_total": len(called),
            "frontend_call_sites_total": fe["total_sites"],
            "frontend_per_root": fe["per_root"],
            "frontend_regex_only_names": fe["regex_only_names"],
            "frontend_structural_only_names": fe["structural_only_names"],
            "frontend_comment_only_mentions": fe["comment_only_mentions"],
            "frontend_unknown_namespace_hits_top": dict(
                sorted(fe["unknown_namespace_hits"].items(),
                       key=lambda kv: (-kv[1], kv[0]))[:30]),
            "backend_internal_dispatch_refs": sorted(internal_refs),
            "dynamic_forwarding_sinks": dyn_sinks,
            "orphans_caveat": (
                f"孤儿清单为静态上界：存在 {len(dyn_sinks)} 处首参非字面量的 RPC 转发点"
                "（syncCall/队列/命令面板/桌宠桥），经它们可达的方法静态扫不出，"
                "需以运行时 system.status 或人工复核收敛"),
            "orphans": {
                "registered_but_never_called_by_frontend": orphans_frontend,
                "count_frontend": len(orphans_frontend),
                "count_effective_excl_internal_refs": len(orphans_effective),
                "effective_list": orphans_effective,
                "registered_files": {
                    k: (registered.get(k) or reg_regex_only.get(k, []))[:3]
                    for k in orphans_frontend},
            },
            "hallucinations": {
                "called_but_not_registered": hallucinations,
                "count": len(hallucinations),
                "matched_by_dynamic_prefix_only": called_unmatched_dynamic,
            },
        },
        "events": {
            "backend_emitted_total": backend_ev["distinct_emitted"],
            "backend_emit_sites": backend_ev["total_emit_sites"],
            "backend_py_wildcard_subscribers": backend_ev["py_wildcard_subscribers"],
            "frontend_subscribed_total": frontend_ev["distinct_subscribed"],
            "frontend_wildcard_subscribers": frontend_ev["wildcard_subscribers"],
            "backend_regex_only_mentions": backend_ev["regex_only_mentions"],
            "emitted_detail": {k: {"sites": backend_ev["emit_sites"][k], "files": v[:4]}
                               for k, v in sorted(backend_ev["emitted"].items())},
            "frontend_subscriptions": {k: sorted(set(v))[:4]
                                       for k, v in sorted(frontend_ev["subscribed"].items())},
            "test_only_subscriptions": {k: sorted(set(v))
                                        for k, v in sorted(frontend_ev["subscribed_in_tests_only"].items())},
            "py_internal_subscriptions": {k: sorted(set(v)) for k, v in sorted(backend_ev["py_subscribers"].items())},
            "zero_frontend_subscribers_API": {
                "list": zero_frontend_api, "count": len(zero_frontend_api)},
            "zero_frontend_subscribers": {
                "list": zero_frontend, "count": len(zero_frontend),
                "note": "已用启发式腿（case/===）抑制假零订阅后的保守清单（重点）",
                "heuristic_suppressed": heuristic_only},
            "zero_subscriber_anywhere": {
                "list": zero_any, "count": len(zero_any)},
            "subscribed_but_never_emitted": {
                "list": never_emitted, "count": len(never_emitted)},
            "heuristic_subscriptions": {
                k: sorted(set(v))[:3]
                for k, v in sorted(frontend_ev["subscribed_heuristic_only"].items())},
        },
        "bare_rpc": bare,
        "info_items": info,
        "verdicts": {
            "裸调RPC": f"{bare['V4_bare_callsite_files_FINAL']['files']} 文件 / "
                       f"{bare['V4_bare_callsite_files_FINAL']['sites']} 处（口径 V4，见 bare_rpc.reconciliation）",
            "info_items体积": f"{info['result']['chars_length_value']} 字符 = "
                              f"{info['result']['MiB_from_chars']} MiB = "
                              f"{info['result']['MB_decimal_from_chars']} MB(十进制)；"
                              f"UTF-8 真实字节 {info['result']['bytes_utf8']} = "
                              f"{info['result']['MiB_from_bytes']} MiB "
                              "→「15.62 vs 16.4」是同一 length(value) 的 MiB/十进制 MB 单位差",
            "事件零订阅": f"零前端 API 订阅 {len(zero_frontend)} 个"
                          f"（启发式腿再抑制 {len(heuristic_only)} 个后）；"
                          f"含后端内部订阅仍零订阅 {len(zero_any)} 个",
            "RPC孤儿": f"已注册未被前端调用 {len(orphans_frontend)} 个；"
                       f"其中后端内部也无引用 {len(orphans_effective)} 个",
            "RPC幻觉": f"被前端调用但未注册 {len(hallucinations)} 个",
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False),
                   encoding="utf-8")

    if not (reg_names and called and backend_ev["emitted"] and info["result"]):
        print("[c4] FAIL: 关键集合为空，疑似采集规则失效（不产出可信基线）", file=sys.stderr)
        print(f"[c4] registered={len(reg_names)} called={len(called)} "
              f"emitted={backend_ev['distinct_emitted']} info={bool(info['result'])}",
              file=sys.stderr)
        return 2

    if not args.quiet:
        ir = info["result"]
        print(f"[c4] 基线已写入 {out}")
        print(f"[c4] RPC: registered={len(reg_names)} called={len(called)} "
              f"orphan(frontend)={len(orphans_frontend)} orphan(effective)={len(orphans_effective)} "
              f"hallucination={len(hallucinations)}")
        print(f"[c4] 事件: emitted={backend_ev['distinct_emitted']} "
              f"fe_subscribed={frontend_ev['distinct_subscribed']} "
              f"零前端订阅={len(zero_frontend)} 完全零订阅={len(zero_any)} "
              f"订阅但从未发射={len(never_emitted)}")
        print(f"[c4] 裸调 RPC 定谳(V4): {bare['V4_bare_callsite_files_FINAL']['files']} 文件 / "
              f"{bare['V4_bare_callsite_files_FINAL']['sites']} 处 / "
              f"{bare['V4_bare_callsite_files_FINAL']['distinct_methods']} 个不同方法；"
              f"符号出现面(V1)={bare['V1_symbol_grep_files_incl_bak']['files']} 文件/"
              f"{bare['V1_symbol_grep_files_incl_bak']['occurrences']} 次")
        print(f"[c4] info_items: {ir['chars_length_value']} 字符 = "
              f"{ir['MiB_from_chars']} MiB = {ir['MB_decimal_from_chars']} MB(十进制)；"
              f"UTF-8 实字节 {ir['bytes_utf8']} = {ir['MiB_from_bytes']} MiB "
              f"(库 {rel(Path(info['authoritative_db']))})")
        if warnings:
            print(f"[c4] warnings={len(warnings)} (详见 meta.warnings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
