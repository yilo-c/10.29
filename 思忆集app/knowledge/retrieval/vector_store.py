# -*- coding: utf-8 -*-
"""ChromaDB向量存储封装"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

if TYPE_CHECKING:
    from chromadb.api.types import Documents, EmbeddingFunction, IDs, Metadatas

logger = logging.getLogger(__name__)

class ChromaVectorStore:
    """ChromaDB向量存储封装类"""
    
    def __init__(
        self,
        persist_directory: Path,
        collection_name: str = "knowledge_base",
        embedding_function: Optional[EmbeddingFunction] = None
    ):
        """
        初始化Chroma向量存储
        
        Args:
            persist_directory: 持久化目录路径
            collection_name: 集合名称
            embedding_function: 嵌入函数，如果为None则使用默认的all-MiniLM-L6-v2
        """
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self.embedding_function = embedding_function
        
        # 确保持久化目录存在
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        
        # 初始化Chroma客户端（PERF-9：chromadb 惰性导入，仅在实例化时加载，
        # 避免模块顶层拉起 chromadb 及其重型依赖拖慢知识库桥接的导入路径）
        import chromadb

        self.client = chromadb.PersistentClient(
            path=str(self.persist_directory)
        )
        
        # 获取或创建集合
        try:
            self.collection = self.client.get_collection(
                name=self.collection_name,
                embedding_function=self.embedding_function
            )
        except Exception:
            # 集合不存在，创建新集合
            # 注意：ChromaDB的NotFoundError可能不是标准异常，所以捕获所有异常
            logger.info(f"Collection '{self.collection_name}' not found, creating new collection")
            self.collection = self.client.create_collection(
                name=self.collection_name,
                embedding_function=self.embedding_function
            )
    
    def add_documents(
        self,
        documents: Documents,
        metadatas: Optional[Metadatas] = None,
        ids: Optional[IDs] = None
    ) -> None:
        """
        添加文档到向量存储
        
        Args:
            documents: 文档列表
            metadatas: 元数据列表（可选）
            ids: 文档ID列表（可选）
        """
        try:
            self.collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids  # type: ignore[arg-type]  # ChromaDB stub: add ids 不含 None，运行时可选
            )
            logger.info(f"Added {len(documents)} documents to collection '{self.collection_name}'")
        except Exception as e:
            logger.error(f"Failed to add documents: {e}")
            raise
    
    def query(
        self,
        query_texts: List[str],
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
        where_document: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        查询相似文档
        
        Args:
            query_texts: 查询文本列表
            n_results: 返回结果数量
            where: 元数据过滤条件
            where_document: 文档内容过滤条件
            
        Returns:
            查询结果字典
        """
        try:
            results = self.collection.query(
                query_texts=query_texts,
                n_results=n_results,
                where=where,
                where_document=where_document  # type: ignore[arg-type]
            )
            logger.debug(f"Query returned {len(results['documents'] or [])} results")
            return cast("Dict[str, Any]", results)
        except Exception as e:
            logger.error(f"Query failed: {e}")
            raise
    
    def delete(
        self,
        ids: Optional[IDs] = None,
        where: Optional[Dict[str, Any]] = None,
        where_document: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        删除文档
        
        Args:
            ids: 要删除的文档ID列表
            where: 元数据过滤条件
            where_document: 文档内容过滤条件
        """
        try:
            self.collection.delete(
                ids=ids,
                where=where,
                where_document=where_document  # type: ignore[arg-type]
            )
            logger.info("Documents deleted successfully")
        except Exception as e:
            logger.error(f"Delete failed: {e}")
            raise
    
    def get_all_documents(self) -> Dict[str, Any]:
        """获取所有文档"""
        try:
            results = self.collection.get()
            logger.debug(f"Retrieved {len(results['documents'] or [])} total documents")
            return cast("Dict[str, Any]", results)
        except Exception as e:
            logger.error(f"Get all documents failed: {e}")
            raise

    def close(self) -> None:
        """释放 ChromaDB 资源，关闭持久化连接。"""
        try:
            if hasattr(self, 'client') and self.client is not None:
                self.client.close()  # type: ignore[attr-defined]  # PersistentClient 运行时含 close
        except Exception as e:
            logger.warning(f"Error closing ChromaDB client: {e}")