from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app_runtime.app_context import AppContext
from assistant.autonomy import PlannedStep


def test_precheck_does_not_consume_permission_quota(tmp_path):
    context = AppContext(data_base_dir=str(tmp_path), headless=True)
    step = PlannedStep(
        step_id="s1",
        action_id="news.lookup",
        payload={"query": "AI", "limit": 2},
        risk_level="low",
    )

    before = context.autonomous_assistant_manager.get_remaining_quota("news.lookup")
    checks = context.autonomous_assistant_manager.pre_check_steps([step])
    after = context.autonomous_assistant_manager.get_remaining_quota("news.lookup")

    assert checks
    assert checks[0]["auth"]["allowed"] is True
    assert before == after


def test_codex_goal_requires_manual_approval_for_high_risk_action(tmp_path):
    context = AppContext(data_base_dir=str(tmp_path), headless=True)

    context.autonomous_assistant_manager.planner.plan_goal = lambda goal: [
        PlannedStep(
            step_id="kill1",
            action_id="process.kill",
            payload={"name": "notepad.exe"},
            risk_level="high",
        )
    ]

    result = context.autonomous_assistant_manager.run_codex_goal("结束一个进程", options={"auto_verify": False})

    assert result["status"] == "pending_approval"
    assert len(result["approvals"]) == 1
    assert result["approvals"][0]["action_id"] == "process.kill"


def test_codex_goal_runs_after_approval_and_persists_run(tmp_path):
    context = AppContext(data_base_dir=str(tmp_path), headless=True)

    context.autonomous_assistant_manager.planner.plan_goal = lambda goal: [
        PlannedStep(
            step_id="shell1",
            action_id="shell.run",
            payload={"command": "echo hello", "timeout": 5},
            risk_level="high",
        )
    ]

    context.autonomous_assistant_manager.executor.execute_plan = lambda steps, authorize_callback: {
        "executed_at": "2026-04-03T00:00:00",
        "total": len(steps),
        "ok": len(steps),
        "failed": 0,
        "blocked": 0,
        "degraded": 0,
        "outcomes": [
            {
                "step_id": steps[0].step_id,
                "action_id": steps[0].action_id,
                "status": "ok",
                "attempt": 1,
                "payload": {"returncode": 0},
            }
        ],
    }

    result = context.autonomous_assistant_manager.run_codex_goal(
        "执行一个命令",
        approved_actions={"shell.run"},
        options={"auto_verify": False, "max_repair_rounds": 2},
    )

    assert result["status"] == "success"
    assert result["summary"]["ok"] == 1

    history = context.autonomous_assistant_manager.list_codex_runs(limit=5)
    assert history
    assert history[-1]["task_id"] == result["task_id"]


def test_codex_goal_blocks_unsafe_verify_shell_syntax(tmp_path):
    context = AppContext(data_base_dir=str(tmp_path), headless=True)

    context.autonomous_assistant_manager.planner.plan_goal = lambda goal: [
        PlannedStep(
            step_id="shell1",
            action_id="shell.run",
            payload={"command": "echo hello", "timeout": 5},
            risk_level="high",
        )
    ]

    context.autonomous_assistant_manager.executor.execute_plan = lambda steps, authorize_callback: {
        "executed_at": "2026-04-03T00:00:00",
        "total": len(steps),
        "ok": len(steps),
        "failed": 0,
        "blocked": 0,
        "degraded": 0,
        "outcomes": [
            {
                "step_id": steps[0].step_id,
                "action_id": steps[0].action_id,
                "status": "ok",
                "attempt": 1,
                "payload": {"returncode": 0},
            }
        ],
    }

    result = context.autonomous_assistant_manager.run_codex_goal(
        "执行一个命令",
        approved_actions={"shell.run"},
        options={"verify_commands": ["echo ok && whoami"]},
    )

    assert result["status"] == "verify_failed"
    assert result["verification"]["status"] == "failed"
    assert result["verification"]["results"][0]["reason"] == "unsafe_verify_syntax_blocked"
