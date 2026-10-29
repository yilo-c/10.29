"""资讯抓取器子包。

- ``mixin.InfoFetchersMixin``：26 个内置源 fetcher 与解析辅助，
  被 ``InfoCollectorService`` 继承混入（``getattr(self, FETCHER_METHOD_MAP[..])``
  调用方式不变）。

从 ``services/info_collector_service.py`` 拆出，降低大文件复杂度。
"""
from services.info_fetchers.mixin import InfoFetchersMixin

__all__ = ["InfoFetchersMixin"]
