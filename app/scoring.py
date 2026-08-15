"""
scoring.py — 元器件选型多维量化评分引擎 v2.0

评估体系（参照《电子元器件选型结果量化评估与评分推荐机制研究报告 V1.0》，
结合《电子元器件选型风险评估研究报告 v1.0》十维风险模型）：

  Gate   硬门槛  Pass / Conditional / Fail
  F      适配度  D1–D7 七维几何加权聚合（0–100，越高越好）
  R      风险分  R1–R9 九维加权 + 尾部修正 + 交互项（0–100，越低越好）
  C      可信度  来源可靠度 × 时效因子 × 完整性因子（0–100，越高越好）
  B      稳健性  权重扰动下排序稳定性（简化三扰动验证，0–100）
  RS     推荐分  G × 100 × (F/100)^α × ((100-R)/100)^β × (C/100)^γ × (B/100)^δ

推荐等级映射：
  A 优先推荐 → recommendation_level = "recommended"  (RS≥85，R≤20，C≥85)
  B 推荐     → recommendation_level = "recommended"  (RS≥75，R≤35，C≥75)
  C 条件推荐 → recommendation_level = "backup"       (RS≥65 或 Conditional Gate)
  D 不优先   → recommendation_level = "backup"       (RS<65 或 R>50)
  E 禁止推荐 → recommendation_level = "not_recommended" (Gate Fail)

ScoreBreakdown 字段映射：
  parameter_match_score → D1 功能与参数适配分（0–100）
  supply_risk_score     → D4 供应链适配分（0–100，高=供应好）
  cost_score            → D7 商业/成本适配分（0–100）
  domestic_score        → 国产化加分（0–100）
  evidence_score        → C 证据可信度（0–100）
  total_score           → RS 最终推荐分（0–100）
  llm_application_score → F 综合适配度（0–100）
  llm_design_risk_score → 100−R 风险倒置分（0–100，高=低风险）

参考：ISO 31000:2018 / IEC 31010:2019 / IEC 60812:2018 / IEC 62402:2019 /
      OECD/JRC Composite Indicators Handbook
"""

from __future__ import annotations

import math
import os
import re as _re_core
import random
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple

from .schemas import RequirementConstraints, PartIR, ScoredPart, ScoreBreakdown


# ──────────────────────────────────────────────────────────────────────────────
# 全局常量
# ──────────────────────────────────────────────────────────────────────────────

_MAX_RECOMMENDED = 5    # 最多推荐 Top N 款
_MAX_LISTED = 15        # BOM 最多列出 N 款（含推荐 + 备选）
_GEO_EPSILON = 1e-3     # 几何聚合防零下界

# ── 统一可信制造商白名单（M1：合并 _KNOWN_QUALITY 与 _REPUTABLE）─────
_TRUSTED_MANUFACTURERS: set = {
    # 国际一线原厂
    "ti", "texas instruments",
    "adi", "analog devices", "linear technology",
    "st", "stmicroelectronics",
    "microchip", "microchip technology",
    "infineon", "ir",
    "onsemi", "on semiconductor", "fairchild",
    "maxim", "maxim integrated",
    "mps", "monolithic power",
    "rohm", "rohm semiconductor",
    "nxp", "nxp semiconductors", "renesas",
    "yageo", "panasonic", "murata", "tdk",
    "vishay", "samsung electro-mechanics", "diodes incorporated",
    # 知名国产原厂
    "圣邦微", "sgmicro", "思瑞浦", "3peak",
    "南芯", "杰华特", "joulwatt",
    "华润矽威", "crmt", "芯朋微",
    "英集芯", "injoinic", "台湾矽力杰", "silergy",
}

# 模型版本（用于审计和复现）
SCORING_MODEL_VERSION = "v2.0"
WEIGHT_PROFILE_VERSION = "2026-06-18-default"

# ──────────────────────────────────────────────────────────────────────────────
# 场景权重配置表
# 一级适配度维度权重（D1–D7，合计 = 1.0）
# 推荐分指数 α/β/γ/δ（合计 = 1.0）
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SceneProfile:
    """场景化权重配置。"""
    name: str
    # 七维适配度权重
    fit_w: Dict[str, float] = field(default_factory=dict)
    # 九维风险权重
    risk_w: Dict[str, float] = field(default_factory=dict)
    # 推荐分指数 [alpha, beta, gamma, delta]
    exponents: Tuple[float, float, float, float] = (0.55, 0.25, 0.10, 0.10)
    # 国产加分系数（叠加在 RS 之上，乘法因子）
    domestic_bonus: float = 0.04


_PROFILES: Dict[str, SceneProfile] = {
    "industrial": SceneProfile(
        name="工业/通用",
        fit_w={
            "D1": 0.25,  # 功能与参数适配
            "D2": 0.15,  # 可靠性与环境
            "D3": 0.10,  # 质量与资格证据
            "D4": 0.20,  # 供应与生命周期（工业长寿命强化）
            "D5": 0.10,  # 制造与集成
            "D6": 0.10,  # 合规与可持续性
            "D7": 0.10,  # 商业与总成本
        },
        risk_w={
            "R1": 0.15, "R2": 0.15, "R3": 0.15, "R4": 0.15,
            "R5": 0.10, "R6": 0.10, "R7": 0.10,
            "R8": 0.05, "R9": 0.05,
        },
        exponents=(0.55, 0.25, 0.10, 0.10),
        domestic_bonus=0.04,
    ),
    "automotive": SceneProfile(
        name="汽车/车规",
        fit_w={
            "D1": 0.22,  # 功能参数
            "D2": 0.22,  # 可靠性（车规强化）
            "D3": 0.18,  # 质量/AEC 资格（车规强化）
            "D4": 0.15,  # 供应
            "D5": 0.10,  # 制造
            "D6": 0.08,  # 合规
            "D7": 0.05,  # 成本（降权）
        },
        risk_w={
            "R1": 0.15, "R2": 0.18, "R3": 0.15, "R4": 0.12,
            "R5": 0.12, "R6": 0.10, "R7": 0.08,
            "R8": 0.05, "R9": 0.05,
        },
        exponents=(0.45, 0.35, 0.10, 0.10),
        domestic_bonus=0.03,
    ),
    "consumer": SceneProfile(
        name="消费类",
        fit_w={
            "D1": 0.25,  # 参数
            "D2": 0.10,  # 可靠性（降权）
            "D3": 0.08,  # 质量
            "D4": 0.15,  # 供应
            "D5": 0.10,  # 制造
            "D6": 0.07,  # 合规
            "D7": 0.25,  # 成本（强化）
        },
        risk_w={
            "R1": 0.15, "R2": 0.10, "R3": 0.15, "R4": 0.15,
            "R5": 0.10, "R6": 0.10, "R7": 0.10,
            "R8": 0.05, "R9": 0.10,
        },
        exponents=(0.60, 0.20, 0.10, 0.10),
        domestic_bonus=0.05,
    ),
}


def _get_profile(constraints: RequirementConstraints) -> SceneProfile:
    """根据需求约束选择场景权重配置。"""
    grade = (getattr(constraints, "grade", None) or "").lower().strip()
    if grade in ("automotive", "车规", "aec", "aeq"):
        return _PROFILES["automotive"]
    if grade in ("consumer", "消费", "消费类"):
        return _PROFILES["consumer"]
    return _PROFILES["industrial"]


# ──────────────────────────────────────────────────────────────────────────────
# 门禁评估（Gate）
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class GateResult:
    status: str          # "PASS" / "CONDITIONAL" / "FAIL"
    fail_reasons: List[str] = field(default_factory=list)
    conditional_reasons: List[str] = field(default_factory=list)
    gate_factor: float = 1.0   # PASS=1.00, CONDITIONAL=0.85, FAIL=0.0


def _gate_evaluate(
    part: PartIR,
    constraints: RequirementConstraints,
) -> GateResult:
    """
    硬门槛评估（规则引擎，不可被高总分抵消）。

    门槛优先级：
      G1 电气边界     G2 温度边界     G3 生命周期
      G4 车规资格     G5 MPN/制造商缺失
    """
    fails: List[str] = []
    conds: List[str] = []

    # G1 电气边界：输出电流不足（硬失败）
    if (constraints.output_current_a is not None
            and part.output_current_max_a is not None
            and part.output_current_max_a < constraints.output_current_a * 0.90):
        fails.append(
            f"[G1] 输出电流不足：需求 {constraints.output_current_a}A，"
            f"最大 {part.output_current_max_a}A（低于 90% 门槛）"
        )

    # G1 输入电压范围不覆盖标称值（硬失败）
    if (constraints.input_voltage_nominal_v is not None
            and part.input_voltage_min_v is not None
            and part.input_voltage_max_v is not None):
        vin = constraints.input_voltage_nominal_v
        if not (part.input_voltage_min_v <= vin <= part.input_voltage_max_v):
            fails.append(
                f"[G1] 输入电压标称值超出范围："
                f"标称 {vin}V，器件范围 {part.input_voltage_min_v}–{part.input_voltage_max_v}V"
            )

    # G2 温度范围不满足（硬失败）
    if (constraints.temperature_min_c is not None
            and constraints.temperature_max_c is not None
            and part.temperature_min_c is not None
            and part.temperature_max_c is not None):
        t_min_ok = part.temperature_min_c <= constraints.temperature_min_c
        t_max_ok = part.temperature_max_c >= constraints.temperature_max_c
        if not t_min_ok or not t_max_ok:
            fails.append(
                f"[G2] 工作温度范围不满足：需求 "
                f"{constraints.temperature_min_c}–{constraints.temperature_max_c}°C，"
                f"器件 {part.temperature_min_c}–{part.temperature_max_c}°C"
            )

    # G3 生命周期：Obsolete/EOL 新设计禁用
    lc_status = (part.lifecycle_status or "").lower().strip()
    if lc_status in ("obsolete", "discontinued"):
        fails.append(
            f"[G3] 器件已停产（{part.lifecycle_status}），禁止新设计导入"
        )
    elif lc_status == "eol":
        conds.append(
            f"[G3] 器件 EOL（End of Life），可条件导入，需评估 LTB 策略和替代方案"
        )
    elif lc_status in ("nrnd",):
        conds.append(
            "[G3] 器件 NRND（不推荐新设计），可条件导入，需评估替代料"
        )

    # G4 车规资格：项目要求但器件无 AEC-Q 认证
    grade = (getattr(constraints, "grade", None) or "").lower()
    automotive_req = grade in ("automotive", "车规")
    if automotive_req and not part.automotive_grade:
        fails.append(
            "[G4] 项目要求 AEC-Q 车规认证，但器件未通过，不可用于车载量产"
        )

    # G5 数据底线：MPN 或制造商缺失
    if not part.part_number:
        fails.append("[G5] MPN 缺失，无法进行工程判断")
    elif not part.manufacturer:
        conds.append("[G5] 制造商信息缺失，可追溯性存疑，需补充")

    # 决定门禁状态
    if fails:
        return GateResult("FAIL", fails, conds, 0.0)
    if conds:
        return GateResult("CONDITIONAL", fails, conds, 0.85)
    return GateResult("PASS", fails, conds, 1.0)


# ──────────────────────────────────────────────────────────────────────────────
# 参数归一化工具
# ──────────────────────────────────────────────────────────────────────────────

def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _benefit(x: Optional[float], low: float, target: float) -> float:
    """效益型：数值越高越好（线性从 0 到 100）。"""
    if x is None:
        return 0.0
    if target <= low:
        return 100.0
    return _clip((x - low) / (target - low) * 100.0)


def _cost_type(x: Optional[float], worst: float, best: float) -> float:
    """成本型：数值越低越好。"""
    if x is None:
        return 50.0
    if best >= worst:
        return 50.0
    return _clip((worst - x) / (worst - best) * 100.0)


# ──────────────────────────────────────────────────────────────────────────────
# 七维适配度（Fit Score F）
# ──────────────────────────────────────────────────────────────────────────────

def _fit_d1_functional(
    part: PartIR,
    constraints: RequirementConstraints,
) -> Tuple[float, List[str]]:
    """D1 功能与参数适配（0–100）。"""
    scores = []
    reasons = []

    # 输入电压覆盖
    if (constraints.input_voltage_nominal_v is not None
            and part.input_voltage_min_v is not None
            and part.input_voltage_max_v is not None):
        vin = constraints.input_voltage_nominal_v
        if part.input_voltage_min_v <= vin <= part.input_voltage_max_v:
            # 电压余量越大越好（分别考虑上下余量）
            margin_low = (vin - part.input_voltage_min_v) / max(vin, 1e-6)
            margin_high = (part.input_voltage_max_v - vin) / max(part.input_voltage_max_v, 1e-6)
            margin_score = _clip(50.0 + min(margin_low, margin_high) * 200.0)
            scores.append(margin_score)
            reasons.append(
                f"✓ 输入电压覆盖（{part.input_voltage_min_v}–{part.input_voltage_max_v}V"
                f"，标称 {vin}V，余量 {min(margin_low, margin_high)*100:.0f}%）"
            )
        else:
            scores.append(0.0)
            reasons.append(
                f"✗ 输入电压不匹配（标称 {vin}V，器件 "
                f"{part.input_voltage_min_v}–{part.input_voltage_max_v}V）"
            )

    # 输出电压精度
    if constraints.output_voltage_v is not None and part.output_voltage_v is not None:
        delta = abs(part.output_voltage_v - constraints.output_voltage_v)
        rel_err = delta / max(constraints.output_voltage_v, 1e-6)
        if rel_err < 0.01:
            scores.append(100.0)
            reasons.append(f"✓ 输出电压匹配（{part.output_voltage_v}V ≈ 需求 {constraints.output_voltage_v}V）")
        elif rel_err <= 0.05:
            scores.append(70.0)
            reasons.append(f"~ 输出电压偏差 {rel_err*100:.1f}%（{part.output_voltage_v}V vs {constraints.output_voltage_v}V）")
        else:
            scores.append(20.0)
            reasons.append(f"⚠ 输出电压偏差 {rel_err*100:.1f}%（较大）")

    # 输出电流裕量
    if constraints.output_current_a is not None and part.output_current_max_a is not None:
        ratio = part.output_current_max_a / max(constraints.output_current_a, 1e-6)
        if ratio >= 1.0:
            # 1.0~2.0 之间线性加分，超过 2.0 不继续增加（避免过设计加分）
            s = _clip(50.0 + (min(ratio, 2.0) - 1.0) * 50.0)
            reasons.append(
                f"✓ 输出电流满足（{part.output_current_max_a}A，裕量 {(ratio-1)*100:.0f}%）"
            )
        else:
            s = _clip(ratio * 50.0 - 10.0)
            reasons.append(
                f"✗ 输出电流不足（需 {constraints.output_current_a}A，最大 {part.output_current_max_a}A）"
            )
        scores.append(s)

    # 温度范围覆盖裕量
    if (constraints.temperature_min_c is not None
            and constraints.temperature_max_c is not None
            and part.temperature_min_c is not None
            and part.temperature_max_c is not None):
        req_span = constraints.temperature_max_c - constraints.temperature_min_c
        part_span = part.temperature_max_c - part.temperature_min_c
        if (part.temperature_min_c <= constraints.temperature_min_c
                and part.temperature_max_c >= constraints.temperature_max_c):
            excess = max(0, part_span - req_span)
            s = _clip(70.0 + excess / max(req_span, 1.0) * 30.0)
            reasons.append(
                f"✓ 温度范围覆盖（{part.temperature_min_c}–{part.temperature_max_c}°C）"
            )
        else:
            s = 0.0
            reasons.append(
                f"✗ 温度范围不足（需 {constraints.temperature_min_c}–{constraints.temperature_max_c}°C，"
                f"器件 {part.temperature_min_c}–{part.temperature_max_c}°C）"
            )
        scores.append(s)

    if not scores:
        return 50.0, ["参数信息不足，使用保守默认分（50）"]

    return sum(scores) / len(scores), reasons


def _fit_d2_reliability(
    part: PartIR,
    constraints: RequirementConstraints,
) -> Tuple[float, List[str]]:
    """D2 可靠性与环境适配（0–100）。

    v2 新增：利用 efficiency_pct 和 features（TSD/OCP/SYNC）调整分数。
    """
    score = 50.0
    reasons = []

    grade = (getattr(constraints, "grade", None) or "").lower()
    automotive_req = grade in ("automotive", "车规")

    if part.automotive_grade:
        score = 95.0
        reasons.append("✓ 通过 AEC-Q 车规认证，可靠性证据充分")
    elif automotive_req:
        score = 20.0
        reasons.append("✗ 要求车规但无 AEC-Q 认证，可靠性不满足车规要求")
    else:
        # 温度范围宽度作为可靠性代理指标
        if part.temperature_min_c is not None and part.temperature_max_c is not None:
            span = part.temperature_max_c - part.temperature_min_c
            if span >= 165:   # -40~125 宽温
                score = 85.0
                reasons.append(f"✓ 宽温范围（{part.temperature_min_c}–{part.temperature_max_c}°C）")
            elif span >= 125: # -40~85 或类似
                score = 70.0
                reasons.append(f"温度范围适中（{part.temperature_min_c}–{part.temperature_max_c}°C）")
            else:
                score = 50.0
                reasons.append(f"温度范围较窄（{part.temperature_min_c}–{part.temperature_max_c}°C）")
        else:
            score = 40.0
            reasons.append("温度规格未知，可靠性证据不足")

    # v2：效率加分（高效率 = 更低热应力 = 更高可靠性裕量）
    eff = getattr(part, "efficiency_pct", None)
    if eff is not None and eff > 0:
        if eff >= 95.0:
            score = min(100.0, score + 5.0)
            reasons.append(f"✓ 典型效率 {eff:.0f}%，热应力低")
        elif eff >= 90.0:
            score = min(100.0, score + 2.0)
            reasons.append(f"效率 {eff:.0f}%，热管理适中")
        elif eff < 80.0:
            score = max(0.0, score - 5.0)
            reasons.append(f"⚠ 效率仅 {eff:.0f}%，散热需重点关注")

    # v2：保护特性加分（TSD/OCP 提升可靠性）
    feats = getattr(part, "features", []) or []
    prot_feats = [f for f in feats if f in ("TSD", "OCP", "OVP")]
    if len(prot_feats) >= 2:
        score = min(100.0, score + 3.0)
        reasons.append(f"✓ 内置保护特性（{'、'.join(prot_feats)}），故障模式风险降低")
    elif prot_feats:
        score = min(100.0, score + 1.0)
        reasons.append(f"内置 {prot_feats[0]} 保护")

    return score, reasons


def _fit_d3_quality(part: PartIR) -> Tuple[float, List[str]]:
    """D3 质量与资格证据（0–100）。"""
    mfr = (part.manufacturer or "").lower()
    if not mfr:
        return 30.0, ["制造商信息缺失，质量无法评估"]

    for known in _TRUSTED_MANUFACTURERS:
        if known in mfr or mfr in known:
            auto_tag = "（AEC-Q 认证）" if part.automotive_grade else ""
            dom_tag = " 🇨🇳" if getattr(part, "is_domestic", False) else ""
            return 85.0, [f"✓ {part.manufacturer}{dom_tag}{auto_tag}，知名原厂，质量体系成熟"]

    if getattr(part, "is_domestic", False):
        return 65.0, [f"{part.manufacturer} 🇨🇳 为国产厂商，建议核查 ISO 9001 质量体系"]

    return 50.0, [f"{part.manufacturer} 非主流原厂，建议首件检验和供应商评估"]


def _fit_d4_supply(part: PartIR) -> Tuple[float, List[str]]:
    """D4 供应与生命周期（0–100，高=供应好）。"""
    reasons = []
    scores = []

    # 生命周期评分
    lc = (part.lifecycle_status or "").lower().strip()
    if lc in ("active", "生产中", "在产"):
        lc_score = 90.0
        reasons.append("✓ 生命周期 Active，供货稳定")
    elif lc == "nrnd":
        lc_score = 50.0
        reasons.append("⚠ NRND（不推荐新设计），建议评估替代料")
    elif lc == "ltb":
        lc_score = 25.0
        reasons.append("⚠ LTB（最后采购期），需建立安全库存")
    elif lc == "eol":
        lc_score = 15.0
        reasons.append("⚠ EOL（停产进行中），供应风险高")
    elif lc in ("obsolete", "discontinued"):
        lc_score = 5.0
        reasons.append("✗ 已停产，供应无保障")
    else:
        lc_score = 40.0
        reasons.append("生命周期状态未知，供应不确定性高")
    scores.append(lc_score)

    # 库存数据不可用（eZ-PLM v1 不返回实时库存），按中等默认值处理
    scores.append(40.0)
    reasons.append("库存信息不可用，供应不确定性参考中值")

    return sum(scores) / len(scores), reasons


def _fit_d5_manufacturing(part: PartIR) -> Tuple[float, List[str]]:
    """D5 制造与集成（0–100）。

    v2 新增：利用 features（INT_SW）和 switching_frequency_khz 评估集成度与工艺难度。
    """
    pkg = (part.package or "").upper()
    feats = getattr(part, "features", []) or []
    fsw = getattr(part, "switching_frequency_khz", None)

    if not pkg:
        base = 40.0
        reasons = ["封装信息缺失，工艺适配性无法评估"]
    elif any(s in pkg for s in ("0402", "0603", "0805", "1206", "1210", "2010", "0201")):
        base = 95.0
        reasons = [f"✓ {pkg} 标准贴片封装，工艺成熟"]
    elif any(s in pkg for s in ("SOT23", "SOT-23", "SOD", "SOIC", "SSOP", "SOP", "TSOP", "MSOP")):
        base = 88.0
        reasons = [f"✓ {pkg} 常规 SMT 封装，工艺兼容性好"]
    elif any(s in pkg for s in ("QFN", "DFN", "SON", "HSOIC", "SOIC-8")):
        base = 75.0
        reasons = [f"{pkg} 需控制焊盘设计和回流温度曲线"]
    elif any(s in pkg for s in ("DIP", "PDIP", "THT", "SIP", "D2PAK", "TO-220", "TO220", "TO-263", "TO263")):
        base = 70.0
        reasons = [f"{pkg} 通孔封装，工艺成熟，但占板面积较大"]
    elif any(s in pkg for s in ("BGA", "CSP", "WLCSP", "LGA", "FCBGA", "FBGA")):
        base = 55.0
        reasons = [f"{pkg} 高密度封装，需 X-Ray 检验和严格回流工艺"]
    else:
        base = 70.0
        reasons = [f"{pkg} 封装，需常规工艺验证"]

    # v2：内置开关加分（减少外部 MOSFET，降低 BOM 复杂度）
    if "INT_SW" in feats:
        base = min(100.0, base + 5.0)
        reasons.append("✓ 内置功率管（INT_SW），BOM 精简，焊接点减少")

    # v2：软启动加分（减少上电冲击，提升生产良率）
    if "SS" in feats:
        base = min(100.0, base + 2.0)
        reasons.append("✓ 内置软启动（SS），降低上电应力")

    # v2：开关频率提示（高频 = 小感量，有利集成；但 EMI 更严苛）
    if fsw is not None:
        if fsw >= 1000:
            reasons.append(f"开关频率 {fsw:.0f} kHz，外围元件尺寸小，EMI 需额外关注")
        elif fsw >= 500:
            reasons.append(f"开关频率 {fsw:.0f} kHz，外围元件尺寸适中")
        elif fsw > 0:
            reasons.append(f"开关频率 {fsw:.0f} kHz，外围电感/电容尺寸较大")

    return _clip(base), reasons


def _fit_d6_compliance(
    part: PartIR,
    constraints: RequirementConstraints,
) -> Tuple[float, List[str]]:
    """D6 合规与可持续性（0–100）。"""
    source = (getattr(part, "source", "unknown") or "").lower()

    if part.automotive_grade:
        return 85.0, ["✓ AEC-Q 认证，主要合规证据充分"]

    grade = (getattr(constraints, "grade", None) or "").lower()
    if grade in ("automotive", "车规") and not part.automotive_grade:
        return 20.0, ["✗ 要求车规，但缺少 AEC-Q/IATF 合规证据"]

    if source in ("ezplm", "api", "ezplm_api"):
        return 72.0, ["eZ-PLM 平台数据，RoHS/REACH 基础合规可信（E1 级证据）"]

    return 50.0, ["合规证据等级低，建议向制造商索取正式 RoHS/REACH 声明（E3 级）"]


def _fit_d7_commercial(part: PartIR, constraints: RequirementConstraints) -> Tuple[float, List[str]]:
    """D7 商业与总成本（0–100）。按器件等级调整价格区间（M4）。"""
    price = part.unit_price_cny

    if price is None:
        return 50.0, ["价格信息缺失，成本无法评估"]
    if price <= 0:
        return 40.0, ["价格数据异常（≤0）"]

    # ── M4：按器件等级调整成本区间 ──
    grade = (getattr(constraints, "grade", None) or "").lower()
    if grade in ("automotive", "车规", "aec", "aeq"):
        worst, best = 300.0, 2.0
    elif grade in ("industrial", "工业", "工业级"):
        worst, best = 150.0, 1.0
    else:
        worst, best = 100.0, 0.5  # 消费级 / 默认

    s = _cost_type(price, worst=worst, best=best)

    if price < 1.0:
        msg = f"✓ 价格极低（¥{price:.2f}），成本优势明显"
    elif price < 10.0:
        msg = f"✓ 价格合理（¥{price:.2f}）"
    elif price < 50.0:
        msg = f"价格中等（¥{price:.2f}）"
    elif price < 200.0:
        msg = f"⚠ 价格较高（¥{price:.2f}），关注成本波动"
    else:
        msg = f"⚠ 价格高昂（¥{price:.2f}），成本风险较高"
        s = min(s, 30.0)

    return s, [msg]


def _compute_fit_score(
    part: PartIR,
    constraints: RequirementConstraints,
) -> Tuple[float, Dict[str, float], List[str]]:
    """
    计算七维适配度 F（加权几何聚合）。

    返回：(F, {dim: score}, combined_reasons)
    """
    profile = _get_profile(constraints)

    d1, r1 = _fit_d1_functional(part, constraints)
    d2, r2 = _fit_d2_reliability(part, constraints)
    d3, r3 = _fit_d3_quality(part)
    d4, r4 = _fit_d4_supply(part)
    d5, r5 = _fit_d5_manufacturing(part)
    d6, r6 = _fit_d6_compliance(part, constraints)
    d7, r7 = _fit_d7_commercial(part, constraints)

    dim_scores = {
        "D1": d1, "D2": d2, "D3": d3, "D4": d4,
        "D5": d5, "D6": d6, "D7": d7,
    }
    all_reasons = r1 + r2 + r3 + r4 + r5 + r6 + r7

    # 加权几何聚合：F = 100 × Π((max(ε, D_k)/100)^w_k)
    log_sum = 0.0
    for dim, w in profile.fit_w.items():
        d = max(_GEO_EPSILON, dim_scores[dim])
        log_sum += w * math.log(d / 100.0)
    F = 100.0 * math.exp(log_sum)
    F = _clip(F)

    return F, dim_scores, all_reasons


# ──────────────────────────────────────────────────────────────────────────────
# 九维风险分（Risk Score R）
# ──────────────────────────────────────────────────────────────────────────────

def _compute_risk_score(
    part: PartIR,
    constraints: RequirementConstraints,
    d1_fit: float,
    d4_supply: float,
) -> Tuple[float, Dict[str, float]]:
    """
    计算九维风险分 R（0–100，越低越好）。

    R = min(100, 0.60×R_mean + 0.25×max(r_m) + 0.15×R_interaction)
    """
    profile = _get_profile(constraints)
    lc = (part.lifecycle_status or "").lower().strip()
    source = (getattr(part, "source", "unknown") or "").lower()
    is_ezplm = source in ("ezplm", "api", "ezplm_api")
    grade = (getattr(constraints, "grade", None) or "").lower()
    automotive_req = grade in ("automotive", "车规")

    # R1 技术应用失配风险（D1 倒置）
    r1 = _clip(100.0 - d1_fit)

    # R2 可靠性与环境风险
    if part.automotive_grade:
        r2 = 10.0
    elif automotive_req and not part.automotive_grade:
        r2 = 85.0
    elif part.temperature_min_c is not None and part.temperature_max_c is not None:
        span = part.temperature_max_c - part.temperature_min_c
        r2 = _clip(80.0 - span / 2.0)
    else:
        r2 = 55.0  # 温度信息缺失，保守估计

    # R3 生命周期与停产风险
    lc_risk = {
        "active": 10.0, "nrnd": 45.0, "ltb": 70.0,
        "eol": 80.0, "obsolete": 95.0, "discontinued": 95.0,
    }
    r3 = lc_risk.get(lc, 50.0)

    # R4 供应可获得性风险（D4 倒置）
    r4 = _clip(100.0 - d4_supply)

    # R5 制造商/供应商风险
    mfr = (part.manufacturer or "").lower()
    if not mfr:
        r5 = 70.0
    else:
        if any(k in mfr or mfr in k for k in _TRUSTED_MANUFACTURERS):
            r5 = 12.0
        elif getattr(part, "is_domestic", False):
            r5 = 30.0
        else:
            r5 = 45.0

    # R6 质量、假冒与追溯风险
    if is_ezplm and part.datasheet_url:
        r6 = 15.0
    elif is_ezplm:
        r6 = 25.0
    elif part.datasheet_url:
        r6 = 40.0
    else:
        r6 = 65.0

    # R7 合规风险
    if part.automotive_grade:
        r7 = 12.0
    elif is_ezplm:
        r7 = 25.0
    else:
        r7 = 45.0

    # R8 制造装配工艺风险
    pkg = (part.package or "").upper()
    if any(s in pkg for s in ("BGA", "CSP", "WLCSP", "LGA", "FCBGA")):
        r8 = 45.0
    elif any(s in pkg for s in ("QFN", "DFN", "SON")):
        r8 = 30.0
    elif pkg:
        r8 = 15.0
    else:
        r8 = 40.0  # 封装未知

    # R9 商业与成本波动风险
    if part.unit_price_cny is None:
        r9 = 40.0
    elif part.unit_price_cny <= 0:
        r9 = 50.0
    elif part.unit_price_cny < 5.0:
        r9 = 10.0
    elif part.unit_price_cny < 50.0:
        r9 = 25.0
    elif part.unit_price_cny < 200.0:
        r9 = 50.0
    else:
        r9 = 75.0

    r_dims = {
        "R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": r5,
        "R6": r6, "R7": r7, "R8": r8, "R9": r9,
    }

    # 加权均值
    r_mean = sum(profile.risk_w[k] * v for k, v in r_dims.items())

    # 尾部风险（最高单项）
    r_max = max(r_dims.values())

    # 交互风险
    r_interaction = 0.0
    # 单一制造商 + 无替代料 + 停产临近
    if (not getattr(part, "replacement_for", [])
            and lc in ("nrnd", "ltb", "eol", "obsolete")):
        r_interaction += 20.0
    # 非授权渠道 + 高假冒率风险（R6 > 40 且非 ezplm）
    if not is_ezplm and r6 > 40:
        r_interaction += 15.0
    # 车规要求 + 无认证 + 量产环境
    if automotive_req and not part.automotive_grade:
        r_interaction += 25.0
    r_interaction = min(r_interaction, 50.0)

    # 最终风险分（尾部修正公式）
    R = min(100.0, 0.60 * r_mean + 0.25 * r_max + 0.15 * r_interaction)
    R = _clip(R)

    return R, r_dims


# ──────────────────────────────────────────────────────────────────────────────
# 证据可信度（Confidence Score C）
# ──────────────────────────────────────────────────────────────────────────────

def _compute_confidence(part: PartIR) -> float:
    """
    计算证据可信度 C（0–100）。

    c_i = S_i × T_i × M_i
    C = 100 × Σ(a_i × c_i) / Σ(a_i)

    v2 增强：本地数据手册可用时，来源可靠度 S 从 0.80 提升到 1.00。
    """
    # ── 检查本地数据手册 ──────────────────────────────────────────
    has_local_ds = False
    try:
        from .datasheet_rag import get_registry
        registry = get_registry()
        has_local_ds = registry.has_datasheet(part.part_number)
    except Exception:
        pass

    # 来源可靠度 S
    if has_local_ds:
        S = 1.00   # 原厂数据手册 → 最高可靠度
    else:
        source = (getattr(part, "source", "unknown") or "").lower()
        if source in ("ezplm", "api", "ezplm_api"):
            S = 0.80   # 授权分销商直接数据
        else:
            S = 0.50   # 普通聚合网站或未确认数据

    # 时效因子 T（库存/价格 30天内认为新鲜；静态参数衰减慢）
    T_dynamic = 0.95 if (has_local_ds or (getattr(part, "source", "unknown") or "").lower() in ("ezplm", "api", "ezplm_api")) else 0.70
    T_static = 1.00  # 数据手册静态参数

    # 完整性因子 M（字段覆盖率）
    # v2：本地数据手册可用时，静态参数完整度接近 1.0
    total_fields = 8
    present = sum([
        bool(part.part_number),
        bool(part.manufacturer),
        bool(part.package),
        bool(part.lifecycle_status),
        part.input_voltage_min_v is not None or part.input_voltage_max_v is not None,
        part.output_voltage_v is not None or part.output_current_max_a is not None,
        part.temperature_min_c is not None,
        bool(part.datasheet_url),
    ])
    M_dynamic = present / total_fields
    M_static = max(M_dynamic, 0.90) if has_local_ds else M_dynamic

    # 动态数据（库存/价格）
    c_dynamic = S * T_dynamic * M_dynamic
    # 静态数据（参数/规格）
    c_static = S * T_static * M_static

    # 综合（动态权重 0.4，静态权重 0.6）
    C = 100.0 * (0.4 * c_dynamic + 0.6 * c_static)
    return _clip(C)


# ──────────────────────────────────────────────────────────────────────────────
# 稳健性分析（Robustness Score B）
# ──────────────────────────────────────────────────────────────────────────────

def _compute_robustness(
    part: PartIR,
    constraints: RequirementConstraints,
    F_base: float,
    R_base: float,
    C_base: float,
) -> float:
    """
    简化稳健性分析：对权重指数做三次扰动，检验 RS 相对变化。

    B = 100 × (1 − 标准差/均值) ≈ 变异系数的倒置
    """
    profile = _get_profile(constraints)
    alpha, beta, gamma, delta = profile.exponents

    # 基础指数
    exps_list = [
        profile.exponents,
        # 扰动1：适配度权重+5%，风险权重-5%
        (min(alpha + 0.05, 0.80), max(beta - 0.05, 0.05), gamma, delta),
        # 扰动2：风险权重+5%，适配度权重-5%
        (max(alpha - 0.05, 0.30), min(beta + 0.05, 0.55), gamma, delta),
        # 扰动3：可信度+5%，稳健性-5%
        (alpha, beta, min(gamma + 0.05, 0.20), max(delta - 0.05, 0.05)),
    ]

    rs_values = []
    for a, b, g, d in exps_list:
        rs = _rs_formula(1.0, F_base, R_base, C_base, 80.0, a, b, g, d)
        rs_values.append(rs)

    mean_rs = sum(rs_values) / len(rs_values)
    if mean_rs < 1e-6:
        return 50.0
    std_rs = math.sqrt(sum((x - mean_rs) ** 2 for x in rs_values) / len(rs_values))
    cv = std_rs / mean_rs

    # cv 越低，稳健性越高
    B = _clip(100.0 - cv * 300.0)
    return B


def _rs_formula(
    G: float, F: float, R: float, C: float, B: float,
    alpha: float, beta: float, gamma: float, delta: float,
) -> float:
    """
    推荐分公式：
    RS = G × 100 × (F/100)^α × ((100-R)/100)^β × (C/100)^γ × (B/100)^δ
    """
    f_term = max(_GEO_EPSILON / 100.0, F / 100.0) ** alpha
    r_term = max(_GEO_EPSILON / 100.0, (100.0 - R) / 100.0) ** beta
    c_term = max(_GEO_EPSILON / 100.0, C / 100.0) ** gamma
    b_term = max(_GEO_EPSILON / 100.0, B / 100.0) ** delta
    return _clip(G * 100.0 * f_term * r_term * c_term * b_term)


# ──────────────────────────────────────────────────────────────────────────────
# 国产化加分
# ──────────────────────────────────────────────────────────────────────────────

def _domestic_score(part: PartIR) -> Tuple[float, List[str]]:
    """国产化优先分（0–100）。"""
    if getattr(part, "is_domestic", False):
        return 100.0, ["🇨🇳 国产器件（国产化优先政策加分）"]
    return 0.0, []


# ──────────────────────────────────────────────────────────────────────────────
# 推荐等级映射
# ──────────────────────────────────────────────────────────────────────────────

def _assign_recommendation_level(
    RS: float,
    R: float,
    C: float,
    gate: GateResult,
    rank: int,
) -> Tuple[str, str]:
    """
    返回 (recommendation_level, grade_letter)。

    等级定义：
      A 优先推荐 → "recommended"
      B 推荐     → "recommended"
      C 条件推荐 → "backup"
      D 不优先   → "backup"
      E 禁止推荐 → "not_recommended"
    """
    if gate.status == "FAIL":
        return "not_recommended", "E"
    # Grade A: 较高综合质量（标准相对放宽以适应实际数据覆盖）
    if RS >= 70 and R <= 25 and C >= 65 and rank <= _MAX_RECOMMENDED:
        return "recommended", "A"
    # Grade B: 推荐，可信度≥50
    if RS >= 55 and R <= 40 and C >= 50 and rank <= _MAX_RECOMMENDED:
        return "recommended", "B"
    # Grade C: 条件推荐（含 Gate Conditional）
    if RS >= 40 or gate.status == "CONDITIONAL":
        return "backup", "C"
    # Grade D: 不优先
    if RS >= 25:
        return "backup", "D"
    return "not_recommended", "D"


# ──────────────────────────────────────────────────────────────────────────────
# MPN 归一化与去重
# ──────────────────────────────────────────────────────────────────────────────

_CORE_MPN_RE = _re_core.compile(r"^([A-Za-z]+[0-9]+[A-Za-z0-9]*)")


def _extract_core_mpn(part_number: str) -> str:
    """提取核心型号（去掉封装/卷带后缀）。"""
    if not part_number:
        return part_number
    for sep in ("/", "-TR", "-T&R", "T&R", "TR-"):
        idx = part_number.find(sep)
        if idx > 3:
            part_number = part_number[:idx]
    m = _CORE_MPN_RE.match(part_number)
    return m.group(1) if m else part_number


def _merge_variant_info(kept: ScoredPart, dropped: ScoredPart) -> None:
    """将 dropped 的变体信息合并到 kept 的 reasons 中。"""
    if dropped.part.package and dropped.part.package != kept.part.package:
        kept.score.reasons.append(
            f"同类变体：{dropped.part.part_number}（{dropped.part.package}）"
        )
    else:
        kept.score.reasons.append(f"同类变体：{dropped.part.part_number}")


# ──────────────────────────────────────────────────────────────────────────────
# 主评分函数
# ──────────────────────────────────────────────────────────────────────────────

def score_candidates(
    constraints: RequirementConstraints,
    candidates: List[PartIR],
    ref_designs_map: Optional[Dict[str, List[Any]]] = None,
) -> List[ScoredPart]:
    """
    对候选器件进行多维量化评分，返回已排名、已分级的 ScoredPart 列表。

    评分流程（参照《研究报告 V1.0》第 8.1 节推荐流程）：
      1. 参数与单位标准化
      2. 硬门槛过滤（Gate）
      3. 七维适配度评分 F
      4. 九维风险分 R（含尾部修正）
      5. 证据可信度 C
      6. 稳健性分析 B
      7. Pareto 非支配筛选（简化）
      8. 风险修正推荐分 RS
      9. 去重合并（同核心型号）
      10. 排名 + 分级（A/B/C/D/E）

    Args:
        constraints:      解析后的需求约束
        candidates:       候选器件列表（PartIR）
        ref_designs_map:  {part_number: [reference_design, ...]}，可选
    """
    from .llm_config import get_api_key
    has_llm_key = bool(get_api_key().strip())
    use_llm = has_llm_key and ref_designs_map is not None
    _score_with_llm = None
    if use_llm:
        try:
            from .llm_client import score_part_with_llm as _score_with_llm
        except Exception:
            use_llm = False

    profile = _get_profile(constraints)
    alpha, beta, gamma, delta = profile.exponents

    raw_scored: List[ScoredPart] = []

    for p in candidates:
        # ── Step 1: 硬门槛 ─────────────────────────────────────────
        gate = _gate_evaluate(p, constraints)

        # Gate Fail → 跳过适配度计算，直接给极低分
        if gate.status == "FAIL":
            reasons = [f"⛔ 门禁失败：{r}" for r in gate.fail_reasons]
            sb = ScoreBreakdown(
                parameter_match_score=0.0,
                supply_risk_score=0.0,
                cost_score=0.0,
                domestic_score=0.0,
                evidence_score=0.0,
                total_score=0.0,
                reasons=reasons,
                scoring_mode="gate_fail",
                llm_application_score=0.0,
                llm_design_risk_score=100.0,
                llm_reasoning="; ".join(gate.fail_reasons),
            )
            sp = ScoredPart(part=p, score=sb)
            raw_scored.append(sp)
            continue

        # ── Step 2: 七维适配度 F ────────────────────────────────────
        F, dim_scores, reasons = _compute_fit_score(p, constraints)

        # ── Step 3: 九维风险分 R ────────────────────────────────────
        R, risk_dims = _compute_risk_score(
            p, constraints,
            d1_fit=dim_scores["D1"],
            d4_supply=dim_scores["D4"],
        )

        # ── Step 4: 证据可信度 C ────────────────────────────────────
        C = _compute_confidence(p)

        # ── Step 5: LLM 增强（可选）────────────────────────────────
        scoring_mode = "rule_only"
        llm_app = None
        llm_risk = None
        llm_reasoning = None

        if use_llm and _score_with_llm:
            ref_designs = (ref_designs_map or {}).get(p.part_number, [])
            if ref_designs:
                try:
                    llm_result = _score_with_llm(
                        constraints.raw_input,
                        {
                            "part_number": p.part_number,
                            "manufacturer": p.manufacturer,
                            "description": p.description,
                            "vin_min": p.input_voltage_min_v,
                            "vin_max": p.input_voltage_max_v,
                            "iout_max": p.output_current_max_a,
                            "temp_min": p.temperature_min_c,
                            "temp_max": p.temperature_max_c,
                        },
                        ref_designs,
                    )
                    if "application_score" in llm_result:
                        llm_app = float(llm_result["application_score"])
                        llm_risk = float(llm_result.get("design_risk_score", 50.0))
                        llm_reasoning = llm_result.get("reasoning")
                        # 将 LLM 分融入 F 和 R（各 20% 权重融合）
                        F = 0.80 * F + 0.20 * llm_app
                        R = 0.80 * R + 0.20 * llm_risk
                        F = _clip(F)
                        R = _clip(R)
                        scoring_mode = "llm_enhanced"
                        if llm_reasoning:
                            reasons.append(f"LLM 评估：{llm_reasoning}")
                except Exception:
                    pass

        # ── Step 6: 稳健性 B ────────────────────────────────────────
        B = _compute_robustness(p, constraints, F, R, C)

        # ── Step 7: 推荐分 RS ────────────────────────────────────────
        RS = _rs_formula(gate.gate_factor, F, R, C, B, alpha, beta, gamma, delta)

        # 国产化加分（乘法因子，叠加在 RS 上）
        dom_score, dom_reasons = _domestic_score(p)
        reasons.extend(dom_reasons)
        if dom_score > 0:
            RS = _clip(RS * (1.0 + profile.domestic_bonus))

        # 条件门禁附加说明
        if gate.status == "CONDITIONAL":
            for cond in gate.conditional_reasons:
                reasons.append(f"⚠ 条件门槛：{cond}")

        # ── Step 8: 构建 ScoreBreakdown ─────────────────────────────
        sb = ScoreBreakdown(
            parameter_match_score=round(dim_scores["D1"], 2),
            supply_risk_score=round(dim_scores["D4"], 2),
            cost_score=round(dim_scores["D7"], 2),
            domestic_score=round(dom_score, 2),
            evidence_score=round(C, 2),
            total_score=round(RS, 2),
            reasons=reasons,
            scoring_mode=scoring_mode,
            llm_application_score=round(F, 2),
            llm_design_risk_score=round(100.0 - R, 2),
            llm_reasoning=(
                f"F={F:.1f} R={R:.1f} C={C:.1f} B={B:.1f} "
                f"Gate={gate.status} RS={RS:.1f}"
                + (f" | {llm_reasoning}" if llm_reasoning else "")
            ),
            dim_scores={
                "param_match": round(dim_scores["D1"], 1),
                "reliability": round(dim_scores["D2"], 1),
                "quality":     round(dim_scores["D3"], 1),
                "supply":      round(dim_scores["D4"], 1),
                "manufacturing": round(dim_scores["D5"], 1),
                "compliance":  round(dim_scores["D6"], 1),
                "commercial":  round(dim_scores["D7"], 1),
                "fit":         round(F, 1),
                "risk":        round(R, 1),
            },
        )
        sp = ScoredPart(part=p, score=sb)
        raw_scored.append(sp)

    # ── Step 9: 按 RS 降序排序 ──────────────────────────────────────
    raw_scored.sort(key=lambda s: s.score.total_score, reverse=True)

    # ── Step 10: 去重合并（同核心型号保留最高分变体）─────────────────
    seen_cores: Dict[str, int] = {}
    deduped: List[ScoredPart] = []
    for s in raw_scored:
        core = _extract_core_mpn(s.part.part_number)
        if core and core in seen_cores:
            prev_idx = seen_cores[core]
            prev = deduped[prev_idx]
            if s.score.total_score > prev.score.total_score:
                _merge_variant_info(s, prev)
                deduped[prev_idx] = s
            else:
                _merge_variant_info(prev, s)
            continue
        if core:
            seen_cores[core] = len(deduped)
        deduped.append(s)

    # ── Step 11: 排名 + 推荐等级分级 ─────────────────────────────────
    for idx, s in enumerate(deduped, start=1):
        s.rank = idx
        # 从 llm_reasoning 中解析 R 和 C（用于等级判断）
        try:
            meta = s.score.llm_reasoning or ""
            R_val = float(_re_core.search(r"R=(\d+\.?\d*)", meta).group(1)) if "R=" in meta else 50.0
            C_val = float(_re_core.search(r"C=(\d+\.?\d*)", meta).group(1)) if "C=" in meta else 50.0
            gate_status = _re_core.search(r"Gate=(\w+)", meta).group(1) if "Gate=" in meta else "PASS"
        except Exception:
            R_val, C_val, gate_status = 50.0, 50.0, "PASS"

        gate_mock = GateResult(gate_status, [], [], 0.0 if gate_status == "FAIL" else 1.0)
        level, grade_letter = _assign_recommendation_level(
            s.score.total_score, R_val, C_val, gate_mock, idx
        )
        s.recommendation_level = level

        # 将等级字母追加到 reasons（便于前端展示）
        grade_labels = {
            "A": "🌟 优先推荐",
            "B": "✅ 推荐",
            "C": "🟡 条件推荐",
            "D": "🔵 备选",
            "E": "⛔ 禁止推荐",
        }
        s.score.reasons.append(
            f"推荐等级：{grade_labels.get(grade_letter, grade_letter)} "
            f"({grade_letter}) | RS={s.score.total_score:.1f} "
            f"| F={s.score.llm_application_score:.0f} "
            f"| R_inv={s.score.llm_design_risk_score:.0f} "
            f"| C={s.score.evidence_score:.0f}"
        )

    # 仅返回前 _MAX_LISTED 条
    return deduped[:_MAX_LISTED]
