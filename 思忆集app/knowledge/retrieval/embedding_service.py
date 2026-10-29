# -*- coding: utf-8 -*-
"""嵌入服务 - 使用sentence-transformers进行文本嵌入"""

import logging
from typing import Dict, List, Optional

# PERF-9：numpy 仅在 _offline_embeddings 内部使用，改为惰性导入以免模块顶层拉起
# numpy（~100-400ms）拖慢知识检索相关模块的 import 路径。

logger = logging.getLogger(__name__)


def sentence_transformers_available() -> bool:
    """七维-性C1（2026-08-29）：用 find_spec 探测可用性，绝不执行模块体。

    此处曾是模块级 import sentence_transformers → 连带加载 torch（冷启动
    实测 30.9s + 数百 MB），任何 import 本模块的路径都会被引爆（当前生产
    无人 import 本模块属埋雷）。find_spec 只读包元数据、毫秒级返回；真正的
    模型加载仍推迟到 EmbeddingService.__init__（懒加载，见下方 elif 分支）。
    """
    import importlib.util

    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        # 命名空间包残缺等边缘情形：按不可用处理（与旧 ImportError 分支等价）
        logger.debug("sentence_transformers availability probe failed — treat as unavailable")
        return False


# 预定义模型配置
MODEL_REGISTRY: Dict[str, Dict] = {
    "bge-small-zh-v1.5": {
        "name": "BAAI/bge-small-zh-v1.5",
        "dim": 512,
        "description": "中文优化，轻量本地运行（推荐）",
        "instruction": "为这个句子生成表示以用于检索相关文章：",
    },
    "bge-large-zh-v1.5": {
        "name": "BAAI/bge-large-zh-v1.5",
        "dim": 1024,
        "description": "中文优化，更高精度，资源消耗更大",
        "instruction": "为这个句子生成表示以用于检索相关文章：",
    },
    "all-MiniLM-L6-v2": {
        "name": "all-MiniLM-L6-v2",
        "dim": 384,
        "description": "通用英文优化，轻量快速",
        "instruction": None,
    },
}


class EmbeddingService:
    """嵌入服务类"""

    def __init__(
        self,
        model_name: str = "bge-small-zh-v1.5",
        offline_mode: bool = False,
        device: Optional[str] = None,
    ):
        """
        初始化嵌入服务

        Args:
            model_name: 嵌入模型名称或注册表中的键名，默认使用 bge-small-zh-v1.5
            offline_mode: 是否使用离线模式（模拟嵌入，用于测试/开发）
            device: 推理设备（如 'cpu', 'cuda'），None 为自动选择
        """
        self.offline_mode = offline_mode
        self._model_config = MODEL_REGISTRY.get(
            model_name,
            {
                "name": model_name,
                "dim": 512,
                "description": "自定义模型",
                "instruction": None,
            },
        )
        self.model_name = self._model_config["name"]
        self.embedding_dim = self._model_config["dim"]
        self._instruction = self._model_config.get("instruction")

        if offline_mode:
            logger.info("Initialized embedding service in OFFLINE mode (dim=%d, model=%s)", self.embedding_dim, self.model_name)
        elif not sentence_transformers_available():
            raise ImportError("sentence-transformers is required for embedding functionality")
        else:
            try:
                from sentence_transformers import SentenceTransformer

                self.model = SentenceTransformer(
                    self.model_name,
                    device=device,
                )
                logger.info("Initialized embedding service with model: %s (dim=%d)", self.model_name, self.embedding_dim)
            except Exception as e:
                logger.warning("Failed to load model %s, falling back to offline mode: %s", self.model_name, e)
                self.offline_mode = True

    # ── 公共接口 ──────────────────────────────────────────────────────

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        获取文本的嵌入向量

        Args:
            texts: 文本列表

        Returns:
            嵌入向量列表
        """
        if self.offline_mode:
            return self._offline_embeddings(texts, dim=self.embedding_dim)
        try:
            # BGE 模型支持通过 instruction 提升检索质量
            if self._instruction and texts:
                prefixed = [f"{self._instruction}{t}" for t in texts]
                embeddings = self.model.encode(prefixed, normalize_embeddings=True).tolist()
            else:
                embeddings = self.model.encode(texts, normalize_embeddings=True).tolist()
            logger.debug("Generated embeddings for %d texts (dim=%d)", len(texts), self.embedding_dim)
            return embeddings
        except Exception as e:
            logger.error("Failed to get embeddings: %s", e)
            raise

    def get_embedding(self, text: str) -> List[float]:
        """获取单个文本的嵌入向量"""
        embeddings = self.get_embeddings([text])
        return embeddings[0]

    # ── 离线模式（测试/开发用） ────────────────────────────────────────

    @staticmethod
    def _offline_embeddings(texts: List[str], dim: int = 512) -> List[List[float]]:
        """离线模式：为每个文本生成确定性的伪随机向量"""
        import numpy as np

        embeddings = []
        for text in texts:
            seed = hash(text) % (2**32)
            np.random.seed(seed)
            embedding = np.random.rand(dim).tolist()
            # L2 归一化，模拟 normalise_embeddings=True 的行为
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = (np.array(embedding) / norm).tolist()
            embeddings.append(embedding)
        logger.debug("Generated offline embeddings for %d texts", len(texts))
        return embeddings
