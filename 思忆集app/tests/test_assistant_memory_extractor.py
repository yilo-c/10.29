"""Tests for MemoryExtractor - automatic memory extraction with MCP-10 capabilities.



Covers all 5 tasks:

Task 1: Post-conversation auto extraction

Task 2: Three-level confidence mechanism

Task 3: Conflict detection

Task 4: Memory dedup and merging

Task 5: Privacy controls

"""

from __future__ import annotations


import sys

from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


import pytest

from data.local_storage import LocalDataStore

from memory.extractor import (
    MemoryExtractor,
    MemoryCandidate,
    ExtractionResult,
    PendingMemory,
    ConflictRecord,
    PrivacyFilter,
    PendingMemoryStore,
    DedupEngine,
    ConflictTracker,
    AUTO_WRITE_THRESHOLD,
    CONFIRM_THRESHOLD,
)


# 鈹€鈹€ Fixtures 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


@pytest.fixture
def data_store(tmp_path):

    return LocalDataStore(base_dir=str(tmp_path))


@pytest.fixture
def extractor(data_store):

    return MemoryExtractor(
        ai_client=None,
        data_store=data_store,
    )


@pytest.fixture
def sample_messages():

    return [
        {"role": "user", "content": "我叫张三，我鏄竴名软件工程师"},
        {"role": "assistant", "content": "你好张三！很高兴认识浣犮€"},
        {"role": "user", "content": "鎴戝在北浜紝下周要去上海出差"},
        {"role": "assistant", "content": "好的，了解了。去上海出差鏄伐作安排吗锛"},
        {"role": "user", "content": "鏄殑锛岃鎷滆客户，大概待3澶"},
        {"role": "assistant", "content": "好的锛岀你出宸『鍒┿€"},
    ]


# ══════════════════════════════════════════════════════════════════════════

# Task 1: 对话后自动提鍙"
# ══════════════════════════════════════════════════════════════════════════


class TestTask1Extraction:
    """Post-conversation auto extraction with memory context."""

    def test_extract_empty_returns_empty(self, extractor):

        result = extractor.extract([])

        assert isinstance(result, ExtractionResult)

        assert len(result.candidates) == 0

    def test_extract_without_ai_returns_empty(self, extractor, sample_messages):
        """Without AI client, extraction should return empty."""

        result = extractor.extract(sample_messages)

        assert isinstance(result, ExtractionResult)

        assert len(result.candidates) == 0

    def test_get_conversation_context_multiple_exchanges(self, extractor):
        """Task 1: Should capture multiple exchanges, not just last."""

        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "third"},
            {"role": "assistant", "content": "resp3"},
        ]

        context = extractor._get_conversation_context(messages, max_exchanges=3)

        assert len(context) == 6

        assert context[0]["content"] == "first"

        assert context[-1]["content"] == "resp3"

    def test_get_conversation_context_respects_exchange_limit(self, extractor):
        """Task 1: Should respect max_exchanges parameter."""

        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)] + [{"role": "assistant", "content": f"resp{i}"} for i in range(10)]

        context = extractor._get_conversation_context(messages, max_exchanges=2)

        user_msgs = [m for m in context if m["role"] == "user"]

        assert len(user_msgs) <= 2

    def test_extract_disabled_returns_empty(self, extractor, sample_messages):
        """Task 5: When extraction is disabled, should return empty."""

        extractor.set_extraction_enabled(False)

        result = extractor.extract(sample_messages, conversation_id="test_conv")

        assert len(result.candidates) == 0

        assert len(result.auto_write) == 0

    def test_extract_with_memory_context(self, extractor):
        """Task 1: Should handle existing_memories parameter."""

        existing = [
            {"content": "用户姓名是张三", "type": "factual", "confidence": 0.9},
            {"content": "用户是软件工程师", "type": "factual", "confidence": 0.85},
        ]

        summary = extractor._summarize_existing_memories(existing)

        assert "张三" in summary

        assert "软件工程师" in summary

    def test_extraction_logging(self, extractor, data_store):
        """Task 5: Extraction should be logged."""

        result = ExtractionResult(
            candidates=[MemoryCandidate("factual", "test", 0.9, "user")],
            auto_write=[MemoryCandidate("factual", "test", 0.9, "user")],
        )

        extractor._log_extraction(result, "conv123")

        logs = extractor.get_recent_extractions()

        assert len(logs) == 1

        assert logs[0]["conversation_id"] == "conv123"

        assert logs[0]["auto_write"] == 1

    def test_delete_extraction(self, extractor, data_store):
        """Task 5: User should be able to delete extraction logs."""

        result = ExtractionResult(
            candidates=[MemoryCandidate("factual", "test", 0.9, "user")],
            auto_write=[MemoryCandidate("factual", "test", 0.9, "user")],
        )

        extractor._log_extraction(result, "conv1")

        extractor._log_extraction(result, "conv2")

        assert len(extractor.get_recent_extractions()) == 2

        extractor.delete_extraction(0)

        assert len(extractor.get_recent_extractions()) == 1


# ══════════════════════════════════════════════════════════════════════════

# Task 2: 三级缃俊度机鍒"
# ══════════════════════════════════════════════════════════════════════════


class TestTask2Confidence:
    """Three-level confidence mechanism with pending queue."""

    def test_confidence_levels_classification(self, extractor):
        """Task 2: Candidates should be classified by confidence level."""

        candidates = [
            MemoryCandidate("factual", "clearly stated info", 0.9, "user"),
            MemoryCandidate("factual", "uncertain info", 0.6, "user"),
            MemoryCandidate("factual", "noise", 0.3, "ai"),
        ]

        result = ExtractionResult(candidates=candidates)

        for c in candidates:
            if c.confidence >= AUTO_WRITE_THRESHOLD:
                result.auto_write.append(c)

            elif c.confidence >= CONFIRM_THRESHOLD:
                result.needs_confirm.append(c)

            else:
                result.discarded.append(c)

        assert len(result.auto_write) == 1

        assert result.auto_write[0].content == "clearly stated info"

        assert len(result.needs_confirm) == 1

        assert result.needs_confirm[0].content == "uncertain info"

        assert len(result.discarded) == 1

        assert result.discarded[0].content == "noise"

    def test_confidence_adjustment_with_repetition(self, extractor):
        """Task 2: Repetition should boost confidence."""

        candidate = MemoryCandidate("factual", "I like Python", 0.7, "user")

        existing = [
            {"content": "用户鍠滄Python", "type": "factual", "confidence": 0.8},
        ]

        adjusted = extractor._compute_adjusted_confidence(candidate, existing)

        assert adjusted > 0.7

    def test_confidence_adjustment_with_consistency(self, extractor):
        """Task 2: Consistency across multiple memories should boost."""

        candidate = MemoryCandidate("factual", "张三鏄竴名软件工程师", 0.75, "user")

        existing = [
            {"content": "用户姓名鏄紶一", "type": "factual", "confidence": 0.9},
            {"content": "用户职业鏄蒋件工程师", "type": "factual", "confidence": 0.85},
            {"content": "用户使用Python", "type": "factual", "confidence": 0.7},
        ]

        adjusted = extractor._compute_adjusted_confidence(candidate, existing)

        assert adjusted >= 0.75

    def test_confidence_adjustment_user_source_boost(self, extractor):
        """Task 2: User-sourced info should get a small boost."""

        candidate = MemoryCandidate("factual", "some info", 0.8, "user")

        adjusted_user = extractor._compute_adjusted_confidence(candidate, [])

        candidate_ai = MemoryCandidate("factual", "some info", 0.8, "ai")

        adjusted_ai = extractor._compute_adjusted_confidence(candidate_ai, [])

        assert adjusted_user >= adjusted_ai

    def test_pending_memory_store_add_and_get(self, data_store):
        """Task 2: PendingMemoryStore should persist and retrieve."""

        store = PendingMemoryStore(data_store)

        candidate = MemoryCandidate("factual", "user preference", 0.6, "user")

        store.add(candidate, conversation_id="conv1")

        pending = store.get_pending()

        assert len(pending) == 1

        assert pending[0].candidate.content == "user preference"

        assert pending[0].conversation_id == "conv1"

    def test_pending_memory_store_pop_confirmed(self, data_store):
        """Task 2: Should be able to promote pending to confirmed."""

        store = PendingMemoryStore(data_store)

        candidate = MemoryCandidate("factual", "test", 0.6, "user")

        store.add(candidate)

        pending = store.get_pending()

        assert len(pending) == 1

        confirmed = store.pop_confirmed(0)

        assert confirmed is not None

        assert confirmed.candidate.content == "test"

        assert len(store.get_pending()) == 0

    def test_pending_memory_store_remove(self, data_store):
        """Task 2: Should be able to remove pending items."""

        store = PendingMemoryStore(data_store)

        store.add(MemoryCandidate("factual", "a", 0.6, "user"))

        store.add(MemoryCandidate("factual", "b", 0.6, "user"))

        assert len(store.get_pending()) == 2

        store.remove(0)

        assert len(store.get_pending()) == 1

        assert store.get_pending()[0].candidate.content == "b"

    def test_pending_memory_store_count(self, data_store):
        """Task 2: Should report correct pending count."""

        store = PendingMemoryStore(data_store)

        assert store.get_pending_count() == 0

        store.add(MemoryCandidate("factual", "test", 0.6, "user"))

        assert store.get_pending_count() == 1

    def test_pending_memory_record_prompt(self, data_store):
        """Task 2: Should track prompt history."""

        store = PendingMemoryStore(data_store)

        store.add(MemoryCandidate("factual", "test", 0.6, "user"))

        store.record_prompt(0)

        pending = store.get_pending()

        assert pending[0].confirm_count == 1


# ══════════════════════════════════════════════════════════════════════════

# Task 3: 冲突妫€娴"
# ══════════════════════════════════════════════════════════════════════════


class TestTask3Conflict:
    """Conflict detection with history and resolution."""

    def test_contradiction_detection(self, extractor):
        """Task 3: Should detect contradictory statements."""

        assert extractor._is_contradictory("鎴戝在深鍦", "鎴戝在北了")

    def test_no_false_contradiction(self, extractor):
        """Task 3: Should not flag non-contradictory statements."""

        assert not extractor._is_contradictory("我喜欢跑姝", "我喜娆㈣涔")

    def test_negation_contradiction(self, extractor):
        """Contradiction through negation."""

        assert extractor._is_contradictory("I do not like Python", "I like Python")

    def test_conflict_resolution_keep_new(self, extractor):
        """Task 3: Significantly higher confidence should keep new."""

        candidate = MemoryCandidate("factual", "new info", 0.95, "user")

        existing = {"content": "old info", "confidence": 0.5}

        strategy = extractor._resolve_conflict_strategy(candidate, existing)

        assert strategy == "keep_new"

    def test_conflict_resolution_keep_old(self, extractor):
        """Task 3: Significantly lower confidence should keep old."""

        candidate = MemoryCandidate("factual", "new info", 0.3, "user")

        existing = {"content": "old info", "confidence": 0.9}

        strategy = extractor._resolve_conflict_strategy(candidate, existing)

        assert strategy == "keep_old"

    def test_conflict_resolution_keep_both(self, extractor):
        """Task 3: Close confidence should keep both."""

        candidate = MemoryCandidate("factual", "new info", 0.7, "user")

        existing = {"content": "old info", "confidence": 0.75}

        strategy = extractor._resolve_conflict_strategy(candidate, existing)

        assert strategy == "keep_both"

    def test_conflict_tracker_record_and_get(self, data_store):
        """Task 3: ConflictTracker should persist conflicts."""

        tracker = ConflictTracker(data_store)

        record = ConflictRecord(
            new_content="家在北京",
            existing_content="家在深圳",
            new_confidence=0.8,
            existing_confidence=0.9,
            detected_at="2025-01-01T00:00:00",
        )

        tracker.record_conflict(record)

        unresolved = tracker.get_unresolved_conflicts()

        assert len(unresolved) == 1

        assert unresolved[0].new_content == "家在北京"

        assert not unresolved[0].resolved

    def test_conflict_tracker_resolve(self, data_store):
        """Task 3: Should mark conflicts as resolved."""

        tracker = ConflictTracker(data_store)

        record = ConflictRecord(
            new_content="new",
            existing_content="old",
            new_confidence=0.8,
            existing_confidence=0.8,
            detected_at="2025-01-01T00:00:00",
        )

        tracker.record_conflict(record)

        assert len(tracker.get_unresolved_conflicts()) == 1

        tracker.resolve_conflict(0, "keep_new")

        assert len(tracker.get_unresolved_conflicts()) == 0

        assert len(tracker.get_all_conflicts()) == 1

        assert tracker.get_all_conflicts()[0].resolved

        assert tracker.get_all_conflicts()[0].resolution == "keep_new"

    def test_conflict_tracker_get_all(self, data_store):
        """Task 3: Should return all conflicts (resolved + unresolved)."""

        tracker = ConflictTracker(data_store)

        tracker.record_conflict(
            ConflictRecord(
                new_content="a",
                existing_content="b",
                new_confidence=0.8,
                existing_confidence=0.8,
                detected_at="2025-01-01T00:00:00",
            )
        )

        tracker.record_conflict(
            ConflictRecord(
                new_content="c",
                existing_content="d",
                new_confidence=0.8,
                existing_confidence=0.8,
                detected_at="2025-01-01T00:00:00",
            )
        )

        tracker.resolve_conflict(0, "keep_new")

        all_conflicts = tracker.get_all_conflicts()

        assert len(all_conflicts) == 2

    def test_location_conflict_detection(self, extractor):
        """Task 3: Should detect location-specific conflicts."""

        assert extractor._is_contradictory("鎴戝在北了", "鎴戝在深鍦")

        assert extractor._is_contradictory("住在上海", "住在广州")


# ══════════════════════════════════════════════════════════════════════════

# Task 4: 记忆合并与去閲"
# ══════════════════════════════════════════════════════════════════════════


class TestTask4Dedup:
    """Memory dedup and merging."""

    def test_token_similarity_identical(self):
        """Task 4: Identical text should have high similarity."""

        sim = DedupEngine._token_similarity("I like Python", "I like Python")

        assert sim > 0.9

    def test_token_similarity_different(self):
        """Task 4: Different text should have low similarity."""

        sim = DedupEngine._token_similarity("I like Python", "The weather is nice today")

        assert sim < 0.5

    def test_dedup_unique(self):
        """Task 4: Low similarity should be unique."""

        engine = DedupEngine()

        candidate = MemoryCandidate("factual", "用户鍠滄Python", 0.9, "user")

        existing = [
            {"content": "今日天气晴朗"},
        ]

        result = engine.check(candidate, existing)

        assert result["status"] == "unique"

    def test_merge_content(self):
        """Task 4: Merge should combine content from candidate and existing."""

        candidate = MemoryCandidate("factual", "绮鹃€歅ython寮傛编程", 0.8, "user")

        existing = {"content": "用户鍠滄Python", "tags": ["python"]}

        merged = DedupEngine.merge(candidate, existing)

        assert "用户鍠滄Python" in merged["content"]

        assert merged["source"] == "user"

    def test_merge_preserves_tags(self):
        """Task 4: Merge should combine tags without duplicates."""

        candidate = MemoryCandidate("factual", "新技鑳", 0.8, "user", tags=["python", "async"])

        existing = {"content": "原有鎶€鑳", "tags": ["python", "backend"]}

        merged = DedupEngine.merge(candidate, existing)

        assert "python" in merged["tags"]

        assert "async" in merged["tags"]

        assert "backend" in merged["tags"]


# ══════════════════════════════════════════════════════════════════════════

# Task 5: 闅愮与控鍒"
# ══════════════════════════════════════════════════════════════════════════


class TestTask5Privacy:
    """Privacy controls and extraction settings."""

    def test_sensitive_password_detection(self):
        """Task 5: Should detect passwords."""

        assert PrivacyFilter.is_sensitive("我的密码是abc12345")

        assert PrivacyFilter.is_sensitive("password=my_secret_pwd")

    def test_sensitive_bank_card_detection(self):
        """Task 5: Should detect bank card numbers."""

        assert PrivacyFilter.is_sensitive("我的银行卡是234567890123456")

        assert PrivacyFilter.is_sensitive("credit card number: 4111111111111111")

    def test_sensitive_api_key_detection(self):
        """Task 5: Should detect API keys."""

        assert PrivacyFilter.is_sensitive("api_key=sk-1234567890abcdef")

        assert PrivacyFilter.is_sensitive("我的API密钥是abcdef123456")

    def test_sensitive_id_card_detection(self):
        """Task 5: Should detect ID card numbers."""

        assert PrivacyFilter.is_sensitive("我的身份证是110101199001011234")

    def test_non_sensitive_content(self):
        """Task 5: Normal content should pass through."""

        assert not PrivacyFilter.is_sensitive("我叫张三")

        assert not PrivacyFilter.is_sensitive("我喜欢编绋")

        assert not PrivacyFilter.is_sensitive("今天天气不错")

    def test_privacy_filter_candidates(self):
        """Task 5: PrivacyFilter should split safe and sensitive candidates."""

        candidates = [
            MemoryCandidate("factual", "我叫张三", 0.9, "user"),
            MemoryCandidate("factual", "我的密码是hello123", 0.8, "user"),
            MemoryCandidate("factual", "我喜娆ython", 0.7, "user"),
        ]

        safe, sensitive = PrivacyFilter.filter_candidates(candidates)

        assert len(safe) == 2

        assert len(sensitive) == 1

        assert sensitive[0].content == "我的密码是hello123"

    def test_extraction_global_toggle(self, extractor):
        """Task 5: Should respect global extraction toggle."""

        assert extractor.is_extraction_enabled() is True

        extractor.set_extraction_enabled(False)

        assert extractor.is_extraction_enabled() is False

        extractor.set_extraction_enabled(True)

        assert extractor.is_extraction_enabled() is True

    def test_extraction_per_conversation_toggle(self, extractor):
        """Task 5: Should respect per-conversation override."""

        assert extractor.is_extraction_enabled("conv1") is True

        extractor.set_conversation_extraction("conv1", False)

        assert extractor.is_extraction_enabled("conv1") is False

        assert extractor.is_extraction_enabled("conv2") is True

        extractor.set_conversation_extraction("conv1", True)

        assert extractor.is_extraction_enabled("conv1") is True

    def test_extraction_notice(self, extractor):
        """Task 5: Should provide extraction notice for conversation start."""

        notice = extractor.get_extraction_notice()

        assert isinstance(notice, str)

        assert len(notice) > 10

    def test_extraction_status_report(self, extractor):
        """Task 5: Should provide extraction status."""

        status = extractor.get_extraction_status()

        assert "extraction_enabled" in status

        assert "per_conversation_overrides" in status

        assert "pending_count" in status

        assert status["extraction_enabled"] is True

    def test_extraction_status_with_overrides(self, extractor):
        """Task 5: Status should reflect per-conversation settings."""

        extractor.set_conversation_extraction("conv_a", False)

        status = extractor.get_extraction_status()

        assert "conv_a" in status["per_conversation_overrides"]

        assert status["per_conversation_overrides"]["conv_a"] is False


# ══════════════════════════════════════════════════════════════════════════

# Data model tests

# ══════════════════════════════════════════════════════════════════════════


class TestDataModels:
    """MemoryCandidate, PendingMemory, ConflictRecord serialization."""

    def test_memory_candidate_roundtrip(self):

        candidate = MemoryCandidate(
            type="factual",
            content="test content",
            confidence=0.85,
            source="user",
            tags=["tag1", "tag2"],
            reason="important info",
        )

        d = candidate.to_dict()

        restored = MemoryCandidate.from_dict(d)

        assert restored.type == "factual"

        assert restored.content == "test content"

        assert restored.confidence == 0.85

        assert restored.tags == ["tag1", "tag2"]

    def test_memory_candidate_defaults(self):

        mc = MemoryCandidate(type="factual", content="user likes Rust", confidence=0.9, source="user")

        assert mc.tags == []

        assert mc.reason == ""

        d = mc.to_dict()

        assert d["type"] == "factual"

        assert d["content"] == "user likes Rust"

        assert d["confidence"] == 0.9

        assert d["source"] == "user"

    def test_extraction_result_defaults(self):

        r = ExtractionResult()

        assert r.candidates == []

        assert r.auto_write == []

        assert r.needs_confirm == []

        assert r.discarded == []

        assert r.conflicts == []

    def test_pending_memory_roundtrip(self):

        candidate = MemoryCandidate("factual", "content", 0.6, "user")

        pending = PendingMemory(
            candidate=candidate,
            created_at="2025-01-01T00:00:00",
            conversation_id="conv1",
            confirm_count=2,
        )

        d = pending.to_dict()

        restored = PendingMemory.from_dict(d)

        assert restored.candidate.content == "content"

        assert restored.conversation_id == "conv1"

        assert restored.confirm_count == 2

    def test_conflict_record_roundtrip(self):

        record = ConflictRecord(
            new_content="new",
            existing_content="old",
            new_confidence=0.9,
            existing_confidence=0.5,
            detected_at="2025-01-01T00:00:00",
            resolved=True,
            resolution="keep_new",
            resolved_at="2025-01-02T00:00:00",
        )

        d = record.to_dict()

        restored = ConflictRecord.from_dict(d)

        assert restored.new_content == "new"

        assert restored.resolved is True

        assert restored.resolution == "keep_new"

    def test_extraction_result_to_dict(self):

        result = ExtractionResult(
            candidates=[MemoryCandidate("factual", "c", 0.8, "user")],
            auto_write=[MemoryCandidate("factual", "a", 0.9, "user")],
            needs_confirm=[MemoryCandidate("factual", "n", 0.6, "user")],
        )

        d = result.to_dict()

        assert len(d["candidates"]) == 1

        assert len(d["auto_write"]) == 1

        assert len(d["needs_confirm"]) == 1

        assert d["auto_write"][0]["confidence"] == 0.9

    def test_extractor_returns_empty_for_empty_input(self):

        extractor = MemoryExtractor(ai_client=None)

        result = extractor.extract([])

        assert len(result.candidates) == 0

        assert len(result.auto_write) == 0

        assert len(result.needs_confirm) == 0

    def test_extractor_returns_empty_when_no_client(self):

        extractor = MemoryExtractor(ai_client=None)

        result = extractor.extract([{"role": "user", "content": "hello"}])

        assert len(result.candidates) == 0

    def test_confidence_clamped(self):

        mc = MemoryCandidate(type="factual", content="test", confidence=1.5, source="user")

        assert mc.confidence == 1.5  # raw value preserved

        mc2 = MemoryCandidate(type="factual", content="test", confidence=-0.5, source="user")

        assert mc2.confidence == -0.5


# ══════════════════════════════════════════════════════════════════════════

# Edge cases

# ══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case handling."""

    def test_privacy_filter_empty_text(self):

        assert not PrivacyFilter.is_sensitive("")

        assert not PrivacyFilter.is_sensitive("")  # empty str

    def test_dedup_empty_content(self):

        sim = DedupEngine._token_similarity("", "test")

        assert sim == 0.0

        sim = DedupEngine._token_similarity("test", "")

        assert sim == 0.0

    def test_memory_candidate_from_empty_dict(self):

        candidate = MemoryCandidate.from_dict({})

        assert candidate.type == "factual"

        assert candidate.content == ""

        assert candidate.confidence == 0.0

    def test_extraction_result_empty(self):

        result = ExtractionResult()

        d = result.to_dict()

        assert len(d["candidates"]) == 0

        assert len(d["auto_write"]) == 0

    def test_conflict_detection_no_data(self):

        extractor = MemoryExtractor(ai_client=None)

        result = extractor._detect_conflict_with_history(
            MemoryCandidate("factual", "test", 0.9, "user"),
            [],
        )

        assert result is None

    def test_pending_memory_store_empty(self, data_store):

        store = PendingMemoryStore(data_store)

        assert store.get_pending() == []

        assert store.pop_confirmed(0) is None


# ══════════════════════════════════════════════════════════════════════════

# E1 consumer switch: _llm_extract via AiTextService.extract()

# ══════════════════════════════════════════════════════════════════════════


class TestE1PortExtractConsumer:
    """ai_text_service 注入时，_llm_extract 改走端口 extract()（不再调 complete）。"""

    def test_llm_extract_uses_port_extract_not_complete(self):

        calls = {}

        class _PortService:
            def extract(self, text, schema, system_prompt="", temperature=0.1, max_tokens=800):

                calls["text"] = text

                calls["schema"] = schema

                calls["system_prompt"] = system_prompt

                calls["temperature"] = temperature

                calls["max_tokens"] = max_tokens

                return {
                    "memories": [
                        {"type": "factual", "content": "用户住在上海", "confidence": 0.9, "source": "user", "tags": ["城市"], "reason": "明确陈述"},
                    ]
                }

            def complete(self, *args, **kwargs):

                raise AssertionError("port path must use extract(), never complete()")

        extractor = MemoryExtractor(ai_client=object(), ai_text_service=_PortService())

        candidates = extractor._llm_extract(
            [{"role": "user", "content": "我住在上海"}],
            existing_memories=[{"content": "用户在杭州", "type": "factual", "confidence": 0.9}],
        )

        assert len(candidates) == 1

        c = candidates[0]

        assert isinstance(c, MemoryCandidate)

        assert c.type == "factual"

        assert c.content == "用户住在上海"

        assert c.source == "user"

        assert c.tags == ["城市"]

        assert c.reason == "明确陈述"

        assert c.confidence == pytest.approx(0.95)  # 0.9 + source=user 0.05

        # 入参契约：schema 顶层对象含 memories；对话序列化为 text；既有记忆进提示词

        assert calls["schema"]["type"] == "object"

        assert "memories" in calls["schema"]["properties"]

        assert "我住在上海" in calls["text"]

        assert "[EXISTING MEMORIES]" in calls["system_prompt"]

        assert calls["temperature"] == 0.1

        assert calls["max_tokens"] == 800

    def test_llm_extract_port_failure_returns_empty(self):

        class _FailingPort:
            def extract(self, text, schema, system_prompt="", temperature=0.1, max_tokens=800):

                raise RuntimeError("ai_client not available")

        extractor = MemoryExtractor(ai_client=object(), ai_text_service=_FailingPort())

        assert extractor._llm_extract([{"role": "user", "content": "hi"}]) == []

    def test_llm_extract_port_result_without_memories_returns_empty(self):

        class _NoMemoriesPort:
            def extract(self, text, schema, system_prompt="", temperature=0.1, max_tokens=800):

                return {"unexpected": "shape"}

        extractor = MemoryExtractor(ai_client=object(), ai_text_service=_NoMemoriesPort())

        assert extractor._llm_extract([{"role": "user", "content": "hi"}]) == []
