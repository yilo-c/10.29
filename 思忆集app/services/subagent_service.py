from __future__ import annotations

import html
import logging
import os
import smtplib
import ssl
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from core.settings_schema import KEY_ASSISTANT_FOCUS_TOPICS

logger = logging.getLogger("siyi.subagent")


@dataclass
class SubagentRun:
    run_id: str
    label: str
    workflow: str
    status: str
    created_at: str
    updated_at: str
    payload: dict[str, Any]
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "label": self.label,
            "workflow": self.workflow,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "payload": self.payload,
            "result": self.result,
        }


class SubagentManager:
    CAPABILITY_TIMEOUT_SECONDS = 20

    def __init__(
        self,
        *,
        data_store=None,
        settings_repository=None,
        observability_service=None,
        ai_client_factory: Callable[[], Any] | None = None,
        capability_executor: Callable[[list[dict[str, Any]], bool], dict[str, Any]] | None = None,
        note_repository=None,
        inbox_repository=None,
        memory_index_service=None,
        max_workers: int = 2,
    ):
        self.data_store = data_store
        self.settings_repository = settings_repository
        self.observability_service = observability_service
        self.ai_client_factory = ai_client_factory
        self.capability_executor = capability_executor
        self.note_repository = note_repository
        self.inbox_repository = inbox_repository
        self.memory_index_service = memory_index_service
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max(1, min(max_workers, 4)), thread_name_prefix="siyi-subagent")
        self._runs: dict[str, SubagentRun] = {}
        self._load_runs()

    def spawn(self, workflow: str, payload: dict[str, Any] | None = None, *, label: str = "后台任务") -> dict[str, Any]:
        run = self._create_run(workflow, payload, label=label, status="queued")
        with self._lock:
            self._runs[run.run_id] = run
            self._save_runs()
        self._executor.submit(self._run_workflow, run.run_id)
        return run.to_dict()

    def run_now(self, workflow: str, payload: dict[str, Any] | None = None, *, label: str = "手动任务") -> dict[str, Any]:
        run = self._create_run(workflow, payload, label=label, status="running")
        with self._lock:
            self._runs[run.run_id] = run
            self._save_runs()
        try:
            result = self._execute_workflow(run.workflow, run.payload)
            self._update_run(run.run_id, status="completed", result=result)
            self._track_event("subagent.completed", {"run_id": run.run_id, "workflow": run.workflow, "mode": "run_now"})
        except Exception as exc:
            logger.warning("subagent run_now failed: %s", exc)
            self._update_run(run.run_id, status="failed", result={"error": str(exc)})
            self._track_event("subagent.failed", {"run_id": run.run_id, "workflow": run.workflow, "error": str(exc), "mode": "run_now"})
        return self.get_run(run.run_id) or run.to_dict()

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda item: item.updated_at, reverse=True)
            return [item.to_dict() for item in runs[:limit]]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(str(run_id or "").strip())
            return run.to_dict() if run is not None else None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_workflow(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        self._update_run(run_id, status="running")
        try:
            result = self._execute_workflow(run.workflow, run.payload)
            self._update_run(run_id, status="completed", result=result)
            self._track_event("subagent.completed", {"run_id": run_id, "workflow": run.workflow})
        except Exception as exc:
            logger.warning("subagent run failed: %s", exc)
            self._update_run(run_id, status="failed", result={"error": str(exc)})
            self._track_event("subagent.failed", {"run_id": run_id, "workflow": run.workflow, "error": str(exc)})

    def _create_run(self, workflow: str, payload: dict[str, Any] | None, *, label: str, status: str) -> SubagentRun:
        now = datetime.now(timezone.utc).isoformat()
        return SubagentRun(
            run_id=uuid.uuid4().hex[:12],
            label=str(label or workflow or "后台任务").strip() or "后台任务",
            workflow=str(workflow or "execute_steps").strip() or "execute_steps",
            status=str(status or "queued"),
            created_at=now,
            updated_at=now,
            payload=dict(payload or {}),
            result={},
        )

    def _execute_workflow(self, workflow: str, payload: dict[str, Any]) -> dict[str, Any]:
        if workflow == "weekly_notes_digest":
            return self._run_weekly_notes_digest(payload)
        if workflow == "weekly_tech_digest":
            return self._run_weekly_tech_digest(payload)
        return self._run_execute_steps(payload)

    def _run_weekly_notes_digest(self, payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(str(payload.get("root") or Path.cwd())).resolve()
        output_path = Path(str(payload.get("output_path") or (root / "weekly_notes_digest.md"))).resolve()
        days = max(1, min(int(payload.get("days") or 7), 30))
        cutoff = datetime.now() - timedelta(days=days)
        markdown_files = []
        for candidate in root.rglob("*.md"):
            try:
                modified = datetime.fromtimestamp(candidate.stat().st_mtime)
            except OSError:
                continue
            if modified >= cutoff:
                markdown_files.append(candidate)
        markdown_files = sorted(markdown_files, key=lambda item: item.stat().st_mtime, reverse=True)[:40]
        snippets = []
        for item in markdown_files:
            try:
                text = item.read_text(encoding="utf-8")
            except Exception:
                continue
            cleaned = text.strip().replace("\r\n", "\n")
            snippets.append(f"## {item.name}\n\n{cleaned[:1200]}")
        combined = "\n\n".join(snippets)
        summary = self._summarize_text(
            combined,
            title=str(payload.get("title") or "本周笔记整理"),
            instruction=str(payload.get("instruction") or "请按主题归纳本周笔记，输出重点、待办和后续建议。"),
        )
        output = "# 本周笔记整理\n\n"
        output += f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        output += summary.strip() or "本周没有找到可整理的 Markdown 笔记。"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        return {
            "summary_path": str(output_path),
            "files": [str(item) for item in markdown_files[:10]],
            "count": len(markdown_files),
            "summary": summary,
        }

    def _run_execute_steps(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps = payload.get("steps") or []
        parallel = bool(payload.get("parallel", False))
        if not isinstance(steps, list) or not steps:
            return {"status": "skipped", "reason": "no_steps"}
        if self.capability_executor is None:
            return {"status": "skipped", "reason": "no_capability_executor"}
        return self.capability_executor(steps, parallel)

    def _run_weekly_tech_digest(self, payload: dict[str, Any]) -> dict[str, Any]:
        topics = self._resolve_digest_topics(payload)
        if not topics:
            return {"status": "skipped", "reason": "no_topics", "topics": []}
        output_path = self._resolve_output_path(payload.get("output_path"), default_name="weekly_tech_digest.md")
        title = str(payload.get("title") or "每周技术追踪").strip() or "每周技术追踪"
        context_snippets = self._resolve_context_snippets(payload)
        sections: list[str] = []
        for topic in topics[:5]:
            memory_result = self._execute_single_capability("memory.recall", {"query": topic, "top_k": 4})
            search_result = self._execute_single_capability("web.search", {"query": f"{topic} 最新进展", "num_results": 4})
            briefing_result = self._execute_single_capability("news.briefing", {"topic": topic, "max_items": 6})
            sections.append(self._build_topic_section(topic, memory_result, search_result, briefing_result))
        output = [f"# {title}", "", f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "", f"关注主题：{', '.join(topics)}", ""]
        if context_snippets:
            output.extend(["## 触发上下文", ""])
            for item in context_snippets:
                output.append(f"- {item}")
            output.append("")
        output.extend(sections)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(output).strip() + "\n", encoding="utf-8")
        note_id = self._save_digest_note(title, output_path)
        inbox_entry_id = self._save_digest_inbox_entry(title, output_path)
        result = {
            "status": "completed",
            "summary_path": str(output_path),
            "topics": topics,
            "note_id": note_id,
            "inbox_entry_id": inbox_entry_id,
        }
        delivery = self._deliver_digest(title=title, topics=topics, output_path=output_path, payload=payload)
        if delivery is not None:
            result["delivery"] = delivery
        return result

    def _summarize_text(self, text: str, *, title: str, instruction: str) -> str:
        clipped = str(text or "").strip()[:18000]
        if not clipped:
            return "本周没有找到可整理的 Markdown 笔记。"
        if self.ai_client_factory is None:
            return self._heuristic_summary(clipped)
        try:
            client = self.ai_client_factory()
            return client.complete_text(
                [
                    {
                        "role": "system",
                        "content": "你是桌面助手的后台子代理。请输出 Markdown，总结主题、要点、待办和建议，保留代码块边界。",
                    },
                    {
                        "role": "user",
                        "content": f"标题：{title}\n说明：{instruction}\n\n材料：\n{clipped}",
                    },
                ],
                temperature=0.2,
                max_tokens=1200,
            )
        except Exception as exc:
            logger.warning("subagent AI summary failed, fallback heuristic: %s", exc, exc_info=True)
            return self._heuristic_summary(clipped)

    @staticmethod
    def _heuristic_summary(text: str) -> str:
        paragraphs = [chunk.strip() for chunk in text.split("\n\n") if chunk.strip()]
        highlights = paragraphs[:5]
        lines = ["## 核心片段"]
        for item in highlights:
            lines.append(f"- {item[:180].replace(chr(10), ' ')}")
        lines.append("")
        lines.append("## 建议")
        lines.append("- 把重复主题合并成单独笔记")
        lines.append("- 把可执行事项转成任务或日报")
        lines.append("- 如果涉及代码方案，补充示例与结论")
        return "\n".join(lines)

    def _resolve_digest_topics(self, payload: dict[str, Any]) -> list[str]:
        raw_topics = payload.get("topics")
        if isinstance(raw_topics, list):
            topics = [str(item).strip() for item in raw_topics if str(item).strip()]
        elif isinstance(raw_topics, str):
            topics = [part.strip() for part in raw_topics.splitlines() if part.strip()]
        else:
            topics = []
        if topics:
            return list(dict.fromkeys(topics))[:8]
        if self.memory_index_service is not None:
            topics = self.memory_index_service.get_focus_topics()
            if topics:
                return list(dict.fromkeys(topics))[:8]
        if self.settings_repository is None:
            return []
        configured = self.settings_repository.get_setting(KEY_ASSISTANT_FOCUS_TOPICS, [])
        if isinstance(configured, list):
            return [str(item).strip() for item in configured if str(item).strip()][:8]
        return []

    @staticmethod
    def _resolve_context_snippets(payload: dict[str, Any]) -> list[str]:
        raw_items = payload.get("context_snippets")
        if isinstance(raw_items, list):
            return [str(item).strip() for item in raw_items if str(item).strip()][:5]
        if isinstance(raw_items, str):
            return [raw_items.strip()] if raw_items.strip() else []
        return []

    def _execute_single_capability(self, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.capability_executor is None:
            return {"success": False, "reason": "no_capability_executor"}
        # P1-2 修复：复用实例线程池，避免每次调用都创建/销毁临时 ThreadPoolExecutor，
        # 防止线程创建开销和上下文管理器退出时 wait=True 导致的阻塞。
        future = self._executor.submit(self.capability_executor, [{"action_id": action_id, "payload": payload}], False)
        try:
            outcome = future.result(timeout=self.CAPABILITY_TIMEOUT_SECONDS)
        except FutureTimeoutError:
            future.cancel()
            return {"success": False, "reason": "capability_timeout", "action_id": action_id}
        except Exception as exc:
            future.cancel()
            return {"success": False, "reason": str(exc), "action_id": action_id}
        entries = outcome.get("outcomes") or []
        if not entries:
            return {"success": False, "reason": "empty_outcome"}
        first = entries[0]
        if first.get("status") != "ok":
            return {"success": False, "reason": first.get("error") or "failed"}
        result = dict(first.get("payload") or {})
        result.setdefault("success", True)
        return result

    def _resolve_output_path(self, raw_output_path: Any, *, default_name: str) -> Path:
        base_dir = getattr(self.data_store, "base_dir", None)
        data_root = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()
        if raw_output_path:
            candidate = Path(str(raw_output_path))
            resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (data_root / candidate).resolve(strict=False)
        else:
            resolved = (data_root / "assistant_reports" / default_name).resolve(strict=False)
        if not (resolved == data_root or resolved.is_relative_to(data_root)):
            raise ValueError("output_path_outside_data_root")
        return resolved

    def _build_topic_section(self, topic: str, memory_result: dict[str, Any], search_result: dict[str, Any], briefing_result: dict[str, Any]) -> str:
        lines = [f"## {topic}", ""]
        memory_items = list(memory_result.get("items") or [])[:3]
        lines.append("### 本地记忆线索")
        if memory_items:
            for item in memory_items:
                lines.append(f"- [{item.get('source_type')}] {item.get('title')}：{item.get('snippet') or ''}".strip())
        else:
            lines.append("- 暂无本地沉淀。")
        lines.append("")
        lines.append("### 周度简报")
        lines.append(str(briefing_result.get("briefing") or "暂无简报。"))
        lines.append("")
        lines.append("### 联网追踪")
        search_items = list(search_result.get("results") or [])[:4]
        if search_items:
            for index, item in enumerate(search_items, start=1):
                lines.append(f"{index}. {item.get('title')} - {item.get('url')}")
        else:
            lines.append("1. 暂无新的联网结果")
        lines.append("")
        return "\n".join(lines)

    def _save_digest_note(self, title: str, output_path: Path) -> int | None:
        if self.note_repository is None:
            return None
        try:
            return self.note_repository.add_note(
                {
                    "title": title,
                    "content": output_path.read_text(encoding="utf-8"),
                    "folder_path": "/助手日志/技术周报",
                    "tags": ["assistant", "tech-digest"],
                    "category": "assistant",
                }
            )
        except Exception as exc:
            logger.warning("save weekly tech digest note failed: %s", exc, exc_info=True)
            return None

    def _save_digest_inbox_entry(self, title: str, output_path: Path) -> int | None:
        if self.inbox_repository is None:
            return None
        try:
            return self.inbox_repository.add_entry(
                {
                    "title": f"技术周报已生成：{title}",
                    "content": str(output_path),
                    "tags": ["assistant", "tech-digest"],
                    "source": "assistant",
                    "status": "new",
                }
            )
        except Exception as exc:
            logger.warning("save weekly tech digest inbox failed: %s", exc, exc_info=True)
            return None

    def _deliver_digest(self, *, title: str, topics: list[str], output_path: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
        target = str(payload.get("delivery_target") or "").strip().lower()
        if not target:
            return None
        if target == "wechat_clipboard":
            delivery_text = self._build_digest_delivery_text(title=title, topics=topics, output_path=output_path)
            clipboard_result = self._execute_single_capability("clipboard.write", {"text": delivery_text})
            launch_result = self._execute_single_capability(
                "app.launch",
                {"command": str(payload.get("delivery_app") or "微信").strip() or "微信"},
            )
            return {
                "target": target,
                "content_length": len(delivery_text),
                "clipboard": clipboard_result,
                "launch": launch_result,
            }
        if target == "email":
            delivery_text = self._build_digest_email_text(title=title, topics=topics, output_path=output_path)
            try:
                email_result = self._send_digest_email(title=title, content=delivery_text)
            except Exception as exc:
                logger.warning("digest email delivery failed: %s", exc)
                email_result = {"success": False, "reason": str(exc)}
            email_result = dict(email_result or {})
            email_result.setdefault("success", False)
            return {
                "target": target,
                "content_length": len(delivery_text),
                **email_result,
            }
        return {"target": target, "success": False, "reason": "unsupported_delivery_target"}

    def _send_digest_email(self, *, title: str, content: str) -> dict[str, Any]:
        smtp_config = self._load_smtp_delivery_config()
        if not bool(smtp_config.get("enabled")):
            return {"success": False, "reason": "notification_disabled"}
        host = str(smtp_config.get("smtp_host") or "").strip()
        user = str(smtp_config.get("smtp_user") or "").strip()
        password = str(smtp_config.get("smtp_password") or "").strip()
        from_addr = str(smtp_config.get("smtp_from") or user).strip()
        to_addrs = smtp_config.get("smtp_to") or []
        if not isinstance(to_addrs, list):
            to_addrs = []
        to_addrs = [str(item).strip() for item in to_addrs if str(item).strip()]

        if not host:
            return {"success": False, "reason": "smtp_host_missing"}
        if not from_addr:
            return {"success": False, "reason": "smtp_from_missing"}
        if not to_addrs:
            return {"success": False, "reason": "smtp_to_missing"}
        if not user:
            return {"success": False, "reason": "smtp_user_missing"}
        if not password:
            return {"success": False, "reason": "smtp_password_missing"}

        port = int(smtp_config.get("smtp_port") or 587)
        subject = f"[INFO] {title}"
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = from_addr
        message["To"] = ", ".join(to_addrs)
        message.attach(MIMEText(content, "plain", "utf-8"))
        message.attach(MIMEText(self._build_digest_email_html(title=title, content=content), "html", "utf-8"))

        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as server:
                server.ehlo()
                server.login(user, password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(user, password)
                server.send_message(message)

        return {
            "success": True,
            "channel": "smtp",
            "subject": subject,
            "from_addr": from_addr,
            "to_addrs": to_addrs,
        }

    def _load_smtp_delivery_config(self) -> dict[str, Any]:
        config = {
            "enabled": False,
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "",
            "smtp_to": [],
        }
        config_path = Path(__file__).resolve().parents[1] / "config.yaml"
        if config_path.exists():
            try:
                payload = self._load_simple_yaml(config_path)
            except Exception as exc:
                logger.warning("load digest smtp config failed: %s", exc, exc_info=True)
                payload = {}
            notification = payload.get("notification") if isinstance(payload, dict) else {}
            if isinstance(notification, dict):
                for key in list(config.keys()):
                    if key in notification:
                        config[key] = notification.get(key)

        if os.getenv("NOTIFICATION_ENABLED"):
            config["enabled"] = os.getenv("NOTIFICATION_ENABLED").strip().lower() in {"1", "true", "yes", "on"}

        if os.getenv("SMTP_HOST"):
            config["smtp_host"] = os.getenv("SMTP_HOST")
        if os.getenv("SMTP_PORT"):
            try:
                config["smtp_port"] = int(os.getenv("SMTP_PORT"))
            except Exception:
                logger.debug("invalid SMTP_PORT value: %s", os.getenv("SMTP_PORT"))
        if os.getenv("SMTP_USER"):
            config["smtp_user"] = os.getenv("SMTP_USER")
        if os.getenv("SMTP_PASSWORD"):
            config["smtp_password"] = os.getenv("SMTP_PASSWORD")
        if os.getenv("SMTP_FROM"):
            config["smtp_from"] = os.getenv("SMTP_FROM")
        if os.getenv("SMTP_TO"):
            raw_to = os.getenv("SMTP_TO") or ""
            config["smtp_to"] = [item.strip() for item in raw_to.replace(";", ",").split(",") if item.strip()]

        smtp_to = config.get("smtp_to") or []
        if isinstance(smtp_to, str):
            smtp_to = [item.strip() for item in smtp_to.replace(";", ",").split(",") if item.strip()]
        elif isinstance(smtp_to, list):
            smtp_to = [str(item).strip() for item in smtp_to if str(item).strip()]
        else:
            smtp_to = []
        config["smtp_to"] = smtp_to
        return config

    @staticmethod
    def _load_simple_yaml(config_path: Path) -> dict[str, Any]:
        try:
            import yaml  # type: ignore

            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            return payload if isinstance(payload, dict) else {}
        except Exception:
            logger.warning("YAML 解析失败，使用手动回退解析")

        root: dict[str, Any] = {}
        current_section: str | None = None
        current_list_key: str | None = None
        for raw_line in config_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            if not raw_line.startswith(" ") and raw_line.rstrip().endswith(":"):
                current_section = raw_line.strip()[:-1]
                root[current_section] = {}
                current_list_key = None
                continue
            if current_section is None:
                continue
            section = root.get(current_section)
            if not isinstance(section, dict):
                continue
            stripped = raw_line.strip()
            if stripped.startswith("- ") and current_list_key:
                section.setdefault(current_list_key, []).append(stripped[2:].strip().strip('"').strip("'"))
                continue
            if ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not value:
                section[key] = []
                current_list_key = key
                continue
            current_list_key = None
            cleaned = value.split("#", 1)[0].strip().strip('"').strip("'")
            lowered = cleaned.lower()
            if lowered in {"true", "yes", "on"}:
                section[key] = True
            elif lowered in {"false", "no", "off"}:
                section[key] = False
            elif cleaned.isdigit():
                section[key] = int(cleaned)
            else:
                section[key] = cleaned
        return root

    @staticmethod
    def _build_digest_email_html(*, title: str, content: str) -> str:
        return (
            "<html><body>"
            f"<h2>{html.escape(title)}</h2>"
            f"<pre>{html.escape(content)}</pre>"
            "</body></html>"
        )

    @staticmethod
    def _build_digest_email_text(*, title: str, topics: list[str], output_path: Path) -> str:
        body = output_path.read_text(encoding="utf-8").strip()
        topic_line = f"关注主题：{', '.join(topics)}" if topics else "关注主题：未设置"
        if body:
            return f"{title}\n{topic_line}\n\n{body}"
        return f"{title}\n{topic_line}\n\n简报已生成，但正文为空。"

    @staticmethod
    def _build_digest_delivery_text(*, title: str, topics: list[str], output_path: Path) -> str:
        body = output_path.read_text(encoding="utf-8").strip()
        topic_line = f"关注主题：{', '.join(topics)}" if topics else "关注主题：未设置"
        if body:
            return f"{title}\n{topic_line}\n\n{body}"[:5000]
        return f"{title}\n{topic_line}\n\n简报已生成，但正文为空。"

    def _load_runs(self) -> None:
        if self.data_store is None:
            return
        try:
            rows = self.data_store.load_json("subagent_runs", [])
        except Exception:
            logger.warning("failed to load subagent_runs, resetting to empty", exc_info=True)
            rows = []
        if not isinstance(rows, list):
            rows = []
        with self._lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                run_id = str(row.get("run_id") or "").strip()
                if not run_id:
                    continue
                self._runs[run_id] = SubagentRun(
                    run_id=run_id,
                    label=str(row.get("label") or "后台任务"),
                    workflow=str(row.get("workflow") or "execute_steps"),
                    status=str(row.get("status") or "unknown"),
                    created_at=str(row.get("created_at") or ""),
                    updated_at=str(row.get("updated_at") or ""),
                    payload=dict(row.get("payload") or {}),
                    result=dict(row.get("result") or {}),
                )

    def _save_runs(self) -> None:
        if self.data_store is None:
            return
        rows = sorted(self._runs.values(), key=lambda item: item.updated_at, reverse=True)
        self.data_store.save_json("subagent_runs", [item.to_dict() for item in rows[:200]])

    def _update_run(self, run_id: str, *, status: str | None = None, result: dict[str, Any] | None = None) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            if status is not None:
                run.status = status
            if result is not None:
                run.result = dict(result)
            run.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_runs()

    def _track_event(self, event_name: str, payload: dict[str, Any]) -> None:
        if self.observability_service is None:
            return
        try:
            self.observability_service.track_event(event_name, payload=payload)
        except Exception:
            logger.debug("subagent observability skipped", exc_info=True)
