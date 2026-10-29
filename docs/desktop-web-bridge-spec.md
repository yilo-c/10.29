# Desktop ↔ Web 桥接协议规范

> 更新于 2026-05-16 | 当前实现状态：Phase 1 桥接已运行（WebFloatingBubble），Phase 2-4 待执行。

本文档定义 Python（PyQt6 + QWebChannel）与 Web 前端（React 18 + TypeScript）之间的双向通信协议。所有跨边界调用必须遵循本规范。

---

## 1. 通信通道

| 通道 | 方向 | 传输方式 | 用途 |
|------|------|---------|------|
| QWebChannel | Python → JS | Qt Signal | 服务端主动推送数据变更、通知 |
| QWebChannel | JS → Python | Qt Slot（QObject 方法调用） | 前端触发业务逻辑 |
| 回调 | Python → JS | 返回值 | 前端调用 Python 方法后同步返回结果 |

> **约束**：QWebChannel 是 Python ↔ JS 的唯一通信路径。不另开 HTTP 端点、不读写共享文件作为通信手段。
> **当前实现**：Phase 1 浮动气泡使用 `Bridge(QObject)` 直接通过 `@pyqtSlot` 方法通信（见 `web_floating_bubble.py`），不走 `call_service` 通用路由。Phase 2 主窗口迁移将改用 `AppBridge.call_service` 统一入口模式，详见第 2 节。

---

## 2. Python 侧接口定义

Python 侧通过 `QObject` 子类向 QWebChannel 注册。每个 `QObject` 的方法和信号对 JS 侧可见。

### 2.1 核心 Bridge Object: `AppBridge`

```python
class AppBridge(QObject):
    """注册到 QWebChannel 的主对象，暴露所有 Python Service 接口"""
    
    # ═══ 信号（Python → JS 推送）═══
    
    # 数据变更通知
    data_changed = Signal(str, object)  # key: str, payload: Any
    # 示例: data_changed.emit("inbox:unread_count", {"count": 3})
    
    # 系统通知
    system_notification = Signal(str, str, str)  # title, message, level(info/warning/error)
    
    # 桌宠情绪
    pet_mood_changed = Signal(str, str)  # pet_id, mood (happy/idle/sad/angry)
    
    # 同步状态
    sync_status_changed = Signal(str, str, int)  # status(active/idle/error), message, progress_pct
    
    # ═══ Slot 方法（JS → Python 调用）═══
    
    @Slot(str, object, result=object)
    def call_service(self, service_name: str, method: str, params: dict) -> dict:
        """通用服务调用入口"""
        # service_name: "doctor" | "assistant" | "sync" | "pet" | "settings" | "database"
        # method: 具体方法名
        # params: 参数 dict
        # 返回: {"ok": bool, "data": Any, "error": str | None}
        pass
```

### 2.2 服务注册表

通过 `call_service` 暴露的 Service 列表：

| service_name | 职责 | 典型 method |
|-------------|------|------------|
| `doctor` | 诊断 | `run_check`, `get_report`, `get_debug_assets` |
| `assistant` | 助手交互 | `ask`, `get_suggestions`, `execute_action` |
| `sync` | 数据同步 | `trigger_sync`, `get_status`, `get_history` |
| `pet` | 桌宠控制 | `launch`, `close`, `set_mood`, `get_moods` |
| `settings` | 设置管理 | `get_all`, `update`, `test_api_connection` |
| `database` | 数据访问 | `query`, `insert`, `update`, `delete` |
| `inbox` | 收件箱 | `get_items`, `add_item`, `summarize` |
| `notes` | 笔记 | `get_notes`, `create_note`, `update_note`, `delete_note` |
| `tasks` | 任务 | `get_tasks`, `create_task`, `update_task`, `complete_task` |
| `news` | 新闻 | `get_feeds`, `fetch_new`, `mark_read` |

### 2.3 返回值规范

所有 `call_service` 返回值遵循统一格式：

```json
{
    "ok": true,
    "data": { ... },
    "error": null
}
```

失败时：

```json
{
    "ok": false,
    "data": null,
    "error": "描述性错误信息"
}
```

---

## 3. JS 侧使用方式

### 3.1 初始化

```typescript
// channels/siyi-bridge.ts

interface SiyiBridge {
  // 信号监听
  data_changed: {
    connect(callback: (key: string, payload: any) => void): void;
  };
  system_notification: {
    connect(callback: (title: string, message: string, level: string) => void): void;
  };
  pet_mood_changed: {
    connect(callback: (petId: string, mood: string) => void): void;
  };
  sync_status_changed: {
    connect(callback: (status: string, message: string, progress: number) => void): void;
  };

  // 服务调用
  call_service(service_name: string, method: string, params: object, callback: (result: ServiceResult) => void): void;
}

interface ServiceResult {
  ok: boolean;
  data: any;
  error: string | null;
}

// QWebChannel 自动注入 bridge 对象后使用
const bridge = (window as any).bridge as SiyiBridge;

// 监听信号
bridge.data_changed.connect((key, payload) => {
  console.log(`Data changed: ${key}`, payload);
});

// 调用服务
bridge.call_service("notes", "get_notes", { filter: "recent" }, (result) => {
  if (result.ok) {
    updateNotesList(result.data);
  }
});
```

### 3.2 封装层（推荐）

```typescript
// api/siyi-api.ts
// 将 QWebChannel 的异步回调封装为 Promise + TypeScript 类型

async function callSyiService<T>(
  service: string, 
  method: string, 
  params: Record<string, any> = {}
): Promise<T> {
  return new Promise((resolve, reject) => {
    (window as any).bridge.call_service(service, method, params, (result: ServiceResult) => {
      if (result.ok) {
        resolve(result.data as T);
      } else {
        reject(new Error(result.error || "Unknown error"));
      }
    });
  });
}

// 类型化 API
export const siyiApi = {
  notes: {
    getNotes: (filter?: string) => callSyiService<any[]>("notes", "get_notes", { filter }),
    createNote: (note: CreateNoteInput) => callSyiService<any>("notes", "create_note", note),
    updateNote: (id: string, updates: Partial<UpdateNoteInput>) => 
      callSyiService<any>("notes", "update_note", { id, ...updates }),
    deleteNote: (id: string) => callSyiService<void>("notes", "delete_note", { id }),
  },
  tasks: {
    getTasks: (filter?: string) => callSyiService<any[]>("tasks", "get_tasks", { filter }),
    createTask: (task: CreateTaskInput) => callSyiService<any>("tasks", "create_task", task),
    completeTask: (id: string) => callSyiService<any>("tasks", "complete_task", { id }),
  },
  // ... 其他服务
};
```

---

## 4. 数据变更通知 (data_changed) 信号规范

`data_changed` 信号使用 `key` 标识变更类型，前端根据 key 选择性更新组件。

### 4.1 key 命名空间

| 命名空间 | 触发时机 | payload 示例 |
|---------|---------|-------------|
| `inbox:unread_count` | 收件箱未读数变化 | `{"count": 5}` |
| `inbox:new_item` | 新增收件项 | `{"item": {...}}` |
| `notes:updated` | 笔记增删改 | `{"note_id": "xxx", "action": "created"}` |
| `tasks:updated` | 任务增删改 | `{"task_id": "xxx", "action": "completed"}` |
| `suggestions:updated` | 建议列表变化 | `{"suggestions": [...]}` |
| `sync:progress` | 同步进度变化 | `{"status": "syncing", "progress": 45}` |
| `doctor:report_ready` | 诊断完成 | `{"report": {...}}` |
| `pet:status` | 桌宠状态 | `{"pet_id": "qinai", "running": true}` |
| `news:new_items` | 新新闻 | `{"count": 3}` |
| `settings:updated` | 设置变更 | `{"key": "youdao_app_key", "new_value": "***"}` |

---

## 5. 错误码

| 错误码 | 含义 | 处理建议 |
|-------|------|---------|
| `SERVICE_NOT_FOUND` | 未知 service_name | 前端检查拼写 |
| `METHOD_NOT_FOUND` | 未知 method | 检查 API 文档 |
| `INVALID_PARAMS` | 参数校验失败 | 检查类型匹配 |
| `DATABASE_ERROR` | 数据库操作失败 | 显示错误提示 |
| `API_TIMEOUT` | 后端 API 超时 | 提示用户稍后重试 |
| `AUTH_ERROR` | 认证失败 | 跳转设置页 |
| `PERMISSION_DENIED` | 权限不足 | 提示无权限 |
| `PET_LAUNCH_FAILED` | 桌宠启动失败 | 提示检查文件 |
| `INTERNAL_ERROR` | 服务端内部错误 | 记录日志 + 友好提示 |

---

## 6. 生命周期

### 6.1 启动流程

```
1. PyQt6 MainWindow 启动
2. 创建 QWebEngineView + QWebChannel
3. 注册 AppBridge QObject 到 channel
4. 加载 Web UI (index.html)
5. Web UI 初始化，连接 bridge 信号
6. Web UI 调用 bridge.call_service("settings", "get_all", ...) 加载初始状态
7. QWebChannel 就绪，双向通信开始
```

### 6.2 断开/重连

- **Web 重载** (`QWebEngineView.reload()`)：QWebChannel 自动重建注册
- **应用关闭**：MainWindow 清理所有 QObject，确保无悬挂槽函数
- **异常保护**：所有 `call_service` 调用需 .catch() 处理，JS 侧不因单次调用失败而崩溃

---

## 7. 安全约束

1. **不暴露文件系统路径**：JS 侧不直接操作文件，由 Python Service 层代理
2. **不暴露进程控制**：桌宠启停通过 `pet` service 间接操作，不直接暴露 `subprocess` 调用
3. **凭证保护**：`settings` service 返回的敏感字段（如 `youdao_app_secret`）做脱敏处理，JS 侧只拿到 `"***"` 或哈希指纹
4. **大小限制**：单次 `call_service` 的 payload 不超过 1MB，超出时返回 `PAYLOAD_TOO_LARGE` 错误
5. **来源校验**：QWebChannel 仅绑定到本地 file:// 或内置页面，不加载远程 URL

---

## 8. 渐进迁移路线

### 阶段 1（已完成 — 浮动气泡桥接）
- React FloatingBubbleApp + QWebEngineView 集成 → Bridge 已运行
- 实际实现：`web_floating_bubble.py` 中的 `Bridge(QObject)` + `WebFloatingBubble(QMainWindow)`
- 通信协议：@pyqtSlot 方法 + page.runJavaScript()
- 验证通过：双向通信正常，路径 `file:///...dist/index.html#floating`

### 阶段 2（进行中 — 主窗口功能迁移）
- 新建 `AppBridge` 扩展 Bridge，增加 `call_service` 统一入口信号
- 逐个迁移主窗口功能：仪表盘 → 命令面板 → 内联面板 → 状态栏 → 全局搜索
- Web 前端加载路径：`file:///...dist/index.html#main`

### 阶段 3（计划 — 业务对话框替换）
- 逐个对话框迁移：Notes/Todo → News → Translate → WordBank → URL Learning → MCP → Knowledge Base
- 替换后移除 PyQt6 旧对话框并下线相关工厂代码

### 阶段 4（计划 — 壳层瘦身）
- MainWindow 退化为纯壳层（<200 行）
- 所有业务 UI 由 Web UI 承载
- PyQt6 只保留：窗口管理、系统托盘、全局快捷键、桌宠进程管理

---

## 9. 附录：PyQt 注册示例

```python
# 思忆集app/desktop_web_bridge.py

from PyQt6.QtCore import QObject, Slot, Signal, pyqtSlot

class AppBridge(QObject):
    """注册到 QWebChannel 的桥接对象"""
    
    # 信号定义
    data_changed = Signal(str, object)
    system_notification = Signal(str, str, str)
    pet_mood_changed = Signal(str, str)  
    sync_status_changed = Signal(str, str, int)
    
    def __init__(self, services: dict):
        super().__init__()
        self._services = services  # {"doctor": doctor_service, ...}
    
    @Slot(str, str, object, result=object)
    def call_service(self, service_name: str, method: str, params: dict) -> dict:
        """JS 通用调用入口"""
        service = self._services.get(service_name)
        if service is None:
            return {"ok": False, "data": None, "error": f"SERVICE_NOT_FOUND: {service_name}"}
        
        func = getattr(service, method, None)
        if func is None:
            return {"ok": False, "data": None, "error": f"METHOD_NOT_FOUND: {service_name}.{method}"}
        
        try:
            data = func(**params) if params else func()
            return {"ok": True, "data": data, "error": None}
        except Exception as e:
            return {"ok": False, "data": None, "error": str(e)}


# 在 MainWindow 中注册
def _setup_web_channel(self):
    self.web_channel = QWebChannel()
    self.bridge = AppBridge({
        "doctor": self.doctor_service,
        "assistant": self.assistant_service,
        "sync": self.sync_orchestrator,
        "pet": self.companion_controller,
        "settings": self.settings_repo,
        "database": self.db_handler,
        "inbox": self.inbox_service,
        "notes": self.notes_service,
        "tasks": self.tasks_service,
    })
    self.web_channel.registerObject("bridge", self.bridge)
    self.web_engine_view.page().setWebChannel(self.web_channel)
