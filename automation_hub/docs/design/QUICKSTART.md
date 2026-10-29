# 快速开始指南

本指南帮助您快速启动和使用 Automation Hub（无需AI）

## 📋 前置要求

- Python 3.10+
- SQLite 3
- Git（如果使用Git工具）
- ripgrep（如果使用代码搜索）

## 🚀 快速开始

### 1. 安装依赖

```bash
# 安装CLI依赖
pip install -r automation-hub/requirements-cli.txt

# 安装ripgrep（可选，用于代码搜索）
# Windows: choco install ripgrep
# Linux: apt install ripgrep
# Mac: brew install ripgrep
```

### 2. 初始化数据库

```bash
# 运行数据库迁移
python automation-hub/api/db/migrate.py

# 初始化系统（创建管理员账户等）
python automation-hub/quickstart.py
```

### 3. 注册工具

```bash
# 批量注册常用工具
python automation-hub/scripts/register_tools.py

# 查看已注册的工具
python automation-hub/cli.py tools list
```

### 4. 使用CLI

```bash
# 查看系统状态
python automation-hub/cli.py status

# 列出所有工具
python automation-hub/cli.py tools list

# 查看工具详情
python automation-hub/cli.py tools show code_search

# 执行工具
python automation-hub/cli.py run code_search --args '{"pattern": "TODO", "path": "."}'

# 查看任务列表
python automation-hub/cli.py runs list

# 查看任务状态
python automation-hub/cli.py runs status <run_id>

# 查看任务日志
python automation-hub/cli.py runs logs <run_id>
```

## 📚 常用命令示例

### 代码搜索

```bash
# 搜索TODO注释
python automation-hub/cli.py run code_search --args '{"pattern": "TODO"}'

# 搜索特定文件类型
python automation-hub/cli.py run code_search --args '{"pattern": "import", "file_type": "py"}'
```

### Git操作

```bash
# 查看Git状态
python automation-hub/cli.py run git_status

# 查看文件差异
python automation-hub/cli.py run git_diff

# 查看提交日志
python automation-hub/cli.py run git_log --args '{"count": 5}'
```

### 代码质量

```bash
# 运行测试
python automation-hub/cli.py run run_pytest --args '{"path": "tests/"}'

# Lint检查
python automation-hub/cli.py run lint_python --args '{"path": "."}'

# 代码格式化（需要审批）
python automation-hub/cli.py run format_python --args '{"path": "src/"}'
```

### 审批管理

```bash
# 查看待审批请求
python automation-hub/cli.py approvals list

# 批准请求
python automation-hub/cli.py approvals approve <approval_id>

# 拒绝请求
python automation-hub/cli.py approvals deny <approval_id> --reason "不安全"
```

### 审计日志

```bash
# 查看最近的审计日志
python automation-hub/cli.py audit list

# 查看最近1小时的日志
python automation-hub/cli.py audit list --last 1h

# 查看最近24小时的日志
python automation-hub/cli.py audit list --last 24h

# 按事件类型过滤
python automation-hub/cli.py audit list --event-type run.executed
```

## 🔧 工具管理

### 启用/禁用工具

```bash
# 禁用工具
python automation-hub/cli.py tools disable format_python

# 启用工具
python automation-hub/cli.py tools enable format_python
```

### 查看工具详情

```bash
# 查看完整的工具定义
python automation-hub/cli.py tools show code_search
```

## 🐛 故障排查

### 问题1: "工具不存在"

**解决方案:**
```bash
# 重新注册工具
python automation-hub/scripts/register_tools.py

# 检查工具列表
python automation-hub/cli.py tools list
```

### 问题2: "任务一直是queued状态"

**原因:** Worker没有运行

**解决方案（临时）:** 使用SimpleExecutor直接执行
```python
from automation_hub.simple_executor import SimpleExecutor

executor = SimpleExecutor("data/automation_hub.sqlite3")
result = executor.execute_tool("code_search", {"pattern": "TODO"})
print(result)
```

### 问题3: "ripgrep命令不存在"

**解决方案:** 安装ripgrep
```bash
# Windows
choco install ripgrep

# Linux
sudo apt install ripgrep

# Mac
brew install ripgrep
```

## 📖 进阶使用

### 在Python代码中使用

```python
from automation_hub.simple_executor import SimpleExecutor

# 创建执行器
executor = SimpleExecutor("data/automation_hub.sqlite3")

# 执行工具
result = executor.execute_tool(
    tool_id="code_search",
    args={"pattern": "TODO", "path": "."},
    user_id="admin"
)

# 检查结果
if result["success"]:
    print("执行成功！")
    print(result["stdout"])
else:
    print("执行失败：", result.get("error"))

# 查看任务状态
if "run_id" in result:
    status = executor.get_run_status(result["run_id"])
    print(status)
```

### 创建别名（可选）

在 `.bashrc` 或 `.zshrc` 中添加：

```bash
alias hub='python automation-hub/cli.py'
```

然后就可以使用：

```bash
hub tools list
hub run code_search --args '{"pattern": "TODO"}'
hub status
```

## 🎯 下一步

1. **启动API服务** - `python automation-hub/api/main.py`
2. **创建Web UI** - 使用Streamlit或React
3. **设置定时任务** - 自动化常规操作
4. **添加更多工具** - 根据需要注册自定义工具

## 💡 提示

- 所有操作都会记录审计日志
- 高风险操作（write, exec_high）需要审批
- 使用 `--help` 查看命令帮助
- CLI支持通过部分ID匹配（如：只输入前8个字符）

祝使用愉快！🎉
