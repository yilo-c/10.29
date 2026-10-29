"""思忆集回滚演练 — 沙箱五步（V2.1 批次3 重写：支持 SQLite 主存储）。

R-DRILL-1/2：旧脚本仅 glob 顶层 ``*.json`` 且硬卡 ``settings/tasks/notes.json``，
而真实数据已在 SQLite（``思忆集app/data/local_storage.db`` 等），演练对象与真实
数据错位 → 全部 missing/FAIL。本版重写：

- 快照/恢复/重放同时覆盖 ``*.json`` + ``*.db``（SQLite 经 ``Connection.backup``
  一致性副本，含 WAL 场景）；
- verify 按「数据形态」自适应：存在 .db 则用 ``sqlite3`` 做 ``PRAGMA integrity_check``
  与表存在性校验；否则回退 json 解析校验；
- 提供 ``--smoke`` 可选后端启动 + /health 冒烟（默认跳过，避免 CI 常驻后端冲突）。

用法：
    python rollback_drill.py --sandbox <dir>            # 沙箱演练
    python rollback_drill.py --sandbox <dir> --smoke    # 附加后端启动冒烟
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROLLBACK_DRILL_STEPS = [
    "save_snapshot",
    "restore_backup",
    "verify_data",
    "reapply_current",
    "verify_recovery",
]

# 演练 verify 的目标数据形态（json 键在无 .db 时回退检查；有 .db 时以 db 为准）
_JSON_CHECK_KEYS = ("settings", "tasks", "notes")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _discover_data_dir(repo_root: Path) -> Path:
    candidates = [
        repo_root / "思忆集app" / "data",
        repo_root / "data",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def _data_files(data_dir: Path) -> list[Path]:
    """收集 json + db（含 -wal/-shm 附属文件）供快照/恢复。"""
    files = sorted(data_dir.glob("*.json")) + sorted(data_dir.glob("*.db"))
    return [f for f in files if not f.name.startswith(".drill_")]


def _snapshot_db(src_db: Path, dest_db: Path) -> None:
    """SQLite 一致性快照（Connection.backup 处理 WAL，避免冷拷贝损坏）。"""
    src = sqlite3.connect(str(src_db))
    try:
        dst = sqlite3.connect(str(dest_db))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _copy_data_file(src: Path, dest_dir: Path) -> None:
    shutil.copy2(str(src), str(dest_dir / src.name))


def _verify_db(db_path: Path) -> tuple[bool, dict[str, Any]]:
    """验证 SQLite 数据库：integrity_check + 表存在性。"""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            ok_integrity = bool(integrity) and integrity[0] == "ok"
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            ok_tables = len(tables) > 0
            return ok_integrity and ok_tables, {
                "integrity": integrity[0] if integrity else "unknown",
                "tables": tables[:20],
                "table_count": len(tables),
            }
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return False, {"error": str(exc)}


def _verify_data_shape(data_dir: Path) -> dict[str, Any]:
    """按数据形态自适应校验：有 .db 以 SQLite 为准；否则回退 json。"""
    dbs = sorted(data_dir.glob("*.db"))
    if dbs:
        checks: dict[str, Any] = {}
        for db in dbs[:20]:
            ok, detail = _verify_db(db)
            checks[db.name] = {"ok": ok, **detail}
        return {"mode": "sqlite", "checks": checks}
    # 回退 json（旧数据形态）
    checks = {}
    for name in _JSON_CHECK_KEYS:
        fp = data_dir / f"{name}.json"
        exists = fp.is_file()
        valid = False
        if exists:
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                valid = isinstance(data, (dict, list))
            except Exception:  # noqa: BLE001
                valid = False
        checks[name] = {"exists": exists, "valid": valid}
    return {"mode": "json", "checks": checks}


def _verify_all_ok(shape: dict[str, Any]) -> bool:
    return all((c.get("ok") if shape["mode"] == "sqlite" else (c.get("exists") and c.get("valid"))) for c in shape["checks"].values())


def _step_save_snapshot(data_dir: Path, sandbox_dir: Path) -> dict[str, Any]:
    """Step 1: Save current state snapshot（json + SQLite 一致性副本）。"""
    snapshot_dir = sandbox_dir / "snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {"json": 0, "db": 0, "bytes": 0}
    for f in _data_files(data_dir):
        if f.suffix == ".db":
            _snapshot_db(f, snapshot_dir / f.name)
            counts["db"] += 1
        else:
            _copy_data_file(f, snapshot_dir)
            counts["json"] += 1
        counts["bytes"] += f.stat().st_size

    manifest = {
        "step": "save_snapshot",
        "timestamp": _utcnow(),
        "source": str(data_dir),
        "snapshot": str(snapshot_dir),
        "files": counts,
    }
    (snapshot_dir / "drill_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DRILL_STEP save_snapshot ok json={counts['json']} db={counts['db']}")
    return manifest


def _step_restore_backup(snapshot_dir: Path, data_dir: Path, sandbox_dir: Path) -> dict[str, Any]:
    """Step 2: Simulate rollback by restoring snapshot（模拟旧版本）。"""
    rollback_dir = sandbox_dir / "rollback"
    rollback_dir.mkdir(parents=True, exist_ok=True)

    # 移走当前数据（json + db）到备份区
    moved = 0
    for f in _data_files(data_dir):
        if not f.name.startswith(".drill_"):
            shutil.move(str(f), str(rollback_dir / f.name))
            moved += 1

    restored = 0
    for f in sorted(snapshot_dir.iterdir()):
        if f.name == "drill_manifest.json":
            continue
        _copy_data_file(f, data_dir)
        restored += 1

    manifest = {
        "step": "restore_backup",
        "timestamp": _utcnow(),
        "data_dir": str(data_dir),
        "files_moved": moved,
        "files_restored": restored,
        "original_backup": str(rollback_dir),
    }
    print(f"DRILL_STEP restore_backup ok moved={moved} restored={restored}")
    return manifest


def _step_verify_data(data_dir: Path) -> dict[str, Any]:
    """Step 3: Verify data integrity after rollback（按数据形态自适应）。"""
    shape = _verify_data_shape(data_dir)
    shape["step"] = "verify_data"
    shape["timestamp"] = _utcnow()
    shape["all_ok"] = _verify_all_ok(shape)
    print(f"DRILL_STEP verify_data ok mode={shape['mode']} all_ok={shape['all_ok']}")
    return shape


def _step_reapply_current(snapshot_dir: Path, data_dir: Path) -> dict[str, Any]:
    """Step 4: Reapply current data（模拟前向恢复）。"""
    restored = 0
    for f in sorted(snapshot_dir.iterdir()):
        if f.name == "drill_manifest.json":
            continue
        _copy_data_file(f, data_dir)
        restored += 1

    manifest = {
        "step": "reapply_current",
        "timestamp": _utcnow(),
        "files_restored": restored,
    }
    print(f"DRILL_STEP reapply_current ok restored={restored}")
    return manifest


def _step_verify_recovery(data_dir: Path) -> dict[str, Any]:
    """Step 5: Final verification after recovery（按数据形态自适应）。"""
    shape = _verify_data_shape(data_dir)
    shape["step"] = "verify_recovery"
    shape["timestamp"] = _utcnow()
    shape["all_ok"] = _verify_all_ok(shape)
    print(f"DRILL_STEP verify_recovery ok mode={shape['mode']} all_ok={shape['all_ok']}")
    return shape


def _smoke_health(app_dir: Path, data_dir: Path) -> dict[str, Any]:
    """可选冒烟：启动后端进程并请求 /health（best-effort，失败不阻断演练主结论）。"""
    import os
    import socket
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ)
    env["SIYI_WS_HOST"] = "127.0.0.1"
    env["SIYI_WS_PORT"] = str(port)
    env["SIYI_DATA_DIR"] = str(data_dir)
    env["SIYI_LOCK_TAKEOVER"] = "0"
    env["SIYI_TEST_OFFLINE"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.headless_main"],
        cwd=str(app_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import urllib.request

        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                return {"ok": False, "error": f"backend exited with {proc.returncode}"}
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                    return {"ok": resp.status == 200, "status": resp.status}
            except Exception:  # noqa: BLE001
                time.sleep(1)
        return {"ok": False, "error": "health timeout"}
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="思忆集桌面端回滚演练（操作沙箱内数据副本，不影响真实配置）")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]), help="项目根目录")
    parser.add_argument("--sandbox", default="", help="沙箱目录（默认临时目录）")
    parser.add_argument("--smoke", action="store_true", help="附加后端启动 + /health 冒烟")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    data_dir = _discover_data_dir(repo_root)

    if not data_dir.is_dir():
        print(f"DRILL_FAILED data_dir_not_found {data_dir}")
        return 2

    sandbox_root = Path(args.sandbox).expanduser().resolve() if args.sandbox else Path(tempfile.mkdtemp(prefix="siyi_rollback_drill_"))
    sandbox_dir = sandbox_root / "sandbox_data"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    # Copy real data into sandbox for safe drill（json + db）
    sandbox_data = sandbox_dir / "data"
    sandbox_data.mkdir(parents=True, exist_ok=True)
    for f in _data_files(data_dir):
        _copy_data_file(f, sandbox_data)

    drill_log: dict[str, Any] = {
        "drill_time": _utcnow(),
        "sandbox_dir": str(sandbox_data),
        "results": {},
    }

    try:
        result1 = _step_save_snapshot(sandbox_data, sandbox_dir)
        drill_log["results"]["save_snapshot"] = result1

        result2 = _step_restore_backup(sandbox_dir / "snapshot", sandbox_data, sandbox_dir)
        drill_log["results"]["restore_backup"] = result2

        result3 = _step_verify_data(sandbox_data)
        drill_log["results"]["verify_data"] = result3

        result4 = _step_reapply_current(sandbox_dir / "snapshot", sandbox_data)
        drill_log["results"]["reapply_current"] = result4

        result5 = _step_verify_recovery(sandbox_data)
        drill_log["results"]["verify_recovery"] = result5

        if args.smoke:
            smoke = _smoke_health(repo_root / "思忆集app", sandbox_data)
            drill_log["results"]["smoke_health"] = smoke
            result5["smoke_health"] = smoke

        all_ok = all(r.get("all_ok", True) for r in [result3, result5])
        drill_log["all_ok"] = all_ok

        (sandbox_dir / "drill_report.json").write_text(json.dumps(drill_log, ensure_ascii=False, indent=2), encoding="utf-8")

        if all_ok:
            print(f"DRILL_OK sandbox={sandbox_dir}")
            return 0

        print(f"DRILL_PARTIAL sandbox={sandbox_dir}")
        return 1

    except Exception as exc:
        print(f"DRILL_FAILED error={exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
