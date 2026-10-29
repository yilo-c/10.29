from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


TEXT_FILE_SUFFIXES = {
    ".md",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".txt",
    ".ps1",
    ".bat",
    ".toml",
}

EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    ".venv",
    ".venv3.11",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
}

LEGACY_PATTERNS = {
    "legacy_python_path": re.compile(r"(?<![\w.])\.venv/scripts/python\.exe", re.IGNORECASE),
    "legacy_launch_label": re.compile(r'"label"\s*:\s*"[^"]*\(\.venv\)[^"]*"', re.IGNORECASE),
}


@dataclass(frozen=True)
class LegacyVenvReference:
    file_path: Path
    line_number: int
    pattern_name: str
    line_text: str


def should_scan_file(file_path: Path) -> bool:
    if file_path.name.startswith("test_legacy_venv_guard"):
        return False
    return file_path.suffix.lower() in TEXT_FILE_SUFFIXES


def iter_candidate_files(root: Path):
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in file_path.parts):
            continue
        if should_scan_file(file_path):
            yield file_path


def match_legacy_pattern(line_text: str) -> str | None:
    normalized = line_text.replace("\\", "/")
    for pattern_name, pattern in LEGACY_PATTERNS.items():
        if pattern.search(normalized):
            return pattern_name
    return None


def find_legacy_refs(root: Path) -> list[LegacyVenvReference]:
    findings: list[LegacyVenvReference] = []
    for file_path in iter_candidate_files(root):
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line_text in enumerate(content.splitlines(), start=1):
            pattern_name = match_legacy_pattern(line_text)
            if pattern_name is None:
                continue
            findings.append(
                LegacyVenvReference(
                    file_path=file_path,
                    line_number=line_number,
                    pattern_name=pattern_name,
                    line_text=line_text.strip(),
                )
            )
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan repository text files for legacy .venv interpreter references.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to scan. Defaults to the 思忆集 repo root.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    findings = find_legacy_refs(root)

    if not findings:
        print(f"LEGACY_VENV_GUARD_OK scanned={root}")
        return 0

    print(f"LEGACY_VENV_GUARD_FAIL scanned={root} findings={len(findings)}")
    for finding in findings:
        relative_path = finding.file_path.relative_to(root).as_posix()
        print(f"{relative_path}:{finding.line_number}: {finding.pattern_name}: {finding.line_text}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())