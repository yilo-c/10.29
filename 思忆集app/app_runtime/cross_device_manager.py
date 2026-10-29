"""跨设备同步域：管理跨设备会话生命周期和启动/停止。"""

from __future__ import annotations

import logging

from cross_device.session import CrossDeviceSessionManager
from cross_device.capability_router import CrossDeviceCapabilityRouter

logger = logging.getLogger("siyi.cross_device_manager")


class CrossDeviceManager:
    """管理跨设备会话生命周期和启动/停止。"""

    def __init__(self, data_store, sync_orchestrator) -> None:
        self.session = CrossDeviceSessionManager(
            data_store=data_store,
            sync_orchestrator=sync_orchestrator,
        )
        self.router = CrossDeviceCapabilityRouter()

    def shutdown(self) -> None:
        """关闭跨设备会话。"""
        try:
            self.session.shutdown()
        except Exception:
            logger.debug("cross device session shutdown skipped", exc_info=True)
