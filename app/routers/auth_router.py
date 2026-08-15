from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from ..database import get_db
from ..models_db import User
from ..auth import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_refresh_token,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


def _user_out(u) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": getattr(u, "email", "") or "",
        "is_admin": u.is_admin,
        "is_guest": getattr(u, "is_guest", False),
        "dual_model_enabled": bool(getattr(u, "dual_model_enabled", False)),
    }


@router.post("/register")
def register(body: RegisterBody, db: Session = Depends(get_db)):
    # 内测阶段，注册通道关闭（仅允许首个管理员账号初始化）
    existing_admin = db.query(User).filter(User.is_admin == True).first()
    if existing_admin:
        raise HTTPException(403, "内测阶段，注册通道尚未开通，请联系管理员获取访问权限")
    if len(body.username) < 2 or len(body.username) > 30:
        raise HTTPException(400, "用户名长度须在 2–30 字符之间")
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "用户名已存在")

    is_admin = db.query(User).count() == 0
    user = User(
        username=body.username,
        hashed_password=hash_password(body.password),
        is_admin=is_admin,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id), "username": user.username, "is_admin": user.is_admin})
    refresh = create_refresh_token({"sub": str(user.id), "username": user.username, "is_admin": user.is_admin})
    return {"token": token, "refresh_token": refresh, "user": _user_out(user)}


@router.post("/login")
def login(body: LoginBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已被禁用，请联系管理员")

    token = create_access_token({"sub": str(user.id), "username": user.username, "is_admin": user.is_admin})
    refresh = create_refresh_token({"sub": str(user.id), "username": user.username, "is_admin": user.is_admin})
    return {"token": token, "refresh_token": refresh, "user": _user_out(user)}


@router.post("/refresh")
def refresh_token(body: dict, db: Session = Depends(get_db)):
    """P2-2: Exchange a valid refresh token for a new access + refresh token pair (sliding expiry)."""
    rt = (body or {}).get("refresh_token", "")
    if not rt:
        raise HTTPException(400, "缺少 refresh_token")
    payload = decode_refresh_token(rt)
    user_id = payload.get("sub")
    if not user_id or user_id == "guest":
        raise HTTPException(401, "刷新令牌无效")
    from ..models_db import User as _User
    user = db.query(_User).filter(_User.id == int(user_id), _User.is_active == True).first()
    if not user:
        raise HTTPException(401, "用户不存在或已被禁用")
    token = create_access_token({"sub": str(user.id), "username": user.username, "is_admin": user.is_admin})
    new_refresh = create_refresh_token({"sub": str(user.id), "username": user.username, "is_admin": user.is_admin})
    return {"token": token, "refresh_token": new_refresh}


@router.post("/guest")
def guest_login(db: Session = Depends(get_db)):
    """游客演示登录：无需注册，管理员配置好后才可使用。对话不会保存。"""
    has_admin = db.query(User).filter(User.is_admin == True).first()
    if not has_admin:
        raise HTTPException(403, "系统尚未初始化，请先完成管理员注册和配置")
    token = create_access_token({"sub": "guest", "is_guest": True, "username": "游客"})
    return {"token": token, "user": {"id": 0, "username": "游客", "email": "", "is_admin": False, "is_guest": True}}


@router.get("/me")
def me(current_user=Depends(get_current_user)):
    return _user_out(current_user)


@router.post("/me/dual-model")
def toggle_dual_model(
    body: dict,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户开关双模型验证。需要管理员先配置验证模型。"""
    if getattr(current_user, "is_guest", False):
        raise HTTPException(403, "游客不支持此功能")
    enabled = bool((body or {}).get("enabled", False))
    if enabled:
        from ..models_db import AdminConfig
        cfg = db.query(AdminConfig).first()
        if not cfg or not (cfg.verifier_model or "").strip():
            raise HTTPException(403, "管理员未授权此功能")
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    user.dual_model_enabled = enabled
    db.commit()
    return {"dual_model_enabled": user.dual_model_enabled}
