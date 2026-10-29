# 基础设施导入白名单

## core.events — EventBus

`core.events` 提供全局事件发布/订阅总线 (`EventBus`)，属于基础设施层，
被 UI、Assistant、Core 等多个层引用。

### 策略

允许在 `ui/` 中通过 `from core.events import AppEvent, event_bus` 直接导入，
但每处导入必须标注 `# infrastructure import` 注释。

### 白名单文件

| 文件 | 导入内容 |
|------|---------|
| `ui/main_window.py` | `AppEvent`, `event_bus` |
| `ui/main_window_proactive.py` | `event_bus` |
| `ui/assistant/message_renderer.py` | `AppEvent`, `event_bus` |

### 理由

EventBus 是全局单例基础设施，包装为服务层会导致不必要的间接层，
且不影响分层架构的核心约束（UI→Data、UI→Core 非基础设施模块的违规导入）。
