from __future__ import annotations

import copy
import logging
import os
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from assistant.models import PlannedStep
from core.observability import SEVERITY_INFO

logger = logging.getLogger("siyi.assistant.codex")


class CodexMissionOrchestrator:
    """Codex mission orchestrator for task planning and execution."""

    MAX_RUN_HISTORY = 300
    VERIFY_TIMEOUT_SECONDS = 180
    _DANGEROUS_VERIFY_PATTERNS = (
        r"\brm\s+-rf\s+/",
        r"\bformat\s+[a-zA-Z]:",
        r"\bdel\s+/[sfq]",
        r"\brd\s+/s\s+/q",
        r":\\\s*$",
        r"\bmkfs\b",
        r"\bdd\s+if=.*of=/dev/",
        r"\breg\s+delete\s+hk",
        r"\bnet\s+user\s+.*\s+/delete",
        r"\bshutdown\s+/[srp]",
    )
    _UNSAFE_VERIFY_TOKENS = ("&&", "||", "|", ">", "<", ";", "`", "$(")

    def __init__(
        self,
        *,
        data_store,
        planner,
        executor,
        security,
        memory,
        observability_service=None,
        settings_repository=None,
    ):
        self.data_store = data_store
        self.planner = planner
        self.executor = executor
        self.security = security
        self.memory = memory
        self.observability_service = observability_service
        self.settings_repository = settings_repository
        self.workspace_dir = Path(__file__).resolve().parent.parent

    def plan(self, goal: str, workspace_context: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_goal = str(goal or "").strip()
        task_id = f"codex-{uuid.uuid4().hex[:12]}"
        if not normalized_goal:
            return {
                "task_id": task_id,
                "goal": normalized_goal,
                "status": "invalid_goal",
                "steps": [],
                "approvals": [],
                "relevant_files": [],
                "context_summary": "",
            }

        steps = self.planner.plan_goal(normalized_goal)
        approvals = self._build_approval_preview(steps)
        relevant_files = self._select_relevant_files(steps)
        context_summary = self._compress_context(normalized_goal, steps, workspace_context)
        route_trace = self._build_route_trace(steps)

        return {
            "task_id": task_id,
            "goal": normalized_goal,
            "status": "planned",
            "steps": steps,
            "approvals": approvals,
            "relevant_files": relevant_files,
            "route_trace": route_trace,
            "context_summary": context_summary,
        }

    def run(
        self,
        goal: str,
        approved_actions: set | None = None,
        force: bool = False,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = dict(options or {})
        approved_actions = set(approved_actions or set())
        max_rounds = max(1, min(int(options.get("max_repair_rounds") or 3), 5))
        auto_verify = bool(options.get("auto_verify", True))
        verify_commands = options.get("verify_commands")

        plan_result = self.plan(goal, workspace_context=options.get("workspace_context"))
        task_id = plan_result.get("task_id", f"codex-{uuid.uuid4().hex[:12]}")
        steps = list(plan_result.get("steps") or [])

        if not steps:
            result = {
                "task_id": task_id,
                "goal": str(goal or "").strip(),
                "status": "no_steps",
                "summary": {"total": 0, "ok": 0, "failed": 0, "blocked": 0, "degraded": 0, "outcomes": []},
                "repair_rounds": 0,
                "verification": {"status": "skipped", "reason": "no_steps"},
                "approvals": plan_result.get("approvals") or [],
                "route_trace": plan_result.get("route_trace") or [],
                "relevant_files": plan_result.get("relevant_files") or [],
                "context_summary": plan_result.get("context_summary") or "",
            }
            self._store_run(result)
            return result

        pending_approvals = self._collect_pending_approvals(steps, approved_actions)
        if pending_approvals and not force:
            result = {
                "task_id": task_id,
                "goal": str(goal or "").strip(),
                "status": "pending_approval",
                "summary": {"total": 0, "ok": 0, "failed": 0, "blocked": 0, "degraded": 0, "outcomes": []},
                "repair_rounds": 0,
                "verification": {"status": "skipped", "reason": "waiting_approval"},
                "approvals": pending_approvals,
                "route_trace": plan_result.get("route_trace") or [],
                "relevant_files": plan_result.get("relevant_files") or [],
                "context_summary": plan_result.get("context_summary") or "",
            }
            self._store_run(result)
            return result

        rounds = 0
        current_steps = list(steps)
        final_summary: dict[str, Any] = {"total": 0, "ok": 0, "failed": 0, "blocked": 0, "degraded": 0, "outcomes": []}
        execution_rounds: list[dict[str, Any]] = []

        def _authorize_with_approval(step: PlannedStep, source: str = "assistant.codex"):
            return self.security.authorize_step(step, source=source, pre_approved=approved_actions, consume_quota=True)

        while current_steps and rounds < max_rounds:
            rounds += 1
            summary = self.executor.execute_plan(current_steps, authorize_callback=_authorize_with_approval)
            execution_rounds.append({"round": rounds, "step_count": len(current_steps), "summary": summary})
            final_summary = summary

            failed_action_ids = {
                str(outcome.get("action_id") or "")
                for outcome in (summary.get("outcomes") or [])
                if outcome.get("status") in {"failed", "timeout"}
            }
            if not failed_action_ids:
                break

            retry_steps = [step for step in steps if step.action_id in failed_action_ids]
            if not retry_steps:
                break
            current_steps = retry_steps

        verification = self._run_verification(final_summary, auto_verify=auto_verify, commands=verify_commands)
        status = self._resolve_status(final_summary, verification)

        result = {
            "task_id": task_id,
            "goal": str(goal or "").strip(),
            "status": status,
            "summary": final_summary,
            "repair_rounds": rounds,
            "execution_rounds": execution_rounds,
            "verification": verification,
            "approvals": plan_result.get("approvals") or [],
            "route_trace": plan_result.get("route_trace") or [],
            "relevant_files": plan_result.get("relevant_files") or [],
            "context_summary": plan_result.get("context_summary") or "",
        }
        self.memory.append_decision(goal, steps, final_summary)
        self._store_run(result)
        return result

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        runs = self.data_store.load_json("assistant_codex_runs", [])
        if not isinstance(runs, list):
            return []
        safe_limit = max(1, min(int(limit), 200))
        return runs[-safe_limit:]

    def _build_approval_preview(self, steps: list[PlannedStep]) -> list[dict[str, Any]]:
        previews: list[dict[str, Any]] = []
        for step in steps:
            decision = self.security.authorize_step(copy.deepcopy(step), source="assistant.codex.plan", consume_quota=False)
            previews.append(
                {
                    "step_id": step.step_id,
                    "action_id": step.action_id,
                    "allowed": bool(decision.get("allowed")),
                    "reason": str(decision.get("reason") or "ok"),
                    "risk_level": str(decision.get("risk_level") or step.risk_level),
                    "warnings": list(decision.get("warnings") or []),
                }
            )
        return previews

    def _collect_pending_approvals(self, steps: list[PlannedStep], approved_actions: set) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for step in steps:
            preview = self.security.authorize_step(
                copy.deepcopy(step),
                source="assistant.codex.precheck",
                pre_approved=approved_actions,
                consume_quota=False,
            )
            if preview.get("reason") == "manual_confirmation_required":
                pending.append(
                    {
                        "step_id": step.step_id,
                        "action_id": step.action_id,
                        "risk_level": str(preview.get("risk_level") or step.risk_level),
                        "warnings": list(preview.get("warnings") or []),
                        "payload": dict(step.payload or {}),
                    }
                )
        return pending

    def _compress_context(self, goal: str, steps: list[PlannedStep], workspace_context: dict[str, Any] | None) -> str:
        context = dict(workspace_context or {})
        selected = self._select_relevant_files(steps)
        action_ids = ", ".join(step.action_id for step in steps[:8])
        parts = [f"goal={str(goal or '').strip()[:120]}", f"step_count={len(steps)}", f"actions={action_ids}"]
        if selected:
            parts.append("files=" + ", ".join(selected[:6]))
        if context:
            topic = str(context.get("topic") or "").strip()
            if topic:
                parts.append(f"topic={topic[:80]}")
            focus = context.get("focus_file")
            if focus:
                parts.append(f"focus_file={str(focus)[:120]}")
        return " | ".join(parts)

    def _select_relevant_files(self, steps: list[PlannedStep]) -> list[str]:
        candidates: list[str] = []
        for step in steps:
            payload = dict(step.payload or {})
            for key in ("path", "file", "file_path", "target"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
            command = payload.get("command")
            if isinstance(command, str) and command.strip():
                for token in shlex.split(command, posix=False):
                    token_text = token.strip().strip('"').strip("'")
                    if token_text.lower().endswith((".py", ".md", ".txt", ".json", ".yaml", ".yml")):
                        candidates.append(token_text)

        normalized: list[str] = []
        seen = set()
        for candidate in candidates:
            resolved = candidate
            if not os.path.isabs(resolved):
                resolved = str((self.workspace_dir / resolved).resolve())
            try:
                relative = os.path.relpath(resolved, str(self.workspace_dir))
            except ValueError:
                relative = resolved
            normalized_path = relative.replace("\\", "/")
            if normalized_path not in seen:
                seen.add(normalized_path)
                normalized.append(normalized_path)
        return normalized[:30]

    def _build_route_trace(self, steps: list[PlannedStep]) -> list[dict[str, Any]]:
        routed: list[dict[str, Any]] = []
        for step in steps:
            route = "semantic"
            if step.action_id in {"file.read", "file.write"}:
                route = "text-search"
            elif step.action_id in {"shell.run", "app.launch"}:
                route = "global-scan"
            routed.append({"step_id": step.step_id, "action_id": step.action_id, "route": route})
        return routed

    def _run_verification(self, summary: dict[str, Any], *, auto_verify: bool, commands: Any) -> dict[str, Any]:
        if not auto_verify:
            return {"status": "skipped", "reason": "disabled"}
        outcomes = list(summary.get("outcomes") or [])
        touched_mutating_actions = any(
            item.get("action_id") in {"file.write", "shell.run", "app.launch"} and item.get("status") == "ok"
            for item in outcomes
        )
        if not touched_mutating_actions:
            return {"status": "skipped", "reason": "readonly_task"}

        verify_commands = self._resolve_verify_commands(commands)
        if not verify_commands:
            return {"status": "skipped", "reason": "no_verify_command"}

        results = []
        all_passed = True
        for command in verify_commands:
            command_text = str(command or "").strip()
            command_lower = command_text.lower()
            blocked = next((pattern for pattern in self._DANGEROUS_VERIFY_PATTERNS if re.search(pattern, command_lower)), None)
            if blocked is not None:
                all_passed = False
                results.append({
                    "command": command_text,
                    "passed": False,
                    "reason": f"dangerous_verify_command_blocked:{blocked}",
                })
                continue
            if any(token in command_text for token in self._UNSAFE_VERIFY_TOKENS):
                all_passed = False
                results.append(
                    {
                        "command": command_text,
                        "passed": False,
                        "reason": "unsafe_verify_syntax_blocked",
                    }
                )
                continue
            try:
                parsed_command = shlex.split(command_text, posix=os.name != "nt")
                if not parsed_command:
                    all_passed = False
                    results.append(
                        {
                            "command": command_text,
                            "passed": False,
                            "reason": "empty_verify_command",
                        }
                    )
                    continue
                completed = subprocess.run(
                    parsed_command,
                    shell=False,
                    cwd=str(self.workspace_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.VERIFY_TIMEOUT_SECONDS,
                    check=False,
                )
                passed = completed.returncode == 0
                if not passed:
                    all_passed = False
                results.append(
                    {
                        "command": command,
                        "passed": passed,
                        "returncode": completed.returncode,
                        "stdout": (completed.stdout or "")[-4000:],
                        "stderr": (completed.stderr or "")[-2000:],
                    }
                )
            except Exception as exc:
                logger.warning("verify command failed: %s | error=%s", command_text, exc)
                all_passed = False
                results.append({"command": command_text, "passed": False, "reason": str(exc)})
        return {"status": "passed" if all_passed else "failed", "results": results}

    def _resolve_verify_commands(self, commands: Any) -> list[str]:
        if isinstance(commands, list):
            prepared = [str(item).strip() for item in commands if str(item).strip()]
            return prepared[:10]
        if isinstance(commands, str) and commands.strip():
            return [commands.strip()]

        if self.settings_repository is not None:
            from core.settings_schema import KEY_ASSISTANT_CODEX_VERIFY_COMMANDS

            saved = self.settings_repository.get_setting(KEY_ASSISTANT_CODEX_VERIFY_COMMANDS, [])
            if isinstance(saved, list):
                prepared = [str(item).strip() for item in saved if str(item).strip()]
                if prepared:
                    return prepared[:10]

        smoke_script = self.workspace_dir / "思忆集app" / "smoke_check.py"
        if smoke_script.exists():
            return [f'"{sys.executable}" "{smoke_script}" --suite core']
        return []

    @staticmethod
    def _resolve_status(summary: dict[str, Any], verification: dict[str, Any]) -> str:
        if verification.get("status") == "failed":
            return "verify_failed"
        failed = int(summary.get("failed") or 0)
        blocked = int(summary.get("blocked") or 0)
        degraded = int(summary.get("degraded") or 0)
        if failed > 0:
            return "failed"
        if blocked > 0:
            return "blocked"
        if degraded > 0:
            return "degraded"
        return "success"

    def _store_run(self, payload: dict[str, Any]) -> None:
        run_entry = dict(payload)
        run_entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.data_store.update_json(
            "assistant_codex_runs",
            [],
            lambda runs: (list(runs) if isinstance(runs, list) else [])[-(self.MAX_RUN_HISTORY - 1):] + [run_entry],
        )
        self._track(
            "assistant.codex.run",
            SEVERITY_INFO,
            {
                "task_id": run_entry.get("task_id"),
                "status": run_entry.get("status"),
                "repair_rounds": run_entry.get("repair_rounds", 0),
            },
        )

    def _track(self, event_name: str, severity: str, payload: dict[str, Any]) -> None:
        if self.observability_service is None:
            return
        try:
            self.observability_service.track_event(event_name, severity=severity, payload=payload)
        except Exception:
            logger.warning("跟踪鍙??娴嬫€褌簨件失璐? %s", event_name)
