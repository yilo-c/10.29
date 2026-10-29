# -*- coding: utf-8 -*-
"""health和meta路由的HTTP测试"""

from fastapi.testclient import TestClient
from automation_hub.api.main import app

client = TestClient(app)


def test_health_endpoint_returns_200():
    """测试健康检查端点返回200"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_health_response_contains_components():
    """测试健康检查响应包含组件状态列表"""
    response = client.get("/health")
    data = response.json()
    assert "components" in data
    assert isinstance(data["components"], list)

    # Should report database and redis
    component_names = [c["name"] for c in data["components"]]
    assert "database" in component_names
    assert "redis" in component_names


def test_health_database_component_reported():
    """测试数据库组件状态被上报"""
    response = client.get("/health")
    data = response.json()
    db_component = next((c for c in data["components"] if c["name"] == "database"), None)
    assert db_component is not None
    assert db_component["status"] in ("ok", "degraded", "down")
    assert "latency_ms" in db_component


def test_health_redis_component_reported():
    """测试Redis组件状态被上报（测试环境可能为down）"""
    response = client.get("/health")
    data = response.json()
    redis_component = next((c for c in data["components"] if c["name"] == "redis"), None)
    assert redis_component is not None
    assert redis_component["status"] in ("ok", "degraded", "down")


def test_meta_endpoint_returns_version():
    """测试元信息端点返回版本信息"""
    response = client.get("/meta")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data


def test_meta_response_contains_service_name():
    """测试元信息响应包含服务名称"""
    response = client.get("/meta")
    data = response.json()
    assert "service_name" in data
    assert data["service_name"] == "automation-hub"
