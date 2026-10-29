"""AutomationHubService — 包装 integrations.automation_hub_client 供 UI 层使用。"""

from __future__ import annotations

from integrations.automation_hub_client import (
    AutomationHubClientError,
    AutomationHubConnectionConfig,
    create_automation_hub_client,
    resolve_automation_hub_connection_config,
)


class AutomationHubService:
    """automation-hub 客户端服务，封装连接与客户端工厂函数。"""

    def __init__(self, settings_repository=None):
        self.settings_repository = settings_repository

    def create_client(self, base_url=None, api_token=None, session=None):
        return create_automation_hub_client(
            settings_repository=self.settings_repository,
            base_url=base_url,
            api_token=api_token,
            session=session,
        )

    def resolve_connection_config(self):
        return resolve_automation_hub_connection_config(
            settings_repository=self.settings_repository,
        )

    @property
    def ClientError(self):
        return AutomationHubClientError

    @property
    def ConnectionConfig(self):
        return AutomationHubConnectionConfig
