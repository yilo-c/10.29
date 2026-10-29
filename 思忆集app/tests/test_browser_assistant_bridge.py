from __future__ import annotations

import http.client
import json
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from browser.assistant_bridge import _MAX_REQUEST_BODY_BYTES, BrowserAssistantBridgeService, _sanitize_html
from core.settings_schema import KEY_ASSISTANT_UPGRADE_CHANNEL, KEY_BROWSER_ASSISTANT_BRIDGE_PORT
from services.url_learning_service import build_learning_result_from_browser_context


class _FakeSettingsRepository:
    def __init__(self, port: int, trusted_extension_ids=None, channel: str = "preview"):
        self.port = port
        self.trusted_extension_ids = list(trusted_extension_ids or [])
        self.channel = channel

    def get_setting(self, key, default=None):
        if key == KEY_BROWSER_ASSISTANT_BRIDGE_PORT:
            return self.port
        if key == KEY_ASSISTANT_UPGRADE_CHANNEL:
            return self.channel
        if key == "browser_assistant_trusted_extension_ids":
            return list(self.trusted_extension_ids)
        return default


def test_browser_bridge_requires_configured_whitelist_in_stable_channel():
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(_pick_free_port(), trusted_extension_ids=[], channel="stable")
    )

    try:
        service.register_extension_id("stable-smoke-extension")
    except ValueError as exc:
        assert str(exc) == "extension_whitelist_required_in_stable"
    else:
        raise AssertionError("stable channel should require configured whitelist")


def test_browser_bridge_allows_runtime_registration_in_preview_without_config():
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(_pick_free_port(), trusted_extension_ids=[], channel="preview")
    )

    service.register_extension_id("preview-extension")

    assert "preview-extension" in service._trusted_extension_ids()


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request_json(url: str, *, method: str = "GET", headers=None, payload=None):
    parsed = urlparse(url)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request(method, parsed.path, body=data, headers=request_headers)
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        return response.status, json.loads(body or "{}")
    finally:
        connection.close()


def _request_with_headers(url: str, *, method: str = "GET", headers=None, payload=None):
    parsed = urlparse(url)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request(method, parsed.path, body=data, headers=request_headers)
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        return response.status, dict(response.getheaders()), json.loads(body or "{}")
    finally:
        connection.close()


class _FakeWorkbenchService:
    def __init__(self):
        self.captured = []

    def build_context_suggestions(self, snapshot):
        return [{"text": f"待办 {len(snapshot.get('pending_tasks') or [])} 个，先处理最重要的一项。", "action": "tasks"}]

    def capture_quick_entry(self, text, source="manual", title=None, tags=None, metadata=None):
        entry = {
            "id": len(self.captured) + 1,
            "title": title or "浏览器收件",
            "content": text,
            "source": source,
            "tags": list(tags or []),
            "metadata": dict(metadata or {}),
        }
        self.captured.append(entry)
        return entry


class _FakeNoteRepository:
    def __init__(self):
        self.notes = []

    def list_notes(self, include_deleted=False):
        return list(self.notes)

    def add_note(self, note_info):
        note_id = len(self.notes) + 1
        item = dict(note_info)
        item["id"] = note_id
        self.notes.append(item)
        return note_id


class _FakeTaskRepository:
    def __init__(self):
        self.tasks = []

    def list_tasks(self, include_deleted=False):
        return list(self.tasks)

    def add_task(self, task_info):
        task_id = len(self.tasks) + 1
        item = dict(task_info)
        item["id"] = task_id
        self.tasks.append(item)
        return task_id


class _FakeNewsRepository:
    def get_favorite_news_keys(self):
        return ["news-1"]


class _FakeInboxRepository:
    def __init__(self):
        self.entries = [{"id": 1, "title": "浏览器收件", "status": "new"}]

    def list_entries(self, include_archived=False):
        return list(self.entries)


class _FakeProposalRepository:
    def list_proposals(self):
        return []


class _FakeTranslationRepository:
    def get_translation_history(self):
        return []


def test_build_learning_result_from_browser_context_prefers_selection_text():
    result = build_learning_result_from_browser_context(
        "https://example.com/post",
        title="示例文章",
        selection_text="这是选中的重点段落。它应该优先进入总结。",
        page_text="这是整个页面的更长正文，但本次应优先用选中内容。",
    )

    assert result.title == "示例文章"
    assert result.summary
    assert any("选中" in item for item in result.warnings)


def test_build_learning_result_from_browser_context_extracts_game_mechanism_rules():
    sample = (
        "今天说一下这个星辰虫 这个星辰虫的获取机制 其实挺简单的 就是需要捕捉虫系精灵到一定数量 "
        "然后触发深渊 触发以后有概率就是星辰虫 但是你不能一直抓一种虫子 你得两种虫子来回抓 "
        "比如一种抓五个 那种抓五个 你最好是这边抓三个 那边抓三个 这样获取概率会更高 "
        "然后再一个就是深渊形态的星辰虫也是可以用的 但是你得先用清除药剂去除污染以后 才能拿来排位PVP "
        "再一个说是它可以生蛋 如果你抓的星辰虫是母的 你就可以把它放在窝里跟别的虫子一块生蛋 "
        "你抓这个星辰虫最好是用国王球抓 最好是用魔光球 因为它是根据球的不同属性来区分的 "
        "尽量不要用炫彩球去抓 因为是随机触发任意一个效果 但是PVP没什么用"
    )

    result = build_learning_result_from_browser_context(
        "https://www.bilibili.com/video/BV1mechanism",
        title="（星辰虫）如何获取及属性用途-洛克王国-世界",
        page_text=sample,
    )

    assert "获取与用途" in result.summary
    assert "清除药剂" in result.summary
    assert any(point.startswith("获取方式：") for point in result.key_points)
    assert any(point.startswith("概率优化：") for point in result.key_points)
    assert any(point.startswith("使用限制：") for point in result.key_points)
    assert any(point.startswith("球属性建议：") for point in result.key_points)


def test_browser_bridge_summarize_page_returns_context_suggestions():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    result = service.summarize_page(
        {
            "url": "https://example.com/article",
            "title": "浏览器桥接测试",
            "contentKind": "article",
            "selectionText": "这是需要总结的选中内容。",
            "pageText": "这是页面全文。",
        }
    )

    assert result["ok"] is True
    assert result["summary"]
    assert result["contextSuggestions"]
    assert result["meta"]["title"] == "浏览器桥接测试"
    assert result["meta"]["contentKind"] == "article"
    assert result["actionHints"]
    assert result["recommendedActions"]


def test_browser_bridge_can_capture_note_and_task():
    workbench = _FakeWorkbenchService()
    notes = _FakeNoteRepository()
    tasks = _FakeTaskRepository()
    service = BrowserAssistantBridgeService(
        workbench_service=workbench,
        note_repository=notes,
        task_repository=tasks,
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    payload = {
        "url": "https://example.com/article",
        "title": "测试页面",
        "contentKind": "document_page",
        "selectionText": "选中段落",
        "summary": "摘要内容",
        "keyPoints": ["关键点 1"],
        "studySuggestions": ["建议 1"],
        "actionHints": ["优先沉淀成笔记"],
        "executedRecommendedAction": {
            "actionId": "createNote",
            "label": "优先转成笔记",
            "executedAt": "2026-04-03T10:00:00Z",
        },
    }

    capture = service.capture_page_context(payload)
    note = service.create_note_from_page(payload)
    task = service.create_task_from_page(payload)

    assert capture["ok"] is True
    assert workbench.captured
    assert workbench.captured[0]["metadata"]["content_kind"] == "document_page"
    assert note["ok"] is True and notes.notes
    assert "document_page" in notes.notes[0]["tags"]
    assert "建议动作" in notes.notes[0]["content"]
    assert task["ok"] is True and tasks.tasks


def test_browser_bridge_emits_assistant_events_for_actions():
    workbench = _FakeWorkbenchService()
    notes = _FakeNoteRepository()
    tasks = _FakeTaskRepository()
    service = BrowserAssistantBridgeService(
        workbench_service=workbench,
        note_repository=notes,
        task_repository=tasks,
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    payload = {
        "url": "https://example.com/article",
        "title": "测试页面",
        "contentKind": "video_page",
        "selectionText": "选中段落",
        "summary": "摘要内容",
        "keyPoints": ["关键点 1"],
        "studySuggestions": ["建议 1"],
    }

    service.summarize_page(payload)
    service.capture_page_context(
        {
            **payload,
            "executedRecommendedAction": {
                "actionId": "capture",
                "label": "先存到收件箱",
                "executedAt": "2026-04-03T10:00:00Z",
            },
        }
    )
    service.create_note_from_page(
        {
            **payload,
            "executedRecommendedAction": {
                "actionId": "createNote",
                "label": "优先转成笔记",
                "executedAt": "2026-04-03T10:01:00Z",
            },
        }
    )
    service.create_task_from_page(
        {
            **payload,
            "executedRecommendedAction": {
                "actionId": "createTask",
                "label": "优先生成任务",
                "executedAt": "2026-04-03T10:02:00Z",
            },
        }
    )
    events = service.drain_assistant_events()

    assert len(events) == 4
    assert [item["event_type"] for item in events] == [
        "browser.summarized",
        "browser.captured",
        "browser.note_created",
        "browser.task_created",
    ]
    assert events[0]["payload"]["title"] == "测试页面"
    assert events[0]["payload"]["contentKind"] == "video_page"
    assert events[0]["payload"]["actionHints"]
    assert events[1]["payload"]["executedRecommendedAction"]["actionId"] == "capture"
    assert events[2]["payload"]["executedRecommendedAction"]["actionId"] == "createNote"
    assert events[3]["payload"]["executedRecommendedAction"]["actionId"] == "createTask"
    assert service.drain_assistant_events() == []


def test_browser_bridge_routes_video_page_summary_with_action_hints():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    result = service.summarize_page(
        {
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "课程视频：任务拆解",
            "contentKind": "video_page",
            "rootHint": "#v_desc",
            "selectionText": "这段视频主要讲任务拆解、执行顺序和排期风险。",
            "pageText": "视频简介和正文说明。",
        }
    )

    assert result["meta"]["contentKind"] == "video_page"
    assert result["meta"]["learningContentKind"] == "视频页"
    assert result["meta"]["summaryMode"] == "video_focus"
    assert result["actionHints"]
    assert result["recommendedActions"]
    assert result["topRecommendedAction"]
    assert result["recommendedActions"][0]["actionId"] == "createTask"
    assert result["recommendedActions"][0]["enabled"] == "true"
    assert result["topRecommendedAction"]["actionId"] == "createTask"
    assert any("任务" in item for item in result["actionHints"])
    assert result["meta"]["subtitleStatus"] == "absent"


def test_browser_bridge_video_page_key_points_fallback_when_ai_points_sparse():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    result = service.summarize_page(
        {
            "url": "https://www.bilibili.com/video/BV9xx4y1z7mD",
            "title": "项目复盘：任务拆解与执行节奏",
            "contentKind": "video_page",
            "rootHint": "#v_desc",
            "selectionText": "",
            "pageText": "00:30 开场介绍项目背景。01:45 核心步骤：先拆目标，再做排期。03:10 风险点：跨团队依赖。05:20 建议动作：先建立任务看板。",
        }
    )

    assert result["ok"] is True
    assert result["meta"]["contentKind"] == "video_page"
    assert result["keyPoints"]
    assert len(result["keyPoints"]) >= 2
    assert any("步骤" in item or "风险" in item or "建议" in item for item in result["keyPoints"])


def test_browser_bridge_video_page_without_subtitle_emits_warning():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    result = service.summarize_page(
        {
            "url": "https://www.bilibili.com/video/BV1zd9KB2E79",
            "title": "AI Agent 入门视频",
            "contentKind": "video_page",
            "selectionText": "",
            "pageText": "这是简介。",
            "mediaHints": {
                "hasVideo": True,
                "hasSubtitle": False,
                "site": "bilibili",
            },
        }
    )

    warnings = result["meta"].get("warnings") or []
    assert any("主体文本信号偏弱" in item or "未检测到稳定字幕" in item for item in warnings)
    assert result["meta"].get("degradedVideoTextSignal") is True
    assert result["topRecommendedAction"]["actionId"] == "transcribe_subtitle"
    assert result["meta"].get("subtitleStatus") == "absent"
    paste_action = next(item for item in result["recommendedActions"] if item["actionId"] == "paste_transcript")
    assert paste_action["enabled"] == "true"
    assert all(item["actionId"] != "createTask" for item in result["recommendedActions"])


def test_browser_bridge_summarize_with_transcript_improves_video_signal():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    result = service.summarize_with_transcript(
        {
            "url": "https://www.bilibili.com/video/BV1zd9KB2E79",
            "title": "AI Agent 入门视频",
            "contentKind": "video_page",
            "pageText": "这是简介。",
            "descriptionText": "讲解 Agent 工作流与执行策略。",
            "transcriptText": "第一部分讲目标拆解。第二部分讲执行步骤，需要立刻安排任务并跟进风险。",
            "mediaHints": {
                "hasVideo": True,
                "hasSubtitle": False,
                "site": "bilibili",
            },
        }
    )

    warnings = result["meta"].get("warnings") or []
    assert result["meta"].get("transcriptUsed") is True
    assert result["meta"].get("transcriptFallback") is False
    assert result["meta"].get("degradedVideoTextSignal") is False
    assert any("合并标题、简介与转录文本" in item for item in warnings)
    assert result["topRecommendedAction"]["actionId"] in {"createTask", "capture", "createNote"}


def test_browser_bridge_handshake_registers_extension_and_returns_token_over_http():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        status, payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://unit-test-ext"},
            payload={"extensionId": "unit-test-ext"},
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["sessionToken"] == service.get_session_token()
        assert "unit-test-ext" in service._allowed_extension_ids
    finally:
        service.stop()


def test_browser_bridge_rejects_untrusted_origin_over_http():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        status, payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "https://evil.example"},
            payload={"extensionId": "evil-ext"},
        )
        assert status == 403
        assert payload["error"] == "origin_rejected"
    finally:
        service.stop()


def test_browser_bridge_requires_extension_to_match_configured_whitelist():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port, trusted_extension_ids=["allowed-ext"]),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        rejected_status, rejected_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://other-ext"},
            payload={"extensionId": "other-ext"},
        )
        assert rejected_status == 403
        assert rejected_payload["error"] == "origin_rejected"

        accepted_status, accepted_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://allowed-ext"},
            payload={"extensionId": "allowed-ext"},
        )
        assert accepted_status == 200
        assert accepted_payload["ok"] is True
    finally:
        service.stop()


def test_browser_bridge_rejects_handshake_when_origin_and_extension_id_do_not_match():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        status, payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://trusted-ext"},
            payload={"extensionId": "other-ext"},
        )
        assert status == 403
        assert payload["error"] == "origin_rejected"
    finally:
        service.stop()


def test_browser_bridge_requires_valid_token_after_handshake():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        handshake_status, handshake_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://trusted-ext"},
            payload={"extensionId": "trusted-ext"},
        )
        assert handshake_status == 200
        session_token = handshake_payload["sessionToken"]

        summarize_payload = {
            "url": "https://example.com/article",
            "title": "安全测试页面",
            "contentKind": "article",
            "selectionText": "需要总结的内容。",
            "pageText": "页面全文。",
        }
        base_url = f"http://127.0.0.1:{port}/api/browser-assistant/summarize"

        missing_status, missing_payload = _request_json(
            base_url,
            method="POST",
            headers={"Origin": "chrome-extension://trusted-ext"},
            payload=summarize_payload,
        )
        assert missing_status == 401
        assert missing_payload["error"] == "invalid_session_token"

        invalid_status, invalid_payload = _request_json(
            base_url,
            method="POST",
            headers={
                "Origin": "chrome-extension://trusted-ext",
                "X-Bridge-Token": "invalid-token",
            },
            payload=summarize_payload,
        )
        assert invalid_status == 401
        assert invalid_payload["error"] == "invalid_session_token"

        valid_status, valid_payload = _request_json(
            base_url,
            method="POST",
            headers={
                "Origin": "chrome-extension://trusted-ext",
                "X-Bridge-Token": session_token,
            },
            payload=summarize_payload,
        )
        assert valid_status == 200
        assert valid_payload["ok"] is True
        assert valid_payload["meta"]["title"] == "安全测试页面"
    finally:
        service.stop()


def test_browser_bridge_refresh_token_rotates_and_invalidates_old_token():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        handshake_status, handshake_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://trusted-ext"},
            payload={"extensionId": "trusted-ext"},
        )
        assert handshake_status == 200
        old_token = handshake_payload["sessionToken"]

        refresh_status, refresh_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/refresh-token",
            method="POST",
            headers={
                "Origin": "chrome-extension://trusted-ext",
                "X-Bridge-Token": old_token,
            },
            payload={},
        )
        assert refresh_status == 200
        assert refresh_payload["sessionToken"] != old_token

        summarize_payload = {
            "url": "https://example.com/article",
            "title": "安全测试页面",
            "contentKind": "article",
            "selectionText": "需要总结的内容。",
            "pageText": "页面全文。",
        }
        base_url = f"http://127.0.0.1:{port}/api/browser-assistant/summarize"

        invalid_status, invalid_payload = _request_json(
            base_url,
            method="POST",
            headers={"Origin": "chrome-extension://trusted-ext", "X-Bridge-Token": old_token},
            payload=summarize_payload,
        )
        assert invalid_status == 401
        assert invalid_payload["error"] == "invalid_session_token"

        valid_status, valid_payload = _request_json(
            base_url,
            method="POST",
            headers={"Origin": "chrome-extension://trusted-ext", "X-Bridge-Token": refresh_payload["sessionToken"]},
            payload=summarize_payload,
        )
        assert valid_status == 200
        assert valid_payload["ok"] is True
    finally:
        service.stop()


def test_browser_bridge_rejects_expired_token_with_specific_error():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.TOKEN_TTL_SECONDS = 1
    service.start()
    try:
        handshake_status, handshake_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://trusted-ext"},
            payload={"extensionId": "trusted-ext"},
        )
        assert handshake_status == 200
        service._token_issued_monotonic = time.monotonic() - 2

        status, payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/summarize",
            method="POST",
            headers={
                "Origin": "chrome-extension://trusted-ext",
                "X-Bridge-Token": handshake_payload["sessionToken"],
            },
            payload={
                "url": "https://example.com/article",
                "title": "过期测试",
                "contentKind": "article",
                "selectionText": "内容",
                "pageText": "页面全文。",
            },
        )
        assert status == 401
        assert payload["error"] == "token_expired"
    finally:
        service.stop()


def test_sanitize_html_strips_unsafe_tags_and_attributes():
    raw = '<p onclick="alert(1)">safe</p><script>alert(2)</script><a href="javascript:alert(3)">x</a><b>ok</b>'
    cleaned = _sanitize_html(raw)
    assert "onclick" not in cleaned
    assert "javascript:" not in cleaned
    assert "<script>" not in cleaned
    assert "alert(2)" in cleaned
    assert "<a>x</a>" in cleaned
    assert "<b>ok</b>" in cleaned


def test_sanitize_html_blocks_entity_encoded_javascript_href():
    cleaned = _sanitize_html('<a href="&#106;avascript:alert(1)">点击</a>')
    assert "javascript:" not in cleaned.lower()
    assert cleaned == "<a>点击</a>"


def test_sanitize_html_blocks_data_href_and_img_event_handler():
    cleaned = _sanitize_html('<a href="data:text/html,<script>alert(1)</script>" onclick="alert(2)">demo</a><img src=x onerror=alert(3)>')
    assert "data:" not in cleaned.lower()
    assert "onclick" not in cleaned.lower()
    assert "onerror" not in cleaned.lower()
    assert "<img" not in cleaned.lower()
    assert cleaned.startswith("<a")


def test_sanitize_payload_recurses_through_nested_values():
    payload = {
        "title": "<script>alert(1)</script>",
        "nested": {"html": '<a href="javascript:alert(2)">link</a>'},
        "items": ['<p onclick="x">ok</p>', {"value": "<img src=x onerror=alert(3)>"}],
    }
    cleaned = BrowserAssistantBridgeService._sanitize_payload(payload)
    assert "<script>" not in cleaned["title"]
    assert "javascript:" not in cleaned["nested"]["html"].lower()
    assert "onclick" not in cleaned["items"][0].lower()
    assert "<img" not in cleaned["items"][1]["value"].lower()


def test_browser_bridge_health_response_echoes_cors_origin():
    port = _pick_free_port()
    extension_id = "cors-ext"
    extension_origin = f"chrome-extension://{extension_id}"
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        handshake_status, handshake_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": extension_origin},
            payload={"extensionId": extension_id},
        )
        assert handshake_status == 200
        status, response_headers, payload = _request_with_headers(
            f"http://127.0.0.1:{port}/api/browser-assistant/health",
            method="GET",
            headers={
                "Origin": extension_origin,
                "X-Bridge-Token": handshake_payload["sessionToken"],
            },
        )
        assert status == 200
        assert payload["ok"] is True
        assert response_headers["Access-Control-Allow-Origin"] == extension_origin
        assert response_headers["Vary"] == "Origin"
    finally:
        service.stop()


def test_browser_bridge_options_rejects_untrusted_origin():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        status, response_headers, payload = _request_with_headers(
            f"http://127.0.0.1:{port}/api/browser-assistant/health",
            method="OPTIONS",
            headers={"Origin": "https://evil.example"},
        )
        assert status == 403
        assert payload["error"] == "origin_rejected"
        assert response_headers["Access-Control-Allow-Origin"] == "https://evil.example"
    finally:
        service.stop()


def test_browser_bridge_rejects_no_origin_on_protected_routes():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        status, payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/status",
            method="GET",
            headers={},
        )
        assert status == 403
        assert payload["error"] == "origin_rejected"
    finally:
        service.stop()


def test_browser_bridge_rejects_payload_too_large():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port),
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.start()
    try:
        handshake_status, handshake_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://oversize-ext"},
            payload={"extensionId": "oversize-ext"},
        )
        assert handshake_status == 200
        parsed = urlparse(f"http://127.0.0.1:{port}/api/browser-assistant/summarize")
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            connection.putrequest("POST", parsed.path)
            connection.putheader("Origin", "chrome-extension://oversize-ext")
            connection.putheader("X-Bridge-Token", handshake_payload["sessionToken"])
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(_MAX_REQUEST_BODY_BYTES + 1024))
            connection.endheaders(b"{}")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8") or "{}")
            status = response.status
        finally:
            connection.close()
        assert status == 413
        assert payload["error"] == "payload_too_large"
    finally:
        service.stop()


def test_browser_bridge_summarize_with_transcript_fallback_when_empty_text():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    status, payload = service._dispatch_request(
        "POST",
        "/api/browser-assistant/summarize-with-transcript",
        {
            "url": "https://www.bilibili.com/video/BV1zd9KB2E79",
            "title": "AI Agent 入门视频",
            "contentKind": "video_page",
            "pageText": "只有简介文本。",
            "descriptionText": "这是视频简介。",
            "transcriptText": "",
            "mediaHints": {
                "hasVideo": True,
                "hasSubtitle": False,
                "site": "bilibili",
            },
        },
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["meta"].get("transcriptUsed") is False
    assert payload["meta"].get("transcriptFallback") is True
    assert any("已回退到常规网页总结" in item for item in (payload["meta"].get("warnings") or []))


def test_browser_bridge_transcribe_subtitle_endpoint_manual_and_provided():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    status_manual, payload_manual = service._dispatch_request(
        "POST",
        "/api/browser-assistant/transcribe-subtitle",
        {
            "url": "https://www.bilibili.com/video/BV1zd9KB2E79",
            "title": "AI Agent 入门视频",
            "transcriptText": "",
        },
    )
    assert status_manual == 200
    assert payload_manual["ok"] is True
    assert payload_manual["requiresManualInput"] is True
    assert payload_manual["source"] == "manual_required"

    status_provided, payload_provided = service._dispatch_request(
        "POST",
        "/api/browser-assistant/transcribe-subtitle",
        {
            "url": "https://www.bilibili.com/video/BV1zd9KB2E79",
            "title": "AI Agent 入门视频",
            "transcriptText": "这是用户粘贴的转录文本。",
        },
    )
    assert status_provided == 200
    assert payload_provided["ok"] is True
    assert payload_provided["requiresManualInput"] is False
    assert payload_provided["source"] == "provided"
    assert "转录文本" in payload_provided["transcriptText"]

    status_auto, payload_auto = service._dispatch_request(
        "POST",
        "/api/browser-assistant/transcribe-subtitle",
        {
            "url": "https://www.bilibili.com/video/BV1zd9KB2E79",
            "title": "AI Agent 入门视频",
            "subtitleText": "这是自动提取的字幕。",
        },
    )
    assert status_auto == 200
    assert payload_auto["ok"] is True
    assert payload_auto["requiresManualInput"] is False
    assert payload_auto["source"] == "auto_subtitle"


def test_browser_bridge_auto_extracted_subtitle_improves_video_summary():
    """自动提取的 subtitleText 应在 summarize_page 中直接提升视频页信号。"""
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    auto_extracted_subtitle = "第一句字幕。这是重要的内容信息。最后是结论部分。"

    result = service.summarize_page(
        {
            "url": "https://www.bilibili.com/video/BV1zd9KB2E79",
            "title": "技术讲座",
            "contentKind": "video_page",
            "pageText": "简短的视频简介。",
            "descriptionText": "简短的视频简介。",
            "subtitleText": auto_extracted_subtitle,
            "subtitleStatus": "text_ready",
            "extractionDiagnostics": {"contentConfidence": 78, "noiseScore": 1},
            "mediaHints": {"hasVideo": True, "hasSubtitle": True, "site": "bilibili"},
        }
    )

    assert result, "summarize_page should return a result"
    assert "summary" in result
    assert result["meta"].get("transcriptUsed") is True
    assert result["meta"].get("transcriptSource") == "auto_subtitle"
    assert result["meta"].get("degradedVideoTextSignal") is False


def test_browser_bridge_video_page_detects_noisy_context_even_when_text_is_long():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    result = service.summarize_page(
        {
            "url": "https://www.bilibili.com/video/BV-noisy",
            "title": "杂讯很多的视频页",
            "contentKind": "video_page",
            "pageText": "播放 10万 弹幕 2000 点赞 3000 收藏 5000 评论区 相关推荐 " * 40,
            "descriptionText": "很短的简介。",
            "subtitleStatus": "absent",
            "extractionDiagnostics": {"contentConfidence": 24, "noiseScore": 7},
            "mediaHints": {
                "hasVideo": True,
                "hasSubtitle": False,
                "site": "bilibili",
            },
        }
    )

    assert result["meta"].get("degradedVideoTextSignal") is True
    assert result["topRecommendedAction"]["actionId"] == "transcribe_subtitle"
    assert any("混入推荐" in item or "主体文本信号偏弱" in item for item in (result["meta"].get("warnings") or []))


def test_browser_bridge_routes_document_page_with_note_first_action():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    result = service.summarize_page(
        {
            "url": "https://wenku.baidu.com/view/abc",
            "title": "项目规范手册",
            "contentKind": "document_page",
            "selectionText": "这份手册主要定义提交流程、接口要求和命名规范。",
            "pageText": "文档正文。",
        }
    )

    assert result["meta"]["summaryMode"] == "document_focus"
    assert result["recommendedActions"]
    assert result["recommendedActions"][0]["actionId"] == "createNote"


def test_browser_bridge_disables_task_action_when_article_has_no_execution_signal():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )

    result = service.summarize_page(
        {
            "url": "https://example.com/article",
            "title": "普通文章",
            "contentKind": "article",
            "selectionText": "这是一段普通说明文字，主要用于知识了解和信息浏览。",
            "pageText": "页面正文。",
        }
    )

    disabled_task = next(item for item in result["recommendedActions"] if item["actionId"] == "createTask")
    assert disabled_task["enabled"] == "false"
    assert disabled_task["disabledReason"]
    assert result["topRecommendedAction"]["actionId"] == "capture"


def test_browser_bridge_diagnostics_snapshot_contains_workspace_counts():
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    service.task_repository.tasks.append({"id": 1, "name": "任务 1", "is_completed": False})
    service.note_repository.notes.append({"id": 1, "title": "笔记 1"})

    result = service.get_diagnostics_snapshot()

    assert result["ok"] is True
    assert result["workspace"]["pendingTasks"] == 1
    assert result["workspace"]["notes"] == 1
    assert result["workspace"]["inboxEntries"] == 1
    assert result["availableActions"]["capture"] is True
    assert result["tips"]


def test_browser_bridge_backend_subtitle_fallback_returns_empty_for_non_bilibili():
    """_fetch_bilibili_subtitle_fallback 对非 bilibili URL 应立即返回空。"""
    service = BrowserAssistantBridgeService()
    text, status = service._fetch_bilibili_subtitle_fallback("https://example.com/page")
    assert text == ""
    assert status == "no_bvid"


def test_browser_bridge_video_page_transcript_source_reflects_backend_fallback():
    """当前端 subtitleText 为空但 bilibili URL，后端兜底字幕获取 transcriptSource 应正确标记。"""
    service = BrowserAssistantBridgeService(
        workbench_service=_FakeWorkbenchService(),
        note_repository=_FakeNoteRepository(),
        task_repository=_FakeTaskRepository(),
        news_repository=_FakeNewsRepository(),
        inbox_repository=_FakeInboxRepository(),
        proposal_repository=_FakeProposalRepository(),
        translation_repository=_FakeTranslationRepository(),
    )
    # 模拟前端没取到字幕、后端兜底也没取到（因为是测试环境无网络）
    result = service.summarize_page(
        {
            "url": "https://www.bilibili.com/video/BV1xK4y1P7bX",
            "title": "测试视频",
            "contentKind": "video_page",
            "pageText": "简短简介。",
            "descriptionText": "简短简介。",
            "subtitleText": "",
            "subtitleStatus": "absent",
            "extractionDiagnostics": {"contentConfidence": 30, "noiseScore": 2},
            "mediaHints": {"hasVideo": True, "hasSubtitle": False, "site": "bilibili"},
        }
    )
    assert result["ok"] is True
    # 无网络时后端兜底不到字幕，degraded 信号应为 True
    assert result["meta"]["degradedVideoTextSignal"] is True


# ── P0-2：划词入库端点（/add-word）适配到真实能力 word.add ─────────────


class _FakeVocabularyRepository:
    """最小词库替身：只实现桥接端点与 word.add 共用的 repo.add_word 契约。"""

    def __init__(self):
        self._next_id = 500
        self.added: list[dict] = []

    def add_word(self, data):
        self._next_id += 1
        record = dict(data)
        record["id"] = self._next_id
        self.added.append(record)
        return self._next_id

    def list_wordbooks(self, include_deleted: bool = False):
        return [{"id": 7, "name": "默认词本", "book_key": "default"}]

    def list_words(self):
        return list(self.added)


def _make_bridge_with_vocabulary(port: int, *, channel: str = "preview", trusted=None):
    vocab = _FakeVocabularyRepository()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port, trusted_extension_ids=trusted or [], channel=channel),
        vocabulary_repository=vocab,
    )
    return service, vocab


def test_browser_bridge_add_word_route_reaches_real_wordbook_capability():
    """P0-2：插件 background.js 调用的 /add-word 必须真实落库（修复前 404 not_found）。"""
    from core.events import AppEvent, event_bus

    port = _pick_free_port()
    service, vocab = _make_bridge_with_vocabulary(port)
    captured: list[AppEvent] = []
    cb = lambda e: captured.append(e) if e.event_type == "vocabulary.word_added" else None
    event_bus.on("vocabulary.word_added", cb)
    service.start()
    try:
        handshake_status, handshake_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://word-ext"},
            payload={"extensionId": "word-ext"},
        )
        assert handshake_status == 200

        status, payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/add-word",
            method="POST",
            headers={"Origin": "chrome-extension://word-ext", "X-Bridge-Token": handshake_payload["sessionToken"]},
            payload={"text": "ephemeral", "url": "https://example.com/a", "title": "示例页"},
        )
        assert status == 200, payload
        assert payload["ok"] is True
        assert payload["word"] == "ephemeral"
        assert payload["wordId"] == vocab.added[0]["id"]
        assert payload["wordbookId"] == 7  # 未指定 wordbookId 时落到默认词本
        assert vocab.added[0]["word"] == "ephemeral"
        assert vocab.added[0]["source"] == "browser_extension"
        assert len(captured) == 1 and captured[0].payload["word"] == "ephemeral"
    finally:
        event_bus.off("vocabulary.word_added", cb)
        service.stop()


def test_browser_bridge_add_word_requires_text_and_token():
    port = _pick_free_port()
    service, vocab = _make_bridge_with_vocabulary(port)
    service.start()
    try:
        handshake_status, handshake_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://word-ext"},
            payload={"extensionId": "word-ext"},
        )
        assert handshake_status == 200

        empty_status, empty_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/add-word",
            method="POST",
            headers={"Origin": "chrome-extension://word-ext", "X-Bridge-Token": handshake_payload["sessionToken"]},
            payload={"text": "   "},
        )
        assert empty_status == 400
        assert empty_payload["error"] == "text_required"

        no_token_status, _ = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/add-word",
            method="POST",
            headers={"Origin": "chrome-extension://word-ext"},
            payload={"text": "ephemeral"},
        )
        assert no_token_status == 401
        assert vocab.added == []
    finally:
        service.stop()


def test_browser_bridge_add_word_reports_unavailable_vocabulary():
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(settings_repository=_FakeSettingsRepository(port))
    service.start()
    try:
        handshake_status, handshake_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://word-ext"},
            payload={"extensionId": "word-ext"},
        )
        assert handshake_status == 200
        status, payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/add-word",
            method="POST",
            headers={"Origin": "chrome-extension://word-ext", "X-Bridge-Token": handshake_payload["sessionToken"]},
            payload={"text": "ephemeral"},
        )
        assert status == 503
        assert payload["error"] == "vocabulary_unavailable"
    finally:
        service.stop()


# ── P0-1：白名单/握手闸门 ─────────────────────────────────────────────


def test_browser_bridge_stable_without_whitelist_handshake_is_403_not_500():
    """修复前：stable+空名单握手抛 ValueError 被兜成 500（插件只能显示"请求失败"）。"""
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(settings_repository=_FakeSettingsRepository(port, trusted_extension_ids=[], channel="stable"))
    service.start()
    try:
        status, payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://unlisted-ext"},
            payload={"extensionId": "unlisted-ext"},
        )
        assert status == 403
        assert payload["error"] == "extension_whitelist_required_in_stable"
        assert service.get_session_token() not in {"", None}
        assert "unlisted-ext" not in service._allowed_extension_ids
    finally:
        service.stop()


def test_browser_bridge_rejects_unregistered_extension_origin_on_business_routes():
    """修复前：空名单时任意 chrome-extension Origin 都在 Origin 层放行（白名单形同虚设）。"""
    port = _pick_free_port()
    service, vocab = _make_bridge_with_vocabulary(port)
    service.start()
    try:
        handshake_status, handshake_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://registered-ext"},
            payload={"extensionId": "registered-ext"},
        )
        assert handshake_status == 200
        token = handshake_payload["sessionToken"]

        foreign_status, foreign_payload = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/add-word",
            method="POST",
            headers={"Origin": "chrome-extension://impostor-ext", "X-Bridge-Token": token},
            payload={"text": "sneaky"},
        )
        assert foreign_status == 403
        assert foreign_payload["error"] == "origin_rejected"
        assert vocab.added == []

        ok_status, _ = _request_json(
            f"http://127.0.0.1:{port}/api/browser-assistant/add-word",
            method="POST",
            headers={"Origin": "chrome-extension://registered-ext", "X-Bridge-Token": token},
            payload={"text": "legit"},
        )
        assert ok_status == 200
    finally:
        service.stop()


def test_browser_bridge_handshake_reuses_valid_token_across_extensions():
    """修复前：每次握手无条件轮换全局 token，多扩展/多窗口互相吊销 → 随机 401。"""
    port = _pick_free_port()
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(port, trusted_extension_ids=["ext-aaa", "ext-bbb"], channel="preview")
    )
    service.start()
    base = f"http://127.0.0.1:{port}"
    try:
        first_status, first_payload = _request_json(
            f"{base}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://ext-aaa"},
            payload={"extensionId": "ext-aaa"},
        )
        second_status, second_payload = _request_json(
            f"{base}/api/browser-assistant/handshake",
            method="POST",
            headers={"Origin": "chrome-extension://ext-bbb"},
            payload={"extensionId": "ext-bbb"},
        )
        assert first_status == 200 and second_status == 200
        assert first_payload["sessionToken"] == second_payload["sessionToken"]

        for ext_id in ("ext-aaa", "ext-bbb"):
            status, _ = _request_json(
                f"{base}/api/browser-assistant/status",
                headers={"Origin": f"chrome-extension://{ext_id}", "X-Bridge-Token": first_payload["sessionToken"]},
            )
            assert status == 200, ext_id
    finally:
        service.stop()


# ── explain_question（侧栏聊天提问路径）─────────────────────────────────────
# 2026-10-17 回归锁：生产注入的 ai_service 是 JudgeAwareClient（只有 chat_completion、
# 无 explain_concept），旧实现直接调 explain_concept 导致该路径 100% AttributeError。


class _ExplainConceptFake:
    """旧接口形态：实现 explain_concept（兼容路径）。"""

    def __init__(self):
        self.calls = []

    def explain_concept(self, *, concept, context, page_text, max_tokens=800):
        self.calls.append((concept, context, page_text, max_tokens))
        return "旧接口解释结果"


class _JudgeAwareLikeFake:
    """生产形态：JudgeAwareClient 只有 chat_completion（OpenAI 风格 dict 返回）。"""

    def __init__(self):
        self.calls = []

    def chat_completion(self, messages, temperature=0.2, max_tokens=None, **kwargs):
        self.calls.append((messages, temperature, max_tokens))
        return {"choices": [{"message": {"content": "新接口解释结果"}}]}


def test_explain_question_uses_legacy_explain_concept_when_available():
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(_pick_free_port()),
    )
    fake = _ExplainConceptFake()
    service.ai_service = fake

    result = service.explain_question({"question": "什么是X", "context": "ctx", "pageText": "正文"})

    assert result["ok"] is True
    assert result["explanation"] == "旧接口解释结果"
    assert fake.calls[0][0] == "什么是X"


def test_explain_question_falls_back_to_chat_completion_for_judge_aware_client():
    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(_pick_free_port()),
    )
    fake = _JudgeAwareLikeFake()
    service.ai_service = fake

    result = service.explain_question({"question": "重点是什么", "context": "页面", "pageText": "正文内容"})

    assert result["ok"] is True
    assert result["explanation"] == "新接口解释结果"
    messages = fake.calls[0][0]
    assert messages[1]["role"] == "user"
    assert "重点是什么" in messages[1]["content"]


def test_explain_question_surfaces_empty_response_as_error():
    class _EmptyFake:
        def chat_completion(self, messages, temperature=0.2, max_tokens=None, **kwargs):
            return {"choices": []}

    service = BrowserAssistantBridgeService(
        settings_repository=_FakeSettingsRepository(_pick_free_port()),
    )
    service.ai_service = _EmptyFake()

    try:
        service.explain_question({"question": "q"})
    except RuntimeError as exc:
        assert "AI 未返回解释内容" in str(exc)
    else:
        raise AssertionError("empty AI response should surface as RuntimeError")
