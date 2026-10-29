"""Project health check & auto repair handlers — extracted from capability_router.py."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

logger = logging.getLogger("siyi.assistant.autonomy.router")


class ProjectRouterHost(Protocol):
    """ProjectHandlerMixin 依赖的宿主契约 — 由 MCPRouterMixin 提供 _get_subagent_manager。"""

    def _get_subagent_manager(self) -> Any: ...


class ProjectHandlerMixin:
    """Project health check and auto-repair capabilities."""

    if TYPE_CHECKING:
        def _get_subagent_manager(self) -> Any: ...

    def _project_health_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            root = Path(__file__).resolve().parent.parent
            script_path = root / "scripts" / "project_health_check.py"
            if not script_path.exists():
                return {"success": False, "reason": "health_check_script_not_found", "path": str(script_path)}

            import importlib.util
            spec = importlib.util.spec_from_file_location("project_health_check", str(script_path))
            if spec is None or spec.loader is None:
                return {"success": False, "reason": "import_failed"}
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            fmt = str(payload.get("format") or "summary").strip()
            report = mod.generate_report(output_json=(fmt == "json"))

            if fmt == "json":
                return {"success": True, "report": report}
            if fmt == "analyze":
                analysis = mod.generate_analysis(report)
                return {"success": True, "report": report, "analysis": analysis}

            s = report["summary"]
            issue_list = []
            for cap in report["capabilities"]:
                if cap["gaps"]:
                    issue_list.append(f"{cap['name']}: {'; '.join(cap['gaps'])}")
            return {
                "success": True,
                "summary": {
                    "total": s["total_capabilities"],
                    "ok": s["fully_aligned"],
                    "partial": s["partial"],
                    "missing": s["missing"],
                    "backend_missing": s["backend_missing_count"],
                    "doc_issues": s["doc_issues_count"],
                    "high_severity": s["high_severity_count"],
                },
                "issues": issue_list,
                "xiaowen_summary": mod.format_for_xiaowen(report),
            }
        except Exception as exc:
            logger.error("project.health_check failed: %s", exc, exc_info=True)
            return {"success": False, "reason": str(exc)}

    def _project_auto_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            root = Path(__file__).resolve().parent.parent
            script_path = root / "scripts" / "project_health_check.py"
            if not script_path.exists():
                return {"success": False, "reason": "health_check_script_not_found"}

            import importlib.util
            spec = importlib.util.spec_from_file_location("project_health_check", str(script_path))
            if spec is None or spec.loader is None:
                return {"success": False, "reason": "import_failed"}
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            report = mod.generate_report(output_json=False)
            analysis = mod.generate_analysis(report)

            scope = str(payload.get("scope") or "all").strip()
            filtered = [a for a in analysis if scope == "all" or a["type"].startswith(scope)]

            if not filtered:
                return {"success": True, "message": "无需修复", "total_analysis": len(analysis)}

            repair_plan = []
            steps = []
            for a in filtered:
                entry = {"type": a["type"], "fix": a["fix_approach"], "severity": a["severity"]}
                repair_plan.append(entry)
                hint = a.get("prompt_hint") or a["fix_approach"]
                steps.append({
                    "action_id": "shell.run",
                    "payload": {"command": f'echo "{hint[:200]}"'},
                })

            manager = self._get_subagent_manager()
            run_result = None
            if manager is not None and steps:
                run = manager.spawn(
                    workflow="execute_steps",
                    payload={"steps": steps, "parallel": True},
                    label=f"项目自动修复({scope}, {len(filtered)}项)",
                )
                run_result = run

            return {
                "success": True,
                "message": f"发现 {len(filtered)} 个可修复项" + ("" if not run_result else "，已提交到后台"),
                "repair_count": len(filtered),
                "repair_plan": repair_plan,
                "run": run_result,
            }
        except Exception as exc:
            logger.error("project.auto_repair failed: %s", exc, exc_info=True)
            return {"success": False, "reason": str(exc)}
