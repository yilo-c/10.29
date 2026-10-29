# Agent 模块集成指南

## 🎯 完成情况

已创建以下文件：

```
automation-hub/agent/
├── __init__.py          ✅ 模块入口
├── models.py            ✅ 数据模型定义
├── context.py           ✅ 对话上下文管理
├── planner.py           ✅ 规划器（LLM集成）
├── executor.py          ✅ 执行器
├── example_config.py    ✅ 示例配置
├── README.md            ✅ 使用文档
└── tests/
    ├── __init__.py      ✅ 测试包
    ├── test_context.py  ✅ 上下文测试
    └── test_models.py   ✅ 模型测试

automation-hub/api/routes/
└── agent.py             ✅ API 路由

automation-hub/api/
└── main.py              ✅ 已集成 Agent 路由
```

## 🚀 下一步操作

### 1️⃣ 立即可用（无需额外配置）

**测试上下文管理和数据模型：**

```bash
# 进入项目目录
cd automation-hub

# 运行测试
pytest agent/tests/test_context.py -v
pytest agent/tests/test_models.py -v
```

**运行示例代码：**

```bash
# 注意：需要先有数据库和工具注册
python agent/example_config.py
```

### 2️⃣ API 测试（需启动服务）

**启动服务：**

```bash
# 在项目根目录
python automation-hub/api/main.py
# 或
uvicorn api.main:app --reload
```

**测试 API：**

```bash
# 询问 Agent（模拟模式）
curl -X POST http://localhost:8000/agent/ask \
  -H "Content-Type: application/json" \
  -d '{
    "query": "搜索所有 TODO 注释",
    "session_id": "test-session"
  }'

# 生成计划
curl -X POST http://localhost:8000/agent/plan \
  -H "Content-Type: application/json" \
  -d '{
    "query": "格式化所有 Python 文件"
  }'
```

### 3️⃣ 集成 LLM（推荐步骤）

当前使用的是模拟 LLM，需要集成真实的 LLM API：

#### 选项 A: OpenAI

**安装依赖：**
```bash
pip install openai
```

**修改 `planner.py` 的 `_call_llm` 方法：**

```python
async def _call_llm(self, prompt: str) -> str:
    import openai
    
    # 设置 API Key
    openai.api_key = os.getenv("OPENAI_API_KEY")
    
    response = await openai.ChatCompletion.acreate(
        model="gpt-4-turbo-preview",
        messages=[
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        response_format={"type": "json_object"}
    )
    return response.choices[0].message.content
```

**设置环境变量：**
```bash
export OPENAI_API_KEY="your-api-key"
```

#### 选项 B: Claude (Anthropic)

**安装依赖：**
```bash
pip install anthropic
```

**修改代码：**
```python
async def _call_llm(self, prompt: str) -> str:
    import anthropic
    
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    
    response = await client.messages.create(
        model="claude-3-sonnet-20240229",
        max_tokens=4096,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    return response.content[0].text
```

#### 选项 C: 本地模型 (Ollama)

**安装 Ollama：**
```bash
# 访问 https://ollama.ai/ 下载安装
ollama pull llama3
```

**修改代码：**
```python
async def _call_llm(self, prompt: str) -> str:
    import httpx
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3",
                "prompt": prompt,
                "stream": False
            }
        )
        return response.json()["response"]
```

### 4️⃣ 集成现有工具执行

修改 `executor.py` 的 `_execute_step` 方法：

**当前是模拟的：**
```python
run_response = {
    "run_id": f"run_{step.step_id}",
    "status": "queued"
}
```

**改为实际调用：**
```python
# 导入现有的 API 客户端或直接调用数据库
from ...api.routes.runs import create_run as api_create_run

run_response = await api_create_run({
    "tool_id": step.tool_id,
    "args": step.args
}, user_id=user_id)
```

### 5️⃣ 注册工具（必需）

Agent 需要有可用的工具才能工作。参考之前的工具注册：

```python
# 示例：注册代码搜索工具
import sqlite3

conn = sqlite3.connect("data/automation_hub.sqlite3")
cursor = conn.cursor()

cursor.execute("""
    INSERT INTO tools (id, name, description, risk_level, executor, command_json, args_schema_json, enabled)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
""", (
    "code_search",
    "代码搜索",
    "使用 ripgrep 搜索代码",
    "read",
    "host",
    '["rg", "--json"]',
    '{"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}',
    1
))

conn.commit()
conn.close()
```

## 📝 集成检查清单

- [x] ✅ 创建 Agent 模块文件
- [x] ✅ 创建 API 路由
- [x] ✅ 集成到 main.py
- [x] ✅ 编写测试用例
- [ ] ⚠️ 集成真实 LLM（需要 API Key）
- [ ] ⚠️ 连接实际工具执行系统
- [ ] ⚠️ 注册至少 1 个可用工具
- [ ] ⚠️ 端到端测试
- [ ] ⚠️ 添加审批处理逻辑
- [ ] ⚠️ 持久化对话上下文

## 🧪 测试验证

### 单元测试

```bash
# 测试数据模型
pytest automation-hub/agent/tests/test_models.py -v

# 测试上下文管理
pytest automation-hub/agent/tests/test_context.py -v
```

### 集成测试（需要数据库）

```python
# 创建测试脚本 test_integration.py
import asyncio
from automation_hub.agent import AgentPlanner, AgentExecutor, ConversationContext
from automation_hub.agent.example_config import MockLLMClient, MockAPIClient

async def test():
    planner = AgentPlanner(MockLLMClient(), "data/automation_hub.sqlite3")
    executor = AgentExecutor(MockAPIClient(), None)
    context = ConversationContext("test_user", "test_session")
    
    plan = await planner.plan("搜索 TODO", context)
    result = await executor.execute_plan(plan, "test_user")
    
    print("✅ 测试通过！")
    print(f"状态: {result.status}")
    print(f"摘要: {result.summary}")

asyncio.run(test())
```

## 🔧 常见问题

### Q1: "找不到 agent 模块"

**解决方案：**
```bash
# 确保在正确的目录
cd /d D:\BaiduNetdiskDownload\BaiduSyncdisk\个人\思忆集\思集\运维

# 或者设置 PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:/path/to/运维"
```

### Q2: "数据库中没有工具"

**解决方案：**
```bash
# 先注册工具
python automation-hub/scripts/register_example_tools.py

# 或手动插入
sqlite3 data/automation_hub.sqlite3
> INSERT INTO tools (...) VALUES (...);
```

### Q3: "LLM 返回格式错误"

**解决方案：**
- 检查 Prompt 是否明确要求 JSON 格式
- 使用 `response_format={"type": "json_object"}` (OpenAI)
- 添加响应验证和重试逻辑

### Q4: "执行超时"

**解决方案：**
- 调整 `timeout_seconds` 参数
- 检查工具是否实际在执行
- 查看 Worker 日志

## 📚 参考资源

- [Agent README](automation-hub/agent/README.md) - 详细使用文档
- [示例配置](automation-hub/agent/example_config.py) - 配置示例
- [ARCHITECTURE_DESIGN.md](ARCHITECTURE_DESIGN.md) - 整体架构

## 🎉 成功标志

当你完成集成后，应该能够：

1. ✅ 通过 API 发送自然语言请求
2. ✅ Agent 自动生成执行计划
3. ✅ 执行计划调用实际工具
4. ✅ 返回格式化的执行结果
5. ✅ 保持对话上下文

**示例成功输出：**

```json
{
  "plan_id": "plan_abc123",
  "summary": "✅ 找到 15 个 TODO 注释\n关键结果:\n  1. src/main.py: TODO: 优化性能\n  2. src/utils.py: TODO: 添加测试...",
  "status": "success",
  "steps_completed": 1,
  "total_steps": 1,
  "session_id": "session456",
  "execution_time": 2.3
}
```

恭喜！🎊 Agent 模块已成功添加，可以开始使用了！
