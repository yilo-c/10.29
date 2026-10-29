"""七维-数C8：DR 演练固化 — hermetic 测试（pytest tmp_path 沙箱）。

被测对象：仓库根 scripts/dr_drill.py（DR-1 全新机还原 / DR-2 单库损坏 /
DR-3 audit.rollback 单条档）。仓库既有先例是「把脚本逻辑做成可 import
函数」（仓库根 tests/test_legacy_venv_guard.py 直接 import
scripts.check_legacy_venv_refs）；本测试按同一思路以 importlib 按文件
路径加载脚本模块——不把仓库根注入 sys.path，避免仓库根 tests 命名空间
遮蔽 思忆集app/tests。

铁律：全流程仅操作 pytest tmp_path（TEMP），绝不触碰 思忆集app/data/ 真实
数据；SIYI_DATA_DIR 由被测脚本内部覆盖并在 finally 恢复。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "dr_drill.py"


@pytest.fixture(scope="module")
def drill() -> object:
    """按文件路径加载 scripts/dr_drill.py 为可 import 模块。"""
    spec = importlib.util.spec_from_file_location("dr_drill_script", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("dr_drill_script", module)
    spec.loader.exec_module(module)
    return module


def _dump(report: object) -> str:
    try:
        return json.dumps(report, ensure_ascii=False)[:2000]
    except (TypeError, ValueError):
        return str(report)[:2000]


# ── DR-1：全新机还原（备份→清空→还原→校验 RestoreResult/checksum/条数）──


def test_dr1_backup_wipe_restore_roundtrip(tmp_path: Path, drill) -> None:
    report = drill.run_dr1(tmp_path)
    assert report["ok"], _dump(report)

    steps = report["steps"]
    backup = steps["backup"]
    assert backup["success"] is True and backup["backup_id"]
    assert backup["checksum"]
    assert backup["note_count"] > 0

    wipe = steps["wipe"]
    assert wipe["removed_files"] >= 1, "清空步骤应至少移除 SQLite 主库文件"
    assert wipe["db_gone"] is True

    restore = steps["restore"]
    assert restore["success"] is True, restore.get("error")
    # RestoreResult.checksum 必须与备份时内容校验和一致
    assert restore["checksum"] == backup["checksum"]
    assert restore["restored_items"]["notes"] == backup["note_count"]
    assert restore["restored_items"]["tasks"] == backup["task_count"]
    assert restore["restored_items"]["memories"] == backup["memory_file_count"]
    assert restore["restored_items"]["settings"] == 1

    verify = steps["verify"]
    # 条数断言：kv_store 行数（键数）恢复前后一致
    assert verify["row_count_after"] == verify["row_count_before"]
    assert verify["notes_match"] is True
    assert verify["tasks_match"] is True
    assert verify["settings_match"] is True
    assert verify["memories_count_match"] is True
    assert verify["state_keys_restored"] is True


def test_dr1_restore_rejects_tampered_backup(tmp_path: Path, drill) -> None:
    """备份产物被篡改后，恢复必须被 checksum 门禁拦截（演练的负向分支）。"""
    report = drill.run_dr1_tamper_guard(tmp_path)
    assert report["ok"], _dump(report)

    steps = report["steps"]
    assert steps["tamper"]["tampered"] is True
    restore = steps["restore"]
    assert restore["success"] is False, "被篡改备份必须恢复失败"
    assert "校验和" in (restore.get("error") or "")
    assert steps["verify"]["rejected"] is True


# ── DR-2：单库损坏（注入垃圾字节→integrity 检查发现→快照回灌→row_count 断言）──


def test_dr2_corruption_detected_snapshot_restored(tmp_path: Path, drill) -> None:
    report = drill.run_dr2(tmp_path)
    assert report["ok"], _dump(report)

    steps = report["steps"]
    assert steps["baseline"]["integrity_ok"] is True
    assert steps["corrupt"]["bytes_written"] > 0

    # integrity 检查必须发现损坏（integrity_check 返回非 ok 或抛 DatabaseError）
    detect = steps["detect"]
    assert detect["integrity_ok"] is False, _dump(detect)
    assert detect["detail"]

    restore = steps["restore"]
    assert restore["integrity_ok"] is True, "快照回灌后 integrity 必须恢复 ok"

    verify = steps["verify"]
    assert verify["row_count_after"] == verify["row_count_before"]
    assert verify["probe_key_match"] is True


# ── DR-3：audit.rollback 单条档 ──


def test_dr3_audit_rollback_restores_deleted_note(tmp_path: Path, drill) -> None:
    report = drill.run_dr3(tmp_path)
    assert report["ok"], _dump(report)

    steps = report["steps"]
    assert steps["seed"]["note_id"]

    delete = steps["delete"]
    assert delete["gone"] is True, "硬删除后 get_note 应返回 None"

    can = steps["can_rollback"]
    assert can["can_rollback"] is True, can.get("reason")

    rollback = steps["rollback"]
    assert rollback["rolled_back"] is True, rollback.get("reason")

    verify = steps["verify"]
    assert verify["note_restored"] is True
    assert verify["content_match"] is True


# ── 横切约定 ──


def test_drill_restores_siyyi_data_dir_env(tmp_path: Path, drill, monkeypatch) -> None:
    """演练必须把 SIYI_DATA_DIR 覆盖限制在 try/finally 内（不污染进程环境）。"""
    sentinel = str(tmp_path / "pre-existing-sentinel")
    monkeypatch.setenv("SIYI_DATA_DIR", sentinel)
    report = drill.run_dr3(tmp_path)
    assert report["ok"], _dump(report)
    assert os.environ.get("SIYI_DATA_DIR") == sentinel


def test_full_drill_main_end_to_end(tmp_path: Path, drill) -> None:
    """CLI 全量演练：--sandbox 指定沙箱，退出码 0，报告 JSON 落盘且 all_ok。"""
    sandbox = tmp_path / "dr-sandbox"
    exit_code = drill.main(["--sandbox", str(sandbox)])
    assert exit_code == 0

    report_file = sandbox / "drill_report.json"
    assert report_file.is_file()
    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        pytest.fail("drill_report.json 不可解析: " + str(exc))
    assert data["all_ok"] is True
    assert set(data["results"].keys()) == {"dr1", "dr1_tamper_guard", "dr2", "dr3"}
