#!/usr/bin/env python3
"""
eZmanbo CLI — Claude Code 风格终端对话 REPL

迁移自 Claude Code 2.7.1 的 UI 设计模式:
  - 全宽水平分隔线（─────）夹住每条消息 — 对齐 Claude Code 对话框
  - 底部状态栏：状态文字 + 分隔线 + ❯ 提示符
  - Slash 命令系统
  - 流式 SSE 选型 + 对话模式
  - 选型完成后向 Agent 注入上下文，解决上下文丢失问题
"""

import sys
import os
import json
import argparse

# ── 命令行参数解析（模型管理 + 思考深度）─────────────────────
_API_BASE = os.environ.get("EZMANBO_API_BASE", "http://localhost:8000")

def _list_models():
    """从后端 API 获取可用模型列表。"""
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"{_API_BASE}/api/models", timeout=5)
        data = json.loads(resp.read())
        print("可用模型：")
        for m in data.get("models", []):
            marker = "  → " if m["id"] == data.get("active") else "    "
            print(f"{marker}{m['id']}  —  {m['name']}")
            print(f"      {m['description']}")
        print(f"\n当前激活：{data.get('active', '未知')}")
    except Exception as e:
        print(f"获取模型列表失败: {e}")
    sys.exit(0)

def _switch_model(model_id: str):
    """通过后端 API 切换模型。"""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{_API_BASE}/api/models/switch",
            data=json.dumps({"model": model_id}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        print(f"已切换至: {data.get('active', model_id)}")
    except Exception as e:
        print(f"切换模型失败: {e}")
    sys.exit(0)

parser = argparse.ArgumentParser(description="eZmanbo CLI — 电子元器件智能选型与对话")
parser.add_argument("--list-models", action="store_true", help="列出可用模型")
parser.add_argument("--switch-model", type=str, help="切换到指定模型（如 deepseek-v4-pro）")
parser.add_argument("--thinking", type=str, default="default",
                    choices=["off", "default", "contemplation", "exhaustive"],
                    help="思考深度（off=关闭, default=标准, contemplation=深入, exhaustive=穷究）")
args, remaining = parser.parse_known_args()

if args.list_models:
    _list_models()
if args.switch_model:
    _switch_model(args.switch_model)

# 将思考深度写入环境变量供下游模块读取
os.environ["EZMANBO_THINKING_DEPTH"] = args.thinking
import urllib.request
import urllib.error
import socket
import time
from typing import Optional
from datetime import datetime

API_BASE = "http://127.0.0.1:8000"
SESSION_ID: Optional[str] = None
REQUEST_TIMEOUT = 180
THINKING_DEPTH = "default"   # off | default | contemplation | exhaustive
_has_active_selection = False  # 跟踪当前会话是否已完成过选型（用于 adjustment 检测）

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner
from rich import box

# 新 UI 组件
from ezmanbo_cli.theme import get_theme, Theme
from ezmanbo_cli.figures import (
    BLACK_CIRCLE, BULLET_OPERATOR, UP_ARROW, DOWN_ARROW,
    FLAG_ICON, BLOCKQUOTE_BAR, DIAMOND_FILLED, BOX_SEPARATOR,
)
from ezmanbo_cli.header import print_welcome, print_condensed_header
from ezmanbo_cli.statusline import (
    StatusInfo, render_status_line, _format_tokens, _format_cost,
)

console = Console()


# ══════════════════════════════════════════════════════════════════
# 主题配色（动态，响应 /theme 切换）
# ══════════════════════════════════════════════════════════════════

_is_dark_theme = True

def _t() -> Theme:
    return get_theme(_is_dark_theme)

def _s(key: str) -> str:
    """获取主题色 key（动态查询，响应主题切换）。"""
    t = _t()
    mapping = {
        "brand":   t.brand,
        "accent":  t.accent,
        "text":    t.text,
        "subtle":  t.inactive,
        "dim":     t.dim,
        "user":    t.brand,
        "success": t.success,
        "warn":    t.warning,
        "error":   t.error,
        "border":  t.inactive,
        "info":    t.info,
    }
    return mapping.get(key, t.text)


# ══════════════════════════════════════════════════════════════════
# 状态跟踪
# ══════════════════════════════════════════════════════════════════

_status = StatusInfo(
    model_display="DeepSeek-V3",
    context_window_size=128000,
)

def _update_status(**kw):
    global _status
    for k, v in kw.items():
        if hasattr(_status, k):
            setattr(_status, k, v)


# ══════════════════════════════════════════════════════════════════
# Slash 命令表
# ══════════════════════════════════════════════════════════════════

SLASH_COMMANDS = {
    "quit":     ("Exit eZmanbo",                        lambda: "quit"),
    "exit":     ("Exit eZmanbo",                        lambda: "quit"),
    "help":     ("Show all commands",                   lambda: _cmd_help()),
    "clear":    ("Clear session",                       lambda: _cmd_clear()),
    "new":      ("New session",                         lambda: _cmd_new()),
    "save":     ("Export report as MD  [filename]",     lambda: _cmd_save()),
    "export":   ("Download BOM Excel",                  lambda: _cmd_bom()),
    "import":   ("Import file as input  <filepath>",    lambda: "[IMPORT_NEEDS_PATH]"),
    "compact":  ("Summarize conversation",              lambda: _cmd_compact()),
    "status":   ("Check backend status",                lambda: _cmd_status()),
    "theme":    ("Toggle dark/light theme",             lambda: _cmd_theme()),
    "thinking": ("Set thinking depth  [off|default|contemplation|exhaustive]",
                 lambda: _cmd_thinking("")),
}


def _cmd_help():
    t = Table(box=box.SIMPLE, show_header=False,
              border_style=_s("border"), padding=(0, 2))
    t.add_column("Command", width=12)
    t.add_column("Description")
    for cmd, (desc, _) in sorted(SLASH_COMMANDS.items()):
        t.add_row(
            Text(f"/{cmd}", style=f"bold {_s('brand')}"),
            Text(desc,      style=_s("subtle")),
        )
    console.print(t)
    return ""


def _cmd_clear():
    global SESSION_ID, _has_active_selection
    SESSION_ID = None
    _has_active_selection = False
    _update_status(used_tokens=0, total_cost_usd=0.0, context_used_pct=0.0)
    return f"{DIAMOND_FILLED} Session cleared."


def _cmd_new():
    global SESSION_ID, _has_active_selection
    SESSION_ID = None
    _has_active_selection = False
    _update_status(used_tokens=0, total_cost_usd=0.0, context_used_pct=0.0)
    return f"{DIAMOND_FILLED} New session."


def _cmd_compact():
    global SESSION_ID
    r = _api("/agent/chat", {
        "user_input": "请将当前对话历史总结为一段简洁摘要（不超过150字），保留关键器件型号和参数。",
        "session_id": SESSION_ID,
    })
    return r.get("response", "") or r.get("output", "") or "Compact done."


def _cmd_export():
    return "Export chat: use /save in the Web UI for full Markdown export."


def _cmd_save(args: str = "") -> str:
    """导出最新选型报告（BOM + 风险评估）为 Markdown 文件。

    用法:
        /save              → 保存到 ~/Downloads/eZmanbo_YYYYMMDD_HHMMSS.md
        /save my_report    → 保存到 ~/Downloads/my_report.md
    """
    from datetime import datetime as _dt

    # ── 并发拉取两类报告 ──────────────────────────────────────────
    reports: dict[str, str] = {}
    for rtype in ("bom", "risk"):
        try:
            req = urllib.request.Request(
                f"{API_BASE}/report/{rtype}", method="GET")
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
                reports[rtype] = data.get("content", "")
        except Exception:
            reports[rtype] = ""

    if not any(reports.values()):
        return f"{FLAG_ICON} 暂无报告可导出，请先完成一次选型分析。"

    # ── 组装 Markdown ──────────────────────────────────────────────
    sections: list[str] = [
        f"# eZmanbo 选型报告\n",
        f"> 导出时间：{_dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
    ]
    if reports.get("bom"):
        sections.append("\n---\n\n## BOM 物料清单\n")
        sections.append(reports["bom"])
    if reports.get("risk"):
        sections.append("\n\n---\n\n## 风险评估报告\n")
        sections.append(reports["risk"])

    # ── 写入文件 ──────────────────────────────────────────────────
    ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    fname = args.strip() if args.strip() else f"eZmanbo_{ts}"
    if not fname.endswith(".md"):
        fname += ".md"
    path = os.path.expanduser(f"~/Downloads/{fname}")

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(sections))
        return f"{DIAMOND_FILLED} 报告已导出 → {path}"
    except Exception as e:
        return f"{FLAG_ICON} 写入失败: {e}"


def _cmd_bom():
    try:
        req = urllib.request.Request(f"{API_BASE}/export/bom", method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        path = os.path.expanduser("~/Downloads/BOM.xlsx")
        with open(path, "wb") as f:
            f.write(data)
        return f"{DIAMOND_FILLED} BOM exported → {path}"
    except Exception as e:
        return f"{FLAG_ICON} Export failed: {e}"


# ══════════════════════════════════════════════════════════════════
# 文件导入辅助 — /import <filepath>
# ══════════════════════════════════════════════════════════════════

_IMPORT_TEXT_EXTS = {".txt", ".md", ".json", ".csv"}
_IMPORT_BIN_EXTS  = {".pdf", ".xlsx", ".xls"}
_IMPORT_ALL_EXTS  = _IMPORT_TEXT_EXTS | _IMPORT_BIN_EXTS


def _import_file(filepath: str) -> tuple[str, str]:
    """解析本地文件，返回 (text_content, error_message)。

    - 文本类（.txt .md .json .csv）：直接读取，最多 8000 字符。
    - 二进制类（.pdf .xlsx .xls）：上传到后端 /upload/parse 提取文本。
    - 其他类型：返回不支持的错误。
    """
    path = os.path.expanduser(filepath.strip().strip("'\""))
    if not os.path.exists(path):
        return "", f"文件不存在: {path}"

    ext = os.path.splitext(path)[1].lower()
    size_kb = os.path.getsize(path) / 1024

    # ── 文本类：直接读取 ──────────────────────────────────────────
    if ext in _IMPORT_TEXT_EXTS:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(8000)
            truncated = os.path.getsize(path) > 8000
            if truncated:
                content += f"\n\n[注：文件较大（{size_kb:.0f} KB），已截取前 8000 字符]"
            return content, ""
        except Exception as e:
            return "", f"读取失败: {e}"

    # ── 二进制类：上传后端解析 ────────────────────────────────────
    elif ext in _IMPORT_BIN_EXTS:
        try:
            import mimetypes
            boundary = "----eZmanboFormBoundary7x"
            fname = os.path.basename(path)
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"

            with open(path, "rb") as f:
                fdata = f.read()

            # Multipart 构建（不依赖 requests，只用标准库）
            header = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
            footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
            body = header + fdata + footer

            req = urllib.request.Request(
                f"{API_BASE}/upload/parse",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                result = json.loads(r.read())

            content = result.get("content", "")
            if not content:
                return "", "后端解析返回空内容（文件可能无文本层）"

            if result.get("truncated"):
                content += f"\n\n[注：文件较大，后端已截取前 5000 字符]"
            return content, ""

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return "", f"后端返回 HTTP {e.code}: {body[:200]}"
        except Exception as e:
            return "", f"上传解析失败: {e}"

    else:
        supported = "  ".join(sorted(_IMPORT_ALL_EXTS))
        return "", f"不支持的文件类型 {ext}\n  支持: {supported}"


def _cmd_status():
    if _is_port_open():
        return f"{BLACK_CIRCLE} Backend online — {API_BASE}/docs"
    return f"{FLAG_ICON} Backend OFFLINE — start with: ezmanbo start"


def _cmd_theme():
    global _is_dark_theme
    _is_dark_theme = not _is_dark_theme
    label = "dark" if _is_dark_theme else "light"
    return f"{BLACK_CIRCLE} Theme → {label}"


_THINKING_LEVELS = ("off", "default", "contemplation", "exhaustive")
_THINKING_LABELS = {
    "off":           "关闭（直接回复）",
    "default":       "默认（基础分步推理）",
    "contemplation": "沉思（详细链式推理）",
    "exhaustive":    "穷究（穷尽分析，最高深度）",
}

def _cmd_thinking(args: str = "") -> str:
    """设置思考深度：/thinking [off|default|contemplation|exhaustive]"""
    global THINKING_DEPTH
    lvl = args.strip().lower() if args.strip() else ""
    if lvl in _THINKING_LEVELS:
        THINKING_DEPTH = lvl
        return f"{DIAMOND_FILLED} 思考深度 → {lvl}（{_THINKING_LABELS[lvl]}）"
    # 未指定级别：展示当前状态 + 可选项
    lines = [f"当前思考深度：{THINKING_DEPTH}（{_THINKING_LABELS.get(THINKING_DEPTH, '')}）", ""]
    for k, label in _THINKING_LABELS.items():
        marker = "●" if k == THINKING_DEPTH else " "
        lines.append(f"  {marker} {k:<14} — {label}")
    lines.append("")
    lines.append("用法：/thinking [off|default|contemplation|exhaustive]")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# HTTP 工具
# ══════════════════════════════════════════════════════════════════

def _api(path: str, data: dict, timeout: int = 60) -> dict:
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def _classify(text: str) -> dict:
    return _api("/classify", {"user_input": text, "has_active_selection": _has_active_selection})


def _is_port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", 8000)) == 0


# ══════════════════════════════════════════════════════════════════
# UI 工具函数 — Claude Code 风格分隔线 + 消息块
# ══════════════════════════════════════════════════════════════════

def _hr(style: str = "") -> None:
    """全宽水平分隔线（dim）。"""
    console.print(Text("─" * console.width, style=style or _s("dim")))


def _print_response_header(title: str = "eZmanbo") -> None:
    """AI 回复块顶部：品牌标识 + 分隔线。

    对齐 Claude Code: 每条 AI 消息上方有来源标识 + 水平线。
    """
    console.print()
    label = Text()
    label.append(f"  {DIAMOND_FILLED} ", style=f"bold {_s('brand')}")
    label.append(title, style=_s("brand"))
    console.print(label)
    _hr()


def _print_response_footer(status_line: str = "", style: str = "") -> None:
    """AI 回复块底部：可选状态行 + 分隔线。"""
    console.print()
    if status_line:
        console.print(Text(f"  {status_line}", style=style or _s("subtle")))
    _hr()
    console.print()


# ══════════════════════════════════════════════════════════════════
# 用户消息回显 — Claude Code 用户消息块风格
# ══════════════════════════════════════════════════════════════════

def _echo_user_input(text: str) -> None:
    """回显用户输入 — 对齐 Claude Code userMessageBlock。

    上方分隔线 + ❯ 前缀 + 消息文本。
    """
    console.print()
    _hr()
    msg = Text()
    msg.append(" ❯ ", style=f"bold {_s('user')}")
    msg.append(text, style=_s("text"))
    console.print(msg)
    console.print()


# ══════════════════════════════════════════════════════════════════
# 底部提示符区域 — 状态栏 + 分隔线（Claude Code 底部夹层）
# ══════════════════════════════════════════════════════════════════

def _print_status_bar() -> None:
    """打印底部状态栏。

    布局：
      [空行]
      DeepSeek-V3  │  Context 2%  │  $0.0010
      ─────────────────────────────────────── ← 提示符上方的分隔线
    """
    console.print()
    sl = render_status_line(_status, width=console.width)
    if sl.plain.strip():
        status_text = Text("  ") + sl
        console.print(status_text)
    _hr()


# ══════════════════════════════════════════════════════════════════
# 上下文注入 — 选型完成后向 Agent 会话同步选型结果
# ══════════════════════════════════════════════════════════════════

def _inject_selection_context(summary: str) -> None:
    """将选型摘要注入 Agent 会话，避免后续对话丢失上下文。

    调用 /agent/init_session 直接写入 AI 历史消息（无 LLM 调用），
    后续 /agent/chat 请求会基于此上下文作答，不再触发自我介绍。
    """
    global SESSION_ID
    if not summary.strip():
        return
    try:
        r = _api("/agent/init_session", {
            "context": f"用户刚完成选型，选型结果摘要如下：\n{summary[:1000]}\n\n"
                       "后续用户追问时（如'就选第1款'、'有国产替代吗'），请基于此作答，不要重新介绍自己。",
        }, timeout=15)
        new_sid = r.get("session_id")
        if new_sid:
            SESSION_ID = new_sid
    except Exception:
        pass  # 注入失败不影响主流程


# ══════════════════════════════════════════════════════════════════
# 流式选型 (SSE) — Claude Code Spinner + HR 消息块风格
# ══════════════════════════════════════════════════════════════════

def _handle_sse_event(
    evt: str, d: dict, live: Live,
    collected: list, state: dict,
) -> None:
    """处理 SSE 事件：更新 Live 进度条 / 收集文本 / 记录完成状态。"""

    if evt == "cache_hit":
        if d.get("hit"):
            sim = d.get("similarity", 0)
            live.update(Spinner("dots",
                text=f"  缓存命中（相似度 {sim:.2%}），正在加载…",
                style=_s("info")))

    elif evt == "stage":
        stage_map = {
            "parse":    "正在解析需求…",
            "search":   "正在搜索候选器件…",
            "score":    "正在计算评分…",
            "evidence": "正在构建证据链…",
            "risk":     "正在评估风险…",
            "report":   "正在生成报告…",
        }
        label = stage_map.get(d.get("stage", ""), f"{d.get('stage', '')}…")
        status_text = d.get("status", "")
        if "relaxing" in status_text:
            label = f"候选不足，自动放宽约束 — {status_text}"
        live.update(Spinner("dots", text=f"  {label}", style=_s("subtle")))

    elif evt == "parse_done":
        n = d.get("fields_parsed", 0)
        live.update(Spinner("dots",
            text=f"  需求解析完成（{n} 个参数字段）",
            style=_s("subtle")))

    elif evt == "search_done":
        cnt = d.get("candidate_count", 0)
        live.update(Spinner("dots",
            text=f"  搜索完成，共 {cnt} 条候选",
            style=_s("subtle")))

    elif evt == "score_update":
        pn  = d.get("part_number", "?")
        idx = d.get("index", "?")
        tot = d.get("total", "?")
        sc  = d.get("total_score", "")
        lvl = d.get("recommendation_level", "")
        tag = {"recommended": "★", "backup": "△", "not_recommended": "✗"}.get(lvl, " ")
        live.update(Spinner("dots",
            text=f"  评分 [{idx}/{tot}] {tag} {pn}（{sc}分）",
            style=_s("subtle")))

    elif evt == "evidence_done":
        cnt  = d.get("evidence_count", 0)
        conf = d.get("avg_confidence", 0)
        live.update(Spinner("dots",
            text=f"  证据链完成（{cnt} 条，置信度 {conf:.2f}）",
            style=_s("subtle")))

    elif evt == "risk_done":
        lvl  = d.get("overall_risk_level", "?").upper()
        high = d.get("high", 0)
        med  = d.get("medium", 0)
        live.update(Spinner("dots",
            text=f"  风险评估完成 — {lvl}（高 {high} / 中 {med}）",
            style=_s("subtle")))

    elif evt == "warning":
        live.stop()
        console.print(Text(f"  ⚠  {d.get('message', '')}", style=_s("warn")))
        live.start()

    elif evt == "text_delta":
        txt = d.get("text", "")
        if txt.strip():
            collected.append(txt)

    elif evt == "thinking_delta":
        txt = d.get("text", "")
        if txt.strip():
            state.setdefault("thinking_lines", []).append(txt)

    elif evt == "done":
        live.stop()
        state["done_data"] = d
        _update_status(
            total_cost_usd=_status.total_cost_usd + 0.001,
            used_tokens=_status.used_tokens + 2000,
            context_used_pct=min(100, _status.context_used_pct + 2),
        )

    elif evt == "error":
        live.stop()
        console.print()
        _hr(_s("error"))
        console.print(Text(
            f"  ✗  {d.get('message', '未知错误')}",
            style=_s("error")))
        _hr(_s("error"))
        console.print()


def _stream_selection(text: str) -> str:
    """流式选型 SSE 请求，返回收集到的 Markdown 摘要文本。"""
    url = f"{API_BASE}/analyze/stream"
    req = urllib.request.Request(
        url, data=json.dumps({"user_input": text, "thinking_depth": THINKING_DEPTH}).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "text/event-stream"},
        method="POST")

    spinner = Spinner("dots", text="  正在分析需求…", style=_s("subtle"))
    live = Live(spinner, console=console, refresh_per_second=10, transient=True)
    live.start()
    collected_md: list[str] = []
    state: dict = {"done_data": None}

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            buf = b""
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    idx = buf.index(b"\n\n")
                    block = buf[:idx].decode("utf-8", errors="replace")
                    buf = buf[idx + 2:]
                    evt = ""
                    for line in block.split("\n"):
                        if line.startswith("event:"):
                            evt = line[6:].strip()
                        elif line.startswith("data:"):
                            try:
                                d = json.loads(line[5:])
                                _handle_sse_event(evt, d, live, collected_md, state)
                            except json.JSONDecodeError:
                                pass
    except Exception as e:
        live.stop()
        console.print()
        _hr(_s("error"))
        console.print(Text(f"  ✗  Connection error: {e}", style=_s("error")))
        _hr(_s("error"))
        console.print()
        return ""

    # ── 渲染最终报告（Claude Code HR 夹层风格）──────────────────
    if collected_md:
        live.stop()

        # ── 思考过程块（仅 thinking_depth != off 且有内容时显示）───
        thinking_lines = state.get("thinking_lines", [])
        if thinking_lines and THINKING_DEPTH != "off":
            console.print()
            _hr(_s("dim"))
            label = Text()
            label.append(f"  {DIAMOND_FILLED} ", style=f"bold {_s('dim')}")
            label.append("思考过程", style=_s("dim"))
            console.print(label)
            _hr(_s("dim"))
            for tl in thinking_lines:
                console.print(Text(f"  {tl}", style=f"dim italic"))
            console.print()

        _print_response_header("eZmanbo  ·  选型报告")
        console.print(Markdown("\n".join(collected_md)))

        # 从 done 事件提取状态信息
        done_d = state.get("done_data") or {}
        risk = done_d.get("overall_risk", "").upper()
        rc = (_s("success") if risk == "LOW"
              else _s("warn") if risk == "MEDIUM"
              else _s("error") if risk in ("HIGH", "UNKNOWN") else _s("subtle"))
        parts = []
        if risk:
            parts.append(f"{risk} risk")
        rec = done_d.get("recommended_count")
        if rec is not None:
            parts.append(f"{rec} 条推荐")
        elapsed = done_d.get("elapsed_s", 0)
        if elapsed:
            parts.append(f"{elapsed:.1f}s")
        if done_d.get("cache_hit"):
            parts.append("缓存命中")
        status_str = f"  {BULLET_OPERATOR}  ".join(parts)
        _print_response_footer(status_str, rc)

    return "\n".join(collected_md)


# ══════════════════════════════════════════════════════════════════
# 对话模式
# ══════════════════════════════════════════════════════════════════

def _chat(message: str) -> None:
    """非选型对话，调用 Agent ReAct，带 HR 夹层渲染。"""
    global SESSION_ID
    with console.status("  Thinking…", spinner="dots",
                        spinner_style=_s("subtle")):
        r = _api("/agent/chat", {
            "user_input": message,
            "session_id": SESSION_ID,
            "thinking_depth": THINKING_DEPTH,
        }, timeout=120)

    if "error" in r:
        console.print()
        _hr(_s("error"))
        console.print(Text(f"  ✗  {r['error']}", style=_s("error")))
        _hr(_s("error"))
        console.print()
        return

    SESSION_ID = r.get("session_id", SESSION_ID)
    response = r.get("response", "") or r.get("output", "") or ""

    # ── 思考过程：展示工具调用链 ─────────────────────────────
    if THINKING_DEPTH != "off":
        tool_calls = r.get("tool_calls", [])
        if tool_calls:
            console.print()
            _hr(_s("dim"))
            label = Text()
            label.append(f"  {DIAMOND_FILLED} ", style=f"bold {_s('dim')}")
            label.append("思考过程", style=_s("dim"))
            console.print(label)
            _hr(_s("dim"))
            for tc in tool_calls:
                tn = tc.get("tool", "?")
                ta = tc.get("args", {})
                args_str = ", ".join(f"{k}={str(v)[:50]}" for k, v in ta.items())
                console.print(Text(f"  调用工具：{tn}({args_str})", style="dim italic"))
                if THINKING_DEPTH in ("contemplation", "exhaustive") and tc.get("result"):
                    preview = str(tc["result"])[:120].replace("\n", " ")
                    console.print(Text(f"  工具返回：{preview}", style="dim italic"))
            console.print()

    if response.strip():
        _print_response_header()
        console.print(Markdown(response))
        _print_response_footer()

    _update_status(
        total_cost_usd=_status.total_cost_usd + 0.0005,
        used_tokens=_status.used_tokens + 1500,
        context_used_pct=min(100, _status.context_used_pct + 1.5),
    )


# ══════════════════════════════════════════════════════════════════
# REPL — 对齐 Claude Code REPL.tsx 主循环
# ══════════════════════════════════════════════════════════════════

def repl():
    """主 REPL 循环。"""

    # ── 欢迎屏幕 ─────────────────────────────────────────────────
    print_welcome(console, cwd=os.getcwd(),
                  model_name="DeepSeek-V3",
                  billing="API Usage")
    console.print(Text(
        f"  /help 查看命令  ·  /quit 退出  ·  /theme 切换主题  ·  /thinking 设置思考深度",
        style=_s("dim")))

    while True:
        try:
            # ── 底部状态栏 + 分隔线（Claude Code 提示符夹层）────
            _print_status_bar()
            prompt = Text.assemble(
                (" ❯ ", f"bold {_s('user')}"),
                ("", ""),
            )
            user_input = console.input(prompt).strip()

        except (EOFError, KeyboardInterrupt):
            console.print()
            console.print(Text("  Goodbye!", style=_s("subtle")))
            break

        if not user_input:
            continue

        # ── 回显用户消息（HR 夹住 ❯ 文字）────────────────────────
        _echo_user_input(user_input)

        # ── Slash 命令处理 ────────────────────────────────────────
        if user_input.startswith("/"):
            parts = user_input[1:].split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            # ── /save [filename] — 需要可选参数 ──────────────────
            if cmd == "save":
                result = _cmd_save(arg)
                if result.strip():
                    console.print(Text(f"  {result}", style=_s("subtle")))
                console.print()
                continue

            # ── /thinking [level] — 思考深度设置 ─────────────────
            if cmd == "thinking":
                result = _cmd_thinking(arg)
                _print_response_header("思考深度设置")
                console.print(Text(result, style=_s("text")))
                _print_response_footer()
                continue

            # ── /import <filepath> — 文件导入，完整独立流程 ───────
            if cmd == "import":
                if not arg:
                    console.print(Text(
                        f"  {FLAG_ICON} 用法: /import <文件路径>\n"
                        f"  支持格式: .txt  .md  .json  .csv  .pdf  .xlsx  .xls",
                        style=_s("warn")))
                    console.print()
                    continue

                # 解析文件
                with console.status("  读取文件…", spinner="dots",
                                    spinner_style=_s("subtle")):
                    content, err = _import_file(arg)

                if err:
                    console.print()
                    _hr(_s("error"))
                    console.print(Text(f"  ✗  {err}", style=_s("error")))
                    _hr(_s("error"))
                    console.print()
                    continue

                # 显示文件内容预览（前 300 字符）
                fname = os.path.basename(os.path.expanduser(arg))
                preview = content[:300] + ("…" if len(content) > 300 else "")
                console.print()
                console.print(Text(
                    f"  {DIAMOND_FILLED} 已载入文件：{fname}（{len(content)} 字符）",
                    style=f"bold {_s('brand')}"))
                _hr()
                console.print(Text(preview, style=_s("text")))
                _hr()
                console.print()

                # 将文件内容当作用户输入走意图分流
                try:
                    cls = _classify(content[:2000])
                    intent = cls.get("intent", "chat")
                except Exception:
                    intent = "chat"

                if intent in ("selection", "adjustment"):
                    summary = _stream_selection(content[:4000])
                    if summary:
                        _inject_selection_context(summary)
                else:
                    _chat(content[:4000])
                continue

            # ── 通用 slash 命令 ───────────────────────────────────
            if cmd in SLASH_COMMANDS:
                _, handler = SLASH_COMMANDS[cmd]
                result = handler()
                if result == "quit":
                    console.print(Text("  Goodbye!", style=_s("subtle")))
                    break
                if isinstance(result, str) and result.strip() and result != "[IMPORT_NEEDS_PATH]":
                    console.print(Text(f"  {result}", style=_s("subtle")))
                console.print()
                continue
            else:
                console.print(Text(
                    f"  Unknown: /{cmd}. Type /help for commands.",
                    style=_s("warn")))
                console.print()
                continue

        # ── 意图分流（选型 vs 对话） ──────────────────────────────
        try:
            cls = _classify(user_input)
            intent = cls.get("intent", "chat")
        except Exception:
            intent = "chat"

        if intent in ("selection", "adjustment"):
            summary = _stream_selection(user_input)
            # 选型完成后向 Agent 注入上下文，避免下一轮对话丢失
            if summary:
                _inject_selection_context(summary)
                global _has_active_selection
                _has_active_selection = True
        else:
            _chat(user_input)


def single_query(text: str):
    """单次查询（非 REPL）。"""
    try:
        cls = _classify(text)
        intent = cls.get("intent", "chat")
    except Exception:
        intent = "chat"

    _echo_user_input(text)
    if intent in ("selection", "adjustment"):
        summary = _stream_selection(text)
        if summary:
            _inject_selection_context(summary)
    else:
        _chat(text)


# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    import dotenv; dotenv.load_dotenv(override=True)
    _td = os.environ.get("EZMANBO_THINKING_DEPTH", "default")
    if len(sys.argv) > 1:
        single_query(" ".join(sys.argv[1:]))
    else:
        repl()
