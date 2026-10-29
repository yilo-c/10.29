"""鍚屾冲突璺緞测试：验证仓储层 merge 在双写冲突场鏅笅鐨勮涓恒€"

覆盖锛"
- 词库 wordbook 冲突妫€测与冲突记录
- 单词 word 冲突妫€测与冲突记录
- 翻译历史 translation 冲突妫€测与冲突记录
- prefer_remote=True 时冲突应琚嚜动解鍐"
- 无冲突时鏂板和更鏂版常工浣"
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories import LocalTranslationHistoryRepository, LocalVocabularyRepository


def _make_vocab_repo(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    return LocalVocabularyRepository(store), store


def _make_translation_repo(tmp_path):
    store = LocalDataStore(base_dir=str(tmp_path))
    return LocalTranslationHistoryRepository(store), store


# 鈹€鈹€ Wordbook 冲突 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def test_wordbook_merge_new_remote_added(tmp_path):
    """杩滅有新词库，本地没鏈"鈫"应新澧炪€"""
    repo, store = _make_vocab_repo(tmp_path)

    remote = [{"remote_id": "r-wb-1", "name": "鎶€鏈瘝姹", "book_key": "bk-1", "updated_at": "2026-04-18T00:00:00"}]
    result = repo.merge_remote_wordbooks(remote)

    assert result["added"] == 1
    assert result["conflicts"] == []
    wordbooks = repo.list_wordbooks()
    names = [wb["name"] for wb in wordbooks]
    assert "鎶€鏈瘝姹" in names


def test_wordbook_merge_conflict_when_local_dirty(tmp_path):
    """鏈湴词库有脏淇敼 + 杩滅也有不同淇敼 鈫"prefer_remote=False 时应报冲绐併€"""
    repo, store = _make_vocab_repo(tmp_path)
    store.save_json("wordbooks", [
        {"id": 1, "name": "鏈湴淇敼鍚", "remote_id": "r-wb-1", "book_key": "bk-1",
         "sync_state": "dirty", "updated_at": "2026-04-17T12:00:00"},
    ])

    remote = [{"remote_id": "r-wb-1", "name": "杩滅淇敼鍚", "book_key": "bk-1", "updated_at": "2026-04-18T00:00:00"}]
    result = repo.merge_remote_wordbooks(remote, prefer_remote=False)

    assert result["skipped"] == 1
    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["entity_type"] == "wordbook"
    assert conflict["local_title"] == "鏈湴淇敼鍚"
    assert conflict["remote_title"] == "杩滅淇敼鍚"


def test_wordbook_merge_auto_resolve_when_prefer_remote(tmp_path):
    """prefer_remote=True 时脏词库搴旇杩滅覆盖，不产生冲突。"""
    repo, store = _make_vocab_repo(tmp_path)
    store.save_json("wordbooks", [
        {"id": 1, "name": "鏈湴旧名", "remote_id": "r-wb-1", "book_key": "bk-1",
         "sync_state": "dirty", "updated_at": "2026-04-17T12:00:00"},
    ])

    remote = [{"remote_id": "r-wb-1", "name": "杩滅新名", "book_key": "bk-1", "updated_at": "2026-04-18T00:00:00"}]
    result = repo.merge_remote_wordbooks(remote, prefer_remote=True)

    assert result["conflicts"] == []
    assert result["updated"] == 1
    wordbooks = repo.list_wordbooks()
    assert wordbooks[0]["name"] == "杩滅新名"


def test_wordbook_merge_no_conflict_when_identical_and_synced(tmp_path):
    """杩滅与本地内容相同且已同姝"鈫"应跳过，不报冲突。"""
    repo, store = _make_vocab_repo(tmp_path)
    store.save_json("wordbooks", [
        {"id": 1, "name": "相同名称", "remote_id": "r-wb-1", "book_key": "bk-1",
         "sync_state": "synced", "updated_at": "2026-04-17T12:00:00"},
    ])

    remote = [{"remote_id": "r-wb-1", "name": "相同名称", "book_key": "bk-1", "updated_at": "2026-04-17T12:00:00"}]
    result = repo.merge_remote_wordbooks(remote, prefer_remote=False)

    assert result["conflicts"] == []


# 鈹€鈹€ Word 冲突 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def test_word_merge_new_remote_added(tmp_path):
    """杩滅有新单词，本地没鏈"鈫"应新澧炪€"""
    repo, store = _make_vocab_repo(tmp_path)

    remote = [{"remote_id": "r-w-1", "word": "algorithm", "word_key": "wk-1",
               "meaning": "算法", "updated_at": "2026-04-18T00:00:00"}]
    result = repo.merge_remote_words(remote)

    assert result["added"] == 1
    assert result["conflicts"] == []


def test_word_merge_conflict_when_local_dirty(tmp_path):
    """鏈湴单词脏状鎬"+ 杩滅不同 鈫"应报冲突。"""
    repo, store = _make_vocab_repo(tmp_path)
    store.save_json("words", [
        {"id": 1, "word": "algorithm", "meaning": "鏈湴翻译", "remote_id": "r-w-1",
         "word_key": "wk-1", "sync_state": "dirty", "updated_at": "2026-04-17T12:00:00"},
    ])

    remote = [{"remote_id": "r-w-1", "word": "algorithm", "meaning": "杩滅翻译",
               "word_key": "wk-1", "updated_at": "2026-04-18T00:00:00"}]
    result = repo.merge_remote_words(remote, prefer_remote=False)

    assert result["skipped"] == 1
    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["entity_type"] == "word"
    assert conflict["local_meaning"] == "鏈湴翻译"
    assert conflict["remote_meaning"] == "杩滅翻译"


def test_word_merge_auto_resolve_when_prefer_remote(tmp_path):
    """prefer_remote=True 时脏单词搴旇杩滅覆盖。"""
    repo, store = _make_vocab_repo(tmp_path)
    store.save_json("words", [
        {"id": 1, "word": "algorithm", "meaning": "旧翻璇", "remote_id": "r-w-1",
         "word_key": "wk-1", "sync_state": "dirty", "updated_at": "2026-04-17T12:00:00"},
    ])

    remote = [{"remote_id": "r-w-1", "word": "algorithm", "meaning": "新翻璇",
               "word_key": "wk-1", "updated_at": "2026-04-18T00:00:00"}]
    result = repo.merge_remote_words(remote, prefer_remote=True)

    assert result["conflicts"] == []
    assert result["updated"] == 1


# 鈹€鈹€ Translation 冲突 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def test_translation_merge_new_remote_added(tmp_path):
    """杩滅有新翻译记录 鈫"应新澧炪€"""
    repo, store = _make_translation_repo(tmp_path)

    remote = [{"remote_id": "r-t-1", "source_text": "hello", "translated_text": "你好",
               "history_key": "hk-1", "updated_at": "2026-04-18T00:00:00"}]
    result = repo.merge_remote_translations(remote)

    assert result["added"] == 1
    assert result["conflicts"] == []


def test_translation_merge_conflict_when_local_dirty(tmp_path):
    """鏈湴翻译脏状鎬"+ 杩滅不同 鈫"应报冲突。"""
    repo, store = _make_translation_repo(tmp_path)
    store.save_json("translations", [
        {"id": 1, "source_text": "hello", "translated_text": "鏈湴翻译",
         "remote_id": "r-t-1", "history_key": "hk-1",
         "sync_state": "dirty", "updated_at": "2026-04-17T12:00:00"},
    ])

    remote = [{"remote_id": "r-t-1", "source_text": "hello", "translated_text": "杩滅翻译",
               "history_key": "hk-1", "updated_at": "2026-04-18T00:00:00"}]
    result = repo.merge_remote_translations(remote, prefer_remote=False)

    assert result["skipped"] == 1
    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["local_translated_text"] == "鏈湴翻译"
    assert conflict["remote_translated_text"] == "杩滅翻译"


def test_translation_merge_auto_resolve_when_prefer_remote(tmp_path):
    """prefer_remote=True 时脏翻译搴旇杩滅覆盖。"""
    repo, store = _make_translation_repo(tmp_path)
    store.save_json("translations", [
        {"id": 1, "source_text": "hello", "translated_text": "旧翻璇",
         "remote_id": "r-t-1", "history_key": "hk-1",
         "sync_state": "dirty", "updated_at": "2026-04-17T12:00:00"},
    ])

    remote = [{"remote_id": "r-t-1", "source_text": "hello", "translated_text": "新翻璇",
               "history_key": "hk-1", "updated_at": "2026-04-18T00:00:00"}]
    result = repo.merge_remote_translations(remote, prefer_remote=True)

    assert result["conflicts"] == []
    assert result["updated"] == 1


def test_translation_merge_no_conflict_when_identical_and_synced(tmp_path):
    """杩滅与本地完全一致且已同姝"鈫"不报冲突。"""
    repo, store = _make_translation_repo(tmp_path)
    store.save_json("translations", [
        {"id": 1, "source_text": "hello", "translated_text": "你好",
         "remote_id": "r-t-1", "history_key": "hk-1",
         "sync_state": "synced", "updated_at": "2026-04-17T12:00:00"},
    ])

    remote = [{"remote_id": "r-t-1", "source_text": "hello", "translated_text": "你好",
               "history_key": "hk-1", "updated_at": "2026-04-17T12:00:00"}]
    result = repo.merge_remote_translations(remote, prefer_remote=False)

    assert result["conflicts"] == []


# 鈹€鈹€ 多实体混合冲绐"鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def test_wordbook_merge_multiple_some_conflict_some_add(tmp_path):
    """多个杩滅词库：一涓啿绐併€佷竴涓柊澧"鈫"正确分类。"""
    repo, store = _make_vocab_repo(tmp_path)
    store.save_json("wordbooks", [
        {"id": 1, "name": "冲突词库", "remote_id": "r-wb-1", "book_key": "bk-1",
         "sync_state": "dirty", "updated_at": "2026-04-17T12:00:00"},
    ])

    remote = [
        {"remote_id": "r-wb-1", "name": "杩滅改名", "book_key": "bk-1", "updated_at": "2026-04-18T00:00:00"},
        {"remote_id": "r-wb-2", "name": "全新词库", "book_key": "bk-2", "updated_at": "2026-04-18T00:00:00"},
    ]
    result = repo.merge_remote_wordbooks(remote, prefer_remote=False)

    assert result["added"] == 1
    assert result["skipped"] == 1
    assert len(result["conflicts"]) == 1
    assert result["total_remote"] == 2
