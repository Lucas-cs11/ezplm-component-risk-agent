"""Minimal logging utility — emits warnings for swallowed exceptions during debugging."""

import os
import logging
import traceback
import uuid
from contextvars import ContextVar

_DEBUG = os.getenv("EZPLM_DEBUG", "").strip().lower() in ("1", "true", "yes")

# P2-5: request_id context variable — propagates across async tasks in same request
_request_id: ContextVar[str] = ContextVar('request_id', default='')


def set_request_id(rid: str) -> None:
    _request_id.set(rid)


def get_request_id() -> str:
    return _request_id.get('')


def new_request_id() -> str:
    return str(uuid.uuid4())[:8]


logging.basicConfig(
    level=logging.DEBUG if _DEBUG else logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
_logger = logging.getLogger("ezplm")


def _rid_prefix() -> str:
    rid = get_request_id()
    return f"[{rid}] " if rid else ""


def warn_swallow(module: str, exc: Exception, context: str = ""):
    """Log a warning when an exception is intentionally swallowed."""
    ctx = f" ({context})" if context else ""
    _logger.warning(f"{_rid_prefix()}[{module}]{ctx} Swallowed exception: {exc}")
    if _DEBUG:
        traceback.print_exc()


def log_error(module: str, exc: Exception, context: str = "", extra: dict | None = None):
    """Log an error with optional structured extra data for downstream errors."""
    ctx = f" ({context})" if context else ""
    if extra:
        _logger.error(f"{_rid_prefix()}[{module}]{ctx} Error: {exc} | extra={extra}")
    else:
        _logger.error(f"{_rid_prefix()}[{module}]{ctx} Error: {exc}")
    if _DEBUG:
        traceback.print_exc()


