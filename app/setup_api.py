"""启动配置、诊断、权限管理 API 路由。"""
import os
import json
import subprocess
from typing import Optional
from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

router = APIRouter(tags=["setup"])

# ── 预置 LLM 提供商模板 ──────────────────────────────
PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "default_model": "deepseek-v4-flash",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
        "default_model": "gpt-4o-mini",
    },
    "claude": {
        "name": "Anthropic Claude",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
        "default_model": "claude-sonnet-4-20250514",
    },
}

# ── 权限级别 ──────────────────────────────────────────
PERMISSION_LEVELS = {
    "readonly": {
        "label": "只读 (L1)",
        "desc": "仅允许查看文件、搜索等只读操作",
        "allowed_tools": ["Read", "Grep", "Glob", "WebSearch"],
    },
    "standard": {
        "label": "标准 (L2)",
        "desc": "读写文件、执行一般终端命令",
        "allowed_tools": ["Read", "Grep", "Glob", "Write", "Edit", "Bash"],
    },
    "full": {
        "label": "完整 (L3)",
        "desc": "完全权限，可安装软件、修改系统设置",
        "allowed_tools": ["Read", "Grep", "Glob", "Write", "Edit", "Bash"],
    },
}
_current_permission = "standard"

_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


def _load_env() -> dict:
    """读取 .env 文件（不加载到进程）。"""
    data = {}
    if not os.path.exists(_ENV_PATH):
        return data
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data


def _save_env(updates: dict):
    """更新 .env 文件中的指定键值对。"""
    lines = []
    seen = set()
    if os.path.exists(_ENV_PATH):
        with open(_ENV_PATH) as f:
            lines = f.readlines()
    # 更新或追加
    for key, val in updates.items():
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={val}\n"
                found = True
                seen.add(key)
                break
        if not found:
            lines.append(f"{key}={val}\n")
            seen.add(key)
    with open(_ENV_PATH, "w") as f:
        f.writelines(lines)
    # 同步到当前进程环境
    for key, val in updates.items():
        os.environ[key] = val


def _mask_key(key: str) -> str:
    """隐藏 API Key 中间部分。"""
    if not key or len(key) < 12:
        return key
    return key[:6] + "****" + key[-4:]


# ── 1. 设置状态 ──────────────────────────────────────
@router.get("/api/setup/status")
async def setup_status():
    """返回当前配置状态，指示哪些已完成、哪些缺失。"""
    env = _load_env()
    llm_key = env.get("OPENAI_API_KEY") or env.get("ANTHROPIC_API_KEY") or ""
    llm_url = env.get("OPENAI_BASE_URL") or env.get("ANTHROPIC_BASE_URL") or ""
    ezplm_key = env.get("EZPLM_API_KEY", "")
    ezplm_url = env.get("EZPLM_BASE_URL", "")
    user_done = os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), "memory", "USER.md"))

    return {
        "onboarding_done": bool(llm_key and ezplm_key and user_done),
        "llm": {
            "configured": bool(llm_key and llm_url),
            "provider": "deepseek" if "deepseek" in llm_url else "openai" if "openai" in llm_url else "claude" if "anthropic" in llm_url else "custom",
            "model": env.get("OPENAI_MODEL") or env.get("ANTHROPIC_MODEL") or "未设置",
            "key_masked": _mask_key(llm_key) if llm_key else None,
        },
        "ezplm": {
            "configured": bool(ezplm_key and ezplm_url),
            "url": ezplm_url or None,
            "key_masked": _mask_key(ezplm_key) if ezplm_key else None,
        },
        "user_profile": {
            "completed": user_done,
        },
        "permission": _current_permission,
    }


# ── 2. 配置 LLM 提供商 ───────────────────────────────
@router.post("/api/setup/provider")
async def set_provider(body: dict = Body(...)):
    """配置 LLM 提供商。body: { provider: str, api_key: str, base_url?: str, model?: str }"""
    provider = body.get("provider", "custom")
    api_key = body.get("api_key", "").strip()
    if not api_key:
        return JSONResponse(status_code=400, content={"detail": "API Key 不能为空"})

    if provider in PROVIDERS:
        info = PROVIDERS[provider]
        base_url = body.get("base_url") or info["base_url"]
        model = body.get("model") or info["default_model"]
    else:
        base_url = body.get("base_url", "").strip()
        model = body.get("model", "").strip()
        if not base_url:
            return JSONResponse(status_code=400, content={"detail": "自定义提供商需要提供 base_url"})

    updates = {
        "ANTHROPIC_API_KEY": api_key,
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_MODEL": model,
        "OPENAI_API_KEY": api_key,
        "OPENAI_BASE_URL": base_url,
        "OPENAI_MODEL": model,
    }
    _save_env(updates)
    return {"status": "ok", "provider": provider, "model": model}


# ── 3. 测试 LLM 连接 ────────────────────────────────
@router.post("/api/setup/test-llm")
async def test_llm(body: dict = Body(...)):
    """测试 LLM API 连通性。"""
    api_key = body.get("api_key", os.environ.get("OPENAI_API_KEY", "")).strip()
    base_url = body.get("base_url", os.environ.get("OPENAI_BASE_URL", "")).strip()
    model = body.get("model", os.environ.get("OPENAI_MODEL", "claude-sonnet-5"))

    if not api_key:
        return {"status": "error", "message": "API Key 未提供"}
    if not base_url:
        return {"status": "error", "message": "Base URL 未提供"}

    try:
        import requests
        url = base_url.rstrip("/") + "/v1/chat/completions"
        resp = requests.post(url, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }, json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }, timeout=15)
        if resp.status_code == 200:
            return {"status": "ok", "message": f"连接成功，模型 {model} 可用"}
        elif resp.status_code == 401:
            return {"status": "error", "message": "API Key 无效，请检查"}
        elif resp.status_code == 404:
            return {"status": "error", "message": f"模型 {model} 不可用或 URL 不正确"}
        else:
            return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:100]}"}
    except requests.ConnectionError:
        return {"status": "error", "message": f"无法连接到 {base_url}，请检查网络和 URL"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


# ── 4. 配置 eZ-PLM ──────────────────────────────────
@router.post("/api/setup/ezplm")
async def set_ezplm(body: dict = Body(...)):
    """配置 eZ-PLM 连接。body: { api_key: str, base_url?: str }"""
    api_key = body.get("api_key", "").strip()
    base_url = body.get("base_url", "https://www.ezplm.cn").strip()
    if not api_key:
        return JSONResponse(status_code=400, content={"detail": "eZ-PLM API Key 不能为空"})
    _save_env({"EZPLM_API_KEY": api_key, "EZPLM_BASE_URL": base_url})
    return {"status": "ok"}


# ── 5. 保存用户角色信息 ─────────────────────────────
@router.post("/api/setup/user-profile")
async def save_user_profile(body: dict = Body(...)):
    """保存用户角色信息到 USER.md。body: { name: str, role: str, background?: str }"""
    name = body.get("name", "").strip()
    role = body.get("role", "").strip()
    bg = body.get("background", "").strip()
    if not name or not role:
        return JSONResponse(status_code=400, content={"detail": "姓名和角色不能为空"})
    content = f"""# {name} 的用户画像

> 通过 Onboarding 向导自动生成

**姓名**：{name}
**角色**：{role}
**背景**：{bg or "未填写"}

## 项目偏好

- 当前角色：{role}
- 技术背景：{bg or "通用"}
"""
    user_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "memory", "USER.md")
    os.makedirs(os.path.dirname(user_path), exist_ok=True)
    with open(user_path, "w") as f:
        f.write(content)
    return {"status": "ok"}


# ── 6. 完整健康诊断 ──────────────────────────────────
@router.get("/api/health/full")
async def full_health():
    """全面的环境诊断，适合启动时调用。"""
    env = _load_env()
    checks = {}

    # Python
    import sys
    checks["python"] = {"status": "ok", "version": sys.version}

    # 后端服务自检
    checks["backend"] = {"status": "ok"}

    # LLM
    llm_key = env.get("OPENAI_API_KEY") or env.get("ANTHROPIC_API_KEY", "")
    checks["llm"] = {
        "status": "ok" if llm_key else "missing",
        "message": "已配置" if llm_key else "API Key 未配置",
    }

    # eZ-PLM
    ezplm_key = env.get("EZPLM_API_KEY", "")
    checks["ezplm"] = {
        "status": "ok" if ezplm_key else "missing",
        "message": "已配置" if ezplm_key else "API Key 未配置",
    }

    # 端口检查
    import socket
    for port, name in [(8000, "后端"), (3000, "前端")]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", port))
            checks[name] = {"status": "available", "message": f"端口 {port} 可用"}
        except OSError:
            checks[name] = {"status": "in_use", "message": f"端口 {port} 已被占用"}
        finally:
            s.close()

    # 关键目录
    for d in ["app", "frontend/web", "memory"]:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), d)
        checks[d] = {"status": "ok" if os.path.exists(path) else "missing"}

    # 磁盘空间
    import shutil
    usage = shutil.disk_usage(os.path.dirname(os.path.dirname(__file__)))
    checks["disk"] = {
        "status": "ok" if usage.free > 500 * 1024 * 1024 else "low",
        "free_gb": round(usage.free / (1024**3), 1),
    }

    overall = all(c.get("status") == "ok" for c in checks.values())
    return {"status": "ok" if overall else "warning", "checks": checks}


# ── 7. 权限管理 ──────────────────────────────────────
@router.get("/api/permissions")
async def get_permissions():
    """获取当前权限级别和定义。"""
    return {
        "current": _current_permission,
        "levels": {k: {"label": v["label"], "desc": v["desc"]} for k, v in PERMISSION_LEVELS.items()},
    }


@router.post("/api/permissions")
async def set_permissions(body: dict = Body(...)):
    """设置权限级别。body: { level: str }"""
    global _current_permission
    level = body.get("level", "")
    if level not in PERMISSION_LEVELS:
        return JSONResponse(status_code=400, content={"detail": f"不支持的权限级别: {level}，可选: {list(PERMISSION_LEVELS.keys())}"})
    _current_permission = level
    # 同步到环境变量，供下游工具使用
    os.environ["EZMANBO_PERMISSION"] = level
    return {"status": "ok", "level": level, "allowed_tools": PERMISSION_LEVELS[level]["allowed_tools"]}
