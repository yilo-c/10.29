# -*- coding: utf-8 -*-
"""文档解析器 - 支持Markdown、TXT、HTML格式"""
import logging
from pathlib import Path
from typing import List, Dict, Any
from urllib.parse import urlparse

import markdown
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class DocumentParser:
    """文档解析器类"""
    
    def __init__(self):
        self.supported_extensions = {'.md', '.txt', '.html'}
    
    def parse_file(self, file_path: Path) -> Dict[str, Any]:
        """
        解析单个文件
        
        Args:
            file_path: 文件路径
            
        Returns:
            包含内容和元数据的字典
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if file_path.suffix.lower() not in self.supported_extensions:
            raise ValueError(f"Unsupported file type: {file_path.suffix}")
        
        # 读取文件内容
        content = file_path.read_text(encoding='utf-8')
        
        # 解析内容
        if file_path.suffix.lower() == '.md':
            parsed_content = self._parse_markdown(content)
        elif file_path.suffix.lower() == '.html':
            parsed_content = self._parse_html(content)
        else:  # .txt
            parsed_content = content.strip()
        
        # 创建元数据
        metadata = {
            'source': str(file_path),
            'file_name': file_path.name,
            'file_type': file_path.suffix.lower(),
            'file_size': file_path.stat().st_size,
        }
        
        return {
            'content': parsed_content,
            'metadata': metadata
        }
    
    def parse_directory(self, directory: Path, recursive: bool = True) -> List[Dict[str, Any]]:
        """
        解析目录中的所有支持的文件
        
        Args:
            directory: 目录路径
            recursive: 是否递归子目录
            
        Returns:
            解析结果列表
        """
        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")
        
        results = []
        pattern = "**/*" if recursive else "*"
        
        for file_path in directory.glob(pattern):
            if file_path.is_file() and file_path.suffix.lower() in self.supported_extensions:
                try:
                    result = self.parse_file(file_path)
                    results.append(result)
                    logger.info(f"Parsed file: {file_path}")
                except Exception as e:
                    logger.warning(f"Failed to parse {file_path}: {e}")
                    continue
        
        logger.info(f"Parsed {len(results)} files from {directory}")
        return results
    
    def _parse_markdown(self, content: str) -> str:
        """解析Markdown内容为纯文本"""
        try:
            # 转换Markdown为HTML
            html = markdown.markdown(content)
            # 使用BeautifulSoup提取纯文本
            soup = BeautifulSoup(html, 'html.parser')
            return soup.get_text().strip()
        except Exception as e:
            logger.warning(f"Markdown parsing failed, using raw content: {e}")
            return content.strip()
    
    def _parse_html(self, content: str) -> str:
        """解析HTML内容为纯文本"""
        try:
            soup = BeautifulSoup(content, 'html.parser')
            return soup.get_text().strip()
        except Exception as e:
            logger.warning(f"HTML parsing failed, using raw content: {e}")
            return content.strip()
    
    def chunk_text(self, text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[str]:
        """
        将文本分块（基于字符数）

        Args:
            text: 输入文本
            chunk_size: 块大小（字符数）
            chunk_overlap: 块重叠大小

        Returns:
            文本块列表
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")

        chunks = []
        start = 0
        text_length = len(text)

        while start < text_length:
            end = min(start + chunk_size, text_length)
            chunk = text[start:end]

            # 如果不是最后一块，尝试在段落/句子边界分割
            if end < text_length:
                # 优先在段落边界分割
                para_end = chunk.rfind('\n\n')
                if para_end > chunk_size - chunk_overlap and para_end > 0:
                    end = start + para_end + 2
                    chunk = text[start:end]
                else:
                    # 其次在句子边界分割（中文句号、问号、感叹号、英文句号等）
                    sentence_end = max(
                        chunk.rfind('。'),
                        chunk.rfind('？'),
                        chunk.rfind('！'),
                        chunk.rfind('.'),
                        chunk.rfind('?'),
                        chunk.rfind('!')
                    )
                    if sentence_end > chunk_size - chunk_overlap and sentence_end > 0:
                        end = start + sentence_end + 1
                        chunk = text[start:end]

            chunks.append(chunk.strip())
            # 确保start总是向前移动，避免无限循环
            new_start = end - chunk_overlap if chunk_overlap > 0 else end
            if new_start <= start:
                new_start = end  # 如果重叠导致不前进，则强制前进
            start = new_start

        # 过滤空块
        chunks = [chunk for chunk in chunks if chunk]
        logger.debug(f"Text split into {len(chunks)} chunks")
        return chunks

    # ── 基于 Token 的分块（设计规范：512 tokens / 64 overlap） ──────────

    # 中文 token 比例：约 1 token = 1.5~2 个中文字符
    _CHINESE_CHARS_PER_TOKEN = 1.6

    @classmethod
    def estimate_token_count(cls, text: str) -> int:
        """
        估算文本的 token 数量

        CJK 字符按 ~1.6 chars/token，ASCII 按 ~3.5 chars/token 估算，
        近似 OpenAI / 多数 embedding 模型的 tokenizer 行为。
        """
        if not text:
            return 0
        cjk = sum(1 for ch in text if '一' <= ch <= '鿿' or '　' <= ch <= '〿')
        ascii_chars = len(text) - cjk
        return max(1, int(cjk / cls._CHINESE_CHARS_PER_TOKEN + ascii_chars / 3.5))

    def chunk_by_tokens(
        self,
        text: str,
        chunk_tokens: int = 512,
        overlap_tokens: int = 64
    ) -> List[str]:
        """
        基于 token 估算值分块，优先在段落/句子边界分割。

        设计规范中的 chunk_size=512 tokens / overlap=64 tokens 对应：
        - 中文约 800 字符 / 100 字符重叠
        - 按段落边界 > 句子边界 > 固定长度 的优先级分割

        Args:
            text: 输入文本
            chunk_tokens: 目标 token 数（默认 512）
            overlap_tokens: 块间重叠 token 数（默认 64）

        Returns:
            文本块列表
        """
        if chunk_tokens <= 0:
            raise ValueError("chunk_tokens must be positive")
        if overlap_tokens >= chunk_tokens:
            raise ValueError("overlap_tokens must be less than chunk_tokens")

        # 换算为字符数
        chunk_chars = max(1, int(chunk_tokens * self._CHINESE_CHARS_PER_TOKEN))
        overlap_chars = max(1, int(overlap_tokens * self._CHINESE_CHARS_PER_TOKEN))

        # 直接复用字符分块逻辑，但使用设计规范的比例
        raw_chunks = self.chunk_text(text, chunk_size=chunk_chars, chunk_overlap=overlap_chars)

        # 对过长的块（超出目标 token 数太多）做二次分割
        refined_chunks = []
        for chunk in raw_chunks:
            est_tokens = self.estimate_token_count(chunk)
            if est_tokens <= chunk_tokens * 1.5:
                refined_chunks.append(chunk)
            else:
                # 超长块按更小的窗口二次分割
                sub_chunks = self.chunk_text(
                    chunk,
                    chunk_size=chunk_chars,
                    chunk_overlap=overlap_chars
                )
                refined_chunks.extend(sub_chunks)

        logger.debug(
            "Token-based chunking: input=%d chars → %d chunks (target=%d tokens, overlap=%d tokens)",
            len(text), len(refined_chunks), chunk_tokens, overlap_tokens
        )
        return refined_chunks