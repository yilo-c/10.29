"""仓储灞"CRUD roundtrip 测试 '"验证鍚"Repository 鐨勫删改查原瀛愭€褋€"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories import (
    LocalImprovementProposalRepository,
    LocalPersonalInboxRepository,
    LocalPersonalNoteRepository,
    LocalPersonalTaskRepository,
    LocalSettingsRepository,
    LocalTranslationHistoryRepository,
    LocalVocabularyRepository,
)


def _make_store(tmp_path):
    return LocalDataStore(base_dir=str(tmp_path))


# 鈹€鈹€ Tasks 鈹€鈹€

def test_task_add_and_list(tmp_path):
    repo = LocalPersonalTaskRepository(_make_store(tmp_path))
    tid = repo.add_task({"name": "写测璇"})
    assert tid >= 1
    tasks = repo.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["name"] == "写测璇"
    assert tasks[0]["id"] == tid


def test_task_update_roundtrip(tmp_path):
    repo = LocalPersonalTaskRepository(_make_store(tmp_path))
    tid = repo.add_task({"name": "鍘熷"})
    repo.update_task(tid, {"name": "已改"})
    tasks = repo.list_tasks()
    assert tasks[0]["name"] == "已改"


def test_task_complete_flag(tmp_path):
    repo = LocalPersonalTaskRepository(_make_store(tmp_path))
    tid = repo.add_task({"name": "待办"})
    repo.update_task(tid, {"is_completed": True})
    tasks = repo.list_tasks()
    assert tasks[0]["is_completed"] is True
    assert tasks[0]["completed"] is True


def test_task_soft_delete(tmp_path):
    repo = LocalPersonalTaskRepository(_make_store(tmp_path))
    tid = repo.add_task({"name": "要删闄"})
    repo.update_task(tid, {"deleted_at": "2025-01-01T00:00:00"})
    assert len(repo.list_tasks()) == 0
    assert len(repo.list_tasks(include_deleted=True)) == 1


def test_task_auto_assigns_timestamps(tmp_path):
    repo = LocalPersonalTaskRepository(_make_store(tmp_path))
    tid = repo.add_task({"name": "时间测试"})
    task = repo.list_tasks()[0]
    assert task.get("created_at")
    assert task.get("updated_at")


# 鈹€鈹€ Notes 鈹€鈹€

def test_note_add_and_list(tmp_path):
    repo = LocalPersonalNoteRepository(_make_store(tmp_path))
    nid = repo.add_note({"title": "绗旇涓€", "content": "鍐呭"})
    assert nid >= 1
    notes = repo.list_notes()
    assert len(notes) == 1
    assert notes[0]["title"] == "绗旇涓€"


def test_note_update_keeps_version(tmp_path):
    repo = LocalPersonalNoteRepository(_make_store(tmp_path))
    nid = repo.add_note({"title": "v1", "content": "鍘熷"})
    repo.update_note(nid, {"content": "淇敼鍚"}, keep_version=True)
    note = repo.list_notes()[0]
    assert note["content"] == "淇敼鍚"
    assert len(note.get("versions", [])) >= 1
    assert note["versions"][0]["content"] == "鍘熷"


def test_note_soft_delete(tmp_path):
    repo = LocalPersonalNoteRepository(_make_store(tmp_path))
    nid = repo.add_note({"title": "删我"})
    repo.delete_note(nid, soft=True)
    assert len(repo.list_notes()) == 0
    assert len(repo.list_notes(include_deleted=True)) == 1


def test_note_default_folder_path(tmp_path):
    repo = LocalPersonalNoteRepository(_make_store(tmp_path))
    repo.add_note({"title": "无文件夹"})
    note = repo.list_notes()[0]
    assert note.get("folder_path") == "/"


# 鈹€鈹€ Inbox 鈹€鈹€

def test_inbox_add_and_list(tmp_path):
    repo = LocalPersonalInboxRepository(_make_store(tmp_path))
    eid = repo.add_entry({"title": "收件测试", "content": "some text"})
    assert eid >= 1
    entries = repo.list_entries()
    assert len(entries) == 1
    assert entries[0]["title"] == "收件测试"
    assert entries[0]["status"] == "new"


def test_inbox_update_entry(tmp_path):
    repo = LocalPersonalInboxRepository(_make_store(tmp_path))
    eid = repo.add_entry({"title": "鍘熷"})
    repo.update_entry(eid, {"title": "已改", "status": "triaged"})
    entry = repo.list_entries()[0]
    assert entry["title"] == "已改"
    assert entry["status"] == "triaged"


def test_inbox_archive_filters_out(tmp_path):
    repo = LocalPersonalInboxRepository(_make_store(tmp_path))
    eid = repo.add_entry({"title": "归档测试"})
    repo.update_entry(eid, {"status": "archived"})
    assert len(repo.list_entries()) == 0
    assert len(repo.list_entries(include_archived=True)) == 1


def test_inbox_tags_normalized(tmp_path):
    repo = LocalPersonalInboxRepository(_make_store(tmp_path))
    repo.add_entry({"title": "鏍囩", "tags": ["  ai ", " test "]})
    entry = repo.list_entries()[0]
    assert entry["tags"] == ["ai", "test"]


# 鈹€鈹€ Settings 鈹€鈹€

def test_settings_get_set_roundtrip(tmp_path):
    repo = LocalSettingsRepository(_make_store(tmp_path))
    repo.set_setting("theme", "dark")
    assert repo.get_setting("theme", "light") == "dark"


def test_settings_default_on_missing(tmp_path):
    repo = LocalSettingsRepository(_make_store(tmp_path))
    assert repo.get_setting("nonexistent", 42) == 42


# 鈹€鈹€ Improvement Proposals 鈹€鈹€

def test_proposal_add_and_list(tmp_path):
    repo = LocalImprovementProposalRepository(_make_store(tmp_path))
    pid = repo.add_proposal({"title": "寤鸿涓€", "description": "改进描述"})
    assert pid >= 1
    proposals = repo.list_proposals()
    assert len(proposals) == 1
    assert proposals[0]["title"] == "寤鸿涓€"


# 鈹€鈹€ 澶氳录边鐣"鈹€鈹€

def test_multiple_tasks_independent_ids(tmp_path):
    repo = LocalPersonalTaskRepository(_make_store(tmp_path))
    id1 = repo.add_task({"name": "A"})
    id2 = repo.add_task({"name": "B"})
    id3 = repo.add_task({"name": "C"})
    assert len({id1, id2, id3}) == 3
    assert len(repo.list_tasks()) == 3


def test_update_nonexistent_task_no_crash(tmp_path):
    repo = LocalPersonalTaskRepository(_make_store(tmp_path))
    repo.add_task({"name": "存在"})
    repo.update_task(99999, {"name": "不存鍦"})
    tasks = repo.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["name"] == "存在"


# 鈹€鈹€ Translations 鈹€鈹€

def test_translation_add_and_list(tmp_path):
    repo = LocalTranslationHistoryRepository(_make_store(tmp_path))
    tid = repo.add_translation_record("hello", "你好", "en", "zh-CHS")
    assert tid >= 1
    records = repo.list_translation_records()
    assert len(records) == 1
    assert records[0]["source_text"] == "hello"
    assert records[0]["translated_text"] == "你好"
    assert records[0]["from_lang"] == "en"
    assert records[0]["to_lang"] == "zh-CHS"


def test_translation_soft_delete(tmp_path):
    repo = LocalTranslationHistoryRepository(_make_store(tmp_path))
    tid = repo.add_translation_record("test", "测试", "en", "zh-CHS")
    repo.delete_translation_record(tid, soft=True)
    assert len(repo.list_translation_records()) == 0
    assert len(repo.list_translation_records(include_deleted=True)) == 1


def test_translation_hard_delete(tmp_path):
    repo = LocalTranslationHistoryRepository(_make_store(tmp_path))
    tid = repo.add_translation_record("test", "测试", "en", "zh-CHS")
    repo.delete_translation_record(tid, soft=False)
    assert len(repo.list_translation_records(include_deleted=True)) == 0


def test_translation_get_history_normalizes(tmp_path):
    repo = LocalTranslationHistoryRepository(_make_store(tmp_path))
    repo.add_translation_record("apple", "苹果", "en", "zh-CHS")
    history = repo.get_translation_history()
    assert len(history) == 1
    assert history[0]["source_text"] == "apple"
    assert history[0]["translated_text"] == "苹果"


def test_translation_multiple_records_sorted(tmp_path):
    repo = LocalTranslationHistoryRepository(_make_store(tmp_path))
    repo.add_translation_record("first", "绗竴", "en", "zh-CHS")
    repo.add_translation_record("second", "绗簩", "en", "zh-CHS")
    records = repo.list_translation_records()
    assert len(records) == 2
    assert records[0]["source_text"] == "second"


def test_translation_auto_timestamps(tmp_path):
    repo = LocalTranslationHistoryRepository(_make_store(tmp_path))
    repo.add_translation_record("hi", "鍡", "en", "zh-CHS")
    record = repo.list_translation_records()[0]
    assert record.get("created_at")
    assert record.get("updated_at")


def test_translation_empty_store(tmp_path):
    repo = LocalTranslationHistoryRepository(_make_store(tmp_path))
    assert repo.list_translation_records() == []
    assert repo.get_translation_history() == []


# 鈹€鈹€ Vocabulary: Wordbooks 鈹€鈹€

def test_wordbook_default_created(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wordbooks = repo.list_wordbooks()
    assert len(wordbooks) >= 1
    assert wordbooks[0]["name"] == "默认单词本"


def test_wordbook_add_and_list(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wid = repo.add_wordbook("鑻辫四级")
    assert wid >= 2
    wordbooks = repo.list_wordbooks()
    names = [wb["name"] for wb in wordbooks]
    assert "鑻辫四级" in names


def test_wordbook_duplicate_raises(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    repo.add_wordbook("测试本")
    import pytest
    with pytest.raises(ValueError, match="已存在"):
        repo.add_wordbook("测试本")


def test_wordbook_rename(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wid = repo.add_wordbook("旧名")
    repo.rename_wordbook(wid, "新名")
    wordbooks = repo.list_wordbooks()
    names = [wb["name"] for wb in wordbooks]
    assert "新名" in names
    assert "旧名" not in names


def test_wordbook_delete_soft(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wid = repo.add_wordbook("待删")
    repo.delete_wordbook(wid)
    wordbooks = repo.list_wordbooks()
    names = [wb["name"] for wb in wordbooks]
    assert "待删" not in names
    all_wordbooks = repo.list_wordbooks(include_deleted=True)
    deleted_names = [wb["name"] for wb in all_wordbooks if wb.get("deleted_at")]
    assert "待删" in deleted_names


def test_wordbook_delete_default_raises(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    import pytest
    with pytest.raises(ValueError, match="不允许删除默认单词本"):
        repo.delete_wordbook(1)


# 鈹€鈹€ Vocabulary: Words 鈹€鈹€

def test_word_add_and_list(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wid = repo.add_word({"word": "apple", "meaning": "苹果", "wordbook_id": 1})
    assert wid >= 1
    words = repo.list_words()
    assert len(words) == 1
    assert words[0]["word"] == "apple"
    assert words[0]["meaning"] == "苹果"


def test_word_update(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wid = repo.add_word({"word": "old", "meaning": "鏃", "wordbook_id": 1})
    repo.update_word(wid, {"meaning": "旧的"})
    words = repo.list_words()
    assert words[0]["meaning"] == "旧的"


def test_word_delete(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wid = repo.add_word({"word": "gone", "meaning": "走了", "wordbook_id": 1})
    repo.delete_word(wid)
    assert len(repo.list_words()) == 0
    assert len(repo.list_words(include_deleted=True)) == 1


def test_word_get_by_wordbook(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wbid = repo.add_wordbook("专用鏈")
    repo.add_word({"word": "cat", "meaning": "鐚", "wordbook_id": 1})
    repo.add_word({"word": "dog", "meaning": "鐙", "wordbook_id": wbid})
    default_words = repo.get_words_by_wordbook(1)
    custom_words = repo.get_words_by_wordbook(wbid)
    assert len(default_words) == 1
    assert default_words[0]["word"] == "cat"
    assert len(custom_words) == 1
    assert custom_words[0]["word"] == "dog"


def test_word_search(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    repo.add_word({"word": "banana", "meaning": "香蕉", "wordbook_id": 1})
    repo.add_word({"word": "apple", "meaning": "苹果", "wordbook_id": 1})
    results = repo.search_words("apple")
    assert len(results) == 1
    assert results[0]["word"] == "apple"


def test_word_search_in_wordbook(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wbid = repo.add_wordbook("搜索鏈")
    repo.add_word({"word": "fish", "meaning": "楸", "wordbook_id": 1})
    repo.add_word({"word": "fish", "meaning": "鱼类", "wordbook_id": wbid})
    results = repo.search_words_in_wordbook("fish", wbid)
    assert len(results) == 1
    assert results[0]["meaning"] == "鱼类"


def test_word_auto_timestamps(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    repo.add_word({"word": "time", "meaning": "时间", "wordbook_id": 1})
    word = repo.list_words()[0]
    assert word.get("created_at")
    assert word.get("updated_at")


def test_wordbook_delete_cascades_words(tmp_path):
    repo = LocalVocabularyRepository(_make_store(tmp_path))
    wbid = repo.add_wordbook("级联删除")
    repo.add_word({"word": "bye", "meaning": "鍐嶈", "wordbook_id": wbid})
    repo.delete_wordbook(wbid)
    assert len(repo.get_words_by_wordbook(wbid)) == 0
    deleted_words = [w for w in repo.list_words(include_deleted=True) if w.get("deleted_at")]
    assert len(deleted_words) >= 1
