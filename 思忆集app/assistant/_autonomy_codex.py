"""Codex mission mixin for AutonomousAssistantManager."""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

logger = logging.getLogger("siyi.assistant.autonomy.codex")


class CodexMixin:
    """Codex 任务混入 — 规划、执行、列出 Codex 任务。"""

    if TYPE_CHECKING:
        # 宿主 AutonomousAssistantManager 构造期注入的属性/服务
        codex: Any
        planner: Any
        ai_client: Any
        observability_service: Any

    def plan_codex_goal(
        self, goal: str, workspace_context: dict[str, object] | None = None
    ) -> dict[str, object]:
        self._ensure_planner_ai_client()
        return self.codex.plan(goal, workspace_context=workspace_context)

    def run_codex_goal(
        self,
        goal: str,
        approved_actions: set | None = None,
        force: bool = False,
        options: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self._ensure_planner_ai_client()
        return self.codex.run(
            goal, approved_actions=approved_actions, force=force, options=options
        )

    def list_codex_runs(self, limit: int = 20) -> list[dict[str, object]]:
        return self.codex.list_runs(limit=limit)

    def _ensure_planner_ai_client(self) -> None:
        if getattr(self.planner, "_ai_client", None) is not None:
            return
        if self.ai_client is not None:
            self.planner.set_ai_client(self.ai_client)
            logger.info("Planner AI client reused from existing client")
            return
        try:
            from services.ai_service import (
                OpenAICompatibleClient,
                resolve_openai_compatible_config,
            )

            config = resolve_openai_compatible_config()
            if config.api_key and config.base_url and config.model:
                client = OpenAICompatibleClient(
                    api_key=config.api_key,
                    api_base=config.base_url,
                    model=config.model,
                    timeout=config.timeout,
                )
                self.planner.set_ai_client(client)
                logger.info("Planner AI client connected: %s", config.base_url)
        except Exception as exc:
            logger.warning(
                "Cannot create AI client for Planner, falling back to keyword matching: %s",
                exc,
            )
            if self.observability_service:
                self.observability_service.track_exception(
                    "assistant.planner_client_failed", exc
                )
