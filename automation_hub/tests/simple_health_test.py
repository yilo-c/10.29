# -*- coding: utf-8 -*-
"""简单的health和meta路由功能测试"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest

from automation_hub.api.routes.health import health_check
from automation_hub.api.routes.meta import get_meta


@pytest.mark.asyncio
async def test_health_check():
    """测试健康检查功能"""
    result = await health_check()
    print(f"Health check result: {result}")
    assert result.status in ("ok", "down"), f"unexpected status: {result.status}"
    print("✅ Health check passed")


@pytest.mark.asyncio
async def test_get_meta():
    """测试元信息功能"""
    result = await get_meta()
    print(f"Meta result: {result}")
    assert "service_name" in result
    assert result["service_name"] == "automation-hub"
    print("✅ Meta check passed")


if __name__ == "__main__":
    import asyncio
    
    print("Running simple health and meta tests...")
    
    asyncio.run(test_health_check())
    asyncio.run(test_get_meta())
    
    print("All tests passed! 🎉")