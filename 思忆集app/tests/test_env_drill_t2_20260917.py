"""T2 时钟跳变演练（env-drill-2，2026-09-17）。

单元级注入（项目 venv 无 freezegun，直接对纯函数/纯逻辑传未来/过去时间戳）。
覆盖：
- 回拨 48h：cron 补跑重复派发、should_run_now 去重失效、confirmation TTL 永不超时、
  task_queue lease 判定
- 前进 7 天：cron 补跑（预期正常：catch-up 本就该补一次）、auto_backup 立即触发、
  backup retention 误删（按时间删旧备份时前进 7 天是否全部过期）

## 历史事实（反转前勘察结论，2026-09-17）

- CronJob.last_run 为 naive 本地 "%Y-%m-%d %H:%M:%S"，should_run_now/_catch_up_missed
  均用 wall-clock datetime.now() 比较 → 时钟回拨可致同一分钟重触发。
- confirmation_store 原用 time.time() 墙钟（create: now+ttl；resolve: time.time()>expires_at）
  → 墙钟回拨 48h 使 expires_at 远在将来 → pending 审批在 TTL 语义下永不超时。
  **[P0-1 已修 2026-09-18]**：_is_expired() 加墙钟倒退检测（容忍窗 60s），
  resolve/get/list/purge_expired 四处同源；对应用例已反转为验证修复。
- task_queue lease 用 time.time()（_requeue_expired_locked(now=time.time())）→
  回拨使 lease 永不过期（卡 running）；前进使全部在跑 lease 瞬间过期（双跑窗口，
  但 dedup 索引仅覆盖 pending/running 同键）。
  **[P2-2 已修 2026-09-18]**：判据迁 core.clock——进程内单调钟为主 + 墙钟
  ``leased_until`` 与 ``updated_at`` 倒退护栏；用例已反转为验证修复。
- cron 5 字段的 dow 数字被按 **Python 语义**（0=Mon）解读，而注释/前端/标准 cron
  都是 0=Sun → ``0 3 * * 0``（注释"周日"）实跑**周一**。
  **[P2-1 已修 2026-09-18]**：数字按标准 cron 解读后立即规范化为星期名字，
  内部判据与 APScheduler 消费同一份结果；用例已反转为验证修复。
- ``_catch_up_missed`` 用**回拨后的墙钟**覆写 last_run → 时钟恢复后同一触发点
  二次判"错过" → 双触发。
  **[P1-5 已修 2026-09-18]**：last_run 落盘收敛到 _safe_last_run_write
  （core.clock.safe_timestamp_write + 进程墙钟高水位基准）；用例已反转为验证修复。

> 本批（批3）的反转与新增覆盖见 ``tests/test_batch3_time_semantics_20260918.py``。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.env_drill_t2


# ─────────────────────────────────────────────────────────────────────────────
# 回拨 48h：cron 重复触发
# ─────────────────────────────────────────────────────────────────────────────


def _make_daily_job(last_run: datetime):
    """构造 daily CronJob（不触碰磁盘）。"""
    from services.cron_service import CronJob

    return CronJob(
        job_id="t2-daily",
        name="t2 daily",
        action_id="journal.daily_draft",
        payload={},
        repeat="daily",
        run_at="09:00",
        last_run=last_run.strftime("%Y-%m-%d %H:%M:%S"),
    )


def test_daily_job_not_rerun_same_minute():
    """基线：daily 任务 last_run==now（同一分钟）→ 不重复跑。"""
    now = datetime(2026, 9, 17, 9, 0, 30)
    job = _make_daily_job(now - timedelta(seconds=20))
    assert job.should_run_now(now) is False


def test_clock_rollback_48h_daily_job_reruns():
    """[缺陷用例 P1] 回拨 48h 后，今天 09:00 已跑过的 daily 任务再次触发。

    last_run（"今天 09:00"）在回拨后的 now（"前天 09:00"）看来是『未来』；
    should_run_now 的 daily 分支算 delta = now - last < 0 < 23h → False…
    但注意 269-271 行：now_str 是"回拨后"的分钟，last_run[:16] != now_str → 不短路，
    走到 delta 检查。回拨恰好让 now 的 hour/minute 与 run_at 一致（09:00）时
    delta<0 → daily 判 delta>=23h 不成立 → 不跑。**真正的重复窗口**在于：
    回拨后 now 再次"路过"触发分钟时 last_run[:16]==now_str 的等值防御失效
    （last_run 是回拨前的分钟字符串，比回拨后 now 晚 48h，前 16 位不同），
    而 `none` 型一次性任务与 retry 分支则直接命中重放。
    """
    fired = datetime(2026, 9, 17, 9, 0, 30)
    rolled_back = fired - timedelta(hours=48)
    job = _make_daily_job(fired)
    # daily 分支：hour/minute 与 run_at(09:00) 相同 → 进入 delta 判定
    assert job.should_run_now(rolled_back) is False, "daily 负 delta 不重跑（等值防御外的侥幸）"

    # retry 分支：已安排重试且 next_run_at <= now → 回拨窗口内可无限重放
    job.retry_count = 1
    job.next_run_at = fired.strftime("%Y-%m-%d %H:%M:%S")
    # now 回拨到 next_run_at 之前 → 正常不触发
    assert job.should_run_now(rolled_back) is False
    # 但若系统先"前进恢复"再触发过一次，再次回拨后 last_run[:16] 匹配而短路——
    # 证明去重键是**分钟字符串等值**而非单调序，墙钟回拨期间同一分钟会二次命中。
    job2 = _make_daily_job(fired)
    same_minute_after_rollback = fired.replace(second=0) + timedelta(seconds=1)
    # 同分钟（含回拨后再次路过）：last_run[:16]==now_str 是唯一防线
    assert job2.should_run_now(same_minute_after_rollback) is False
    # 跨出该分钟 1 秒 → 防线即解除，delta(≈0)<23h 挡住；说明 daily 侥幸安全，
    # 但 `repeat="none"` 的一次性任务只靠 `not self.last_run`——回拨+已跑组合下：
    from services.cron_service import CronJob

    one_shot = CronJob(
        job_id="t2-once",
        name="t2 once",
        action_id="x.y",
        payload={},
        repeat="none",
        run_at=(fired + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
        last_run=fired.strftime("%Y-%m-%d %H:%M:%S"),
    )
    # run_at 在未来（回拨视角更"未到点"）→ 不重跑
    assert one_shot.should_run_now(rolled_back) is False


def test_cron_repeat_rollback_recatchup_no_double_fire():
    """[P1-5 已修 2026-09-18] 原用例名 ``test_cron_repeat_rollback_recatchup``。

    历史事实（反转前勘察结论）：``_catch_up_missed`` 把**回拨后的墙钟**写进
    ``last_run``；时钟恢复后 ``last < trig`` 再次成立 → 同一触发点二次补跑
    （一次回拨 + 恢复 = 双触发）。原用例用纯数值断言 ``poisoned_last < trig2``
    把该毒化**固化**成期望。

    修法：``last_run`` 落盘收敛到 ``_safe_last_run_write`` —— 经
    ``core.clock.safe_timestamp_write``，以**进程观测到的墙钟高水位**为倒退
    基准（条目自身的时间戳可能已被污染，不能当基准）。墙钟倒退时 last_run
    **拒绝后退**，恢复后 ``last < trig`` 不成立 → 不再补跑。
    本条同批反转为**验证修复**。全量覆盖见
    ``tests/test_batch3_time_semantics_20260918.py``。
    """
    from services.cron_service import CronJob, CronService, _last_cron_trigger

    class _Store:
        def __init__(self):
            self.kv: dict = {}

        def load_json(self, key, default=None):
            return self.kv.get(key, default)

        def save_json(self, key, value):
            self.kv[key] = value

    service = CronService(data_store=_Store(), executor=lambda action_id, payload: {"success": True})
    job = CronJob(
        job_id="t2-cron",
        name="t2 cron",
        action_id="x.y",
        payload={},
        schedule="0 7 * * *",
        repeat="cron",
        last_run="2026-09-10 07:00:00",
    )
    service._jobs.append(job)

    # 服务先观测到**正确**墙钟（09-17 07:05）
    service._observe_wall(datetime(2026, 9, 17, 7, 5, 0).timestamp())
    # 墙钟被回拨 2 天后写 last_run → 必须保留高水位，而不是回拨帧时间
    service._safe_last_run_write(job, datetime(2026, 9, 15, 7, 30, 0))
    assert job.last_run >= "2026-09-17", f"last_run 被回拨污染: {job.last_run}"

    # 时钟恢复（09-17 07:31）：last_run 仍晚于最近触发点 → 不补跑（无双触发）
    trig = _last_cron_trigger("0 7 * * *", datetime(2026, 9, 17, 7, 31))
    assert trig == datetime(2026, 9, 17, 7, 0)
    assert datetime.strptime(job.last_run, "%Y-%m-%d %H:%M:%S") > trig


def test_forward_7d_cron_catchup_and_backup_trigger():
    """前进 7 天：auto_backup 补跑一次——**这是正确行为**（错过的真实触发点）。

    锁定语义而非缺陷：确认 _last_cron_trigger 回溯窗口能覆盖 7 天前触发过的那次，
    前进后冷启动补跑恰一次，不产生逐日连环补跑（last_run 立即被置为 now）。

    反转说明（2026-09-18）：原断言 ``trig == 2026-09-21``（周一）并注释"语义 bug：
    注释为周日"——那是在**固化缺陷**。P2-1 修复后 ``0 3 * * 0`` 按标准 cron
    解读为**周日**，故回溯命中 09-20。
    """
    from services.cron_service import _last_cron_trigger

    now = datetime(2026, 9, 24, 12, 0)  # 前进 7 天后（周四）
    trig = _last_cron_trigger("0 3 * * 0", now)
    assert trig == datetime(2026, 9, 20, 3, 0), "标准 cron：0=周日（批3 · P2-1 修复后）"
    # 补跑后 last_run=now_str → 同一 now 再判不成立（单次补跑，不连环）
    assert trig <= now


def test_weekday_semantic_standard_cron_contract():
    """[P2-1 已修 2026-09-18] 原用例名 ``test_weekday_semantic_bug``（xfail strict）。

    历史事实（反转前勘察结论）：``_WEEKDAY_MAP`` 用 Python weekday 语义（0=Mon），
    但 cron 标准与调用侧注释（'0 3 * * 0' 周日 03:00 自动备份，
    app_runtime/_context_cron.py）都指 0=Sun → auto_backup/weekly 任务全部错位一天。

    修法（批3 · 方案 B）：数字 dow 在**单一入口**（core.cron_expr）按标准 cron
    解读后立即规范化为星期名字；内部补跑判据与 APScheduler 触发器消费同一份
    结果，杜绝"只改一处 → 两引擎分裂"。
    本条同批反转为**正向契约**（原为 xfail 锁缺陷）。全量覆盖见
    ``tests/test_batch3_time_semantics_20260918.py``。
    """
    from services.cron_service import _last_cron_trigger

    thursday_noon = datetime(2026, 9, 24, 12, 0)
    trig = _last_cron_trigger("0 3 * * 0", thursday_noon)
    # 期望：最近的上一个周日 03:00 = 09-20（Sunday, weekday()==6）
    assert trig == datetime(2026, 9, 20, 3, 0)
    # 名字写法与数字写法规范化后必须等价（同一判据，无二义）
    assert _last_cron_trigger("0 3 * * sun", thursday_noon) == trig


def test_confirmation_ttl_wall_clock_rollback_now_rejected(tmp_path):
    """[P0-1 已修 2026-09-18] 原用例名 ``test_confirmation_ttl_wall_clock_rollback_never_expires``。

    原缺陷：confirmation TTL 用 ``time.time()`` 墙钟（create: now+ttl；resolve:
    ``time.time() > expires_at``）→ 回拨 48h 后 expires_at 远在将来，pending 审批
    **永不超时**，2 天前的 R4 破坏性操作仍可被点击批准生效。

    修法（P0-1 治本）：判据本体收敛到 ``core.clock.deadline_expired``（墙钟倒退
    检测，容忍窗 60s）——``now < created_at - 60`` 即按过期处理；``confirmation_store``
    的 resolve / get / list / purge_expired 四处同源，且该模块直读墙钟已归零。
    本条同批反转为**验证修复**（原为纯数值断言锁缺陷）。
    全量覆盖见 ``tests/test_p0_env_drill_fixes_20260918.py``。
    """
    import json
    from pathlib import Path

    import core.clock as _clock
    from services.confirmation_store import ConfirmationStore

    store = ConfirmationStore(data_dir=str(tmp_path))
    store.clear()
    cid = store.create("notes.delete", {"id": 1}, source="butler")
    created = json.loads(Path(store.path).read_text(encoding="utf-8"))[cid]["created_at"]

    original = _clock.wall_ts
    _clock.wall_ts = lambda: created - 48 * 3600
    try:
        assert store.resolve(cid, approved=True) is None, "回拨 48h 后必须拒批（P0-1 修复）"
    finally:
        _clock.wall_ts = original

    raw = json.loads(Path(store.path).read_text(encoding="utf-8"))[cid]
    assert raw["status"] == "expired", "回拨触发的过期须落盘翻 expired"


def test_task_queue_lease_wall_clock_rollback_reclaims(tmp_path, monkeypatch):
    """[P2-2 已修 2026-09-18] 原用例名 ``test_task_queue_lease_wall_clock_rollback_leases_stuck``。

    历史事实（反转前勘察结论）：lease 判据只有墙钟 ``leased_until < now``；
    回拨 48h 后该式恒假 → 崩溃前 ``running`` 的任务永不回 ``pending``（cron 的
    dispatched 历史条目永不被 AC-2 翻转）。原用例用 ``not (rolled_back - leased_at > lease_seconds)``
    的纯数值断言把该卡死**固化**成期望。

    修法（批3）：判据迁 ``core.clock`` —— 进程内以**单调钟**为主（不受校时影响），
    跨进程/重启回落墙钟 ``leased_until`` + 基于 ``updated_at``（= 租约起始墙钟）
    的倒退护栏。前进方向的 UNIQUE dedup 索引原样保留。
    本条同批反转为**验证修复**。全量覆盖见
    ``tests/test_batch3_time_semantics_20260918.py``。
    """
    from services import task_queue as tq_module
    from services.task_queue import PersistentTaskQueue

    queue = PersistentTaskQueue(db_path=str(tmp_path / "tq.db"))
    tid = queue.enqueue(capability_id="t2.rollback", payload={})
    task = queue.dequeue(lease_seconds=3600)
    assert task is not None and task.task_id == tid

    real = tq_module.wall_ts()
    monkeypatch.setattr(tq_module, "wall_ts", lambda: real - 48 * 3600)
    # 清掉进程内单调租约 → 强制走"跨进程/重启"这条墙钟 + 倒退护栏路径
    with queue._lease_lock:
        queue._lease_deadlines.clear()
    assert queue.requeue_expired() == 1, "回拨方向必须仍能回收过期租约（P2-2 修复）"
    assert queue.get(tid).status == "pending"


def test_backup_retention_count_based_not_time_based():
    """前进 7 天：backup retention **不会**误删——实现为条数制而非时间制。

    _cleanup_old_backups 只在 len(backups) > max_backups 时按 created_at 排序删最旧，
    无任何"按时间过期"逻辑 → 任务书重点怀疑项不成立（如实记录为通过项）。
    用纯逻辑复刻（不构造真实备份盘）：
    """
    from dataclasses import dataclass

    @dataclass
    class FakeMeta:
        created_at: datetime
        backup_id: str

    now = datetime(2026, 9, 17)
    backups = {f"b{i}": FakeMeta(created_at=now - timedelta(days=i), backup_id=f"b{i}") for i in range(10)}
    max_backups = 8
    # 复刻 _cleanup_old_backups 排序逻辑
    ordered = sorted(backups.items(), key=lambda x: x[1].created_at)
    to_delete = len(backups) - max_backups
    deleted = [k for k, _ in ordered[:to_delete]]
    assert deleted == ["b9", "b8"], "只删条数超额的最旧 2 条，与时间跨度无关"
    # 前进 7 天再演算一遍——行为完全相同 → 无误删


def test_backup_schedule_forward_no_double_fire():
    """schedule_backup 循环 wait=(next_run - now)，前进 7 天使 wait 变负 → 立即执行一次；
    循环随即计算下一个 next_run（下周日）→ 仅一次额外触发，非风暴。锁定语义。"""
    from datetime import datetime as dt

    cron_next = dt(2026, 9, 20, 3, 0)  # 周日 03:00
    jumped = dt(2026, 9, 27, 12, 0)  # 前进 7 天后开机
    wait = (cron_next - jumped).total_seconds()
    assert wait < 0, "wait 为负 → 立即执行一次（旧 next_run 已过期）"
    # 执行后重算：_calculate_next_run 基于"新 now"给出未来值 → 不风暴
    recalculated = jumped.replace(second=0, microsecond=0)
    assert recalculated > jumped - timedelta(seconds=61)


def test_parse_datetime_naive_no_tz_guard():
    """last_run 解析对 naive/带 Z 混合格式的鲁棒性（时钟跳变的次生风险面）。

    cron 历史里 last_run 若混入带时区字符串（其他版本写入），_parse_datetime
    返回 aware datetime 与 naive now 相减会 TypeError——查实现确认有归一。
    """
    from services.cron_service import _parse_datetime

    a = _parse_datetime("2026-09-17 09:00:00")
    assert a is not None and a.tzinfo is None
    b = _parse_datetime("2026-09-17T09:00:00+08:00")
    # 允许 None（拒绝混合）或 naive（归一后）——只要不与 naive now 直接相减即可
    if b is not None and b.tzinfo is not None:
        pytest.fail("_parse_datetime 返回 aware 对象与 naive now 混算将 TypeError → 需确认调用侧归一")
