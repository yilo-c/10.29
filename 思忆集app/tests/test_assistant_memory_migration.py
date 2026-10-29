"""测试助手记忆数据迁移模块。"""



from __future__ import annotations



import json

import os

import sys

from datetime import datetime, timezone

from pathlib import Path



APP_ROOT = Path(__file__).resolve().parents[1]

if str(APP_ROOT) not in sys.path:

    sys.path.insert(0, str(APP_ROOT))



import pytest



from memory.index import AssistantMemoryIndexService

from memory.migration import run_migration, _parse_memory_md, _map_preference_key

from data.local_storage import LocalDataStore





class _ProfileService:

    def __init__(self):

        self._profile = {

            "long_term": {"focus_topics": [], "common_sources": [], "task_templates": [], "preferred_tools": []},

            "context": {"current_focus": "", "active_project": "", "temp_interests": [], "last_context_at": ""},

            "permission": {"allow_proactive_push": True, "proactive_categories": [], "quiet_hours": [],

                          "auto_execute_level": "low", "require_confirm_high_risk": True},

            "output": {"summary_style": "concise", "language": "zh-CN", "max_items_per_digest": 10,

                       "preferred_delivery": "inbox"},

        }



    def load(self):

        return self._profile



    def update_layer(self, layer, patch):

        if layer in self._profile:

            self._profile[layer].update(patch)

        return self._profile



    def get_focus_topics(self):

        return list(self._profile.get("long_term", {}).get("focus_topics") or [])





class _MemoryIndexService:

    def __init__(self):

        self.rebuild_called = False



    def rebuild_index(self):

        self.rebuild_called = True

        return {"entries": [], "built_at": datetime.now(timezone.utc).isoformat(), "source_counts": {}}





# 鈹€鈹€ Fixtures 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€





@pytest.fixture

def data_dir(tmp_path):

    return str(tmp_path)





@pytest.fixture

def store(tmp_path):

    return LocalDataStore(base_dir=str(tmp_path))





@pytest.fixture

def profile():

    return _ProfileService()





@pytest.fixture

def memory_index():

    return _MemoryIndexService()





# 鈹€鈹€ Tests 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€





def test_migrate_user_preferences(store, profile, memory_index, data_dir):

    """迁移 user_preferences.json 鍒UserProfileService。"""
    old_path = os.path.join(data_dir, "user_preferences.json")

    old_data = {

        "require_confirm_high_risk": False,

        "allow_proactive_push": True,

        "auto_execute_level": "medium",

        "summary_style": "detailed",

        "focus_topics": ["AI", "编程"],

        "language": "zh-CN",

        "_updated_at": "2026-01-01T00:00:00",

    }

    with open(old_path, "w", encoding="utf-8") as f:

        json.dump(old_data, f, ensure_ascii=False)



    result = run_migration(

        data_store=store,

        user_profile_service=profile,

        memory_index_service=memory_index,

        data_dir=data_dir,

    )



    assert "user_preferences.json" in result["files_migrated"]

    # 验证旧文件已重命鍚"
    assert not os.path.exists(old_path)

    # 验证 .bak 存在

    baks = [f for f in os.listdir(data_dir) if f.startswith("user_preferences.json.bak")]

    assert baks



    # 验证数据已迁移到 profile 灞"
    assert profile._profile["permission"]["require_confirm_high_risk"] is False

    assert profile._profile["permission"]["auto_execute_level"] == "medium"

    assert profile._profile["output"]["summary_style"] == "detailed"

    assert profile._profile["long_term"]["focus_topics"] == ["AI", "编程"]





def test_migrate_session_summaries(store, profile, memory_index, data_dir):

    """迁移 session_summaries.json 到新 key。"""
    old_path = os.path.join(data_dir, "session_summaries.json")

    old_data = [

        {"session_id": "s1", "summary": "璁ㄨ浜AI 新闻", "topics": ["AI", "新闻"], "created_at": "2026-01-01T00:00:00"},

        {"session_id": "s2", "summary": "编程相关", "topics": ["编程"], "created_at": "2026-01-02T00:00:00"},

    ]

    with open(old_path, "w", encoding="utf-8") as f:

        json.dump(old_data, f, ensure_ascii=False)



    result = run_migration(

        data_store=store,

        user_profile_service=profile,

        memory_index_service=memory_index,

        data_dir=data_dir,

    )



    assert "session_summaries.json" in result["files_migrated"]

    assert not os.path.exists(old_path)

    assert [f for f in os.listdir(data_dir) if f.startswith("session_summaries.json.bak")]



    # 验证数据已存储到鏂key

    summaries = store.load_json("assistant_session_summaries", [])

    assert len(summaries) == 2

    assert summaries[0]["session_id"] == "s1"

    assert summaries[0]["summary"] == "璁ㄨ浜AI 新闻"





def test_migrate_memory_md(store, profile, memory_index, data_dir):

    """迁移 MEMORY.md 鍒拌忆索寮曘€"""
    old_path = os.path.join(data_dir, "MEMORY.md")

    content = """## 2026-01-01 10:00

- 用户鍠滄鐪AI 新闻

- 偏好绠€洁回澶


## 2026-01-02 14:30

- 常用工具鏄VS Code

"""

    with open(old_path, "w", encoding="utf-8") as f:

        f.write(content)



    result = run_migration(

        data_store=store,

        user_profile_service=profile,

        memory_index_service=memory_index,

        data_dir=data_dir,

    )



    assert "MEMORY.md" in result["files_migrated"]

    assert not os.path.exists(old_path)

    assert [f for f in os.listdir(data_dir) if f.startswith("MEMORY.md.bak")]



    # 验证 raw 备份

    imported = store.load_json("assistant_imported_memory_md", [])

    assert len(imported) == 3

    assert imported[0]["content"] == "用户鍠滄鐪AI 新闻"

    assert imported[1]["content"] == "偏好绠€洁回澶"



    # 验证 memory index 琚皟用了

    assert memory_index.rebuild_called





def test_idempotent_migration(store, profile, memory_index, data_dir):

    """迁移鏄箓等的鈥斺€旂浜屾运費重复执琛屻€"""
    # 创建数据

    old_path = os.path.join(data_dir, "user_preferences.json")

    with open(old_path, "w", encoding="utf-8") as f:

        json.dump({"summary_style": "detailed"}, f)



    # 绗竴次运琛"
    result1 = run_migration(

        data_store=store, user_profile_service=profile,

        memory_index_service=memory_index, data_dir=data_dir,

    )

    assert result1["files_migrated"] == ["user_preferences.json"]



    # 绗簩次运琛"
    result2 = run_migration(

        data_store=store, user_profile_service=profile,

        memory_index_service=memory_index, data_dir=data_dir,

    )

    assert result2["already_done"] is True

    assert not result2["files_migrated"]





def test_no_old_files(store, profile, memory_index, data_dir):

    """没有旧文件时迁移无报閿欍€"""
    result = run_migration(

        data_store=store,

        user_profile_service=profile,

        memory_index_service=memory_index,

        data_dir=data_dir,

    )

    assert result["already_done"] is False

    assert not result["files_migrated"]

    assert not result["errors"]





def test_partial_migration(store, profile, memory_index, data_dir):

    """鍙湁部分旧文件存在时正常迁移。"""
    # 鍙垱寤user_preferences.json

    with open(os.path.join(data_dir, "user_preferences.json"), "w", encoding="utf-8") as f:

        json.dump({"summary_style": "bullet"}, f)



    result = run_migration(

        data_store=store, user_profile_service=profile,

        memory_index_service=memory_index, data_dir=data_dir,

    )



    assert "user_preferences.json" in result["files_migrated"]

    assert "session_summaries.json" not in result["files_migrated"]

    assert "MEMORY.md" not in result["files_migrated"]

    assert not result["errors"]





def test_map_preference_key():

    """测试旧偏濂key 鈫"鏂profile 层名映射。"""
    assert _map_preference_key("require_confirm_high_risk", True) == ("permission", {"require_confirm_high_risk": True})

    assert _map_preference_key("summary_style", "detailed") == ("output", {"summary_style": "detailed"})

    assert _map_preference_key("focus_topics", ["AI", "编程"]) == ("long_term", {"focus_topics": ["AI", "编程"]})

    assert _map_preference_key("unknown_key", "val") is None





def test_parse_memory_md():

    """解析 MEMORY.md 格式文本。"""
    content = """## 2026-01-01 10:00

- 偏好 A

- 偏好 B



## 2026-01-02

- 偏好 C"""

    entries = _parse_memory_md(content)

    assert len(entries) == 3

    assert entries[0]["content"] == "偏好 A"

    assert entries[0]["updated_at"] == "2026-01-01 10:00"

    assert entries[2]["updated_at"] == "2026-01-02"

    assert entries[2]["content"] == "偏好 C"





def test_empty_memory_md(store, profile, memory_index, data_dir):

    """空的 MEMORY.md 涔熻迁移锛bak 重命名）。"""
    old_path = os.path.join(data_dir, "MEMORY.md")

    with open(old_path, "w", encoding="utf-8") as f:

        f.write("")



    result = run_migration(

        data_store=store, user_profile_service=profile,

        memory_index_service=memory_index, data_dir=data_dir,

    )



    assert "MEMORY.md" in result["files_migrated"]

    assert not os.path.exists(old_path)

