"""
workflow_executor.py — 工作流后端执行引擎

将 ComfyUI 式 JSON 工作流图翻译为实际的 eZmanbo 流水线调用。
每个节点类型映射到对应的后端函数或 Agent 操作。
"""
from __future__ import annotations

import json
import asyncio
from typing import Any, AsyncGenerator


# ── 节点执行器注册表 ───────────────────────────────────────────────

_NODE_HANDLERS: dict[str, Any] = {}

def register_node(node_type: str):
    def decorator(fn):
        _NODE_HANDLERS[node_type] = fn
        return fn
    return decorator


# ── 内置节点处理器 ─────────────────────────────────────────────────

@register_node("parse")
async def _handle_parse(ctx: dict) -> dict:
    from .constraint_checker import extract_constraints
    constraints = extract_constraints(ctx.get("user_input", ""))
    return {"constraints": constraints}


@register_node("search")
async def _handle_search(ctx: dict) -> dict:
    from .ezplm_client import search_components_by_constraints
    constraints = ctx.get("constraints") or {}
    candidates = await asyncio.to_thread(search_components_by_constraints, constraints)
    return {"candidates": candidates}


@register_node("score")
async def _handle_score(ctx: dict) -> dict:
    from .scoring import score_candidates
    candidates = ctx.get("candidates") or []
    constraints = ctx.get("constraints") or {}
    scored = await asyncio.to_thread(score_candidates, candidates, constraints)
    return {"scored": scored}


@register_node("evidence")
async def _handle_evidence(ctx: dict) -> dict:
    from .rag import build_evidence_for_candidates
    scored = ctx.get("scored") or []
    evidence = await asyncio.to_thread(build_evidence_for_candidates, scored)
    return {"evidence": evidence}


@register_node("risk")
async def _handle_risk(ctx: dict) -> dict:
    from .report_generator import build_risk_assessment
    scored = ctx.get("scored") or []
    constraints = ctx.get("constraints") or {}
    risks = await asyncio.to_thread(build_risk_assessment, scored, constraints)
    return {"risks": risks}


@register_node("dual_verify")
async def _handle_dual_verify(ctx: dict) -> dict:
    from .dual_model_verify import verify_selection
    scored = ctx.get("scored") or []
    constraints = ctx.get("constraints") or {}

    req_text = ", ".join(
        f"{k}={v}" for k, v in constraints.items()
        if k in ("input_voltage_nominal_v", "output_voltage_v", "output_current_a")
    )
    candidates_flat = []
    for sp in scored[:8]:
        part = getattr(sp, "part", sp) if not isinstance(sp, dict) else sp.get("part", {})
        score = getattr(sp, "score", ) if not isinstance(sp, dict) else sp.get("score", {})
        pn = getattr(part, "part_number", None) or (part.get("part_number") if isinstance(part, dict) else "?")
        total = getattr(score, "total_score", 0) or (score.get("total_score", 0) if isinstance(score, dict) else 0)
        mfr = getattr(part, "manufacturer", "") or (part.get("manufacturer", "") if isinstance(part, dict) else "")
        candidates_flat.append({"part_number": pn, "manufacturer": mfr, "total_score": total})

    primary_top = candidates_flat[0]["part_number"] if candidates_flat else ""
    result = await verify_selection(req_text, candidates_flat, primary_top, ctx.get("risk_level", "medium"))
    return {"verify_result": result.__dict__ if hasattr(result, "__dict__") else {}}


@register_node("report")
async def _handle_report(ctx: dict) -> dict:
    from .report_generator import build_report
    constraints_obj = ctx.get("constraints")
    scored = ctx.get("scored") or []
    evidence = ctx.get("evidence") or []
    report = await asyncio.to_thread(build_report, constraints_obj, scored, evidence)
    return {"report": report}


# ── 工作流执行引擎 ──────────────────────────────────────────────────

async def execute_workflow(
    workflow_json: dict,
    initial_context: dict,
) -> AsyncGenerator[dict, None]:
    """
    执行 ComfyUI 式工作流图。

    workflow_json 格式：
    {
      "nodes": [{"id": "n1", "type": "parse", ...}, ...],
      "edges": [{"source": "n1", "target": "n2"}, ...]
    }

    每个节点执行完后 yield 进度事件，最终 yield done 事件。
    """
    nodes = {n["id"]: n for n in workflow_json.get("nodes", [])}
    edges = workflow_json.get("edges", [])

    # Build adjacency: node_id → list of successor node_ids
    successors: dict[str, list[str]] = {nid: [] for nid in nodes}
    predecessors: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in edges:
        src, tgt = e["source"], e["target"]
        if src in successors: successors[src].append(tgt)
        if tgt in predecessors: predecessors[tgt].append(src)

    # Topological sort
    in_degree = {nid: len(preds) for nid, preds in predecessors.items()}
    queue = [nid for nid, d in in_degree.items() if d == 0]
    topo_order: list[str] = []
    while queue:
        nid = queue.pop(0)
        topo_order.append(nid)
        for succ in successors[nid]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    # Execute in topological order, accumulating context
    ctx = dict(initial_context)
    completed: list[str] = []

    for nid in topo_order:
        node = nodes[nid]
        node_type = node.get("type", "unknown")
        handler = _NODE_HANDLERS.get(node_type)

        yield {"event": "node_start", "node_id": nid, "node_type": node_type}

        if handler:
            try:
                result = await handler(ctx)
                ctx.update(result)
                completed.append(nid)
                yield {"event": "node_done", "node_id": nid, "node_type": node_type, "output_keys": list(result.keys())}
            except Exception as e:
                yield {"event": "node_error", "node_id": nid, "error": str(e)}
                return
        else:
            yield {"event": "node_skip", "node_id": nid, "reason": f"unknown type: {node_type}"}
            completed.append(nid)

    yield {"event": "workflow_done", "completed": completed, "context_keys": list(ctx.keys())}


def build_default_workflow() -> dict:
    """返回 eZmanbo 默认选型流水线的 JSON 工作流描述。"""
    return {
        "name": "eZmanbo 默认选型流水线",
        "nodes": [
            {"id": "parse",       "type": "parse",       "label": "约束解析"},
            {"id": "search",      "type": "search",      "label": "eZ-PLM 检索"},
            {"id": "score",       "type": "score",       "label": "多维评分"},
            {"id": "evidence",    "type": "evidence",    "label": "证据链构建"},
            {"id": "risk",        "type": "risk",        "label": "风险评估"},
            {"id": "dual_verify", "type": "dual_verify", "label": "双模型验证"},
            {"id": "report",      "type": "report",      "label": "报告生成"},
        ],
        "edges": [
            {"source": "parse",    "target": "search"},
            {"source": "search",   "target": "score"},
            {"source": "score",    "target": "evidence"},
            {"source": "score",    "target": "risk"},
            {"source": "score",    "target": "dual_verify"},
            {"source": "evidence", "target": "report"},
            {"source": "risk",     "target": "report"},
            {"source": "dual_verify", "target": "report"},
        ],
    }
