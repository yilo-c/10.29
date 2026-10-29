"""ExtensionService — 包装 core.extension_registry 供 UI 层使用。"""

from __future__ import annotations

from core.extension_registry import ExtensionRegistry, ExtensionSource, ExtensionSourceType


class ExtensionService:
    """联网扩展注册服务，封装 ExtensionRegistry 并提供类型工厂。"""

    def __init__(self, data_store=None):
        self._data_store = data_store
        self._registry = None  # 延迟初始化，由 get_registry() 按需创建

    def get_registry(self, data_store=None):
        if self._registry is None:
            resolved = data_store or self._data_store
            if resolved is not None:
                self._registry = ExtensionRegistry(data_store=resolved)
        return self._registry

    def register_source(self, source):
        reg = self.get_registry()
        if reg is not None:
            reg.register_source(source)

    def unregister_source(self, source_id: str):
        reg = self.get_registry()
        if reg is not None:
            reg.unregister_source(source_id)

    def get_sources(self):
        reg = self.get_registry()
        if reg is not None:
            return reg.get_sources()
        return []

    # 工厂方法，供面板直接创建独立 registry 实例
    @staticmethod
    def create_registry(data_store=None):
        return ExtensionRegistry(data_store=data_store)
