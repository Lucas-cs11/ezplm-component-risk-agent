"""
report_generator.py — 十维元器件选型风险评估引擎

基于《电子元器件选型风险评估研究报告 v1.0》建立。

评估体系：
  - 十个加权维度 D1–D10，权重合计 100
  - 单项评分 1–5（1=极低风险，5=极高风险）
  - 风险指数 R = Σ(weight/100 × (score-1)/4)，范围 0–1；乘 100 归一化至 0–100
  - 五级风险等级：L1(0–20) / L2(21–40) / L3(41–60) / L4(61–80) / L5(81–100)
  - 门禁项触发时，最终等级 = max(总分等级, 门禁等级)，不可因加权分低而绕过

参考：ISO 31000:2018, IEC 31010:2019, IEC 60812:2018, IEC 62402:2019
"""

from __future__ import annotations

import uuid
from typing import List, Optional, Tuple

from .schemas import (
    SelectionReport, RequirementConstraints, ScoredPart,
    RiskIR, RiskItem, PartIR,
)

# ──────────────────────────────────────────────────────────────────────────────
# 维度权重（总计 100）
# ──────────────────────────────────────────────────────────────────────────────
_DIMENSION_WEIGHTS = {
    "D1": 12,   # 功能与电气适配
    "D2": 14,   # 可靠性与应用环境
    "D3": 14,   # 生命周期与停产
    "D4": 12,   # 供应连续性
    "D5": 12,   # 法规与材料合规
    "D6": 10,   # 供应商/制造商质量
    "D7": 10,   # 假冒与渠道可信度
    "D8":  8,   # 制造装配工艺
    "D9":  5,   # 成本与商务
    "D10": 3,   # 数据完整性与可追溯
}

_DIMENSION_NAMES = {
    "D1":  "功能与电气适配",
    "D2":  "可靠性与应用环境",
    "D3":  "生命周期与停产",
    "D4":  "供应连续性",
    "D5":  "法规与材料合规",
    "D6":  "供应商/制造商质量",
    "D7":  "假冒与渠道可信度",
    "D8":  "制造装配工艺",
    "D9":  "成本与商务",
    "D10": "数据完整性与可追溯",
}

# 知名原厂白名单（用于 D6 质量评分）
_REPUTABLE_MANUFACTURERS = {
    "ti", "texas instruments",
    "adi", "analog devices",
    "st", "stmicroelectronics",
    "microchip", "microchip technology",
    "nxp",
    "infineon",
    "renesas",
    "on semiconductor", "onsemi",
    "vishay",
    "murata",
    "tdk",
    "samsung electro-mechanics",
    "yageo",
    "panasonic",
    "rohm",
    "diodes incorporated",
    "maxim", "maxim integrated",
    # 知名国产
    "圣邦微", "圣邦", "sgmicro",
    "思瑞浦", "3peak",
    "芯片", "中微半导体",
    "南芯", "nanq",
    "富满", "fuman",
    "力芯微",
    "华润矽威", "crmt",
    "杰华特", "joulwatt",
    "英集芯", "injoinic",
    "台湾矽力杰", "silergy",
}


# ──────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────────────────────────────────────

def _lifecycle_code(status: Optional[str]) -> str:
    """标准化生命周期代码。"""
    if not status:
        return "unknown"
    s = status.lower().strip()
    if s in ("active", "生产中", "在产"):
        return "active"
    if s in ("nrnd", "not recommended for new designs", "不推荐新设计", "不建议新设计"):
        return "nrnd"
    if s in ("ltb", "last time buy", "最后一次采购"):
        return "ltb"
    if s in ("eol", "end of life", "生命周期末", "停止生命"):
        return "eol"
    if s in ("obsolete", "discontinued", "停产", "已停产"):
        return "obsolete"
    return "unknown"


def _score_d1_functional(
    part: PartIR,
    constraints: RequirementConstraints,
    param_score: float,
) -> Tuple[float, str]:
    """D1 功能与电气适配风险（1–5）。"""
    # 使用 scoring 模块计算的参数匹配分（0–100）映射至 1–5
    if param_score >= 85:
        score = 1.0
        reason = "参数完全满足规格，设计裕量充分"
    elif param_score >= 65:
        score = 2.0
        reason = f"参数基本满足规格（参数得分 {param_score:.0f}），个别参数裕量较小"
    elif param_score >= 45:
        score = 3.0
        reason = f"部分参数接近边界（参数得分 {param_score:.0f}），建议样品验证"
    elif param_score >= 25:
        score = 4.0
        reason = f"参数匹配度偏低（参数得分 {param_score:.0f}），存在明显裕量不足风险"
    else:
        score = 5.0
        reason = f"参数匹配极差（参数得分 {param_score:.0f}），关键参数可能不满足需求"

    # 封装信息缺失时额外加风险
    if not part.package and score < 3.0:
        score = min(score + 1.0, 5.0)
        reason += "；封装信息缺失，接口兼容性无法验证"

    return score, reason


def _score_d2_reliability(
    part: PartIR,
    constraints: RequirementConstraints,
) -> Tuple[float, str]:
    """D2 可靠性与应用环境风险（1–5）。"""
    grade = getattr(constraints, "grade", None) or ""
    automotive_req = grade.lower() in ("automotive", "车规", "aeq", "aec")

    # 基础分：有车规认证给分低（风险低），否则看温度覆盖情况
    if part.automotive_grade:
        base = 1.0
        reason = "通过 AEC-Q 车规认证，可靠性证据充分"
    else:
        # 检查温度范围是否满足需求
        req_tmin = getattr(constraints, "temperature_min_c", None)
        req_tmax = getattr(constraints, "temperature_max_c", None)

        temp_ok = True
        temp_notes = []
        if req_tmin is not None and part.temperature_min_c is not None:
            if part.temperature_min_c > req_tmin:
                temp_ok = False
                temp_notes.append(f"最低温度不满足（需 {req_tmin}°C，实际 {part.temperature_min_c}°C）")
        if req_tmax is not None and part.temperature_max_c is not None:
            if part.temperature_max_c < req_tmax:
                temp_ok = False
                temp_notes.append(f"最高温度不满足（需 {req_tmax}°C，实际 {part.temperature_max_c}°C）")

        if automotive_req and not part.automotive_grade:
            base = 4.0
            reason = "要求车规但器件无 AEC-Q 认证，可靠性风险高"
        elif not temp_ok:
            base = 4.0
            reason = "温度范围不满足需求；" + "；".join(temp_notes)
        elif part.temperature_min_c is not None and part.temperature_max_c is not None:
            temp_range = part.temperature_max_c - part.temperature_min_c
            if temp_range >= 150:  # -40~125 等工业/车规宽温
                base = 2.0
                reason = f"温度范围宽（{part.temperature_min_c}–{part.temperature_max_c}°C），可靠性良好"
            elif temp_range >= 100:
                base = 2.0
                reason = f"温度范围满足（{part.temperature_min_c}–{part.temperature_max_c}°C）"
            else:
                base = 3.0
                reason = f"温度范围较窄（{part.temperature_min_c}–{part.temperature_max_c}°C），需确认工况适配性"
        else:
            base = 3.0
            reason = "温度规格信息不完整，可靠性证据有限"

    return base, reason


def _score_d3_lifecycle(part: PartIR) -> Tuple[float, str]:
    """D3 生命周期与停产风险（1–5）。"""
    lc = _lifecycle_code(part.lifecycle_status)
    mapping = {
        "active":   (1.0, "当前在产（Active），生命周期风险低"),
        "nrnd":     (3.5, "不推荐新设计（NRND），应尽快寻找替代料"),
        "ltb":      (4.0, "最后一次采购（LTB），停产迫近，需建立安全库存"),
        "eol":      (4.5, "已宣布停产（EOL），不建议新项目选用"),
        "obsolete": (5.0, "已停产（Obsolete），必须寻找替代料"),
        "unknown":  (3.0, "生命周期状态未知，需向制造商确认"),
    }
    return mapping.get(lc, (3.0, "生命周期状态未知"))


def _score_d4_supply(part: PartIR) -> Tuple[float, str]:
    """D4 供应连续性风险（1–5）。"""
    stock = part.stock
    source = (getattr(part, "source", "unknown") or "").lower()
    is_ezplm = source in ("ezplm", "api", "ezplm_api")

    if stock is None:
        score = 3.5
        reason = "库存信息缺失，无法评估供应连续性"
    elif stock == 0:
        score = 4.5
        reason = "当前库存为零，断供风险极高"
    elif stock < 50:
        score = 4.0
        reason = f"库存极低（{stock} 件），采购风险高"
    elif stock < 200:
        score = 3.0
        reason = f"库存偏低（{stock} 件），建议提前预购"
    elif stock < 1000:
        score = 2.5
        reason = f"库存尚可（{stock} 件），交期建议确认"
    elif stock < 5000:
        score = 2.0
        reason = f"库存充足（{stock} 件）"
    else:
        score = 1.0
        reason = f"库存充裕（{stock} 件），供应连续性风险极低"

    # 非 eZ-PLM 授权来源的供应连续性加分（降低信任度）
    if not is_ezplm and score < 4.0:
        score = min(score + 0.5, 5.0)
        reason += "；来源非 eZ-PLM 授权渠道，渠道可信度待核实"

    return score, reason


def _score_d5_compliance(
    part: PartIR,
    constraints: RequirementConstraints,
) -> Tuple[float, str]:
    """D5 法规与材料合规风险（1–5）。"""
    grade = (getattr(constraints, "grade", None) or "").lower()
    automotive_req = grade in ("automotive", "车规")

    # 有车规认证时合规性证据较充分
    if part.automotive_grade:
        return 2.0, "具备 AEC-Q 认证，主要合规证据齐全（RoHS/REACH 建议补充完整声明）"

    # 车规需求但无认证 → 合规高风险
    if automotive_req and not part.automotive_grade:
        return 4.0, "要求车规但缺少 AEC-Q 认证，法规合规性不确定"

    # 普通场景：eZ-PLM 来源相对可信
    source = (getattr(part, "source", "unknown") or "").lower()
    if source in ("ezplm", "api", "ezplm_api"):
        return 2.0, "eZ-PLM 平台数据，RoHS/REACH 合规基础证据可信"

    return 3.0, "合规状态依赖平台数据，建议向制造商索取正式 RoHS/REACH 声明"


def _score_d6_quality(part: PartIR) -> Tuple[float, str]:
    """D6 供应商/制造商质量风险（1–5）。"""
    mfr = (part.manufacturer or "").lower().strip()

    if not mfr:
        return 4.0, "制造商信息缺失，质量体系无法评估"

    # 检查是否为知名原厂
    for known in _REPUTABLE_MANUFACTURERS:
        if known in mfr or mfr in known:
            domestic_tag = " 🇨🇳" if getattr(part, "is_domestic", False) else ""
            return 1.5, f"{part.manufacturer}{domestic_tag} 为知名原厂，质量体系成熟"

    if getattr(part, "is_domestic", False):
        return 2.5, f"{part.manufacturer} 为国产制造商，建议核查质量体系（ISO 9001/IATF）"

    return 3.0, f"{part.manufacturer} 为非主流制造商，建议首件检验和供应商评估"


def _score_d7_counterfeit(part: PartIR) -> Tuple[float, str]:
    """D7 假冒与渠道可信度风险（1–5）。"""
    source = (getattr(part, "source", "unknown") or "").lower()
    has_datasheet = bool(part.datasheet_url)

    if source in ("ezplm", "api", "ezplm_api"):
        if has_datasheet:
            return 1.5, "eZ-PLM 授权渠道，含数据手册链接，可追溯性良好"
        return 2.0, "eZ-PLM 授权渠道，建议补充数据手册以完善追溯记录"

    if has_datasheet:
        return 3.0, "来源非 eZ-PLM 授权渠道，但有数据手册；建议核查授权证明（CoC）"

    return 4.0, "来源不明，无数据手册，假冒风险较高，建议从 OCM/授权分销商采购"


def _score_d8_manufacturing(part: PartIR) -> Tuple[float, str]:
    """D8 制造装配工艺风险（1–5）。"""
    pkg = (part.package or "").upper().strip()

    if not pkg:
        return 3.5, "封装信息缺失，SMT 工艺适配性无法评估"

    # BGA/QFN/多排引脚高密度封装需要更严格的工艺控制
    high_complexity = {"BGA", "CSP", "LGA", "WLCSP", "FBGA"}
    for h in high_complexity:
        if h in pkg:
            return 3.0, f"{pkg} 为高密度封装，需严格 X-Ray 检验和回流工艺控制"

    # QFN/DFN 通用
    if "QFN" in pkg or "DFN" in pkg:
        return 2.5, f"{pkg} 封装，需控制焊盘设计和回流温度曲线"

    # 通孔插件
    if "DIP" in pkg or "THT" in pkg or "PDIP" in pkg:
        return 2.0, f"{pkg} 为通孔封装，工艺成熟，风险低"

    # 常规 SMT 封装
    if any(s in pkg for s in ("SOT", "SOP", "SOIC", "TSOP", "SOD")):
        return 1.5, f"{pkg} 为常规 SMT 封装，工艺兼容性好"

    # 标准 0402/0603/0805 无源器件
    if any(s in pkg for s in ("0402", "0603", "0805", "1206", "1210", "2010")):
        return 1.0, f"{pkg} 为标准贴片封装，工艺完全兼容"

    return 2.0, f"{pkg} 封装，工艺需常规验证"


def _score_d9_cost(part: PartIR) -> Tuple[float, str]:
    """D9 成本与商务风险（1–5）。"""
    price = part.unit_price_cny

    if price is None:
        return 3.0, "价格信息缺失，成本不确定性较高"

    if price <= 0:
        return 3.5, "价格数据异常（≤0），需重新确认"

    # 按价格范围判断商务风险（单位：CNY）
    if price < 1.0:
        return 1.0, f"价格极低（¥{price:.2f}），成本风险可控"
    elif price < 10.0:
        return 1.5, f"价格合理（¥{price:.2f}）"
    elif price < 50.0:
        return 2.0, f"价格中等（¥{price:.2f}）"
    elif price < 200.0:
        return 3.0, f"价格较高（¥{price:.2f}），需关注价格波动"
    elif price < 1000.0:
        return 4.0, f"价格高昂（¥{price:.2f}），存在成本/NCNR 风险"
    else:
        return 5.0, f"价格极高（¥{price:.2f}），成本风险不可接受"


def _score_d10_data_integrity(part: PartIR) -> Tuple[float, str]:
    """D10 数据完整性与可追溯风险（1–5）。"""
    missing = []
    if not part.part_number:
        missing.append("MPN")
    if not part.manufacturer:
        missing.append("制造商")
    if not part.package:
        missing.append("封装")
    if not part.lifecycle_status:
        missing.append("生命周期状态")
    if part.input_voltage_min_v is None and part.input_voltage_max_v is None:
        missing.append("输入电压")
    if part.output_voltage_v is None and part.output_current_max_a is None:
        missing.append("输出参数")
    if not part.datasheet_url:
        missing.append("数据手册")

    n = len(missing)
    if n == 0:
        return 1.0, "所有关键字段完整，数据可追溯性良好"
    elif n <= 1:
        return 2.0, f"关键信息基本完整；待补充：{missing[0]}"
    elif n <= 3:
        return 3.0, f"数据字段不完整（缺失 {n} 项：{', '.join(missing[:3])}），需人工确认"
    elif n <= 5:
        return 4.0, f"数据缺口较大（缺失 {n} 项），BOM/AVL 记录可靠性低"
    else:
        return 5.0, f"数据严重缺失（缺失 {n} 项），MPN/制造商无法确认，禁止导入"


# ──────────────────────────────────────────────────────────────────────────────
# 门禁项评估
# ──────────────────────────────────────────────────────────────────────────────

def _evaluate_gate_items(
    part: PartIR,
    constraints: RequirementConstraints,
    d_scores: dict,
) -> Tuple[List[str], int]:
    """
    评估门禁项，返回 (触发的门禁描述列表, 门禁触发的最低等级编号 1–5)。
    若无触发，返回 ([], 0)。
    """
    gate_triggers: List[str] = []
    max_gate_level = 0

    def trigger(desc: str, level: int):
        gate_triggers.append(desc)
        nonlocal max_gate_level
        max_gate_level = max(max_gate_level, level)

    lc = _lifecycle_code(part.lifecycle_status)

    # G1：已停产且无替代料
    if lc in ("obsolete", "eol") and not getattr(part, "replacement_for", []):
        trigger(
            f"[G1] {part.part_number} 已停产（{part.lifecycle_status}），且无批准替代料记录，"
            "禁止新项目导入（门禁 L5）",
            5
        )
    elif lc in ("obsolete", "eol"):
        trigger(
            f"[G1] {part.part_number} 已停产（{part.lifecycle_status}），"
            "即使有替代料记录也需管理层批准（门禁 L4）",
            4
        )

    # G2：要求车规但无 AEC-Q 认证
    grade = (getattr(constraints, "grade", None) or "").lower()
    automotive_req = grade in ("automotive", "车规")
    if automotive_req and not part.automotive_grade:
        trigger(
            f"[G2] 项目要求车规等级，但 {part.part_number} 未通过 AEC-Q 认证，"
            "不可直接用于量产车载产品（门禁 L4）",
            4
        )

    # G3：D1 功能参数极差（得分 = 5）
    if d_scores.get("D1", 0) >= 5.0:
        trigger(
            f"[G3] {part.part_number} 功能参数匹配极差，"
            "关键参数不满足设计需求，禁止导入（门禁 L4）",
            4
        )

    # G4：数据手册和 MPN/制造商均缺失
    if not part.part_number or not part.manufacturer or not part.datasheet_url:
        missing_count = sum([not part.part_number, not part.manufacturer, not part.datasheet_url])
        if missing_count >= 2:
            trigger(
                f"[G4] {part.part_number or '未知料号'} 缺少关键身份信息（数据手册/MPN/制造商），"
                "真实性无法确认（门禁 L4）",
                4
            )

    # G5：D10 数据完整性评分 = 5（完全无法确认）
    if d_scores.get("D10", 0) >= 5.0:
        trigger(
            f"[G5] {part.part_number} 数据完整性极差，"
            "MPN/制造商/规格均无法确认，禁止导入（门禁 L5）",
            5
        )

    # G6：D4 供应连续性评分 ≥ 4.5（库存为零 + 来源不明）
    if d_scores.get("D4", 0) >= 4.5 and d_scores.get("D7", 0) >= 4.0:
        trigger(
            f"[G6] {part.part_number} 库存为零且来源不明，"
            "存在断供+假冒双重风险（门禁 L4）",
            4
        )

    return gate_triggers, max_gate_level


# ──────────────────────────────────────────────────────────────────────────────
# 单料号十维评分
# ──────────────────────────────────────────────────────────────────────────────

def _score_part_ten_dimensions(
    part: PartIR,
    constraints: RequirementConstraints,
    param_score: float,
) -> dict:
    """
    对单个 PartIR 运行十维评分，返回包含评分、理由、风险指数、等级的字典。
    """
    d1, r1 = _score_d1_functional(part, constraints, param_score)
    d2, r2 = _score_d2_reliability(part, constraints)
    d3, r3 = _score_d3_lifecycle(part)
    d4, r4 = _score_d4_supply(part)
    d5, r5 = _score_d5_compliance(part, constraints)
    d6, r6 = _score_d6_quality(part)
    d7, r7 = _score_d7_counterfeit(part)
    d8, r8 = _score_d8_manufacturing(part)
    d9, r9 = _score_d9_cost(part)
    d10, r10 = _score_d10_data_integrity(part)

    d_scores = {
        "D1": d1, "D2": d2, "D3": d3, "D4": d4, "D5": d5,
        "D6": d6, "D7": d7, "D8": d8, "D9": d9, "D10": d10,
    }
    d_reasons = {
        "D1": r1, "D2": r2, "D3": r3, "D4": r4, "D5": r5,
        "D6": r6, "D7": r7, "D8": r8, "D9": r9, "D10": r10,
    }

    # 风险指数 R = Σ(weight/100 × (score-1)/4) × 100
    risk_index = sum(
        (_DIMENSION_WEIGHTS[dim] / 100.0) * ((score - 1.0) / 4.0) * 100.0
        for dim, score in d_scores.items()
    )
    risk_index = round(min(max(risk_index, 0.0), 100.0), 1)

    # 基础等级（按加权总分）
    if risk_index <= 20:
        base_level = 1
        level_code = "L1"
    elif risk_index <= 40:
        base_level = 2
        level_code = "L2"
    elif risk_index <= 60:
        base_level = 3
        level_code = "L3"
    elif risk_index <= 80:
        base_level = 4
        level_code = "L4"
    else:
        base_level = 5
        level_code = "L5"

    # 门禁项
    gate_triggers, gate_level = _evaluate_gate_items(part, constraints, d_scores)

    # 最终等级 = max(加权总分等级, 门禁等级)
    final_level = max(base_level, gate_level)
    final_code = f"L{final_level}"
    final_index = max(risk_index, (final_level - 1) * 20.5) if final_level > base_level else risk_index

    return {
        "part_number": part.part_number,
        "risk_index": round(final_index, 1),
        "risk_level_code": final_code,
        "base_risk_index": risk_index,
        "base_level_code": level_code,
        "gate_triggers": gate_triggers,
        "dimension_scores": d_scores,
        "dimension_reasons": d_reasons,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 主评估函数
# ──────────────────────────────────────────────────────────────────────────────

def _assess_risks(
    constraints: RequirementConstraints,
    scored: List[ScoredPart],
) -> RiskIR:
    """基于十维模型对全部候选器件评估风险，返回 RiskIR。"""
    risk_items: List[RiskItem] = []
    per_part_risks: List[dict] = []
    all_gate_triggers: List[str] = []
    overall_risk_indices: List[float] = []

    recommended = [s for s in scored if s.recommendation_level == "recommended"]

    # ── 无候选时的基础风险 ──────────────────────────────────────────
    if not scored:
        risk_items.append(RiskItem(
            risk_type="supply",
            severity="critical",
            description="检索无结果，供应链风险无法评估；请确认分类/拓扑约束或扩充数据源。",
            mitigation="检查 eZ-PLM API 白名单覆盖情况，或扩充关键词表以覆盖更多物料前缀。",
            dimension="D4",
            risk_score=5.0,
            is_gate_item=True,
            action_required="必须：重新检索或手动添加候选器件后才能继续评估。",
        ))
        return RiskIR(
            overall_risk_level="critical",
            risk_items=risk_items,
            supply_risk_summary="检索无结果，无法评估",
            engineering_risk_summary="检索无结果，无法评估",
            risk_index=100.0,
            risk_level_code="L5",
            gate_items_triggered=["[G0] 无候选器件，评估无法继续"],
            per_part_risks=[],
        )

    # ── 对每个候选器件运行十维评分 ──────────────────────────────────
    for sp in scored:
        part = sp.part
        param_score = getattr(sp.score, "parameter_match_score", 50.0)
        result = _score_part_ten_dimensions(part, constraints, param_score)
        per_part_risks.append(result)
        overall_risk_indices.append(result["risk_index"])
        all_gate_triggers.extend(result["gate_triggers"])

        # 将每个维度中评分 ≥ 3 的项目转化为 RiskItem
        for dim, score in result["dimension_scores"].items():
            if score < 3.0:
                continue
            dim_name = _DIMENSION_NAMES[dim]
            reason = result["dimension_reasons"][dim]
            severity = "critical" if score >= 5.0 else (
                "high" if score >= 4.0 else "medium"
            )
            is_gate = score >= 5.0 or (
                dim == "D3" and score >= 4.5
            ) or (
                dim == "D1" and score >= 5.0
            )
            action = None
            if score >= 5.0:
                action = f"禁止导入 {part.part_number}，需管理层特批或替代方案。"
            elif score >= 4.0:
                action = f"制定 {dim_name} 缓解方案，确认责任人和关闭日期。"
            elif score >= 3.0:
                action = f"补充 {dim_name} 相关证据或验证方案。"

            risk_items.append(RiskItem(
                risk_type=_dim_to_risk_type(dim),
                severity=severity,
                description=f"[{dim} {dim_name}] {part.part_number}：{reason}",
                related_part_number=part.part_number,
                mitigation=_suggest_mitigation(dim, score, part),
                dimension=dim,
                risk_score=score,
                is_gate_item=is_gate,
                action_required=action,
            ))

        # 门禁项触发的 RiskItem
        for gate_desc in result["gate_triggers"]:
            risk_items.append(RiskItem(
                risk_type="gate",
                severity="critical",
                description=gate_desc,
                related_part_number=part.part_number,
                mitigation="需升级评审；参考缓解方案后方可条件准入。",
                is_gate_item=True,
                action_required="立即：提交跨部门评审，记录批准决策。",
            ))

    # ── 去重门禁触发列表 ────────────────────────────────────────────
    seen = set()
    unique_gates = []
    for g in all_gate_triggers:
        if g not in seen:
            seen.add(g)
            unique_gates.append(g)

    # ── 整体风险等级（取推荐器件的最高风险） ────────────────────────
    if recommended:
        rec_pns = {s.part.part_number for s in recommended}
        rec_indices = [
            r["risk_index"] for r in per_part_risks
            if r["part_number"] in rec_pns
        ]
        if rec_indices:
            overall_index = max(rec_indices)
        else:
            overall_index = max(overall_risk_indices) if overall_risk_indices else 0.0
    else:
        overall_index = max(overall_risk_indices) if overall_risk_indices else 0.0

    # 门禁强制拉高整体等级
    # 只计入推荐/备选器件触发的门禁项（not_recommended 已被排除在选型外）
    active_pns = {s.part.part_number for s in scored if s.recommendation_level != "not_recommended"}
    gate_levels = [
        int(r["risk_level_code"][1])
        for r in per_part_risks
        if r["gate_triggers"] and r.get("part_number", "") in active_pns
    ]
    if gate_levels:
        gate_forced_level = max(gate_levels)
        overall_index = max(overall_index, (gate_forced_level - 1) * 20.5)

    overall_index = round(overall_index, 1)
    if overall_index <= 20:
        overall_level_code = "L1"
        overall_level = "low"
    elif overall_index <= 40:
        overall_level_code = "L2"
        overall_level = "low_medium"
    elif overall_index <= 60:
        overall_level_code = "L3"
        overall_level = "medium"
    elif overall_index <= 80:
        overall_level_code = "L4"
        overall_level = "high"
    else:
        overall_level_code = "L5"
        overall_level = "critical"

    # ── 供应链摘要 & 工程摘要 ────────────────────────────────────────
    supply_dims = {"D3", "D4", "D6", "D7", "D9"}
    eng_dims = {"D1", "D2", "D5", "D8", "D10"}

    supply_issues = [
        r for r in risk_items
        if r.risk_type in ("supply", "gate") and (r.dimension in supply_dims or r.is_gate_item)
    ]
    eng_issues = [
        r for r in risk_items
        if r.risk_type in ("engineering", "compliance") and r.dimension in eng_dims
    ]

    if supply_issues:
        supply_summary = f"共 {len(supply_issues)} 项供应链风险；最高严重度：" + \
            ("严重" if any(r.severity == "critical" for r in supply_issues) else
             "高" if any(r.severity == "high" for r in supply_issues) else "中")
    else:
        supply_summary = "供应链风险可控（L1/L2）"

    if eng_issues:
        eng_summary = f"共 {len(eng_issues)} 项工程风险；主要关注：" + \
            "；".join(r.description[:40] + "…" for r in eng_issues[:2])
    else:
        eng_summary = "工程参数匹配正常，技术风险可控"

    return RiskIR(
        overall_risk_level=overall_level,
        risk_items=risk_items,
        supply_risk_summary=supply_summary,
        engineering_risk_summary=eng_summary,
        risk_index=overall_index,
        risk_level_code=overall_level_code,
        gate_items_triggered=unique_gates,
        per_part_risks=per_part_risks,
    )


def _dim_to_risk_type(dim: str) -> str:
    """将维度代码映射到 risk_type 枚举值。"""
    supply_dims = {"D3", "D4", "D6", "D7", "D9"}
    compliance_dims = {"D5"}
    if dim in supply_dims:
        return "supply"
    if dim in compliance_dims:
        return "compliance"
    return "engineering"


def _suggest_mitigation(dim: str, score: float, part: PartIR) -> str:
    """根据维度和评分生成具体缓解建议。"""
    suggestions = {
        "D1": {
            3: "进行样品实测或原理图仿真，确认参数裕量满足实际工况。",
            4: "更换参数匹配度更高的型号；若必须使用，需工程变更评审（ECR）批准。",
            5: "禁止使用；重新选型。",
        },
        "D2": {
            3: "补充可靠性测试报告或制造商应用说明；评估温度降额设计。",
            4: "进行应用环境验证测试（高温/低温/振动）；车规要求需 AEC-Q 认证。",
            5: "禁止用于目标应用；需重新选型。",
        },
        "D3": {
            3: "监控 PCN/PDN 信息；预研替代料。",
            4: "立即启动替代料验证；评估 LTB 安全库存数量。",
            5: "必须切换至在产型号；启用批准替代料。",
        },
        "D4": {
            3: "监控库存动态；与供应商确认补货周期；考虑建立安全库存。",
            4: "立即向分销商预定；启用备选方案；引入第二供应源。",
            5: "停止使用该器件；切换至库存充足型号。",
        },
        "D5": {
            3: "向制造商索取最新 RoHS/REACH 声明（E3 级证据）。",
            4: "核查目标市场法规适用性；补充 AEC-Q/PPAP 等合规证明。",
            5: "法规不合规，禁止导入目标市场产品；须替换合规器件。",
        },
        "D6": {
            3: "进行首件检验（IQC）；要求供应商提供 ISO 9001 证书。",
            4: "实施供应商现场审核；要求 8D 报告和历史质量记录。",
            5: "禁止从该供应商采购；更换经认可的原厂或授权分销商。",
        },
        "D7": {
            3: "要求供应商提供完整 CoC 和批次追溯文件。",
            4: "对该批次器件进行第三方外观/功能检测（参考 SAE AS6171）。",
            5: "疑似假冒，禁止使用；向 ERAI/GIDEP 报告并追溯来源。",
        },
        "D8": {
            3: "核查 MSL 等级和存储条件；进行 SMT 工艺验证。",
            4: "执行烘烤程序（J-STD-033）；评估焊接温度曲线兼容性。",
            5: "该器件与现有工艺不兼容；更换封装或调整工艺流程。",
        },
        "D9": {
            3: "与采购协商长期价格协议（LTA）；评估替代器件的成本优势。",
            4: "评估替代方案；确认 NCNR 条款影响；建立成本预警机制。",
            5: "成本超出项目预算；必须寻找成本合理的替代方案。",
        },
        "D10": {
            3: "补充缺失的 MPN、制造商、封装、数据手册等字段。",
            4: "从官方渠道核查并补充所有关键字段；更新 AVL/AML 记录。",
            5: "数据严重缺失，禁止导入 BOM；需重新采集完整器件数据。",
        },
    }
    level = 5 if score >= 5.0 else (4 if score >= 4.0 else 3)
    return suggestions.get(dim, {}).get(level, "补充相关证据并制定缓解计划。")


# ──────────────────────────────────────────────────────────────────────────────
# Markdown 报告生成
# ──────────────────────────────────────────────────────────────────────────────

_LEVEL_EMOJI = {
    "L1": "✅", "L2": "🟡", "L3": "🟠", "L4": "🔴", "L5": "⛔",
}
_LEVEL_LABEL = {
    "L1": "低风险",
    "L2": "可控风险",
    "L3": "中高风险",
    "L4": "高风险",
    "L5": "极高风险",
}
_LEVEL_ACTION = {
    "L1": "可导入；纳入常规 PCN/EOL 监控。",
    "L2": "可条件导入；关闭轻微行动项后准入。",
    "L3": "需跨部门评审；制定缓解计划和责任人。",
    "L4": "原则上不建议导入；需项目负责人/质量/供应链联合批准。",
    "L5": "禁止导入或仅限工程样机；量产需管理层特批。",
}


def _build_summary(
    constraints: RequirementConstraints,
    scored: List[ScoredPart],
    risks: RiskIR,
) -> str:
    recommended = [s for s in scored if s.recommendation_level == "recommended"]
    backup = [s for s in scored if s.recommendation_level == "backup"]
    not_rec = [s for s in scored if s.recommendation_level not in ("recommended", "backup")]

    lc = risks.risk_level_code or "L3"
    emoji = _LEVEL_EMOJI.get(lc, "?")
    label = _LEVEL_LABEL.get(lc, "未知")
    action = _LEVEL_ACTION.get(lc, "")
    ri = risks.risk_index or 0.0

    lines = [
        "---",
        "",
    ]

    # ── 推荐器件表格 ──────────────────────────────────────────────────
    if recommended:
        lines += [
            "### 推荐器件",
            "",
            "| # | 型号 | 厂商 | 评分 | 风险 | 主要特征 |",
            "|---|------|------|:----:|:----:|----------|",
        ]
        for idx, s in enumerate(recommended, start=1):
            p = s.part
            mfr = p.manufacturer or "—"
            pkg = p.package or ""
            dom_tag = " 🇨🇳" if getattr(p, "is_domestic", False) else ""
            part_risk = next(
                (r for r in risks.per_part_risks if r.get("part_number") == p.part_number),
                None,
            )
            part_lc = part_risk["risk_level_code"] if part_risk else "—"
            part_ri = part_risk["risk_index"] if part_risk else "—"
            part_emoji = _LEVEL_EMOJI.get(part_lc, "?")

            features = []
            good = [r for r in (s.score.reasons or []) if "✓" in r or "满足" in r][:2]
            for g in good:
                short = g.replace("✓ ", "").split("，")[0][:30]
                features.append(short)
            feature_str = "；".join(features) if features else pkg[:20] if pkg else "—"
            lines.append(
                f"| {idx} | `{p.part_number}`{dom_tag} | {mfr} | **{int(s.score.total_score)}** | {part_emoji} {part_lc} | {feature_str} |"
            )
        lines.append("")

    # ── 备选器件表格 ──────────────────────────────────────────────────
    if backup:
        lines += [
            "### 备选器件",
            "",
            "| # | 型号 | 厂商 | 评分 | 风险 |",
            "|---|------|------|:----:|:----:|",
        ]
        start_idx = len(recommended) + 1
        for idx, s in enumerate(backup, start=start_idx):
            p = s.part
            dom_tag = " 🇨🇳" if getattr(p, "is_domestic", False) else ""
            part_risk = next(
                (r for r in risks.per_part_risks if r.get("part_number") == p.part_number),
                None,
            )
            part_lc = part_risk["risk_level_code"] if part_risk else "—"
            part_emoji = _LEVEL_EMOJI.get(part_lc, "?")
            lines.append(
                f"| {idx} | `{p.part_number}`{dom_tag} | {p.manufacturer or '—'} | {int(s.score.total_score)} | {part_emoji} {part_lc} |"
            )
        lines.append("")

    # ── 风险维度表 ──────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "### 风险评估维度",
        "",
        "| 维度 | 名称 | 权重 | 最优评分 |",
        "|------|------|:----:|:--------:|",
    ]
    if risks.per_part_risks:
        for dim, weight in _DIMENSION_WEIGHTS.items():
            max_score = max(
                r["dimension_scores"].get(dim, 1.0) for r in risks.per_part_risks
            )
            score_emoji = "🔴" if max_score >= 4.0 else ("🟠" if max_score >= 3.0 else "✅")
            lines.append(
                f"| {dim} | {_DIMENSION_NAMES[dim]} | {weight}% | {score_emoji} {max_score:.0f}/5 |"
            )
    lines.append("")

    # ── 摘要项 ──────────────────────────────────────────────────
    if risks.gate_items_triggered:
        lines += ["**门禁触发**\n"]
        for g in risks.gate_items_triggered:
            lines.append(f"- {g}")
        lines.append("")

    lines += [
        f"- **供应链风险**：{risks.supply_risk_summary}" if risks.supply_risk_summary else "",
        f"- **工程风险**：{risks.engineering_risk_summary}" if risks.engineering_risk_summary else "",
    ]

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 公开 API
# ──────────────────────────────────────────────────────────────────────────────

def build_report(
    constraints: RequirementConstraints,
    scored: List[ScoredPart],
    evidence: List,
    request_id: Optional[str] = None,
) -> SelectionReport:
    """
    主入口：执行十维风险评估，构建完整 SelectionReport。

    参数：
        constraints  — 解析后的需求约束
        scored       — 已评分的候选器件列表（含 recommendation_level）
        evidence     — 证据链列表
        request_id   — 请求 ID（可选，自动生成）

    返回：
        SelectionReport（含 risks.risk_index、risks.risk_level_code、
        risks.per_part_risks、risks.gate_items_triggered）
    """
    if request_id is None:
        request_id = str(uuid.uuid4())

    recommended = [s for s in scored if s.recommendation_level == "recommended"]
    risks = _assess_risks(constraints, scored)
    summary = _build_summary(constraints, scored, risks)

    return SelectionReport(
        request_id=request_id,
        user_input=constraints.raw_input if constraints else "",
        constraints=constraints,
        candidates=scored,
        recommended_parts=recommended,
        risks=risks,
        evidence=evidence,
        summary_markdown=summary,
    )
