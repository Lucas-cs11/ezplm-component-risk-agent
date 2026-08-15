import os
import bcrypt as _bcrypt
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .database import get_db

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "ezmanbo-jwt-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7
REFRESH_TOKEN_EXPIRE_DAYS = 30  # P2-2: long-lived refresh token

security = HTTPBearer(auto_error=False)


def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload["type"] = "access"
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """P2-2: 30-day refresh token with sliding expiry on use."""
    payload = {k: v for k, v in data.items() if k != "exp"}
    payload["exp"] = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload["type"] = "refresh"
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_refresh_token(token: str) -> dict:
    """Decode and validate a refresh token. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="不是有效的刷新令牌")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="刷新令牌无效或已过期，请重新登录")


def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="无效或已过期的令牌，请重新登录")


class GuestUser:
    """游客伪用户对象，不持久化到数据库。"""
    id = 0
    username = "游客"
    email = ""
    is_admin = False
    is_active = True
    is_guest = True


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    if not credentials:
        raise HTTPException(status_code=401, detail="未提供认证令牌，请先登录")
    payload = _decode(credentials.credentials)

    if payload.get("is_guest"):
        return GuestUser()

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="令牌格式错误")
    from .models_db import User
    user = db.query(User).filter(User.id == int(user_id), User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或已被禁用")
    return user


def get_current_admin(user=Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    if not credentials:
        return None
    try:
        payload = _decode(credentials.credentials)
        if payload.get("is_guest"):
            return GuestUser()
        user_id = payload.get("sub")
        if not user_id:
            return None
        from .models_db import User
        return db.query(User).filter(User.id == int(user_id), User.is_active == True).first()
    except Exception:
        return None

