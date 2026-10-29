"""画像习惯学习引擎 — 从使用记录中发现用户习惯模式。

核心流程：
1. 定期扫描 assistant_events / telemetry_events 事件记录
2. 发现时段习惯、动作序列、常用能力等模式
3. 写回 UserProfileService 画像层
4. 可选：通过 CronService 注册智能定时任务
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

logger = logging.getLogger("siyi.habit_learner")

# 学习参数
_SCAN_INTERVAL = 300          # 每次扫描间隔（秒）
_MIN_PATTERN_CONFIDENCE = 3   # 同一模式至少出现 N 次才算可信
_MAX_HOUR_PATTERNS = 10       # 时段模式保留上限
_MAX_TEMPLATES = 5            # 任务模板保留上限


class HabitLearner:
    """扫描使用记录，发现习惯模式并更新用户画像。"""

    def __init__(self, data_store, user_profile_service):
        self._data_store = data_store
        self._profile = user_profile_service

    def scan_and_learn(self) -> dict[str, Any]:
        """单次扫描 + 学习入口。返回本次发现的新模式摘要。"""
        events = self._load_events()
        if not events:
            return {"status": "skipped", "reason": "no_events"}

        result: dict[str, Any] = {"status": "ok", "discoveries": {}}

        # 1. 时段习惯
        hour_patterns = self._discover_hour_patterns(events)
        if hour_patterns:
            result["discoveries"]["hour_patterns"] = hour_patterns

        # 2. 高频能力
        top_tools = self._discover_top_tools(events)
        if top_tools:
            result["discoveries"]["top_tools"] = top_tools

        # 3. 动作序列模式
        sequences = self._discover_sequences(events)
        if sequences:
            result["discoveries"]["sequences"] = sequences

        # 4. 更新画像
        self._apply_to_profile(hour_patterns, top_tools)

        result["total_events_scanned"] = len(events)
        return result

    # ── 事件加载 ────────────────────────────────────────────

    def _load_events(self) -> list[dict[str, Any]]:
        """从 data_store 加载 assistant_events 和 telemetry_events。"""
        events: list[dict[str, Any]] = []

        for key in ("assistant_events", "telemetry_events"):
            try:
                raw = self._data_store.load_json(key, [])
                if isinstance(raw, list):
                    events.extend(raw)
            except Exception:
                logger.debug("load %s failed (expected if empty)", key)

        # 也尝试读取 JSONL 遥测文件
        try:
            from pathlib import Path
            telemetry_file = Path(str(self._data_store.data_dir)) / "telemetry_events.jsonl"
            if telemetry_file.exists():
                with open(telemetry_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            import json
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError:
                                logger.debug("Non-critical error (see details)", exc_info=True)

        except Exception:
            logger.debug("Non-critical error (see details)", exc_info=True)


        return events

    # ── 模式发现 ────────────────────────────────────────────

    def _discover_hour_patterns(self, events: list[dict]) -> list[dict[str, Any]]:
        """发现时段习惯：某动作集中在哪些小时段。"""
        action_hours: dict[str, list[int]] = defaultdict(list)

        for ev in events[-200:]:
            action = (ev.get("action") or "").strip() or str(ev.get("payload", {}).get("action_id", "")).strip()
            if not action:
                continue

            ts = ev.get("timestamp") or ev.get("time") or ""
            hour = self._extract_hour(ts)
            if hour is not None:
                action_hours[action].append(hour)

        patterns = []
        for action, hours in action_hours.items():
            if len(hours) >= _MIN_PATTERN_CONFIDENCE:
                avg_h = sum(hours) / len(hours)
                peak = Counter(hours).most_common(1)
                patterns.append({
                    "action": action,
                    "avg_hour": round(avg_h, 1),
                    "peak_hour": peak[0][0] if peak else round(avg_h),
                    "frequency": len(hours),
                })

        patterns.sort(key=lambda p: p["frequency"], reverse=True)  # type: ignore[arg-type]
        return patterns[:_MAX_HOUR_PATTERNS]

    def _discover_top_tools(self, events: list[dict]) -> list[str]:
        """发现高频使用的能力。"""
        counter: Counter[str] = Counter()
        for ev in events[-500:]:
            action = (ev.get("action") or "").strip() or str(ev.get("payload", {}).get("action_id", "")).strip()
            if action:
                counter[action] += 1
        return [a for a, _ in counter.most_common(10) if a]

    def _discover_sequences(self, events: list[dict]) -> list[dict[str, Any]]:
        """发现两步动作序列模式（A→B 频繁出现）。"""
        actions = []
        for ev in events[-500:]:
            action = (ev.get("action") or "").strip() or str(ev.get("payload", {}).get("action_id", "")).strip()
            if action:
                actions.append(action)

        pair_counter: Counter[tuple[str, str]] = Counter()
        for i in range(len(actions) - 1):
            pair_counter[(actions[i], actions[i + 1])] += 1

        sequences = []
        for (a, b), count in pair_counter.most_common(10):
            if count >= _MIN_PATTERN_CONFIDENCE:
                sequences.append({"from": a, "to": b, "frequency": count})

        return sequences

    # ── 画像更新 ────────────────────────────────────────────

    def _apply_to_profile(self, hour_patterns: list[dict], top_tools: list[str]) -> None:
        """将发现的模式写回 UserProfileService。"""
        if not hour_patterns and not top_tools:
            return

        profile = self._profile.load()

        # 更新 preferred_tools
        if top_tools:
            existing = list(profile.get("long_term", {}).get("preferred_tools") or [])
            seen = set(existing)
            for tool in top_tools:
                if tool not in seen:
                    existing.append(tool)
                    seen.add(tool)
            profile.setdefault("long_term", {})["preferred_tools"] = existing[-20:]

        # 更新时段习惯到画像（新字段，写入 context）
        if hour_patterns:
            profile.setdefault("context", {})["habit_hours"] = hour_patterns

        self._profile.save(profile)
        logger.debug("画像已更新: tools=%d, hour_patterns=%d", len(top_tools), len(hour_patterns))

    # ── 工具 ────────────────────────────────────────────────

    @staticmethod
    def _extract_hour(ts: str) -> int | None:
        if not ts:
            return None
        try:
            if "T" in ts:
                return datetime.fromisoformat(ts).hour
            cleaned = re.sub(r"[^0-9\- :]", "", ts[:19])
            if len(cleaned) >= 16:
                return datetime.strptime(cleaned[:16], "%Y-%m-%d %H:%M").hour
            return None
        except (ValueError, TypeError):
            return None
