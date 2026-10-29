"""
MCP Server 管理路由

提供 MCP Server 配置的 CRUD 和状态查询。
当前使用内存存储，TODO: 替换为数据库持久化。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging

from automation_hub.api.auth.deps import require_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])

# ==================== 内部状态 ====================

# TODO: 替换为数据库持久化
_SERVER_STORE: Dict[str, Dict[str, Any]] = {}


def _get_server_or_raise(name: str) -> Dict[str, Any]:
    server = _SERVER_STORE.get(name)
    if server is None:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {name}")
    return server


# ==================== 请求/响应模型 ====================


class MCPServerConfig(BaseModel):
    """MCP Server 配置"""
    name: str = Field(description="服务器唯一标识", min_length=1, max_length=128)
    command: str = Field(description="启动命令", min_length=1, max_length=1024)
    enabled: bool = Field(default=True, description="是否启用")
    timeout_seconds: int = Field(default=60, ge=5, le=3600, description="超时秒数")


class MCPServerResponse(BaseModel):
    """MCP Server 响应"""
    name: str
    command: str
    enabled: bool
    timeout_seconds: int
    running: bool = Field(default=False, description="当前运行状态")


class MCPServerToggleRequest(BaseModel):
    """启用/禁用请求"""
    enabled: bool = Field(description="true=启用, false=禁用")


class MCPOverallStatus(BaseModel):
    """MCP 子系统整体状态"""
    enabled: bool
    total_servers: int
    running_servers: int
    servers: List[MCPServerResponse]


# ==================== API 端点 ====================


@router.get("/servers", response_model=List[MCPServerResponse])
async def list_servers(
    token=Depends(require_scope("mcp:read")),
):
    """
    列出所有已配置的 MCP Server。

    返回所有已注册的服务器配置及其运行状态。
    """
    try:
        result = []
        for name, cfg in _SERVER_STORE.items():
            result.append(MCPServerResponse(
                name=name,
                command=cfg.get("command", ""),
                enabled=cfg.get("enabled", False),
                timeout_seconds=cfg.get("timeout_seconds", 60),
                # TODO: 接入 MCP 客户端运行时后查询真实运行状态
                running=False,
            ))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("列出 MCP servers 时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"列出 servers 失败: {str(e)}",
        )


@router.get("/servers/{name}", response_model=MCPServerResponse)
async def get_server(
    name: str,
    token=Depends(require_scope("mcp:read")),
):
    """
    查询单个 MCP Server 的配置和状态。
    """
    try:
        cfg = _get_server_or_raise(name)
        return MCPServerResponse(
            name=name,
            command=cfg.get("command", ""),
            enabled=cfg.get("enabled", False),
            timeout_seconds=cfg.get("timeout_seconds", 60),
            # TODO: 接入 MCP 客户端运行时后查询真实运行状态
            running=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("查询 MCP server 时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询 server 失败: {str(e)}",
        )


@router.post("/servers", response_model=MCPServerResponse, status_code=status.HTTP_201_CREATED)
async def add_server(
    config: MCPServerConfig,
    token=Depends(require_scope("mcp:admin")),
):
    """
    添加新的 MCP Server 配置。

    name 必须唯一。如果 name 已存在返回 409 冲突。
    """
    try:
        if config.name in _SERVER_STORE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"MCP server already exists: {config.name}",
            )

        record = config.model_dump()
        _SERVER_STORE[config.name] = record

        logger.info("MCP server added: %s (command=%s)", config.name, config.command)

        return MCPServerResponse(
            name=config.name,
            command=config.command,
            enabled=config.enabled,
            timeout_seconds=config.timeout_seconds,
            running=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("添加 MCP server 时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"添加 server 失败: {str(e)}",
        )


@router.delete("/servers/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    name: str,
    token=Depends(require_scope("mcp:admin")),
):
    """
    删除 MCP Server 配置。

    TODO: 如果服务器正在运行，应先停止再删除。
    """
    try:
        _get_server_or_raise(name)
        del _SERVER_STORE[name]
        logger.info("MCP server deleted: %s", name)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除 MCP server 时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除 server 失败: {str(e)}",
        )


@router.post("/servers/{name}/toggle", response_model=MCPServerResponse)
async def toggle_server(
    name: str,
    body: MCPServerToggleRequest,
    token=Depends(require_scope("mcp:admin")),
):
    """
    启用或禁用指定的 MCP Server。

    禁用不会停止正在运行的进程（TODO），只是标记配置为禁用状态。
    """
    try:
        cfg = _get_server_or_raise(name)
        cfg["enabled"] = body.enabled
        _SERVER_STORE[name] = cfg

        state = "enabled" if body.enabled else "disabled"
        logger.info("MCP server %s: %s", name, state)

        return MCPServerResponse(
            name=name,
            command=cfg.get("command", ""),
            enabled=cfg.get("enabled", False),
            timeout_seconds=cfg.get("timeout_seconds", 60),
            # TODO: 接入 MCP 客户端运行时后查询真实运行状态
            running=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("切换 MCP server 状态时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"切换状态失败: {str(e)}",
        )


@router.get("/status", response_model=MCPOverallStatus)
async def mcp_overall_status(
    token=Depends(require_scope("mcp:read")),
):
    """
    MCP 子系统整体状态。

    返回已配置总数、运行中数量和每台服务器的详情。
    """
    try:
        servers = []
        running_count = 0
        for name, cfg in _SERVER_STORE.items():
            # TODO: 接入 MCP 客户端运行时后查询真实运行状态
            is_running = False
            if is_running:
                running_count += 1
            servers.append(MCPServerResponse(
                name=name,
                command=cfg.get("command", ""),
                enabled=cfg.get("enabled", False),
                timeout_seconds=cfg.get("timeout_seconds", 60),
                running=is_running,
            ))

        return MCPOverallStatus(
            enabled=True,
            total_servers=len(servers),
            running_servers=running_count,
            servers=servers,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取 MCP 整体状态时出错")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取状态失败: {str(e)}",
        )
