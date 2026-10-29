# 开发环境与工作流指南

> 本文档回答：如何从零搭建思忆集的开发环境、如何运行和调试、以及常见的开发陷阱。

---

## 1. 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | ≥ 3.11 | 虚拟环境 `.venv3.11/` |
| Redis | ≥ 5.0 | 后端任务队列（若只做桌面端开发可跳过） |
| Git | 任意 | 版本控制 |
| Windows Terminal | 任意 | 推荐终端 |

桌面端开发只需 Python 虚拟环境。后端开发还需要 Redis。

---

## 2. 快速启动

### 2.1 首次搭建

```powershell
# 1. 进入项目目录
cd D:\h\3.25\思忆集

# 2. 创建虚拟环境（如不存在）
python -m venv .venv3.11

# 3. 激活虚拟环境
.venv3.11\Scripts\activate

# 4. 安装桌面端依赖
pip install -r requirements-desktop.txt

# 5. 验证环境
python validate_syntax.py
```

### 2.2 启动桌面端

```powershell
# 方式 A：直接启动
python 思忆集app/思忆集test1.py

# 方式 B：VS Code 任务
# Ctrl+Shift+P → Run Task → 思忆集app: run

# 方式 C：smoke-then-run（推荐）
# Ctrl+Shift+P → Run Task → 思忆集app: smoke-then-run
```

### 2.3 启动后端

```powershell
# 启动 API 服务
cd automation-hub
uvicorn api.main:app --host 127.0.0.1 --port 8010 --reload

# 启动 Worker（另一个终端，需要 Redis）
python worker/worker.py
```

---

## 3. 目录导航速查

| 当你想… | 去这里 |
|---------|--------|
| 修改后端 API | `automation-hub/api/` |
| 修改桌面 UI | `思忆集app/ui_*.py` |
| 修改数据模型 | `automation-hub/api/models/` |
| 修改数据库 | `automation-hub/api/db/migrations/` |
| 添加测试 | `tests/`（根级）或 `automation-hub/tests/` |
| 查看文档 | `docs/` → 从 `document-map.md` 开始 |
| 学习代码 | `思忆集项目学习笔记/思忆集项目按文件学习笔记.md` |

---

## 4. 质量门禁

### 4.1 日常开发门禁

```powershell
# 快速冒烟测试（开发中频繁执行）
python automation-hub/scripts/release_check.py --mode smoke

# 完整回归（提交前）
python automation-hub/scripts/release_check.py --mode regression

# 桌面端冒烟
python 思忆集app/smoke_check.py

# 桌面端发布门禁
python scripts/desktop_quality_gate.py --suite release

# 语法检查
python validate_syntax.py

# 旧 venv 路径防回退
python scripts/check_legacy_venv_refs.py
```

### 4.2 VS Code 任务入口

| 任务 | 用途 |
|------|------|
| `automation-hub: smoke` | 后端冒烟 |
| `automation-hub: regression` | 后端全回归 |
| `思忆集app: smoke-check` | 桌面端冒烟 |
| `思忆集app: run` | 启动桌面端 |
| `思忆集app: smoke-then-run` | 冒烟通过后自动启动 |
| `思忆集: legacy-venv-guard` | venv 路径检查 |

---

## 5. 开发循环

### 5.1 修改后端代码

```
1. 修改 api/models/ 或 api/routes/
2. 运行: python automation-hub/scripts/release_check.py --mode smoke
3. 若通过: 启动 API 手动验证 → http://127.0.0.1:8010/docs
4. 若新增路由: 更新 api/main.py 注册
5. 若改动模型: 生成 Alembic 迁移
```

### 5.2 修改桌面端代码

```
1. 修改 思忆集app/ui_*.py
2. 运行: python 思忆集app/smoke_check.py
3. 若通过: 启动桌面端手动验证
4. 注意: ui_main_window.py 6695 行，不要继续往上堆逻辑
5. 新功能优先走插件化或独立模块，不扩主窗口
```

### 5.3 数据库变更

```
1. 修改 models/ 中的 SQLAlchemy 模型
2. 生成迁移: alembic revision --autogenerate -m "描述"
3. 检查生成的迁移脚本
4. 应用迁移: alembic upgrade head
5. 运行回归测试确认
```

---

## 6. 常见开发陷阱

### 6.1 路径混淆

- 开发根目录固定在 `D:\h\3.25\思忆集`，不要 cd 到子目录
- `automation_hub`（下划线）是兼容导入层，不要往里写新代码
- 所有改动只在 `automation-hub`（连字符）和 `思忆集app` 两个入口

### 6.2 虚拟环境

- 只有一个活跃虚拟环境：`.venv3.11/`
- `.venv3.11.broken-*` 是损坏的历史环境，已从 `.gitignore` 排除
- 不要创建新的 venv，统一使用 `.venv3.11/`

### 6.3 不要做的事

- 不要直接改 `ui_main_window.py` 增加新功能 → 走插件/独立模块
- 不要把新业务逻辑放进 `automation_hub/`（下划线包）
- 不要把第三方 API key 写到代码里 → 用环境变量
- 不要用 `os.startfile` / `subprocess` 绕过 action_guard
- 不要提交 `__pycache__/`、`.pytest_cache/`、`*.backup`、`*.bak`

### 6.4 巨型文件警告

| 文件 | 行数 | 红线 | 处理方式 |
|------|------|------|---------|
| `ui_main_window.py` | 6695 | 800 | 解耦计划进行中 |
| `ai_service.py` | 1145 | 800 | 应拆分 provider 层 |
| `cli.py` | 781 | 800 | 接近红线，注意控制 |

---

## 7. 调试

### 7.1 后端调试

- Swagger UI: `http://127.0.0.1:8010/docs`
- 健康检查: `http://127.0.0.1:8010/health`
- VS Code 调试：使用 `/.vscode/launch.json` 中的配置

### 7.2 桌面端调试

- 查看日志：`data/logs/`
- 桌面 Doctor：启动后通过托盘菜单访问
- 冷启动稳定性检查：`python scripts/cold_start_stability_check.py --rounds 30`

### 7.3 常见问题

| 问题 | 排查 |
|------|------|
| 启动 desktop 闪退 | 查看 `data/logs/` 最新日志 |
| API 500 错误 | 检查 Redis 是否运行；检查 `.admin_token` |
| 同步失败 | 检查 `automation-hub` 是否在运行；检查 token |
| 翻译不可用 | 检查环境变量 `SIYI_YOUDAO_APP_KEY` |

---

## 8. 文档维护

修改代码后如果涉及设计变更，按 [document-map.md](document-map.md) 的「新内容应该写到哪里」判断是否需要更新文档。
