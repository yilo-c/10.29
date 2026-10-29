# 插件 SDK 规范

> 本文档定义思忆集的插件体系：Manifest 格式、插件类型、沙箱模型和生命周期。
> 路线图明确：先做受控的内部插件制，不做开放市场。本文档为内部插件开发提供唯一合同。

## 参考对标

| 项目 | 借鉴点 |
|------|--------|
| **openclaw** | extensions/* 目录结构、channels 入口抽象 |
| **MCP (Model Context Protocol)** | `tools/list` / `tools/call` 协议、JSON Schema 参数定义 |
| **nanobot** | provider 适配接口、事件总线（EventBus） |
| **Dify** | 插件 Manifest、工具注册、权限声明 |
| **Obsidian** | 社区插件 Manifest、沙箱模型（iframe 隔离） |

---

## 1. 插件类型体系

| 类型 | 接口 | 说明 | 示例 |
|------|------|------|------|
| **工具插件** (ToolPlugin) | `tool_id`, `parameters_schema`, `execute(params) → result` | 注册为可被 AI 调用的工具 | 翻译、天气、计算器 |
| **数据源插件** (DataSourcePlugin) | `source_id`, `fetch(query) → items`, `sync(cursor) → delta` | 提供外部数据接入 | RSS 源、GitHub 通知 |
| **UI 卡片插件** (CardPlugin) | `card_id`, `render(state) → widget`, `preferred_size` | 工作台可布局卡片 | 天气卡片、股市卡片 |
| **动作插件** (ActionPlugin) | `action_id`, `condition(context) → bool`, `perform(context) → result` | 条件触发执行 | 自动备份、定时整理 |
| **悬浮助手插件** (CompanionPlugin) | `companion_id`, `on_hover()`, `on_click()`, `status_text` | 悬浮助手扩展入口 | 快速翻译、剪贴板监听 |

---

## 2. Plugin Manifest

```json
{
  "manifest_version": "1.0",
  "id": "com.example.weather-card",
  "name": "天气卡片",
  "version": "1.0.0",
  "description": "在工作台显示实时天气信息",
  "author": "思忆集团队",
  "license": "MIT",
  "type": "card",
  "entry_point": "weather_card/plugin.py:WeatherCardPlugin",
  "permissions": [
    "network:api.openweathermap.org",
    "storage:local:weather_cache"
  ],
  "capabilities": [
    "render_card",
    "background_refresh"
  ],
  "config_schema": {
    "type": "object",
    "properties": {
      "city": { "type": "string", "default": "auto" },
      "refresh_interval_min": { "type": "integer", "default": 30 }
    }
  },
  "min_app_version": "0.2.0",
  "max_app_version": null
}
```

### 字段说明

| 字段 | 必需 | 说明 |
|------|------|------|
| `manifest_version` | ✅ | Manifest 格式版本，当前 1.0 |
| `id` | ✅ | 全局唯一标识，反向域名格式 |
| `type` | ✅ | 插件类型：tool / datasource / card / action / companion |
| `entry_point` | ✅ | `文件路径:类名` |
| `permissions` | ✅ | 所需权限列表 |
| `capabilities` | ✅ | 插件声明的能力 |
| `config_schema` | ✅ | 用户可配置项的 JSON Schema |
| `min_app_version` | — | 最低思忆集版本要求 |

---

## 3. 权限模型

### 3.1 权限类别

| 权限 | 格式 | 说明 |
|------|------|------|
| 网络访问 | `network:{domain}` | 允许访问指定域名 API |
| 本地存储 | `storage:local:{namespace}` | 读写本地文件，限定命名空间 |
| 数据库访问 | `db:read:{table}` / `db:write:{table}` | 读写指定数据表 |
| 系统通知 | `notification:send` | 发送系统通知 |
| 文件系统 | `fs:read:{path}` / `fs:write:{path}` | 读写指定路径 |
| UI 注入 | `ui:card` / `ui:companion` | 在 UI 中创建元素 |

### 3.2 权限审批

- 安装时展示所需权限，用户确认
- `network:*` 和 `fs:*` 需要显式二次确认
- 运行时权限检查由 `action_guard` 统一拦截

---

## 4. 插件生命周期

```
安装 (Install)
  ├─ 验证 Manifest
  ├─ 检查权限 → 用户确认
  ├─ 安装依赖（如有 requirements.txt）
  └─ 注册到 PluginRegistry

启用 (Enable)
  ├─ 调用 plugin.on_enable()
  └─ 开始接收事件

运行 (Active)
  ├─ 响应事件/调用
  └─ 状态持久化

禁用 (Disable)
  ├─ 调用 plugin.on_disable()
  ├─ 清理定时器和连接
  └─ 保留数据

卸载 (Uninstall)
  ├─ 调用 plugin.on_uninstall()
  ├─ 清理数据（用户选择保留/删除）
  └─ 从 PluginRegistry 移除
```

---

## 5. 沙箱模型

### 5.1 阶段 1：进程内隔离（当前）

- 插件作为 Python 模块在独立线程中运行
- 通过 `action_guard` 拦截所有副作用性调用
- 插件不能直接 import 思忆集内部模块（白名单控制）

### 5.2 阶段 2：子进程隔离（Phase 3+）

- 插件运行在独立 Python 子进程中
- 通过 JSON-RPC over stdin/stdout 通信
- 子进程网络/文件系统访问受限

约束：路线图明确「不开放无边界自治」。Phase 2+ 之前，所有插件必须经过代码审查才能安装。

---

## 6. Plugin Registry（插件注册表）

```python
class PluginRegistry:
    """全局插件注册表，管理所有已安装插件"""

    def register(manifest: PluginManifest, instance: BasePlugin) -> None
    def unregister(plugin_id: str) -> None
    def get(plugin_id: str) -> BasePlugin | None
    def list_by_type(type: PluginType) -> list[BasePlugin]
    def list_by_capability(capability: str) -> list[BasePlugin]
    def get_config(plugin_id: str) -> dict
    def set_config(plugin_id: str, config: dict) -> None
    def health_check(plugin_id: str) -> PluginHealth
```

---

## 7. 首批内部插件规划

| 插件 | 类型 | 说明 |
|------|------|------|
| 天气卡片 | CardPlugin | 工作台实时天气 |
| RSS 订阅源 | DataSourcePlugin | 统一管理 RSS 源 |
| 快速翻译 | ToolPlugin | 悬浮助手触发翻译 |
| 剪贴板监听 | CompanionPlugin | 悬浮助手状态提示 |
| 每日备份 | ActionPlugin | 定时自动备份本地数据 |

---

## 8. 与 Coze/Dify 插件生态对比

| 维度 | Coze | Dify | openclaw | 思忆集 |
|------|------|------|---------|--------|
| 插件类型 | Bot 工具插件 | Tool/Model/Extension | extensions/* | 5 种内部类型 |
| 安装方式 | 市场一键安装 | 市场/GitHub | 本地目录 | 本地文件 |
| 沙箱 | 云端沙箱 | 云端沙箱 | 进程内 | 进程内→子进程 |
| 权限模型 | OAuth 授权 | API Key | 无 | Manifest 声明 |
| 开放市场 | ✅ | ✅ | ❌ | 明确不做 |

思忆集的插件体系不追求成为第三方市场，而是为**内部扩展和用户自定义**提供标准化接口。
