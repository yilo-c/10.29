# 功能健康度检查清单

> 生成日期：2026-05-07
> 基于完整代码扫描（76 个功能，21 个领域）

---

## 最近修复记录（2026-05-08）

| 修复项 | 状态 | 涉及文件 |
|--------|------|----------|
| 设置保存按钮（QMessageBox 缺失导入） | ✅ | `ui/settings/_section_evolution.py` |
| cost_tracker.py API 不匹配 | ✅ | `ai/client/cost_tracker.py` |
| 对话框默认取消置顶 | ✅ | 7 个对话框 `__init__` |
| 工作台页面空白（build_workbench_panel 未调用） | ✅ | `ui/main_window.py`, `workbench/controller.py` |
| `_action_guard` 未初始化导致启动崩溃 | ✅ | `ui/main_window.py` + `workbench/card_renderer.py` |
| `_CardHoverFilter` 缺 QObject 基类 | ✅ | `workbench/card_renderer.py` |
| `_wrap_suggestions_card` 传参错误 (`_action_guard` 当 `workbench_service` 传入) | ✅ | `ui/main_window_panels.py` |
| `_action_dispatcher` 未初始化（使用时会崩溃） | ✅ | `ui/main_window.py`, `ui/main_window_setup.py` |
| `_weather_service` 未初始化（使用时会崩溃） | ✅ | `ui/main_window.py`, `ui/main_window_setup.py` |

## 最近修复记录（2026-05-07）

| 修复项 | 状态 | 涉及文件 |
|--------|------|----------|
| AutoSaveList 对话自动持久化 | ✅ | `core/auto_save_list.py`, `ui/ai_chat_page.py`, `main_window_chat.py`, `main_window_setup.py` |
| 静默异常吞咽修复（第1轮 ~50处） | ✅ | `app_entry/`, `ai/`, `assistant/` (8文件), `ui/` (7文件), `cross_device/`, `core/`, `desktop/`, `evolution/`, `knowledge/` |
| 静默异常吞咽修复（第2轮 ~109处） | ✅ | `assistant/`(35处)、`memory/`(33处)、`ui/`+`services/`+`browser/`(41处) |
| AutoSaveList 单元测试（29个） | ✅ | `tests/test_auto_save_list.py` |
| 冒烟测试（7个） | ✅ | `tests/test_chat_smoke.py`, `test_note_smoke.py`, `test_task_smoke.py` |
| 测试收集错误修复 | ✅ | 1541 个测试全部可收集 |
| 设置持久层冒烟测试（12个） | ✅ | `tests/test_settings_smoke.py` |
| 设置保存按钮问题排查 | 🔍 | 数据层已验证正常，导入路径正确，可能是运行时信号/异常问题 |

## 核心结论

**76 个功能**，分布在 21 个领域。一个人维护这么多功能，必须做出选择。

### 建议保留（P0，核心生产力）
| # | 功能 | 说明 |
|---|------|------|
| 1 | AI 聊天 | 消息发送/流式/历史/Provider 切换 |
| 2 | 笔记 | CRUD + 文件夹 |
| 3 | 任务 | CRUD + 完成切换 |
| 4 | 翻译 | 文本翻译 + 历史 |
| 5 | 新闻 | RSS 抓取 + 阅读 |

### 可保留（P1，辅助功能）
| # | 功能 | 说明 |
|---|------|------|
| 6 | 单词本 | 背单词需要的话 |
| 7 | URL 学习 | 如果常用 |
| 8 | 工作台仪表板 | 概览有用的话 |
| 9 | 闹钟/提醒 | 如果需要 |
| 10 | 天气 | 简单，已集成 |

### 建议冻结或删除（P2+）
| # | 功能 | 理由 |
|---|------|------|
| MCP 工具 | 需要外部 MCP 服务器才能用 |
| 工作流引擎 | 半成品，无测试 |
| 进化分析 | 美观页面，无实际用途 |
| 知识库 RAG | 需要 ChromaDB + embedding 模型 |
| 插件系统 | 无生态，无插件可装 |
| 跨设备同步 | 需要后端服务器 |
| 私人空间 | 概念复杂，用户未必用 |
| 权限矩阵 | 过度工程化 |
| 规则中心 | 同 |
| 自主助手 | 已有 AI 聊天够用 |
| 记忆系统 | 后台运行，前端无感知 |
| 桌宠（2 种） | 纯娱乐 |
| OCR 翻译 | 8 行代码的半成品 |
| 语音输入/TTS | 需额外 API |
| 自动化 Hub | 需外部后端 |

---

## 76 个功能完整清单

格式：`[状态] 功能名 — 入口 → 数据流 → 存储`

状态标记：
- 🟢 已验证可用
- 🟡 部分可用/已知问题
- 🔴 不可用
- ⚪ 空壳
- ❓ 未验证

---

### 领域 A：AI 聊天（6 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| 🟢 | AI 对话 | 侧栏"对话"/Ctrl+2 | CopilotEngine → AI client → 响应渲染 | chat_sessions.json | 有 |
| ❓ | 浮动聊天 | Ctrl+Shift+F | 同上 | 同上 | 有 |
| ❓ | AI 侧面板 | Ctrl+Shift+A | 同上 | 同上 | 有 |
| ❓ | AI 工具面板 | AI 路由触发 | 内联页面 | 无存储 | 无 |
| ❓ | 模型选择器 | 聊天页下拉 | ProviderService → setting | settings.json | 无 |
| ❓ | 意图路由 | AI 内部 | JudgeAwareClient → router | 无 | 有 |

### 领域 B：笔记（3 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| 🟢 | 笔记管理 | 侧栏"笔记"/Ctrl+3 | _note.py → LocalDataStore | notes.json | 有 |
| ❓ | 笔记文件夹 | 笔记面板内联 | 同上 | folders.json | 无 |
| ❓ | 笔记同步 | 设置中开关 | sync/note_sync.py | cloud_notes.json | 无 |

### 领域 C：任务（3 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| 🟢 | 任务管理 | 侧栏"任务"/Ctrl+4 | _task.py → LocalDataStore | tasks.json | 有 |
| ❓ | 任务提醒 | 任务创建自动设置 | ReminderService | reminders.json | 有 |
| ❓ | 任务同步 | 设置中开关 | sync/task_sync.py | cloud_tasks.json | 无 |

### 领域 D：新闻（3 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 新闻推送 | 侧栏"新闻" | news_service → RSS 抓取 | news.json | 有 |
| ❓ | RSS 源管理 | 新闻面板内 | _news_source.py | news_sources.json | 无 |
| ❓ | 新闻同步 | 设置中开关 | sync/news_sync.py | cloud_news.json | 无 |

### 领域 E：翻译（4 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 文本翻译 | 侧栏"翻译"/Ctrl+5 | translate_service → 有道 API | 无 | 部分 |
| ❓ | 翻译历史 | 翻译面板 | _translation.py | translations.json | 部分 |
| ❓ | OCR 翻译 | 翻译面板 OCR 按钮 | translate/ocr.py (8 行!) | 无 | 无 |
| ❓ | 翻译同步 | 设置中开关 | sync/translation_sync.py | cloud_translations.json | 无 |

> OCR 翻译只有 8 行代码，显然是空壳。

### 领域 F：单词本（2 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 单词本 | 侧栏"单词本"/Ctrl+6 | _vocab.py | words.json | 部分 |
| ❓ | 词汇同步 | 设置中开关 | sync/vocabulary_sync.py | cloud_wordbooks.json | 无 |

### 领域 G：URL 学习（1 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 链接学习 | 侧栏"链接学习"/Ctrl+7 | url_learning_service → Web Fetch | 无持久化 | 无 |

### 领域 H：工作台（3 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| 🟡 | 工作台仪表板 | 侧栏"工作台"/Ctrl+1 | WorkbenchController → 聚合数据 | 无 | 有 |
| ❓ | 布局编辑器 | 溢出菜单 → 布局编辑 | layout_manager | settings.json | 无 |
| ❓ | 今日工作视图 | 工作台切换按钮 | WorkbenchController | 无 | 无 |

### 领域 I：收件箱（3 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 个人收件箱 | 侧栏"收件箱" | InboxCaptureService | inbox_entries.json | 有 |
| ❓ | 内容整理 | "转笔记"/"转任务"按钮 | content_organizer_service | 多种 | 无 |
| ❓ | 私人空间 | "组织到私人空间" | private_space_service | 三层模型 | 有 |

### 领域 J：知识库（1 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 知识库/RAG | 侧栏"知识库" | ChromaDB + embedding | chroma/ | 有 |

### 领域 K：MCP/工具集成（4 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | MCP 服务器 | 侧栏"MCP 工具" | mcp/client.py | settings.json | 有 |
| ❓ | 工作流编辑器 | 侧栏"工作流" | workflow/execution_engine.py | workflows.json | 无 |
| ❓ | 插件管理 | 侧栏"插件" | plugins/registry.py | 文件系统 | 有 |
| ❓ | 扩展注册表 | 后台运行 | extension_service.py | settings.json | 有 |

### 领域 L：设置（1 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| 🟡 | 应用设置 | 侧栏"设置"/Ctrl+, | _settings.py → LocalDataStore | settings.json | 有（12 冒烟测试 ✅） |

> 12 个设置分区，含 AI Provider 配置。数据持久层已验证（12 冒烟测试全部通过），导入路径与保存流程逻辑均正确。若运行时仍无法保存，可能是信号连接或异常捕获问题，需运行应用后查看日志确认。

### 领域 M：进化/诊断（3 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 进化系统 | 侧栏"进化" | evolution/orchestrator.py | 无 | 有 |
| ❓ | 技术瞭望 | 设置中 | tech_watch.py | tech_watch.db | 无 |
| 🟡 | 桌面医生 | Ctrl+Alt+D | desktop_doctor_service.py | 无 | 有 |

### 领域 N：桌宠（3 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | Nana 桌宠 | 托盘菜单 → 小问桌宠 | pet_controller.py | 帧图文件 | 无 |
| ❓ | Neuro 桌宠 | 托盘菜单 → 肥牛桌宠 | 同上 (Live2D) | Live2D 模型 | 无 |
| ❓ | 情绪系统 | AI 响应自动触发 | _set_pet_mood() → controller | 无 | 无 |

### 领域 O：闹钟（1 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 闹钟提醒 | 溢出菜单 → 闹钟提醒 | _reminder.py → ReminderService | reminders.json | 有 |

### 领域 P：网络服务（3 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 网页搜索 | "/search" 命令 | web_search.py | web_search_cache | 有 |
| ❓ | 网页抓取 | AI 内部调用 | web_fetch.py | 无 | 无 |
| ❓ | 天气服务 | 工作台"今日情境" | weather_service.py | settings.json | 无 |

### 领域 Q：助手指令（4 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 截屏 | Ctrl+Shift+S | screenshot_service.py | 剪贴板 | 无 |
| ❓ | 语音输入 | Ctrl+Shift+M | voice_input_service.py | 临时 WAV | 无 |
| ❓ | TTS | 聊天页 TTS 按钮 | tts.py | 无 | 无 |
| ❓ | 自主助手 | 后台运行 | autonomy.py | 多种 | 有 |

### 领域 R：记忆系统（3 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 记忆索引 | 后台运行 | memory/index.py | ChromaDB | 有 |
| ❓ | 记忆提取 | 对话完成后触发 | memory/extractor/ | ChromaDB | 有 |
| ❓ | 超标签 | 后台/AI 调用 | supertag_search.py | supertag JSON | 有 |

### 领域 S：同步（2 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 跨设备同步 | 设置中 | cross_device/session.py | cross_device_sessions | 有 |
| ❓ | 文件同步 | 后台 | sync/file_sync_service.py | 多种 | 有 |

### 领域 T：系统工具（6 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | 审计回滚 | 溢出菜单 | audit_rollback.py | data/backups/ | 有 |
| ❓ | 撤销管理 | 撤销对话框 | audit_rollback.py | 同上 | 无 |
| ❓ | 规则中心 | 溢出菜单 | rule_center.py | settings.json | 无 |
| ❓ | 权限矩阵 | 溢出菜单 | permission_matrix.py | settings.json | 有 |
| ❓ | 生命周期恢复 | 启动时 | lifecycle_recovery_service.py | 无 | 有 |
| ❓ | 行为日志 | 后台 | behavior_logger.py | 无 | 无 |

### 领域 U：基础设施（5 个功能）

| 状态 | 功能 | 入口 | 数据流 | 存储 | 测试 |
|------|------|------|--------|------|------|
| ❓ | Cron 调度 | 启动时 | cron_service.py | cron_jobs.json | 无 |
| ❓ | 浏览器桥 | 设置中 | browser/assistant_bridge.py | 无 | 有 |
| ❓ | 可观测性 | 后台 | observability.py | 无 | 有 |
| ❓ | 习惯学习 | 后台 | habit_learner.py | user_profile | 无 |
| ❓ | 用户画像 | 后台 | user_profile.py | assistant_user_profile | 有 |

---

## 验证优先级建议

### 第一轮（你今天能做的）

```powershell
cd D:\h\3.25\思忆集
.\.venv3.11\Scripts\python.exe 思忆集app\main.py
```

1. **AI 聊天**：发消息 → 看数据在 chat_sessions.json 里 → 关掉重开 → 看历史加载
2. **笔记**：创建笔记 → 看 notes.json → 关掉重开
3. **任务**：创建任务 → 看 tasks.json → 关掉重开
4. **翻译**：翻译一句话 → 看 translations.json
5. **新闻**：手动刷新 → 看 news.json

### 第二轮（决定砍哪些）

每项花 5 分钟判断：这个功能你真的在用/会用吗？

- 不用 → 删掉对应 dialog + panel + service + repository（至少 50% 的代码可以删）
- 用但坏了 → 修端到端链路
- 可能以后用 → 冻结，不投入修复时间

### 第三轮（系统修复）

对保留的功能：

1. 写一个简单的端到端测试（create → save → load → verify）
2. 确保所有错误路径有用户可见的错误提示
3. 堵上"数据在内存但不落盘"的漏洞
