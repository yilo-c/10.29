# 思忆集稳定性改进实施指南

**创建日期**: 2026年6月  
**目标**: 提供具体可执行的稳定性改进方案

---

## 一、现状分析

### 1.1 已有基础设施

| 模块 | 文件 | 已有能力 | 缺失能力 |
|------|------|----------|----------|
| 错误处理 | `core/errors.py` | 完整错误码、SiyiError 基类、aiohttp middleware | WebSocket 消息级错误捕获 |
| 连接管理 | `backend/connection_manager.py` | 连接池、心跳检测、空闲超时、优雅关闭 | 无 |
| AI 客户端 | `ai/client/fallback.py` | 多级回退、缓存 TTL/大小限制、隐私过滤 | 熔断机制 |
| 缓存 | `ai/client/fallback.py` | TTL 过期、LRU 裁剪、原子写入 | 无 |

### 1.2 风险评估

| 风险 | 当前状态 | 影响程度 | 优先级 |
|------|----------|----------|--------|
| WebSocket 消息处理异常 | 未捕获会导致连接断开 | 🔴 高 | P0 |
| AI 服务持续失败无熔断 | 每次请求都等待超时 | 🟡 中 | P1 |
| 前端组件错误白屏 | 无 ErrorBoundary | 🔴 高 | P0 |
| 缓存无限增长 | 已有 TTL+LRU，风险低 | 🟢 低 | P2 |

---

## 二、P0 稳定性修复（建议 1 周内完成）

### 2.1 WebSocket 消息级错误捕获

**问题**: `ws_server.py` 中处理 JSON-RPC 消息时，未捕获的异常会导致连接断开

**现状代码位置**: `思忆集app/backend/ws_server.py`

**具体实现**:

```python
# 在 ws_server.py 的 handle_connection 方法中添加全局异常捕获

async def handle_connection(self, request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    conn_id = await self.connection_manager.add_connection(ws, request.remote)
    logger.info("WebSocket connected: id=%s", conn_id)
    
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                # ★ 关键：整个消息处理包裹在 try-except 中
                try:
                    response = await self.handle_message(msg.data, ws)
                    if response:
                        await ws.send_str(response)
                except SiyiError as e:
                    # 业务错误：返回结构化错误
                    logger.warning("SiyiError in message handling: %s", e)
                    error_response = make_jsonrpc_error(
                        request_id=self._extract_id(msg.data),
                        code=e.code,
                        message=e.message,
                        data=e.details,
                    )
                    await ws.send_str(json.dumps(error_response))
                except Exception as e:
                    # 未预期错误：记录日志并返回通用错误
                    logger.exception("Unexpected error in message handling: %s", e)
                    error_response = make_jsonrpc_error(
                        request_id=self._extract_id(msg.data),
                        code=ErrorCode.INTERNAL_ERROR,
                        message="服务器内部错误",
                        data={"detail": str(e)} if self.debug else None,
                    )
                    await ws.send_str(json.dumps(error_response))
                    
            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("WebSocket error: %s", ws.exception())
                
    except asyncio.CancelledError:
        logger.info("Connection cancelled: id=%s", conn_id)
    except Exception as e:
        logger.exception("Connection handler error: %s", e)
    finally:
        await self.connection_manager.remove_connection(conn_id)
        logger.info("WebSocket disconnected: id=%s", conn_id)
    
    return ws

def _extract_id(self, raw: str) -> Any:
    """从 JSON-RPC 请求中提取 id 字段"""
    try:
        data = json.loads(raw)
        return data.get('id')
    except:
        return None
```

**验证方法**:
```python
# 测试用例：发送会导致异常的请求
import websocket
import json

ws = websocket.create_connection("ws://127.0.0.1:18765/ws")

# 发送畸形请求
ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": None}))
response = json.loads(ws.recv())
assert response["error"]["code"] == -32600  # Invalid Request

# 发送不存在的方法
ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "nonexistent.method"}))
response = json.loads(ws.recv())
assert response["error"]["code"] == -32601  # Method Not Found

# 连接应该仍然存活
ws.send(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}))
response = json.loads(ws.recv())
assert response["result"] == "pong"

ws.close()
```

---

### 2.2 前端 ErrorBoundary 组件

**问题**: React 组件错误会导致整个应用白屏

**实现位置**: `desk-notes/packages/desktop/src/components/ErrorBoundary.tsx`

**具体实现**:

```tsx
import React, { Component, ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode | ((error: Error, reset: () => void) => ReactNode)
  onError?: (error: Error, errorInfo: ErrorInfo) => void
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // 记录错误
    console.error('[ErrorBoundary] Caught error:', error, errorInfo)
    
    // 调用外部回调
    this.props.onError?.(error, errorInfo)
    
    // 上报到后端（可选）
    this.reportError(error, errorInfo)
  }

  private reportError(error: Error, errorInfo: ErrorInfo): void {
    try {
      // 通过 WebSocket 上报错误
      const report = {
        type: 'error_report',
        error: {
          message: error.message,
          stack: error.stack,
          componentStack: errorInfo.componentStack,
        },
        timestamp: Date.now(),
        url: window.location.href,
      }
      
      // 假设已有 wsClient
      if (typeof window !== 'undefined' && (window as any).wsClient) {
        (window as any).wsClient.send(JSON.stringify(report))
      }
    } catch (e) {
      console.error('[ErrorBoundary] Failed to report error:', e)
    }
  }

  private handleReset = (): void => {
    this.setState({ hasError: false, error: null })
  }

  render(): ReactNode {
    if (this.state.hasError) {
      // 自定义 fallback
      if (this.props.fallback) {
        if (typeof this.props.fallback === 'function') {
          return this.props.fallback(this.state.error!, this.handleReset)
        }
        return this.props.fallback
      }
      
      // 默认 fallback UI
      return (
        <div className="error-boundary-fallback">
          <div className="error-icon">⚠️</div>
          <h2>出现了一些问题</h2>
          <p className="error-message">
            {this.state.error?.message || '未知错误'}
          </p>
          <div className="error-actions">
            <button onClick={this.handleReset} className="retry-button">
              重试
            </button>
            <button 
              onClick={() => window.location.reload()} 
              className="reload-button"
            >
              刷新页面
            </button>
          </div>
          {import.meta.env.DEV && this.state.error?.stack && (
            <details className="error-details">
              <summary>错误详情（开发模式）</summary>
              <pre>{this.state.error.stack}</pre>
            </details>
          )}
        </div>
      )
    }

    return this.props.children
  }
}

// 带样式的版本（使用 TailwindCSS）
export function StyledErrorBoundary(props: Omit<Props, 'fallback'>) {
  return (
    <ErrorBoundary
      {...props}
      fallback={(error, reset) => (
        <div className="flex flex-col items-center justify-center min-h-[200px] p-8 bg-red-50 rounded-lg">
          <div className="text-4xl mb-4">⚠️</div>
          <h2 className="text-xl font-semibold text-red-800 mb-2">
            组件渲染出错
          </h2>
          <p className="text-red-600 mb-4 text-center">
            {error?.message || '未知错误'}
          </p>
          <div className="flex gap-2">
            <button
              onClick={reset}
              className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 transition"
            >
              重试
            </button>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-gray-200 text-gray-800 rounded hover:bg-gray-300 transition"
            >
              刷新页面
            </button>
          </div>
        </div>
      )}
    />
  )
}
```

**在 App.tsx 中使用**:

```tsx
// desk-notes/packages/desktop/src/App.tsx
import { ErrorBoundary, StyledErrorBoundary } from './components/ErrorBoundary'

function App() {
  return (
    // 最外层：捕获整个应用的错误
    <ErrorBoundary
      onError={(error, errorInfo) => {
        // 全局错误日志
        console.error('[App] Global error:', error)
      }}
      fallback={(error, reset) => (
        <div className="min-h-screen flex items-center justify-center bg-gray-100">
          <div className="bg-white p-8 rounded-lg shadow-lg max-w-md">
            <h1 className="text-2xl font-bold text-red-600 mb-4">应用出错</h1>
            <p className="text-gray-600 mb-4">{error?.message}</p>
            <button
              onClick={reset}
              className="px-4 py-2 bg-blue-600 text-white rounded"
            >
              重试
            </button>
          </div>
        </div>
      )}
    >
      {/* 关键区域：单独包裹，避免一处错误影响全局 */}
      <StyledErrorBoundary>
        <ChatPanel />
      </StyledErrorBoundary>
      
      <StyledErrorBoundary>
        <NotesList />
      </StyledErrorBoundary>
      
      <StyledErrorBoundary>
        <TaskList />
      </StyledErrorBoundary>
    </ErrorBoundary>
  )
}
```

**验证方法**:
```tsx
// 测试组件：故意抛出错误
function ThrowError() {
  throw new Error('测试错误')
}

// 在开发环境中测试
<ErrorBoundary>
  <ThrowError />
</ErrorBoundary>
// 应该显示 fallback UI 而不是白屏
```

---

## 三、P1 功能增强（建议 2-4 周内完成）

### 3.1 AI 服务熔断机制

**问题**: AI 服务持续失败时，每次请求都等待超时，用户体验差

**实现位置**: `思忆集app/ai/client/circuit_breaker.py`（新建）

**具体实现**:

```python
"""AI 服务熔断器。

防止级联故障：当 AI 服务持续失败时，快速失败而不是持续等待。

状态转换：
CLOSED --[失败次数达阈值]--> OPEN
OPEN --[恢复超时]--> HALF_OPEN
HALF_OPEN --[成功]--> CLOSED
HALF_OPEN --[失败]--> OPEN
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

logger = logging.getLogger("siyi.ai.circuit_breaker")

T = TypeVar("T")


class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = "closed"        # 正常：请求正常通过
    OPEN = "open"            # 熔断：快速失败
    HALF_OPEN = "half_open"  # 半开：允许一个请求试探


@dataclass
class CircuitBreakerConfig:
    """熔断器配置"""
    failure_threshold: int = 5          # 连续失败次数阈值
    recovery_timeout: float = 60.0      # 熔断后恢复超时（秒）
    half_open_max_calls: int = 1        # 半开状态最大试探请求数
    success_threshold: int = 2          # 半开状态成功次数阈值（达到后关闭）


@dataclass
class CircuitBreakerStats:
    """熔断器统计"""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.time)
    total_calls: int = 0
    total_failures: int = 0
    total_successes: int = 0


class CircuitBreakerOpenError(Exception):
    """熔断器已打开，请求被拒绝"""
    
    def __init__(self, message: str = "服务暂时不可用，请稍后重试", retry_after: float = 0):
        self.retry_after = retry_after
        super().__init__(message)


class CircuitBreaker:
    """熔断器实现"""
    
    def __init__(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
        on_state_change: Callable[[CircuitState, CircuitState], None] | None = None,
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._stats = CircuitBreakerStats()
        self._lock = asyncio.Lock()
        self._on_state_change = on_state_change
    
    @property
    def state(self) -> CircuitState:
        return self._state
    
    @property
    def stats(self) -> CircuitBreakerStats:
        return self._stats
    
    def _transition_to(self, new_state: CircuitState) -> None:
        """状态转换"""
        if self._state == new_state:
            return
        
        old_state = self._state
        self._state = new_state
        self._stats.last_state_change = time.time()
        
        logger.info(
            "Circuit breaker '%s' state change: %s -> %s",
            self.name, old_state.value, new_state.value
        )
        
        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state)
            except Exception as e:
                logger.warning("State change callback failed: %s", e)
    
    async def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """通过熔断器调用函数
        
        Args:
            func: 要调用的函数（可以是协程函数）
            *args, **kwargs: 函数参数
            
        Returns:
            函数返回值
            
        Raises:
            CircuitBreakerOpenError: 熔断器打开时
            Exception: 函数执行失败时
        """
        async with self._lock:
            self._stats.total_calls += 1
            
            # 检查状态
            if self._state == CircuitState.OPEN:
                # 检查是否可以进入半开状态
                if time.time() - self._stats.last_failure_time >= self.config.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
                    self._stats.success_count = 0
                    logger.info("Circuit breaker '%s' entering half-open state", self.name)
                else:
                    # 仍然熔断，快速失败
                    retry_after = self.config.recovery_timeout - (time.time() - self._stats.last_failure_time)
                    self._stats.total_failures += 1
                    raise CircuitBreakerOpenError(
                        f"服务 '{self.name}' 暂时不可用",
                        retry_after=max(0, retry_after)
                    )
            
            elif self._state == CircuitState.HALF_OPEN:
                # 半开状态：限制试探请求数
                if self._stats.success_count >= self.config.half_open_max_calls:
                    retry_after = self.config.recovery_timeout / 2
                    self._stats.total_failures += 1
                    raise CircuitBreakerOpenError(
                        f"服务 '{self.name}' 试探中，请稍后",
                        retry_after=retry_after
                    )
        
        # 执行调用（在锁外执行，避免阻塞）
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            # 成功
            await self._on_success()
            return result
            
        except Exception as e:
            # 失败
            await self._on_failure()
            raise
    
    async def _on_success(self) -> None:
        """调用成功处理"""
        async with self._lock:
            self._stats.total_successes += 1
            self._stats.success_count += 1
            
            if self._state == CircuitState.HALF_OPEN:
                if self._stats.success_count >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
                    self._stats.failure_count = 0
                    self._stats.success_count = 0
            elif self._state == CircuitState.CLOSED:
                # 成功时重置失败计数
                self._stats.failure_count = 0
    
    async def _on_failure(self) -> None:
        """调用失败处理"""
        async with self._lock:
            self._stats.total_failures += 1
            self._stats.failure_count += 1
            self._stats.last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                # 半开状态失败，重新熔断
                self._transition_to(CircuitState.OPEN)
                self._stats.success_count = 0
                
            elif self._state == CircuitState.CLOSED:
                # 连续失败达阈值，熔断
                if self._stats.failure_count >= self.config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
    
    async def reset(self) -> None:
        """手动重置熔断器"""
        async with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._stats.failure_count = 0
            self._stats.success_count = 0
    
    def get_status(self) -> dict[str, Any]:
        """获取熔断器状态"""
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._stats.failure_count,
            "success_count": self._stats.success_count,
            "last_failure_time": self._stats.last_failure_time,
            "total_calls": self._stats.total_calls,
            "total_failures": self._stats.total_failures,
            "total_successes": self._stats.total_successes,
        }


class CircuitBreakerRegistry:
    """熔断器注册表
    
    管理多个服务的熔断器实例
    """
    
    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
    
    async def get_or_create(
        self,
        name: str,
        config: CircuitBreakerConfig | None = None,
    ) -> CircuitBreaker:
        """获取或创建熔断器"""
        async with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
            return self._breakers[name]
    
    def get(self, name: str) -> CircuitBreaker | None:
        """获取熔断器"""
        return self._breakers.get(name)
    
    async def get_all_status(self) -> dict[str, dict[str, Any]]:
        """获取所有熔断器状态"""
        return {
            name: breaker.get_status()
            for name, breaker in self._breakers.items()
        }
    
    async def reset_all(self) -> None:
        """重置所有熔断器"""
        for breaker in self._breakers.values():
            await breaker.reset()


# 全局注册表
registry = CircuitBreakerRegistry()


# ── 与 FallbackOpenAIClient 集成 ──


async def call_with_circuit_breaker(
    provider_name: str,
    func: Callable[..., T],
    *args,
    config: CircuitBreakerConfig | None = None,
    **kwargs,
) -> T:
    """通过熔断器调用 AI 服务
    
    Args:
        provider_name: 服务提供方名称（如 "primary", "fallback"）
        func: 要调用的函数
        *args, **kwargs: 函数参数
        config: 熔断器配置（可选）
        
    Returns:
        函数返回值
        
    Raises:
        CircuitBreakerOpenError: 熔断器打开时
    """
    breaker = await registry.get_or_create(provider_name, config)
    return await breaker.call(func, *args, **kwargs)
```

**在 FallbackOpenAIClient 中集成**:

```python
# 在 fallback.py 中修改 chat_completion 方法

from ai.client.circuit_breaker import (
    call_with_circuit_breaker,
    CircuitBreakerOpenError,
    CircuitBreakerConfig,
)

# 为每个 provider 配置熔断器
CIRCUIT_BREAKER_CONFIGS = {
    "primary": CircuitBreakerConfig(
        failure_threshold=3,      # 连续 3 次失败后熔断
        recovery_timeout=30.0,    # 30 秒后尝试恢复
    ),
    "fallback": CircuitBreakerConfig(
        failure_threshold=5,
        recovery_timeout=60.0,
    ),
    "local": CircuitBreakerConfig(
        failure_threshold=10,     # 本地服务更宽容
        recovery_timeout=120.0,
    ),
}

async def chat_completion_async(
    self,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int | None = 900,
    **kwargs,
) -> dict[str, Any]:
    """异步版本的 chat_completion，支持熔断器"""
    
    for stage_name, client, provider_name in (...):
        if client is None:
            continue
        
        try:
            # 通过熔断器调用
            config = CIRCUIT_BREAKER_CONFIGS.get(stage_name)
            resp = await call_with_circuit_breaker(
                stage_name,
                client.chat_completion_async,  # 需要客户端支持异步
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                config=config,
                **kwargs,
            )
            return resp
            
        except CircuitBreakerOpenError as e:
            logger.warning(
                "Circuit breaker open for %s, skipping: %s",
                stage_name, e
            )
            # 继续尝试下一个 provider
            
        except AIServiceError as e:
            # 其他 AI 错误，继续回退
            logger.warning("%s failed, trying next: %s", stage_name, e)
            continue
    
    # 所有 provider 都失败
    raise AIServiceError("AI 服务不可用")
```

**验证方法**:

```python
# 测试熔断器
import asyncio
from ai.client.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

async def test_circuit_breaker():
    breaker = CircuitBreaker(
        "test",
        CircuitBreakerConfig(failure_threshold=3, recovery_timeout=1.0)
    )
    
    # 模拟失败函数
    call_count = 0
    async def failing_func():
        nonlocal call_count
        call_count += 1
        raise Exception("模拟失败")
    
    # 连续失败 3 次，触发熔断
    for i in range(3):
        try:
            await breaker.call(failing_func)
        except Exception:
            pass
    
    assert breaker.state.value == "open"
    
    # 第 4 次应该快速失败
    from ai.client.circuit_breaker import CircuitBreakerOpenError
    try:
        await breaker.call(failing_func)
    except CircuitBreakerOpenError:
        print("熔断器正常工作！")
    
    # 等待恢复
    await asyncio.sleep(1.1)
    
    # 现在应该进入半开状态
    try:
        await breaker.call(failing_func)
    except Exception:
        pass
    
    assert breaker.state.value == "half_open"

asyncio.run(test_circuit_breaker())
```

---

### 3.2 智能上下文压缩

**问题**: 简单截断消息列表可能丢失关键上下文

**实现位置**: `思忆集app/assistant/context_compressor.py`（扩展现有）

**具体实现**:

```python
"""智能上下文压缩器。

在保留关键信息的前提下，压缩对话历史以适应 token 限制。

策略：
1. 始终保留系统消息
2. 始终保留最近 N 条消息
3. 对旧消息进行摘要
4. 优先保留包含工具调用结果的消息
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("siyi.assistant.context_compressor")


@dataclass
class CompressionResult:
    """压缩结果"""
    messages: list[dict[str, Any]]
    original_count: int
    compressed_count: int
    tokens_saved: int
    summary_used: bool


class SmartContextCompressor:
    """智能上下文压缩器"""
    
    def __init__(
        self,
        llm_client: Any,  # FallbackOpenAIClient 或其他
        max_tokens: int = 4000,
        preserve_recent: int = 10,
        summary_model: str | None = None,
    ):
        self.llm_client = llm_client
        self.max_tokens = max_tokens
        self.preserve_recent = preserve_recent
        self.summary_model = summary_model
    
    def compress(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int | None = None,
    ) -> CompressionResult:
        """压缩消息列表
        
        Args:
            messages: 原始消息列表
            target_tokens: 目标 token 数（默认使用 max_tokens）
            
        Returns:
            CompressionResult: 压缩结果
        """
        target = target_tokens or self.max_tokens
        original_count = len(messages)
        
        # 估算当前 token 数
        current_tokens = self._estimate_tokens(messages)
        
        if current_tokens <= target:
            # 不需要压缩
            return CompressionResult(
                messages=messages,
                original_count=original_count,
                compressed_count=original_count,
                tokens_saved=0,
                summary_used=False,
            )
        
        # 1. 分类消息
        system_msgs = []
        tool_msgs = []
        user_assistant_msgs = []
        
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                system_msgs.append(msg)
            elif role == "tool":
                tool_msgs.append(msg)
            else:
                user_assistant_msgs.append(msg)
        
        # 2. 始终保留最近 N 条用户/助手消息
        recent_count = min(self.preserve_recent, len(user_assistant_msgs))
        recent_msgs = user_assistant_msgs[-recent_count:]
        old_msgs = user_assistant_msgs[:-recent_count]
        
        # 3. 对旧消息进行摘要
        summary_used = False
        if old_msgs:
            summary = self._summarize_messages(old_msgs)
            if summary:
                summary_msg = {
                    "role": "system",
                    "content": f"[之前对话的摘要]\n{summary}",
                }
                system_msgs.append(summary_msg)
                summary_used = True
        
        # 4. 保留最近的工具调用结果
        recent_tool_msgs = tool_msgs[-5:]  # 保留最近 5 条工具结果
        
        # 5. 组合结果
        compressed = system_msgs + recent_tool_msgs + recent_msgs
        
        # 6. 如果还是太长，进一步截断
        while self._estimate_tokens(compressed) > target and len(compressed) > 2:
            # 移除最早的非系统消息
            for i, msg in enumerate(compressed):
                if msg.get("role") != "system":
                    compressed.pop(i)
                    break
            else:
                break
        
        compressed_tokens = self._estimate_tokens(compressed)
        tokens_saved = current_tokens - compressed_tokens
        
        logger.info(
            "Context compressed: %d -> %d messages, saved ~%d tokens",
            original_count, len(compressed), tokens_saved
        )
        
        return CompressionResult(
            messages=compressed,
            original_count=original_count,
            compressed_count=len(compressed),
            tokens_saved=tokens_saved,
            summary_used=summary_used,
        )
    
    def _estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """估算消息列表的 token 数
        
        简单估算：中文约 1.5 字/token，英文约 4 字符/token
        """
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total_chars += len(part.get("text", ""))
        
        # 粗略估算：平均每 token 约 2 个字符（中英文混合）
        return total_chars // 2
    
    def _summarize_messages(self, messages: list[dict[str, Any]]) -> str | None:
        """使用 LLM 生成消息摘要"""
        if not messages:
            return None
        
        # 构建摘要请求
        content_parts = []
        for msg in messages[-20:]:  # 最多取最近 20 条
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                content_parts.append(f"[{role}]: {content[:200]}")
        
        if not content_parts:
            return None
        
        conversation_text = "\n".join(content_parts)
        
        prompt = f"""请用 200 字以内总结以下对话的关键信息，包括：
1. 用户的主要意图
2. 讨论的关键主题
3. 达成的结论或决定

对话内容：
{conversation_text}

总结："""
        
        try:
            # 使用 LLM 生成摘要
            response = self.llm_client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300,
            )
            summary = response["choices"][0]["message"]["content"].strip()
            return summary
        except Exception as e:
            logger.warning("Failed to generate summary: %s", e)
            # 回退：简单拼接
            return f"之前进行了 {len(messages)} 轮对话，讨论了相关话题。"


# ── 与现有代码集成 ──


def compress_context_for_chat(
    messages: list[dict[str, Any]],
    llm_client: Any,
    max_tokens: int = 4000,
) -> list[dict[str, Any]]:
    """便捷函数：压缩对话上下文
    
    可直接替换原有的消息截断逻辑。
    """
    compressor = SmartContextCompressor(
        llm_client=llm_client,
        max_tokens=max_tokens,
    )
    result = compressor.compress(messages)
    return result.messages
```

---

## 四、P2 功能增强（建议 4-8 周内完成）

### 4.1 数据自动备份

**实现位置**: `思忆集app/services/backup_service.py`（扩展现有）

**具体实现**:

```python
"""数据自动备份服务。

功能：
- 定时自动备份
- 手动备份
- 备份恢复
- 旧备份清理
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.services.backup")


class BackupService:
    """数据备份服务"""
    
    def __init__(
        self,
        data_dir: str | Path,
        backup_dir: str | Path | None = None,
        max_backups: int = 10,
    ):
        self.data_dir = Path(data_dir)
        self.backup_dir = Path(backup_dir) if backup_dir else self.data_dir.parent / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.max_backups = max_backups
        self._backup_task: asyncio.Task | None = None
    
    def create_backup(self, name: str | None = None) -> Path:
        """创建数据备份"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = name or f"backup_{timestamp}"
        backup_path = self.backup_dir / backup_name
        
        logger.info("Creating backup: %s", backup_path)
        
        # 复制数据目录
        shutil.copytree(
            self.data_dir,
            backup_path,
            ignore=shutil.ignore_patterns("*.lock", "*.tmp", "__pycache__"),
        )
        
        # 创建元数据
        metadata = {
            "name": backup_name,
            "created_at": datetime.now().isoformat(),
            "data_dir": str(self.data_dir),
            "size_bytes": self._get_dir_size(backup_path),
        }
        
        import json
        with open(backup_path / ".backup_meta.json", "w") as f:
            json.dump(metadata, f, indent=2)
        
        # 清理旧备份
        self._cleanup_old_backups()
        
        logger.info("Backup created: %s (%.2f MB)", backup_name, metadata["size_bytes"] / 1024 / 1024)
        return backup_path
    
    def restore_backup(self, backup_path: str | Path, create_safety_backup: bool = True) -> None:
        """从备份恢复"""
        backup_path = Path(backup_path)
        
        if not backup_path.exists():
            raise FileNotFoundError(f"备份不存在: {backup_path}")
        
        logger.warning("Restoring from backup: %s", backup_path)
        
        # 先创建当前数据的备份（安全措施）
        if create_safety_backup:
            safety_name = f"before_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self.create_backup(safety_name)
        
        # 删除当前数据
        shutil.rmtree(self.data_dir)
        
        # 复制备份数据
        shutil.copytree(backup_path, self.data_dir)
        
        logger.info("Backup restored successfully")
    
    def list_backups(self) -> list[dict[str, Any]]:
        """列出所有备份"""
        backups = []
        
        for path in self.backup_dir.iterdir():
            if not path.is_dir():
                continue
            
            meta_file = path / ".backup_meta.json"
            if meta_file.exists():
                import json
                with open(meta_file) as f:
                    metadata = json.load(f)
                backups.append(metadata)
            else:
                # 没有元数据，使用基本信息
                backups.append({
                    "name": path.name,
                    "created_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                    "size_bytes": self._get_dir_size(path),
                })
        
        # 按创建时间倒序
        backups.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return backups
    
    def delete_backup(self, name: str) -> None:
        """删除指定备份"""
        backup_path = self.backup_dir / name
        if backup_path.exists():
            shutil.rmtree(backup_path)
            logger.info("Backup deleted: %s", name)
    
    def _cleanup_old_backups(self) -> None:
        """清理旧备份，保留最近 N 个"""
        backups = self.list_backups()
        
        if len(backups) <= self.max_backups:
            return
        
        # 删除最旧的备份
        to_delete = backups[self.max_backups:]
        for backup in to_delete:
            self.delete_backup(backup["name"])
    
    def _get_dir_size(self, path: Path) -> int:
        """获取目录大小（字节）"""
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        return total
    
    # ── 定时备份 ──
    
    def start_auto_backup(self, interval_hours: float = 24.0) -> None:
        """启动定时自动备份"""
        if self._backup_task is not None:
            logger.warning("Auto backup already running")
            return
        
        self._backup_task = asyncio.create_task(self._auto_backup_loop(interval_hours))
        logger.info("Auto backup started: every %.1f hours", interval_hours)
    
    def stop_auto_backup(self) -> None:
        """停止定时自动备份"""
        if self._backup_task is not None:
            self._backup_task.cancel()
            self._backup_task = None
            logger.info("Auto backup stopped")
    
    async def _auto_backup_loop(self, interval_hours: float) -> None:
        """自动备份循环"""
        while True:
            try:
                await asyncio.sleep(interval_hours * 3600)
                
                # 创建备份
                name = f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                self.create_backup(name)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Auto backup failed: %s", e)
```

---

## 五、实施检查清单

### Phase 1: P0 稳定性修复（第 1 周）

- [ ] **WebSocket 错误捕获**
  - [ ] 修改 `ws_server.py` 添加 try-except
  - [ ] 添加 `_extract_id` 辅助方法
  - [ ] 编写测试用例验证
  
- [ ] **前端 ErrorBoundary**
  - [ ] 创建 `ErrorBoundary.tsx` 组件
  - [ ] 在 `App.tsx` 中集成
  - [ ] 测试错误捕获和恢复

### Phase 2: P1 功能增强（第 2-4 周）

- [ ] **AI 熔断机制**
  - [ ] 创建 `circuit_breaker.py`
  - [ ] 实现 `CircuitBreaker` 类
  - [ ] 集成到 `FallbackOpenAIClient`
  - [ ] 编写单元测试
  
- [ ] **智能上下文压缩**
  - [ ] 扩展 `context_compressor.py`
  - [ ] 实现 `SmartContextCompressor`
  - [ ] 集成到对话流程

### Phase 3: P2 功能增强（第 5-8 周）

- [ ] **数据自动备份**
  - [ ] 扩展 `backup_service.py`
  - [ ] 实现定时备份
  - [ ] 添加备份恢复功能

---

## 六、验证标准

| 改进项 | 验证方法 | 通过标准 |
|--------|----------|----------|
| WebSocket 错误捕获 | 发送畸形请求 | 连接不断开，返回错误响应 |
| ErrorBoundary | 故意抛出错误 | 显示 fallback UI，不白屏 |
| AI 熔断器 | 模拟连续失败 | 达到阈值后快速失败 |
| 上下文压缩 | 长对话后检查 | token 数在限制内，关键信息保留 |
| 数据备份 | 删除文件后恢复 | 数据完整恢复 |

---

*文档版本: 1.0*  
*最后更新: 2026年6月*