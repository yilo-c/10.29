"""BackupService 恢复校验和复验 + 往返测试（T1.5）。

覆盖：
- 备份→恢复往返成功，且 RestoreResult.checksum 与备份内嵌校验和一致；
- 备份文件被篡改（内容校验和不匹配）后恢复必须失败并给出明确错误；
- 不压缩（compress=False）路径下篡改同样被拦截。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from services.backup_service import BackupConfig, BackupService


def _seed_data(data_dir: Path) -> None:
    (data_dir / "notes.json").write_text(
        json.dumps([{"id": "n1", "title": "周复盘", "content": "这周状态不错"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "todos.json").write_text(
        json.dumps([{"id": "t1", "title": "背单词", "done": False}], ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "settings.json").write_text(
        json.dumps({"theme": "light"}, ensure_ascii=False), encoding="utf-8",
    )
    mem_dir = data_dir / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "memory_0.json").write_text(
        json.dumps({"id": "m1", "content": "开始系统复习英语"}, ensure_ascii=False), encoding="utf-8",
    )
    # T5.2：语义记忆状态（assistant_memory_index / assistant_decisions）
    (data_dir / "assistant_memory_index.json").write_text(
        json.dumps(
            {"built_at": "2026-08-23T00:00:00+00:00",
             "entries": [{"entry_id": "note:n9", "source_type": "note",
                          "source_id": "n9", "title": "语义记忆测试",
                          "content": "这是一条语义记忆", "created_at": "2026-08-23T00:00:00+00:00"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "assistant_decisions.json").write_text(
        json.dumps({"decisions": [{"id": "d1", "decision": "本月暂不扩张"}]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _make_service(data_dir: Path, compress: bool = False) -> BackupService:
    cfg = BackupConfig(compress=compress, max_backups=5)
    return BackupService(data_dir=str(data_dir), config=cfg, data_store=None)


@pytest.mark.asyncio
async def test_backup_restore_roundtrip_checksum(tmp_path: Path) -> None:
    _seed_data(tmp_path)
    svc = _make_service(tmp_path, compress=False)

    create_res = await svc.create_backup()
    assert create_res.success, create_res.error
    assert create_res.metadata is not None
    backup_id = create_res.backup_id
    backup_file = Path(create_res.file_path)

    # 篡改数据，再恢复应能把旧数据还原
    (tmp_path / "notes.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")

    restore_res = await svc.restore_backup(backup_id=backup_id)
    assert restore_res.success, restore_res.error
    assert restore_res.checksum == create_res.metadata.checksum
    assert restore_res.restored_items.get("notes") == 1

    # 数据确实被还原
    restored = json.loads((tmp_path / "notes.json").read_text(encoding="utf-8"))
    assert restored[0]["id"] == "n1"


@pytest.mark.asyncio
async def test_backup_restore_roundtrip_semantic_memory(tmp_path: Path) -> None:
    """T5.2：语义记忆状态（assistant_memory_index / assistant_decisions）应随备份恢复。

    反证：此前的备份集不含这两个键 -> 恢复后语义记忆永久丢失。
    本测试先篡改/清空，再恢复，断言语义记忆条目回到原位。
    """
    _seed_data(tmp_path)
    svc = _make_service(tmp_path, compress=False)

    create_res = await svc.create_backup()
    assert create_res.success, create_res.error
    assert create_res.metadata is not None
    backup_id = create_res.backup_id

    # 模拟"清空后丢失"：删除语义记忆状态文件
    (tmp_path / "assistant_memory_index.json").unlink()
    (tmp_path / "assistant_decisions.json").unlink()

    restore_res = await svc.restore_backup(backup_id=backup_id)
    assert restore_res.success, restore_res.error
    assert restore_res.restored_items.get("assistant_memory_index") == 1
    assert restore_res.restored_items.get("assistant_decisions") == 1

    restored_idx = json.loads((tmp_path / "assistant_memory_index.json").read_text(encoding="utf-8"))
    assert restored_idx["entries"][0]["entry_id"] == "note:n9"
    restored_dec = json.loads((tmp_path / "assistant_decisions.json").read_text(encoding="utf-8"))
    assert restored_dec["decisions"][0]["id"] == "d1"


@pytest.mark.asyncio
async def test_restore_rejects_tampered_backup(tmp_path: Path) -> None:
    _seed_data(tmp_path)
    svc = _make_service(tmp_path, compress=False)

    create_res = await svc.create_backup()
    assert create_res.success
    backup_file = Path(create_res.file_path)

    # 篡改备份文件内容（破坏校验和）
    raw = backup_file.read_text(encoding="utf-8")
    tampered = raw.replace('"周复盘"', '"被篡改的恶意内容"')
    assert tampered != raw
    backup_file.write_text(tampered, encoding="utf-8")

    restore_res = await svc.restore_backup(backup_id=create_res.backup_id)
    assert not restore_res.success
    assert "校验和" in (restore_res.error or "")
    # 原数据不应被覆盖
    notes = json.loads((tmp_path / "notes.json").read_text(encoding="utf-8"))
    assert notes[0]["title"] == "周复盘"


@pytest.mark.asyncio
async def test_restore_rejects_tampered_compressed_backup(tmp_path: Path) -> None:
    _seed_data(tmp_path)
    svc = _make_service(tmp_path, compress=True)

    create_res = await svc.create_backup()
    assert create_res.success
    backup_file = Path(create_res.file_path)
    assert backup_file.suffix == ".gz"

    # 通过 BackupService 内部 content_checksum 复算逻辑：用 raw bytes 重写文件
    import gzip
    with gzip.open(backup_file, "rt", encoding="utf-8") as f:
        raw = f.read()
    tampered = raw.replace('"周复盘"', '"x"')
    with gzip.open(backup_file, "wt", encoding="utf-8") as f:
        f.write(tampered)

    restore_res = await svc.restore_backup(backup_id=create_res.backup_id)
    assert not restore_res.success
    assert "校验和" in (restore_res.error or "")


@pytest.mark.asyncio
async def test_restore_by_path_self_verifies(tmp_path: Path) -> None:
    """按 backup_path 恢复（无元数据）时仍应通过内嵌校验和自验。"""
    _seed_data(tmp_path)
    svc = _make_service(tmp_path, compress=False)

    create_res = await svc.create_backup()
    assert create_res.success
    backup_file = Path(create_res.file_path)

    restore_res = await svc.restore_backup(backup_path=backup_file)
    assert restore_res.success, restore_res.error
    assert restore_res.checksum == create_res.metadata.checksum
