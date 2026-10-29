"""T1.6 契约测试：hub personal 域镜像锁（桌面权威源收敛）。

V2-01（2026-08-24）起**默认开启**：
  - 未显式设置时，personal 写入端点（POST/PUT/DELETE）拒绝缺少桌面同步来源头的请求（403）
  - 接受携带 X-Siyi-Sync-Source: desktop 的请求
  - SIYI_HUB_PERSONAL_MIRROR_LOCK=0 显式关闭时恢复向后兼容（无头写入可用）
"""
from __future__ import annotations

import pytest

from automation_hub.api.routes.personal_factory import (
    PERSONAL_MIRROR_SYNC_HEADER,
    PERSONAL_MIRROR_SYNC_VALUE,
    personal_mirror_lock_enabled,
)
from automation_hub.tests.support import bootstrap_headers, configure_test_env, make_client

NOTE_PAYLOAD = {
    "id": 901,
    "title": "Mirror Lock Draft",
    "content": "<p>draft</p>",
    "tags": ["desktop", "draft"],
    "category": "笔记",
    "folder_path": "/sync",
    "favorite": True,
    "created_at": "2026-03-11T12:00:00+00:00",
    "updated_at": "2026-03-11T12:10:00+00:00",
    "version": 1,
    "device_id": "desktop-a",
    "sync_state": "local_only",
}


def _disable_lock(monkeypatch):
    monkeypatch.setenv("SIYI_HUB_PERSONAL_MIRROR_LOCK", "0")


def test_lock_default_on_rejects_without_sync_source(tmp_path, monkeypatch):
    """V2-01：默认（不设环境变量）即锁定——无桌面来源头的写入被拒。"""
    configure_test_env(tmp_path, monkeypatch)
    # 环境变量未被设置（configure_test_env 不设该变量），无需显式断言清理
    assert personal_mirror_lock_enabled() is True
    client = make_client()
    headers = bootstrap_headers(client)

    resp = client.post("/personal/notes", headers=headers, json=NOTE_PAYLOAD)
    assert resp.status_code == 403, resp.text


def test_create_rejected_without_sync_source(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    resp = client.post("/personal/notes", headers=headers, json=NOTE_PAYLOAD)
    assert resp.status_code == 403, resp.text


def test_create_allowed_with_sync_source(tmp_path, monkeypatch):
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)
    headers[PERSONAL_MIRROR_SYNC_HEADER] = PERSONAL_MIRROR_SYNC_VALUE

    resp = client.post("/personal/notes", headers=headers, json=NOTE_PAYLOAD)
    assert resp.status_code == 200, resp.text
    note = resp.json()["note"]
    assert note["remote_id"]

    # 锁定状态下，无来源的 PUT/DELETE 同样被拒（复用已 bootstrap 的 headers，
    # 不再二次 bootstrap —— 否则同库二次初始化返回 409）
    remote_id = note["remote_id"]
    bad_headers = dict(headers)
    bad_headers.pop(PERSONAL_MIRROR_SYNC_HEADER, None)
    bad_put = client.put(
        f"/personal/notes/{remote_id}",
        headers=bad_headers,
        json={**NOTE_PAYLOAD, "remote_id": remote_id, "title": "x"},
    )
    assert bad_put.status_code == 403, bad_put.text


def test_lock_off_keeps_backward_compat(tmp_path, monkeypatch):
    # V2-01 迁移期回退：SIYI_HUB_PERSONAL_MIRROR_LOCK=0 显式关闭锁 → 无头写入可用
    _disable_lock(monkeypatch)
    configure_test_env(tmp_path, monkeypatch)
    client = make_client()
    headers = bootstrap_headers(client)

    resp = client.post("/personal/notes", headers=headers, json=NOTE_PAYLOAD)
    assert resp.status_code == 200, resp.text
