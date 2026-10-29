# 思忆集

> 个人 AI 助手桌面应用 + 手机端 + 自动化执行底座
>
> 主仓库：`D:\h\3.25\思忆集`

## 这是什么

思忆集是一个**有人格的个人 AI 管家**（小问），跨桌面 + 手机两端共享同一套大脑。
不是工具集合，是"延伸记忆 + 外置思考"。

```
桌面端 (Electron desk-notes/)              Python 后端 (思忆集app/)
┌─────────────────────┐    WS JSON-RPC     ┌──────────────────────────┐
│  Orb + Chat + UI    │ ◄────────────────► │ backend/headless_main.py │
│  (React/TypeScript) │    port 18765      │   ws_server.py           │
└─────────────────────┘                    │   method_registry.py     │
                                           │   handlers/ (18 模块)     │
手机端 (思忆集手机端app/)                   │   smolagents 18 工具      │
┌─────────────────────┐                    │                          │
│ Capacitor + React   │ ◄────────────────► │ AppContext (DI 容器)     │
│ + Android 原生桥接  │                    │   AI / Memory / ...      │
└─────────────────────┘                    └──────────────────────────┘
```

## 启动

**双击 `启动.bat`**，自动起后端 + Electron 前端。或分步：

```powershell
# 1. 后端
cd 思忆集app
..\.venv3.11\Scripts\python.exe -m backend.headless_main

# 2. 前端
cd desk-notes
npm run electron:dev

# 3. 手机端（开发模式）
cd 思忆集手机端app
npm run dev
```

## 关键目录

| 目录 | 内容 |
|---|---|
| `思忆集app/` | Python 后端主源码（backend/ + ai/ + memory/ + ...） |
| `desk-notes/` | Electron 桌面前端 |
| `思忆集手机端app/` | Capacitor + React 手机端（v2.0.x） |
| `automation_hub/` | 历史自动化执行底座（**已弱化**，新功能不走这里） |
| `browser-extension/` | 浏览器扩展 |
| `docs/` | 技术文档（37 份精简版，归档见 `docs/archive/`） |
| `tests/` | 根级 pytest（246 用例） |
| `archive/android-deprecated-202606/` | 已归档的旧 Kotlin 客户端 |

## 文档入口

- **[AGENTS.md](AGENTS.md)** — AI 协作必读：技术栈、357+ JSON-RPC 方法表、目录结构
- **[docs/INDEX.md](docs/INDEX.md)** — 文档真源索引（唯一导航入口）
- **[docs/document-map.md](docs/document-map.md)** — 文档地图（历史模块地图）
- **[docs/audit-2026-06-18.md](docs/archive/audit-2026-06-18.md)** — 早期审查与修复报告（已归档）
- **桌面前端**：[desk-notes/CLAUDE.md](desk-notes/CLAUDE.md)
- **手机端**：[思忆集手机端app/CLAUDE.md](思忆集手机端app/CLAUDE.md)

## 技术栈

- **前端 Electron**: React 18 + TypeScript + Zustand + Vite
- **手机端**: Capacitor 6 + React 18 + Zustand + Vite + framer-motion
- **后端**: Python 3.11 + asyncio + aiohttp WebSocket
- **AI**: DeepSeek (主) + Moonshot (路由) + Ollama qwen3-4b (本地)
- **向量搜索**: ChromaDB + sentence-transformers

## 安全约定

- `data/settings.json` 含 API Key — 已在 `.gitignore`
- 新版 provider key 走 `provider_configs` Fernet 加密；旧 flat key 字段已废弃
- 手机端凭证（API token / DeepSeek key / PIN hash）走 Capacitor Preferences（Android EncryptedSharedPreferences / iOS Keychain）
- ActionGuard 默认拒绝 LLM 触发的高危动作（shell.run / file.write / process.kill 等），用户需显式 opt-in `warn_only` 模式

## 测试

```powershell
# 桌面端 Python
.venv3.11\Scripts\python.exe -m pytest tests/

# 桌面端完整套件
.venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/

# 手机端
cd 思忆集手机端app
npm run test
npm run typecheck

# 桌面 Electron
cd desk-notes
npm run typecheck
npm run test
```

## License
MIT — 见 [LICENSE](LICENSE)
