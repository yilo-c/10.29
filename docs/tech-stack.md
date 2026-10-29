# 技术栈一览

本项目涉及三层技术栈：桌面端（壳层 + 业务 UI）、后端服务（automation-hub）、桌宠进程。本文档集中记录各层当前技术、问题与替代规划，作为技术决策的单一信息源。

---

## 桌面壳层

| 技术 | 版本 | 用途 | 状态 | 问题 / 替代 |
|------|------|------|------|------------|
| PyQt6 | 6.x | 主窗口、系统托盘、菜单栏、全局快捷键、QWebEngineView 容器 | **保留** | — |
| QWebEngineView | (内置) | 嵌入 Web 前端 | **启用** | 替代所有 PyQt 手工 Widget 对话框 |
| QSystemTrayIcon | (内置) | 系统托盘 | **保留** | — |
| PyInstaller / Nuitka | 待选 | 桌面端打包分发为 .exe | **待引入** | 当前仅有 `.bat` 启动脚本 |

---

## 业务 UI 层

| 技术 | 版本 | 用途 | 状态 | 问题 / 替代 |
|------|------|------|------|------------|
| PyQt6 手工 Widget | — | 10+ 业务对话框（笔记、任务、收件箱、新闻、翻译、设置等） | **🔴 待替换** | 每个对话框独立文件，样式不统一，组件无法复用，难以扩展。**→ Web UI（React/Vue + 组件库）嵌入 QWebEngineView** |
| QWebChannel | (内置) | Python ↔ JavaScript 双向通信桥 | **待启用** | 当前无桥接层，引入 Web UI 时作为基础设施 |
| React / Vue | 待选 | Web 前端框架，承载全部业务界面 | **待引入** | 二选一，建议 React（生态更丰富，状态管理方案成熟） |
| TypeScript | — | 前端类型安全 | **建议引入** | 配合 React/Vue 提升前端代码质量 |

---

## 后端服务 (automation-hub)

| 技术 | 当前版本 | 用途 | 状态 | 问题 / 替代 |
|------|---------|------|------|------------|
| FastAPI | 0.105 | REST API 框架 | **🟡 待升级** | 多个安全修复落后。**→ 0.115+** |
| Pydantic | 1.10.13 | 数据校验与序列化 | **🔴 待升级** | v1 已停维，FastAPI v2 已全面迁移。**→ Pydantic v2**（注意破坏性变更） |
| Uvicorn | 0.34 | ASGI 服务器 | **保留** | 版本较新，暂无需升级 |
| SQLite | — | 数据库（单副本） | **🟢 待迁移** | 生产环境多副本受限。**→ PostgreSQL**（README 已提及） |
| 裸 SQL | — | 数据访问（DatabaseHandler.py） | **🔴 待替换** | 无 ORM，类型不安全，迁移困难。**→ SQLAlchemy 2.0 + Alembic** |
| RQ | 1.16 | 任务队列 | **🟡 待评估** | 无原生重试策略、工作流编排能力弱。**→ ARQ（async native，更轻量）或 Celery** |
| Redis | 7.x | 消息代理 / 缓存 | **保留** | — |
| requests | — | HTTP 客户端（桌面端） | **🟢 待替换** | 同步阻塞 event loop。**→ httpx**（支持 async） |
| Docker | — | 执行器容器化 | **保留** | — |
| Kubernetes / Helm | — | 生产部署 | **保留** | — |

---

## 桌宠层

| 技术 | 类型 | 用途 | 状态 |
|------|------|------|------|
| 七奈桌宠 (Shimeji-ee) | Java 进程 | 桌面精灵角色 | **保留** |
| 肥牛桌宠 | Live2D + Electron 进程 | 桌宠角色 | **保留** |
| os.startfile / subprocess | Python 标准库 | 桌宠进程启动与管理 | **保留** |

---

## 桌面端依赖清单 (requirements-desktop.txt)

| 包 | 当前版本 | 用途 | 状态 |
|----|---------|------|------|
| PyQt6 | 6.x | GUI 框架 | 保留 |
| PyQt6-WebEngine | 6.x | Web 引擎（QWebEngineView 依赖） | 保留 |
| requests | 2.x | HTTP 客户端 | → httpx |
| schedule | — | 定时任务 | 保留 |
| feedparser | — | RSS 解析 | 保留 |
| markdown | — | Markdown 渲染 | 保留 |
| pypiwin32 | — | Windows API 调用 | 保留 |
| python-docx | — | Word 文档读写 | 保留 |
| openpyxl | — | Excel 文档读写 | 保留 |

---

## 技术升级优先级

| 优先级 | 项目 | 影响面 | 风险 |
|--------|------|--------|------|
| 🔴 P0 | PyQt Widget → Web UI (QWebEngineView) | 所有业务对话框 | 高（架构级变更，需分步迁移） |
| 🔴 P0 | 裸 SQL → SQLAlchemy 2.0 + Alembic | 全部数据访问层 | 中（需建立迁移脚本） |
| 🟡 P1 | Pydantic v1 → v2 | 全部 API schema | 中（破坏性变更，需批量适配） |
| 🟡 P1 | FastAPI 0.105 → 0.115+ | API 层 | 低 |
| 🟡 P1 | RQ → ARQ / Celery | Worker 执行 | 中（任务语义迁移） |
| 🟢 P2 | requests → httpx | 桌面端 HTTP 调用 | 低 |
| 🟢 P2 | SQLite → PostgreSQL | 数据库 | 中（需部署 PostgreSQL 实例） |
| 🟢 P2 | .bat 启动 → PyInstaller/Nuitka 打包 | 分发体验 | 低 |

---

## 决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-04-29 | 保留 PyQt6 壳层，业务 UI 通过 QWebEngineView 嵌入 Web 前端 | 桌宠需要 PyQt 原生能力（进程管理、悬浮窗口），Web UI 提供更好的业务界面扩展性和样式统一性 |
| 2026-04-29 | 暂不引入第三方 DI 框架 | `app_context` 手动装配足够轻量，够用于当前规模 |
| 2026-04-20 | Service 层统一通过 `app_context` 依赖注入 | 消除 `parent_ref.db_handler` 等隐式共享字段穿透 |
| 2026-04-03 | 开发根目录固定为 `思忆集/` | 避免路径混用和任务执行漂移 |
