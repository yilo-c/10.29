"""Knowledge Base / RAG API routes.

Provides document management and semantic retrieval for the knowledge base.
In-memory storage — TODO: replace with database + vector search engine
(e.g. ChromaDB + sentence-transformers, referencing desktop-side
思忆集app/knowledge_base/ modules).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any
import logging
import uuid
import time

from automation_hub.api.auth.deps import require_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


# ── In-memory storage ──────────────────────────────────────────────

_DOCUMENTS: dict[str, dict[str, Any]] = {}
_VECTOR_INDEX_DIRTY = True


# ── Models ─────────────────────────────────────────────────────────

class DocumentCreate(BaseModel):
    """Request body for adding a document."""

    title: str = Field(min_length=1, max_length=500, description="文档标题")
    content: str = Field(min_length=1, description="文档内容")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    source: str | None = Field(default=None, description="来源标识")


class DocumentItem(BaseModel):
    """Document info for list/detail responses."""

    id: str
    title: str
    tags: list[str] = Field(default_factory=list)
    source: str | None = None
    content_preview: str = Field(description="内容预览（前200字）")
    chunk_count: int = Field(default=0, description="分块数")
    created_at: float = Field(description="创建时间戳")
    updated_at: float = Field(description="更新时间戳")


class QueryRequest(BaseModel):
    """Request body for knowledge base retrieval."""

    query: str = Field(min_length=1, max_length=2000, description="查询文本")
    top_k: int = Field(default=5, ge=1, le=50, description="返回结果数")
    threshold: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="相似度阈值（0=不过滤, 仅内存模式使用简单文本匹配）",
    )


class QueryResultItem(BaseModel):
    """A single retrieval result."""

    document_id: str
    title: str
    content: str
    score: float = Field(description="相关度分数 (0~1)")
    tags: list[str] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """Response for knowledge base retrieval."""

    results: list[QueryResultItem]
    query: str
    total: int


class KnowledgeBaseStatus(BaseModel):
    """Knowledge base overall status."""

    total_documents: int
    total_chunks: int
    vector_index_ready: bool


# ── Helpers ────────────────────────────────────────────────────────


def _get_doc_or_404(doc_id: str) -> dict[str, Any]:
    doc = _DOCUMENTS.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"document not found: {doc_id}")
    return doc


def _chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Simple character-level text chunking (placeholder for token-based chunking)."""
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            # try to break at a sentence boundary
            boundary = max(
                text.rfind("。", start, end),
                text.rfind("？", start, end),
                text.rfind("！", start, end),
                text.rfind("\n\n", start, end),
            )
            if boundary > start + chunk_size // 2:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        start = end - overlap
        if start >= end:
            start = end
    return [c for c in chunks if c]


def _simple_rank(
    query: str,
    docs: list[dict[str, Any]],
    top_k: int,
    threshold: float,
) -> list[QueryResultItem]:
    """Simple keyword-overlap scoring (placeholder for vector + BM25 hybrid)."""
    query_lower = query.lower()
    query_terms = set(query_lower.split())

    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in docs:
        content_lower = (doc.get("content") or "").lower()
        title_lower = (doc.get("title") or "").lower()

        # term overlap ratio on content
        content_terms = set(content_lower.split())
        overlap = len(query_terms & content_terms) / max(len(query_terms), 1)

        # title match bonus
        title_bonus = 0.3 if any(t in title_lower for t in query_terms) else 0.0

        # exact phrase bonus
        phrase_bonus = 0.2 if query_lower in content_lower else 0.0

        score = min(overlap + title_bonus + phrase_bonus, 1.0)
        if score >= threshold:
            scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    results: list[QueryResultItem] = []
    for score, doc in top:
        chunks = doc.get("_chunks") or [doc.get("content", "")]
        best_chunk = chunks[0] if chunks else ""
        # pick the chunk with highest overlap
        for ch in chunks:
            if query_lower in ch.lower():
                best_chunk = ch
                break

        results.append(QueryResultItem(
            document_id=doc["id"],
            title=doc.get("title", ""),
            content=best_chunk,
            score=round(score, 4),
            tags=list(doc.get("tags", [])),
        ))

    return results


# ── Endpoints ──────────────────────────────────────────────────────


@router.get("/documents", response_model=list[DocumentItem])
async def list_documents(
    token=Depends(require_scope("knowledge-base:use")),
):
    """List all documents in the knowledge base."""
    try:
        items: list[DocumentItem] = []
        for doc_id, doc in _DOCUMENTS.items():
            content = doc.get("content", "")
            chunks = doc.get("_chunks") or _chunk_text(content)
            items.append(DocumentItem(
                id=doc_id,
                title=doc.get("title", ""),
                tags=list(doc.get("tags", [])),
                source=doc.get("source"),
                content_preview=content[:200],
                chunk_count=len(chunks),
                created_at=doc.get("created_at", 0.0),
                updated_at=doc.get("updated_at", 0.0),
            ))
        return items
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list documents failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/documents", response_model=DocumentItem, status_code=201)
async def add_document(
    body: DocumentCreate,
    token=Depends(require_scope("knowledge-base:use")),
):
    """Add a new document to the knowledge base."""
    try:
        doc_id = str(uuid.uuid4())
        now = time.time()
        chunks = _chunk_text(body.content)

        doc: dict[str, Any] = {
            "id": doc_id,
            "title": body.title,
            "content": body.content,
            "tags": list(body.tags),
            "source": body.source,
            "_chunks": chunks,
            "created_at": now,
            "updated_at": now,
        }
        _DOCUMENTS[doc_id] = doc
        global _VECTOR_INDEX_DIRTY
        _VECTOR_INDEX_DIRTY = True

        logger.info(
            "document added: %s (title=%s, chunks=%d)",
            doc_id, body.title, len(chunks),
        )

        return DocumentItem(
            id=doc_id,
            title=body.title,
            tags=list(body.tags),
            source=body.source,
            content_preview=body.content[:200],
            chunk_count=len(chunks),
            created_at=now,
            updated_at=now,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("add document failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    token=Depends(require_scope("knowledge-base:use")),
):
    """Delete a document from the knowledge base."""
    try:
        _get_doc_or_404(doc_id)
        del _DOCUMENTS[doc_id]
        global _VECTOR_INDEX_DIRTY
        _VECTOR_INDEX_DIRTY = True
        logger.info("document deleted: %s", doc_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete document failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query", response_model=QueryResponse)
async def query_knowledge_base(
    body: QueryRequest,
    token=Depends(require_scope("knowledge-base:use")),
):
    """Search the knowledge base with a natural language query.

    Uses simple keyword-overlap scoring as a placeholder.
    TODO: replace with vector + BM25 hybrid retrieval (see desktop-side
    思忆集app/knowledge_base/retriever.py).
    """
    try:
        if not _DOCUMENTS:
            return QueryResponse(results=[], query=body.query, total=0)

        docs = list(_DOCUMENTS.values())
        results = _simple_rank(body.query, docs, body.top_k, body.threshold)

        logger.debug(
            "query returned %d / %d results (top_k=%d, threshold=%.2f)",
            len(results), len(docs), body.top_k, body.threshold,
        )

        return QueryResponse(
            results=results,
            query=body.query,
            total=len(results),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("query knowledge base failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=KnowledgeBaseStatus)
async def get_status(
    token=Depends(require_scope("knowledge-base:use")),
):
    """Get knowledge base status and statistics."""
    try:
        total_chunks = 0
        for doc in _DOCUMENTS.values():
            chunks = doc.get("_chunks") or []
            total_chunks += len(chunks)

        return KnowledgeBaseStatus(
            total_documents=len(_DOCUMENTS),
            total_chunks=total_chunks,
            vector_index_ready=not _VECTOR_INDEX_DIRTY,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get status failed")
        raise HTTPException(status_code=500, detail=str(e))
