import re
import os
from .schemas import RequirementConstraints

VOLTAGE_PAT    = re.compile(r"(\d+(?:\.\d+)?)\s*[Vv]")
CURRENT_PAT    = re.compile(r"(\d+(?:\.\d+)?)\s*[Aa]")
CURRENT_MA_PAT = re.compile(r"(\d+(?:\.\d+)?)\s*m[Aa]")   # 毫安，需转换 ÷1000
TEMP_PAT       = re.compile(r"(-?\d+)[°º]?C")
# 温度范围模式：匹配 "X 到 Y°C"、"X°C 到 Y°C"、"X~Y°C" 等
# 即使前一个数字没有 °C 后缀也能匹配
TEMP_RANGE_PAT = re.compile(
    r"(-?\d+)\s*[°º]?\s*[Cc]?\s*[到至~\-—]\s*(-?\d+)\s*[°º]?\s*[Cc]",
    re.IGNORECASE,
)
# 功率匹配："100W"、"65W"、"240W" 等
WATT_PAT = re.compile(r"(\d+(?:\.\d+)?)\s*[Ww](?!\s*[VvAa])")

# USB PD 功率→电压/电流映射（常见 PPS 档位）
# 100W → 最高 20V/5A；65W → 20V/3.25A；140W → 28V/5A (PD 3.1 EPR)
_PD_POWER_MAP: dict = {
    (0, 30):   (5,  3),    # <30W: 5V/3A (手机)
    (30, 65):  (12, 3),    # 30-65W: 12V/3A (平板/轻薄本)
    (65, 100): (20, 5),    # 65-100W: 20V/5A (笔记本)
    (100, 240):(20, 5),    # 100-240W: 20V/5A 或 28V/5A (游戏本)
    (240, 999):(28, 5),    # >240W: 28V/5A (PD 3.1 EPR)
}

try:
    from .llm_client import parse_requirement_with_fc, parse_requirement_with_llm
except Exception as e:
    from .log_util import warn_swallow; warn_swallow("requirement_parser", e, "LLM import")
    parse_requirement_with_fc = None
    parse_requirement_with_llm = None

# ── LLM 输出归一化映射 ────────────────────────────────────────────
_CAT_NORM: dict = {
    "dc_dc_converter": "dc_dc_converter",
    "dc-dc": "dc_dc_converter", "dc_dc": "dc_dc_converter",
    "电源转换": "dc_dc_converter", "电源管理": "dc_dc_converter",
    "降压": "dc_dc_converter", "降到": "dc_dc_converter", "升压": "dc_dc_converter", "升到": "dc_dc_converter",
    "buck": "dc_dc_converter", "boost": "dc_dc_converter",
    "pmic": "dc_dc_converter",
    "ldo": "ldo", "线性稳压": "ldo", "低压差": "ldo",
    "linear": "ldo",
}
_TOPO_NORM: dict = {
    "降压": "buck",  "降到": "buck", "buck": "buck",  "buck converter": "buck",
    "升压": "boost", "升到": "boost", "boost": "boost", "boost converter": "boost",
    "buck/boost": "buck_boost",
    "ldo": "ldo", "线性": "ldo", "linear regulator": "ldo",
}

# ── 已知封装名（用于 package_preference 提取）────────────────────
_KNOWN_PACKAGES = [
    "SOT-23-5", "SOT-23-6", "SOT-23", "SOT23",
    "QFN", "DFN", "WSON",
    "SOP-8", "SOP8", "SOP",
    "SOIC", "TSSOP", "HSOP",
    "TO-263", "TO-252",
    "LGA", "BGA",
]


def _norm_cat(v: str) -> "str | None":
    if not v:
        return None
    vl = str(v).lower().strip()
    for k, mapped in _CAT_NORM.items():
        if k in vl:
            return mapped
    return None


def _norm_topo(v: str) -> "str | None":
    if not v:
        return None
    vl = str(v).lower().strip()
    return _TOPO_NORM.get(vl) or next(
        (mapped for k, mapped in _TOPO_NORM.items() if k in vl), None
    )


def _extract_package(text: str) -> "str | None":
    """从文本中提取封装名称。"""
    upper = text.upper()
    for pkg in _KNOWN_PACKAGES:
        if pkg.upper() in upper:
            return pkg
    # 支持"XXX封装"中文句式，如"QFN封装"
    m = re.search(r"([A-Za-z0-9\-]{2,10})\s*封装", text)
    if m:
        return m.group(1).upper()
    return None


def parse_requirement(text: str) -> RequirementConstraints:
    rc = RequirementConstraints(raw_input=text)
    lower = text.lower()

    # ── LLM 优先解析（Function Calling → 旧版 fallback）────────
    from .llm_config import get_api_key
    if get_api_key():
        llm_res = {}
        # 优先使用 Function Calling（P2）
        if parse_requirement_with_fc is not None:
            try:
                llm_res = parse_requirement_with_fc(text)
            except Exception:
                pass
        # Fallback: 旧版 JSON 解析
        if not llm_res and parse_requirement_with_llm is not None:
            try:
                llm_res = parse_requirement_with_llm(text)
            except Exception:
                pass
        if llm_res:
            if "category" in llm_res:
                llm_res["category"] = _norm_cat(llm_res["category"])
            if "topology" in llm_res:
                llm_res["topology"] = _norm_topo(llm_res["topology"])
            for k, v in llm_res.items():
                if hasattr(rc, k) and v is not None:
                    try:
                        setattr(rc, k, v)
                    except Exception:
                        pass

    # ── 规则化 category / topology（规则优先，覆盖 LLM）─────────
    if "buck" in lower or "降压" in lower or "降到" in lower:
        rc.category = "dc_dc_converter"
        rc.topology = "buck"
    elif "boost" in lower or "升压" in lower or "升到" in lower:
        rc.category = "dc_dc_converter"
        rc.topology = "boost"
    elif "ldo" in lower or "低压差" in lower or "线性稳压" in lower:
        rc.category = "ldo"
        rc.topology = rc.topology or "ldo"
    elif ("转" in text and re.search(r"\d+\s*[Vv]", text)) or re.search(r"\d+V\s*to\s*\d+V", lower):
        rc.category = "dc_dc_converter"
        rc.topology = rc.topology or "buck"
    elif re.search(r"输入.{0,6}\d+\s*[Vv]", text) and re.search(r"输出.{0,6}\d+\s*[Vv]", text):
        rc.category = "dc_dc_converter"
        rc.topology = rc.topology or "buck"

    # ── 电压提取 ──────────────────────────────────────────────────
    # 优先匹配"输入 NV / 输出 NV"句式
    m_io = re.search(r"输入\D{0,6}?(\d+(?:\.\d+)?)\s*[Vv]", text)
    m_oo = re.search(r"输出\D{0,6}?(\d+(?:\.\d+)?)\s*[Vv]", text)
    if m_io:
        try:
            rc.input_voltage_nominal_v = rc.input_voltage_nominal_v or float(m_io.group(1))
        except Exception:
            pass
    if m_oo:
        try:
            rc.output_voltage_v = rc.output_voltage_v or float(m_oo.group(1))
        except Exception:
            pass

    # 其次匹配"NV 转/to NV"句式
    m = re.search(r"(\d+(?:\.\d+)?)\s*[Vv].{0,8}?[转to]+.{0,8}?(\d+(?:\.\d+)?)\s*[Vv]", text)
    if m:
        try:
            rc.input_voltage_nominal_v = rc.input_voltage_nominal_v or float(m.group(1))
            rc.output_voltage_v = rc.output_voltage_v or float(m.group(2))
        except Exception:
            pass
    else:
        vs = VOLTAGE_PAT.findall(text)
        if vs:
            try:
                if len(vs) >= 2:
                    rc.input_voltage_nominal_v = rc.input_voltage_nominal_v or float(vs[0])
                    rc.output_voltage_v = rc.output_voltage_v or float(vs[1])
                elif len(vs) == 1:
                    rc.output_voltage_v = rc.output_voltage_v or float(vs[0])
            except Exception:
                pass

    # ── 电压比较兜底（防止 LLM 误判 topology）────────────────────
    # 若 Vin/Vout 均已知且 category=dc_dc_converter，按电压方向强制覆盖 topology
    if (rc.category == "dc_dc_converter"
            and rc.input_voltage_nominal_v is not None
            and rc.output_voltage_v is not None):
        if rc.input_voltage_nominal_v > rc.output_voltage_v:
            rc.topology = "buck"
        elif rc.input_voltage_nominal_v < rc.output_voltage_v:
            rc.topology = "boost"

    # ── 电流提取（优先 mA，再匹配 A）────────────────────────────
    if rc.output_current_a is None:
        try:
            m_ma = CURRENT_MA_PAT.search(text)
            if m_ma:
                rc.output_current_a = float(m_ma.group(1)) / 1000.0
            else:
                m2 = CURRENT_PAT.search(text)
                if m2:
                    rc.output_current_a = float(m2.group(1))
        except Exception:
            pass

    # ── 功率提取与产品级推断（USB-C PD / 快充等场景）────────────
    # 当输入中只有"W"瓦特值而无 V/A 时，从功率反推电压电流
    if rc.output_voltage_v is None and rc.output_current_a is None:
        m_watt = WATT_PAT.search(text)
        if m_watt:
            try:
                power_w = float(m_watt.group(1))
                # 按功率区间映射到对应的 V/A 档位
                for (lo, hi), (v, a) in _PD_POWER_MAP.items():
                    if lo <= power_w <= hi:
                        rc.output_voltage_v = rc.output_voltage_v or float(v)
                        rc.output_current_a = rc.output_current_a or float(a)
                        break
            except Exception:
                pass

    # USB-C PD / 快充 / 充电器 → 类别和拓扑提示
    if ("pd" in lower or "快充" in lower or "充电器" in lower
            or "usb-c" in lower or "usb c" in lower):
        if not rc.category:
            rc.category = "dc_dc_converter"
        if not rc.topology and rc.input_voltage_nominal_v is None:
            # USB-C PD 适配器通常输入 90-264V AC → 经 AC-DC → DC 母线 → DC-DC Buck
            # 这里假设 AC-DC 后母线约 24V，实际 DC-DC 为 Buck 降压
            rc.topology = rc.topology or "buck"
            rc.grade = rc.grade or "industrial"

    # ── 温度范围提取（优先匹配范围模式）──────────────────────────
    m_temp_range = TEMP_RANGE_PAT.search(text)
    if m_temp_range:
        try:
            t1 = int(m_temp_range.group(1))
            t2 = int(m_temp_range.group(2))
            rc.temperature_min_c = rc.temperature_min_c or min(t1, t2)
            rc.temperature_max_c = rc.temperature_max_c or max(t1, t2)
        except Exception:
            pass

    # 兜底：单点温度提取
    if rc.temperature_min_c is None or rc.temperature_max_c is None:
        temps = TEMP_PAT.findall(text)
        if temps:
            nums = [int(t) for t in temps]
            if len(nums) >= 2 and (rc.temperature_min_c is None or rc.temperature_max_c is None):
                rc.temperature_min_c = rc.temperature_min_c or min(nums)
                rc.temperature_max_c = rc.temperature_max_c or max(nums)
            elif len(nums) == 1 and rc.temperature_min_c is None:
                rc.temperature_min_c = nums[0]

    # ── 等级 ──────────────────────────────────────────────────────
    # 归一化 LLM 返回的等级值（"车规级"/"AEC-Q100"/"automotive" → "automotive"）
    _AUTO_GRADES = {"automotive", "车规", "车规级", "aec-q100", "aec-q200", "aec_q100", "vehicle"}
    if rc.grade and rc.grade.lower().strip() in _AUTO_GRADES:
        rc.grade = "automotive"
    if "车规" in lower or "automotive" in lower or "aec-q" in lower:
        # 排除否定句式："非车规"、"不是车规"、"不要求车规"
        if not re.search(r"(非|不是|不要求|无需|不用)\s*车规", text):
            rc.grade = "automotive"
        elif rc.grade != "automotive":
            rc.grade = "industrial"  # 明确非车规 → 默认工业级

    # ── 封装偏好 ──────────────────────────────────────────────────
    if not rc.package_preference:
        rc.package_preference = _extract_package(text)

    # ── preferences ───────────────────────────────────────────────
    if ("国产" in lower or "国产替代" in lower or "优先国产" in lower) and "domestic_alternative" not in rc.preferences:
        rc.preferences.append("domestic_alternative")
    _LOW_SUPPLY_KW = ["低供应", "低供应链风险", "库存充足", "优先有库存", "供应稳定", "供应链风险低", "库存优先"]
    if any(kw in lower for kw in _LOW_SUPPLY_KW) and "low_supply_risk" not in rc.preferences:
        rc.preferences.append("low_supply_risk")

    # ── must_have ─────────────────────────────────────────────────
    _MUST_TRIGGERS = ["必须", "强制", "一定要", "不可缺少"]
    is_must = any(t in text for t in _MUST_TRIGGERS)
    if is_must:
        # 注意：automotive_grade 不再作为硬约束，系统仅识别需求但不过滤
        # （removed: if ("车规" in lower or "automotive" in lower) and "automotive_grade" not in rc.must_have:）
        # （removed: rc.must_have.append("automotive_grade")）
        if ("国产" in lower) and "domestic" not in rc.must_have:
            rc.must_have.append("domestic")
        if rc.package_preference and "package" not in rc.must_have:
            rc.must_have.append(f"package:{rc.package_preference}")

    # ── nice_to_have ──────────────────────────────────────────────
    _NICE_TRIGGERS = ["最好", "如果可以", "尽量", "可选", "nice to have"]
    is_nice = any(t in lower for t in _NICE_TRIGGERS)
    if is_nice:
        if ("国产" in lower) and "domestic" not in rc.nice_to_have:
            rc.nice_to_have.append("domestic")
        if rc.package_preference and f"package:{rc.package_preference}" not in rc.nice_to_have:
            rc.nice_to_have.append(f"package:{rc.package_preference}")

    return rc
