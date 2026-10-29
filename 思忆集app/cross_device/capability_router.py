"""跨端能力路由：根据设备能力自动分配任务执行位置。

根据 dual-positioning-architecture-plan.md 第 5.4 节设计：
- 桌面端独有能力：Docker， local_rag, workflow_edit, full_tools, large_screen
- 手机端独有能力：voice, camera, notification, light_ai, quick_capture
- 两端共有能力：chat, notes, tasks, translate, memory_query, approval
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("siyi.cross_device.capability_router")

DEVICE_DESKTOP = "desktop"
DEVICE_ANDROID = "android"

# 设备健康状态常量
DEVICE_STATUS_ONLINE = "online"
DEVICE_STATUS_OFFLINE = "offline"
DEVICE_STATUS_UNKNOWN = "unknown"

# 设备能力矩阵
DEVICE_CAPABILITIES: dict[str, set[str]] = {
    DEVICE_DESKTOP: {"docker", "local_rag", "workflow_edit", "full_tools", "large_screen", "batch_import"},
    DEVICE_ANDROID: {"voice", "camera", "notification", "light_ai", "quick_capture", "gps", "biometric_auth"},
    "both": {"chat", "notes", "tasks", "translate", "memory_query", "approval", "daily_briefing"},
}

# 能力 → 首选设备映射
_CAPABILITY_PREFERRED_DEVICE: dict[str, str] = {
    "workflow_edit": DEVICE_DESKTOP,
    "local_rag": DEVICE_DESKTOP,
    "docker": DEVICE_DESKTOP,
    "batch_import": DEVICE_DESKTOP,
    "full_tools": DEVICE_DESKTOP,
    "voice": DEVICE_ANDROID,
    "camera": DEVICE_ANDROID,
    "quick_capture": DEVICE_ANDROID,
    "gps": DEVICE_ANDROID,
    "biometric_auth": DEVICE_ANDROID,
    "notification": DEVICE_ANDROID,
}

# 能力 → 描述映射
CAPABILITY_DESCRIPTIONS: dict[str, str] = {
    "docker": "Docker 容器执行",
    "local_rag": "本地 RAG 知识库检索",
    "workflow_edit": "可视化工作流编辑",
    "full_tools": "完整工具链",
    "large_screen": "大屏幕多面板",
    "batch_import": "批量文件导入",
    "voice": "语音输入",
    "camera": "拍照",
    "notification": "通知推送",
    "light_ai": "轻量 AI",
    "quick_capture": "快速捕获",
    "gps": "位置信息",
    "biometric_auth": "生物识别认证",
    "chat": "AI 对话",
    "notes": "笔记管理",
    "tasks": "任务管理",
    "translate": "翻译",
    "memory_query": "记忆查询",
    "approval": "审批处理",
    "daily_briefing": "每日回顾",
}


@dataclass
class CapabilityRoutingResult:
    """能力路由结果。"""
    capability: str
    preferred_device: str | None       # None = 两端均可
    can_handle_current: bool            # 当前设备能否处理
    hint: str = ""                      # 用户提示
    metadata: dict[str, Any] = field(default_factory=dict)


class CrossDeviceCapabilityRouter:
    """跨端能力路由。

    职责：
    1. 根据能力矩阵判断任务该在哪个设备执行
    2. 当任务不能在当前设备完成时，提示用户切换到合适设备
    3. 提供设备能力查询接口
    4. 可选集成 CapabilityRegistry 动态发现能力
    5. 跟踪设备在线状态，避免路由到离线设备
    """

    def __init__(self, capability_registry=None):
        self._current_device = DEVICE_DESKTOP  # 默认桌面端
        self._capability_registry = capability_registry
        # 设备健康状态: device_type -> {status, last_seen, dynamic_capabilities}
        self._device_status: dict[str, dict[str, Any]] = {
            DEVICE_DESKTOP: {
                "status": DEVICE_STATUS_UNKNOWN,
                "last_seen": 0.0,
                "dynamic_capabilities": set(),
            },
            DEVICE_ANDROID: {
                "status": DEVICE_STATUS_UNKNOWN,
                "last_seen": 0.0,
                "dynamic_capabilities": set(),
            },
        }

    def set_capability_registry(self, registry) -> None:
        self._capability_registry = registry

    def set_current_device(self, device_type: str) -> None:
        if device_type in (DEVICE_DESKTOP, DEVICE_ANDROID):
            self._current_device = device_type

    def heartbeat(self, device_type: str | None = None) -> None:
        """发送心跳，标记设备在线。

        由外部定时器周期性调用（建议每 60 秒）。
        也作为 update_device_health(online=True) 的便捷封装，
        自动更新最后可见时间。

        Args:
            device_type: 设备类型，默认当前设备。
        """
        device_type = device_type or self._current_device
        self.update_device_health(device_type, online=True)

    def update_device_health(self, device_type: str, online: bool) -> None:
        """更新设备在线状态和最后可见时间。"""
        if device_type not in self._device_status:
            return
        self._device_status[device_type]["status"] = (
            DEVICE_STATUS_ONLINE if online else DEVICE_STATUS_OFFLINE
        )
        if online:
            self._device_status[device_type]["last_seen"] = time.time()

    def update_device_capabilities(
        self, device_type: str, capabilities: list[str]
    ) -> None:
        """更新设备动态能力列表（从设备心跳中获取）。"""
        if device_type not in self._device_status:
            return
        self._device_status[device_type]["dynamic_capabilities"] = set(capabilities)
        self._device_status[device_type]["last_seen"] = time.time()
        self._device_status[device_type]["status"] = DEVICE_STATUS_ONLINE

    def is_device_online(self, device_type: str, max_age_seconds: float = 300.0) -> bool:
        """判断设备是否在线（在 max_age_seconds 内有心跳视为在线）。"""
        status_info = self._device_status.get(device_type)
        if status_info is None:
            return False
        if status_info["status"] == DEVICE_STATUS_OFFLINE:
            return False
        if status_info["last_seen"] > 0 and (time.time() - status_info["last_seen"]) > max_age_seconds:
            return False
        return status_info["status"] != DEVICE_STATUS_UNKNOWN

    def _collect_device_caps(self, device_type: str) -> set[str]:
        """收集指定设备的全部能力（静态 + 动态发现）。"""
        caps = set(DEVICE_CAPABILITIES.get(device_type, set()))
        caps.update(DEVICE_CAPABILITIES.get("both", set()))

        # 从注册表补充动态能力
        if self._capability_registry is not None:
            for spec in self._capability_registry.list_capabilities():
                if spec.enabled:
                    caps.add(spec.capability_id)

        # 从设备心跳补充设备特有动态能力
        status_info = self._device_status.get(device_type)
        if status_info:
            caps.update(status_info["dynamic_capabilities"])

        return caps

    def route(self, capability: str) -> CapabilityRoutingResult:
        """判断给定能力是否能由当前设备处理。

        考虑因素（按优先级）：
        1. 当前设备能否处理
        2. 首选设备是否在线
        3. 是否需要提示用户切换设备
        """
        preferred = _CAPABILITY_PREFERRED_DEVICE.get(capability)

        # 检查当前设备能力（静态 + 动态）
        current_caps = self._collect_device_caps(self._current_device)
        can_handle = capability in current_caps

        # 即使当前设备能处理，也要检查首选设备健康状态
        preferred_online = False
        if preferred and preferred != self._current_device:
            preferred_online = self.is_device_online(preferred)
        elif preferred == self._current_device:
            preferred_online = True

        hint = ""
        if not can_handle:
            if preferred:
                desc = CAPABILITY_DESCRIPTIONS.get(capability, capability)
                device_label = "桌面端" if preferred == DEVICE_DESKTOP else "手机端"
                if preferred_online:
                    hint = f"「{desc}」在{device_label}上更方便，已为你保存草稿，稍后继续？"
                else:
                    hint = f"「{desc}」需要在{device_label}上使用（当前{device_label}不在线），请稍后重试"
            else:
                desc = CAPABILITY_DESCRIPTIONS.get(capability, capability)
                hint = f"「{desc}」暂不支持，请稍后再试"

        online_device = self.is_device_online(self._current_device)

        return CapabilityRoutingResult(
            capability=capability,
            preferred_device=preferred,
            can_handle_current=can_handle,
            hint=hint,
            metadata={
                "requested_capability": capability,
                "current_device": self._current_device,
                "current_device_online": online_device,
                "preferred_device_online": preferred_online,
            },
        )

    def get_device_capabilities(self, device_type: str | None = None) -> dict[str, list[str]]:
        """获取指定或所有设备的能力清单（静态 + 注册表动态发现）。"""
        result: dict[str, list[str]] = {}

        if device_type:
            caps = set(DEVICE_CAPABILITIES.get(device_type, set()))
            both = set(DEVICE_CAPABILITIES.get("both", set()))
            result[device_type] = sorted(caps | both)
        else:
            for dev in (DEVICE_DESKTOP, DEVICE_ANDROID):
                caps = set(DEVICE_CAPABILITIES.get(dev, set()))
                both = set(DEVICE_CAPABILITIES.get("both", set()))
                result[dev] = sorted(caps | both)

        # 从注册表补充动态发现的能力
        if self._capability_registry is not None:
            for spec in self._capability_registry.list_capabilities():
                for dev in result:
                    if spec.capability_id not in result[dev]:
                        result[dev].append(spec.capability_id)
            for dev in result:
                result[dev] = sorted(result[dev])

        return result

    def get_capability_details(self, capability: str) -> dict[str, Any]:
        """返回某项能力的详细路由信息。"""
        details: dict[str, Any] = {
            "capability": capability,
            "description": CAPABILITY_DESCRIPTIONS.get(capability, ""),
            "preferred_device": _CAPABILITY_PREFERRED_DEVICE.get(capability),
            "available_on": [
                dev for dev in (DEVICE_DESKTOP, DEVICE_ANDROID)
                if capability in DEVICE_CAPABILITIES.get(dev, set())
                or capability in DEVICE_CAPABILITIES.get("both", set())
            ],
        }

        # 从注册表补充描述
        if self._capability_registry is not None:
            spec = self._capability_registry.get_capability(capability)
            if spec is not None:
                if not details["description"]:
                    details["description"] = spec.description
                if spec.enabled:
                    for dev in (DEVICE_DESKTOP, DEVICE_ANDROID):
                        if dev not in details["available_on"]:
                            details["available_on"].append(dev)

        return details

    def get_status_snapshot(self) -> dict[str, Any]:
        now = time.time()
        device_statuses = {}
        for dev, info in self._device_status.items():
            status_label = info["status"]
            if status_label == DEVICE_STATUS_ONLINE and info["last_seen"] > 0:
                age = now - info["last_seen"]
                if age > 300:
                    status_label = DEVICE_STATUS_UNKNOWN
                device_statuses[dev] = {
                    "status": status_label,
                    "last_seen_ago_seconds": round(age, 1),
                }
            else:
                device_statuses[dev] = {
                    "status": status_label,
                    "last_seen_ago_seconds": None,
                }

        return {
            "current_device": self._current_device,
            "current_device_online": self.is_device_online(self._current_device),
            "device_statuses": device_statuses,
            "current_capabilities": self.get_device_capabilities(self._current_device),
        }
