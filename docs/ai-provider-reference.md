# AI 服务商与模型参考

> 最后更新：2026-05-18
> 用途：集中记录所有可用/可用的 AI 服务商、模型、接入方式、当前状态

---

## 一、当前正在运行的服务

### 1. Kiro-gateway（AWS Q Developer 反代）

| 项目 | 内容 |
|------|------|
| 端口 | `http://127.0.0.1:8000` |
| API Key | `sk-kiro-local`（Authorization: Bearer） |
| 协议 | OpenAI `/v1/chat/completions` + Anthropic `/v1/messages` |
| 状态 | ✅ 多账号轮换运行中 |
| 源码 | `D:\h\3.25\中转\kiro-gateway\` |

**可用模型（已验证）：**

| 模型 | 状态 | 说明 |
|------|------|------|
| `claude-sonnet-4.5` | ✅ 已验证 | 主力编码模型 |
| `claude-sonnet-4` | ✅ 已验证 | 次选 |
| `claude-haiku-4.5` | ✅ 已验证 | 轻量快速 |
| `auto-kiro` | ⚠️ 未测试 | 自动选择 |

**多账号配置：** `ACCOUNT_SYSTEM=true` + `credentials.json` → `D:/kiro-accounts/` 文件夹扫描
**当前账号：** 3 个
- `acc1.sqlite3` — Builder ID (<your-email>, 配额已耗尽)
- `acc3.sqlite3` — GitHub (19552989837@163.com, 有配额)
- `acc4.sqlite3` — GitHub (19552989837@139.com, 有配额)
- 自动轮换 + 熔断 + 指数退避

**curl 测试：**
```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-kiro-local" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4.5","messages":[{"role":"user","content":"hi"}]}'
```

---

### 2. Ollama（本地）

| 项目 | 内容 |
|------|------|
| 端口 | `http://127.0.0.1:11434` |
| API | OpenAI 兼容 `/v1/chat/completions` + 原生 `/api/generate` |
| 状态 | ✅ 运行中 |

**已安装模型：**
- `qwen3-4b` — 日常对话/分类
- `qwen2.5-coder:3b` — 代码辅助

**可扩展（需手动 pull）：**
```bash
ollama pull qwen2.5:7b       # 需要 ~8GB
ollama pull qwen2.5:14b      # 需要 ~16GB
ollama pull deepseek-r1:8b   # 推理能力强
```

**curl 测试：**
```bash
curl http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:3b","messages":[{"role":"user","content":"hi"}]}'
```

---

### 3. DeepSeek API（主力云端）

| 项目 | 内容 |
|------|------|
| API Base | `https://api.deepseek.com/v1` |
| API Key | 已配置（settings.json） |
| 模型 | `deepseek-v4-flash`（原 `deepseek-chat` / `deepseek-reasoner` 官方已于 2026-07-24 退役，勿再用） |
| 成本 | ~¥2/百万 token（极低） |
| 状态 | ✅ 已配置、可用 |

思忆集中作为 primary LLM 配置，编程能力强。

---

### 4. Moonshot API（路由）

| 项目 | 内容 |
|------|------|
| 模型 | `moonshot-v1-8k` |
| 用途 | 中文知识、总结、通用对话路由 |
| 状态 | ✅ 已配置 |

思忆集中的路由配置：
```json
{
  "chinese_knowledge": "moonshot",
  "general_chat": "moonshot",
  "summarization": "moonshot",
  "default": "primary"
}
```

---

## 二、待部署/可用的免费方案

### 5. cto.new Proxy（GPT-5.4 免费）

| 项目 | 内容 |
|------|------|
| 端口 | `http://127.0.0.1:9091`（计划） |
| API Key | `xs2-cto-secret`（默认） |
| 协议 | OpenAI + Anthropic |
| 模型 | `gpt-5.4`（×3 配额）、`glm-5.1`（×2） |
| 成本 | $0（需批量注册免费账号） |
| 限制 | ❌ 不支持 function calling / tool use |
| 状态 | 📦 源码在 `D:\h\3.25\中转\cto-new-openai-proxy\`，未部署 |

**每账号：** ~300 次/天，GPT-5.4 按 ×3 扣 ≈ 100 次/天/账号
**多账号池：** 7 个账号 ≈ 700 次/天
**适用场景：** 纯对话、代码生成、翻译、Plan 模式
**不适用的场景：** Claude Code / agent 工具调用

**部署步骤：**
```bash
# 1. 注册账号（需 Playwright + 处理 Turnstile 风控）
pip install playwright requests
playwright install chromium
python cto_register_pw.py --start 1 --count 5

# 2. 编译 Go 反代
cd go-proxy-cto
go build -o cto-proxy .

# 3. 运行
CTO_BASE_DIR="$(pwd)" ./go-proxy-cto/cto-proxy
```

---

### 6. PhantomAPI（ChatGPT 免费版中转）

| 项目 | 内容 |
|------|------|
| 类型 | ChatGPT 免费网页版 → OpenAI API |
| 技术 | Playwright 自动化浏览器 |
| 成本 | $0 |
| 限制 | 不稳定（限频、降级、Token 过期）|
| 状态 | 📦 源码在 `D:\h\3.25\中转\PhantomAPI\`，未部署 |

---

### 7. GitHub Models（免费额度）

| 项目 | 内容 |
|------|------|
| 入口 | `https://models.inference.ai.azure.com` |
| 认证 | GitHub Token（Personal Access Token） |
| 可用模型 | GPT-4o-mini、DeepSeek、Llama 等 |
| 成本 | $0（有免费额度）|
| 状态 | ⏸ 未配置 |

---

### 8. Groq（免费）

| 项目 | 内容 |
|------|------|
| 入口 | `https://api.groq.com/openai/v1` |
| 模型 | Llama 3、Mixtral 等 |
| 特点 | 推理速度极快 |
| 成本 | $0（有免费额度）|
| 状态 | ⏸ 未配置 |

---

### 9. GitHub Copilot Proxy（VS Code Copilot 反代）

| 项目 | 内容 |
|------|------|
| 类型 | VS Code Copilot Chat token → OpenAI 兼容 API |
| 技术 | Go/Python 反代，从 VS Code 提取 GitHub OAuth token |
| 可用模型 | `claude-haiku-4.5`（免费）、`gpt-5-mini` |
| 前置条件 | GitHub Copilot 订阅或个人免费额度 |
| 成本 | $0（利用已有订阅/免费额度）|
| 限制 | ❌ 不支持 tool calling / function calling |
| 状态 | ⏸ 未部署 |

**部署方案（推荐 `copilot-gpt4-service` 类反代）：**

```bash
# 1. 提取 Copilot Token（需安装 VS Code + Copilot 插件）
# Token 位置：VS Code → Ctrl+Shift+P → "Developer: Toggle Developer Tools"
# → Application → Local Storage → github.com → copilot_token

# 2. 部署反代服务（端口 9092）
git clone https://github.com/aaamoon/copilot-gpt4-service
cd copilot-gpt4-service
# 配置 token 后启动

# 3. 启动后测试
curl http://127.0.0.1:9092/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5-mini","messages":[{"role":"user","content":"hi"}]}'
```

**适用场景：** 思忆集日常对话路由、文本生成、分类整理（不需 tool calling）
**不适用的场景：** Claude Code / agent 工具调用

**当前建议用法：** 搭好后配到思忆集路由表，低成本 Haiku 做中文日常任务
---

## 三、思忆集中 AI 架构速览

```
用户输入 → JudgeAwareClient.chat_completion()
  ├─ LocalAIRouter → Ollama 能处理 → 直接返回 ✅
  ├─ PrivacyClassifier → 检测隐私 → 强制 LOCAL_ONLY
  ├─ JudgeScheduler.route()
  │    ├─ 关键词预检 → 匹配规则 → 指定 provider
  │    ├─ local_router 安全网 → 本地能处理 → 本地
  │    ├─ LLM 评估 → route_chat(optimal_model)
  │    └─ 回退 → FallbackOpenAIClient
  └─ FallbackOpenAIClient (primary → fallback → local → cache)
```

**三层架构：**
1. **本地**（Ollama qwen3-4b）— 日常分类、整理，不出本机
2. **DeepSeek**（deepseek-v4-flash）— 主力，复杂推理和编码
3. **Moonshot**（moonshot-v1-8k）— 路由，中文类任务

---

## 四、Quick Reference

### 端口占用总表

| 端口 | 服务 | 当前状态 |
|------|------|---------|
| 11434 | Ollama | ✅ 运行中 |
| 8000 | Kiro-gateway | ✅ 运行中（多账号轮换） |
| 9091 | cto.new proxy | 📦 待部署 |
| 18765 | 思忆集后端 | ❌ 未运行 |

### 各服务商 API 兼容性

| 服务商 | OpenAI 协议 | Anthropic 协议 | 工具调用 |
|--------|:-----------:|:-------------:|:--------:|
| Kiro-gateway | ✅ | ✅ | ✅ |
| Ollama | ✅ | ❌ | ❌ |
| DeepSeek | ✅ | ❌ | ✅ |
| cto.new proxy | ✅ | ✅ | ❌ |
| PhantomAPI | ✅ | ❌ | ❌ |
| GitHub Models | ✅ | ❌ | ✅ |
| Groq | ✅ | ❌ | ✅ |

### settings.json 关键 AI 配置

```
思忆集app/data/settings.json
```

| 键 | 值 | 说明 |
|---|-----|------|
| `llm_api_base` | `https://api.deepseek.com/v1` | 主力 API |
| `llm_model` | `deepseek-v4-flash` | 主力模型（原 `deepseek-chat` 已于 2026-07-24 退役） |
| `llm_fallback_api_base` | `https://api.deepseek.com/v1` | 备用 API |
| `llm_fallback_model` | `deepseek-v4-flash` | 备用模型（原 `deepseek-chat` 已于 2026-07-24 退役） |
| `llm_local_api_base` | `http://localhost:11434/v1` | 本地 API |
| `llm_local_model` | `qwen3-4b` | 本地模型 |
| `provider_configs` | `[{"name":"DeepSeek",...}]` | 提供商列表 |
| `llm_provider_routing` | `{"chinese_knowledge":"moonshot",...}` | 路由表 |

