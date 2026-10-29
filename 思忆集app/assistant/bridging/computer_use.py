"""Computer Use Agent — dynamic execution loop for desktop automation.

Screenshot → LLM decides next action → execute → observe → repeat.
Built on top of DesktopAutomationCapabilityDomain capabilities.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from assistant.models import PlannedStep
from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.assistant.computer_use")

# ── System prompts for the LLM decision loop ──────────────────

COMPUTER_USE_SYSTEM_PROMPT = """You are a Computer Use AI agent controlling a Windows desktop.

## Goal
{goal}

## Current Screen
Screen resolution: {width}x{height}
OCR text from screen:
```
{ocr_text}
```

## Actions Taken So Far
{history}

## Available Actions — return ONE as a JSON object

### mouse_click
Click at screen coordinates (coordinate fallback — prefer mouse_click_text).
{{"type": "mouse_click", "reasoning": "why this action", "x": <int>, "y": <int>, "button": "left"|"right", "clicks": 1|2|3}}

### mouse_click_text
Click on text found via OCR — use this instead of guessing coordinates.
{{"type": "mouse_click_text", "reasoning": "why", "text": "<text to click, e.g. '保存'>", "button": "left"|"right", "clicks": 1|2}}

### keyboard_type
Type text at current keyboard focus.
{{"type": "keyboard_type", "reasoning": "why", "text": "<string>", "enter": true|false}}

### keyboard_press
Press key combination.
{{"type": "keyboard_press", "reasoning": "why", "keys": ["ctrl", "c"]}}

### window_find
Find a window by title (use this first before activating).
{{"type": "window_find", "reasoning": "why", "title": "<window title keyword>"}}

### window_activate
Activate a window by handle from window_find result.
{{"type": "window_activate", "reasoning": "why", "hwnd": <int>}}

### done
Mark the task as complete.
{{"type": "done", "reasoning": "<summary of what was accomplished>"}}

### error
Report that the task cannot be completed.
{{"type": "error", "reasoning": "<reason>"}}

## Rules
1. Return ONLY a single JSON object. No markdown, no explanation.
2. Each action must be one atomic step — do one click, one text entry, etc.
3. **Prefer mouse_click_text over mouse_click** — use text labels instead of guessing pixel coordinates.
4. To type into a field: first click the field (or click its label), then type the text.
5. Use window_find to locate applications, then window_activate to bring them to front.
6. The OCR text shows what's currently visible — read it carefully.
7. When finished, use "done" with a brief summary.
8. If stuck or the goal is impossible, use "error" with the reason."""

COMPUTER_USE_VISION_PROMPT = """You are a Computer Use AI agent controlling a Windows desktop. You can SEE the current screen as an image.

## Goal
{goal}

## Screen
Resolution: {width}x{height}
The screenshot is attached as an image — examine it carefully.

OCR text extracted from screen (may be incomplete):
```
{ocr_text}
```

## Actions Taken So Far
{history}

## Available Actions — return ONE as a JSON object

### mouse_click
Click at screen coordinates (coordinate fallback — prefer mouse_click_text).
{{"type": "mouse_click", "reasoning": "why this action", "x": <int>, "y": <int>, "button": "left"|"right", "clicks": 1|2|3}}

### mouse_click_text
Click on text found via OCR — use this instead of guessing coordinates.
{{"type": "mouse_click_text", "reasoning": "why", "text": "<text to click, e.g. '保存'>", "button": "left"|"right", "clicks": 1|2}}

### keyboard_type
Type text at current keyboard focus.
{{"type": "keyboard_type", "reasoning": "why", "text": "<string>", "enter": true|false}}

### keyboard_press
Press key combination.
{{"type": "keyboard_press", "reasoning": "why", "keys": ["ctrl", "c"]}}

### window_find
Find a window by title.
{{"type": "window_find", "reasoning": "why", "title": "<window title keyword>"}}

### window_activate
Activate a window by handle.
{{"type": "window_activate", "reasoning": "why", "hwnd": <int>}}

### done
Mark the task as complete.
{{"type": "done", "reasoning": "<summary>"}}

### error
Report unrecoverable error.
{{"type": "error", "reasoning": "<reason>"}}

## Rules
1. LOOK at the screenshot image carefully before deciding.
2. Return ONLY a single JSON object. No markdown, no explanation.
3. Each action must be one atomic step.
4. **Prefer mouse_click_text over mouse_click** — click by text label, not by guessing coordinates.
5. To type into a field: click the field label or input box first, then type.
6. When done, use "done" with a summary.
7. If stuck, use "error" with the reason."""


class ComputerUseEngine:
    """Dynamic Computer Use Agent loop.

    Captures screen state, asks the LLM what to do next, executes
    the action, observes the result, and repeats until done.
    """

    ACTION_MAP: dict[str, str] = {
        "mouse_click": "mouse.click",
        "mouse_click_text": "mouse.click_text",
        "keyboard_type": "keyboard.type",
        "keyboard_press": "keyboard.press",
        "window_find": "window.find",
        "window_activate": "window.activate",
    }

    COMPUTER_USE_ACTIONS: frozenset[str] = frozenset({
        "screen.read", "mouse.click", "mouse.click_text",
        "keyboard.type", "keyboard.press",
        "window.find", "window.activate",
    })

    def __init__(self, capability_router: Any, ai_client: Any) -> None:
        self.capability_router = capability_router
        self.ai_client = ai_client

    def _has_vision(self) -> bool:
        """Check if the AI client supports vision (image input)."""
        return hasattr(self.ai_client, "chat_completion_vision")

    def execute(
        self,
        goal: str,
        max_iterations: int = 30,
        authorize_callback: Any | None = None,
        use_vision: bool | None = None,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Run the computer-use loop until completion or error.

        Args:
            goal: High-level natural language goal.
            max_iterations: Maximum loop iterations (capped at 100).
            authorize_callback: Optional auth callback for each action step.
            use_vision: Whether to send screenshots to the LLM as images.
                        None = auto-detect (True if client supports it).
            cancellation_event: Optional event — when set, the loop stops.

        Returns:
            Result dict with success, message, iteration counts, and history.
        """
        max_iterations = min(max_iterations, 100)
        history: list[dict[str, Any]] = []

        # Auto-detect vision capability
        if use_vision is None:
            use_vision = self._has_vision()
        if use_vision and not self._has_vision():
            logger.warning("Vision mode requested but AI client does not support it — falling back to text")
            use_vision = False

        if use_vision:
            logger.info("Computer Use in VISION mode")
        else:
            logger.info("Computer Use in OCR TEXT mode")

        for iteration in range(max_iterations):
            # Check cancellation
            if cancellation_event and cancellation_event.is_set():
                logger.info("Computer Use cancelled by user at iteration %d", iteration)
                self._emit_complete(goal, history, False, "cancelled by user")
                return self._build_result(goal, history, False, "cancelled by user")

            logger.info(
                "Computer Use iteration %d/%d — goal: %.60s",
                iteration + 1, max_iterations, goal,
            )

            # Phase 1 — Capture screen with OCR (and image if vision mode)
            screen = self._capture_screen(return_image=use_vision)
            if not screen.get("success"):
                reason = screen.get("reason", "unknown error")
                self._emit_complete(goal, history, False, f"screen capture failed: {reason}")
                return self._build_result(goal, history, False, f"screen capture failed: {reason}")

            # Phase 2 — Ask the LLM what to do next
            try:
                decision = self._llm_decide(goal, screen, history, use_vision=use_vision)
            except Exception as exc:
                logger.warning("LLM decision failed at iteration %d: %s", iteration, exc)
                self._emit_complete(goal, history, False, f"LLM decision error: {exc}")
                return self._build_result(goal, history, False, f"LLM decision error: {exc}")

            action_type = decision.get("type", "")
            reasoning = decision.get("reasoning", "")

            # Phase 3 — Check termination conditions
            if action_type == "done":
                logger.info("Computer Use complete — %s", reasoning)
                self._emit_complete(goal, history, True, reasoning)
                return self._build_result(goal, history, True, reasoning)

            if action_type == "error":
                logger.warning("Computer Use aborted — %s", reasoning)
                self._emit_complete(goal, history, False, reasoning)
                return self._build_result(goal, history, False, reasoning)

            if action_type not in self.ACTION_MAP:
                logger.warning("Unknown action type '%s' — skipping", action_type)
                history.append({
                    "iteration": iteration,
                    "action": action_type,
                    "status": "unknown_action",
                    "reasoning": reasoning,
                })
                continue

            # Phase 4 — Emit step start event (for progress UI)
            self._emit_step_start(iteration, action_type, reasoning)

            # Phase 5 — Execute the action
            action_result = self._execute_action(action_type, decision, authorize_callback)

            entry: dict[str, Any] = {
                "iteration": iteration,
                "action": action_type,
                "status": "ok" if action_result.get("success") else "failed",
                "reasoning": reasoning,
                "result": action_result,
            }
            screen_text = screen.get("text", "")
            if isinstance(screen_text, str):
                entry["screen_summary"] = screen_text[:200]
            history.append(entry)

            self._emit_step_end(iteration, action_type, reasoning,
                "ok" if action_result.get("success") else "failed",
                action_result.get("reason", ""))

            if not action_result.get("success"):
                logger.warning(
                    "Action %s failed: %s",
                    action_type, action_result.get("reason", "unknown"),
                )

            # Phase 6 — Verify action result by re-capturing screen
            time.sleep(0.8)
            if action_type in ("mouse_click", "mouse_click_text", "keyboard_type", "keyboard_press"):
                verify_screen = self._capture_screen(return_image=False)
                if verify_screen.get("success"):
                    old_text = str(screen.get("text", "") or "")
                    new_text = str(verify_screen.get("text", "") or "")
                    screen_changed = (new_text.strip() != old_text.strip())
                    entry["screen_changed"] = screen_changed
                    if not screen_changed and action_result.get("success"):
                        logger.warning(
                            "Screen unchanged after successful %s — possible silent failure",
                            action_type,
                        )

            # Phase 7 — Retry failed actions once
            if not action_result.get("success") and action_type in (
                "mouse_click", "mouse_click_text", "keyboard_type",
            ):
                logger.info("Retrying %s (once)...", action_type)
                time.sleep(0.5)
                retry_result = self._execute_action(action_type, decision, authorize_callback)
                entry["retried"] = True
                entry["retry_ok"] = retry_result.get("success", False)
                if retry_result.get("success"):
                    entry["status"] = "ok"
                    # Re-verify after retry
                    time.sleep(0.8)
                    retry_verify = self._capture_screen(return_image=False)
                    if retry_verify.get("success"):
                        new_text = str(retry_verify.get("text", "") or "")
                        entry["screen_changed"] = (new_text.strip() != str(
                            screen.get("text", "")
                        ).strip())

        self._emit_complete(goal, history, False,
            f"reached max iterations ({max_iterations}) without completing the goal")
        return self._build_result(
            goal, history, False,
            f"reached max iterations ({max_iterations}) without completing the goal",
        )

    # ── Internal methods ──────────────────────────────────────

    def _capture_screen(self, return_image: bool = False) -> dict[str, Any]:
        """Capture the current screen with OCR (and optionally an image)."""
        step = PlannedStep(
            step_id="cu_screen_capture",
            action_id="screen.read",
            payload={"ocr": True, "return_image": return_image},
            risk_level="low",
            timeout_seconds=30,
            retries=1,
        )
        try:
            result = self.capability_router.execute(step)
            if isinstance(result, dict):
                return result
            return {"success": False, "reason": f"unexpected result type: {type(result).__name__}"}
        except Exception as exc:
            logger.warning("Screen capture failed: %s", exc)
            return {"success": False, "reason": str(exc)}

    def _llm_decide(
        self,
        goal: str,
        screen: dict[str, Any],
        history: list[dict[str, Any]],
        use_vision: bool = False,
    ) -> dict[str, Any]:
        """Send screen state + history to the LLM and get the next action."""
        width = screen.get("width", 0)
        height = screen.get("height", 0)
        ocr_text = screen.get("text", "") or ""

        # Build a compact history summary (last 10 entries)
        history_lines: list[str] = []
        for h in history[-10:]:
            icon = "✓" if h.get("status") == "ok" else "✗"
            act = h.get("action", "?")
            reason = str(h.get("reasoning", ""))[:80]
            idx = h.get("iteration", 0)
            history_lines.append(f"  [{idx}] {icon} {act}: {reason}")
        history_text = "\n".join(history_lines) or "  (no actions yet)"

        if use_vision:
            image_base64 = screen.get("image_base64", "")
            if image_base64:
                prompt = COMPUTER_USE_VISION_PROMPT.format(
                    goal=goal,
                    width=width,
                    height=height,
                    ocr_text=ocr_text,
                    history=history_text,
                )
                response = self._call_vision(prompt, image_base64)
            else:
                logger.warning("Vision mode enabled but no image data — falling back to text")
                use_vision = False

        if not use_vision:
            prompt = COMPUTER_USE_SYSTEM_PROMPT.format(
                goal=goal,
                width=width,
                height=height,
                ocr_text=ocr_text,
                history=history_text,
            )
            response = self.ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": (
                        f"Iteration {len(history) + 1}: screen captured ({width}x{height}). "
                        f"What should I do next?"
                    )},
                ],
                temperature=0.1,
                max_tokens=512,
            )

        raw_text = self._extract_response_text(response)
        if not raw_text or not raw_text.strip():
            return {"type": "error", "reasoning": "empty response from LLM"}

        return self._parse_decision(raw_text)

    def _call_vision(self, prompt: str, image_base64: str) -> Any:
        """Call the AI vision API with the screenshot image."""
        try:
            return self.ai_client.chat_completion_vision(
                text=prompt,
                image_base64=image_base64,
                image_format="png",
                system_prompt=None,
                temperature=0.1,
                max_tokens=512,
            )
        except Exception as exc:
            logger.warning("Vision API call failed, falling back to text: %s", exc)
            # Fallback: treat the vision prompt as a text-only prompt
            return self.ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": (
                        "The screenshot is not available. "
                        "Use the OCR text and history provided in the system prompt to decide."
                    )},
                ],
                temperature=0.1,
                max_tokens=512,
            )

    def _execute_action(
        self,
        action_type: str,
        decision: dict[str, Any],
        authorize_callback: Any | None = None,
    ) -> dict[str, Any]:
        """Execute a single action via the capability router, with auth check."""
        action_id = self.ACTION_MAP.get(action_type)
        if not action_id:
            return {"success": False, "reason": f"unknown action type: {action_type}"}

        try:
            payload = self._build_payload(action_type, decision)
        except (ValueError, TypeError, RuntimeError) as exc:
            return {"success": False, "reason": f"invalid payload for {action_type}: {exc}"}
        if not payload:
            return {"success": False, "reason": f"empty payload for {action_type}"}

        step = PlannedStep(
            step_id=f"cu_{action_type}",
            action_id=action_id,
            payload=payload,
            risk_level="high" if action_type in ("mouse_click", "mouse_click_text", "keyboard_type", "keyboard_press") else "low",
            timeout_seconds=15,
            retries=1,
        )

        # Run authorization check before execution
        if authorize_callback is not None:
            try:
                auth_result = authorize_callback(step)
                if not auth_result.get("allowed", False):
                    reason = auth_result.get("reason", "not authorized")
                    logger.warning("Action %s blocked by auth: %s", action_type, reason)
                    return {"success": False, "reason": reason, "blocked": True}
            except Exception as exc:
                logger.warning("Auth callback failed for %s: %s", action_type, exc)
                return {"success": False, "reason": f"authorization error: {exc}"}

        try:
            result = self.capability_router.execute(step)
            if isinstance(result, dict):
                result.setdefault("success", True)
                return result
            return {"success": False, "reason": f"unexpected result type: {type(result).__name__}"}
        except Exception as exc:
            return {"success": False, "reason": str(exc)}

    # ── Static helpers ────────────────────────────────────────

    # ── EventBus emissions (for progress UI) ──────────────

    def _emit_step_start(self, iteration: int, action_type: str, reasoning: str) -> None:
        try:
            event_bus.emit(AppEvent(
                event_type="execution.step.step_start",
                source="assistant.computer_use",
                payload={
                    "step_index": iteration,
                    "action_id": self.ACTION_MAP.get(action_type, action_type),
                    "description": str(reasoning)[:100],
                },
            ))
        except Exception as exc:
            logger.warning("Failed to emit step_start event: %s", exc)

    def _emit_step_end(self, iteration: int, action_type: str, reasoning: str, status: str, error: str) -> None:
        try:
            event_bus.emit(AppEvent(
                event_type="execution.step.step_end",
                source="assistant.computer_use",
                payload={
                    "step_index": iteration,
                    "action_id": self.ACTION_MAP.get(action_type, action_type),
                    "description": str(reasoning)[:100],
                    "status": status,
                    "error": error,
                },
            ))
        except Exception as exc:
            logger.warning("Failed to emit step_end event: %s", exc)

    def _emit_complete(self, goal: str, history: list, success: bool, message: str) -> None:
        try:
            event_bus.emit(AppEvent(
                event_type="execution.complete",
                source="assistant.computer_use",
                payload={
                    "goal": goal[:100],
                    "success": success,
                    "iterations": len(history),
                    "message": str(message)[:200],
                },
            ))
        except Exception as exc:
            logger.warning("Failed to emit complete event: %s", exc)

    @staticmethod
    def _build_payload(action_type: str, decision: dict[str, Any]) -> dict[str, Any]:
        """Construct the payload dict for the given action type."""
        if action_type == "mouse_click":
            return {
                "x": _safe_int(decision.get("x", 0)),
                "y": _safe_int(decision.get("y", 0)),
                "button": str(decision.get("button", "left")),
                "clicks": max(1, min(_safe_int(decision.get("clicks", 1)), 3)),
            }
        if action_type == "mouse_click_text":
            return {
                "text": str(decision.get("text", "")),
                "button": str(decision.get("button", "left")),
                "clicks": max(1, min(_safe_int(decision.get("clicks", 1)), 3)),
                "fuzzy": True,
            }
        if action_type == "keyboard_type":
            return {
                "text": str(decision.get("text", "")),
                "enter": bool(decision.get("enter", False)),
                "delay": 0.01,
            }
        if action_type == "keyboard_press":
            keys = decision.get("keys")
            if isinstance(keys, str):
                keys = [keys]
            return {"keys": list(keys) if isinstance(keys, list) else []}
        if action_type == "window_find":
            return {
                "title": str(decision.get("title", "")),
                "max_results": 5,
            }
        if action_type == "window_activate":
            return {
                "hwnd": _safe_int(decision.get("hwnd", 0)),
            }
        return {}

    @staticmethod
    def _parse_decision(text: str) -> dict[str, Any]:
        """Parse the LLM JSON response with multiple fallback strategies."""
        text = text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            for i, line in enumerate(lines):
                if line.startswith("```"):
                    text = "\n".join(lines[i + 1:])
                    break
            if text.endswith("```"):
                text = text[:-3].strip()

        # Direct JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Find first JSON object in text
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        # Unparseable
        return {"type": "error", "reasoning": f"failed to parse: {text[:200]}"}

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        """Extract text content from various AI client response formats."""
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
            # Fallback to "content" key
            content = response.get("content", "")
            if isinstance(content, str):
                return content
        return ""

    @staticmethod
    def _build_result(
        goal: str,
        history: list[dict[str, Any]],
        success: bool,
        message: str,
    ) -> dict[str, Any]:
        """Build the final result dictionary."""
        return {
            "success": success,
            "goal": goal,
            "message": message,
            "cancelled": message == "cancelled by user",
            "iterations": len(history),
            "actions_taken": sum(1 for h in history if h.get("status") == "ok"),
            "actions_failed": sum(1 for h in history if h.get("status") == "failed"),
            "history": history,
        }


def _safe_int(value: Any, default: int = 0) -> int:
    """Convert a value to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
