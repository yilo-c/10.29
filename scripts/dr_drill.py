#!/usr/bin/env python3
"""七维-数C8：DR 演练固化脚本（P0，2026-08-29）。

在 TEMP 沙箱中演练三条灾备路径（规格：deliverables/
seven-dimension-risk-assessment-2026-08-29.md §2.3 C8 / §2.4）：

- DR-1 全新机还原：SIYI_DATA_DIR 环境变量覆盖 + BackupService 备份 ->
  清空数据目录 -> 按 backup_id 还原 -> 校验 RestoreResult/checksum/条数。
- DR-1 负向分支（篡改防护）：备份产物被改写后恢复必须被 checksum 门禁拦截。
- DR-2 单库损坏：SQLite 主库注入垃圾字节（中部覆写 + 尾部追加）->
  PRAGMA integrity_check 必须发现 -> 快照回灌 -> row_count 断言。
- DR-3 误删单条档：audit.rollback（AuditTrail + RollbackManager）把硬删除
  的笔记按 before_snapshot 恢复。

约定：
- 所有操作仅发生在 --sandbox 指定（或系统 TEMP）目录内，绝不触碰真实数据；
  SIYI_DATA_DIR 覆盖用 try/finally 语义恢复（_DataDirEnv）。
- 备份目录固定放在沙箱数据目录之外（sandbox/backups），清空数据不会误删备份。
- 退出码：0=DRILL_OK / 1=DRILL_PARTIAL / 3=DRILL_FAILED（与
  scripts/rollback_drill.py 同约定）；报告落盘 <sandbox>/drill_report.json。

用法：
    python scripts/dr_drill.py [--drill dr1|dr2|dr3|all] [--sandbox <dir>]
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import shutil
import sqlite3
import sys

# [stdio-utf8-guard] 中文 Windows 控制台(cp1252)下 print 中文会 UnicodeEncodeError 并退出 1，
# 导致 CI quality-gates 中断、后续步骤全部 skipped。详见 scripts/_stdio.py。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _REPO_ROOT / "思忆集app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from core.audit_rollback import AuditTrail, RollbackManager  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402
from repositories._note import LocalPersonalNoteRepository  # noqa: E402
from services.backup_service import BackupConfig, BackupService  # noqa: E402

logger = logging.getLogger("siyi.dr.drill")

DRILL_OK = 0
DRILL_PARTIAL = 1
DRILL_FAILED = 3

ENV_DATA_DIR = "SIYI_DATA_DIR"
_SQLITE_SIDECARS = ("-wal", "-shm")


class _DataDirEnv:
    """把 SIYI_DATA_DIR 覆盖到沙箱数据目录；退出时恢复原值（try/finally 语义）。"""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = str(data_dir)
        self._prev: str | None = None

    def __enter__(self) -> str:
        self._prev = os.environ.get(ENV_DATA_DIR)
        os.environ[ENV_DATA_DIR] = self._data_dir
        return self._data_dir

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self._prev is None:
            os.environ.pop(ENV_DATA_DIR, None)
        else:
            os.environ[ENV_DATA_DIR] = self._prev
        return False


# ── 通用辅助 ──────────────────────────────────────────────────────────


def _seed_sandbox(store: LocalDataStore, data_dir: Path) -> dict[str, Any]:
    """注入确定性种子数据：3 个统一存储键域 + 2 个语义状态键 + 2 个记忆文件。"""
    notes = [
        {
            "id": "drill-note-1",
            "title": "DR 演练笔记一",
            "content": "DR-1 全新机还原种子内容 A",
            "folder": "dr-drill",
        },
        {
            "id": "drill-note-2",
            "title": "DR 演练笔记二",
            "content": "DR-1 全新机还原种子内容 B",
            "folder": "dr-drill",
        },
        {
            "id": "drill-note-3",
            "title": "DR 演练笔记三",
            "content": "DR-1 全新机还原种子内容 C",
            "folder": "dr-drill",
        },
    ]
    tasks = [{"id": "drill-task-1", "title": "DR 演练任务", "completed": False}]
    # 注意：不得包含 api_key/token/secret/password 等敏感键名——
    # 备份会递归剥离（_strip_sensitive_settings），会导致 settings 不等。
    settings = {"theme": "dr-drill", "drill_marker": "dr-seed"}
    memory_index = {
        "entries": [{"id": "drill-idx-1", "text": "DR 演练语义记忆条目"}],
        "updated_at": "2026-08-29T00:00:00+00:00",
    }
    decisions = [{"id": "drill-dec-1", "summary": "DR 演练决策记录"}]

    # 数C1 备份集会把全部扩展键域收进备份（load_json(key, None) 对注册键
    # 兜底返回 []，非 None 即入选），恢复时逐一 save_json 物化写回 kv_store。
    # 种子阶段必须预物化同一键集，否则恢复后 kv_store 会多出 8 行"空默认
    # 物化"行，row_count 前后断言失真（5 -> 13）。其中 inbox/words 放真实
    # 内容，让扩展域也参与内容级 roundtrip 校验。
    extended: dict[str, Any] = {
        "inbox_entries": [{"id": "drill-inbox-1", "content": "DR 演练收件箱条目"}],
        "words": [{"id": "drill-word-1", "word": "drill", "definition": "演练"}],
        "wordbooks": [],
        "translations": [],
        "structured_memory_entries": [],
        "audit_trail": [],
        "cron_jobs": [],
        "chat_sessions": [],
    }

    store.save_json("notes", notes)
    store.save_json("tasks", tasks)
    store.save_json("settings", settings)
    store.save_json("assistant_memory_index", memory_index)
    store.save_json("assistant_decisions", decisions)
    for _ext_key, _ext_payload in extended.items():
        store.save_json(_ext_key, _ext_payload)

    memories_dir = data_dir / "memories"
    memories_dir.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        mem = {
            "id": f"drill-mem-{i}",
            "content": f"DR 演练记忆内容 {i}",
            "created_at": "2026-08-29T00:00:00+00:00",
        }
        with open(memories_dir / f"drill_memory_{i}.json", "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)

    return {
        "notes": notes,
        "tasks": tasks,
        "settings": settings,
        "memory_index": memory_index,
        "decisions": decisions,
        "memory_files": 2,
        "extended": extended,
    }


def _make_backup_service(data_dir: Path, data_store: LocalDataStore | None) -> tuple[BackupService, Path]:
    """BackupService + 沙箱外备份目录（sandbox/backups）。"""
    backup_dir = data_dir.parent / "backups"
    config = BackupConfig(backup_dir=backup_dir, max_backups=5)
    service = BackupService(data_dir=str(data_dir), config=config, data_store=data_store)
    return service, backup_dir


def _new_store() -> LocalDataStore:
    """经 SIYI_DATA_DIR 环境变量解析沙箱数据目录（演练的核心覆盖路径）。"""
    return LocalDataStore()


def _row_count(store: LocalDataStore) -> int:
    return int(store._sqlite.row_count())


def _wipe_dir(data_dir: Path) -> int:
    """清空数据目录全部内容（备份目录在沙箱数据目录之外，不受影响）。"""
    removed = 0
    for entry in data_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        removed += 1
    return removed


def _remove_sidecars(db_path: Path) -> int:
    """删除 -wal/-shm 边车文件（须在连接全部关闭后调用）。"""
    removed = 0
    for suffix in _SQLITE_SIDECARS:
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
            removed += 1
    return removed


def _integrity_check(db_path: Path) -> tuple[bool, str]:
    """PRAGMA integrity_check；文件级损坏可能以 DatabaseError 形式暴露。"""
    if not db_path.exists():
        return False, "db file missing"
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.DatabaseError as exc:
        return False, "connect failed: " + str(exc)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        return False, "integrity_check raised: " + str(exc)
    finally:
        conn.close()
    bad = [str(r[0]) for r in rows if str(r[0]) != "ok"]
    if bad:
        return False, "; ".join(bad[:5])
    return True, "ok"


def _corrupt_db(db_path: Path) -> int:
    """注入损坏：中部覆写 + 尾部追加垃圾字节（追加 alone 不触发 integrity 告警）。"""
    size = db_path.stat().st_size
    chunk = os.urandom(256)
    with open(db_path, "r+b") as f:
        f.seek(max(0, size // 2))
        f.write(chunk)
    with open(db_path, "ab") as f:
        f.write(os.urandom(256))
    return len(chunk) * 2


def _default_sandbox() -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return Path(tempfile.gettempdir()) / f"siyi_dr_drill_{stamp}_{os.getpid()}"


# ── DR-1：全新机还原 ──────────────────────────────────────────────────


def run_dr1(sandbox: Path) -> dict[str, Any]:
    """备份 -> 清空数据目录 -> 按 backup_id 还原 -> checksum/条数/行数断言。"""
    sandbox = Path(sandbox)
    data_dir = sandbox / "data"
    steps: dict[str, Any] = {}
    ok = True
    try:
        with _DataDirEnv(data_dir):
            store = _new_store()
            seeds = _seed_sandbox(store, data_dir)
            service, backup_dir = _make_backup_service(data_dir, store)
            result = asyncio.run(service.create_backup())
            if not result.success or result.metadata is None:
                raise RuntimeError("create_backup failed: " + str(result.error))
            metadata = result.metadata
            steps["backup"] = {
                "success": True,
                "backup_id": result.backup_id,
                "checksum": metadata.checksum,
                "file_path": metadata.file_path,
                "note_count": len(seeds["notes"]),
                "task_count": len(seeds["tasks"]),
                "memory_file_count": seeds["memory_files"],
                "row_count_before": _row_count(store),
            }
            row_before = _row_count(store)

            store.close()
            removed = _wipe_dir(data_dir)
            db_gone = not (data_dir / LocalDataStore.DB_FILENAME).exists()
            steps["wipe"] = {"removed_files": removed, "db_gone": db_gone}

            store2 = _new_store()
            service2, _ = _make_backup_service(data_dir, store2)
            rr = asyncio.run(service2.restore_backup(backup_id=result.backup_id))
            restored_items = dict(rr.restored_items or {})
            steps["restore"] = {
                "success": bool(rr.success),
                "checksum": rr.checksum or "",
                "restored_items": restored_items,
                "error": rr.error or "",
            }

            notes_after = store2.load_json("notes", [])
            tasks_after = store2.load_json("tasks", [])
            settings_after = store2.load_json("settings", {})
            mem_dir = data_dir / "memories"
            mem_files = len(list(mem_dir.glob("*.json"))) if mem_dir.exists() else 0
            extended_after = {key: store2.load_json(key, None) for key in seeds["extended"]}
            verify = {
                "checksum_match": (rr.checksum or "") == metadata.checksum,
                "row_count_before": row_before,
                "row_count_after": _row_count(store2),
                "notes_match": notes_after == seeds["notes"],
                "tasks_match": tasks_after == seeds["tasks"],
                "settings_match": settings_after == seeds["settings"],
                "memories_count_match": mem_files == seeds["memory_files"],
                "extended_match": extended_after == seeds["extended"],
                "state_keys_restored": (
                    "assistant_memory_index" in restored_items
                    and "assistant_decisions" in restored_items
                    and store2.load_json("assistant_memory_index", {}) == seeds["memory_index"]
                    and store2.load_json("assistant_decisions", []) == seeds["decisions"]
                ),
            }
            steps["verify"] = verify
            ok = bool(
                rr.success
                and verify["checksum_match"]
                and db_gone
                and verify["row_count_after"] == row_before
                and verify["notes_match"]
                and verify["tasks_match"]
                and verify["settings_match"]
                and verify["memories_count_match"]
                and verify["extended_match"]
                and verify["state_keys_restored"]
            )
            store2.close()
    except Exception as exc:  # noqa: BLE001 — 演练脚本兜底，失败细节进报告
        logger.exception("DR-1 failed")
        steps["error"] = str(exc)
        ok = False
    return {"ok": ok, "steps": steps}


def run_dr1_tamper_guard(sandbox: Path) -> dict[str, Any]:
    """负向分支：改写备份产物后，恢复必须被 checksum 门禁拦截。"""
    sandbox = Path(sandbox)
    data_dir = sandbox / "data"
    steps: dict[str, Any] = {}
    ok = True
    try:
        with _DataDirEnv(data_dir):
            store = _new_store()
            _seed_sandbox(store, data_dir)
            service, _ = _make_backup_service(data_dir, store)
            result = asyncio.run(service.create_backup())
            if not result.success or result.metadata is None:
                raise RuntimeError("create_backup failed: " + str(result.error))

            archive = Path(result.metadata.file_path)
            with gzip.open(archive, "rt", encoding="utf-8") as f:
                raw = f.read()
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError("备份产物不是合法 JSON: " + str(exc)) from exc
            notes = doc.get("data", {}).get("notes", [])
            if not notes:
                raise RuntimeError("备份产物缺少 notes，无法执行篡改")
            notes[0]["title"] = str(notes[0].get("title", "")) + "_tampered"
            with gzip.open(archive, "wt", encoding="utf-8") as f:
                f.write(json.dumps(doc, ensure_ascii=False))
            steps["tamper"] = {"tampered": True, "archive": str(archive)}

            rr = asyncio.run(service.restore_backup(backup_id=result.backup_id))
            error = rr.error or ""
            steps["restore"] = {"success": bool(rr.success), "error": error}
            rejected = (not rr.success) and ("校验和" in error)
            steps["verify"] = {"rejected": rejected}
            ok = rejected
            store.close()
    except Exception as exc:  # noqa: BLE001
        logger.exception("DR-1 tamper guard failed")
        steps["error"] = str(exc)
        ok = False
    return {"ok": ok, "steps": steps}


# ── DR-2：单库损坏 ────────────────────────────────────────────────────


def run_dr2(sandbox: Path) -> dict[str, Any]:
    """垃圾字节注入 -> integrity 检查发现 -> Connection.backup 快照回灌。"""
    sandbox = Path(sandbox)
    data_dir = sandbox / "data"
    db_path = data_dir / LocalDataStore.DB_FILENAME
    snapshot = sandbox / "snapshot_local_storage.db"
    steps: dict[str, Any] = {}
    ok = True
    try:
        with _DataDirEnv(data_dir):
            store = _new_store()
            seeds = _seed_sandbox(store, data_dir)
            row_before = _row_count(store)

            # 1) 把 WAL 刷进主库，再做物理快照
            checkpoint = sqlite3.connect(str(db_path))
            try:
                checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                checkpoint.close()
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(snapshot))
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()

            baseline_ok, baseline_detail = _integrity_check(db_path)
            steps["baseline"] = {"integrity_ok": baseline_ok, "detail": baseline_detail}

            store.close()
            _remove_sidecars(db_path)

            # 2) 注入损坏（中部覆写 + 尾部追加）
            bytes_written = _corrupt_db(db_path)
            steps["corrupt"] = {"bytes_written": bytes_written}

            # 3) integrity 检查必须发现损坏
            detected, detail = _integrity_check(db_path)
            steps["detect"] = {"integrity_ok": detected, "detail": detail}

            # 4) 快照回灌
            os.replace(snapshot, db_path)
            restored_ok, restored_detail = _integrity_check(db_path)
            steps["restore"] = {"integrity_ok": restored_ok, "detail": restored_detail}

            # 5) 行数与内容断言（全新 store 实例经 env 重新挂载）
            store2 = _new_store()
            notes_after = store2.load_json("notes", [])
            probe_match = bool(notes_after) and notes_after[0].get("title") == seeds["notes"][0]["title"]
            row_after = _row_count(store2)
            steps["verify"] = {
                "row_count_before": row_before,
                "row_count_after": row_after,
                "probe_key_match": probe_match,
            }
            ok = bool(baseline_ok and (not detected) and restored_ok and row_after == row_before and probe_match)
            store2.close()
    except Exception as exc:  # noqa: BLE001
        logger.exception("DR-2 failed")
        steps["error"] = str(exc)
        ok = False
    return {"ok": ok, "steps": steps}


# ── DR-3：audit.rollback 单条档 ───────────────────────────────────────


def run_dr3(sandbox: Path) -> dict[str, Any]:
    """误删单条笔记 -> 审计 before_snapshot -> audit.rollback 恢复同 id。"""
    sandbox = Path(sandbox)
    data_dir = sandbox / "data"
    steps: dict[str, Any] = {}
    ok = True
    try:
        with _DataDirEnv(data_dir):
            store = _new_store()
            notes = LocalPersonalNoteRepository(store)
            trail = AuditTrail(store, data_dir=str(sandbox / "audit"))
            manager = RollbackManager(store, trail, note_repository=notes)

            # 显式字符串 id：AuditTrail.record_write_intent 内部把 target_id
            # 强转 str，rollback 时 before_snapshot["id"] 会被覆写为该 str 值；
            # 若走 next_id 生成的 int id，恢复后 get_note(int) == str 比较
            # 永远失配（回滚假成功）。字符串 id 令 intent -> snapshot ->
            # add_note -> get_note 全链路类型一致（add_note 支持 desired_id）。
            note_id = notes.add_note(
                {
                    "id": "dr3-note-1",
                    "title": "DR-3 待误删笔记",
                    "content": "audit.rollback 单条档种子内容",
                    "folder": "dr-drill",
                }
            )
            before = notes.get_note(note_id)
            if before is None:
                raise RuntimeError("add_note 后 get_note 返回 None")
            steps["seed"] = {"note_id": note_id, "title": before.get("title")}

            entry_id = trail.record_write_intent(
                actor="dr-drill",
                action_id="dr3.note.delete",
                target_type="note",
                target_id=str(note_id),
                operation="delete",
                before_snapshot=before,
                metadata={"drill": "dr3"},
            )
            notes.delete_note(note_id, soft=False)
            gone = notes.get_note(note_id) is None
            trail.record_write_complete(entry_id, {"deleted": True, "note_id": str(note_id)})
            steps["delete"] = {"gone": gone}

            can = manager.can_rollback(entry_id)
            steps["can_rollback"] = {
                "can_rollback": bool(can.get("can_rollback")),
                "reason": can.get("reason", ""),
            }

            rb = manager.rollback(entry_id)
            steps["rollback"] = {
                "rolled_back": bool(rb.get("rolled_back")),
                "reason": rb.get("reason", ""),
            }

            restored = notes.get_note(note_id)
            verify = {
                "note_restored": restored is not None,
                "content_match": bool(restored) and restored.get("title") == before.get("title") and restored.get("content") == before.get("content"),
            }
            steps["verify"] = verify
            ok = bool(gone and can.get("can_rollback") and rb.get("rolled_back") and verify["note_restored"] and verify["content_match"])
            store.close()
    except Exception as exc:  # noqa: BLE001
        logger.exception("DR-3 failed")
        steps["error"] = str(exc)
        ok = False
    return {"ok": ok, "steps": steps}


# ── CLI ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """DR 演练：默认全量（DR-1/篡改防护/DR-2/DR-3），--drill 可选单项；报告落盘。

    退出码约定：0=DRILL_OK / 1=DRILL_PARTIAL / 3=DRILL_FAILED。
    """
    parser = argparse.ArgumentParser(description="思忆集 DR 演练（七维-数C8）")
    parser.add_argument(
        "--sandbox",
        default="",
        help="沙箱根目录（默认在系统 TEMP 下新建；所有操作仅限该目录内）",
    )
    parser.add_argument(
        "--drill",
        choices=("dr1", "dr2", "dr3", "all"),
        default="all",
        help="演练项：dr1=全新机还原（含篡改门禁负向分支）/ dr2=单库损坏 / dr3=audit.rollback / all=全部（默认）",
    )
    args = parser.parse_args(argv)

    sandbox = Path(args.sandbox) if args.sandbox else _default_sandbox()
    sandbox.mkdir(parents=True, exist_ok=True)

    selected: tuple[str, ...] = ("dr1", "dr2", "dr3") if args.drill == "all" else (args.drill,)
    results: dict[str, Any] = {}
    for name in selected:
        if name == "dr1":
            # 篡改门禁是 DR-1 checksum 防护的负向分支，随 dr1 一并演练
            results["dr1"] = run_dr1(sandbox / "dr1")
            results["dr1_tamper_guard"] = run_dr1_tamper_guard(sandbox / "dr1_tamper")
        elif name == "dr2":
            results["dr2"] = run_dr2(sandbox / "dr2")
        else:
            results["dr3"] = run_dr3(sandbox / "dr3")
    all_ok = all(item["ok"] for item in results.values())
    any_ok = any(item["ok"] for item in results.values())

    report = {
        "drill": "siyi-dr-c8",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sandbox": str(sandbox),
        "results": results,
        "all_ok": all_ok,
    }
    report_path = sandbox / "drill_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    for name, item in results.items():
        logger.info("DR %s: ok=%s", name, item["ok"])

    if all_ok:
        print("DRILL_OK sandbox=" + str(sandbox))
        code = DRILL_OK
    elif any_ok:
        print("DRILL_PARTIAL sandbox=" + str(sandbox))
        code = DRILL_PARTIAL
    else:
        print("DRILL_FAILED sandbox=" + str(sandbox))
        code = DRILL_FAILED
    print("report=" + str(report_path))
    return code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    sys.exit(main())
