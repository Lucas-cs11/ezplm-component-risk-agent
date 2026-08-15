"""
rag.py — 工程知识 RAG 检索模块（最小可行版）

基于 ChromaDB + sentence-transformers，存储和检索电子工程设计知识。
知识来源：设计公式、选型规范、应用笔记、标准要求等。

用法：
  store = RAGStore(persist_dir="data/chroma_db")
  store.ingest_documents([{"content": "...", "metadata": {...}}, ...])
  results = store.query("12V转5V buck 电感选型", top_k=3)
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

# 懒加载 embedding 模型（首次调用时下载，约 80MB）
_embedding_model = None


def _get_embedding_model():
    """懒加载 sentence-transformers 模型。检测本地缓存优先，避免联网超时。"""
    global _embedding_model
    if _embedding_model is None:
        import os as _os
        from pathlib import Path as _Path
        from sentence_transformers import SentenceTransformer
        _hf_hub = _Path(_os.environ.get("HF_HOME", str(_Path.home() / ".cache" / "huggingface"))) / "hub"
        _cached = (_hf_hub / "models--sentence-transformers--all-MiniLM-L6-v2").exists()
        if _cached:
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        else:
            _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            _os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "15")
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=False)
    return _embedding_model


class RAGStore:
    """工程知识向量存储与检索。

    每个文档 = {content: str, metadata: dict}。
    内部自动分块（按段落）并生成 embedding。
    """

    def __init__(self, persist_dir: str = str(Path(__file__).resolve().parent.parent / "data" / "chroma_db")):
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(self._persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name="engineering_knowledge",
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def count(self) -> int:
        return self._collection.count()

    def ingest_documents(
        self,
        documents: List[Dict[str, Any]],
        batch_size: int = 32,
        id_offset: int = 0,
    ):
        """批量摄入文档。每个文档含 content 和 metadata 字段。

        Args:
            documents: 文档列表
            batch_size: 内部编码批次大小
            id_offset: ID 起始偏移（避免多次调用时 ID 碰撞）
        """
        model = _get_embedding_model()

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]
            texts = [doc["content"] for doc in batch]
            ids = [f"doc_{id_offset + i + j}" for j in range(len(batch))]
            metadatas = [doc.get("metadata", {}) for doc in batch]

            # 生成 embedding 并入库
            embeddings = model.encode(texts, show_progress_bar=False).tolist()
            self._collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )

    def query(
        self,
        query_text: str,
        top_k: int = 5,
        category_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """语义检索最相关的工程知识片段。

        Args:
            query_text: 查询文本（如 "12V转5V buck 电感计算公式"）
            top_k: 返回条数
            category_filter: 可选的类别过滤（如 "buck_design", "thermal"）

        Returns:
            [{content, metadata, score}, ...]
        """
        model = _get_embedding_model()
        query_embedding = model.encode([query_text], show_progress_bar=False).tolist()

        where_filter = None
        if category_filter:
            where_filter = {"category": category_filter}

        results = self._collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        output = []
        if results["ids"] and results["ids"][0]:
            for j in range(len(results["ids"][0])):
                distance = results["distances"][0][j] if results["distances"] else 1.0
                # cosine distance → similarity score (0-1)
                score = max(0.0, 1.0 - distance)
                output.append({
                    "content": results["documents"][0][j],
                    "metadata": results["metadatas"][0][j] if results["metadatas"] else {},
                    "score": round(score, 3),
                })
        return output

    def clear(self):
        """清空知识库（用于重建）。"""
        self._client.delete_collection("engineering_knowledge")
        self._collection = self._client.get_or_create_collection(
            name="engineering_knowledge",
            metadata={"hnsw:space": "cosine"},
        )

    def list_documents(self, limit: int = 100) -> List[Dict[str, Any]]:
        """列出知识库中已索引的文档条目。"""
        try:
            results = self._collection.get(
                limit=limit,
                include=["metadatas", "documents"],
            )
            docs = []
            for i, doc_id in enumerate(results.get("ids", [])):
                meta = (results["metadatas"][i] if results.get("metadatas") else {}) or {}
                text = (results["documents"][i] if results.get("documents") else "") or ""
                docs.append({
                    "id": doc_id,
                    "title": meta.get("title", "未命名"),
                    "category": meta.get("category", ""),
                    "preview": text[:120] + ("…" if len(text) > 120 else ""),
                })
            return docs
        except Exception:
            return []


def build_context_from_results(results: List[Dict[str, Any]], max_chars: int = 1500) -> str:
    """将 RAG 检索结果拼接为 LLM 上下文文本。

    Args:
        results: RAGStore.query() 的返回结果
        max_chars: 拼接后最大字符数（避免超出 LLM context 限制）

    Returns:
        格式化的参考知识文本
    """
    if not results:
        return "（未检索到相关工程知识）"

    lines = []
    total = 0
    for i, r in enumerate(results, 1):
        title = r["metadata"].get("title", f"参考条目 {i}")
        content = r["content"]
        entry = f"【{title}】（相关度: {r['score']:.2f}）\n{content}"
        if total + len(entry) > max_chars:
            entry = entry[:max_chars - total] + "..."
            lines.append(entry)
            break
        lines.append(entry)
        total += len(entry) + 2

    return "\n\n".join(lines)


# ── 全局单例 ──────────────────────────────────────────────────────
_rag_store: Optional[RAGStore] = None


def get_rag_store(persist_dir: str = "data/chroma_db") -> RAGStore:
    """获取全局 RAGStore 单例。"""
    global _rag_store
    if _rag_store is None:
        _rag_store = RAGStore(persist_dir=persist_dir)
    return _rag_store


def query_unified(query_text: str, n_results: int = 5, part_numbers: List[str] = None) -> List[Dict]:
    """P2-8: 统一 RAG 查询入口 — 合并工程知识库 + 数据手册元数据两个知识源。

    返回结构化结果列表，每项包含 source 字段区分来源：
      source="engineering_knowledge" — ChromaDB 工程知识
      source="datasheet_registry"    — 数据手册元数据
    """
    results: List[Dict] = []

    # ── 1. 工程知识库（ChromaDB）────────────────────────────────
    try:
        store = get_rag_store()
        rag_hits = store.query(query_text, n_results=n_results)
        for hit in rag_hits:
            results.append({**hit, "source": "engineering_knowledge"})
    except Exception:
        pass

    # ── 2. 数据手册元数据（DatasheetRegistry 静态索引）──────────
    if part_numbers:
        try:
            from .datasheet_rag import DatasheetRegistry
            registry = DatasheetRegistry()
            for pn in part_numbers:
                meta = registry.get_metadata(pn)
                if meta:
                    results.append({
                        "content": (
                            f"{pn}: {getattr(meta, 'description', '')} "
                            f"| 封装:{getattr(meta, 'package', '')} "
                            f"| 制造商:{getattr(meta, 'manufacturer', '')}"
                        ).strip(" |"),
                        "metadata": {"part_number": pn},
                        "score": 1.0,
                        "source": "datasheet_registry",
                    })
        except Exception:
            pass

    return results
