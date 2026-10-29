"""统一超时配置。

集中管理所有异步操作的超时时间，便于调整和维护。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeoutConfig:
    """超时配置。"""
    
    # ── AI 相关 ──
    ai_chat_stream: float = 25.0        # AI 对话流式响应
    ai_chat_completion: float = 45.0    # AI 对话完整响应（含工具调用）
    ai_tool_execution: float = 60.0     # 工具执行总超时
    ai_embedding: float = 10.0          # 嵌入向量生成
    ai_summary: float = 15.0            # 文本摘要
    ai_preference_extract: float = 10.0 # 偏好提取
    
    # ── 记忆系统 ──
    memory_recall: float = 5.0          # 记忆召回
    memory_search: float = 5.0          # 记忆搜索
    memory_save: float = 3.0            # 记忆保存
    
    # ── 网络请求 ──
    web_search: float = 15.0            # 网络搜索
    web_fetch: float = 20.0             # 网页抓取
    web_fetch_chunk: float = 5.0        # 分块抓取单块超时
    
    # ── 数据存储 ──
    storage_read: float = 5.0           # 存储读取
    storage_write: float = 5.0          # 存储写入
    storage_fts: float = 10.0           # 全文搜索
    
    # ── 连接相关 ──
    ws_connect: float = 10.0            # WebSocket 连接
    ws_rpc_call: float = 30.0           # WebSocket RPC 调用
    ws_heartbeat: float = 30.0          # 心跳间隔
    ws_idle_timeout: float = 300.0      # 空闲超时（5分钟）
    
    # ── 后台任务 ──
    task_shutdown: float = 5.0          # 任务关闭等待
    inbox_auto_capture: float = 3.0     # 收件箱自动捕获
    
    # ── 同步相关 ──
    sync_session: float = 30.0          # 同步会话
    sync_file_transfer: float = 60.0    # 文件传输
    
    @classmethod
    def for_development(cls) -> "TimeoutConfig":
        """开发环境配置（更长的超时）。"""
        return cls(
            ai_chat_stream=60.0,
            ai_chat_completion=120.0,
            ai_tool_execution=180.0,
            web_search=30.0,
            web_fetch=60.0,
        )
    
    @classmethod
    def for_production(cls) -> "TimeoutConfig":
        """生产环境配置（标准超时）。"""
        return cls()


# 全局配置实例
_config: TimeoutConfig | None = None


def get_timeout_config() -> TimeoutConfig:
    """获取全局超时配置。"""
    global _config
    if _config is None:
        _config = TimeoutConfig()
    return _config


def set_timeout_config(config: TimeoutConfig) -> None:
    """设置全局超时配置。"""
    global _config
    _config = config


# 便捷访问函数
def ai_stream() -> float:
    """AI 流式响应超时。"""
    return get_timeout_config().ai_chat_stream


def ai_completion() -> float:
    """AI 完整响应超时。"""
    return get_timeout_config().ai_chat_completion


def ai_tools() -> float:
    """AI 工具执行超时。"""
    return get_timeout_config().ai_tool_execution


def web_search() -> float:
    """网络搜索超时。"""
    return get_timeout_config().web_search


def web_fetch() -> float:
    """网页抓取超时。"""
    return get_timeout_config().web_fetch


def memory_recall() -> float:
    """记忆召回超时。"""
    return get_timeout_config().memory_recall


def storage_io() -> float:
    """存储 IO 超时。"""
    return get_timeout_config().storage_read