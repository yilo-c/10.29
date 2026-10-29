# -*- coding: utf-8 -*-
"""可观测性功能验证测试"""

def test_http_client_import():
    """验证HTTP客户端可以正常导入"""
    from automation_hub.api.http_client import RetryableHttpClient
    assert RetryableHttpClient is not None


def test_exception_handler_import():
    """验证异常处理器可以正常导入"""
    import sys
    import os
    app_dir = os.path.join(os.path.dirname(__file__), '..', '..', '思忆集app')
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    from core.exception_handler import install_global_exception_handler
    assert install_global_exception_handler is not None