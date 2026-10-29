"""Centralized default model constants — single source of truth.

All other modules should import from here instead of hardcoding model names.
"""

# ── Cloud providers ──

DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_KIRO_MODEL = "claude-sonnet-4.5"

# ── Local models (Ollama) ──
# qwen2.5:3b 比 qwen2.5-7b 更适合本地使用:
#   1.9GB vs 4.7GB → 启动快 3脳、推理快 5脳
#   支持 tools 功能调用（qwen2.5-7b 不支持）
#   3B 参数对日常对话/笔记整理已足够
DEFAULT_LOCAL_MODEL = "qwen2.5:3b"
DEFAULT_LOCAL_CODER_MODEL = "qwen2.5-coder:3b"

# ── Fallback defaults ──

DEFAULT_CLOUD_MODEL = DEFAULT_DEEPSEEK_MODEL
DEFAULT_CLOUD_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"

# ── Timeouts ──

DEFAULT_CLOUD_TIMEOUT = 300
DEFAULT_LOCAL_TIMEOUT = 60
