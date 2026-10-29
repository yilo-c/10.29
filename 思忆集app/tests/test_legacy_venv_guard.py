import sys
from pathlib import Path

# scripts/ 包在仓库根（flat layout）；app 侧 sys.path 不含仓库根，迁移自根 tests/ 后在此补齐
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.check_legacy_venv_refs import find_legacy_refs, match_legacy_pattern


def test_match_legacy_pattern_detects_old_python_path():
    pattern_name = match_legacy_pattern("d:/repo/.venv/Scripts/python.exe 思忆集app/smoke_check.py")

    assert pattern_name == "legacy_python_path"


def test_match_legacy_pattern_detects_windows_backslash_path_case_insensitively():
    pattern_name = match_legacy_pattern(r"D:\repo\.VENV\Scripts\PYTHON.exe 思忆集app\smoke_check.py")

    assert pattern_name == "legacy_python_path"


def test_match_legacy_pattern_detects_old_launch_label_only_in_task_context():
    pattern_name = match_legacy_pattern('"label": "思忆集桌面应用 (.venv)"')

    assert pattern_name == "legacy_launch_label"


def test_match_legacy_pattern_ignores_plain_document_mention_of_dot_venv_label():
    pattern_name = match_legacy_pattern("历史上使用过的 (.venv) 虚拟环境已弃用")

    assert pattern_name is None


def test_match_legacy_pattern_ignores_repo_venv311_path():
    pattern_name = match_legacy_pattern("d:/repo/.venv3.11/Scripts/python.exe 思忆集app/smoke_check.py")

    assert pattern_name is None


def test_find_legacy_refs_skips_excluded_directories(tmp_path: Path):
    excluded_dir = tmp_path / ".venv3.11"
    excluded_dir.mkdir()
    (excluded_dir / "README.md").write_text("d:/repo/.venv/Scripts/python.exe", encoding="utf-8")

    findings = find_legacy_refs(tmp_path)

    assert findings == []


def test_find_legacy_refs_reports_markdown_reference(tmp_path: Path):
    doc_path = tmp_path / "README.md"
    doc_path.write_text("使用 d:/repo/.venv/Scripts/python.exe 运行\n", encoding="utf-8")

    findings = find_legacy_refs(tmp_path)

    assert len(findings) == 1
    assert findings[0].file_path == doc_path
    assert findings[0].line_number == 1
    assert findings[0].pattern_name == "legacy_python_path"