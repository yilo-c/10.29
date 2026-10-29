"""七维-数C1：备份范围对齐 boundary 回归测试（方案 A：扩展 BackupService 备份集）。

背景（七维报告 §2.1/§2.2 P0①，backup-boundary.md「80+ 键全量」不实）：
BackupService 实际仅备 6 键+1 目录（notes/tasks/settings/memories/
assistant_memory_index/assistant_decisions/ai_cache），inbox/words/
translations/chat_sessions/structured_memory_entries/audit_trail/cron_jobs/
knowledge_graph/narrative 全不覆盖——恢复后这些域永久丢失。

修复语义：扩展备份集补齐上述域（统一存储键走 data_store，文件/目录域
直接快照）；restore 路径同步扩展；v1 元数据向后兼容（新增 domain_counts
字段，不破坏旧格式）；内容级 checksum（canonical 复算）机制不变。
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from services.backup_service import BackupConfig, BackupService

# 新增统一存储 list 域（词库域 = words + wordbooks）
NEW_LIST_DOMAINS = (
    "inbox_entries",
    "words",
    "wordbooks",
    "translations",
    "structured_memory_entries",
    "audit_trail",
    "cron_jobs",
    "chat_sessions",  # 分片索引（list）；消息分片本体见 NEW_DIR_DOMAINS
)
NEW_FILE_DOMAINS = ("knowledge_graph",)
NEW_DIR_DOMAINS = ("narratives", "chat_session_shards")
# memories/ 为既有目录域，一并锁定防回归
ALL_NEW_DOMAINS = (*NEW_LIST_DOMAINS, *NEW_FILE_DOMAINS, *NEW_DIR_DOMAINS, "memories")


def _dump_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed(data_dir: Path) -> None:
    """按各域真实存储形状播种：统一存储键（文件回退形态）+ 分片/文件/目录域。"""
    _dump_json(data_dir / "inbox_entries.json", [{"id": "i1", "content": "买咖啡", "created_at": "2026-08-29"}])
    _dump_json(data_dir / "words.json", [{"id": "w1", "word": "ephemeral", "definition": "短暂的"}])
    _dump_json(data_dir / "wordbooks.json", [{"id": "wb1", "name": "考研词库"}])
    _dump_json(data_dir / "translations.json", [{"id": "tr1", "source_text": "hello", "translated_text": "你好"}])
    _dump_json(data_dir / "structured_memory_entries.json", [{"id": "s1", "content": "生日在3月"}])
    _dump_json(data_dir / "audit_trail.json", [{"id": "a1", "action": "notes.update"}])
    _dump_json(data_dir / "cron_jobs.json", [{"cron_id": "c1", "name": "早报", "expression": "0 7 * * *"}])
    _dump_json(data_dir / "chat_sessions.json", [{"id": 7, "title": "闲聊"}])
    # chat 分片目录（repositories/_chat_session.py: {id}/meta.json + chunk_N.json + latest.json）
    _dump_json(data_dir / "chat_sessions" / "7" / "meta.json", {"id": 7, "title": "闲聊", "message_count": 2})
    _dump_json(data_dir / "chat_sessions" / "7" / "chunk_0.json", [{"role": "user", "content": "hi"}])
    _dump_json(data_dir / "chat_sessions" / "7" / "latest.json", [{"role": "assistant", "content": "你好"}])
    # 文件域：knowledge_graph.json（knowledge/graph_store.py 文件存储）
    _dump_json(
        data_dir / "knowledge_graph.json",
        {
            "entities": {"e1": {"id": "e1", "name": "思忆集", "type": "project"}},
            "relations": [{"source_id": "e1", "target_id": "e1", "relation": "related_to"}],
            "updated_at": "2026-08-29T00:00:00",
        },
    )
    # 目录域：narratives/（memory/narrative/narrative_store.py: index.json）
    _dump_json(
        data_dir / "narratives" / "index.json",
        {"nar_abc123": {"narrative_id": "nar_abc123", "title": "八月总结", "narrative_type": "monthly", "content": "八月完成备份对齐"}},
    )
    # 既有 memories/ 目录域
    _dump_json(data_dir / "memories" / "memory_0.json", {"id": "m1", "content": "开始系统复习英语"})
    # 基线域
    _dump_json(data_dir / "notes.json", [{"id": "n1", "title": "笔记"}])
    _dump_json(data_dir / "todos.json", [{"id": "t1", "title": "任务"}])
    _dump_json(data_dir / "settings.json", {"theme": "dark"})


def _make_service(data_dir: Path, compress: bool = False) -> BackupService:
    return BackupService(
        data_dir=str(data_dir),
        config=BackupConfig(compress=compress, max_backups=3),
        data_store=None,
    )


def _read_blob(file_path: str | Path) -> dict:
    p = Path(file_path)
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_backup_blob_contains_all_new_domains(tmp_path: Path) -> None:
    _seed(tmp_path)
    svc = _make_service(tmp_path)
    res = await svc.create_backup()
    assert res.success, res.error
    blob = _read_blob(res.file_path)
    for domain in ALL_NEW_DOMAINS:
        assert domain in blob["data"], f"备份 blob 缺失域: {domain}"
        assert domain in blob["includes"], f"includes 未声明域: {domain}"
    # 域内容真实性抽查
    assert blob["data"]["inbox_entries"][0]["content"] == "买咖啡"
    assert blob["data"]["words"][0]["word"] == "ephemeral"
    assert blob["data"]["chat_sessions"][0]["id"] == 7
    assert blob["data"]["knowledge_graph"]["entities"]["e1"]["name"] == "思忆集"
    assert blob["data"]["chat_session_shards"]["7/latest.json"][0]["content"] == "你好"
    assert blob["data"]["narratives"]["index.json"]["nar_abc123"]["title"] == "八月总结"
    # 元数据新增 domain_counts 字段（不破坏 v1 既有字段）
    assert res.metadata is not None
    assert res.metadata.domain_counts.get("inbox_entries") == 1
    assert res.metadata.domain_counts.get("knowledge_graph") == 1
    assert res.metadata.note_count == 1


@pytest.mark.asyncio
async def test_restore_recovers_all_new_domains(tmp_path: Path) -> None:
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    _seed(seed_dir)
    svc = _make_service(seed_dir)
    res = await svc.create_backup()
    assert res.success, res.error
    backup_file = Path(res.file_path)

    # 全新机场景：空数据目录 + 独立备份目录，按 backup_path 恢复
    target = tmp_path / "fresh"
    target.mkdir()
    svc2 = BackupService(
        data_dir=str(target),
        config=BackupConfig(compress=False, max_backups=3, backup_dir=tmp_path / "shared_backups"),
        data_store=None,
    )
    restore_res = await svc2.restore_backup(backup_path=backup_file)
    assert restore_res.success, restore_res.error
    for domain in (*NEW_LIST_DOMAINS, *NEW_FILE_DOMAINS, *NEW_DIR_DOMAINS, "memories"):
        assert restore_res.restored_items.get(domain, 0) >= 1, f"恢复计数缺失域: {domain}"
    # 统一存储键读回（内容与种子一致）
    for key in NEW_LIST_DOMAINS:
        assert _read_json(target / f"{key}.json") == _read_json(seed_dir / f"{key}.json"), key
    # 文件域读回
    assert _read_json(target / "knowledge_graph.json")["entities"]["e1"]["name"] == "思忆集"
    # 目录域读回
    assert _read_json(target / "chat_sessions" / "7" / "latest.json") == [{"role": "assistant", "content": "你好"}]
    assert _read_json(target / "chat_sessions" / "7" / "meta.json")["message_count"] == 2
    assert _read_json(target / "narratives" / "index.json")["nar_abc123"]["title"] == "八月总结"
    # 既有域不回归 + checksum 往返一致
    assert _read_json(target / "memories" / "memory_0.json")["id"] == "m1"
    assert restore_res.checksum == res.metadata.checksum


@pytest.mark.asyncio
async def test_checksum_rejects_tampered_extended_domain(tmp_path: Path) -> None:
    _seed(tmp_path)
    svc = _make_service(tmp_path)
    res = await svc.create_backup()
    assert res.success
    p = Path(res.file_path)
    raw = p.read_text(encoding="utf-8")
    tampered = raw.replace("买咖啡", "恶意篡改内容")
    assert tampered != raw, "种子数据未进入备份 blob，无法验证篡改拦截"
    p.write_text(tampered, encoding="utf-8")
    restore_res = await svc.restore_backup(backup_id=res.backup_id)
    assert not restore_res.success
    assert "校验和" in (restore_res.error or "")
    # 原数据未被覆盖
    assert _read_json(tmp_path / "inbox_entries.json")[0]["content"] == "买咖啡"


class _FakeDataStore:
    """最小 LocalDataStore 桩：仅 load_json/save_json（统一存储路径验证）。"""

    def __init__(self, base_dir: Path):
        self.base_dir = str(base_dir)
        self.kv: dict = {}

    def load_json(self, key, default=None):
        return self.kv.get(key, default)

    def save_json(self, key, value):
        self.kv[key] = value


@pytest.mark.asyncio
async def test_unified_storage_roundtrip_with_data_store(tmp_path: Path) -> None:
    seed_dir = tmp_path / "ds_data"
    seed_dir.mkdir()
    store = _FakeDataStore(seed_dir)
    store.save_json("inbox_entries", [{"id": "i1", "content": "云同步来的收件箱"}])
    store.save_json("words", [{"id": "w1", "word": "resilient"}])
    store.save_json("wordbooks", [{"id": "wb1", "name": "四六级"}])
    store.save_json("translations", [{"id": "tr1", "source_text": "ok", "translated_text": "好"}])
    store.save_json("chat_sessions", [{"id": 3, "title": "会话"}])
    store.save_json("structured_memory_entries", [{"id": "s1", "content": "在学法语"}])
    store.save_json("audit_trail", [{"id": "a1", "action": "tasks.update"}])
    store.save_json("cron_jobs", [{"cron_id": "c1", "name": "巡检", "expression": "0 4 * * *"}])
    # 文件域照旧落盘
    _dump_json(seed_dir / "knowledge_graph.json", {"entities": {"e2": {"id": "e2", "name": "备份域"}}, "relations": []})

    svc = BackupService(
        data_dir=str(seed_dir),
        config=BackupConfig(compress=False, max_backups=3),
        data_store=store,
    )
    res = await svc.create_backup()
    assert res.success, res.error
    blob = _read_blob(res.file_path)
    for domain in NEW_LIST_DOMAINS:
        assert domain in blob["data"], f"统一存储模式下 blob 缺失域: {domain}"
    assert "knowledge_graph" in blob["data"]

    # 模拟数据丢失：清空统一存储 + 删文件域
    store.kv.clear()
    (seed_dir / "knowledge_graph.json").unlink()
    restore_res = await svc.restore_backup(backup_id=res.backup_id)
    assert restore_res.success, restore_res.error
    for domain in NEW_LIST_DOMAINS:
        assert domain in store.kv, f"恢复后统一存储缺失域: {domain}"
    assert store.kv["words"][0]["word"] == "resilient"
    assert _read_json(seed_dir / "knowledge_graph.json")["entities"]["e2"]["name"] == "备份域"


def test_v1_metadata_without_domain_counts_still_loads(tmp_path: Path) -> None:
    """v1 元数据（无 domain_counts 字段）加载不报错，旧字段语义不变。"""
    _make_service(tmp_path)  # 先创建 backups/ 目录
    old_entry = {
        "backup_id": "20260101_000000",
        "backup_type": "full",
        "created_at": "2026-01-01T00:00:00",
        "size_bytes": 10,
        "file_path": str(tmp_path / "backups" / "20260101_000000.json"),
        "checksum": "legacy",
        "version": "1.0.0",
        "includes": ["notes", "tasks"],
    }
    (tmp_path / "backups" / "backups.json").write_text(json.dumps([old_entry], ensure_ascii=False), encoding="utf-8")
    svc2 = _make_service(tmp_path)
    items = svc2.list_backups()
    assert len(items) == 1
    assert items[0].backup_id == "20260101_000000"
    assert items[0].includes == ["notes", "tasks"]
    assert items[0].domain_counts == {}
