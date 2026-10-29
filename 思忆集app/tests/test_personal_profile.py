"""个人画像提取器测试（个人知识体系 M2）。"""
from __future__ import annotations

import pytest

from services.personal_profile_extractor import PersonalProfileExtractor


class FakeRepo:
    def __init__(self, items):
        self.items = items
        self.processed_ids: list[str] = []

    def list(self, *, processed=None, limit=100):
        return [it for it in self.items if processed is None or it.get("processed") == processed][:limit]

    def mark_processed_many(self, ids):
        self.processed_ids = list(ids)
        return len(ids)


class FakeProfile:
    def __init__(self):
        self.key_people: list[str] = []
        self.topics: list[str] = []
        self.focus = ""

    def load(self):
        return {}

    def merge_key_people(self, people):
        self.key_people = list(people)

    def merge_focus_topics(self, topics):
        self.topics = list(topics)

    def set_current_focus(self, focus):
        self.focus = focus


class FakeAI:
    def __init__(self, response):
        self.response = response

    def chat_completion(self, **kwargs):
        return self.response


def _item(content: str, source: str = "wechat", sender: str = "", processed: int = 0) -> dict:
    return {
        "id": f"id_{abs(hash(content))}",
        "content": content,
        "source": source,
        "sensitivity": "sensitive",
        "meta": {"sender": sender},
        "processed": processed,
    }


class TestContacts:
    def test_collects_real_senders_only(self):
        repo = FakeRepo([
            _item("a", sender="张三"),
            _item("b", sender="我"),
            _item("c", sender="微信支付"),
            _item("d", sender="李四"),
            _item("e", sender="张三"),  # 重复
        ])
        profile = FakeProfile()
        extractor = PersonalProfileExtractor(repo, profile)
        assert extractor._collect_contacts(repo.items) == ["张三", "李四"]


class TestInterests:
    def test_rule_fallback(self):
        extractor = PersonalProfileExtractor(FakeRepo([]), FakeProfile())
        found = extractor._extract_interests_by_rule("我喜欢爬山，最近也在学编程。关注投资理财。")
        assert "爬山" in found
        assert "编程" in found
        assert "投资理财" in found

    def test_rule_ignores_stopwords(self):
        extractor = PersonalProfileExtractor(FakeRepo([]), FakeProfile())
        found = extractor._extract_interests_by_rule("我喜欢的东西很多。")
        assert "东西" not in found

    def test_ai_path(self):
        repo = FakeRepo([_item("内容", sender="张三")])
        profile = FakeProfile()
        ai = FakeAI('["爬山", "编程"]')
        extractor = PersonalProfileExtractor(repo, profile, ai_client=ai)
        texts = [it["content"] for it in repo.items]
        assert extractor._extract_interests(texts) == ["爬山", "编程"]

    def test_ai_malformed_falls_back_to_rule(self):
        repo = FakeRepo([_item("我喜欢爬山", sender="张三")])
        profile = FakeProfile()
        ai = FakeAI("抱歉，我无法处理")  # 非 JSON
        extractor = PersonalProfileExtractor(repo, profile, ai_client=ai)
        texts = [it["content"] for it in repo.items]
        found = extractor._extract_interests(texts)
        assert found and "爬山" in found

    def test_ai_none_falls_back(self):
        extractor = PersonalProfileExtractor(FakeRepo([]), FakeProfile(), ai_client=None)
        assert extractor._extract_interests(["我喜欢爬山"]) == ["爬山"]


class TestFullPipeline:
    def test_extract_writes_back_and_marks_processed(self):
        repo = FakeRepo([
            _item("我喜欢爬山", sender="张三"),
            _item("周末去爬山", sender="李四"),
            _item("项目代码提交", sender="我"),
        ])
        profile = FakeProfile()
        extractor = PersonalProfileExtractor(repo, profile, ai_client=None)
        result = extractor.extract(limit=100)

        assert result["scanned"] == 3
        assert result["processed"] == 3
        assert "张三" in profile.key_people and "李四" in profile.key_people
        assert "我" not in profile.key_people
        assert profile.topics  # 兴趣已写回
        assert profile.focus  # 焦点已设置

    def test_extract_empty(self):
        repo = FakeRepo([])
        profile = FakeProfile()
        extractor = PersonalProfileExtractor(repo, profile)
        result = extractor.extract()
        assert result["scanned"] == 0
        assert profile.key_people == []
        assert profile.topics == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
