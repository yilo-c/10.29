#!/usr/bin/env python3
"""CI 引用链自检 —— 静态核对 CI 资产中引用的本地路径是否真实存在。

背景（M0b-A，2026-09-12）：python-tests.yml 曾引用从未入库的
scripts/check_garbled_text.py（引用先于本体），该步非零退出后其后无
if: always() 的全部步骤级联停摆。本脚本把"CI 死引用"从事后排查变成
事前门禁：

  1. 遍历 .github/workflows/*.yml|.yaml：
     - run 命令行中的脚本路径字面量（scripts/*.py、*.ps1/*.mjs、./gradlew 等）
       按步骤/任务 working-directory 解析后核验存在性；
     - `pnpm|npm run <script>`：核验 package.json 中存在该 script；其命令若含
       vite，则要求 working-directory 下存在 index.html 或 vite.config.*；
       命令内的路径 token 亦核验；`--filter <pkg> run <s>` 递归核验；
     - 含 ${{ ... }} 表达式的 token 与 hashFiles 守卫步骤不做硬断言。
  2. scripts/build_desktop.py：AST 提取 `PROJECT_ROOT / "…" / "…"` 字面量
     路径链（build/dist 开头的产物目录跳过），核验存在性。
  3. release/latest.yml（根与 desk-notes）：其中 path:/url: 声明的产物名
     需与同目录实际文件一致（electron-updater 按此名取包）。

退出码：发现缺失引用 → 1（全部为已登记项且 --allow-registered 时 → 0）；
全通过 → 0。

用法：
    python scripts/ci_selfcheck.py [--allow-registered] [--verbose]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        pass

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO / ".github" / "workflows"

# 已登记、归后续批次修复的已知缺口（location 前缀匹配）。仅当 --allow-registered
# 时豁免退出码；清单仍会打印并标注 [registered]。修复完成后请删除对应登记项。
REGISTERED_GAPS = [
    # ② spec 文件从未入库，build_desktop.py 前置检查必失败（只登记不修）
    "scripts/build_desktop.py:*思忆集app/思忆集.spec*",
    # ③（已修复 2026-09-12 C-12）desk-notes-ci.yml Build 步骤 working-directory
    #    已指到 packages/desktop；登记项移除。
    # ④ latest.yml 声明 @desk-notes/desktop-setup-1.2.0.exe，实际产物
    #    "思忆便签 Setup 1.2.0.exe"（electron-builder 默认产物名）
    "desk-notes/release/latest.yml:*",
]

SCRIPT_EXT = (".py", ".ps1", ".mjs", ".cjs", ".sh", ".spec", ".bat", ".cmd")
PATH_TOKEN_RE = re.compile(
    r"(?<![\w./\\-])((?:[\w.\-\u4e00-\u9fff]+[/\\])+[\w.\-\u4e00-\u9fff]*"
    r"\.(?:py|ps1|mjs|cjs|sh|spec))(?![\w./\\-])"
)
BARE_SCRIPT_RE = re.compile(
    r"(?<![\w./\\\-])([\w\-\u4e00-\u9fff]+\.(?:py|ps1|mjs|cjs))(?![\w./\\-])"
)
DOT_EXEC_RE = re.compile(r"(?<![\w./\\-])(\.[/\\][\w.\-]+)(?![\w./\\-])")
RUNNER_EXPR_RE = re.compile(r"\$\{\{.*?\}\}", re.S)
PKG_RUN_RE = re.compile(r"\b(?:pnpm|npm|yarn)\s+(?:--\S+\s+)*(?:-w\s+)*run\s+([\w:.\-]+)")
PKG_BARE_RE = re.compile(r"\b(?:pnpm|npm|yarn)\s+(?![\w-]+=)([a-z][\w:.\-]*)")
FILTER_RUN_RE = re.compile(
    r"--filter\s+(\S+)\s+(?:run\s+)?([\w:.\-]+)"
)
VITE_CMD_RE = re.compile(r"(?:^|[\s&|(\"'])vite(?:\s|$)")
PM_BUILTIN = {
    "install", "ci", "audit", "add", "remove", "uninstall", "run", "exec",
    "dlx", "list", "ls", "link", "login", "logout", "publish", "store",
    "up", "update", "why", "patch", "rebuild", "doctor", "init", "ping",
    "version", "deploy", "fetch", "pack", "prune", "cache", "config", "env",
    "server", "start", "stop", "test",  # test 由 package.json 声明与否均合法
}

_INDENT_STEP = re.compile(r"^(\s*)-\s+(?:name:|uses:|\|>|script:)")
_WORKDIR_RE = re.compile(r"^\s*(?:-\s+)?working-directory:\s*['\"]?(.+?)['\"]?\s*$")
_IF_RE = re.compile(r"^\s*if:\s*(.+?)\s*$")
_RUN_RE = re.compile(r"^(\s*)run:\s*(\||>-|>)?\s*(.*)$")


def _is_registered(location: str, message: str) -> bool:
    from fnmatch import fnmatch

    key = f"{location}:{message}"
    return any(fnmatch(key, pat) for pat in REGISTERED_GAPS)


def _check_token(token: str, wd: Path) -> Path | None:
    """返回解析成功的现有路径；不存在返回 None。"""
    t = token.replace("\\", "/")
    for base in (wd, REPO):
        cand = (base / t).resolve()
        if cand.exists():
            return cand
    return None


def _pkg_scripts(wd: Path) -> dict | None:
    for base in [wd, *wd.parents]:
        pj = base / "package.json"
        if pj.is_file():
            try:
                return json.loads(pj.read_text("utf-8")).get("scripts", {})
            except (ValueError, OSError):
                return None
    return None


def _find_filter_pkg_dir(name: str) -> Path | None:
    for pj in REPO.glob("*/packages/*/package.json"):
        try:
            if json.loads(pj.read_text("utf-8")).get("name") == name:
                return pj.parent
        except (ValueError, OSError):
            continue
    return None


def _check_script_command(command: str, wd: Path, location: str, defects: list) -> None:
    """核验一个 npm/pnpm script 的命令行：vite 入口 + 路径 token。"""
    for m in PATH_TOKEN_RE.finditer(command):
        tok = m.group(1)
        if "*" in tok:
            continue
        if _check_token(tok, wd) is None:
            defects.append((location, f"script 命令引用的文件不存在: {tok}"))
    if VITE_CMD_RE.search(command):
        has_entry = (wd / "index.html").exists() or any(wd.glob("vite.config.*"))
        if not has_entry:
            defects.append(
                (location,
                 f"vite 入口缺失: {wd.relative_to(REPO).as_posix()} 下无 "
                 f"index.html/vite.config.*（monorepo 入口疑似在 packages/* 子包）")
            )
    for m in FILTER_RUN_RE.finditer(command):
        pkg_name, sub_script = m.group(1), m.group(2)
        pkg_dir = _find_filter_pkg_dir(pkg_name)
        if pkg_dir is None:
            defects.append((location, f"--filter 目标包不存在: {pkg_name}"))
            continue
        sub = _pkg_scripts(pkg_dir) or {}
        if sub_script not in PM_BUILTIN and sub_script not in sub:
            defects.append(
                (location, f"filter 包 {pkg_name} 无 script: {sub_script}")
            )
        else:
            _check_script_command(sub.get(sub_script, ""), pkg_dir, location, defects)


def scan_workflows(defects: list, verbose: bool) -> int:
    files = sorted(list(WORKFLOWS_DIR.glob("*.yml")) + list(WORKFLOWS_DIR.glob("*.yaml")))
    for wf in files:
        rel = wf.relative_to(REPO).as_posix()
        lines = wf.read_text("utf-8", errors="replace").splitlines()
        job_wd: Path | None = None
        step_wd: Path | None = None
        step_if = ""
        in_defaults = False
        seen_step = False
        i = 0
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            if stripped.startswith("#"):
                i += 1
                continue
            if _INDENT_STEP.match(raw):
                step_wd = job_wd
                step_if = ""
                seen_step = True
            if re.match(r"^\s*defaults:\s*$", raw):
                in_defaults = True
            m = _WORKDIR_RE.match(raw)
            if m:
                wd_val = m.group(1).strip().strip("'\"")
                target = (REPO / wd_val.lstrip("./").replace("\\", "/")).resolve()
                if in_defaults or not seen_step:
                    job_wd = target
                else:
                    step_wd = target
            m = _IF_RE.match(raw)
            if m:
                step_if = m.group(1)
            if stripped and not re.match(r"^\s*(defaults|run):", raw):
                in_defaults = False
            m = _RUN_RE.match(raw)
            if m:
                run_indent, block_marker, inline = m.group(1), m.group(2), m.group(3)
                cmd_lines = []
                if inline:
                    cmd_lines.append((i + 1, inline))
                if block_marker:
                    j = i + 1
                    while j < len(lines):
                        nxt = lines[j]
                        if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= len(run_indent):
                            break
                        cmd_lines.append((j + 1, nxt.strip()))
                        j += 1
                    i = j - 1
                wd = step_wd or job_wd or REPO
                conditional = "hashFiles" in step_if
                for lineno, cline in cmd_lines:
                    cline = RUNNER_EXPR_RE.sub("", cline)
                    if cline.lstrip().startswith("#"):
                        continue
                    location = f"{rel}:L{lineno}"
                    for mm in DOT_EXEC_RE.finditer(cline):
                        tok = mm.group(1).replace("\\", "/")
                        if _check_token(tok, wd) is None:
                            _record(defects, location, f"引用的可执行文件不存在: {tok}", conditional, verbose)
                    for mm in PATH_TOKEN_RE.finditer(cline):
                        tok = mm.group(1)
                        if "*" in tok:
                            continue
                        if _check_token(tok, wd) is None:
                            _record(defects, location, f"CI 引用的脚本不存在: {tok}", conditional, verbose)
                    for mm in BARE_SCRIPT_RE.finditer(cline):
                        tok = mm.group(1)
                        if "/" in tok or "\\" in tok:
                            continue
                        if _check_token(tok, wd) is None:
                            _record(defects, location, f"CI 引用的脚本不存在: {tok}", conditional, verbose)
                    for mm in PKG_RUN_RE.finditer(cline):
                        script = mm.group(1)
                        scripts = _pkg_scripts(wd)
                        if scripts is None:
                            _record(defects, location,
                                    f"{wd.relative_to(REPO).as_posix()} 下找不到 "
                                    f"package.json（无法解析 run {script}）",
                                    conditional, verbose)
                            continue
                        if script not in scripts:
                            _record(defects, location, f"package.json 无 script: {script}", conditional, verbose)
                            continue
                        _check_script_command(scripts[script], wd, location, defects)
                    for mm in PKG_BARE_RE.finditer(cline):
                        script = mm.group(1)
                        if script in PM_BUILTIN or re.match(r"run$", script):
                            continue
                        scripts = _pkg_scripts(wd)
                        if scripts is not None and script not in scripts:
                            _record(defects, location, f"package.json 无 script: {script}（裸 pnpm/npm 调用）", conditional, verbose)
            i += 1
    return len(files)


def _record(defects: list, location: str, message: str, conditional: bool, verbose: bool) -> None:
    if conditional:
        if verbose:
            print(f"  [skip-if-guarded] {location}: {message}")
        return
    defects.append((location, message))


def scan_build_desktop(defects: list) -> None:
    py = REPO / "scripts" / "build_desktop.py"
    if not py.is_file():
        defects.append(("scripts/build_desktop.py", "文件不存在"))
        return
    tree = ast.parse(py.read_text("utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        parts: list[str] = []
        cur: ast.expr = node.value
        while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div) and isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
            parts.append(cur.right.value)
            cur = cur.left
        if not (isinstance(cur, ast.Name) and cur.id == "PROJECT_ROOT" and parts):
            continue
        parts.reverse()
        if parts[0] in {"build", "dist"}:  # 构建产物目录，提交库中无需存在
            continue
        rel = Path(*parts)
        if not (REPO / rel).exists():
            defects.append(
                (f"scripts/build_desktop.py:L{node.lineno}",
                 f"引用的本地路径不存在: {rel.as_posix()}")
            )


def scan_latest_yml(defects: list) -> None:
    # 限定候选目录，避免 ** 遍历踩到 node_modules 断链/性能陷阱
    candidates = [
        REPO / "release",
        REPO / "desk-notes" / "release",
        REPO / "desk-notes" / "packages" / "desktop" / "release",
        REPO / "思忆集app" / "release",
    ]
    files = [c / "latest.yml" for c in candidates if (c / "latest.yml").is_file()]
    for f in files:
        rel_dir = f.parent
        text = f.read_text("utf-8", errors="replace")
        urls = set(re.findall(r"^\s*(?:-\s+)?(?:url|path):\s*'?([^'\"]+?)'?\s*$", text, re.M))
        for url in sorted(urls):
            if "*" in url or url.startswith(("http://", "https://")):
                continue
            name = url.split("?")[0].replace("\\", "/")
            if not (rel_dir / name).exists():
                actual = ", ".join(
                    p.name for p in rel_dir.glob("*.exe")
                ) or "(无 exe)"
                defects.append(
                    (f"{f.relative_to(REPO).as_posix()}",
                     f"声明的产物不存在: {url}（同目录实际产物: {actual}）")
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI 引用链自检")
    parser.add_argument("--allow-registered", action="store_true",
                        help="仅当所有缺口都已登记（REGISTERED_GAPS）时返回 0")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    defects: list = []
    n_wf = scan_workflows(defects, args.verbose)
    scan_build_desktop(defects)
    scan_latest_yml(defects)

    print(f"[ci-selfcheck] 已扫描 {n_wf} 个 workflow + scripts/build_desktop.py + latest.yml")
    if not defects:
        print("[ci-selfcheck] 结论: PASS（无缺失引用）")
        return 0

    unregistered = 0
    print(f"[ci-selfcheck] 发现 {len(defects)} 处缺失引用：")
    for location, message in defects:
        reg = _is_registered(location, message)
        if not reg:
            unregistered += 1
        print(f"  [MISSING]{'[已登记]' if reg else '[新缺口]'} {location}: {message}")
    print(f"[ci-selfcheck] 结论: FAIL（已登记 {len(defects) - unregistered} / 新缺口 {unregistered}）")
    if args.allow_registered and unregistered == 0:
        print("[ci-selfcheck] --allow-registered: 全部为已登记缺口，按通过处理")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
