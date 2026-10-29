# 思忆集 — 密钥管理规范

**生效日期**：2026-06-14（PR 1 安全应急后）
**适用范围**：思忆集所有端（桌面、手机、Android、Cloudflare、browser-extension）

---

## 1. 密钥的存储位置

| 类型 | 正确位置 | 禁止位置 |
|------|---------|---------|
| 第三方 API Key（DeepSeek / 火山 / Moonshot / Tavily） | `.env` 或系统环境变量 | 源代码字符串、settings.json、config.yaml、提交历史 |
| 用户配对 Token（手机/桌面 transfer key） | 运行时生成、存放在 `data/data/transfer.json`（已 .gitignore） | 源代码、文档示例 |
| 加密用 master key | OS keychain / Windows DPAPI | 任何明文文件 |

## 2. 必须遵守的规则

### 2.1 源代码不得出现真实 Key
任何形如 `sk-`、`ark-`、`xoxb-`、`AIza`、`ghp_` 的字符串都视为真实 Key。
若出于测试目的需要 Key 字符串：

```python
# ✅ 正确：使用明显虚构的占位符
FAKE_KEY = "sk-fakefakefakefakefakefakefakefa12"

# ❌ 错误：真实 Key（即便已吊销也禁止）
KEY = "sk-fakefakefakefakefakefakefakefakefa"  # 此处仅为示意，禁止使用真实 Key
```

### 2.2 settings.json 中只允许保留**字段定义**，不存 Key 值
```json
{
  "llm_api_key": ""    // 必须留空，由环境变量在启动时填入
}
```

### 2.3 启动时必须从环境变量读取
```python
api_key = os.environ.get("VOLC_API_KEY", "").strip()
if not api_key:
    raise RuntimeError("VOLC_API_KEY environment variable is not set")
```

### 2.4 日志不得输出 Key（哪怕只有前缀）
```python
# ❌ 错误：泄露前缀大幅降低暴力破解难度
logger.info("Token: %s...", token[:8])

# ✅ 正确：仅输出哈希用于配对确认
logger.info("Token fingerprint: %s", hashlib.sha256(token.encode()).hexdigest()[:12])
```

## 3. Key 泄露应急流程

发现 Key 泄露时，按以下顺序立即处理：

1. **吊销并轮换**
   - DeepSeek：https://platform.deepseek.com → API Keys → 删除旧 Key + 创建新 Key
   - 火山引擎：https://console.volcengine.com → API Key 管理 → 禁用旧 Key + 创建新 Key
   - Moonshot：https://platform.moonshot.cn → API Keys

2. **代码清理**
   - `grep -rlE "ark-[a-f0-9]{8}|sk-[A-Za-z0-9]{20,}" .`
   - 全部替换为 `""` 或虚构占位符

3. **Git 历史检查**
   ```bash
   git log -p --all -S "PASTE_LEAKED_PREFIX_HERE"
   ```
   - 若历史中存在：
     - 未推到远端 → `git filter-repo --replace-text` 重写本地历史
     - 已推到远端 → 联系所有 fork/clone 持有者，必要时 BFG Repo-Cleaner 清理后强推

4. **CI 加固**
   - 运行 `python scripts/scan_secrets.py`（见 §5）确保仓库洁净
   - 把扫描脚本接入预提交钩子或 CI

## 4. 环境变量列表

正式生效的环境变量（详见 `.env.example`）：

| 变量 | 用途 |
|------|------|
| `SIYI_LLM_API_KEY` | 主 LLM API Key（DeepSeek） |
| `SIYI_LLM_BASE_URL` | 主 LLM Base URL |
| `SIYI_LLM_MODEL` | 主 LLM 模型名 |
| `SIYI_LLM_FALLBACK_API_KEY` | 备用 LLM Key |
| `VOLC_API_KEY` | 火山引擎 glm_proxy 专用（仅供 `glm_proxy.py` 使用） |
| `VOLC_BASE` | 火山引擎 Base URL（可选） |
| `ARK_API_KEY` | 火山引擎 ARK Key（仅供 `.scripts/generate_xiaowen_sprites.py` 桌宠动作帧生成使用） |
| `MOONSHOT_API_KEY` | Moonshot Key（路由用） |
| `DEEPSEEK_API_KEY` | DeepSeek 直连 Key（与 SIYI_LLM_API_KEY 二选一） |
| `SIYI_AUTOMATION_HUB_API_TOKEN` | Automation Hub 后端 Token |
| `SIYI_TRANSFER_KEY` | 桌面/手机互联 transfer 鉴权 Key（缺省时禁用 transfer） |

## 5. 扫描脚本

```bash
python scripts/scan_secrets.py
```

CI 调用此脚本：返回非零退出码即视为构建失败。脚本扫描以下模式（详见脚本头注释）：

- `ark-[a-f0-9]{8}-` （火山）
- `sk-[a-f0-9]{20,}` （OpenAI/DeepSeek）
- `xoxb-[0-9-]{20,}` （Slack）
- `ghp_[A-Za-z0-9]{30,}` （GitHub PAT）
- `AIza[A-Za-z0-9_-]{30,}` （Google API）

排除目录：`.git`、`.venv*`、`__pycache__`、`node_modules`、`build`、`dist`、`ollama_models`、`data/backups/`、`data_backup_*/backups/`。

## 6. 历史记录

- **2026-06-14** — PR 1 安全应急：清理仓库内 6 个文件中的真实 Key（火山 + DeepSeek），新增此规范、`scan_secrets.py`、glm_proxy 启动校验。
- **2026-09-12** — `.scripts/generate_xiaowen_sprites.py:12` 明文火山 ARK Key 外置为环境变量 `ARK_API_KEY`（缺失即报错退出）；该 Key 已随 commit `0f74fc3` 入库，按 §1 视为**已泄露 → 必须去火山控制台禁用并轮换**，旧值不迁入仓库内任何文件（由用户转存个人密码管理器）。
