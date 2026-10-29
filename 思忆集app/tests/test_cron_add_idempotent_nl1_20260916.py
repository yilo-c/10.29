# -*- coding: utf-8 -*-
"""NL-1 回归：cron_service.add_job 对完全相同规格的启用 job 必须幂等去重。

生产取证（2026-09-16）：真实 kv_store `cron_jobs` 中 `tool.zcode.open` 累积
8 条精确重复（`boundary-valid`×4 @ `0 9 * * *` + `boundary-rrule`×4 @
`FREQ=MONTHLY;BYDAY=MO`，全 enabled）——test_integration_boundary 直连真实
后端的 cron.add 残渣 + add_job 无幂等保护叠加所致。数据残渣已随
`data/archive/cron_boundary_residue_2026-09-16.jsonl` 清理；本测试锁死代码
层根治，防任何调用方（RPC cron.add / 未来的 _ensure_*）再叠出重复。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from services.cron_service import CronService


class _SettingsRepo:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


def _make_service(tmp_path):
    return CronService(
        data_store=LocalDataStore(base_dir=str(tmp_path)),
        executor=lambda action_id, payload: {"ok": True},
        settings_repository=_SettingsRepo({"cron_engine": "poll"}),
    )


def test_identical_spec_add_is_idempotent(tmp_path):
    """同名同 action 同 schedule 重复 add → 返回既有 job，总数保持 1。"""
    service = _make_service(tmp_path)
    first = service.add_job("boundary-valid", "tool.zcode.open", {}, schedule="0 9 * * *", repeat="cron")
    second = service.add_job("boundary-valid", "tool.zcode.open", {}, schedule="0 9 * * *", repeat="cron")
    assert first["job_id"] == second["job_id"], "幂等命中必须返回同一 job_id"
    assert len(service.list_jobs(include_disabled=True)) == 1


def test_persisted_dupes_converge_on_reload(tmp_path):
    """历史残渣形态（两条完全相同的持久化 job）重启加载后再 add 不增第三条。"""
    service = _make_service(tmp_path)
    service.add_job("boundary-valid", "tool.zcode.open", {}, schedule="0 9 * * *", repeat="cron")
    # 第二次 add 已被幂等挡住（仍 1 条）——强制构造残渣：绕过幂等直接
    # append 一条同规格 job 并落盘，模拟生产 kv 里的历史重复形态。
    from services.cron_service import CronJob

    service._jobs.append(
        CronJob(
            job_id="residue0001",
            name="boundary-valid",
            action_id="tool.zcode.open",
            payload={},
            schedule="0 9 * * *",
            repeat="cron",
        )
    )
    service._save_jobs()
    reopened = _make_service(tmp_path)
    assert len(reopened.list_jobs(include_disabled=True)) == 2
    again = reopened.add_job("boundary-valid", "tool.zcode.open", {}, schedule="0 9 * * *", repeat="cron")
    assert again["job_id"] == reopened.list_jobs(include_disabled=True)[0]["job_id"]
    assert len(reopened.list_jobs(include_disabled=True)) == 2, "add 幂等命中后不得新增"


def test_different_schedule_is_not_deduplicated(tmp_path):
    """schedule 不同 → 视为不同规格，照常注册（幂等不能误伤正常多任务）。"""
    service = _make_service(tmp_path)
    a = service.add_job("x", "info.collect", {}, schedule="0 8 * * *", repeat="cron")
    b = service.add_job("x", "info.collect", {}, schedule="0 20 * * *", repeat="cron")
    assert a["job_id"] != b["job_id"]
    assert len(service.list_jobs(include_disabled=True)) == 2


def test_payload_difference_is_not_deduplicated(tmp_path):
    """payload 不同 → 不同规格（如 ai_chat.collect 参数变更后重新播种）。"""
    service = _make_service(tmp_path)
    a = service.add_job("sync", "ai_chat.collect", {"max_sessions": 30}, schedule="0 8 * * *", repeat="cron")
    b = service.add_job("sync", "ai_chat.collect", {"max_sessions": 50}, schedule="0 8 * * *", repeat="cron")
    assert a["job_id"] != b["job_id"]


def test_disabled_existing_does_not_block_readd(tmp_path):
    """既有 job 被禁用 → 重新 add 视为新注册（幂等只挡启用态）。"""
    service = _make_service(tmp_path)
    first = service.add_job("patrol", "system.memory_health", {}, schedule="0 4 * * *", repeat="cron")
    service.toggle_job(first["job_id"], False)
    second = service.add_job("patrol", "system.memory_health", {}, schedule="0 4 * * *", repeat="cron")
    assert second["job_id"] != first["job_id"]
    assert len(service.list_jobs(include_disabled=True)) == 2
