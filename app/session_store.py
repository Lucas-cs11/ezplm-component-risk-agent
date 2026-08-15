"""
session_store.py — 跨 worker 会话约束共享存储

优先使用 Redis（设置 REDIS_URL 环境变量即可激活），
未配置时自动降级到进程内字典（单 worker 模式）。

用法::
    from .session_store import constraint_store
    constraint_store.get(sid)          # -> dict
    constraint_store.set(sid, data)
    constraint_store.pop(sid)
    constraint_store.contains(sid)     # -> bool
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_TTL = int(os.environ.get("SESSION_STORE_TTL", 86400))  # 默认 24h
_KEY_PREFIX = "ez:ac:"  # ez accumulated-constraints


class _RedisStore:
    def __init__(self, url: str) -> None:
        import redis  # type: ignore
        self._r = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        self._r.ping()  # 验证连接，失败则抛出异常
        logger.info("SessionStore: Redis connected at %s", url.split("@")[-1])

    def _k(self, sid: str) -> str:
        return f"{_KEY_PREFIX}{sid}"

    def get(self, sid: str) -> dict:
        try:
            raw = self._r.get(self._k(sid))
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def set(self, sid: str, data: dict) -> None:
        try:
            self._r.setex(self._k(sid), _TTL, json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    def pop(self, sid: str) -> None:
        try:
            self._r.delete(self._k(sid))
        except Exception:
            pass

    def contains(self, sid: str) -> bool:
        try:
            return bool(self._r.exists(self._k(sid)))
        except Exception:
            return False


class _MemoryStore:
    """进程内字典，不共享跨 worker，但无额外依赖。"""

    _MAX = 500

    def __init__(self) -> None:
        self._d: Dict[str, dict] = {}

    def _evict(self) -> None:
        if len(self._d) > self._MAX:
            n = len(self._d) - self._MAX + 50
            for _ in range(n):
                try:
                    self._d.pop(next(iter(self._d)))
                except StopIteration:
                    break

    def get(self, sid: str) -> dict:
        return dict(self._d.get(sid, {}))

    def set(self, sid: str, data: dict) -> None:
        self._evict()
        self._d[sid] = dict(data)

    def pop(self, sid: str) -> None:
        self._d.pop(sid, None)

    def contains(self, sid: str) -> bool:
        return sid in self._d


def _build_store() -> _RedisStore | _MemoryStore:
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        try:
            return _RedisStore(url)
        except Exception as exc:
            logger.warning("SessionStore: Redis unavailable (%s), falling back to in-memory", exc)
    else:
        logger.debug("SessionStore: REDIS_URL not set, using in-memory store")
    return _MemoryStore()


constraint_store: _RedisStore | _MemoryStore = _build_store()
