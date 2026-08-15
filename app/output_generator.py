"""
output_generator.py — 三份标准化工程输出文件生成模块

生成内容：
  1. 元器件选型清单（BOM）         — 纯结构化，无需 LLM
  2. 供应链与工程风险评估报告      — 规则验证事实 + LLM 叙述综合
  3. 电路功能模块拓扑结构分析      — 规则模板 + LLM 工程细化

报告风格：学术化、产业规范化，参照 IPC/IEC/IEEE 术语体系。
"""

from __future__ import annotations

import os
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from .schemas import (
    SelectionReport, RequirementConstraints, ScoredPart, RiskItem,
    TopologyIR, TopoNode, ExternalComponent, ThermalEstimate,
)


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

def _severity_cn(severity: str) -> str:
    return {"high": "高 (High)", "medium": "中 (Medium)", "low": "低 (Low)"}.get(
        severity.lower(), severity
    )


def _risk_type_cn(risk_type: str) -> str:
    return {"supply": "供应链风险", "engineering": "工程技术风险"}.get(risk_type, risk_type)


def _lifecycle_cn(status: Optional[str]) -> str:
    if not status:
        return "未知"
    mapping = {
        "active": "现行 (Active)",
        "obsolete": "停产 (Obsolete)",
        "discontinued": "停产 (Discontinued)",
        "nrnd": "不推荐新设计 (NRND)",
    }
    return mapping.get(status.lower(), status)


def _rec_level_cn(level: Optional[str]) -> str:
    return {"recommended": "推荐", "backup": "备选", "not_recommended": "不推荐"}.get(
        level or "", "-"
    )


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _call_llm(system: str, user: str) -> str:
    """调用 LLM，失败时返回空字符串。"""
    try:
        from .llm_client import call_openai_chat_text
        from .llm_config import get_api_key
        if not get_api_key().strip():
            return ""
        return call_openai_chat_text(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
        )
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════
# 1. 元器件选型清单（BOM）— 已移至 app/output_bom.py
# ═══════════════════════════════════════════════════════════════════
from .output_bom import generate_bom  # noqa: F401 — 保持向后兼容


# ═══════════════════════════════════════════════════════════════════
# 2. 供应链与工程风险评估报告
# ═══════════════════════════════════════════════════════════════════

def _build_risk_context(report: SelectionReport) -> str:
    """为 LLM 构建结构化风险事实文本（仅包含规则验证结果）。"""
    risks = report.risks
    if not risks:
        return "无风险数据。"
    lines = [f"整体风险等级：{risks.overall_risk_level.upper()}"]
    for i, item in enumerate(risks.risk_items, 1):
        lines.append(
            f"[R{i:02d}] 类型：{_risk_type_cn(item.risk_type)} | "
            f"严重程度：{_severity_cn(item.severity)} | "
            f"描述：{item.description} | "
            f"缓解建议：{item.mitigation or '待评估'}"
        )
    return "\n".join(lines)


def _llm_executive_summary(report: SelectionReport, constraints: RequirementConstraints) -> str:
    """LLM 生成执行摘要，仅基于规则验证的风险事实，不得新增声明。"""
    risk_context = _build_risk_context(report)
    rec_count = len(report.recommended_parts)
    top_pn = report.recommended_parts[0].part.part_number if report.recommended_parts else "无"

    system = (
        "你是一名专业的电子工程供应链风险分析师，负责撰写学术化、产业规范化的风险评估报告执行摘要。"
        "你的摘要必须严格基于提供的规则验证风险条目，不得引入未经规则识别的新风险声明，不得使用非正式语言或表情符号。"
        "摘要使用正式中文，段落简洁，最多 200 字。"
    )
    user = (
        f"选型需求：{constraints.raw_input}\n"
        f"推荐器件数量：{rec_count}，首选型号：{top_pn}\n"
        f"规则验证风险条目：\n{risk_context}\n\n"
        "请撰写风险评估报告执行摘要（2~3 段，不超过 200 字，不含标题，不含序号）。"
        "内容须涵盖：整体风险等级说明、主要风险来源概述、建议行动要点。"
    )
    result = _call_llm(system, user)
    return result.strip() if result else (
        f"本次选型检索到 {len(report.candidates)} 条候选器件，其中 {rec_count} 条满足推荐门槛。"
        f"规则评估识别到 {len(report.risks.risk_items if report.risks else [])} 项风险条目，"
        f"整体风险等级为 {(report.risks.overall_risk_level.upper() if report.risks else '未知')}。"
        "详细风险分析见各章节。"
    )


def _llm_risk_elaboration(risk_items: list, section_type: str) -> str:
    """LLM 对规则验证的风险条目进行工程语境下的补充说明。"""
    if not risk_items:
        return "本评估周期内，规则引擎未识别到该类别下的风险条目。"

    items_text = "\n".join(
        f"- [{_severity_cn(r.severity)}] {r.description}（建议：{r.mitigation or '待评估'}）"
        for r in risk_items
    )
    system = (
        "你是一名电子工程师，专注于元器件选型与供应链风险评估。"
        "请对以下规则验证的风险条目进行简要的工程背景说明，不超过 150 字，不得引入新风险声明，使用正式中文。"
    )
    user = (
        f"以下为规则引擎验证的{section_type}风险条目：\n{items_text}\n\n"
        "请从工程实践角度补充说明这些风险的潜在影响，并概述优先应对策略（不含序号、标题，纯段落文字）。"
    )
    result = _call_llm(system, user)
    return result.strip() if result else "（LLM 叙述不可用，请参阅上方规则验证条目。）"


def generate_risk_report(
    report: SelectionReport,
    constraints: RequirementConstraints,
    rag_context: str = "",
) -> str:
    """生成供应链与工程风险评估报告（Markdown）。

    架构原则：
    - 规则引擎(_assess_risks)识别的风险条目为事实基础，不可篡改
    - LLM 仅负责叙述综合与工程背景补充，不新增风险声明
    - 每节明确标注数据来源（规则验证 / AI 辅助分析）
    """
    risks = report.risks
    risk_items = risks.risk_items if risks else []
    supply_items = [r for r in risk_items if r.risk_type == "supply"]
    eng_items    = [r for r in risk_items if r.risk_type == "engineering"]
    review_items = [e for e in report.evidence if e.need_human_review]
    overall = risks.overall_risk_level.upper() if risks else "UNKNOWN"

    lines: List[str] = []

    # ── 文件头 ──────────────────────────────────────────────────────
    lines += [
        "# 供应链与工程风险评估报告",
        "",
        "## 文件信息",
        "",
        f"| 字段 | 内容 |",
        f"|------|------|",
        f"| 生成日期 | {_now()} |",
        f"| 需求标识 | {report.request_id[:8]} |",
        f"| 原始需求 | {constraints.raw_input} |",
        f"| 整体风险等级 | {overall} |",
        f"| 规则验证风险条目数 | {len(risk_items)} |",
        f"| 供应链风险 | {len(supply_items)} 条 |",
        f"| 工程技术风险 | {len(eng_items)} 条 |",
        f"| 需人工复核项 | {len(review_items)} 条 |",
        "",
    ]

    # ── 执行摘要 ─────────────────────────────────────────────────
    lines += [
        "## 一、执行摘要",
        "",
        "> **数据来源声明**：本节综合摘要由 AI 语言模型基于规则验证风险条目生成，",
        "> 所有结论均可在后续章节中追溯至具体规则触发依据。",
        "",
        _llm_executive_summary(report, constraints),
        "",
    ]

    # ── 评估方法与数据来源 ────────────────────────────────────────
    lines += [
        "## 二、评估方法与数据来源",
        "",
        "### 2.1 评估方法",
        "",
        "本报告采用双层评估架构：",
        "",
        "**第一层：规则引擎验证（Rule-Based Verification）**",
        "",
        "基于预定义规则集对候选器件进行确定性风险识别，共覆盖 9 类风险规则：",
        "- 供应链规则（7 条）：无候选器件、无推荐器件、单点依赖、库存水平、",
        "  生命周期状态、供应商集中度；",
        "- 工程技术规则（2 条）：车规认证符合性、参数匹配充裕度。",
        "",
        "规则验证结论具有确定性，不受模型推理影响。",
        "",
        "**第二层：AI 辅助分析（AI-Assisted Analysis）**",
        "",
        "基于 eZ-PLM 参考设计库数据，由 LLM 对规则验证结论进行工程背景补充说明。",
        "AI 辅助分析仅在规则识别结论基础上进行叙述综合，不独立产生风险判定。",
        "",
        "### 2.2 数据来源",
        "",
        "| 数据类型 | 来源 | 置信度评估 |",
        "|---------|------|-----------|",
        "| 器件电气参数 | eZ-PLM API 实时查询 | 高（平台字段） |",
        "| 库存与生命周期 | eZ-PLM API | 中（实时性依赖平台更新频率） |",
        "| 参考设计 | eZ-PLM 参考设计库 | 中（案例覆盖度因器件而异） |",
        "| 规则推理 | 本地规则引擎 | 高（确定性逻辑） |",
        "| RAG 知识检索 | ChromaDB 向量知识库 | 中（语义匹配相关性） |",
        "| AI 叙述 | LLM 综合生成 | 低至中（须工程师核实） |",
        "",
    ]

    # ── 风险识别结果 ─────────────────────────────────────────────
    lines += [
        "## 三、风险识别结果",
        "",
        "### 3.1 风险汇总矩阵",
        "",
        "> **来源**：规则引擎验证（Rule-Based Verification）",
        "",
        "| 编号 | 风险类型 | 严重程度 | 风险描述摘要 | 相关器件 | 缓解建议 |",
        "|------|---------|---------|------------|---------|---------|",
    ]
    for i, item in enumerate(risk_items, 1):
        related = item.related_part_number or "-"
        desc_short = item.description[:50] + ("..." if len(item.description) > 50 else "")
        mit_short  = (item.mitigation or "-")[:50] + ("..." if item.mitigation and len(item.mitigation) > 50 else "")
        lines.append(
            f"| R{i:02d} | {_risk_type_cn(item.risk_type)} | {_severity_cn(item.severity)} "
            f"| {desc_short} | {related} | {mit_short} |"
        )
    if not risk_items:
        lines.append("| — | — | — | 规则引擎未识别到风险条目，当前选型结果可接受 | — | — |")

    lines.append("")

    # ── 失效模式与影响分析（FMEA） 量化矩阵（P0新增）───────────────────────────────────
    if risk_items:
        lines += [
            "### 3.1.1 风险量化分析（FMEA）",
            "",
            "> 依据 **IEC 60812 / SAE J1739** 失效模式与影响分析（FMEA） 方法论，对每项风险进行 风险优先级数（RPN） 评分。",
            "> 风险优先级数（RPN） = 严重度(S) × 发生概率(O) × 检测难度(D)，每维 1–5 分。",
            "",
            "| 编号 | 风险描述 | S | O | D | 风险优先级数（RPN） | 风险等级 |",
            "|------|---------|---|---|---|-----|---------|",
        ]
        for i, item in enumerate(risk_items, 1):
            sev_map = {"high": 5, "medium": 3, "low": 1}
            s_val = sev_map.get(item.severity, 2)
            o_val = 3 if "全部" in item.description else 2   # 全量影响 → 高概率
            d_val = 2 if "未知" in item.description else 3    # 数据缺失 → 难检测
            rpn = s_val * o_val * d_val
            rpn_level = "高" if rpn >= 30 else ("中" if rpn >= 15 else "低")
            desc_short = item.description[:40] + ("..." if len(item.description) > 40 else "")
            lines.append(
                f"| R{i:02d} | {desc_short} | {s_val} | {o_val} | {d_val} | **{rpn}** | {rpn_level} |"
            )
        lines.append("")

    # ── 风险矩阵（Likelihood × Impact） ──────────────────────────
    if risk_items:
        lines += [
            "### 3.1.2 风险热力矩阵（5×5 Likelihood × Impact）",
            "",
            "|          | 很低(1) | 低(2) | 中(3) | 高(4) | 很高(5) |",
            "|----------|---------|-------|-------|-------|---------|",
        ]
        # 统计每个严重度下的风险数量
        sev_count = {"low": 0, "medium": 0, "high": 0}
        for item in risk_items:
            sev_count[item.severity] = sev_count.get(item.severity, 0) + 1
        for level_label, level_key in [("很高(5)", "high"), ("高(4)", "high"), ("中(3)", "medium"), ("低(2)", "low"), ("很低(1)", "low")]:
            vals = []
            for col in range(1, 6):
                # 简化的矩阵填充：根据计数决定标记
                if level_key == "high" and col >= 3:
                    vals.append(f"🔴 {sev_count['high']}" if col == 3 and sev_count['high'] > 0 else ("-" if col > 3 else "🔴"))
                elif level_key == "medium" and col == 3:
                    vals.append(f"🟡 {sev_count['medium']}" if sev_count['medium'] > 0 else "-")
                elif level_key == "low" and col == 2:
                    vals.append(f"🟢 {sev_count['low']}" if sev_count['low'] > 0 else "-")
                else:
                    vals.append("-")
            lines.append(f"| {level_label} | {' | '.join(vals)} |")
        lines += [
            "",
            "> 图例：🔴 高风险区域（需立即行动） 🟡 中风险区域（需跟踪监控） 🟢 低风险区域（可接受）",
            "",
        ]

    lines.append("")

    # ── 供应链风险详述 ────────────────────────────────────────────
    lines += [
        "### 3.2 供应链风险详述",
        "",
        "> **来源**：规则引擎验证（Rule-Based Verification）",
        "",
    ]
    if supply_items:
        for i, item in enumerate(supply_items, 1):
            lines += [
                f"#### 3.2.{i} {item.description[:40]}",
                "",
                f"- **严重程度**：{_severity_cn(item.severity)}",
                f"- **风险描述**：{item.description}",
                f"- **相关器件**：{item.related_part_number or '—'}",
                f"- **缓解建议**：{item.mitigation or '待评估'}",
                "",
            ]
        lines += [
            "**供应链风险工程背景说明**（AI 辅助分析）：",
            "",
            "> 以下说明由 AI 基于规则验证结果生成，仅供工程师参考，不构成独立风险判定。",
            "",
            _llm_risk_elaboration(supply_items, "供应链"),
            "",
        ]
    else:
        lines += ["当前评估周期内，规则引擎未识别到供应链风险条目。", ""]

    # ── 工程技术风险详述 ──────────────────────────────────────────
    lines += [
        "### 3.3 工程技术风险详述",
        "",
        "> **来源**：规则引擎验证（Rule-Based Verification）",
        "",
    ]
    if eng_items:
        for i, item in enumerate(eng_items, 1):
            lines += [
                f"#### 3.3.{i} {item.description[:40]}",
                "",
                f"- **严重程度**：{_severity_cn(item.severity)}",
                f"- **风险描述**：{item.description}",
                f"- **相关器件**：{item.related_part_number or '—'}",
                f"- **缓解建议**：{item.mitigation or '待评估'}",
                "",
            ]
        lines += [
            "**工程技术风险背景说明**（AI 辅助分析）：",
            "",
            "> 以下说明由 AI 基于规则验证结果生成，仅供工程师参考，不构成独立风险判定。",
            "",
            _llm_risk_elaboration(eng_items, "工程技术"),
            "",
        ]
    else:
        lines += ["当前评估周期内，规则引擎未识别到工程技术风险条目。", ""]

    # ── 需人工复核项 ──────────────────────────────────────────────
    lines += [
        "## 四、需人工复核项清单",
        "",
        "> 以下条目由证据链分析模块标记为置信度不足或数据缺失，须工程师结合数据手册及",
        "> 企业合格供应商清单（AVL）进行核实后方可采用。",
        "",
        "| 序号 | 器件型号 | 证据类型 | 复核内容 | 置信度 |",
        "|------|---------|---------|---------|-------|",
    ]
    if review_items:
        for i, ev in enumerate(review_items, 1):
            lines.append(
                f"| {i} | {ev.part_number or '-'} | {ev.evidence_type} "
                f"| {ev.claim[:60]} | {ev.confidence:.2f} |"
            )
    else:
        lines.append("| — | — | — | 无需人工复核项 | — |")

    lines.append("")

    # ── 结论与建议 ────────────────────────────────────────────────
    rec_count = len(report.recommended_parts)
    top_part = report.recommended_parts[0] if report.recommended_parts else None

    lines += [
        "## 五、结论与建议",
        "",
        f"本次选型评估检索到 {len(report.candidates)} 条候选器件，"
        f"其中 {rec_count} 条满足推荐门槛。",
        "",
    ]

    if top_part:
        lines += [
            f"首选推荐型号为 **{top_part.part.part_number}**"
            f"（{top_part.part.manufacturer or '未知厂商'}），"
            f"综合评分 {top_part.score.total_score:.2f} 分，"
            f"评分模式为 {top_part.score.scoring_mode}。",
            "",
        ]

    lines += [
        f"整体供应链与工程风险等级评定为 **{overall}**，"
        "主要风险分布见第三章风险矩阵。",
        "",
        "**建议行动事项：**",
        "",
    ]

    action_num = 0
    if any(r.severity == "high" for r in risk_items):
        action_num += 1
        lines += [
            f"{action_num}. 针对高严重度（High）风险条目，须在器件锁定前完成整改或获得工程豁免。",
        ]
    if any(r.severity == "medium" for r in risk_items):
        action_num += 1
        lines += [
            f"{action_num}. 中等严重度（Medium）风险条目须在量产评审（DFM/DVT）阶段进行跟踪确认。",
        ]
    if review_items:
        action_num += 1
        lines += [
            f"{action_num}. 共 {len(review_items)} 项证据置信度不足，须核查数据手册原始参数后方可定案。",
        ]
    action_num += 1
    lines += [
        f"{action_num}. 推荐器件列入企业 AVL 前，须完成样品认证、AEC-Q100/Q101 符合性核实（如适用）。",
        "",
        "---",
        "",
        f"*本报告由 ezplm-component-risk-agent 自动生成，生成时间：{_now()}。*",
        "*规则验证结论具有确定性；AI 辅助分析内容标有专门声明，须经工程师审核。*",
        "*本报告不构成最终选型决策依据，最终采购须符合企业质量管理体系规定。*",
    ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 3. 电路功能模块拓扑结构分析
# ═══════════════════════════════════════════════════════════════════

# ── 规则模板 ──────────────────────────────────────────────────────

def _topology_mermaid_buck_converter(
    ctrl_pn: str, vin_str: str, vout_str: str, iout_str: str
) -> str:
    """集成式同步 Buck 转换器（Converter，MOSFET 在芯片内部）— TPS54xxx 等。"""
    return f"""```mermaid
graph LR
    VIN["输入电源<br/>{vin_str}"] --> CIN["输入电容 CIN<br/>10μF~22μF 多层陶瓷电容（MLCC）"]
    CIN --> CBOOT["自举电容 CBOOT<br/>0.1μF"]
    CBOOT --> IC["Buck 转换器 IC<br/>**{ctrl_pn}**<br/>集成 HS/LS MOSFET"]
    IC --> IND["储能电感 L<br/>~6.8μH"]
    IND --> COUT["输出电容 COUT<br/>22μF×2 多层陶瓷电容（MLCC）"]
    COUT --> VOUT["负载输出<br/>{vout_str} / {iout_str}"]
    COUT -->|"反馈 FB → Rfbt/Rfbb 分压"| IC
    IC --> EN["使能 EN<br/>欠压锁定（UVLO） 电阻分压"]
    IC --> SS["软启动 CSS"]
    PGND["功率地 PGND"] --- IC
    AGND["模拟地 AGND"] --- IC
```"""


def _topology_mermaid_buck_controller(
    ctrl_pn: str, vin_str: str, vout_str: str, iout_str: str
) -> str:
    """外置 MOSFET 的 Buck 控制器（Controller）— LM2596/LM2576 等。"""
    return f"""```mermaid
graph LR
    VIN["输入电源<br/>{vin_str}"] --> CIN["输入电容 CIN"]
    CIN --> CTRL["Buck 控制器<br/>{ctrl_pn}"]
    CTRL -->|"PWM 驱动"| HS["上管 (High-Side MOSFET)"]
    HS --> LS["下管 (Low-Side MOSFET) / 续流二极管"]
    LS --> IND["储能电感 (L)"]
    IND --> COUT["输出滤波电容 (COUT)"]
    COUT --> VOUT["负载输出<br/>{vout_str} / {iout_str}"]
    COUT -->|"电压反馈 (FB)"| CTRL
    PGND["功率地 PGND"] --- CTRL
    PGND --- LS
```"""


def _topology_mermaid_boost(
    ctrl_pn: str, vin_str: str, vout_str: str, iout_str: str
) -> str:
    return f"""```mermaid
graph LR
    VIN["输入电源<br/>{vin_str}"] --> FIN["输入滤波网络"]
    FIN --> IND["储能电感 (L)"]
    IND --> SW["功率开关 (MOSFET)"]
    IND --> DIODE["整流二极管 (D)"]
    DIODE --> FOUT["输出滤波电容 (COUT)"]
    FOUT --> VOUT["负载输出<br/>{vout_str} / {iout_str}"]
    FOUT -->|"电压反馈 (FB)"| CTRL["Boost 控制器<br/>{ctrl_pn}"]
    CTRL -->|"PWM 驱动"| SW
    GND["GND"] --- SW
    GND --- CTRL
```"""


def _topology_mermaid_ldo(
    ctrl_pn: str, vin_str: str, vout_str: str, iout_str: str
) -> str:
    return f"""```mermaid
graph LR
    VIN["输入电源<br/>{vin_str}"] --> FIN["输入旁路电容 (CIN)"]
    FIN --> ADJ["调整管 (Pass Element)<br/>PMOS / NPN"]
    ADJ --> FOUT["输出滤波电容 (COUT)"]
    FOUT --> VOUT["稳压输出<br/>{vout_str} / {iout_str}"]
    FOUT -->|"反馈分压"| ERR["误差放大器 (EA)<br/>{ctrl_pn}"]
    ERR -->|"驱动信号"| ADJ
    GND["GND"] --- ERR
```"""


def _detect_converter_type(part_number: str) -> str:
    """检测 Buck 器件类型：'converter'（集成MOSFET）或 'controller'（外置MOSFET）。"""
    pn_upper = (part_number or "").upper()
    # 集成式同步降压转换器 (Integrated FET)
    if any(pn_upper.startswith(p) for p in ("TPS54", "TPS56", "TPS62", "TPS63",
                                              "ADP23", "MCP16", "ST1S")):
        return "converter"
    # LM2596/LM2576 是集成开关的非同步稳压器，归入 controller 模板（需外置二极管）
    return "controller"


def _get_topology_mermaid(
    topology: Optional[str],
    ctrl_pn: str,
    vin_str: str,
    vout_str: str,
    iout_str: str,
) -> str:
    t = (topology or "").lower()
    if t == "boost":
        return _topology_mermaid_boost(ctrl_pn, vin_str, vout_str, iout_str)
    elif t in ("ldo", "linear"):
        return _topology_mermaid_ldo(ctrl_pn, vin_str, vout_str, iout_str)
    else:
        dev_type = _detect_converter_type(ctrl_pn)
        if dev_type == "converter":
            return _topology_mermaid_buck_converter(ctrl_pn, vin_str, vout_str, iout_str)
        return _topology_mermaid_buck_controller(ctrl_pn, vin_str, vout_str, iout_str)


_TOPO_TYPE_CN = {
    "buck": "降压型 (Buck) DC-DC 转换器",
    "boost": "升压型 (Boost) DC-DC 转换器",
    "buck_boost": "升降压型 (Buck-Boost) DC-DC 转换器",
    "ldo": "低压差线性稳压器 (LDO)",
    "linear": "线性稳压器 (Linear Regulator)",
}

_TOPO_STD_DESC = {
    "buck": (
        "降压型 DC-DC 转换器采用脉冲宽度调制（PWM）技术，通过控制功率开关管的导通占空比，"
        "将较高的输入电压转换为较低的稳定输出电压。电路拓扑由高边开关管（High-Side MOSFET）、"
        "低边同步整流管（Low-Side MOSFET）或续流二极管、储能电感及输出滤波电容构成。"
        "控制器通过对输出电压的闭环反馈调节占空比，维持输出稳定。"
    ),
    "boost": (
        "升压型 DC-DC 转换器利用电感储能原理，将较低的输入电压提升至较高的输出电压。"
        "其基本拓扑由输入滤波电感、功率开关管、整流二极管及输出滤波电容构成。"
        "当开关管导通时，电感储能；断开时，电感释放能量通过二极管向输出端充电，"
        "形成高于输入电压的输出。"
    ),
    "ldo": (
        "低压差线性稳压器（Low Dropout Regulator, LDO）通过调整串联调整管的工作点，"
        "在较小压差（通常低于 1 V）条件下实现电压稳定。相比开关电源，LDO 具有低噪声、"
        "低 电磁干扰（EMI） 的优点，适用于对纹波敏感的模拟及射频电路供电场景。"
        "其核心结构由调整管（PMOS 或 NPN 达林顿管）、误差放大器及反馈分压网络构成。"
    ),
}


def _llm_topology_details(
    constraints: RequirementConstraints,
    top_part: Optional[ScoredPart],
    mermaid_diagram: str,
) -> str:
    """LLM 生成拓扑工程细节说明（设计注意事项、关键元件选型指导）。"""
    part_info = ""
    if top_part:
        p = top_part.part
        part_info = (
            f"首选控制器：{p.part_number}（{p.manufacturer or '未知'}）\n"
            f"描述：{p.description or '—'}\n"
            f"输入电压范围：{p.input_voltage_min_v} ~ {p.input_voltage_max_v} V\n"
            f"最大输出电流：{p.output_current_max_a} A\n"
            f"工作温度范围：{p.temperature_min_c} ~ {p.temperature_max_c} °C\n"
        )

    system = (
        "你是一名资深模拟/电源电路工程师，擅长撰写专业技术文档。"
        "请基于提供的需求参数和拓扑框图，撰写拓扑结构的工程设计要点说明，"
        "包括关键无源元件（电感、电容）的典型参数估算方法和设计注意事项。"
        "不使用表情符号，语言正式，最多 300 字，纯段落，不含标题。"
    )
    user = (
        f"电路需求：{constraints.raw_input}\n"
        f"拓扑类型：{constraints.topology or '降压'}\n"
        f"输入电压：{constraints.input_voltage_nominal_v or '—'} V\n"
        f"输出电压：{constraints.output_voltage_v or '—'} V\n"
        f"输出电流：{constraints.output_current_a or '—'} A\n"
        f"{part_info}\n"
        "请说明该拓扑的主要无源元件参数估算要点及工程设计注意事项。"
    )
    result = _call_llm(system, user)
    return result.strip() if result else "（LLM 工程细节分析不可用，请参阅相关数据手册应用笔记。）"


def _build_topo_modules(topology: Optional[str], ctrl_pn: str, dev_type: str) -> dict:
    """根据器件类型构建功能模块说明表。"""
    if dev_type == "converter":
        return {
            "buck": [
                ("输入电容（CIN）", "抑制输入电压纹波，为开关电流提供低阻抗路径；选用 X7R/X5R 多层陶瓷电容（MLCC），注意耐压降额。"),
                ("自举电容（CBOOT）", f"为 {ctrl_pn} 内部上管栅极驱动提供悬浮电源，典型值 0.1μF，靠近 CBOOT-SW 引脚放置。"),
                ("Buck 转换器 IC", f"核心器件（{ctrl_pn}），内部集成同步整流 MOSFET、栅极驱动、误差放大器、PWM 比较器及保护电路。"),
                ("储能电感（L）", "开关周期内储存与释放磁能；选择饱和电流 > IOUT + ΔIL/2 的屏蔽式功率电感。"),
                ("输出电容（COUT）", "滤除电感纹波电流，维持输出电压稳定；低 等效串联电阻（ESR） 陶瓷电容对瞬态响应至关重要。"),
                ("反馈分压网络（Rfbt/Rfbb）", f"将 VOUT 分压后反馈至 {ctrl_pn} FB 引脚，与内部基准电压比较实现精确稳压。"),
            ],
        }
    else:
        return {
            "buck": [
                ("输入滤波网络", "抑制来自输入电源的差模与共模噪声，防止开关噪声反向传导；通常包含 电磁干扰（EMI） 滤波电感、X/Y 电容及输入旁路电容（CIN）。"),
                ("控制器/稳压器 IC", f"核心控制单元（{ctrl_pn}），内置功率开关管、振荡器、误差放大器及保护电路。"),
                ("续流二极管（D）", "在开关管关断期间为电感电流提供续流回路；需选用快恢复/肖特基二极管以降低反向恢复损耗。"),
                ("储能电感（L）", "在开关管导通与截止期间储存和释放磁能，实现能量的连续传递，是 Buck 拓扑的核心储能元件。"),
                ("输出滤波电容（COUT）", "滤除电感纹波电流引起的输出电压波动，维持输出电压稳定；低 等效串联电阻（ESR） 特性对动态响应至关重要。"),
            ],
        }


def _build_thermal_section(
    constraints: RequirementConstraints,
    top_part: Optional[ScoredPart],
    dev_type: str,
) -> List[str]:
    """生成热性能估算节。"""
    lines: List[str] = []
    vout = constraints.output_voltage_v or 5.0
    vin = constraints.input_voltage_nominal_v or 12.0
    iout = constraints.output_current_a or 3.0

    # 效率估算
    if dev_type == "converter":
        efficiency = 0.90  # 同步整流 Buck 典型效率
        lines.append(f"- **估算效率**：η ≈ {efficiency*100:.0f}%（同步整流 Buck，12V→5V 典型值）")
    else:
        efficiency = 0.82
        lines.append(f"- **估算效率**：η ≈ {efficiency*100:.0f}%（非同步 Buck，含续流二极管损耗）")

    pout = vout * iout
    pin = pout / efficiency
    ploss = pin - pout
    lines += [
        f"- **输出功率**：Pout = {vout}V × {iout}A = **{pout:.1f} W**",
        f"- **输入功率**：Pin = Pout / η = **{pin:.1f} W**",
        f"- **总功耗**：Ploss = Pin − Pout = **{ploss:.1f} W**",
        "",
        "**损耗分解估算：**",
    ]
    if dev_type == "converter":
        # 同步 Buck 典型损耗分布
        p_sw = ploss * 0.40
        p_cond = ploss * 0.30
        p_ind = ploss * 0.20
        p_other = ploss * 0.10
        lines += [
            f"| 损耗来源 | 估算占比 | 估算值 | 说明 |",
            f"|---------|---------|-------|------|",
            f"| 开关损耗 | ~40% | {p_sw:.2f} W | MOSFET 栅极充放电 + 开关交叠 |",
            f"| 导通损耗 | ~30% | {p_cond:.2f} W | HS/LS MOSFET Rds(on) 导通损耗 |",
            f"| 电感损耗 | ~20% | {p_ind:.2f} W | 直流电阻（DCR） + 磁芯损耗 |",
            f"| 其他 | ~10% | {p_other:.2f} W | 控制电路 + PCB 走线损耗 |",
        ]
    else:
        p_sw = ploss * 0.30
        p_diode = ploss * 0.30
        p_ind = ploss * 0.25
        p_other = ploss * 0.15
        lines += [
            f"| 损耗来源 | 估算占比 | 估算值 | 说明 |",
            f"|---------|---------|-------|------|",
            f"| 开关损耗 | ~30% | {p_sw:.2f} W | 内部开关管交叠损耗 |",
            f"| 二极管损耗 | ~30% | {p_diode:.2f} W | 续流二极管正向导通 Vf × I |",
            f"| 电感损耗 | ~25% | {p_ind:.2f} W | 直流电阻（DCR） + 磁芯损耗 |",
            f"| 其他 | ~15% | {p_other:.2f} W | 控制电路 + PCB 走线损耗 |",
        ]

    # 结温估算
    if top_part and top_part.part.temperature_max_c:
        tj_max = top_part.part.temperature_max_c
    else:
        tj_max = 125.0
    # 典型热阻（假设值，实际须查数据手册）
    theta_ja = 35.0 if dev_type == "converter" else 50.0
    # 环境温度：优先取约束最大值，缺失时用常见工业级上限
    ta = constraints.temperature_max_c
    if ta is None:
        ta = constraints.temperature_min_c or 25.0
        ta = max(ta, 25.0)  # 至少 25°C（室温基准）
    tj = ta + ploss * theta_ja
    margin = tj_max - tj

    lines += [
        "",
        "**结温估算（基于自然散热，无额外散热器）：**",
        f"- 假设热阻 θ<sub>JA</sub> ≈ {theta_ja} °C/W（需从数据手册确认实际值）",
        f"- T<sub>J</sub> = T<sub>A</sub> + P<sub>loss</sub> × θ<sub>JA</sub> = {ta} + {ploss:.1f} × {theta_ja} ≈ **{tj:.1f} °C**",
        f"- 器件最高结温 T<sub>J(max)</sub> = {tj_max} °C，余量 = {margin:.1f} °C",
    ]
    if margin < 20:
        lines.append(f"- ⚠ **热余量偏低**（{margin:.1f} °C），建议优化 Layout 散热铜皮面积或评估是否需要散热器。")
    elif margin < 40:
        lines.append(f"- 🟡 热余量适中（{margin:.1f} °C），Layout 时注意增加散热铜皮。")
    else:
        lines.append(f"- ✅ 热余量充足（{margin:.1f} °C）。")
    lines.append("")

    return lines


def _build_layout_guidance(topology: Optional[str], dev_type: str) -> str:
    """生成 PCB Layout 设计要点。"""
    t = (topology or "").lower()
    if t == "ldo":
        return (
            "- 输入电容 CIN 尽量靠近 LDO 的 VIN 和 GND 引脚，减小输入回路面积。\n"
            "- 输出电容 COUT 靠近 VOUT 和 GND，反馈走线远离大电流路径。\n"
            "- LDO 功耗较大时（压差 > 1V），利用 PCB 铜皮辅助散热。\n"
        )

    if dev_type == "converter":
        return (
            "**1. 功率回路最小化（最关键）**：\n"
            "- 输入电容 → IC VIN → IC SW → 电感 → 输出电容 → GND 构成的 di/dt 回路面积必须最小化。\n"
            "- 输入电容紧靠 IC VIN 和 PGND 引脚，距离 ≤ 5 mm。\n\n"
            "**2. SW 节点处理**：\n"
            "- SW 节点为高 dv/dt 噪声源，铜皮面积尽量小，避免形成天线效应。\n"
            "- SW 走线直接连接 IC SW 引脚 → 电感一端，不引出多余分支。\n\n"
            "**3. 反馈走线（FB / VSENSE）**：\n"
            "- FB 走线远离 SW 节点和电感，推荐在 PCB 内层或底层走线。\n"
            "- 反馈分压电阻 Rfbb 靠近 IC 的 GND 引脚（Kelvin 连接）。\n\n"
            "**4. 地平面设计**：\n"
            "- 功率地（PGND）和模拟地（AGND）在 IC 下方单点连接（星形接地）。\n"
            "- IC 底部 Thermal Pad 必须焊接并打过孔连接到内层地平面，兼顾散热与接地。\n\n"
            "**5. 输入/输出电容布局**：\n"
            "- 输入电容优先使用 0603 或更小封装以降低 等效串联电感（ESL）。\n"
            "- 多个输出电容并联时对称布局，均流路径等长。\n\n"
            "**6. 自举电容（CBOOT）**：\n"
            "- CBOOT 紧靠 IC 的 BOOT 和 SW 引脚放置，走线 ≤ 3 mm。\n"
        )
    else:
        return (
            "**1. 功率回路最小化**：\n"
            "- VIN → IC VIN → IC SW → 续流二极管 → 电感 → COUT → GND 回路面积尽可能小。\n"
            "- 续流二极管靠近 IC SW 和 GND 引脚，短而粗的走线。\n\n"
            "**2. 续流二极管布局**：\n"
            "- 二极管阴极直接连 SW 节点，阳极通过大面积铜皮连 GND。\n"
            "- 二极管的开关噪声是主要 电磁干扰（EMI） 源，建议在二极管两端并联 RC 缓冲吸收电路（Snubber）。\n\n"
            "**3. 反馈走线（FB）**：\n"
            "- FB 为高阻抗节点，远离 SW 和电感等噪声源。\n"
            "- 输出电容之后取反馈电压，而非电感之后直接取。\n\n"
            "**4. 地平面与散热**：\n"
            "- IC 底部 Thermal Pad / Tab 焊接到大面积 GND 铜皮。\n"
            "- 使用热过孔将热量传导至内层和底层 GND 平面。\n\n"
            "**5. 输入/输出电容**：\n"
            "- 输入电容靠近 IC VIN 引脚，使用低 等效串联电阻（ESR） 电解/陶瓷电容组合。\n"
            "- 输出电容靠近电感输出端，多颗并联降低 等效串联电阻（ESR）。\n"
        )


def generate_topology(
    constraints: RequirementConstraints,
    report: SelectionReport,
    rag_context: str = "",
) -> str:
    """生成电路功能模块拓扑结构分析文档（Markdown + Mermaid）。"""
    topology = constraints.topology or "buck"
    topo_cn = _TOPO_TYPE_CN.get(topology.lower(), topology)
    topo_desc = _TOPO_STD_DESC.get(topology.lower(), "")

    top_part = report.recommended_parts[0] if report.recommended_parts else None
    ctrl_pn = top_part.part.part_number if top_part else "待定"

    vin_str = f"{constraints.input_voltage_nominal_v} V" if constraints.input_voltage_nominal_v else "VIN"
    vout_str = f"{constraints.output_voltage_v} V" if constraints.output_voltage_v else "VOUT"
    iout_str = f"{constraints.output_current_a} A" if constraints.output_current_a else "IOUT"

    mermaid = _get_topology_mermaid(topology, ctrl_pn, vin_str, vout_str, iout_str)

    lines: List[str] = []

    # ── 文件头 ──────────────────────────────────────────────────────
    lines += [
        "# 电路功能模块拓扑结构分析",
        "",
        "## 文件信息",
        "",
        f"| 字段 | 内容 |",
        f"|------|------|",
        f"| 生成日期 | {_now()} |",
        f"| 需求标识 | {report.request_id[:8]} |",
        f"| 原始需求 | {constraints.raw_input} |",
        f"| 拓扑类型 | {topo_cn} |",
        f"| 首选控制器 | {ctrl_pn} |",
        f"| 设计参数 | 输入 {vin_str}，输出 {vout_str} / {iout_str} |",
        "",
        "> **说明**：本文件给出功能模块级（Block Level）拓扑描述，",
        "> 对应电子工程设计文档层次中的方框图（Block Diagram）阶段，",
        "> 而非完整元件级原理图（Schematic Diagram）。",
        "> 各功能模块的详细实现须参照控制器数据手册应用电路章节。",
        "",
    ]

    # ── 拓扑类型说明 ──────────────────────────────────────────────
    lines += [
        "## 一、拓扑类型及工作原理",
        "",
        f"**选定拓扑：{topo_cn}**",
        "",
        topo_desc or f"当前拓扑类型（{topology}）的标准描述不可用，请参阅相关教材。",
        "",
    ]

    # ── 功能模块框图 ──────────────────────────────────────────────
    lines += [
        "## 二、功能模块框图（Block Diagram）",
        "",
        "> 框图采用 Mermaid 流程图语法描述，可在 GitHub、Obsidian 等环境中直接渲染。",
        "> 框图中各节点代表功能子模块，箭头表示信号或功率流向，不代表物理连线关系。",
        "",
        mermaid,
        "",
    ]

    # ── 模块功能说明 ──────────────────────────────────────────────
    dev_type = _detect_converter_type(ctrl_pn)
    topo_modules = _build_topo_modules(topology, ctrl_pn, dev_type)
    modules = topo_modules.get(topology.lower(), topo_modules.get("buck", []))
    if modules:
        lines += [
            "## 三、功能模块说明",
            "",
            "| 功能模块 | 功能描述 |",
            "|---------|---------|",
        ]
        for mod_name, mod_desc in modules:
            lines.append(f"| **{mod_name}** | {mod_desc} |")
        lines.append("")

    # ── 关键设计参数 ──────────────────────────────────────────────
    lines += [
        "## 四、关键设计参数（参考值）",
        "",
        "以下参数为典型工程估算值，最终取值须依据控制器数据手册应用笔记进行精确计算。",
        "",
        "| 设计参数 | 需求值 | 说明 |",
        "|---------|-------|------|",
        f"| 输入电压（VIN） | {vin_str} | 标称工作电压 |",
        f"| 输出电压（VOUT） | {vout_str} | 稳压目标值 |",
        f"| 最大输出电流（IOUT） | {iout_str} | 峰值负载电流 |",
    ]
    if constraints.temperature_min_c is not None:
        lines.append(f"| 工作温度范围 | {constraints.temperature_min_c} ~ {constraints.temperature_max_c} °C | 环境温度要求 |")
    if constraints.grade:
        lines.append(f"| 应用等级 | {constraints.grade} | 认证等级要求 |")
    if top_part:
        lines.append(f"| 器件类型 | {'集成式同步转换器 (Integrated FET)' if dev_type == 'converter' else '非同步稳压器 / 控制器'} | — |")
    lines.append("")

    # ── LLM 工程设计要点 ──────────────────────────────────────────
    lines += [
        "## 五、工程设计要点（AI 辅助分析）",
        "",
        "> **数据来源声明**：本节内容由 AI 语言模型基于电路拓扑参数、器件规格及",
        "> RAG 工程知识库检索结果综合生成，属于工程参考建议，不构成正式设计规范。",
        "> 所有参数须依据控制器原厂数据手册及应用笔记进行核算验证。",
        "",
    ]
    if rag_context:
        lines += [
            "**工程知识库参考（RAG 检索结果）**：",
            "",
            rag_context,
            "",
        ]
    lines += [
        _llm_topology_details(constraints, top_part, mermaid),
        "",
    ]

    # ── P0 新增：效率与热性能估算 ──────────────────────────────────
    lines += [
        "## 六、效率与热性能初步估算",
        "",
        "> 以下计算为基于典型参数的工程估算，实际值因 PCB Layout、元件选型和工况而异。",
        "",
    ]
    lines += _build_thermal_section(constraints, top_part, dev_type)

    # ── P0 新增：PCB Layout 设计要点 ──────────────────────────────
    lines += [
        "## 七、PCB Layout 设计要点",
        "",
        "> 高频开关电源的性能与 电磁干扰（EMI） 特性高度依赖 PCB Layout。以下规则基于",
        "> IPC-2221 / IPC-2152 以及主流制造商应用笔记总结。",
        "",
        _build_layout_guidance(topology, dev_type),
        "",
    ]

    lines += [
        "---",
        "",
        f"*本文件由 ezplm-component-risk-agent 自动生成，生成时间：{_now()}。*",
        "*框图为功能模块级描述，完整原理图设计须参照控制器原厂参考设计（Reference Design）。*",
    ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# TopologyIR — 结构化输出（eZ-PLM 兼容格式）
# ═══════════════════════════════════════════════════════════════════

def generate_topology_ir(
    constraints: RequirementConstraints,
    report: SelectionReport,
    rag_context: str = "",
) -> TopologyIR:
    """生成结构化的 TopologyIR，可直接序列化为 JSON 对接 eZ-PLM 平台。"""
    import uuid as _uuid

    topology = constraints.topology or "buck"
    top_part = report.recommended_parts[0] if report.recommended_parts else None
    ctrl_pn = top_part.part.part_number if top_part else "待定"
    ctrl_mfr = top_part.part.manufacturer if top_part else None
    dev_type = _detect_converter_type(ctrl_pn)

    vin = constraints.input_voltage_nominal_v or 12.0
    vout = constraints.output_voltage_v or 5.0
    iout = constraints.output_current_a or 3.0
    fsw = 500
    tmin = constraints.temperature_min_c
    tmax = constraints.temperature_max_c

    # ── 节点定义 ──────────────────────────────────────────────────
    if dev_type == "converter":
        nodes = [
            TopoNode(node_id="vin_cin", node_type="input_filter", label="输入电容 CIN",
                     description="抑制输入纹波，为开关电流提供低阻抗路径", connected_to=["buck_ic"]),
            TopoNode(node_id="cboot", node_type="passive_capacitor", label="自举电容 CBOOT",
                     description="上管栅极驱动悬浮电源，0.1μF", connected_to=["buck_ic"], signal_type="control"),
            TopoNode(node_id="buck_ic", node_type="controller_ic", label=f"Buck 转换器 {ctrl_pn}",
                     description=f"集成同步整流 MOSFET（{ctrl_mfr or '未知'}）",
                     connected_to=["vin_cin", "cboot", "power_ind", "fb_resistors", "en_resistors", "css", "pgnd"]),
            TopoNode(node_id="power_ind", node_type="passive_inductor", label="储能电感 L",
                     description="典型 6.8μH，Isat > 4.2A", connected_to=["buck_ic", "cout"]),
            TopoNode(node_id="cout", node_type="passive_capacitor", label="输出电容 COUT",
                     description="2×22μF 多层陶瓷电容（MLCC） X7R 并联", connected_to=["power_ind", "vout", "fb_resistors"]),
            TopoNode(node_id="vout", node_type="output_filter", label=f"负载输出 {vout}V/{iout}A",
                     description="稳压输出端", connected_to=["cout"]),
            TopoNode(node_id="fb_resistors", node_type="passive_resistor", label="反馈分压 Rfbt/Rfbb",
                     description="VOUT 分压至 FB 引脚", connected_to=["cout", "buck_ic"], signal_type="feedback"),
            TopoNode(node_id="en_resistors", node_type="passive_resistor", label="使能分压 EN/欠压锁定（UVLO）",
                     description="设定欠压锁定阈值", connected_to=["buck_ic"], signal_type="control"),
            TopoNode(node_id="css", node_type="passive_capacitor", label="软启动电容 CSS",
                     description="控制启动浪涌电流", connected_to=["buck_ic"], signal_type="control"),
            TopoNode(node_id="pgnd", node_type="ground", label="功率地 PGND",
                     description="与 AGND 在 IC 底部单点连接", connected_to=["buck_ic"], signal_type="ground"),
        ]
    else:
        nodes = [
            TopoNode(node_id="vin_cin", node_type="input_filter", label="输入滤波网络",
                     description="电磁干扰（EMI） 滤波 + 输入旁路电容", connected_to=["ctrl_ic"]),
            TopoNode(node_id="ctrl_ic", node_type="controller_ic", label=f"Buck 控制器 {ctrl_pn}",
                     description=f"内置功率开关管（{ctrl_mfr or '未知'}）",
                     connected_to=["vin_cin", "power_ind", "fb_resistors", "pgnd"]),
            TopoNode(node_id="freewheel_diode", node_type="diode", label="续流二极管 D",
                     description="快恢复/肖特基，靠近 SW 和 GND",
                     connected_to=["ctrl_ic", "power_ind", "pgnd"]),
            TopoNode(node_id="power_ind", node_type="passive_inductor", label="储能电感 L",
                     description="典型 6.8μH，Isat > 4.2A",
                     connected_to=["ctrl_ic", "freewheel_diode", "cout"]),
            TopoNode(node_id="cout", node_type="passive_capacitor", label="输出滤波电容 COUT",
                     description="低 等效串联电阻（ESR） 多层陶瓷电容（MLCC） + 电解组合",
                     connected_to=["power_ind", "vout", "fb_resistors"]),
            TopoNode(node_id="vout", node_type="output_filter", label=f"负载输出 {vout}V/{iout}A",
                     description="稳压输出端", connected_to=["cout"]),
            TopoNode(node_id="fb_resistors", node_type="passive_resistor", label="反馈分压 Rfbt/Rfbb",
                     description="VOUT 分压至 FB 引脚", connected_to=["cout", "ctrl_ic"], signal_type="feedback"),
            TopoNode(node_id="pgnd", node_type="ground", label="功率地 PGND",
                     description="功率回路回流地", connected_to=["ctrl_ic", "freewheel_diode"], signal_type="ground"),
        ]

    # ── 外围元件 ──────────────────────────────────────────────────
    d_vout = vout / vin
    delta_il = iout * 0.3
    l_val = round((vin - vout) * d_vout / (fsw * 1000 * delta_il) * 1_000_000, 1) if fsw > 0 else 6.8

    ext_components = [
        ExternalComponent(refdes="L1", component_type="inductor", value=f"{l_val}μH",
                          selection_notes=f"Isat > {iout + delta_il/2:.1f}A，屏蔽式功率电感，直流电阻（DCR） < 50mΩ"),
        ExternalComponent(refdes="CIN1", component_type="capacitor_mlcc", value="10μF",
                          voltage_rating="50V", temperature_coefficient="X7R", package="1206",
                          selection_notes="靠近 IC VIN 引脚，耐压 ≥ 1.5×Vin(max)"),
        ExternalComponent(refdes="CIN2", component_type="capacitor_mlcc", value="0.1μF",
                          voltage_rating="50V", temperature_coefficient="X7R", package="0603",
                          selection_notes="高频去耦"),
        ExternalComponent(refdes="COUT1", component_type="capacitor_mlcc", value="22μF",
                          voltage_rating="16V", temperature_coefficient="X7R", package="0805",
                          selection_notes="DC 偏压降额后实际约 12μF，2 颗并联"),
        ExternalComponent(refdes="COUT2", component_type="capacitor_mlcc", value="22μF",
                          voltage_rating="16V", temperature_coefficient="X7R", package="0805",
                          selection_notes="与 COUT1 并联降低 等效串联电阻（ESR）"),
        ExternalComponent(refdes="RFBT", component_type="resistor", value="依据 Vout/Vref 计算",
                          tolerance="±1%",
                          selection_notes=f"RFBT/RFBB = (Vout/Vref - 1)，查数据手册确定 Vref"),
        ExternalComponent(refdes="RFBB", component_type="resistor", value="依据 Vout/Vref 计算",
                          tolerance="±1%",
                          selection_notes="靠近 IC FB 引脚，Kelvin 连接"),
    ]
    if dev_type == "converter":
        ext_components.append(ExternalComponent(
            refdes="CBOOT", component_type="capacitor_mlcc", value="0.1μF",
            voltage_rating="25V", temperature_coefficient="X7R", package="0603",
            selection_notes="自举电容，紧靠 BOOT-SW 引脚，走线 ≤ 3mm"))
    else:
        ext_components.append(ExternalComponent(
            refdes="D1", component_type="diode", value="肖特基 ≥ 40V/3A",
            selection_notes="续流二极管，推荐 SS34 或同等规格"))

    # ── 热估算 ────────────────────────────────────────────────────
    efficiency = 0.90 if dev_type == "converter" else 0.82
    pout = vout * iout
    ploss = pout / efficiency - pout
    theta_ja = 35.0 if dev_type == "converter" else 50.0
    ta_val = max(tmax or tmin or 25.0, 25.0)
    tj = ta_val + ploss * theta_ja
    tj_max = top_part.part.temperature_max_c if top_part and top_part.part.temperature_max_c else 125.0

    thermal = ThermalEstimate(
        estimated_efficiency_pct=round(efficiency * 100, 1),
        output_power_w=round(pout, 1),
        total_power_loss_w=round(ploss, 1),
        junction_temp_c=round(tj, 1),
        junction_temp_max_c=tj_max,
        thermal_margin_c=round(tj_max - tj, 1),
        theta_ja_assumed=theta_ja,
        needs_heatsink=(tj_max - tj < 20),
    )

    # ── Mermaid 图 ────────────────────────────────────────────────
    mermaid = _get_topology_mermaid(topology, ctrl_pn, f"{vin} V", f"{vout} V", f"{iout} A")

    # ── Layout ────────────────────────────────────────────────────
    layout_rules = [
        "功率回路面积最小化 (< 1cm²)：CIN → IC VIN → IC SW → L → COUT → GND",
        "SW 节点铜皮面积最小化，避免辐射天线效应",
        "FB 走线远离 SW 节点和电感，推荐内层走线",
        "PGND 和 AGND 在 IC 底部 Thermal Pad 处单点连接（星形接地）",
        "IC Thermal Pad 焊接 + ≥4 个热过孔 (φ0.3mm) 连接内层地平面",
    ]

    # ── RAG 引用 ──────────────────────────────────────────────────
    rag_refs = []
    if rag_context:
        for line in rag_context.split("\n"):
            line = line.strip()
            if line.startswith("【") and "（相关度:" in line:
                rag_refs.append(line.split("】")[0].replace("【", ""))

    return TopologyIR(
        topology_id=str(_uuid.uuid4()),
        topology_type=topology,
        controller_mpn=ctrl_pn,
        controller_manufacturer=ctrl_mfr,
        topology_description=_TOPO_STD_DESC.get(topology.lower(), ""),
        primary_parameters={
            "vin_nominal_v": vin,
            "vin_min_v": constraints.input_voltage_min_v,
            "vin_max_v": constraints.input_voltage_max_v,
            "vout_v": vout,
            "iout_a": iout,
            "fsw_khz": fsw,
            "temp_min_c": tmin,
            "temp_max_c": tmax,
            "grade": constraints.grade or "industrial",
            "device_type": dev_type,
        },
        nodes=nodes,
        external_components=ext_components,
        thermal_estimate=thermal,
        mermaid_diagram=mermaid,
        layout_rules=layout_rules,
        design_references=["IPC-2221", "IPC-2152"],
        rag_knowledge_refs=rag_refs,
        generated_at=datetime.now().isoformat(),
    )


# ═══════════════════════════════════════════════════════════════════
# 统一入口：生成并保存三份报告
# ═══════════════════════════════════════════════════════════════════

def _extract_rag_context(report: SelectionReport) -> str:
    """从证据链中提取 RAG 检索到的知识内容。"""
    try:
        from app.rag import get_rag_store
        store = get_rag_store()
        if store.count == 0:
            return ""
        # 重建 RAG 查询（与 agent_orchestrator 保持一致）
        query_parts = [report.user_input]
        c = report.constraints
        if c.topology:
            query_parts.append(c.topology)
        if c.output_voltage_v and c.output_current_a:
            query_parts.append(f"{c.output_voltage_v}V {c.output_current_a}A")
        results = store.query(" ".join(query_parts), top_k=3)
        if not results:
            return ""
        from app.rag import build_context_from_results
        return build_context_from_results(results, max_chars=1200)
    except Exception:
        return ""


def generate_all_reports(
    report: SelectionReport,
    constraints: RequirementConstraints,
    output_dir: str = "docs/reports",
) -> Dict[str, str]:
    """生成并保存三份报告，返回 {文件名: 文件路径} 映射。"""
    import os as _os
    from pathlib import Path

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    req_short = constraints.raw_input[:20].replace(" ", "_").replace("/", "-")
    base = Path(output_dir) / f"{ts}_{req_short}"
    base.mkdir(parents=True, exist_ok=True)

    # 提取 RAG 知识上下文
    rag_context = _extract_rag_context(report)

    files: Dict[str, str] = {}

    bom_path = base / "01_BOM_元器件选型清单.md"
    bom_path.write_text(generate_bom(report, rag_context=rag_context), encoding="utf-8")
    files["BOM"] = str(bom_path)

    risk_path = base / "02_风险评估报告.md"
    risk_path.write_text(generate_risk_report(report, constraints, rag_context=rag_context), encoding="utf-8")
    files["RiskReport"] = str(risk_path)

    topo_path = base / "03_电路拓扑结构分析.md"
    topo_path.write_text(generate_topology(constraints, report, rag_context=rag_context), encoding="utf-8")
    files["Topology"] = str(topo_path)

    # 生成结构化 TopologyIR JSON（eZ-PLM 对接用）
    import json as _json
    topo_ir_path = base / "04_TopologyIR.json"
    topo_ir = generate_topology_ir(constraints, report, rag_context=rag_context)
    topo_ir_path.write_text(_json.dumps(topo_ir.dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    files["TopologyIR"] = str(topo_ir_path)

    return files
