#!/usr/bin/env python3
"""中文乱码（mojibake）检测门禁 —— CI python-tests.yml「Garbled-text check」步骤本体。

重建说明（M0b-A，2026-09-12）：
    原 scripts/check_garbled_text.py 自提交 db1e575 被引用进 python-tests.yml
    后从未在 git 历史中出现过（git log --all -- '*garbled*' 无任何版本），
    属"引用先于本体"的死链，导致 CI 该步必挂且其后步骤级联停摆。
    本文件为按门禁原意重建的最小可用版。

检测项（轻量启发式，面向"源码/配置文本"而非数据文件）：
    [E1] 严格 UTF-8 解码失败 —— 文件含非法 UTF-8 字节
    [E2] 文本中出现 U+FFFD 替换符（errors=replace 解码的残留痕迹）
    [E3] "UTF-8 字节被按 GBK 误读"后的高频签名（罕见组合字，
         如 EF BF BD 三连按 GBK 读出的经典形态；签名表用 \\u 转义书写，
         避免本文件自身触发自身门禁）
    [E4] "UTF-8 字节被按 Latin-1/cp1252 误读"的双重编码（如 你好 变成
         a+符号+… 形态）：判定 = 该行可按 cp1252 重新编码后能严格解码为
         UTF-8、结果与原文不同、且解码结果含中文而原文不含

扫描范围（默认）：思忆集app/ scripts/ automation_hub/ 下的源码与配置文本，
自动排除 data/、备份、虚拟环境、构建产物等非源码目录。

用法：
    python scripts/check_garbled_text.py
    python scripts/check_garbled_text.py --paths 思忆集app scripts --max-report 60

退出码：0 = 未发现乱码；1 = 发现乱码（输出 file:line 清单）；2 = 参数/环境错误
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF_PATH = Path(__file__).resolve()

DEFAULT_TARGETS = ["思忆集app", "scripts", "automation_hub"]

SOURCE_EXTS = {
    ".py", ".html", ".htm", ".css", ".js", ".ts", ".tsx", ".jsx",
    ".toml", ".yml", ".yaml", ".cfg", ".ini", ".mjs", ".cjs",
}

EXCLUDE_DIR_NAMES = {
    "__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", "dist", "build", "web_dist", "data", "backups",
    "snapshots", ".venv", ".venv3.11", "venv", "env", "e2e-report",
    "fonts", "coverage", ".nyc_output",
}

MAX_FILE_BYTES = 2 * 1024 * 1024

# UTF-8 字节流被按 GBK 误读后的典型产物签名（\u 转义书写，见模块 docstring
# E3 条说明）。这些生僻组合几乎不会出现在正常简体中文源码中。
GBK_MISREAD_SIGNATURES = [
    "\u951f\u65a4\u62f7",  # 锟斤拷
    "\u70eb\u70eb\u70eb",  # 烫烫烫
    "\u5c6f\u5c6f\u5c6f",  # 屯屯屯
    "\u6d63\u72b2\u30bd",  # 你好→GBK 误读
    "\u93b7\u517c\u5e34",  # 拼接→GBK 误读
    "\u95c3\u53c9\u59e2",  # 防护→GBK 误读
    "\u7f02\u682b\u721c",  # 编码→GBK 误读
    "\u9473\u85c9\u59cf",  # 能力→GBK 误读
    "\u93bb\u638d\u6b22",  # 插件→GBK 误读
    "\u7481\u5757\u68f6",  # 访问→GBK 误读
    "\u7487\u950b\u7730",  # 请求→GBK 误读
    "\u6769\u65bf\u6d16",  # 返回→GBK 误读
    "\u935a\u5c7e",  # 同步→GBK 误读
    "\u6434\u65c2\u6564",  # 应用→GBK 误读
    "\u93c1\u7248\u5d41",  # 数据→GBK 误读
    "\u93c2\u56e6\u6b22",  # 文件→GBK 误读
    "\u95b0\u5d87\u7586",  # 配置→GBK 误读
    "\u7035\u55db\u631c",  # 密钥→GBK 误读
]

CJK_LOW = 0x4E00
CJK_HIGH = 0x9FFF

# 存量乱码基线（M0b-A 重建门禁时登记，2026-09-12）：以下为已提交源码中的
# 存量 mojibake（文件 → 命中行号集合），修复归后续批次、不在本任务动 tests/
# 文件；对基线之外的任何新增乱码本门禁仍为硬阻塞。修复后请从基线删除对应项。
KNOWN_GARBLED_BASELINE = {
    "思忆集app/tests/test_sync_conflict_paths.py": {1},
    "思忆集app/tests/test_task_reminder_smoke.py": {42},
}


def _has_cjk(text: str) -> bool:
    return any(CJK_LOW <= ord(ch) <= CJK_HIGH for ch in text)


def _is_excluded(path: Path, *bases: Path) -> bool:
    for base in bases:
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        if any(part in EXCLUDE_DIR_NAMES for part in rel.parts):
            return True
    return False


def _iter_source_files(target: Path):
    if target.is_file():
        yield target
        return
    for p in sorted(target.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SOURCE_EXTS:
            continue
        resolved = p.resolve()
        if resolved == SELF_PATH:
            continue
        if _is_excluded(resolved, REPO_ROOT, target.resolve()):
            continue
        try:
            if resolved.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield resolved


def _scan_line_mojibake(line: str) -> str | None:
    """返回该行乱码类型描述；None 表示正常。"""
    if "\ufffd" in line:
        return "E2 含 U+FFFD 替换符"
    for sig in GBK_MISREAD_SIGNATURES:
        if sig in line:
            return "E3 GBK 误读签名"
    stripped = line.rstrip("\n")
    if stripped and not stripped.isascii() and not _has_cjk(stripped):
        try:
            recovered = stripped.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            recovered = ""
        if recovered and recovered != stripped and _has_cjk(recovered):
            return "E4 Latin-1/cp1252 双重编码（疑似 UTF-8 被误读）"
    return None


def scan_file(path: Path) -> list[tuple[str, int, str]]:
    """返回 (相对路径, 行号, 描述) 列表。"""
    try:
        rel = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = str(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [(rel, 0, f"[E0] 无法读取文件: {exc}")]
    if b"\x00" in raw[:4096]:
        return []  # 疑似二进制，跳过
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        line_no = raw[: exc.start].count(b"\n") + 1
        return [(rel, line_no, f"[E1] 非法 UTF-8 字节（{exc.reason}）")]
    findings: list[tuple[str, int, str]] = []
    for no, line in enumerate(text.splitlines(), 1):
        kind = _scan_line_mojibake(line)
        if kind:
            findings.append((rel, no, f"[{kind}] {line.strip()[:60]!r}"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="源码乱码（mojibake）检测门禁")
    parser.add_argument("--paths", nargs="*", default=DEFAULT_TARGETS,
                        help="扫描目标目录/文件（默认: %(default)s）")
    parser.add_argument("--max-report", type=int, default=40,
                        help="最多打印的明细条数（默认 %(default)s）")
    args = parser.parse_args(argv)

    targets = [Path(t).resolve() for t in args.paths]
    for t in targets:
        if not t.exists():
            print(f"[garbled-check] 扫描目标不存在: {t}", file=sys.stderr)
            return 2

    blocked: list[str] = []
    baselined: list[str] = []
    scanned = 0
    for target in targets:
        for f in _iter_source_files(target):
            scanned += 1
            for rel, no, msg in scan_file(f):
                entry = f"{rel}:{no}: {msg}"
                if no in KNOWN_GARBLED_BASELINE.get(rel, frozenset()):
                    baselined.append(entry)
                else:
                    blocked.append(entry)

    if baselined:
        print(f"[garbled-check] 存量基线豁免 {len(baselined)} 处（已登记，修复后请删除基线项）：")
        for item in baselined[: args.max_report]:
            print(f"  [baseline] {item}")
    if blocked:
        print(f"[garbled-check] 扫描 {scanned} 个文件，基线外发现 {len(blocked)} 处乱码：")
        for item in blocked[: args.max_report]:
            print(f"  {item}")
        if len(blocked) > args.max_report:
            print(f"  ...另有 {len(blocked) - args.max_report} 处未打印")
        print("[garbled-check] 结论: FAIL（请修复上述乱码或将确属误报的项加入基线）")
        return 1
    print(f"[garbled-check] 扫描 {scanned} 个文件，基线外未发现乱码。结论: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
