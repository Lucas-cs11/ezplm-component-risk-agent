"""
agent_tools.py — LangChain ReAct Agent 工具集

将项目现有的 Pipeline 功能封装为 LangChain Tool，
供 ReAct Agent 在"思考→行动→观察"循环中调用。

工具设计原则：
- 每个工具返回结构化文本（字符串），Agent 基于文本内容做下一步决策
- 工具内部调用现有 app 模块，不重复实现逻辑
- 工具粒度适中：既不过细（Agent 难以编排），也不过粗（失去 ReAct 意义）
"""

from __future__ import annotations

import json
from typing import Optional

from langchain.tools import tool


# ═══════════════════════════════════════════════════════════════════
# Tool 1: 元器件搜索
# ═══════════════════════════════════════════════════════════════════

# ── 搜索重试计数器（防止 Agent 循环） ────────────────────────
_search_retry_count: dict = {}


@tool
def search_components(requirement: str) -> str:
    """搜索电子元器件数据库，返回匹配的候选器件列表。

    将用户的自然语言选型需求解析为结构化约束，然后查询 eZ-PLM API，
    返回匹配器件的型号、参数和评分摘要。

    **关键规则**：
    - 对每个用户需求只调用一次。
    - 如果返回"未找到匹配"，说明数据库中没有该类型器件，必须停止搜索并如实告知用户。
    - **严禁**在搜索结果为空时用不同措辞重复搜索。直接回复用户当前数据库暂无该类型器件。

    Args:
        requirement: 用户的自然语言选型需求
    """
    global _search_retry_count

    # ── 重试保护 ──
    req_key = requirement.strip().lower()[:60]
    count = _search_retry_count.get(req_key, 0) + 1
    _search_retry_count[req_key] = count

    if count > 2:
        return (
            f"【已搜索 {count} 次，停止重复搜索】\n"
            f"该需求 \"{requirement[:50]}...\" 在当前数据库中未找到匹配器件。\n"
            f"**必须立即回复用户**：说明数据库暂不支持该类型器件，建议调整需求或联系管理员扩充数据。\n"
            f"**不要再调用任何搜索工具**，直接基于已有信息回复用户。"
        )

    from .agent_orchestrator import analyze

    try:
        report = analyze(requirement)
    except Exception as e:
        return f"搜索失败：{e}。请直接告知用户当前无法完成搜索，不要重复尝试。"

    rec = report.recommended_parts
    bak = [s for s in report.candidates if s.recommendation_level == "backup"]
    total = len(report.candidates)

    lines = [
        f"## 搜索结果摘要",
        f"- 总候选器件：{total} 款",
        f"- 推荐器件：{len(rec)} 款（综合评分 ≥ 75）",
        f"- 备选器件：{len(bak)} 款",
        f"- 风险等级：{report.risks.overall_risk_level.upper() if report.risks else 'N/A'}",
        "",
    ]

    if rec:
        lines.append("### 推荐器件 TOP 5")
        lines.append("| 排名 | 型号 | 厂商 | 得分 | Vout | Iout | 封装 |")
        lines.append("|------|------|------|------|------|------|------|")
        for s in rec[:5]:
            p = s.part
            vout = f"{p.output_voltage_v}V" if p.output_voltage_v else "可调"
            iout = f"{p.output_current_max_a}A" if p.output_current_max_a else "-"
            lines.append(
                f"| {s.rank} | {p.part_number} | {p.manufacturer or '-'} "
                f"| {s.score.total_score:.0f} | {vout} | {iout} | {p.package or '-'} |"
            )

        top = rec[0]
        lines.append("")
        lines.append(f"**首选推荐**：{top.part.part_number}（{top.part.manufacturer or '未知厂商'}）")
        lines.append(f"综合得分 {top.score.total_score:.0f}，")
        key_reasons = [r for r in top.score.reasons if "✓" in r][:3]
        for kr in key_reasons:
            lines.append(f"- {kr}")
    elif total == 0:
        lines.append(
            "【重要】搜索结果为空 — 数据库中**完全没有**匹配该需求的器件。"
            "**必须立即停止搜索**，直接回复用户：当前数据库暂不支持该类型器件的选型。"
        )
    else:
        lines.append("⚠ 候选器件评分未达推荐门槛。请基于以下备选器件回复用户，或建议放宽约束。")
        if bak:
            lines.append(f"有 {len(bak)} 款备选器件可参考（得分 50-74）。")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Tool 2: 工程知识检索
# ═══════════════════════════════════════════════════════════════════

@tool
def query_design_knowledge(topic: str) -> str:
    """从工程知识库中检索设计规范、公式和最佳实践。

    知识库包含 Buck/Boost/LDO 设计公式、热管理、车规认证要求、
    PCB Layout 规范、供应链风险评估框架等内容。

    使用时机：
    - 搜索到候选器件后，需要验证设计参数时
    - 需要了解特定拓扑（Buck/Boost/LDO）的设计要点时
    - 需要确认车规、工业级等认证要求时

    Args:
        topic: 检索主题，如"buck 电感选型公式"、"车规 AEC-Q100 温度等级"、"PCB Layout 接地"
    """
    try:
        from .rag import get_rag_store

        store = get_rag_store()
        if store.count == 0:
            return "知识库为空。请先运行 scripts/build_knowledge_base.py 构建知识库。"

        results = store.query(topic, top_k=3)

        if not results:
            return f"未找到与「{topic}」相关的工程知识。请尝试更具体的关键词。"

        lines = [f"## 知识库检索结果（主题：{topic}）", ""]
        for i, r in enumerate(results, 1):
            title = r["metadata"].get("title", f"结果 {i}")
            lines.append(f"### {i}. {title}（相关度: {r['score']:.2f}）")
            lines.append(r["content"])
            lines.append(f"*来源: {r['metadata'].get('source', '工程知识库')}*")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return f"知识库检索失败：{e}"


# ═══════════════════════════════════════════════════════════════════
# Tool 3: 替代器件查找（场景3专用）
# ═══════════════════════════════════════════════════════════════════

@tool
def find_alternative_parts(original_part_number: str) -> str:
    """查找指定器件的替代型号，支持国产替代和功能等效替代。

    使用时机：
    - 用户追问"有国产替代吗？"
    - 需要为已推荐器件寻找备份方案
    - 需要对比进口 vs 国产方案

    Args:
        original_part_number: 原始器件型号，如 "LM2596S-5.0/NOPB"
    """
    from .agent_orchestrator import replacement_report

    try:
        rep = replacement_report(original_part_number)
    except Exception as e:
        return f"替代搜索失败：{e}"

    lines = [
        f"## 替代器件搜索结果（原始型号：{original_part_number}）",
        f"- 兼容等级：{rep.compatibility_level}",
        f"- 候选替代：{len(rep.replacement_candidates)} 款",
        "",
    ]

    if rep.replacement_candidates:
        lines.append("| 排名 | 型号 | 厂商 | 得分 | 推荐 |")
        lines.append("|------|------|------|------|------|")
        for s in rep.replacement_candidates[:5]:
            level = s.recommendation_level or "-"
            lines.append(
                f"| {s.rank} | {s.part.part_number} | {s.part.manufacturer or '-'} "
                f"| {s.score.total_score:.0f} | {level} |"
            )
    else:
        lines.append("未找到替代器件。建议扩充数据源或联系供应商。")

    lines.append("")
    lines.append(rep.comparison_summary)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Tool 4: 完整报告生成
# ═══════════════════════════════════════════════════════════════════

@tool
def generate_full_report(requirement: str) -> str:
    """生成完整的元器件选型报告，包含 BOM 清单、风险评估和拓扑分析。

    此工具应在完成元器件搜索和知识检索后调用，是选型流程的最后一步。
    生成的报告将保存到 docs/reports/ 目录。

    使用时机：
    - 已完成搜索和知识检索，用户要求输出正式报告
    - 需要生成可提交的标准化文档

    Args:
        requirement: 用户的原始选型需求文本
    """
    from .agent_orchestrator import analyze
    from .output_generator import generate_all_reports

    try:
        report = analyze(requirement)
        files = generate_all_reports(report, report.constraints)
    except Exception as e:
        return f"报告生成失败：{e}"

    rec = len(report.recommended_parts)
    total = len(report.candidates)

    lines = [
        "## ✅ 完整报告已生成",
        "",
        f"**需求**：{requirement}",
        f"**候选器件**：{total} 款 | **推荐**：{rec} 款",
        f"**风险等级**：{report.risks.overall_risk_level.upper() if report.risks else 'N/A'}",
        "",
        "### 输出文件",
    ]
    for name, path in files.items():
        fname = path.split("/")[-1]
        lines.append(f"- **{name}**：`{fname}`")

    if report.recommended_parts:
        top = report.recommended_parts[0]
        p = top.part
        lines += [
            "",
            f"### 首选推荐",
            f"- **{p.part_number}**（{p.manufacturer or '未知'}）— 综合得分 {top.score.total_score:.0f}",
            f"- 输入电压：{p.input_voltage_min_v}~{p.input_voltage_max_v}V",
            f"- 输出电流：{p.output_current_max_a}A",
            f"- 推荐理由：{top.score.reasons[0] if top.score.reasons else '参数匹配'}",
        ]

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 工具列表 + 注册
# ═══════════════════════════════════════════════════════════════════

from .tool_schema import register_tool

register_tool(search_components, pool="selection")
register_tool(query_design_knowledge, pool="selection")
register_tool(find_alternative_parts, pool="replacement")
register_tool(generate_full_report, pool="selection")

# 全量工具（向后兼容）
AGENT_TOOLS = [
    search_components,
    query_design_knowledge,
    find_alternative_parts,
    generate_full_report,
]

# 按池分类
SELECTION_TOOLS = [search_components, query_design_knowledge, generate_full_report]
REPLACEMENT_TOOLS = [search_components, find_alternative_parts]  # 替代也需要搜索
CHAT_TOOLS = [query_design_knowledge, search_components]  # 对话模式：知识检索 + 参数收集完成后可直接搜索
