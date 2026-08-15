"""
constraint_checker.py — 选型约束完整性检查与渐进式追问

硬约束分级：
  P0 (MUST)：Vin, Vout, Iout — 缺失时禁止触发选型
  P1 (SHOULD)：温度范围, 等级 — 缺失时追问
  P2 (NICE)：封装, 应用场景, 偏好 — 缺失时不追问

流程：
  1. 解析用户输入 → 提取约束参数
  2. 合并到会话累积约束上下文
  3. 检查完整性 → 完整则触发选型 / 不完整则生成追问
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import re


# ═══════════════════════════════════════════════════════════════
# 约束定义
# ═══════════════════════════════════════════════════════════════

P0_FIELDS = ["input_voltage_nominal_v", "output_voltage_v", "output_current_a"]
P1_FIELDS = ["temperature_min_c", "temperature_max_c", "grade"]
P2_FIELDS = ["package_preference", "application"]

FIELD_LABELS: Dict[str, str] = {
    "input_voltage_nominal_v": "输入电压 (Vin)",
    "output_voltage_v": "输出电压 (Vout)",
    "output_current_a": "输出电流 (Iout)",
    "temperature_min_c": "最低工作温度",
    "temperature_max_c": "最高工作温度",
    "grade": "应用等级",
    "package_preference": "封装偏好",
    "application": "应用场景",
}

FIELD_EXAMPLES: Dict[str, str] = {
    "input_voltage_nominal_v": "如 12V",
    "output_voltage_v": "如 5V 或 3.3V",
    "output_current_a": "如 3A 或 500mA",
    "temperature_min_c": "如 -40°C",
    "temperature_max_c": "如 85°C 或 125°C",
    "grade": "automotive(车规) / industrial(工业级) / commercial(商业级)",
    "package_preference": "如 SOT-23、QFN、TO-220",
    "application": "如 车载电源、通信设备、工业控制",
}

P0_QUESTIONS: Dict[str, str] = {
    "input_voltage_nominal_v": "系统的输入电压是多少？（如 12V、24V、5V 等）",
    "output_voltage_v": "需要的输出电压是多少？（如 5V、3.3V 等）",
    "output_current_a": "输出端需要多大电流？（如 3A、500mA 等）",
}

P1_QUESTIONS: Dict[str, str] = {
    "temperature_min_c,temperature_max_c": "工作温度范围是多少？（如 -40~85°C 工业级，或 -40~125°C 车规级）",
    "grade": "需要满足什么等级标准？车规 (AEC-Q100)、工业级还是商业级？",
}

# P0 字段提取正则
# 输入电压: "输入12V", "Vin 5V", "输入最高20V", "输入电压最高 20V"
# 使用负向后顾避免匹配"X V 输入"中的"输入"（那属于范围表达的后缀）
_VIN_REGEX = re.compile(
    r"(?:^|[^0-9])(?:输入|Vin|VIN|供电|电源)(?:电压|最高|最大|范围)?[^，,]{0,4}?(\d+\.?\d*)\s*[Vv伏]"
)
# 输出电压: "输出5V", "Vout 3.3V", "输出电压有5V", "转5V", "升到12V"
_VOUT_REGEX = re.compile(
    r"(?:输出|Vout|VOUT|转\s*|→|->|升到|降到|输出电压)[\s\S]{0,6}?(\d+\.?\d*)\s*[Vv伏]"
)
# 最高电压: "最高20V", "最大 48V", "不超过 20V"
_VMAX_REGEX = re.compile(
    r"(?:最高|最大|max|不超过|不高于|≤|<=)\D{0,3}(\d+\.?\d*)\s*[Vv伏]"
)
# 电压范围: "6V–36V", "6~36V", "6-36V", "6 到 36 V"
_VRANGE_REGEX = re.compile(
    r"(\d+\.?\d*)\s*[Vv伏]\s*(?:[-–—~到至])\s*(\d+\.?\d*)\s*[Vv伏]"
)
_CURRENT_INFER = re.compile(r"(\d+\.?\d*)\s*(?:A|a|安)(?![a-zA-Z])")
_MA_INFER = re.compile(r"(\d+)\s*(?:mA|毫安|ｍＡ)")

# 数值+关键词后置："12V 输入"（值在关键词前）
_VIN_VALUE_FIRST = re.compile(r"(\d+\.?\d*)\s*[Vv伏]\s*(?:输入|Vin|VIN|供电|电源)")
# 数值+关键词后置："3.3V 输出"（值在关键词前）
_VOUT_VALUE_FIRST = re.compile(r"(\d+\.?\d*)\s*[Vv伏]\s*(?:输出|Vout|VOUT)")


def extract_constraints(text: str) -> dict:
    """从用户输入文本中提取约束参数（规则层快速提取）。

    提取顺序经过优化，确保：
    1. 电压范围优先匹配（避免"6V–36V 输入"误提取 Vin）
    2. 转/升到/降到 模式提取完整 Vin→Vout 对
    3. 独立 Vin/Vout 模式作为后备
    """
    result: dict = {}

    # ── 1. 电压范围优先（6V–36V）─────────────────
    vr_m = _VRANGE_REGEX.search(text)
    if vr_m:
        result["input_voltage_min_v"] = float(vr_m.group(1))
        result["input_voltage_max_v"] = float(vr_m.group(2))
        result["input_voltage_nominal_v"] = float(vr_m.group(2))

    # ── 2. 转换模式：X V 转/升/降 Y V ──────────
    #    "12V转5V" → Vin=12, Vout=5
    #    "5V升压到12V" → Vin=5, Vout=12
    xform_m = re.search(
        r"(\d+\.?\d*)\s*[Vv伏][\s\S]{0,6}?(?:转|升压到|升到|降压到|降到|→|->)\s*(\d+\.?\d*)\s*[Vv伏]",
        text
    )
    if xform_m:
        if "input_voltage_nominal_v" not in result:
            result["input_voltage_nominal_v"] = float(xform_m.group(1))
        result["output_voltage_v"] = float(xform_m.group(2))

    # ── 2.5. 数值+关键词后置：12V 输入，3.3V 输出 ────
    # 处理 "12V 输入"、"5V 输出" 等值在关键词前，关键词在后的写法
    vin_vf = _VIN_VALUE_FIRST.search(text)
    if vin_vf and "input_voltage_nominal_v" not in result:
        result["input_voltage_nominal_v"] = float(vin_vf.group(1))

    vout_vf = _VOUT_VALUE_FIRST.search(text)
    if vout_vf and "output_voltage_v" not in result:
        result["output_voltage_v"] = float(vout_vf.group(1))

    # ── 3. 最高电压 ──────────────────────────────
    vmax_m = _VMAX_REGEX.search(text)
    if vmax_m:
        vmax_val = float(vmax_m.group(1))
        if "input_voltage_nominal_v" not in result:
            result["input_voltage_nominal_v"] = vmax_val

    # ── 4. 输入电压（独立模式） ──────────────────
    if "input_voltage_nominal_v" not in result:
        vin_m = _VIN_REGEX.search(text)
        if vin_m:
            result["input_voltage_nominal_v"] = float(vin_m.group(1))

    # ── 5. 输出电压（独立模式） ──────────────────
    if "output_voltage_v" not in result:
        vout_m = _VOUT_REGEX.search(text)
        if vout_m:
            result["output_voltage_v"] = float(vout_m.group(1))

    # ── 6. 电流：优先匹配毫安 ────────────────────
    ma = _MA_INFER.search(text)
    if ma:
        result["output_current_a"] = float(ma.group(1)) / 1000.0
    else:
        cm = _CURRENT_INFER.search(text)
        if cm:
            result["output_current_a"] = float(cm.group(1))

    # ── 7. 温度 ──────────────────────────────────
    tm = re.search(r"(-?\d+)\s*(?:~|到|至|-)\s*(-?\d+)\s*(?:°|°C|C|度)", text)
    if tm:
        result["temperature_min_c"] = float(tm.group(1))
        result["temperature_max_c"] = float(tm.group(2))

    # ── 8. 等级 ──────────────────────────────────
    if re.search(r"车规|automotive|AEC|AEC-Q", text, re.I):
        result["grade"] = "automotive"
    elif re.search(r"工业|industrial", text, re.I):
        result["grade"] = "industrial"
    elif re.search(r"商业|commercial", text, re.I):
        result["grade"] = "commercial"
    elif re.search(r"非车规|不要车规|不是车规", text, re.I):
        result["grade"] = "industrial"

    # ── 9. 封装 ──────────────────────────────────
    pkg_m = re.search(r"(SOT-\d+|QFN\d*|TO-\d+|SOIC-\d+|TSSOP-\d+|DFN\d*)", text, re.I)
    if pkg_m:
        result["package_preference"] = pkg_m.group(1).upper()

    # ── 10. 拓扑 ─────────────────────────────────
    if re.search(r"降压|buck|降到|step[.\s]?down", text, re.I):
        result["topology"] = "buck"
    elif re.search(r"升压|boost|升到|step[.\s]?up", text, re.I):
        result["topology"] = "boost"
    elif re.search(r"\bldo\b|线性稳压|LDO", text, re.I):
        result["topology"] = "ldo"

    # ── 11. 器件类别推断（由拓扑推导，供 pre_constraints 快速路径使用）──
    if result.get("topology") in ("buck", "boost"):
        result.setdefault("category", "dc_dc_converter")
    elif result.get("topology") == "ldo":
        result.setdefault("category", "ldo")

    return result


def merge_constraints(accumulated: dict, new_extracted: dict) -> dict:
    """合并新提取的约束到累积约束中（新值优先覆盖旧值）。"""
    merged = dict(accumulated)
    for k, v in new_extracted.items():
        if v is not None and v != "":
            merged[k] = v
    return merged


def check_completeness(constraints: dict) -> Tuple[bool, List[str], List[str]]:
    """检查约束完整性。"""
    missing_p0 = []
    missing_p1 = []

    # input_voltage_nominal_v 可以由 input_voltage_min/max 替代
    has_vin = (constraints.get("input_voltage_nominal_v") is not None or
               (constraints.get("input_voltage_min_v") is not None and
                constraints.get("input_voltage_max_v") is not None))

    for f in P0_FIELDS:
        if f == "input_voltage_nominal_v":
            if not has_vin:
                missing_p0.append(f)
        elif constraints.get(f) is None:
            missing_p0.append(f)

    for f in P1_FIELDS:
        if constraints.get(f) is None:
            missing_p1.append(f)

    # 温度特殊处理 — 两个都缺失才算
    if "temperature_min_c" in missing_p1 and "temperature_max_c" in missing_p1:
        pass  # 都缺失，保留在列表中
    elif "temperature_min_c" in missing_p1 or "temperature_max_c" in missing_p1:
        missing_p1 = [m for m in missing_p1 if m not in ("temperature_min_c", "temperature_max_c")]
        missing_p1.append("temperature_range")

    return (len(missing_p0) == 0, missing_p0, missing_p1)


def template_fallback(confirmed_str: str, missing_all: list) -> str:
    """LLM 不可用时的模板降级。"""
    lines = [f"已确认：{confirmed_str}。"]
    labels = [FIELD_LABELS.get(f, f) for f in missing_all] if missing_all else ["更多参数"]
    lines.append(f"请继续提供：{'、'.join(labels[:3])}")
    return "\n".join(lines)


def generate_clarification_questions(missing_p0: List[str], missing_p1: List[str]) -> List[str]:
    """根据缺失字段生成自然语言追问列表。"""
    questions = []

    # P0 问题 — 必须问
    for f in missing_p0:
        if f in P0_QUESTIONS:
            questions.append(P0_QUESTIONS[f])

    # P1 问题 — 选最重要的 1-2 个
    p1_asked = 0
    if "temperature_min_c,temperature_max_c" in P1_QUESTIONS:
        for mp1 in missing_p1:
            if mp1 in ("temperature_min_c", "temperature_max_c", "temperature_range"):
                questions.append(P1_QUESTIONS["temperature_min_c,temperature_max_c"])
                p1_asked += 1
                break
    for f in missing_p1:
        if f in P1_QUESTIONS and p1_asked < 2:
            questions.append(P1_QUESTIONS[f])
            p1_asked += 1

    return questions


def build_clarification_response(
    user_input: str,
    accumulated: dict,
    agent_name: str = "eZmanbo",
) -> tuple:
    """基于 LLM 生成自然的追问回复。返回 (text, merged_constraints)。"""
    from .llm_client import call_openai_chat_text

    constraints = merge_constraints(accumulated, extract_constraints(user_input))
    is_complete, missing_p0, missing_p1 = check_completeness(constraints)

    if is_complete:
        return "", constraints

    # 构建已确认参数描述
    confirmed_parts = []
    if constraints.get("input_voltage_nominal_v"):
        confirmed_parts.append(f"输入电压：{constraints['input_voltage_nominal_v']}V")
    if constraints.get("output_voltage_v"):
        confirmed_parts.append(f"输出电压：{constraints['output_voltage_v']}V")
    if constraints.get("output_current_a"):
        confirmed_parts.append(f"输出电流：{constraints['output_current_a']}A")
    if constraints.get("topology"):
        t = {"buck": "降压", "boost": "升压", "ldo": "线性稳压(LDO)", "buck_boost": "升降压"}.get(
            constraints["topology"], constraints["topology"]
        )
        confirmed_parts.append(f"拓扑：{t}")
    if constraints.get("grade"):
        g = {"automotive":"车规级","industrial":"工业级","commercial":"商业级"}.get(constraints["grade"], constraints["grade"])
        confirmed_parts.append(f"等级：{g}")
    confirmed_str = "；".join(confirmed_parts) if confirmed_parts else "暂无"

    # 构建缺失参数描述
    missing_all = (missing_p0 or []) + (missing_p1 or [])
    missing_str = "、".join(FIELD_LABELS.get(f, f) for f in missing_all) if missing_all else "无"

    prompt = (
        f"用户正在逐步提供选型需求。\n\n"
        f"目前已确认的参数：{confirmed_str}\n"
        f"还缺少的参数：{missing_str}\n\n"
        f"用户最新输入：「{user_input[:200]}」\n\n"
        f"请以自然、专业的中文回复用户，先简单确认已收到的信息，然后友好地询问最关键的缺失参数（一次只问最多2个问题）。"
        f"不要使用列表编号，而是用自然的语句。不要提及\"P0\"\"P1\"等技术术语。"
        f"回复控制在120字以内。"
    )
    system_msg = f"你是{agent_name}，专门负责电子元器件选型的智能助理。只讨论与电子器件选型相关的话题。绝对不要提及Claude、Anthropic或任何底层AI模型。"
    try:
        text = call_openai_chat_text(
            [{"role": "system", "content": system_msg}, {"role": "user", "content": prompt}],
            thinking_depth="off",
        ).strip() or template_fallback(confirmed_str, missing_all)
    except Exception:
        text = template_fallback(confirmed_str, missing_all)
    return text, constraints
