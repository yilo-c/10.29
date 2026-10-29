"""populate_decision_knowledge.py — 方案知识库初始化脚本.

根据 complete-architecture-design.md 搂4.5 和 搂8.2 设计：
填充 data/knowledge_base/ 下的 tools/, configuration/, best_practices/, decisions/ 目录，
为 TaskOptimizer/PathComparator 提供初始方案知识。

运行方式:
    python -m思忆集app.populate_decision_knowledge [--dir DATA_DIR] [--force]

知识库布局:
    data/knowledge_base/
    ├── tools/           工具/服务方案库（定价、评分、适用场景）
    ├── configuration/   配置指南（API、框架、工具配置）
    ├── best_practices/  最佳实践（通用模式、经验总结）
    └── decisions/       个人决策经验（运行后自动积累，初始为空占位）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("populate_decision_knowledge")


# ── Knowledge entries ──────────────────────────────────────────────────

# Tools & services with pricing, ratings, and use-case mapping
# Format: content as .md files in tools/
TOOLS_KNOWLEDGE: dict[str, str] = {
    "AI模型选型对比.md": """# AI 模型选型对比

| 模型 | 类型 | 单次成本 | 智能 | 推荐场景 | 适用平台 |
|------|------|---------|------|---------|---------|
| DeepSeek V4 Pro | 云端 | ~$0.001 | ★★★★ | 首选均衡 | Python, API |
| DeepSeek Flash | 云端 | ~$0.0005 | ★★★★ | 轻量复杂 | Python, API |
| Claude Sonnet 4.6 | 云端 | ~$0.01 | ★★★★★ | 最强编码 | Python, CLI |
| GPT-4.1 | 云端 | ~$0.01 | ★★★★★ | 最强分析 | Python, API |
| Qwen2.5:7B | 本地 | $0 | ★★★ | 日常对话 | Ollama |
| Qwen2.5:1.5B | 本地 | $0 | ★★ | 简单回答 | Ollama |

**选型建议：**
- 日常问答 → 本地 Qwen2.5:7B ($0)
- 中等分析 → DeepSeek V4 Pro (~$0.001)
- 复杂编码 → Claude Sonnet 4.6 (~$0.01)
- 预算紧张 → DeepSeek Flash (~$0.0005)
""",
    "向量数据库对比.md": """# 向量数据库对比

| 方案 | 部署 | 维度支持 | 性能 | 适用场景 |
|------|------|---------|------|---------|
| ChromaDB | 嵌入式 | 任意 | 中 | 个人知识库、小规模RAG |
| Qdrant | Docker | 任意 | 高 | 中大规模生产 |
| Milvus | K8s | 任意 | 极高 | 企业级 |
| pgvector | PostgreSQL插件 | 2000 | 中 | 已有PostgreSQL场景 |

**选型建议：**
- 个人项目 → ChromaDB（最简单，已集成）
- 已有 Postgres → pgvector（避免额外运维）
- 生产规模 → Qdrant（性能与运维平衡）
""",
    "任务队列对比.md": """# 任务队列对比

| 方案 | 架构 | 持久化 | 延迟 | 适用场景 |
|------|------|--------|------|---------|
| RQ | Redis | 是 | 低 | 轻量任务，Python原生 |
| Celery | Broker+RabbitMQ/Redis | 是 | 低 | 生产级任务 |
| APScheduler | 进程内 | 否 | 中 | 定时任务 |
| WorkManager | Android框架 | 是 | 中 | Android后台 |

**选型建议：**
- Python轻量 → RQ（已集成）
- 生产复杂编排 → Celery
- 定时简单 → APScheduler
""",
    "OCR方案对比.md": """# OCR 方案对比

| 方案 | 准确率 | 中文支持 | 速度 | 成本 | 适用场景 |
|------|--------|---------|------|------|---------|
| PaddleOCR | ★★★★★ | 优 | 快 | 本地免费 | 通用OCR |
| Tesseract 5 | ★★★ | 中 | 中 | 本地免费 | 文档扫描 |
| EasyOCR | ★★★★ | 良 | 中 | 本地免费 | 多语言 |
| Azure OCR | ★★★★★ | 优 | 快 | 按量付费 | 云端高精度 |

**选型建议：**
- 本地中文最优 → PaddleOCR（推荐）
- 快速集成 → Tesseract（系统包即可）
- 云端高质量 → Azure OCR（其他业务已在Azure时）
""",
}

# Configuration guides
CONFIG_KNOWLEDGE: dict[str, str] = {
    "Ollama配置指南.md": """# Ollama 配置指南

## 安装
```bash
# Windows (PowerShell)
winget install Ollama.Ollama

# Linux / macOS
curl -fsSL https://ollama.com/install.sh | sh
```

## 推荐模型
```bash
ollama pull qwen2.5:7b    # 默认本地模型
ollama pull qwen2.5:1.5b  # 轻量替代
ollama pull nomic-embed-text  # 本地 Embedding
```

## 配置建议
- `OLLAMA_HOST=127.0.0.1` 限制本地访问（安全）
- `OLLAMA_KEEP_ALIVE=24h` 保持模型加载
- 建议至少 8GB 显存或 16GB 系统内存
""",
    "ChromaDB配置指南.md": """# ChromaDB 配置指南

## 集成方式
ChromaDB 已包含在 requirements-desktop.txt 中。

## 数据路径
默认数据存储在 `{workspace}/data/chromadb/`。

## 配置建议
- 小型个人项目使用嵌入式模式（默认）
- 持久化路径建议纳入同步目录
- 集合按用途分割：knowledge_base, memories, code_index
""",
}

# Best practices
BEST_PRACTICES_KNOWLEDGE: dict[str, str] = {
    "RAG实现最佳实践.md": """# RAG 实现最佳实践

## 分段策略
- chunk_size: 512 tokens（中文~800字）
- chunk_overlap: 64 tokens
- 优先按段落边界分段 > 按句子边界 > 按固定长度

## 检索策略
- 混合检索（向量+BM25）优于纯向量
- top_k 建议 5-10，配合重排序
- 命中 chunk 带上下一各 1 个 chunk 保留上下文

## Embedding 选型
- 中文优先 BAAI/bge-small-zh-v1.5（轻量）
- 精度要求高用 bge-large-zh-v1.5
- 弱网环境本地可用 chromadb 内置 all-MiniLM-L6-v2
""",
    "桌面端代码组织原则.md": """# 桌面端代码组织原则

## 文件大小红线
- 单个 .py 文件不超过 800 行
- 超过 500 行就考虑拆分
- 超过 800 行必须拆分

## 拆分策略
- 按职责拆分：UI / 业务逻辑 / 数据访问
- Mixin 模式用于大型 UI 类
- 功能相关的小文件放在同一目录

## 命名惯例
- UI 文件: ui_xxx.py
- 助手模块: assistant_xxx.py
- 核心服务: core/xxx.py
- 测试: tests/test_xxx.py
""",
    "异步数据处理模式.md": """# 异步数据处理模式

## 适用场景
- AI API 调用（I/O 密集型）
- WebSocket 消息处理
- 多步骤代理链
- 并发子任务

## 推荐工具
- asyncio + async/await（核心）
- asyncio.subprocess（外部进程调用）
- asyncio.Queue（任务队列）
- asyncio.gather（并行等待）

## 注意事项
- PyQt6 事件循环与 asyncio 不兼容时用 QThread
- subagent_service.py 内部已正确处理异步
- 同步调用耗时操作时用 run_in_executor
""",
}

# Decisions directory placeholder
DECISIONS_PLACEHOLDER = """# 个人决策经验

此目录保存运行中自动积累的个人决策经验。
每条决策记录格式：

```json
{
  "id": "uuid",
  "date": "2026-05-01",
  "context": "决策背景描述",
  "options": ["方案A", "方案B"],
  "selected": "方案A",
  "reason": "选择原因",
  "outcome": "实际效果",
  "dimensions": {
    "cost": {"评价": "满意"},
    "quality": {"评价": "良好"}
  }
}
```

初始为空，运行 populate_decision_knowledge.py --decisions 后填充示例数据。
"""


# ── Main ────────────────────────────────────────────────────────────────


def populate(
    base_dir: str | Path,
    *,
    force: bool = False,
    include_decisions: bool = False,
) -> dict[str, int]:
    """Populate knowledge base directories with initial data.

    Args:
        base_dir: Root data directory (e.g., data/knowledge_base/).
        force: Overwrite existing files.
        include_decisions: Also create initial decision records.

    Returns:
        Dict of category → file count created.
    """
    base = Path(base_dir).resolve()
    counts: dict[str, int] = {}

    sections = [
        ("tools", TOOLS_KNOWLEDGE),
        ("configuration", CONFIG_KNOWLEDGE),
        ("best_practices", BEST_PRACTICES_KNOWLEDGE),
    ]

    for section_name, entries in sections:
        section_dir = base / section_name
        section_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for filename, content in entries.items():
            dest = section_dir / filename
            if dest.exists() and not force:
                logger.info("  SKIP (exists): %s", dest.relative_to(base))
                continue
            dest.write_text(content.strip() + "\n", encoding="utf-8")
            logger.info("  WRITE: %s (%d bytes)", dest.relative_to(base), len(content))
            written += 1
        counts[section_name] = written

    # Decisions
    dec_dir = base / "decisions"
    dec_dir.mkdir(parents=True, exist_ok=True)
    readme = dec_dir / "README.md"
    if not readme.exists() or force:
        readme.write_text(DECISIONS_PLACEHOLDER.strip() + "\n", encoding="utf-8")
        logger.info("  WRITE: %s", readme.relative_to(base))
        counts["decisions"] = 1

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate decision knowledge base")
    parser.add_argument(
        "--dir",
        default="data/knowledge_base",
        help="Knowledge base root directory (default: data/knowledge_base)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    base = Path(args.dir).resolve()
    logger.info("Populating knowledge base at: %s", base)

    counts = populate(base, force=args.force)

    total = sum(counts.values())
    logger.info("Done. %d files written across %d categories.", total, len(counts))
    for section, count in counts.items():
        logger.info("  %s: %d files", section, count)


if __name__ == "__main__":
    main()
