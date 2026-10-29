from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from services.ai_service import create_fallback_client
from integrations.automation_hub_client import (
    create_automation_hub_client,
    resolve_automation_hub_connection_config,
)
from integrations.automation_hub_types import AutomationHubClientError
from ai.llm_provider_topology import build_llm_provider_topology, summarize_llm_provider_topology
from plugins.protocol import PluginRegistry
from core.settings_keys import (
    KEY_ASSISTANT_UPGRADE_CHANNEL,
    KEY_BROWSER_ASSISTANT_BRIDGE_ENABLED,
    KEY_BROWSER_ASSISTANT_BRIDGE_PORT,
    KEY_BROWSER_ASSISTANT_TRUSTED_EXTENSION_IDS,
    KEY_NEWS_AUTO_SYNC_ENABLED,
    KEY_NOTES_AUTO_SYNC_ENABLED,
    KEY_TASKS_AUTO_SYNC_ENABLED,
    KEY_TRANSLATIONS_AUTO_SYNC_ENABLED,
    KEY_VOCABULARY_AUTO_SYNC_ENABLED,
)
from core.action_guard import KEY_COMMAND_WHITELIST


class DesktopDoctorService:
    REQUIRED_COMMANDS = {
        "detect_nana_pet_process",
        "launch_nana_pet",
        "docker_version_check",
        "start_local_agent_backend",
        "start_docker_stack",
        "open_external_url",
    }

    def __init__(
        self,
        *,
        data_store=None,
        settings_repository=None,
        app_context=None,
        plugin_registry: PluginRegistry | None = None,
    ):
        self.data_store = data_store
        self.settings_repository = settings_repository
        self.app_context = app_context
        self.plugin_registry = plugin_registry
        self.workspace_dir = Path(__file__).resolve().parent.parent.parent
        self.backend_entry = self.workspace_dir / "思忆集app" / "backend" / "headless_main.py"
        self.nana_root = self.workspace_dir / "desktop-pet-qinai"
        self.nana_launcher = self.nana_root / "启动七奈桌宠.bat"

    def run_diagnostics(self, *, deep: bool = False) -> dict[str, Any]:
        data_store = self.data_store
        settings_repository = self.settings_repository
        data_dir = Path(getattr(data_store, "base_dir", self.workspace_dir / "思忆集app" / "data"))
        llm_stages = build_llm_provider_topology(settings_repository=settings_repository, environ=os.environ)
        automation_config = resolve_automation_hub_connection_config(settings_repository=settings_repository)
        command_whitelist = self._resolve_command_whitelist()
        plugin_contract = self.plugin_registry.describe_contract() if self.plugin_registry is not None else {"extension_points": [], "counts": {}}

        browser_status = {}
        if self.app_context is not None and hasattr(self.app_context, "get_browser_assistant_bridge_status"):
            try:
                browser_status = self.app_context.get_browser_assistant_bridge_status()
            except Exception as exc:
                browser_status = {"running": False, "last_error": str(exc)}

        report = {
            "ok": bool(self.backend_entry.is_file() and data_dir.exists()),
            "deep": bool(deep),
            "backend_entry": {
                "exists": self.backend_entry.is_file(),
                "path": str(self.backend_entry),
            },
            "data_dir": {
                "exists": data_dir.exists(),
                "path": str(data_dir),
                "missing_core_files": self._collect_missing_core_files(),
            },
            "llm": self._probe_llm(llm_stages, deep=deep),
            "automation_hub": self._probe_automation_hub(automation_config, deep=deep),
            "browser_bridge": {
                "enabled": bool(settings_repository and settings_repository.get_setting(KEY_BROWSER_ASSISTANT_BRIDGE_ENABLED, True)),
                "port": int(settings_repository.get_setting(KEY_BROWSER_ASSISTANT_BRIDGE_PORT, 18766) if settings_repository else 18766),
                "configured_trusted_extension_ids": self._resolve_trusted_extension_ids(),
                "extension_whitelist_required": str(settings_repository.get_setting(KEY_ASSISTANT_UPGRADE_CHANNEL, "stable") if settings_repository else "stable") == "stable",
                "status": browser_status,
            },
            "sync": {
                "news": bool(settings_repository and settings_repository.get_setting(KEY_NEWS_AUTO_SYNC_ENABLED, False)),
                "notes": bool(settings_repository and settings_repository.get_setting(KEY_NOTES_AUTO_SYNC_ENABLED, False)),
                "tasks": bool(settings_repository and settings_repository.get_setting(KEY_TASKS_AUTO_SYNC_ENABLED, False)),
                "translations": bool(settings_repository and settings_repository.get_setting(KEY_TRANSLATIONS_AUTO_SYNC_ENABLED, False)),
                "vocabulary": bool(settings_repository and settings_repository.get_setting(KEY_VOCABULARY_AUTO_SYNC_ENABLED, False)),
            },
            "desktop_pet": {
                "root_exists": self.nana_root.exists(),
                "launcher_exists": self.nana_launcher.exists(),
                "portable_java_found": self._find_portable_java() is not None,
                "portable_java_path": self._find_portable_java() or "",
            },
            "command_whitelist": {
                "configured": sorted(command_whitelist),
                "missing_required": sorted(self.REQUIRED_COMMANDS - command_whitelist),
            },
            "release_channel": str(settings_repository.get_setting(KEY_ASSISTANT_UPGRADE_CHANNEL, "stable") if settings_repository else "stable"),
            "plugins": plugin_contract,
        }
        report["summary"] = self.format_report(report)
        return report

    def derive_conclusions(self, report: dict[str, Any]) -> dict[str, bool]:
        backend_entry = report.get("backend_entry") or {}
        data_dir = report.get("data_dir") or {}
        llm = report.get("llm") or {}
        automation_hub = report.get("automation_hub") or {}
        browser_bridge = report.get("browser_bridge") or {}
        sync = report.get("sync") or {}
        return {
            "can_start": bool(backend_entry.get("exists") and data_dir.get("exists")),
            "can_sync": any(sync.values()),
            "can_bridge": bool(browser_bridge.get("enabled") and (browser_bridge.get("status") or {}).get("running", False)),
            "can_recover": bool(
                llm.get("connection_state") in {"configured", "online"}
                or automation_hub.get("state") in {"configured", "online"}
            ),
        }

    def derive_attributions(self, report: dict[str, Any]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        backend_entry = report.get("backend_entry") or {}
        data_dir = report.get("data_dir") or {}
        llm = report.get("llm") or {}
        automation_hub = report.get("automation_hub") or {}
        browser_bridge = report.get("browser_bridge") or {}
        sync = report.get("sync") or {}
        command_whitelist = report.get("command_whitelist") or {}
        plugins = report.get("plugins") or {}

        if not backend_entry.get("exists"):
            items.append({"category": "配置缺失", "message": "入口脚本不存在", "severity": "critical"})
        if not data_dir.get("exists"):
            items.append({"category": "状态损坏", "message": "数据目录不存在", "severity": "critical"})
        missing_files = data_dir.get("missing_core_files") or []
        if missing_files:
            items.append({"category": "状态损坏", "message": f"核心数据文件缺失: {', '.join(missing_files)}", "severity": "warning"})

        llm_state = llm.get("connection_state", "unconfigured")
        if llm_state == "unconfigured":
            items.append({"category": "配置缺失", "message": "LLM Provider 未配置，AI 对话和自治功能不可用", "severity": "warning"})
        elif llm_state == "offline":
            items.append({"category": "服务未启动", "message": f"LLM Provider 离线: {llm.get('message', '')}", "severity": "warning"})

        hub_state = automation_hub.get("state", "unconfigured")
        if hub_state == "unconfigured":
            items.append({"category": "配置缺失", "message": "automation-hub 未配置", "severity": "info"})
        elif hub_state == "configured_no_token":
            items.append({"category": "配置不完整", "message": "automation-hub 地址已配置但 token 缺失，同步功能可能受限", "severity": "warning"})
        elif hub_state in {"offline", "error"}:
            items.append({"category": "服务未启动", "message": f"automation-hub 不可达: {automation_hub.get('message', '')}", "severity": "warning"})

        if browser_bridge.get("enabled") and not (browser_bridge.get("status") or {}).get("running", False):
            last_err = (browser_bridge.get("status") or {}).get("last_error", "")
            if "address already in use" in last_err.lower() or "10048" in last_err:
                items.append({"category": "端口冲突", "message": f"浏览器桥端口被占用: {last_err}", "severity": "warning"})
            else:
                items.append({"category": "服务未启动", "message": f"浏览器桥未运行: {last_err or '原因不明'}", "severity": "info"})

        if browser_bridge.get("enabled") and browser_bridge.get("extension_whitelist_required") and not (browser_bridge.get("configured_trusted_extension_ids") or []):
            items.append({"category": "配置缺失", "message": "稳定通道已启用浏览器桥，但未配置扩展白名单", "severity": "warning"})

        if not any(sync.values()):
            items.append({"category": "配置缺失", "message": "所有自动同步均已关闭", "severity": "info"})

        missing_cmds = command_whitelist.get("missing_required") or []
        if missing_cmds:
            items.append({"category": "配置缺失", "message": f"命令白名单缺少: {', '.join(missing_cmds)}", "severity": "warning"})

        counts = plugins.get("counts") or {}
        if sum(counts.values()) == 0:
            items.append({"category": "兼容路径残留", "message": "无插件注册，插件边界未接入", "severity": "info"})

        return items

    def derive_suggestions(self, attributions: list[dict[str, str]]) -> list[str]:
        suggestions: list[str] = []
        categories_seen = {a["category"] for a in attributions}
        if "配置缺失" in categories_seen:
            if any("LLM" in a["message"] for a in attributions):
                suggestions.append("打开 设置 → LLM Provider，填写 API 地址和密钥。")
            if any("automation-hub" in a["message"] for a in attributions):
                suggestions.append("打开 设置 → automation-hub，配置 API 地址。")
            if any("命令白名单" in a["message"] for a in attributions):
                suggestions.append("打开 设置 → 命令治理，把缺失命令加入白名单。")
            if any("扩展白名单" in a["message"] for a in attributions):
                suggestions.append("打开 设置 → 浏览器桥，至少填写一个受信任扩展 ID。")
            if any("同步" in a["message"] for a in attributions):
                suggestions.append("打开 设置 → 自动同步，启用至少一个数据域。")
        if "配置不完整" in categories_seen and any("token" in a["message"] for a in attributions):
            suggestions.append("打开 设置 → automation-hub，填写 API token 以启用完整同步。")
        if "端口冲突" in categories_seen:
            suggestions.append("关闭占用端口的进程，或在设置中更换浏览器桥端口。")
        if "服务未启动" in categories_seen and any("LLM" in a["message"] for a in attributions):
            suggestions.append("检查 LLM API 服务是否已启动，网络是否可达。")
        if "状态损坏" in categories_seen:
            suggestions.append("尝试重新初始化数据目录，或从备份恢复。")
        return suggestions

    def build_debug_assets(self, report: dict[str, Any]) -> dict[str, Any]:
        conclusions = self.derive_conclusions(report)
        issues = self.derive_attributions(report)
        suggestions = self.derive_suggestions(issues)
        severity_rank = {"critical": 0, "warning": 1, "info": 2}
        issues = sorted(
            issues,
            key=lambda item: (severity_rank.get(str(item.get("severity") or "info"), 99), str(item.get("category") or "")),
        )
        return {
            "hero": [
                {"key": "can_start", "label": "can_start", "value": bool(conclusions.get("can_start")), "status": "ok" if conclusions.get("can_start") else "fail"},
                {"key": "can_sync", "label": "can_sync", "value": bool(conclusions.get("can_sync")), "status": "ok" if conclusions.get("can_sync") else "fail"},
                {"key": "can_bridge", "label": "can_bridge", "value": bool(conclusions.get("can_bridge")), "status": "ok" if conclusions.get("can_bridge") else "fail"},
                {"key": "can_recover", "label": "can_recover", "value": bool(conclusions.get("can_recover")), "status": "ok" if conclusions.get("can_recover") else "fail"},
            ],
            "issues": issues,
            "suggestions": list(dict.fromkeys(suggestions)),
            "detail_cards": [
                {"key": "llm", "title": "llm", "data": report.get("llm") or {}},
                {"key": "automation_hub", "title": "automation_hub", "data": report.get("automation_hub") or {}},
                {"key": "browser_bridge", "title": "browser_bridge", "data": report.get("browser_bridge") or {}},
                {"key": "sync", "title": "sync", "data": report.get("sync") or {}},
                {"key": "command_whitelist", "title": "command_whitelist", "data": report.get("command_whitelist") or {}},
                {"key": "plugins", "title": "plugins", "data": report.get("plugins") or {}},
            ],
        }

    def build_status_summary(self, debug_assets: dict[str, Any] | None = None) -> str:
        """Build a one-line status summary from debug_assets, usable by any UI entry."""
        assets = debug_assets or {}
        hero = {item.get("key"): item for item in (assets.get("hero") or []) if isinstance(item, dict)}
        issues = [item for item in (assets.get("issues") or []) if isinstance(item, dict)]
        critical_count = sum(1 for item in issues if item.get("severity") == "critical")
        warning_count = sum(1 for item in issues if item.get("severity") == "warning")
        if critical_count:
            return f"Doctor found {critical_count} critical, {warning_count} warning issues"
        if warning_count:
            return f"Doctor found {warning_count} warning issues"
        if hero and not hero.get("can_start", {}).get("value", True):
            return "Doctor: startup blocked"
        if hero:
            return "Doctor: system healthy"
        return "Doctor: no diagnostics available"

    def build_continue_suggestion(self, debug_assets: dict[str, Any] | None = None) -> dict[str, str] | None:
        """Build a low-interruption suggestion card from debug_assets issues."""
        assets = debug_assets or {}
        issues = [item for item in (assets.get("issues") or []) if isinstance(item, dict)]
        if not issues:
            return None
        top_issue = issues[0]
        severity = str(top_issue.get("severity") or "info")
        prefix = "Fix needed:" if severity in {"critical", "warning"} else "Tip:"
        return {
            "text": f"{prefix} {top_issue.get('message') or 'check diagnostics'}",
            "action": "open_doctor",
        }

    def format_report(self, report: dict[str, Any]) -> str:
        llm = report.get("llm") or {}
        automation_hub = report.get("automation_hub") or {}
        browser_bridge = report.get("browser_bridge") or {}
        desktop_pet = report.get("desktop_pet") or {}
        command_whitelist = report.get("command_whitelist") or {}
        plugins = report.get("plugins") or {}
        sync = report.get("sync") or {}
        debug_assets = self.build_debug_assets(report)
        hero_by_key = {item.get("key"): item for item in debug_assets.get("hero") or []}
        attributions = debug_assets.get("issues") or []
        suggestions = debug_assets.get("suggestions") or []

        lines = [
            "[诊断结论]",
            f"能否启动: {'是' if hero_by_key.get('can_start', {}).get('value') else '否'}",
            f"能否同步: {'是' if hero_by_key.get('can_sync', {}).get('value') else '否'}",
            f"能否桥接: {'是' if hero_by_key.get('can_bridge', {}).get('value') else '否'}",
            f"能否恢复: {'是' if hero_by_key.get('can_recover', {}).get('value') else '否'}",
            "",
        ]

        if attributions:
            lines.append("[失败归因]")
            for attr in attributions:
                severity_tag = {"critical": "严重", "warning": "警告", "info": "提示"}.get(attr["severity"], "提示")
                lines.append(f"[{severity_tag}] {attr['category']}: {attr['message']}")
            lines.append("")

        if suggestions:
            lines.append("[修复建议]")
            for idx, suggestion in enumerate(suggestions, 1):
                lines.append(f"{idx}. {suggestion}")
            lines.append("")

        lines += [
            "[桌面入口]",
            f"入口脚本: {'正常' if (report.get('backend_entry') or {}).get('exists') else '缺失'}",
            f"数据目录: {'正常' if (report.get('data_dir') or {}).get('exists') else '缺失'}",
            f"缺失核心文件: {', '.join((report.get('data_dir') or {}).get('missing_core_files') or ['无'])}",
            "",
            "[LLM Provider 链路]",
            llm.get("topology_summary") or "未生成",
            f"连通性: {llm.get('connection_state', 'unknown')}",
            f"说明: {llm.get('message', '未测试')}",
            "",
            "[automation-hub]",
            f"Base URL: {automation_hub.get('base_url') or '未配置'}",
            f"状态: {automation_hub.get('state', 'unknown')}",
            f"说明: {automation_hub.get('message', '未测试')}",
            "",
            "[浏览器桥]",
            f"启用: {'是' if browser_bridge.get('enabled') else '否'}",
            f"端口: {browser_bridge.get('port')}",
            f"发布态白名单: {'已配置' if (browser_bridge.get('configured_trusted_extension_ids') or []) else '缺失'}",
            f"运行态: {json.dumps(browser_bridge.get('status') or {}, ensure_ascii=False)}",
            "",
            "[自动同步]",
            ", ".join(f"{name}={'开' if enabled else '关'}" for name, enabled in sync.items()),
            "",
            "[桌宠依赖]",
            f"目录: {'存在' if desktop_pet.get('root_exists') else '缺失'}",
            f"启动器: {'存在' if desktop_pet.get('launcher_exists') else '缺失'}",
            f"便携 Java: {desktop_pet.get('portable_java_path') or '未找到'}",
            "",
            "[命令治理]",
            f"缺失白名单: {', '.join(command_whitelist.get('missing_required') or ['无'])}",
            f"发布通道: {report.get('release_channel') or 'stable'}",
            "",
            "[插件边界]",
            f"扩展点: {', '.join(item.get('display_name') for item in plugins.get('extension_points') or []) or '未定义'}",
            f"已注册: {json.dumps(plugins.get('counts') or {}, ensure_ascii=False)}",
        ]
        return "\n".join(lines).strip()

    def _collect_missing_core_files(self) -> list[str]:
        """核心数据健康检查：P1-6 之后底层是 SQLite，不再有独立 JSON 文件，
        改为通过 ``load_json(key, _SENTINEL)`` 判断键是否曾被写入过。"""
        if self.data_store is None:
            return []
        sentinel = object()
        missing = []
        for key in ("settings", "tasks", "notes", "news", "translations", "wordbooks", "words"):
            try:
                if self.data_store.load_json(key, sentinel) is sentinel:
                    missing.append(key)
            except Exception:
                missing.append(key)
        return missing

    def _probe_automation_hub(self, config, *, deep: bool) -> dict[str, Any]:
        if not config.has_base_url:
            return {"state": "unconfigured", "base_url": None, "message": "未配置 automation-hub API 地址。"}
        if not deep:
            if not config.has_token:
                return {
                    "state": "configured_no_token",
                    "base_url": config.base_url,
                    "message": "已配置地址但未填写 token，同步功能可能受限。",
                }
            return {
                "state": "configured",
                "base_url": config.base_url,
                "message": "已配置地址；深度模式下会进一步验证 /health 和 /auth/me。",
            }
        try:
            client = create_automation_hub_client(settings_repository=self.settings_repository)
            result = client.probe_connection()
            return {
                "state": "online",
                "base_url": result.get("base_url"),
                "message": result.get("token_status") or "连接正常",
                "health": result.get("health") or {},
                "identity": result.get("identity") or {},
            }
        except AutomationHubClientError as exc:
            return {"state": "offline", "base_url": config.base_url, "message": str(exc)}
        except Exception as exc:
            return {"state": "error", "base_url": config.base_url, "message": str(exc)}

    def _probe_llm(self, stages, *, deep: bool) -> dict[str, Any]:
        snapshot = {
            "stages": [stage.__dict__ for stage in stages],
            "topology_summary": summarize_llm_provider_topology(stages),
            "connection_state": "configured" if any(stage.configured for stage in stages) else "unconfigured",
            "message": "已生成 Provider 链路快照。",
        }
        if not deep:
            return snapshot
        try:
            client = create_fallback_client(settings_repository=self.settings_repository)
            result = client.test_connection()
            snapshot["connection_state"] = "online"
            snapshot["message"] = f"主链路测试成功: {result.get('model') or 'unknown'}"
            snapshot["probe"] = result
            if hasattr(client, "get_runtime_snapshot"):
                snapshot["runtime_snapshot"] = client.get_runtime_snapshot()
        except Exception as exc:
            snapshot["connection_state"] = "offline"
            snapshot["message"] = str(exc)
        return snapshot

    def _resolve_command_whitelist(self) -> set[str]:
        if self.settings_repository is None:
            return set()
        raw = self.settings_repository.get_setting(KEY_COMMAND_WHITELIST, [])
        if isinstance(raw, str):
            return {item.strip() for item in raw.split(",") if item.strip()}
        return {str(item).strip() for item in raw if str(item).strip()}

    def _resolve_trusted_extension_ids(self) -> list[str]:
        if self.settings_repository is None:
            return []
        raw = self.settings_repository.get_setting(KEY_BROWSER_ASSISTANT_TRUSTED_EXTENSION_IDS, [])
        if isinstance(raw, str):
            return [item.strip() for item in raw.splitlines() if item.strip()]
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        return []

    def _find_portable_java(self) -> str | None:
        candidates = [
            self.nana_root / "runtime" / "bin" / "javaw.exe",
            self.nana_root / "runtime" / "bin" / "java.exe",
        ]
        if self.nana_root.exists():
            candidates.extend(self.nana_root.glob("runtime/**/bin/javaw.exe"))
            candidates.extend(self.nana_root.glob("runtime/**/bin/java.exe"))
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    # ── (build_status_summary and build_continue_suggestion are defined above at lines 242–271) ──
