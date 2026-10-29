"""raw items 存储层 + 敏感度分类器测试（个人知识体系底座 P0-A/P0-B）。"""
from __future__ import annotations

import os
import tempfile

import pytest

from repositories._raw_item import RawItemRepository
from services.sensitivity_classifier import SensitivityClassifier, get_classifier


@pytest.fixture()
def repo():
    db = os.path.join(tempfile.gettempdir(), f"raw_items_test_{os.getpid()}.db")
    r = RawItemRepository(db)
    yield r
    r.close()
    try:
        os.unlink(db)
    except OSError:
        pass


class TestRawItemRepository:
    def test_add_and_get(self, repo):
        item = repo.add_one("你好，这是测试内容", "wechat", meta={"sender": "张三"})
        assert item is not None
        assert item["source"] == "wechat"
        assert item["sensitivity"] == "personal"
        assert item["meta"]["sender"] == "张三"
        got = repo.get(item["id"])
        assert got["content"] == "你好，这是测试内容"

    def test_dedup_by_hash(self, repo):
        assert repo.add_one("相同内容", "wechat") is not None
        assert repo.add_one("相同内容", "wechat") is None
        assert repo.count() == 1

    def test_add_many_and_stats(self, repo):
        added = repo.add_many([
            {"content": "a", "source": "wechat", "sensitivity": "sensitive"},
            {"content": "b", "source": "reading", "sensitivity": "personal"},
            {"content": "a", "source": "wechat"},  # 重复，跳过
        ])
        assert added == 2
        stats = repo.stats()
        assert stats["total"] == 2
        assert stats["by_source"] == {"wechat": 1, "reading": 1}
        assert stats["by_sensitivity"] == {"sensitive": 1, "personal": 1}

    def test_list_filters(self, repo):
        repo.add_many([
            {"content": "私密内容", "source": "wechat", "sensitivity": "sensitive"},
            {"content": "公开内容", "source": "reading", "sensitivity": "personal"},
        ])
        sens = repo.list(sensitivity="sensitive")
        assert len(sens) == 1 and sens[0]["content"] == "私密内容"
        src = repo.list(source="reading")
        assert len(src) == 1 and src[0]["content"] == "公开内容"

    def test_sensitivity_update_and_processed(self, repo):
        item = repo.add_one("内容", "wechat")
        assert repo.set_sensitivity(item["id"], "sensitive") is True
        assert repo.get(item["id"])["sensitivity"] == "sensitive"
        assert repo.set_sensitivity(item["id"], "invalid") is False
        assert repo.mark_processed(item["id"]) is True
        assert repo.get(item["id"])["processed"] == 1
        assert len(repo.list(processed=1)) == 1
        assert repo.mark_processed_many([item["id"]]) == 1

    def test_empty_content_rejected(self, repo):
        assert repo.add_one("   ", "wechat") is None


class TestSensitivityClassifier:
    def setup_method(self):
        self.clf = SensitivityClassifier()

    def test_source_default_sensitive(self):
        # 保守默认：微信/剪贴板/日记 → sensitive
        assert self.clf.classify("随便聊聊", "wechat") == "sensitive"
        assert self.clf.classify("复制的片段", "clipboard") == "sensitive"
        assert self.clf.classify("今天天气不错", "daily") == "sensitive"

    def test_source_default_personal(self):
        assert self.clf.classify("一篇文章内容", "reading") == "personal"
        assert self.clf.classify("网页摘要", "url") == "personal"
        assert self.clf.classify("对话", "chatgpt") == "personal"

    def test_keyword_upgrade(self):
        # 长关键词命中 → sensitive（即使来源是 personal）
        assert self.clf.classify("我的身份证号是 110101...", "markdown") == "sensitive"
        assert self.clf.classify("银行卡余额查询", "inbox") == "sensitive"
        assert self.clf.classify("体检报告显示正常", "reading") == "sensitive"

    def test_password_needs_context(self):
        # 短关键词"密码"需要语境（账号/支付等），技术对话不误伤
        assert self.clf.classify("登录密码是 abc123", "markdown") == "sensitive"
        assert self.clf.classify("如何修改支付密码", "markdown") == "sensitive"
        assert self.clf.classify("这段代码的密码学原理", "markdown") == "personal"

    def test_override_priority(self):
        assert self.clf.classify("普通内容", "reading", override="sensitive") == "sensitive"
        assert self.clf.classify("敏感内容", "wechat", override="personal") == "personal"

    def test_for_item_with_meta_override(self):
        clf = get_classifier()
        assert clf.for_item({"content": "x", "source": "reading"}) == "personal"
        assert clf.for_item({
            "content": "x", "source": "reading", "meta": {"sensitivity_override": "sensitive"}
        }) == "sensitive"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
