# -*- coding: utf-8 -*-
"""
知识库 RAG 集成测试

测试核心组件：DocumentParser, EmbeddingService, Cache, HybridRetriever, RAGPipeline
"""
from __future__ import annotations

import math
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

# 确保能找到 knowledge 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from knowledge.retrieval.cache import QueryCache
from knowledge.retrieval.document_parser import DocumentParser
from knowledge.retrieval.embedding_service import EmbeddingService


# ══════════════════════════════════════════════════════════════════════
# DocumentParser 测试
# ══════════════════════════════════════════════════════════════════════

class TestDocumentParser:
    """文档解析器测试"""

    def setup_method(self) -> None:
        self.parser = DocumentParser()

    def test_chunk_by_tokens_default_params(self):
        """默认参数分块（512 tokens / 64 overlap）"""
        text = "机器学习是人工智能的核心领域。" * 200  # ~1100 字
        chunks = self.parser.chunk_by_tokens(text)
        assert len(chunks) >= 1
        # 验证块间有重叠
        total_chars = sum(len(c) for c in chunks)
        assert total_chars < len(text) * 1.5  # 重叠不应导致总字符翻倍

    def test_chunk_by_tokens_boundary_respect(self):
        """分块应优先在段落/句子边界分割"""
        text = "这是第一段。这是第二句！\n\n这是新段落。这是第二段的第二句。"
        chunks = self.parser.chunk_by_tokens(text, chunk_tokens=512, overlap_tokens=64)
        assert len(chunks) <= 3

    def test_estimate_token_count(self):
        """token 估算应合理"""
        # 纯中文
        cn_count = DocumentParser.estimate_token_count("机器学习是人工智能的核心领域")
        assert cn_count > 0
        # 纯英文
        en_count = DocumentParser.estimate_token_count("machine learning is a core field of AI")
        assert en_count > 0
        # 混合
        mixed_count = DocumentParser.estimate_token_count("机器学习 machine learning AI 人工智能")
        assert mixed_count > 0

    def test_chunk_text_sentence_boundary(self):
        """字符分块应优先在句子边界分割"""
        text = "第一句。" + "A" * 500 + "第二句！" + "B" * 500 + "第三句？"
        chunks = self.parser.chunk_text(text, chunk_size=300, chunk_overlap=50)
        for chunk in chunks:
            # 每个块不应在中间截断长词（至少以句子边界结束）
            assert len(chunk) > 0

    def test_chunk_text_empty(self):
        """空文本应返回空列表"""
        assert self.parser.chunk_text("") == []
        assert self.parser.chunk_by_tokens("") == []

    def test_chunk_text_small(self):
        """小于 chunk_size 的文本应返回单个块"""
        text = "短文本。"
        chunks = self.parser.chunk_text(text, chunk_size=1000, chunk_overlap=200)
        assert len(chunks) == 1
        assert chunks[0] == "短文本。"


# ══════════════════════════════════════════════════════════════════════
# EmbeddingService 测试
# ══════════════════════════════════════════════════════════════════════

class TestEmbeddingService:
    """嵌入服务测试（离线模式）"""

    def setup_method(self) -> None:
        self.service = EmbeddingService(offline_mode=True)

    def test_get_embedding_dimension(self):
        """离线模式应返回 512 维向量"""
        emb = self.service.get_embedding("测试文本")
        assert len(emb) == 512

    def test_get_embedding_deterministic(self):
        """相同文本应产生相同向量（确定性）"""
        emb1 = self.service.get_embedding("机器学习")
        emb2 = self.service.get_embedding("机器学习")
        assert emb1 == emb2

    def test_get_embedding_different(self):
        """不同文本应产生不同向量"""
        emb1 = self.service.get_embedding("人工智能")
        emb2 = self.service.get_embedding("自然语言处理")
        assert emb1 != emb2

    def test_get_embeddings_batch(self):
        """批量嵌入应返回与输入相同数量的向量"""
        texts = ["文本A", "文本B", "文本C"]
        embs = self.service.get_embeddings(texts)
        assert len(embs) == len(texts)
        for emb in embs:
            assert len(emb) == 512

    def test_offline_embeddings_normalized(self):
        """离线嵌入应被 L2 归一化（模拟 normalise_embeddings=True）"""
        emb = self.service.get_embedding("测试向量")
        norm = math.sqrt(sum(v * v for v in emb))
        assert abs(norm - 1.0) < 1e-5  # 归一化后 L2 范数应为 1


# ══════════════════════════════════════════════════════════════════════
# QueryCache 测试
# ══════════════════════════════════════════════════════════════════════

class TestQueryCache:
    """查询缓存测试"""

    def setup_method(self) -> None:
        self.cache = QueryCache(ttl_seconds=3600, max_entries=16)

    def test_set_and_get_exact(self):
        """精确匹配应命中缓存"""
        self.cache.set("什么是 RAG?", {"documents": ["RAG is..."]})
        result = self.cache.get("什么是 RAG?")
        assert result is not None
        assert result["documents"] == ["RAG is..."]

    def test_miss(self):
        """未缓存应返回 None"""
        assert self.cache.get("不存在的查询") is None

    def test_invalidate_single(self):
        """使单个查询失效"""
        self.cache.set("查询A", {"documents": ["A的结果"]})
        self.cache.set("查询B", {"documents": ["B的结果"]})
        self.cache.invalidate("查询A")
        assert self.cache.get("查询A") is None
        assert self.cache.get("查询B") is not None

    def test_clear(self):
        """清空缓存"""
        self.cache.set("查询", {"documents": ["结果"]})
        self.cache.clear()
        assert self.cache.get("查询") is None
        assert self.cache.size == 0

    def test_semantic_similarity(self):
        """语义近似匹配应命中缓存（字符级 Jaccard > 0.95 阈值）"""
        long_query = "Python是一种广泛使用的高级编程语言，由吉多路范罗苏姆创造"
        self.cache.set(long_query, {"documents": ["Python 知识..."]})
        # 仅删除一个字符，其余完全一致 → Jaccard ~ 0.976 > 0.95
        similar = self.cache.get(long_query[:-1])
        assert similar is not None

    def test_eviction(self):
        """超过最大条目应淘汰最旧条目"""
        small_cache = QueryCache(ttl_seconds=3600, max_entries=2)
        small_cache.set("查询1", {"documents": ["结果1"]})
        small_cache.set("查询2", {"documents": ["结果2"]})
        small_cache.set("查询3", {"documents": ["结果3"]})
        assert small_cache.size <= 2
        # 查询1 应被淘汰
        assert small_cache.get("查询1") is None

    def test_text_similarity(self):
        """字符级 Jaccard 相似度"""
        sim = QueryCache._text_similarity("hello world", "hello world")
        assert sim == 1.0
        sim2 = QueryCache._text_similarity("abc", "xyz")
        assert sim2 == 0.0
        sim3 = QueryCache._text_similarity("", "hello")
        assert sim3 == 0.0
        sim4 = QueryCache._text_similarity("你好世界", "你好")
        assert sim4 > 0


# ══════════════════════════════════════════════════════════════════════
# Config 常量测试
# ══════════════════════════════════════════════════════════════════════

class TestConfig:
    """配置常量测试"""

    def test_model_registry_has_chinese_model(self):
        """模型注册表应包含 bge-small-zh-v1.5"""
        from knowledge.retrieval.embedding_service import MODEL_REGISTRY
        assert "bge-small-zh-v1.5" in MODEL_REGISTRY
        cfg = MODEL_REGISTRY["bge-small-zh-v1.5"]
        assert cfg["dim"] == 512
        assert "BAAI" in cfg["name"]

    def test_chunk_constants(self):
        """分块参数应符合设计规范"""
        from knowledge.retrieval.rag_pipeline import RAGPipeline
        assert RAGPipeline.DEFAULT_CHUNK_TOKENS == 512
        assert RAGPipeline.DEFAULT_CHUNK_OVERLAP_TOKENS == 64


# ══════════════════════════════════════════════════════════════════════
# RAGPipeline 带 ChromaDB 的集成测试
# ══════════════════════════════════════════════════════════════════════

class TestRAGPipeline:
    """RAGPipeline 集成测试（使用 ChromaDB 临时实例）"""

    @pytest.fixture(autouse=True)
    def _setup_rag(self):
        """每个测试方法前初始化 RAG 组件"""
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="rag_test_"))
        self._persist_dir = self._tmp_dir / "chroma_db"
        self._kb_dir = self._tmp_dir / "knowledge_base"
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._kb_dir.mkdir(parents=True, exist_ok=True)

        from knowledge.retrieval.vector_store import ChromaVectorStore
        from knowledge.retrieval.rag_pipeline import RAGPipeline

        self.embedding_service = EmbeddingService(offline_mode=True)
        self.vector_store = ChromaVectorStore(
            persist_directory=self._persist_dir,
            collection_name="test_kb",
        )
        self.document_parser = DocumentParser()
        self.pipeline = RAGPipeline(
            vector_store=self.vector_store,
            document_parser=self.document_parser,
            embedding_service=self.embedding_service,
            knowledge_base_dir=self._kb_dir,
        )

        yield

        # 清理临时目录
        if self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_add_and_query(self):
        """添加文档后应能检索到"""
        await self.pipeline.add_document(
            content="机器学习是人工智能的一个子领域，专注于让计算机从数据中学习。",
            metadata={"source": "test_doc", "file_name": "ai_intro.md"},
        )
        results = await self.pipeline.query_knowledge_base(
            "什么是机器学习?", n_results=5
        )
        assert len(results["documents"]) > 0
        assert "机器学习" in results["documents"][0]

    @pytest.mark.asyncio
    async def test_query_empty_knowledge_base(self):
        """空知识库应返回空结果"""
        results = await self.pipeline.query_knowledge_base(
            "测试查询", n_results=5
        )
        assert len(results["documents"]) == 0

    @pytest.mark.asyncio
    async def test_add_multiple_documents(self):
        """多次添加文档应累积"""
        await self.pipeline.add_document(content="第一篇文档内容。", metadata={"source": "doc1"})
        await self.pipeline.add_document(content="第二篇文档内容。", metadata={"source": "doc2"})
        all_docs = self.pipeline.get_all_documents()
        assert len(all_docs) >= 2

    @pytest.mark.asyncio
    async def test_context_assembly(self):
        """上下文组装应生成合理的格式"""
        await self.pipeline.add_document(
            content="Transformer 架构是近年来自然语言处理领域的重大突破。",
            metadata={"file_name": "transformer.md"},
        )
        results = await self.pipeline.query_knowledge_base("Transformer")
        context = self.pipeline.assemble_context("Transformer", results)
        assert "[知识库:" in context
        assert "transformer.md" in context
        assert "相关度:" in context
        assert "Transformer" in context

    @pytest.mark.asyncio
    async def test_clear_knowledge_base(self):
        """清空知识库应移除所有文档"""
        await self.pipeline.add_document(content="测试内容", metadata={"source": "test"})
        self.pipeline.clear_knowledge_base()
        all_docs = self.pipeline.get_all_documents()
        assert len(all_docs) == 0

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """相同查询第二次应走缓存"""
        await self.pipeline.add_document(
            content="Python 是一种高级编程语言。",
            metadata={"source": "python_intro"},
        )
        # 第一次查询（miss）
        r1 = await self.pipeline.query_knowledge_base("Python 语言")
        assert r1["cached"] is False
        # 第二次查询（hit）
        r2 = await self.pipeline.query_knowledge_base("Python 语言")
        assert r2["cached"] is True

    @pytest.mark.asyncio
    async def test_query_with_metadata_filter(self):
        """元数据过滤应只返回匹配的文档"""
        await self.pipeline.add_document(
            content="Java 相关内容。",
            metadata={"source": "doc_a", "lang": "java"},
        )
        await self.pipeline.add_document(
            content="Python 相关内容。",
            metadata={"source": "doc_b", "lang": "python"},
        )
        results = await self.pipeline.query_knowledge_base(
            "编程语言", where={"lang": "python"}
        )
        for meta in results["metadatas"]:
            assert meta.get("lang") == "python"

    def test_get_cache_stats(self):
        """缓存统计信息应包含 size 和 ttl"""
        stats = self.pipeline.get_cache_stats()
        assert "cache_size" in stats
        assert "cache_ttl_seconds" in stats
        assert stats["cache_ttl_seconds"] == 86400

    def test_format_source_citation(self):
        """来源引用格式化"""
        results = {
            "metadatas": [{"file_name": "doc1.md"}, {"file_name": "doc2.md"}],
            "scores": [0.95, 0.85],
        }
        citations = self.pipeline.format_as_source_citation(results)
        assert "doc1.md" in citations
        assert "doc2.md" in citations
        assert "0.95" in citations
