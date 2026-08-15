"""
semantic_cache.py — 语义缓存层（B4 任务）

基于 ChromaDB，实现语义相似度缓存。
当新查询与缓存中的历史查询相似度 > 0.95 时，直接返回缓存结果，跳过 LLM 调用。

关键优化：
  - SentenceTransformer 模型单例加载（避免每次 get/set 重新下载）
  - 模型下载失败时优雅降级（禁用缓存，不影响主流程）
  - HuggingFace 超时 10s 快速失败

用法:
  cache = SemanticCache(persist_dir="data/chroma_cache")
  result = cache.get(query)  # 查询缓存
  if result is None:
      result = expensive_llm_call(query)
      cache.set(query, result)  # 存入缓存
"""

from __future__ import annotations

import os
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional

# ── 模型单例 ────────────────────────────────────────────────────
_embedding_model = None
_embedding_model_error = None

MODEL_NAME = "all-MiniLM-L6-v2"
CACHE_PROTOCOL_VERSION = "selection-v2"

# These fields define the identity of a selection request. Free-form text is
# intentionally excluded so semantically similar but electrically different
# requests cannot reuse one another's report.
_CONSTRAINT_FIELDS = (
    "category",
    "topology",
    "input_voltage_nominal_v",
    "input_voltage_min_v",
    "input_voltage_max_v",
    "output_voltage_v",
    "output_current_a",
    "temperature_min_c",
    "temperature_max_c",
    "grade",
    "package_preference",
    "application",
)
_REQUIRED_CONSTRAINT_FIELDS = (
    "input_voltage_nominal_v",
    "output_voltage_v",
    "output_current_a",
)


def _normalize_constraint_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().lower() or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 6)
    return value


def canonical_constraint_payload(constraints: Any) -> Optional[Dict[str, Any]]:
    """Return the stable, complete identity payload for a selection request."""
    if hasattr(constraints, "dict"):
        constraints = constraints.dict()
    elif hasattr(constraints, "__dict__"):
        constraints = vars(constraints)
    if not isinstance(constraints, dict):
        return None

    payload = {
        field: _normalize_constraint_value(constraints.get(field))
        for field in _CONSTRAINT_FIELDS
    }
    has_vin = payload.get("input_voltage_nominal_v") is not None or (
        payload.get("input_voltage_min_v") is not None
        and payload.get("input_voltage_max_v") is not None
    )
    if not has_vin or any(payload.get(field) is None for field in _REQUIRED_CONSTRAINT_FIELDS[1:]):
        return None
    return {"protocol": CACHE_PROTOCOL_VERSION, "constraints": payload}


def canonical_constraint_fingerprint(constraints: Any) -> Optional[str]:
    payload = canonical_constraint_payload(constraints)
    if payload is None:
        return None
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{CACHE_PROTOCOL_VERSION}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _get_embedding_model():
    """获取 SentenceTransformer 模型（单例，带超时保护）。

    首次调用时下载模型（~80MB），后续调用复用。
    下载失败时缓存错误，避免重复尝试。
    """
    global _embedding_model, _embedding_model_error

    if _embedding_model is not None:
        return _embedding_model

    if _embedding_model_error is not None:
        raise _embedding_model_error

    # 检测本地缓存，避免联网版本验证导致的超时（服务器无外网时可达180s）
    from pathlib import Path as _Path
    _hf_hub = _Path(os.environ.get("HF_HOME", str(_Path.home() / ".cache" / "huggingface"))) / "hub"
    _model_cached = (_hf_hub / f"models--sentence-transformers--{MODEL_NAME.replace('/', '--')}").exists()

    try:
        from sentence_transformers import SentenceTransformer
        # 本地有缓存 → local_files_only=True（毫秒级加载）；否则允许下载
        _embedding_model = SentenceTransformer(MODEL_NAME, local_files_only=_model_cached)
        return _embedding_model

    except Exception as e:
        _embedding_model_error = RuntimeError(
            f"Failed to load embedding model '{MODEL_NAME}': {e}. "
            f"Semantic cache disabled. Pre-download: "
            f"HF_ENDPOINT=https://hf-mirror.com python -c "
            f"\"from sentence_transformers import SentenceTransformer; SentenceTransformer('{MODEL_NAME}')\""
        )
        raise _embedding_model_error


class SemanticCache:
    """语义缓存层：利用向量相似度缓存 LLM 调用结果。

    基于 cosine 距离，当相似度 > 0.95 时视为命中。
    模型加载失败时自动降级（get/set 均返回未命中/失败）。
    """

    def __init__(self, persist_dir: str = str(Path(__file__).resolve().parent.parent / "data" / "chroma_cache")):
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._exact_dir = self._persist_dir / CACHE_PROTOCOL_VERSION
        self._exact_dir.mkdir(parents=True, exist_ok=True)

        # The exact selection cache is filesystem-backed and must be available
        # without importing or opening the legacy Chroma vector database.
        self._client = None
        self._collection = None
        self._model_available = True

    def _legacy_collection(self):
        """Lazily initialize the obsolete raw-text semantic cache."""
        if self._collection is not None:
            return self._collection
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        self._client = chromadb.PersistentClient(
            path=str(self._persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name="semantic_cache",
            metadata={"hnsw:space": "cosine"},
        )
        return self._collection

    @property
    def count(self) -> int:
        """返回 legacy 语义缓存中的条目数。"""
        return self._legacy_collection().count()

    def get(self, query: str, threshold: float = 0.95) -> Optional[Dict[str, Any]]:
        """Query the legacy semantic cache.

        Selection reports no longer use this method. They use the exact,
        versioned constraint cache below so electrically different requests
        cannot collide through embedding similarity.
        """
        collection = self._legacy_collection()
        if collection.count() == 0:
            return None

        if not self._model_available:
            return None

        try:
            model = _get_embedding_model()
            query_embedding = model.encode([query], show_progress_bar=False).tolist()

            results = collection.query(
                query_embeddings=query_embedding,
                n_results=1,
            )

            if not results or not results.get("ids") or not results["ids"][0]:
                return None

            distances = results.get("distances", [[]])[0]
            if not distances:
                return None

            distance = distances[0]
            similarity = 1 - distance

            if similarity < threshold:
                return None

            metadatas = results.get("metadatas", [[]])[0]
            if not metadatas or not isinstance(metadatas, list) or len(metadatas) == 0:
                return None

            metadata_dict = metadatas[0]
            if "cached_result" not in metadata_dict:
                return None

            cached_result = json.loads(metadata_dict["cached_result"])
            return {
                "cached_result": cached_result,
                "similarity": round(similarity, 4),
                "cache_hit": True,
            }

        except Exception:
            return None

    def get_exact(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        """Return a report stored under an exact canonical fingerprint."""
        if not fingerprint:
            return None
        try:
            cache_file = self._exact_dir / f"{fingerprint.split(':', 1)[-1]}.json"
            if not cache_file.is_file():
                return None
            envelope = json.loads(cache_file.read_text(encoding="utf-8"))
            if envelope.get("protocol") != CACHE_PROTOCOL_VERSION:
                return None
            cached = envelope.get("cached_result")
            if not isinstance(cached, dict):
                return None
            return {"cached_result": cached, "similarity": 1.0, "cache_hit": True}
        except Exception:
            return None

    def set_exact(self, fingerprint: str, result: Dict[str, Any]) -> bool:
        """Atomically replace a complete report under its canonical fingerprint."""
        if not fingerprint:
            return False
        try:
            cache_file = self._exact_dir / f"{fingerprint.split(':', 1)[-1]}.json"
            tmp_file = cache_file.with_suffix(".tmp")
            envelope = {"protocol": CACHE_PROTOCOL_VERSION, "cached_result": result}
            tmp_file.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
            tmp_file.replace(cache_file)
            return True
        except Exception:
            return False

    def set(self, query: str, result: Dict[str, Any]) -> bool:
        """存入缓存：将查询和结果存储到向量库。"""
        if not self._model_available:
            return False

        try:
            collection = self._legacy_collection()
            model = _get_embedding_model()
            query_embedding = model.encode([query], show_progress_bar=False).tolist()

            import hashlib
            doc_id = f"cache_{hashlib.md5(query.encode()).hexdigest()[:8]}"
            cached_result_json = json.dumps(result, ensure_ascii=False)

            collection.add(
                ids=[doc_id],
                embeddings=query_embedding,
                documents=[query],
                metadatas=[{
                    "cached_result": cached_result_json,
                    "query_length": len(query),
                }],
            )
            return True

        except Exception:
            return False


# 全局缓存实例
_semantic_cache: Optional[SemanticCache] = None


def get_semantic_cache() -> SemanticCache:
    """获取全局语义缓存实例（懒加载）。"""
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
