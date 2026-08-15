import os
from pathlib import Path
from typing import Optional

# ── 确保 .env 被加载（无论从哪里启动）────────────────────
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_path, override=False)


def prefer_env(name: str, primary_prefix: str = "ANTHROPIC", secondary_prefix: str = "OPENAI") -> str:
    """Prefer environment variables in the order: {primary_prefix}_{name} -> {secondary_prefix}_{name} -> {name}.

    Examples:
      prefer_env('API_KEY') -> checks ANTHROPIC_API_KEY, then OPENAI_API_KEY, then API_KEY
    """
    prim = os.getenv(f"{primary_prefix}_{name}", "").strip()
    if prim:
        return prim
    sec = os.getenv(f"{secondary_prefix}_{name}", "").strip()
    if sec:
        return sec
    return os.getenv(name, "")


def get_api_key() -> str:
    return prefer_env("API_KEY")


def get_base_url() -> str:
    url = prefer_env("BASE_URL")
    # 清理已知的污染后缀
    for bad_suffix in ("/anthropic", "/v1/v1"):
        if url.endswith(bad_suffix):
            url = url[:-len(bad_suffix)]
    return url


def get_model() -> str:
    return prefer_env("MODEL")


def get_verifier_model() -> str:
    """返回验证模型名称（由管理员在后台配置）。

    优先级：DB AdminConfig.verifier_model → LLM_VERIFIER_MODEL 环境变量 → 空（跳过验证）
    """
    # DB config 优先
    try:
        from .database import SessionLocal
        from .models_db import AdminConfig
        db = SessionLocal()
        try:
            cfg = db.query(AdminConfig).filter(AdminConfig.id == 1).first()
            if cfg and getattr(cfg, "verifier_model", None):
                return cfg.verifier_model.strip()
        finally:
            db.close()
    except Exception:
        pass
    # 环境变量备选
    return os.getenv("LLM_VERIFIER_MODEL", "").strip()


def get_active_model() -> str:
    """返回当前激活的主模型标识（用于日志和报告元数据）。"""
    return get_model() or "unknown"

