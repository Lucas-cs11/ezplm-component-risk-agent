"""权限检查模块。工具调用前检查当前权限级别是否允许。"""

import os

_PERMISSION_TOOLS = {
    "readonly": {"Read", "Grep", "Glob", "WebSearch"},
    "standard": {"Read", "Grep", "Glob", "Write", "Edit", "Bash"},
    "full": {"Read", "Grep", "Glob", "Write", "Edit", "Bash"},
}


def get_current_level() -> str:
    return os.environ.get("EZMANBO_PERMISSION", "standard")


def is_tool_allowed(tool_name: str) -> bool:
    """检查当前权限是否允许调用指定工具。"""
    level = get_current_level()
    allowed = _PERMISSION_TOOLS.get(level, _PERMISSION_TOOLS["standard"])
    return tool_name in allowed
