"""B-09 防回归：Worker download 同秒窗口契约（静态断言）。

背景（audit-report-2026-08-27 B-09）：worker download 原 `updated_at > since`
严格大于 + 游标取最后成功条目秒级时间戳 → 设备 A 在 T 秒 push notes、设备 B
同 T 秒 push todos，桌面 pull 拿到 A 后 cursor=T，下次 `> T` 永远拿不到 B
（同秒更新丢失窗口）。

修复：worker 改 `>= since` + SELECT 增补 device_id（客户端按 (resource, device_id)
去重 + LWW 幂等吸收重复合并）。

本测试对 worker 源码做静态断言（防该 SQL 被改回 `>` / device_id 被删）；
同秒可达性的客户端行为由 test_cloud_sync_client.py::test_pull_same_second_items_both_reachable_b09 覆盖。
"""

from __future__ import annotations

import re
from pathlib import Path

# 仓库根（本文件位于 思忆集app/tests/ → parents[2]）
REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_SYNC_TS = REPO_ROOT / "cloudflare" / "worker" / "src" / "sync.ts"


def test_worker_download_uses_gte_since_b09() -> None:
    """B-09: download SQL 必须用 `updated_at >= ?`（严格大于会丢同秒条目）。"""
    assert WORKER_SYNC_TS.is_file(), f"worker sync.ts 不存在: {WORKER_SYNC_TS}"
    src = WORKER_SYNC_TS.read_text(encoding="utf-8")
    assert "updated_at >= ?" in src, "worker download 必须用 `updated_at >= ?`（B-09 同秒窗口修复）；若改回 `> ?`，同秒 push 的条目会被游标永久跳过"
    assert "updated_at > ?" not in src.replace("updated_at >= ?", ""), "download 路径不得残留严格大于 `updated_at > ?`（会丢同秒条目）"


def test_worker_download_returns_device_id_b09() -> None:
    """B-09: download SELECT 必须返回 device_id（客户端去重键 = resource + device_id）。"""
    src = WORKER_SYNC_TS.read_text(encoding="utf-8")
    assert "resource, device_id" in src, "worker download SELECT 必须包含 device_id 列；客户端按 (resource, device_id) 去重依赖它"


def test_worker_upload_whitelist_includes_info_channels_t7() -> None:
    """T7 缺口 1: ALLOWED_RESOURCES 必须放行 info_brief / info_sections。

    缺失时资讯简报/板块配置的跨设备 upload 被 Worker 恒 400（invalid resource）。
    """
    src = WORKER_SYNC_TS.read_text(encoding="utf-8")
    m = re.search(r"ALLOWED_RESOURCES\s*=\s*new Set\(\[([^\]]*)\]\)", src)
    assert m, "sync.ts 必须保留 ALLOWED_RESOURCES 白名单定义"
    allowed = {s.strip().strip("'\"") for s in m.group(1).split(",")}
    assert allowed >= {"notes", "todos", "settings", "info_brief", "info_sections"}, (
        "Worker resource 白名单必须包含 info_brief/info_sections"
        "（与 sync/_cloud_sync_crypto.py RESOURCE_INFO_* 键名一致），"
        "否则资讯扩展频道 upload 恒 400"
    )


def test_worker_upload_checks_client_version_t7() -> None:
    """T7 缺口 2: Worker upload 必须读取 body.client_version 做版本协商。

    客户端侧（standalone 推送补发字段）由
    test_cloud_sync_client.py::test_push_standalone_sends_client_version_t7 覆盖。
    """
    src = WORKER_SYNC_TS.read_text(encoding="utf-8")
    assert "rejectStaleVersion(body.client_version, env)" in src, "worker upload 必须用 body.client_version 做 min_client_version 协商"
    assert "client_version?: string" in src, "SyncUploadBody 契约必须声明 client_version 字段"
