"""前后端 JSON-RPC 契约一致性测试。

自动从后端 handler 文件中提取所有 registry.register(...) 注册的方法名，
然后断言前端 desk-notes 调用的方法名都在后端注册列表中。

目的：防止 transcribe.* 那样的「前端调用了后端不存在的方法」契约断裂。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "思忆集app"
DESK_NOTES_DIR = REPO_ROOT / "desk-notes"

# 匹配 registry.register("method.name", ...) 和 registry.register_with_schema("method.name", ...)
RE_BACKEND_REGISTER = re.compile(r'registry\.register(?:_with_schema)?\s*\(\s*["\']([^"\']+)["\']')

# 匹配前端方法调用（兼容泛型与包装，2026-08-20 补全）：
#   .call<...>("m") / .stream<...>("m")     —— wsClient 泛型直调（既有）
#   callRpc<T>("m") / syncCall("m")          —— 本地包装（WordbankModule.tsx / syncCall.ts）
#   client.call("m") / wsClient.call("m")    —— 无泛型对象调用
# 泛型类型参数 <T> / <T,> 可省略；对象前缀（a.b.c.call）可省略。
RE_FRONTEND_CALL = re.compile(r'\b(?:[A-Za-z_$][\w$]*\.)?(?:call|callRpc|syncCall)\s*(?:<[^>]*>)?\s*\(\s*["\']([^"\']+)["\']')
RE_FRONTEND_STREAM = re.compile(r'\b(?:[A-Za-z_$][\w$]*\.)?(?:stream|streamTyped)\s*(?:<[^>]*>)?\s*\(\s*["\']([^"\']+)["\']')


def _collect_backend_methods() -> set[str]:
    """从后端 handler 文件 + 主进程入口提取所有注册的方法名。"""
    methods: set[str] = set()
    for py in (APP_DIR / "backend" / "handlers").rglob("*.py"):
        src = py.read_text(encoding="utf-8", errors="replace")
        methods.update(RE_BACKEND_REGISTER.findall(src))
    # headless_main 内联注册（auth.rotate/auth.revoke/pairing.pending/pairing.approve
    # 依赖 WebSocketServer 实例，未下沉到 handlers/）——纳入扫描，避免契约误报
    # "前端调用但后端未注册"。
    main_entry = APP_DIR / "backend" / "headless_main.py"
    if main_entry.is_file():
        methods.update(RE_BACKEND_REGISTER.findall(main_entry.read_text(encoding="utf-8", errors="replace")))
    return methods


def _collect_frontend_calls() -> set[str]:
    """从前端 desk-notes 代码中提取所有调用的方法名。"""
    import os

    methods: set[str] = set()
    skip_dirs = {"node_modules", ".git", "dist", ".next", "out", "build"}
    for root, dirs, files in os.walk(DESK_NOTES_DIR):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if not (fname.endswith(".ts") or fname.endswith(".tsx")):
                continue
            fpath = Path(root) / fname
            try:
                src = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            methods.update(RE_FRONTEND_CALL.findall(src))
            methods.update(RE_FRONTEND_STREAM.findall(src))
    methods.discard("")
    return methods


# 已知通过动态注册（_module.register_xxx(registry, context)）的方法，
# 不被 registry.register(...) 正则匹配到，但实际后端已注册。
# 从 handler __init__.py 的动态注册模块列表中提取。
# 2026-08-27 R2-4：移除 `info_sources.`（下划线）——真实注册名是 `info.sources.`（点号），
# startswith("info_sources.") 永不匹配，属死豁免；info.sources.* 由 --undocumented 门禁覆盖。
DYNAMICALLY_REGISTERED_PREFIXES = {
    "cron.",  # _cron.register_cron_methods(registry, context)
    "job_hunter.",  # _job_hunter.register(registry, context)
    "rss.",  # _rss.register(registry, context)
    "process.",  # _process.register(registry, context)
    "timeline.",  # _timeline.register(registry, context)
    "task_queue.",  # _task_queue.register(registry, context)
}


def _is_dynamically_registered(method: str) -> bool:
    """判断方法是否通过动态注册（不被正则匹配到但实际已注册）。"""
    return any(method.startswith(prefix) for prefix in DYNAMICALLY_REGISTERED_PREFIXES)


# 前端调用但后端未注册的已知缺口（2026-08-20 改进正则后由测试暴露的真实断裂）。
# 与 UNUSED_METHODS_BASELINE 同理：登记不阻塞 CI，但**新增未登记缺口会失败**，
# 强制修复或登记说明理由。修复后从本集合移除。
#
# 2026-08-20 已清空：`chat.cancel` 曾被登记于此（前端
# desk-notes/packages/desktop/src/components/chat/useChatStream.ts:44 调用但后端
# 未注册，静默 -32601）；同日已在 `思忆集app/backend/handlers/_chat_memory.py`
# 注册 `chat.cancel`（尽力而为确认，前端 AbortController 取消为主），前向断言
# 自然通过——故从白名单移除，未来对该方法改名/删除将被测试捕获。
FRONTEND_CALLS_KNOWN_GAP: set[str] = set()


# 在模块级别收集一次（测试运行时只扫描一次文件系统）
BACKEND_METHODS = _collect_backend_methods()
FRONTEND_CALLS = _collect_frontend_calls()
# 动态注册的方法与已知缺口不计入「缺失」
MISSING = {m for m in (FRONTEND_CALLS - BACKEND_METHODS) if not _is_dynamically_registered(m) and m not in FRONTEND_CALLS_KNOWN_GAP}


def test_backend_has_registered_methods():
    """后端至少注册了一些方法（防止扫描逻辑失效）。"""
    assert len(BACKEND_METHODS) > 100, f"后端只注册了 {len(BACKEND_METHODS)} 个方法，预期 >100，可能是扫描逻辑失效或 handler 文件结构变更"


def test_frontend_has_calls():
    """前端至少调用了一些方法（防止扫描逻辑失效）。"""
    assert len(FRONTEND_CALLS) > 10, f"前端只调用了 {len(FRONTEND_CALLS)} 个方法，预期 >10，可能是扫描逻辑失效或前端代码结构变更"


def test_no_frontend_call_missing_from_backend():
    """前端调用的每个方法都必须在后端注册（否则前端会收到 -32601 Method not found）。"""
    if MISSING:
        # 按域分组输出，方便定位
        details = "\n".join(f"  - {m}" for m in sorted(MISSING))
        pytest.fail(f"前端调用了 {len(MISSING)} 个后端未注册的方法（会返回 -32601 Method not found）:\n{details}")


def test_contract_coverage_ratio():
    """前后端方法匹配率应 > 50%（防止前端只用了极少数后端方法）。"""
    matched = FRONTEND_CALLS & BACKEND_METHODS
    ratio = len(matched) / len(FRONTEND_CALLS) if FRONTEND_CALLS else 0
    assert ratio > 0.5, f"前后端方法匹配率仅 {ratio:.1%}（{len(matched)}/{len(FRONTEND_CALLS)}），可能存在大量契约断裂"


# ---------------------------------------------------------------------------
# S11: 未调用方法基线（防止新增僵尸接口）
# ---------------------------------------------------------------------------
# 2026-08-07 审查结论：以下方法后端已注册，但前端（desk-notes / 手机端app）
# 与后端内部均无真实调用，属于「已实现但前端未接入」的僵尸接口。
# 这里登记为基线白名单，**不阻塞 CI**；但若有人新增了未调用方法，本测试会
# 失败，强制 PR 作者要么补前端调用、要么把方法加入此基线（需说明理由）。
#
# 详见 claudejiaoben/audit_unused_methods_report.md
# 2026-09-23 审计波次①-c：@deprecated 僵尸 RPC 批量下线（M-6）——本基线原 76 项中
# 68 个已随注册摘除而从基线删除（它们不再注册，若留在基线会被
# test_baseline_methods_still_registered 判为过期）。保留的 8 项：
#   - agent.list / usage.today：曾被标 @deprecated，但复核发现前端
#     AgentContextPanel.tsx 有真实调用（client.call<...>），确认未死，保留注册与本基线；
#   - file.write / notification.test：虽前端无 RPC 调用，但分别是 _R4_METHODS 集中清单的
#     sandbox 规范样本（配真实 register_precheck + test_adjudication_r23 / test_r2b_transfer_r4 /
#     test_r2_verify 三套安全回归）与 TestV6NotificationTestChannel 验证样本，非纯僵尸，
#     本波不下线，保留注册与本基线，遗留 lead 决策；
#   - usage.dashboard / usage.export / inbox.list_confirmations / inbox.respond_confirmation：
#     在基线但无 @deprecated 标注，不在本波下线范围。
UNUSED_METHODS_BASELINE: set[str] = {
    "agent.list",
    "file.write",
    "inbox.list_confirmations",
    "inbox.respond_confirmation",
    "notification.test",
    "usage.dashboard",
    "usage.export",
    "usage.today",
}

# 扩展前端调用模式：对象属性调用 client.call('m') / wsClient.call('m') / ws.call('m') /
# invoke.call('m') / router.call('m') / bridge.call('m')。
# 注意：syncCall 是裸函数调用（syncCall('m')），不是 syncCall.call('m') ——
# 2026-08-20 修正，syncCall 已由 RE_FRONTEND_CALL 覆盖。
RE_FRONTEND_PLAIN_CALL = re.compile(r'\b(?:client|wsClient|ws|invoke|router|bridge)\.call\s*\(\s*["\']([^"\']+)["\']')
# 手机端 callRpc('m') / callRpc<T>('m') 直接调用（2026-08-20 补泛型）
RE_MOBILE_CALLRPC = re.compile(r'\bcallRpc\s*(?:<[^>]*>)?\s*\(\s*["\']([^"\']+)["\']')
# 任意点号字符串字面量（粗匹配，用于白名单/常量定义）
RE_DOT_STRING = re.compile(r'["\']([a-zA-Z][a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+)["\']')


def _collect_all_frontend_refs() -> set[str]:
    """收集 desk-notes + 手机端app 中所有疑似 method 名字符串引用。"""
    import os

    refs: set[str] = set()
    skip_dirs = {"node_modules", ".git", "dist", ".next", "out", "build", "archive"}
    roots = [
        REPO_ROOT / "desk-notes",
        REPO_ROOT / "思忆集手机端app",
    ]
    for base in roots:
        if not base.exists():
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                if not (fname.endswith((".ts", ".tsx", ".js", ".jsx", ".vue"))):
                    continue
                fpath = Path(root) / fname
                try:
                    src = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                refs.update(RE_FRONTEND_CALL.findall(src))
                refs.update(RE_FRONTEND_STREAM.findall(src))
                refs.update(RE_FRONTEND_PLAIN_CALL.findall(src))
                refs.update(RE_MOBILE_CALLRPC.findall(src))
                refs.update(RE_DOT_STRING.findall(src))
    refs.discard("")
    return refs


def _collect_backend_internal_refs() -> set[str]:
    """收集后端内部互调引用（排除 register 行自身）。"""
    refs: set[str] = set()
    for py in (APP_DIR / "backend").rglob("*.py"):
        try:
            src = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # 找出所有 method 名字符串
        for m in RE_DOT_STRING.findall(src):
            refs.add(m)
    return refs


ALL_FRONTEND_REFS = _collect_all_frontend_refs()
BACKEND_INTERNAL_REFS = _collect_backend_internal_refs()

# 后端注册但前端 + 后端内部均未引用的方法（真死代码）
_REGISTERED = BACKEND_METHODS
_REFERENCED = ALL_FRONTEND_REFS | BACKEND_INTERNAL_REFS
CURRENT_UNUSED = {m for m in _REGISTERED if m not in _REFERENCED and not _is_dynamically_registered(m)}


def test_no_new_unused_methods():
    """新增的后端方法必须被前端或后端内部调用，否则视为僵尸接口。

    若本测试失败，说明你新增了一个后端方法但没有任何调用方：
      - 若是功能未完成，请补前端调用；
      - 若是内部互调但静态扫描漏检，请在 UNUSED_METHODS_BASELINE 中补上并注明理由；
      - 若确认是废弃桩，同样加入基线并注明 @deprecated。
    """
    new_unused = CURRENT_UNUSED - UNUSED_METHODS_BASELINE
    if new_unused:
        details = "\n".join(f"  - {m}" for m in sorted(new_unused))
        pytest.fail(
            f"检测到 {len(new_unused)} 个新增的未调用后端方法（僵尸接口）。\n"
            f"请补前端调用，或在 UNUSED_METHODS_BASELINE 中登记并说明理由:\n{details}\n"
            f"审查脚本: claudejiaoben/audit_unused_methods.py"
        )


def test_baseline_methods_still_registered():
    """基线中的方法仍应在后端注册列表中（防止基线过期）。"""
    gone = UNUSED_METHODS_BASELINE - _REGISTERED
    if gone:
        details = "\n".join(f"  - {m}" for m in sorted(gone))
        pytest.fail(f"UNUSED_METHODS_BASELINE 中有 {len(gone)} 个方法已不在后端注册列表中，请从基线移除:\n{details}")


# ---------------------------------------------------------------------------
# 2026-10-15 A2：变量/三元拼名调用点盲区（58e5fa8 误删注册的直接成因）
# ---------------------------------------------------------------------------
# 背景：evolution.confirm_upgrade / final_apply / skip_upgrade 三个方法前端经
# 三元表达式拼名（ManagementModule.tsx:135 `const method = kind === 'confirm'
# ? 'evolution.confirm_upgrade' : ...`）后以 client.call(method) 调用——
# RE_FRONTEND_CALL 只匹配 call("字面量")，此类调用点的字面量出现在赋值 RHS
# 而非调用参数里，静态扫描历来扫不到；58e5fa8 的「前端零调用」三重确认因此
# 漏杀了它们（同批共 59 个注册被误删，其中 16 个前端在调）。
# 本测试补齐该盲区：调用点为标识符时，回溯同文件内该标识符的最近一次赋值，
# 提取 RHS 中的方法字面量与模板串前缀，一并纳入契约断言。

RE_CALL_VAR_ARG = re.compile(r'\b(?:[A-Za-z_$][\w$]*\.)?(?:call|callRpc|syncCall)\s*(?:<[^>]*>)?\s*\(\s*(?!["\'`])([A-Za-z_$][\w$]*)')
RE_ASSIGN_LOCAL = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*([^\n;]+)")
RE_METHOD_LIT = re.compile(r'["\']([a-zA-Z][a-zA-Z0-9_]*\.[a-zA-Z0-9_.]+)["\']')
RE_TEMPLATE_PREFIX = re.compile(r"`([a-zA-Z][a-zA-Z0-9_]*\.)[^`]*\$\{")

# 泛封装/透传层：这些文件的 call(method) 是框架通道，方法名由上层调用方
# 提供（已由 RE_FRONTEND_CALL 覆盖），不做回溯解析。
PASSTHROUGH_FILES = {
    "syncCall.ts",
    "ws-client.ts",
    "rpcClient.ts",
    "ws-provider.ts",
    "pluginSystem.ts",
    "syncQueue.ts",
}


def _collect_frontend_varcall_methods() -> tuple[set[str], set[str], dict[str, str]]:
    """收集变量/三元拼名调用点的方法字面量与模板串前缀（含定位）。"""
    import os

    methods: set[str] = set()
    prefixes: set[str] = set()
    locs: dict[str, str] = {}
    skip_dirs = {"node_modules", ".git", "dist", ".next", "out", "build", "archive"}
    roots = [REPO_ROOT / "desk-notes", REPO_ROOT / "思忆集手机端app"]
    for base in roots:
        if not base.exists():
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                if not fname.endswith((".ts", ".tsx")) or fname in PASSTHROUGH_FILES:
                    continue
                fpath = Path(root) / fname
                try:
                    src = fpath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = str(fpath.relative_to(REPO_ROOT))
                for m in RE_CALL_VAR_ARG.finditer(src):
                    var = m.group(1)
                    line = src[: m.start()].count("\n") + 1
                    assigns = [mm for mm in RE_ASSIGN_LOCAL.finditer(src) if mm.group(1) == var and mm.start() < m.start()]
                    if not assigns:
                        continue
                    rhs = assigns[-1].group(2)
                    for lit in RE_METHOD_LIT.findall(rhs):
                        methods.add(lit)
                        locs.setdefault(lit, f"{rel}:{line}")
                    tpl = RE_TEMPLATE_PREFIX.search(rhs)
                    if tpl:
                        prefixes.add(tpl.group(1))
                        locs.setdefault(tpl.group(1), f"{rel}:{line}")
    return methods, prefixes, locs


VARCALL_METHODS, VARCALL_PREFIXES, VARCALL_LOCS = _collect_frontend_varcall_methods()


def test_frontend_varcall_methods_registered():
    """变量/三元拼名调用点提取的方法必须已注册（58e5fa8 误删盲区防线）。"""
    missing = {m for m in VARCALL_METHODS if m not in BACKEND_METHODS and not _is_dynamically_registered(m)}
    if missing:
        details = "\n".join(f"  - {m}  ({VARCALL_LOCS.get(m, '?')})" for m in sorted(missing))
        pytest.fail(f"前端变量调用点引用了 {len(missing)} 个后端未注册的方法:\n{details}")


def test_frontend_varcall_template_prefixes_registered():
    """模板串拼名前缀必须至少有一个注册方法（call(`prefix.${{x}}`) 防线）。"""
    bad = [p for p in VARCALL_PREFIXES if not any(mm.startswith(p) for mm in BACKEND_METHODS)]
    if bad:
        details = "\n".join(f"  - {p}  ({VARCALL_LOCS.get(p, '?')})" for p in sorted(bad))
        pytest.fail(f"前端模板串拼名前缀无任何注册方法:\n{details}")
