"""
memory.py — 四类型记忆系统（参照 Claude Code 架构）

类型分类：
  user      — 用户角色、偏好、技术背景
  feedback  — 用户对 Agent 行为的纠正与认可
  project   — 非代码可推导的项目上下文
  reference — 外部系统指针

索引层：memory/MEMORY.md（每次对话加载，上限 200 行/25KB）
召回层：find_relevant_memories() — DeepSeek 侧查询，筛选 <= 5 条最相关记忆
写入约束：只存无法从当前项目状态推导的信息
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MEM_DIR = _PROJECT_ROOT / "memory"
_MEMORY_LOCK = threading.Lock()  # P2-3: serialize concurrent file writes
_ENTRYPOINT = _MEM_DIR / "MEMORY.md"
_MAX_ENTRYPOINT_LINES = 200
_MAX_ENTRYPOINT_BYTES = 25_000

MEMORY_TYPES = ("user", "feedback", "project", "reference")

# ═══════════════════════════════════════════════════════════════
# 读取
# ═══════════════════════════════════════════════════════════════

def read_soul() -> str:
    """读取 Soul 身份文件。"""
    path = _MEM_DIR / "SOUL.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""

def read_entrypoint() -> str:
    """读取 MEMORY.md 索引。"""
    if not _ENTRYPOINT.exists():
        return ""
    text = _ENTRYPOINT.read_text(encoding="utf-8")
    lines = text.split("\n")
    # 截断保护
    if len(lines) > _MAX_ENTRYPOINT_LINES or len(text.encode()) > _MAX_ENTRYPOINT_BYTES:
        text = "\n".join(lines[:_MAX_ENTRYPOINT_LINES])
        text = text[: _MAX_ENTRYPOINT_BYTES]
        text += f"\n\n> 索引已被截断（原始 {len(lines)} 行 / {len(text.encode())} 字节）。请清理旧条目或拆分记忆文件。"
    return text

def read_memory_file(name: str) -> str:
    """读取指定记忆文件内容。"""
    path = _MEM_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""

def get_soul_summary() -> str:
    """提取 Soul 核心信息（精简版，用于 prompt 注入）。"""
    soul = read_soul()
    if not soul:
        return ""
    name = _extract_field(soul, "名称", "eZmanbo")
    title = _extract_field(soul, "头衔", "智能元器件选型助理")
    style = _extract_section(soul, "沟通风格")
    constraints = _extract_section(soul, "行为约束")
    parts = [p for p in [
        f"你是{name}，{title}。",
        f"沟通原则：{style[:200]}" if style else "",
        f"行为约束：{constraints[:200]}" if constraints else "",
    ] if p]
    return "\n".join(parts)

def get_user_context() -> str:
    """获取用户上下文（优先从 user.md 读取，回退 USER.md）。"""
    for name in ("user", "USER"):
        content = read_memory_file(name)
        if content and "尚未设定" not in content:
            return _strip_frontmatter(content)[:500]
    return "用户画像尚未建立。"

def get_project_context() -> str:
    """获取项目上下文。"""
    content = read_memory_file("project")
    return _strip_frontmatter(content)[:400] if content else ""

def build_system_context() -> str:
    """构建完整的系统上下文（Soul + Memory + 项目 + 环境上下文）。"""
    index = read_entrypoint()
    project = get_project_context()
    env = _get_environment_context()
    return f"""[Memory Index]
{index[:1500]}

[Project Context]
{project if project else '(无项目上下文)'}

[Environment]
{env}
"""

def _get_environment_context() -> str:
    """获取运行环境上下文（OS、Shell、Python 版本等）。"""
    import platform, sys
    parts = [
        f"OS: {platform.system()} {platform.release()}",
        f"Shell: {os.environ.get('SHELL', os.environ.get('COMSPEC', 'unknown'))}",
        f"Python: {sys.version.split()[0]}",
        f"CWD: {os.getcwd()}",
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
    ]
    return "\n".join(parts)

# ═══════════════════════════════════════════════════════════════
# 智能召回（DeepSeek 侧查询）
# ═══════════════════════════════════════════════════════════════

def find_relevant_memories(query: str, max_count: int = 5) -> List[str]:
    """使用 DeepSeek 侧查询从记忆索引中筛选最相关的记忆。

    返回: 记忆文件名列表（不含 .md 后缀），如 ["user", "project"]
    """
    index = read_entrypoint()
    if not index.strip():
        return ["user"]

    # 提取索引中的文件名
    files = re.findall(r'\[.*?\]\((\w+)\.md\)', index)
    if not files:
        return ["user"]

    # 规则筛选：query 命中文件名或描述 → 直接返回
    matched = []
    for f in files:
        if f.lower() in query.lower() or _keyword_match(query, f):
            matched.append(f)
    if matched:
        return matched[:max_count]

    # 全部 <= 5 条 → 直接返回
    if len(files) <= max_count:
        return files

    # LLM 侧查询
    return _llm_memory_select(query, files, max_count)


def _keyword_match(query: str, filename: str) -> bool:
    """简单的关键词匹配。"""
    keywords: Dict[str, List[str]] = {
        "user": ["用户", "偏好", "习惯", "背景"],
        "project": ["项目", "进度", "规范", "约束"],
        "feedback": ["纠正", "反馈", "不要", "应该", "上次"],
        "reference": ["参考", "链接", "数据源", "手册"],
    }
    for kw in keywords.get(filename, []):
        if kw in query:
            return True
    return False


def _llm_memory_select(query: str, files: List[str], max_n: int) -> List[str]:
    """DeepSeek 侧查询：从文件列表中选出最相关的。"""
    try:
        from .llm_client import call_openai_chat_text
        import json as _json

        manifest = "\n".join(f"- {f}" for f in files)
        prompt = f"""从以下记忆文件中选出与查询最相关的最多{max_n}个。

Query: {query}

Available memories:
{manifest}

Only return a JSON array of filenames: ["file1", "file2"]"""

        resp = call_openai_chat_text(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        start = resp.find("[")
        end = resp.rfind("]")
        if start != -1 and end != -1:
            selected = _json.loads(resp[start:end + 1])
            return [f for f in selected if f in files][:max_n]
    except Exception:
        pass
    return files[:max_n]

# ═══════════════════════════════════════════════════════════════
# 写入（四种类型 + 写入约束）
# ═══════════════════════════════════════════════════════════════

def record_user_fact(key: str, value: str):
    """记录用户画像事实（type: user）。"""
    _append_to("user", f"- **{key}**：{value} （{_ts()}）")

def record_correction(issue: str, correction: str):
    """记录用户纠正（type: feedback）。"""
    _append_to("feedback", f"- [{_ts()}] **纠正**: {issue} → **正确做法**: {correction}")

def record_confirmation(practice: str):
    """记录用户认可（type: feedback）。"""
    _append_to("feedback", f"- [{_ts()}] **认可**: {practice}")

def record_project_context(fact: str):
    """记录项目上下文（type: project）。"""
    _append_to("project", f"- [{_ts()}] {fact}")

def record_selection(user_input: str, result_summary: str):
    """记录选型历史到 user.md 和 project.md。"""
    ts = _ts()
    # user.md: 最近选型
    entry = f"- [{ts}] {user_input[:80]} → {result_summary[:120]}"
    _prepend_section("user", "## 最近选型", entry, keep=5)
    # project.md: 选型活动
    _append_to("project", f"- [{ts}] 完成选型：{user_input[:80]} → {result_summary}")

def update_user_name(name: str):
    """更新用户称呼。"""
    _update_field("user", "称呼", name)

def update_dialog_summary(summary: str):
    """更新对话摘要。"""
    _write_section("user", "## 对话摘要", f"[{_ts()}] {summary[:300]}")

# ═══════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def _strip_frontmatter(text: str) -> str:
    """去掉 YAML frontmatter。"""
    return re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL).strip()

def _extract_field(text: str, field: str, default: str = "") -> str:
    m = re.search(rf"\*\*{field}\*\*[：:]\s*(.+)", text)
    return m.group(1).strip() if m else default

def _extract_section(text: str, section: str) -> str:
    pattern = rf"##\s+{section}\s*\n(.*?)(?=\n##\s|\Z)"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""

def _read_section(filename: str, section: str) -> str:
    content = read_memory_file(filename)
    return _extract_section(_strip_frontmatter(content), section)

def _write_section(filename: str, section: str, content: str):
    with _MEMORY_LOCK:
        path = _MEM_DIR / f"{filename}.md"
        if not path.exists():
            path.write_text(f"---\ntype: {filename}\ndescription: \n---\n\n# {filename}\n\n## {section}\n\n{content}\n", encoding="utf-8")
            return
        text = path.read_text(encoding="utf-8")
        pattern = rf"(##\s+{section}\s*\n)(.*?)(?=\n##\s|\Z)"
        replacement = rf"\1{content}\n"
        new_text = re.sub(pattern, replacement, text, flags=re.DOTALL)
        if new_text == text:
            new_text = text.rstrip() + f"\n\n## {section}\n\n{content}\n"
        path.write_text(new_text, encoding="utf-8")

def _prepend_section(filename: str, section: str, entry: str, keep: int = 5):
    """在 section 最前面插入条目，保留最近 N 条。"""
    current = _read_section(filename, section)
    lines = [l for l in current.strip().split("\n") if l.strip().startswith("-")]
    lines = [entry] + lines
    _write_section(filename, section, "\n".join(lines[:keep]))

def _append_to(filename: str, line: str):
    with _MEMORY_LOCK:
        path = _MEM_DIR / f"{filename}.md"
        if not path.exists():
            path.write_text(f"---\ntype: {filename}\ndescription: \n---\n\n# {filename}\n\n{line}\n", encoding="utf-8")
            return
        text = path.read_text(encoding="utf-8")
        path.write_text(text.rstrip() + f"\n{line}\n", encoding="utf-8")

def _update_field(filename: str, field: str, value: str):
    with _MEMORY_LOCK:
        path = _MEM_DIR / f"{filename}.md"
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        pattern = rf"(\*\*{field}\*\*[：:]\s*)(.+)"
        new_text = re.sub(pattern, rf"\1{value}", text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
