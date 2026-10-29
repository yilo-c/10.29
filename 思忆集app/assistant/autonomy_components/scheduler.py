"""System-event scheduling facade."""
from __future__ import annotations


class AutonomyScheduler:
    def __init__(self, manager) -> None:
        self.manager = manager

    def run_system_event(self, event_name: str, payload: dict | None = None) -> dict:
        events = getattr(self.manager, "events", None)
        if events is not None and hasattr(events, "handle_system_event"):
            return events.handle_system_event(event_name, payload or {})
        return {"status": "unavailable", "event": event_name}

    def run_due_schedules(self) -> list[dict]:
        scheduler = getattr(self.manager, "schedule_service", None)
        if scheduler is not None and hasattr(scheduler, "run_due"):
            return list(scheduler.run_due() or [])
        return []