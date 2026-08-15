"""
intent_classifier.py — 规则快筛 + LLM 终判 混合意图分类器

架构：
  1. 规则快筛（毫秒级）：最明显的 case
  2. LLM 终判（~1s）：使用 tool calling 实现结构化输出，消除 JSON 解析错误

输出 dict：
  {intent, reasoning, device_category, merged_input, clarify_response,
   no_spec_params, selected_part, confidence}
"""

from __future__ import annotations

import json
import re
from typing import Optional

Intent = str  # "selection" | "chat" | "adjustment" | "clarify" | "selection_choice" | "out_of_scope"

_OUT_OF_SCOPE_REPLY = "抱歉，我只能协助电子元器件选型相关问题。如需选型，请描述您的电压/电流/拓扑需求。"

# 非电子领域的强信号词（出现即判 out_of_scope，不进 LLM）
_OFF_TOPIC_SIGNALS = [
    # 通用编程/算法
    "排序", "算法", "代码", "程序", "python", "javascript", "java", "c++", "c#",
    "写一段", "写个函数", "写个脚本", "实现一个",
    # 通用AI/ML概念
    "rag", "transformer", "机器学习", "深度学习", "神经网络", "大语言模型",
    "llm", "chatgpt", "gpt", "bert", "embedding", "向量数据库",
    "retrieval", "fine-tune", "微调",
    # 完全无关领域
    "菜谱", "做饭", "天气", "股票", "基金", "汇率", "体育", "足球", "篮球",
    "电影", "音乐", "游戏", "历史", "地理", "语文", "数学题",
    "翻译", "作文", "英语", "法语",
]


# ══════════════════════════════════════════════════════════════════
# 规则快筛
# ══════════════════════════════════════════════════════════════════

_VOLTAGE_CONVERSION = re.compile(
    r"(\d+\.?\d*)\s*[vV]\s*(?:转|→|->|to|转换成|转换到|降[压到]|升[压到])\s*(\d+\.?\d*)\s*[vV]"
)
_DCDC_TOPOLOGY = [
    "buck", "boost", "ldo", "dc-dc", "dc_dc", "dcdc",
    "降压", "升压", "稳压", "电源芯片", "电源模块", "转换器",
    "converter", "regulator",
    # 扩展分类：运放 / 电流检测 / 接口IC
    "运放", "放大器", "op-amp", "opamp", "amplifier",
    "电流检测", "电流感测", "功率监控", "ina226", "ina219",
    "收发器", "总线驱动", "can transceiver", "rs-485", "rs485",
    "instrumentation amp", "仪表放大器",
]
_PARTIAL_SELECTION = re.compile(
    r"(?:输入|输出|供电|Vin|Vout|电压|电流|拓扑|grade|等级)"
    r"[\s\S]{0,8}?"
    r"(?:\d+\.?\d*\s*[VvAamWw伏安毫]|[Bb]uck|[Bb]oost|[Ll][Dd][Oo]|降压|升压|稳压)"
)
_CHAT_SIGNALS = [
    "你好", "hello", "hi", "谢谢", "好的", "ok", "明白了",
    "什么是", "怎么", "如何", "为什么", "帮助", "help", "功能", "能做什么",
]
_ADJUSTMENT_SIGNALS = [
    "换个", "换成", "替代", "代替", "改一下", "改成",
    "调整", "修改", "国产替代", "有没有其他",
]


def _is_question(text: str) -> bool:
    """判断文本是否为疑问/查询句式（而非直接的参数描述）。"""
    t = text.strip()
    if t.endswith("？") or t.endswith("?"):
        return True
    question_prefixes = ("有没有", "有哪些", "请问", "能不能", "是否", "什么", "哪种", "哪个",
                          "如何", "怎么", "为什么", "能否", "可以", "能帮", "帮我找",
                          "what", "which", "how", "can you", "is there", "do you")
    tl = t.lower()
    return any(tl.startswith(p) for p in question_prefixes)


def _is_fast_chat(text: str) -> bool:
    t = text.strip().lower()
    if len(t) <= 3:
        return True
    has_domain = any(kw in t for kw in _DCDC_TOPOLOGY) or bool(_PARTIAL_SELECTION.search(text))
    for s in _CHAT_SIGNALS:
        if s in t and not has_domain:
            return True
    if (t.endswith("?") or t.endswith("？")) and not has_domain:
        return True
    return False


def _is_out_of_scope(text: str) -> bool:
    """快速检测明显的离题内容（无电子关键词 + 有强离题信号）。"""
    t = text.strip().lower()
    has_domain = any(kw in t for kw in _DCDC_TOPOLOGY) or bool(_PARTIAL_SELECTION.search(text))
    if has_domain:
        return False
    return any(sig in t for sig in _OFF_TOPIC_SIGNALS)


def _is_fast_selection(text: str) -> bool:
    t = text.lower()
    return bool(_VOLTAGE_CONVERSION.search(text)) and any(kw in t for kw in _DCDC_TOPOLOGY)


def _is_fast_adjustment(text: str) -> bool:
    return any(s in text.lower() for s in _ADJUSTMENT_SIGNALS)


# ══════════════════════════════════════════════════════════════════
# LLM 终判 — tool calling 结构化输出
# ══════════════════════════════════════════════════════════════════

_CLASSIFY_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_intent",
        "description": "对用户输入进行意图分类",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["selection", "adjustment", "chat", "out_of_scope"],
                    "description": "selection=元器件选型需求; adjustment=调整已有选型; chat=电子/选型相关的技术问答; out_of_scope=与电子元器件选型完全无关的问题"
                },
                "device_category": {
                    "type": "string",
                    "description": "涉及器件类型，如 buck/ldo/mosfet/op_amp/current_sense 等"
                },
                "reasoning": {
                    "type": "string",
                    "description": "判断理由（15字以内）"
                },
            },
            "required": ["intent"]
        }
    }
}

_CLASSIFY_SYSTEM = """你是电子元器件选型系统的意图分类器。

分类规则：
- selection: 用户提出具体选型需求（含电压/电流/封装/参数等）
- adjustment: 用户要修改调整当前选型结果（如换国产、改参数）
- chat: 与电子元器件选型直接相关的技术问答（如电路拓扑原理、器件参数解读、选型建议等）
- out_of_scope: 与电子元器件选型完全无关的问题（编程算法、通用AI概念、生活常识等）

只调用 classify_intent 工具，不要输出其他内容。"""


def _llm_classify(user_input: str) -> dict:
    """使用 tool calling 进行结构化意图分类，消除 JSON 解析脆弱性。"""
    try:
        from .llm_client import call_openai_chat
        resp = call_openai_chat(
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": user_input},
            ],
            tools=[_CLASSIFY_TOOL],
            tool_choice="auto",
            temperature=0.0,
            thinking_depth="off",
        )
        # 优先解析 tool call 结果
        tool_calls = resp.get("tool_calls", [])
        if tool_calls:
            args = json.loads(tool_calls[0]["function"]["arguments"])
            return {
                "intent": args.get("intent", "chat"),
                "device_category": args.get("device_category", "unknown"),
                "reasoning": args.get("reasoning", ""),
            }
        # 降级：尝试解析 content 中的 JSON
        content = resp.get("content", "").strip()
        if content:
            content = re.sub(r"^```(?:json)?\s*\n?", "", content)
            content = re.sub(r"\n?```\s*$", "", content)
            result = json.loads(content)
            return {
                "intent": result.get("intent", "chat"),
                "device_category": result.get("device_category", "unknown"),
                "reasoning": result.get("reasoning", ""),
            }
    except Exception:
        pass
    return {"intent": "chat", "reasoning": "error", "device_category": "unknown"}


# ══════════════════════════════════════════════════════════════════
# 主分类函数 — 返回富 dict
# ══════════════════════════════════════════════════════════════════

def classify(
    user_input: str,
    has_active_selection: bool = False,
    accumulated_input: str = "",
    session_id: Optional[str] = None,
) -> dict:
    """混合意图分类：规则快筛 → LLM 终判。

    Returns dict:
      intent: "selection"|"adjustment"|"chat"|"clarify"|"selection_choice"
      merged_input: str        合并了累积上下文的完整输入
      clarify_response: str    当 intent=clarify 时的追问文本
      no_spec_params: bool     用户明确表示无具体参数
      reasoning: str           LLM 判断理由
      device_category: str     涉及器件类型
      selected_part: str|None  当 intent=selection_choice 时的器件型号
    """
    text = user_input.strip()
    merged = (f"{accumulated_input}; {text}") if accumulated_input else text

    result: dict = {
        "intent": "chat",
        "merged_input": merged,
        "clarify_response": "",
        "no_spec_params": False,
        "reasoning": "",
        "device_category": "unknown",
        "selected_part": None,
    }

    # ── 数字选择检测（需要 has_active_selection 上下文）──────────
    if has_active_selection and re.match(r'^\d{1,2}$', text.strip()):
        result["intent"] = "selection_choice"
        result["selected_index"] = int(text.strip())
        return result

    # ── 快速出域检测（无需 LLM，直接拦截）────────────────────────
    if _is_out_of_scope(text):
        result["intent"] = "out_of_scope"
        result["clarify_response"] = _OUT_OF_SCOPE_REPLY
        return result

    # ── 规则快筛 ────────────────────────────────────────────────
    if _is_fast_chat(text):
        # 有活跃选型且是纯数字 → 不应走到这里（已在上面处理）
        result["intent"] = "chat"
        return result

    if _is_fast_selection(merged):
        from .constraint_checker import extract_constraints, check_completeness
        constraints = extract_constraints(merged)
        is_complete, missing_p0, _ = check_completeness(constraints)
        if not is_complete:
            result["intent"] = "clarify"
            result["clarify_response"] = _build_clarify(missing_p0)
        else:
            result["intent"] = "selection"
        return result

    if _PARTIAL_SELECTION.search(text) and not _is_question(text):
        from .constraint_checker import extract_constraints, check_completeness
        constraints = extract_constraints(merged)
        is_complete, missing_p0, _ = check_completeness(constraints)
        if not is_complete:
            result["intent"] = "clarify"
            result["clarify_response"] = _build_clarify(missing_p0)
        else:
            result["intent"] = "selection"
        return result

    if has_active_selection and _is_fast_adjustment(text):
        result["intent"] = "adjustment"
        result["adjustments"] = extract_adjustment(text)
        return result

    # ── LLM 终判 ────────────────────────────────────────────────
    llm_result = _llm_classify(merged if accumulated_input else text)
    intent = llm_result.get("intent", "chat")
    result["reasoning"] = llm_result.get("reasoning", "")
    result["device_category"] = llm_result.get("device_category", "unknown")

    if intent == "out_of_scope":
        result["intent"] = "out_of_scope"
        result["clarify_response"] = _OUT_OF_SCOPE_REPLY
    elif intent == "selection":
        from .constraint_checker import extract_constraints, check_completeness
        constraints = extract_constraints(merged)
        is_complete, missing_p0, _ = check_completeness(constraints)
        if not is_complete:
            has_params = any([
                constraints.get("output_voltage_v"),
                constraints.get("output_current_a"),
                constraints.get("input_voltage_nominal_v"),
                constraints.get("topology"),
            ])
            if not has_params:
                result["intent"] = "chat"
                result["no_spec_params"] = True
            else:
                result["intent"] = "clarify"
                result["clarify_response"] = _build_clarify(missing_p0)
        else:
            result["intent"] = "selection"
    elif intent == "adjustment" and has_active_selection:
        result["intent"] = "adjustment"
        result["adjustments"] = extract_adjustment(text)
    else:
        result["intent"] = intent

    return result


def _build_clarify(missing_params: list) -> str:
    """构建参数追问文本。"""
    if not missing_params:
        return "请提供更完整的器件参数信息，如输入/输出电压、最大输出电流等。"
    labels = {
        "output_voltage_v": "输出电压（V）",
        "output_current_a": "最大输出电流（A）",
        "input_voltage_nominal_v": "输入电压（V）",
        "topology": "电路拓扑（降压/升压/线性稳压）",
        "category": "器件类别",
    }
    missing_str = "、".join(labels.get(p, p) for p in missing_params[:2])
    return f"您的需求还缺少以下关键参数：**{missing_str}**。请补充后我们继续为您选型。"


# ══════════════════════════════════════════════════════════════════
# 兼容旧接口
# ══════════════════════════════════════════════════════════════════

def get_last_classification() -> dict:
    """向后兼容：返回最近一次 LLM 分类详情。"""
    return {}


def classify_with_llm(user_input: str, has_active_selection: bool = False) -> str:
    """旧接口兼容。"""
    return _llm_classify(user_input).get("intent", "chat")


def extract_adjustment(user_input: str, current_constraints: Optional[dict] = None) -> dict:
    """从调整语句中提取需要修改的约束参数。"""
    changes: dict = {}
    if re.search(r"车规|automotive", user_input, re.I):
        changes["grade"] = "automotive"
    elif re.search(r"非车规|工业级|industrial|不要车规", user_input, re.I):
        changes["grade"] = "industrial"
    if re.search(r"国产|国产化|domestic|矽力杰|圣邦微|南芯", user_input, re.I):
        changes["preferences"] = ["domestic_alternative"]
    m = re.search(r"(\d+\.?\d*)\s*[aA]\b", user_input)
    if m:
        changes["output_current_a"] = float(m.group(1))
    m = re.search(r"(\d+)\s*[vV]\s*(?:转|→|->|to|输出)", user_input)
    if m:
        changes["output_voltage_v"] = float(m.group(1))
    m = re.search(r"(-?\d+)\s*[~\-到至]\s*(-?\d+)\s*[°℃C]", user_input)
    if m:
        changes["temperature_min_c"] = float(m.group(1))
        changes["temperature_max_c"] = float(m.group(2))
    return changes
