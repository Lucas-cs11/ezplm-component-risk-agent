import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models_db import User, AdminConfig
from ..auth import get_current_admin, hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

PROVIDERS = {
    "manbou":   {"name": "Manbou API（推荐）", "base_url": "https://www.manbouapi.com/v1", "models": ["claude-sonnet-5", "claude-opus-5", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"], "default_model": "claude-sonnet-5"},
    "deepseek": {"name": "DeepSeek 官方",      "base_url": "https://api.deepseek.com/v1",  "models": ["deepseek-chat", "deepseek-reasoner"],                                                "default_model": "deepseek-chat"},
    "openai":   {"name": "OpenAI",             "base_url": "https://api.openai.com/v1",    "models": ["gpt-4o", "gpt-4o-mini"],                                                             "default_model": "gpt-4o-mini"},
    "custom":   {"name": "自定义",              "base_url": "",                             "models": [],                                                                                    "default_model": ""},
}


def _mask(key: str) -> str:
    if not key or len(key) < 10:
        return key
    return key[:6] + "****" + key[-4:]


def _apply_config_to_env(cfg: AdminConfig):
    """将 DB 中的管理员配置同步到运行时环境变量，立即生效。"""
    if cfg.llm_api_key:
        os.environ["ANTHROPIC_API_KEY"] = cfg.llm_api_key
        os.environ["OPENAI_API_KEY"] = cfg.llm_api_key
    if cfg.llm_base_url:
        os.environ["ANTHROPIC_BASE_URL"] = cfg.llm_base_url
        os.environ["OPENAI_BASE_URL"] = cfg.llm_base_url
    if cfg.llm_model:
        os.environ["ANTHROPIC_MODEL"] = cfg.llm_model
        os.environ["OPENAI_MODEL"] = cfg.llm_model
    if cfg.ezplm_api_key:
        os.environ["EZPLM_API_KEY"] = cfg.ezplm_api_key
    if cfg.verifier_model:
        os.environ["LLM_MODEL_VERIFIER"] = cfg.verifier_model
    if cfg.verifier_api_key:
        os.environ["LLM_VERIFIER_API_KEY"] = cfg.verifier_api_key
    if cfg.verifier_base_url:
        os.environ["LLM_VERIFIER_BASE_URL"] = cfg.verifier_base_url


class ConfigBody(BaseModel):
    ezplm_api_key: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    verifier_model: Optional[str] = None
    verifier_api_key: Optional[str] = None
    verifier_base_url: Optional[str] = None


@router.get("/setup-required")
def setup_required(db: Session = Depends(get_db)):
    """无需认证：检查是否需要首次初始化（无用户时返回 true）。"""
    return {"setup_required": db.query(User).count() == 0}


@router.get("/providers")
def get_providers():
    return {"providers": PROVIDERS}


@router.get("/config")
def get_config(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    cfg = db.query(AdminConfig).first()
    if not cfg:
        return {"configured": False, "providers": PROVIDERS}
    return {
        "configured": bool(cfg.llm_api_key and cfg.ezplm_api_key),
        "ezplm_api_key_masked": _mask(cfg.ezplm_api_key or ""),
        "llm_provider": cfg.llm_provider or "manbou",
        "llm_base_url": cfg.llm_base_url or "",
        "llm_model": cfg.llm_model or "",
        "llm_api_key_masked": _mask(cfg.llm_api_key or ""),
        "verifier_model": cfg.verifier_model or "",
        "verifier_api_key_masked": _mask(cfg.verifier_api_key or ""),
        "verifier_base_url": cfg.verifier_base_url or "",
        "providers": PROVIDERS,
    }


@router.post("/config")
def set_config(body: ConfigBody, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    cfg = db.query(AdminConfig).first()
    if not cfg:
        cfg = AdminConfig(id=1)
        db.add(cfg)
    if body.ezplm_api_key is not None:
        cfg.ezplm_api_key = body.ezplm_api_key.strip()
    if body.llm_provider is not None:
        cfg.llm_provider = body.llm_provider
    if body.llm_api_key is not None:
        cfg.llm_api_key = body.llm_api_key.strip()
    if body.llm_base_url is not None:
        cfg.llm_base_url = body.llm_base_url.strip().rstrip("/")
    if body.llm_model is not None:
        cfg.llm_model = body.llm_model.strip()
    if body.verifier_model is not None:
        cfg.verifier_model = body.verifier_model.strip() if body.verifier_model else None
    if body.verifier_api_key is not None:
        cfg.verifier_api_key = body.verifier_api_key.strip() if body.verifier_api_key else None
    if body.verifier_base_url is not None:
        cfg.verifier_base_url = body.verifier_base_url.strip().rstrip("/") if body.verifier_base_url else None
    db.commit()
    _apply_config_to_env(cfg)
    return {"status": "ok"}


@router.get("/users")
def list_users(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.created_at).all()
    return {"users": [
        {"id": u.id, "username": u.username, "email": u.email,
         "is_admin": u.is_admin, "is_active": u.is_active,
         "created_at": u.created_at.isoformat() if u.created_at else None}
        for u in users
    ]}


@router.post("/users/{user_id}/toggle")
def toggle_user(user_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    if user.id == admin.id:
        raise HTTPException(400, "不能禁用自己的账号")
    user.is_active = not user.is_active
    db.commit()
    return {"id": user.id, "is_active": user.is_active}


# ── RAG 知识库管理 ─────────────────────────────────────────────────

class RagUploadBody(BaseModel):
    title: str
    content: str
    category: str = "general"


@router.get("/rag/status")
def rag_status(admin=Depends(get_current_admin)):
    try:
        from ..rag import get_rag_store
        store = get_rag_store()
        return {"count": store.count, "ok": True}
    except Exception as e:
        return {"count": 0, "ok": False, "error": str(e)}


@router.get("/rag/docs")
def rag_list_docs(admin=Depends(get_current_admin)):
    try:
        from ..rag import get_rag_store
        store = get_rag_store()
        return {"docs": store.list_documents(limit=100), "count": store.count}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/rag/upload")
def rag_upload(body: RagUploadBody, admin=Depends(get_current_admin)):
    if not body.content.strip():
        raise HTTPException(400, "内容不能为空")
    try:
        from ..rag import get_rag_store
        store = get_rag_store()
        store.ingest_documents(
            [{"content": body.content,
              "metadata": {"title": body.title, "category": body.category}}],
            id_offset=store.count,
        )
        return {"ok": True, "count": store.count}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/rag/clear")
def rag_clear(admin=Depends(get_current_admin)):
    try:
        from ..rag import get_rag_store
        get_rag_store().clear()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))
