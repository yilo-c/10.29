"""扫描仓库中可能存在的密钥泄露。

CI 调用：返回非零退出码即视为构建失败。

扫描范围：
- 默认只扫描 git 跟踪的文件（CI checkout 等价）。
- 传 --all 扫描整个工作区（含未跟踪文件，用于本地审计）。

扫描模式：
- ark-[a-f0-9]{8}-      （火山引擎 API Key）
- sk-[a-f0-9]{20,}      （OpenAI / DeepSeek 兼容）
- xoxb-[0-9-]{20,}      （Slack Bot Token）
- xoxp-[0-9-]{20,}      （Slack User Token）
- ghp_[A-Za-z0-9]{30,}  （GitHub Personal Access Token）
- gho_[A-Za-z0-9]{30,}  （GitHub OAuth Token）
- AIza[A-Za-z0-9_\\-]{30,}（Google API Key）
- UinDoS-[A-Za-z0-9\\-]{20,}（思忆集 automation_hub token）

排除目录见 EXCLUDE_DIRS；测试中明显虚构的 fake key 通过 ALLOWLIST 放行。
"""
from __future__ import annotations

import re
import subprocess
import sys

# [stdio-utf8-guard] 中文 Windows 控制台(cp1252)下 print 中文会 UnicodeEncodeError 并退出 1，
# 导致 CI quality-gates 中断、后续步骤全部 skipped。详见 scripts/_stdio.py。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

from pathlib import Path

PATTERNS = [
    re.compile(r"ark-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xox[bp]-[0-9A-Za-z-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"gho_[A-Za-z0-9]{30,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),
    re.compile(r"UinDoS-[A-Za-z0-9\-]{20,}"),
]

EXCLUDE_DIRS = {
    ".git",
    ".venv3.11",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "ollama_models",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tools",
    "siyi_collection.egg-info",
    "siyiji_app.egg-info",
}

# 允许进入扫描结果但视为合规的字符串（明显虚构、测试用）
ALLOWLIST = [
    "sk-fakefakefakefakefakefakefakefa12",
    "sk-your-api-key-here",
    "sk-your-fallback-key",
    "ghp_your_token_here",
    # 2026-10-31：test_preview_redact_p1c.py 的脱敏测试样本。
    # 该文件测「预览参数脱敏」，密钥值必须形似真密钥才测得到 mask 逻辑；
    # 均为人工虚构，非真实凭据。
    # 事故：CI run 41136713073 的 quality-gates 在该文件 6 处命中而红。
    "sk-9b87c1def1ff4472a38be5e0e219c0c0",
    "sk-abcdefghijklmnop1111",
    "sk-abcdefghijklmnop1234",
]

# 允许扫描的扩展名
SCAN_SUFFIXES = {
    ".py", ".pyw",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".kt", ".kts", ".java",
    ".json", ".yaml", ".yml", ".toml",
    ".md", ".markdown",
    ".env", ".env.example", ".env.local",
    ".sh", ".bash", ".ps1", ".cmd", ".bat",
    ".html", ".htm",
    ".xml",
    ".bak",
}


def scan_file(path: Path) -> list[tuple[int, str]]:
    """返回 (行号, 命中文本片段) 列表。"""
    findings: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings

    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern in PATTERNS:
            for match in pattern.finditer(line):
                hit = match.group(0)
                if hit in ALLOWLIST:
                    continue
                # 上下文允许包含明显的占位符注释
                context = line.strip()
                if "fakefakefake" in hit or "your-api-key" in hit:
                    continue
                findings.append((lineno, f"{hit}  →  {context[:120]}"))
    return findings


def should_scan(path: Path, repo_root: Path) -> bool:
    rel = path.relative_to(repo_root)
    parts = rel.parts
    if any(part in EXCLUDE_DIRS for part in parts):
        return False
    # 跳过备份目录中的 settings 文件（含历史快照，不应当作泄露源）
    rel_str = str(rel).replace("\\", "/")
    if "data_backup_" in rel_str or "data/backups/" in rel_str:
        return False
    suffix = path.suffix.lower()
    if suffix in SCAN_SUFFIXES:
        return True
    # 处理 .env / .env.example 这类没有 suffix 的特殊文件
    if path.name in {".env", ".env.example", ".env.local", ".env.production"}:
        return True
    return False


def _git_tracked_files(repo_root: Path) -> list[Path] | None:
    """返回 git 跟踪的文件列表；非 git 仓库或 git 不可用时返回 None。"""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    files: list[Path] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            files.append(repo_root / line)
    return files


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    total = 0
    findings_by_file: dict[Path, list[tuple[int, str]]] = {}

    # 默认只扫描 git 跟踪的文件（等价于 CI checkout 的内容）。
    # 传 --all 时扫描整个工作区（含未跟踪文件，用于本地审计）。
    scan_all = "--all" in sys.argv[1:]
    if scan_all:
        candidates = (p for p in repo_root.rglob("*") if p.is_file())
    else:
        tracked = _git_tracked_files(repo_root)
        if tracked is None:
            # 非 git 环境回退到全量扫描
            candidates = (p for p in repo_root.rglob("*") if p.is_file())
        else:
            candidates = (p for p in tracked if p.is_file())

    for path in candidates:
        if not should_scan(path, repo_root):
            continue
        hits = scan_file(path)
        if hits:
            findings_by_file[path] = hits
            total += len(hits)

    if findings_by_file:
        print("[scan_secrets] FAIL — 发现疑似密钥泄露：\n")
        for path, hits in sorted(findings_by_file.items()):
            rel = path.relative_to(repo_root)
            for lineno, text in hits:
                print(f"  {rel}:{lineno}  {text}")
        print(f"\n共 {total} 处命中，跨 {len(findings_by_file)} 个文件。")
        print("\n修复指引：参见 docs/security/secret-management.md §3 应急流程。")
        return 1

    print("[scan_secrets] OK — 未发现密钥泄露。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
