"""System, settings, and provider management JSON-RPC handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.provider_config import dict_to_provider, provider_to_dict
from core.settings_keys import (
    KEY_AI_AUTO_TAG_ENABLED,
    KEY_AI_AUTO_TAG_THRESHOLD,
    KEY_DAILY_SUMMARY_ENABLED,
    KEY_DAILY_SUMMARY_TIME,
)
from backend.i18n import t

logger = logging.getLogger("siyi.backend.handlers.system_settings")


def register_system_settings(registry, context, ws_port: int, ak_service) -> None:
    """Register system, settings, and provider-related JSON-RPC methods."""

    def _recreate_ai_client() -> None:
        try:
            context.recreate_ai_client()
        except Exception as exc:
            logger.warning("Failed to recreate AI client: %s", exc)

    def _rebuild_gateway() -> None:
        """内核热换：agent_kernel / kernel_cli_path 变更后重建执行内核网关。

        与 headless_main 启动注入共用 build_gateway_from_repository，
        保证运行时换内核与重启换内核行为一致（type=none/dsh/claude_code/codex/auto）。
        """
        try:
            from services.agent_gateway import build_gateway_from_repository

            context.agent_gateway = build_gateway_from_repository(context.settings_repository)
            logger.info(
                "AgentGateway hot-swapped: kernel=%s",
                getattr(context.agent_gateway, "name", "?"),
            )
        except Exception as exc:
            logger.warning("AgentGateway hot-swap failed: %s", exc, exc_info=True)

    # ── settings.get ──
    # V2.1 批次1#8-3: 敏感 key（api_key/token/secret/password 等）返回时脱敏，
    # 防止明文密钥经 RPC 泄出（复用 core.env_loader._is_sensitive_key/mask_secret）。
    async def settings_get(key: str = "", **kwargs: Any) -> Any:
        if key:
            # P1 修复：settings_repository.get_setting 可能涉及文件 I/O，用 to_thread 避免阻塞
            value = await asyncio.to_thread(context.settings_repository.get_setting, key, None)
            # 敏感 key 且值为字符串 → 脱敏
            if isinstance(value, str):
                from core.env_loader import _is_sensitive_key, mask_secret

                if _is_sensitive_key(key) and value:
                    return {
                        "key": key,
                        "masked": True,
                        "value": mask_secret(value),
                    }
            return value
        # U6（S3-5，2026-09-16）：空 key 原先返回 {"message":..., "hint":"/api/v1/settings
        # in future"} 这种「伪成功」dict——客户端无法判断成败，且 hint 是早已作废的
        # REST 迁移残留话术。改为与仓内其它 handler 一致的诚实错误结构。
        return {"success": False, "error": t("error.missing_param", name="key")}

    registry.register("settings.get", settings_get)

    # ── settings.set ──
    async def settings_set(key: str = "", value: Any = None, **kwargs: Any) -> bool:
        # P0 修复：key/value 缺失时优雅拒绝，避免空参数调用抛 TypeError
        if not key:
            logger.warning("P0-2026-08-17 settings.set 拒绝写入: missing key/value")
            return False
        # P2-6: 写入前校验 key + 类型，严重错误拒绝并返回 False
        repo = context.settings_repository
        if hasattr(repo, "validate_setting"):
            ok, err = await asyncio.to_thread(repo.validate_setting, key, value)
            if not ok:
                logger.warning("P2-6 settings.set 拒绝写入: key=%s, error=%s", key, err)
                return False
        # P1 修复：settings_repository.set_setting 涉及文件写入，用 to_thread 避免阻塞事件循环
        await asyncio.to_thread(repo.set_setting, key, value)

        # Sync flat LLM keys -> provider_configs
        if key.startswith("llm_") and not key.startswith("llm_local_") and not key.startswith("llm_fallback_"):
            providers_raw = await asyncio.to_thread(
                context.settings_repository.get_setting,
                "provider_configs",
                [],
            )
            if providers_raw and isinstance(providers_raw, list):
                providers = [dict_to_provider(p) for p in providers_raw if isinstance(p, dict)]
                primary = next(
                    (p for p in providers if p.enabled and not p.is_fallback and not p.is_local),
                    None,
                )
                if primary:
                    updated = False
                    if key == "llm_api_key" and value:
                        primary.api_key = str(value)
                        updated = True
                    elif key == "llm_api_base" and value:
                        primary.api_base = str(value)
                        updated = True
                    elif key == "llm_model" and value:
                        primary.model = str(value)
                        updated = True
                    if updated:
                        # P1-14 修复：provider sync 失败时回滚 flat key，避免数据丢失。
                        # 旧实现在写 provider_configs 后无条件清空 flat key，若
                        # provider_configs 写入抛异常，异常会传播跳过清空（安全）；
                        # 但若写入"成功"但数据损坏，或后续 recreate_ai_client 失败，
                        # flat key 已被清空 → 值从两处都丢失。
                        # 修复：用 try/except 包裹 provider sync，失败时回滚 flat key
                        # 为原值（已在函数入口写入），并向上抛出让调用方感知失败。
                        try:
                            await asyncio.to_thread(
                                context.settings_repository.set_setting,
                                "provider_configs",
                                [provider_to_dict(p) for p in providers],
                            )
                            # After manual flat-key sync, drop legacy flat key so
                            # ApiKeyService remains the single source of truth.
                            # 仅在 provider_configs 写入成功后才清空 flat key。
                            await asyncio.to_thread(
                                context.settings_repository.set_setting,
                                key,
                                "",
                            )
                        except Exception:
                            # provider sync 失败 → 回滚 flat key 为用户传入的原值
                            # （函数入口已写入 value，此处确保它不被清空）
                            logger.exception(
                                "provider_configs sync failed for key=%s, flat key retained with user value",
                                key,
                            )
                            # 不清空 flat key —— 保留用户刚写入的值作为 fallback
                            raise

        if key.startswith("llm_") or key == "provider_configs":
            # P1 修复：recreate_ai_client 同步重建 AI 客户端（可能涉及解密、初始化），
            # 用 to_thread 避免阻塞事件循环
            await asyncio.to_thread(_recreate_ai_client)

        # 内核热换：agent_kernel / kernel_cli_path 变更后重建执行内核网关
        # （无需重启后端；kernel.status 立即反映新内核，前端徽章自动更新）
        if key in ("agent_kernel", "kernel_cli_path"):
            await asyncio.to_thread(_rebuild_gateway)
        return True

    registry.register("settings.set", settings_set)

    # ── settings.set_auto_tag ──
    async def settings_set_auto_tag(
        enabled: bool = False,
        threshold: float = 0.7,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """设置 AI 自动打标/分类开关与置信度阈值。"""
        await asyncio.to_thread(
            context.settings_repository.set_setting,
            KEY_AI_AUTO_TAG_ENABLED,
            bool(enabled),
        )
        await asyncio.to_thread(
            context.settings_repository.set_setting,
            KEY_AI_AUTO_TAG_THRESHOLD,
            float(threshold),
        )
        return {"success": True, "enabled": bool(enabled), "threshold": float(threshold)}

    registry.register_with_schema(
        "settings.set_auto_tag",
        settings_set_auto_tag,
        schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "threshold": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
    )

    # ── settings.get_auto_tag ──
    async def settings_get_auto_tag(**kwargs: Any) -> dict[str, Any]:
        """获取当前 AI 自动打标/分类配置。"""
        settings = getattr(context, "settings_repository", None)
        if settings is None:
            return {"enabled": False, "threshold": 0.7}
        enabled = await asyncio.to_thread(
            settings.get_setting,
            KEY_AI_AUTO_TAG_ENABLED,
            False,
        )
        threshold = await asyncio.to_thread(
            settings.get_setting,
            KEY_AI_AUTO_TAG_THRESHOLD,
            0.7,
        )
        return {"enabled": bool(enabled), "threshold": float(threshold or 0.7)}

    registry.register("settings.get_auto_tag", settings_get_auto_tag)

    # ── settings.set_daily_summary ──
    async def settings_set_daily_summary(
        enabled: bool = False,
        time: str = "09:00",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """设置 AI 日报开关与生成时间（HH:MM）。"""
        time_value = str(time or "09:00")
        await asyncio.to_thread(
            context.settings_repository.set_setting,
            KEY_DAILY_SUMMARY_ENABLED,
            bool(enabled),
        )
        await asyncio.to_thread(
            context.settings_repository.set_setting,
            KEY_DAILY_SUMMARY_TIME,
            time_value,
        )
        # 实时更新调度器
        try:
            from backend import weekly_report_scheduler

            weekly_report_scheduler.configure_daily(enabled=bool(enabled), time=time_value)
        except Exception as exc:
            logger.warning("failed to reconfigure daily summary scheduler: %s", exc)
        return {"success": True, "enabled": bool(enabled), "time": time_value}

    registry.register_with_schema(
        "settings.set_daily_summary",
        settings_set_daily_summary,
        schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "time": {"type": "string", "pattern": "^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$"},
            },
        },
    )

    # ── settings.get_daily_summary ──
    async def settings_get_daily_summary(**kwargs: Any) -> dict[str, Any]:
        """获取当前 AI 日报配置。"""
        settings = getattr(context, "settings_repository", None)
        if settings is None:
            return {"enabled": False, "time": "09:00"}
        enabled = await asyncio.to_thread(
            settings.get_setting,
            KEY_DAILY_SUMMARY_ENABLED,
            False,
        )
        time_value = await asyncio.to_thread(
            settings.get_setting,
            KEY_DAILY_SUMMARY_TIME,
            "09:00",
        )
        return {"enabled": bool(enabled), "time": str(time_value or "09:00")}

    registry.register("settings.get_daily_summary", settings_get_daily_summary)

    # ── system.status ──
    async def system_status(**kwargs: Any) -> dict[str, Any]:
        return {
            "mode": "headless",
            "services": {
                "ai": context.ai_client is not None,
                "memory": context.memory_index_service is not None,
                "knowledge_graph": context.knowledge_graph_store is not None,
            },
            "methods": registry.list_methods(),
        }

    registry.register("system.status", system_status)

    # ── system.info ──
    async def system_info(**kwargs: Any) -> dict[str, Any]:
        return {
            "version": "0.1.0",
            "name": "思忆集 backend",
            "ws_port": ws_port,
        }

    registry.register("system.info", system_info)

    # ── system.active_tasks ──
    async def system_active_tasks(**kwargs: Any) -> list[dict[str, Any]]:
        """Return current active tasks from AutonomousAssistantManager.

        N-F（2026-09-17）：采集失败时由 debug 升级为 warning——此前真异常被
        debug 级静默吞掉、面板恒显示"无活动任务"（巡检在跑却看不见），属可观测性
        黑洞。返回值仍为 list（前端 backgroundJobsStore/AgentMonitorModule 直接
        当数组消费，改信封会破坏公共接口），仅提升日志可见性。
        """
        manager = getattr(context, "autonomous_assistant_manager", None)
        if manager and hasattr(manager, "get_unfinished_tasks_info"):
            try:
                return await asyncio.to_thread(manager.get_unfinished_tasks_info)
            except Exception as exc:
                logger.warning("system.active_tasks: failed to get tasks: %s", exc, exc_info=True)
        return []

    registry.register("system.active_tasks", system_active_tasks)

    # ── system.tailscale_status ──
    # 检测本机 Tailscale 状态，供手机端自动发现桌面端 IP。
    # 优先调用 tailscale CLI（最准确），失败时回退到扫描 100.x.x.x 网卡。
    async def system_tailscale_status(**kwargs: Any) -> dict[str, Any]:
        import socket
        import shutil

        result: dict[str, Any] = {"connected": False, "tailscale_ip": None, "hostname": None}

        # 方法 1：调用 tailscale status CLI（最可靠）
        ts_bin = shutil.which("tailscale")
        if ts_bin:
            try:
                proc = await asyncio.create_subprocess_exec(
                    ts_bin,
                    "status",
                    "--json",
                    "--self",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
                if proc.returncode == 0:
                    import json

                    data = json.loads(stdout.decode("utf-8", errors="replace"))
                    self_info = data.get("Self") or data.get("self") or {}
                    ts_ip = self_info.get("TailscaleIPs") or self_info.get("tailscale_ips") or []
                    if ts_ip:
                        result["connected"] = True
                        result["tailscale_ip"] = ts_ip[0]
                        result["hostname"] = self_info.get("HostName") or self_info.get("host_name")
                        return result
            except Exception as exc:
                logger.debug("tailscale status CLI failed: %s", exc)

        # 方法 2：扫描本机网卡，找 100.64.0.0/10 段的 IPv4 地址
        # Tailscale 使用 100.64.0.0/10 CGNAT 段，这是协议层面的特征。
        try:
            hostname = socket.gethostname()
            addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)
            for addr in addrs:
                ip = addr[4][0]
                # Tailscale IP 段：100.64.0.0 - 100.127.255.255
                parts = ip.split(".")
                if len(parts) == 4 and parts[0] == "100" and 64 <= int(parts[1]) <= 127:
                    result["connected"] = True
                    result["tailscale_ip"] = ip
                    result["hostname"] = hostname
                    break
        except Exception as exc:
            logger.debug("tailscale IP scan failed: %s", exc)

        return result

    registry.register("system.tailscale_status", system_tailscale_status)

    # ── system.health ──
    # 聚合各子系统健康状态，供前端统一状态面板展示。
    # 使用懒加载的单例 SystemHealthAggregator，带 10s 缓存。
    from services.system_health import SystemHealthAggregator

    _health_aggregator: SystemHealthAggregator | None = None

    def _get_health_aggregator() -> SystemHealthAggregator:
        nonlocal _health_aggregator
        if _health_aggregator is None:
            # P3-2: 缓存 TTL 可配置（环境变量 SIYI_HEALTH_CACHE_TTL，默认 10s）
            import os as _os

            _ttl = float(_os.environ.get("SIYI_HEALTH_CACHE_TTL", "10.0"))
            _health_aggregator = SystemHealthAggregator(context, cache_ttl=_ttl)
        return _health_aggregator

    async def system_health(**kwargs: Any) -> dict[str, Any]:
        force_refresh = bool(kwargs.get("force_refresh", False))
        agg = _get_health_aggregator()
        return await asyncio.to_thread(agg.to_dict, force_refresh=force_refresh)

    registry.register("system.health", system_health)

    # ── system.integrity ──
    # P1-2 / P2-3 / P2-4 / P1-4 的统一**只读**诊断出口（2026-09-18）。
    #
    # 为什么需要它：本批修的四处缺陷形状相同——"写入/解析失败被吞掉，上层不可观测"。
    # 修好"不再静默"只完成一半：用户还得有地方**问一句**"我的数据还好吗"。
    # 单人单机自用没有仪表盘与告警管道，故把观测收敛成一个可随时调用的 RPC。
    #
    # 两段内容，刻意并列而非互相替代：
    #   * notes  —— 实时盘面（每次现算：扫 notes 目录逐篇判定 + 孤儿 tmp 只读探测）
    #   * ledger —— 进程台账（发生过什么：损坏跳过 / store.json 写失败 / 级联失败）
    # 盘面与台账矛盾时**以盘面为准**（台账去重且有界，只作线索）。
    async def system_integrity(**kwargs: Any) -> dict[str, Any]:
        from core.integrity import snapshot as _ledger_snapshot

        report: dict[str, Any] = {"ledger": _ledger_snapshot()}

        repo = getattr(context, "personal_note_repository", None)
        probe = getattr(repo, "integrity_report", None)
        if callable(probe):
            # 适配器实现（DeskNotesNoteAdapter）才有实时盘面；本地 JSON 存储无此概念。
            report["notes"] = await asyncio.to_thread(probe)
        else:
            report["notes"] = None
            report["notes_unavailable_reason"] = "当前笔记仓储未提供 integrity_report（非 desk-notes 数据源）"
        return report

    registry.register("system.integrity", system_integrity)

    # ── Provider Management ──
    from core.provider_config import load_providers, provider_to_safe_dict

    async def provider_list(**params: Any) -> list[dict]:
        """List all providers (api_key masked)."""
        # load_providers 是同步函数（文件 I/O + 解密），用 to_thread 避免阻塞事件循环
        providers = await asyncio.to_thread(load_providers, context.settings_repository)
        return [provider_to_safe_dict(p) for p in providers]

    async def provider_add(**params: Any) -> dict:
        """Add a provider via ApiKeyService.

        接受 AGENTS.md 文档约定的字段名（base_url），同时兼容旧字段名（api_base）。
        """
        try:
            # P1 修复：ak_service.create_key 涉及文件写入 + 加密，用 to_thread 避免阻塞事件循环
            # base_url / api_base 互为别名，优先 base_url（文档约定）
            base_url = params.get("base_url") or params.get("api_base") or ""
            result = await asyncio.to_thread(
                ak_service.create_key,
                {
                    "name": params.get("name", ""),
                    "key_value": params.get("api_key", ""),
                    "base_url": base_url,
                    "model": params.get("model", ""),
                    "is_provider": True,
                    "enabled": bool(params.get("enabled", True)),
                    "is_local": bool(params.get("is_local", False)),
                    "is_fallback": bool(params.get("is_fallback", False)),
                    "description": params.get("description", ""),
                    "timeout": params.get("timeout", 300),
                },
            )
            await asyncio.to_thread(_recreate_ai_client)
            return {"success": True, "message": t("provider.added", name=result["name"])}
        except ValueError as e:
            return {"success": False, "message": str(e)}

    async def provider_remove(**params: Any) -> dict:
        """Remove a provider via ApiKeyService.

        接受 ``provider_id``（AGENTS.md 文档约定）或 ``name`` 作为标识符。
        """
        # provider_id / name 互为别名；手机端发 provider_id，桌面端发 name
        name = params.get("name", "") or str(params.get("provider_id", "") or "")
        try:
            # P1 修复：ak_service.delete_key 涉及文件写入，用 to_thread 避免阻塞事件循环
            await asyncio.to_thread(ak_service.delete_key, f"provider:{name}")
            await asyncio.to_thread(_recreate_ai_client)
            return {"success": True, "message": t("provider.removed", name=name)}
        except ValueError as e:
            return {"success": False, "message": str(e)}

    async def provider_update(**params: Any) -> dict:
        """Update a provider via ApiKeyService.

        接受 ``provider_id``（AGENTS.md 文档约定）或 ``name`` 作为标识符。
        更新字段可直接平铺在 params 中（手机端协议），也可嵌套在 ``updates`` 字段中（旧桌面端协议）。
        """
        # provider_id / name 互为别名
        name = params.get("name", "") or str(params.get("provider_id", "") or "")
        # 兼容两种传参：1) updates 嵌套 dict；2) 平铺字段（手机端 {provider_id, base_url, model, ...}）
        updates = params.get("updates", {})
        if not isinstance(updates, dict):
            updates = {}
        # 平铺字段：把除 name/provider_id/updates 外的字段视为更新
        reserved = {"name", "provider_id", "updates"}
        flat_updates = {k: v for k, v in params.items() if k not in reserved}
        merged_updates: dict[str, Any] = {**flat_updates, **updates}
        field_map = {
            "api_base": "base_url",
            "api_key": "key_value",
            "base_url": "base_url",
        }
        mapped = {}
        for k, v in merged_updates.items():
            target_key = field_map.get(k, k)
            mapped[target_key] = v
        try:
            # P1 修复：ak_service.update_key 涉及文件写入，用 to_thread 避免阻塞事件循环
            await asyncio.to_thread(ak_service.update_key, f"provider:{name}", mapped)
            await asyncio.to_thread(_recreate_ai_client)
            return {"success": True, "message": t("provider.updated", name=name)}
        except ValueError as e:
            return {"success": False, "message": str(e)}

    async def provider_health(**params: Any) -> dict:
        """Test provider connectivity.

        接受 ``provider_id``（AGENTS.md 文档约定）或 ``name`` 作为标识符。
        """
        # provider_id / name 互为别名
        name = params.get("name", "") or str(params.get("provider_id", "") or "")
        # load_providers 是同步函数（文件 I/O + 解密），用 to_thread 避免阻塞事件循环
        providers = await asyncio.to_thread(load_providers, context.settings_repository)
        target = None
        for p in providers:
            if p.name == name:
                target = p
                break
        if not target:
            return {"success": False, "message": t("provider.not_found", name=name)}

        ok, msg = await target.test_connectivity()
        models = await target.discover_models() if ok else []
        billing_error = "账户欠费" in msg or "配额耗尽" in msg
        return {
            "name": name,
            "available": ok,
            "message": msg,
            "models": models,
            "inference_ok": ok,
            "billing_error": billing_error,
            "is_local": target.is_local,
        }

    async def kiro_status() -> dict:
        """Get Kiro-gateway status."""
        import aiohttp

        status: dict[str, Any] = {"running": False, "accounts": 0, "message": ""}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get("http://127.0.0.1:8000/health") as resp:
                    if resp.status == 200:
                        status["running"] = True
                    else:
                        status["message"] = f"HTTP {resp.status}"

                async with session.get(
                    "http://127.0.0.1:8000/v1/models",
                    headers={"Authorization": "Bearer sk-kiro-local"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        status["models"] = [m.get("id") for m in data.get("data", [])]
        except aiohttp.ClientConnectorError:
            status["message"] = t("kiro.not_running")
        except Exception as e:
            logger.exception("kiro.status 获取 Kiro 网关状态失败")
            status["message"] = str(e)
        return status

    registry.register("provider.list", provider_list)
    registry.register("provider.add", provider_add)
    registry.register("provider.remove", provider_remove)
    registry.register("provider.update", provider_update)
    registry.register("provider.health", provider_health)
    registry.register("kiro.status", kiro_status)

    # ──────────────────────────────────────────────────────────────
    # P2.6: Provider 动态路由（按查询类型选择 provider）
    # ──────────────────────────────────────────────────────────────

    # ── provider.routing.get ──
    # 读取当前路由配置 + 已知 preset + 已启用 provider_configs 名
    async def provider_routing_get(**kwargs: Any) -> dict[str, Any]:
        from core.settings_keys import KEY_LLM_PROVIDER_ROUTING
        from ai.provider_routing import (
            _PROVIDER_ROUTING_PRESETS,
            list_enabled_provider_names,
            parse_routing_config,
        )

        raw = await asyncio.to_thread(context.settings_repository.get_setting, KEY_LLM_PROVIDER_ROUTING, "")
        routing = parse_routing_config(str(raw or ""))
        presets = [
            {
                "name": name,
                "display_name": p["display_name"],
                "best_for": list(p["best_for"]),
                "default_model": p["default_model"],
                "env_key": p["env_key"],
            }
            for name, p in _PROVIDER_ROUTING_PRESETS.items()
        ]
        available_names = await asyncio.to_thread(list_enabled_provider_names, context.settings_repository)
        return {
            "enabled": bool(routing),
            "routing": routing,
            "raw": raw,
            "presets": presets,
            "available_provider_names": available_names,
        }

    registry.register("provider.routing.get", provider_routing_get)

    # ── provider.routing.set ──
    # 写入路由配置；接受 dict 或 JSON 字符串；先校验再保存
    async def provider_routing_set(routing: Any = None, **kwargs: Any) -> dict[str, Any]:
        from core.settings_keys import KEY_LLM_PROVIDER_ROUTING
        from ai.provider_routing import (
            _PROVIDER_ROUTING_PRESETS,
            list_enabled_provider_names,
            validate_routing_config,
        )

        # 接受 dict 或 JSON 字符串
        if routing is None:
            return {"success": False, "error": "routing is required"}
        if isinstance(routing, str):
            try:
                import json as _json

                config = _json.loads(routing) if routing.strip() else {}
            except _json.JSONDecodeError as exc:
                return {"success": False, "error": f"invalid JSON: {exc}"}
        elif isinstance(routing, dict):
            config = routing
        else:
            return {"success": False, "error": "routing must be dict or JSON string"}

        # 校验 schema
        ok, err = validate_routing_config(config)
        if not ok:
            return {"success": False, "error": err}

        # 运行时校验：引用的 provider 必须在 preset 或 provider_configs 中
        known = set(_PROVIDER_ROUTING_PRESETS.keys()) | {"primary"}
        available_names = await asyncio.to_thread(list_enabled_provider_names, context.settings_repository)
        unknown_refs = [v for v in config.values() if v not in known and v not in available_names]
        if unknown_refs:
            return {
                "success": False,
                "error": (
                    f"routing references unknown providers: {unknown_refs}. "
                    f"available: presets={list(known - {'primary'})}, "
                    f"provider_configs={available_names}"
                ),
            }

        # 序列化为 JSON 字符串保存（与 schema 约定一致）
        import json as _json

        raw = _json.dumps(config, ensure_ascii=False) if config else ""
        await asyncio.to_thread(context.settings_repository.set_setting, KEY_LLM_PROVIDER_ROUTING, raw)
        # 触发 AI 客户端重建，让懒加载的路由表在下次调用时重新读取
        await asyncio.to_thread(_recreate_ai_client)
        logger.info("provider routing updated: %s", config)
        return {
            "success": True,
            "routing": config,
            "enabled": bool(config),
        }

    registry.register("provider.routing.set", provider_routing_set)

    # ── provider.routing.preview ──
    # 预览给定文本的路由决策（诊断用，不实际调用 LLM）
    async def provider_routing_preview(message: str = "", **kwargs: Any) -> dict[str, Any]:
        from ai.provider_routing import (
            ProviderRoutingTable,
            list_enabled_provider_names,
        )

        if not message:
            return {"error": "message is required"}
        table = await asyncio.to_thread(ProviderRoutingTable, settings_repository=context.settings_repository)
        health_monitor = getattr(context, "provider_health_monitor", None)
        if health_monitor is None:
            # 尝试从 AI 客户端内部获取
            ai_client = getattr(context, "ai_client", None)
            inner = getattr(ai_client, "_inner", None) if ai_client else None
            health_monitor = getattr(inner, "_health_monitor", None) if inner else None
        available_names = await asyncio.to_thread(list_enabled_provider_names, context.settings_repository)
        result = await asyncio.to_thread(table.preview, message, health_monitor, available_names)
        result["available_provider_names"] = available_names
        return result

    registry.register("provider.routing.preview", provider_routing_preview)

    # ── provider.routing.stats ──
    # 获取路由决策统计（来自 JudgeScheduler）
    async def provider_routing_stats(**kwargs: Any) -> dict[str, Any]:
        ai_client = getattr(context, "ai_client", None)
        if ai_client is None:
            return {"error": "ai_client not available"}
        judge = getattr(ai_client, "_judge", None) or getattr(ai_client, "_lazy_judge", None)
        if judge is None:
            # 尝试懒加载
            try:
                judge = ai_client._get_judge()
            except Exception:
                logger.exception("provider.routing.stats 懒加载 judge scheduler 失败")
                judge = None
        if judge is None:
            return {"error": "judge scheduler not available"}
        try:
            stats = await asyncio.to_thread(judge.get_stats)
            snapshot = await asyncio.to_thread(judge.get_status_snapshot)
            return {"stats": stats, "snapshot": snapshot}
        except Exception as exc:
            logger.exception("provider.routing.stats 获取路由统计失败")
            return {"error": f"failed to get judge stats: {exc}"}

    registry.register("provider.routing.stats", provider_routing_stats)
