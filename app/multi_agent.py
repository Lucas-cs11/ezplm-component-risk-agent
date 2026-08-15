"""
multi_agent.py — 多智能体并行编排器 (Multi-Agent Orchestration)

将原串行 I/O 密集阶段改造为并发执行，大幅降低端到端延迟。

编排策略：
  SearchOrchestrator   — 关键词分批，多 SearchAgent 并行查询 eZ-PLM
  EnrichOrchestrator   — 多 EnrichAgent 并发富化器件详情
  AlertOrchestrator    — 并发查询 EOL/Obsolete 替代料
  ReportOrchestrator   — 并发获取 Top-N 参考设计

设计原则：
  · asyncio.Semaphore 控制并发量，避免触发 eZ-PLM 速率限制
  · blocking HTTP 调用全部通过 asyncio.to_thread 在线程池运行
  · 任一 Agent 失败 → 静默降级，其余 Agent 继续
  · 不改变输出数据结构，与现有 scoring / evidence / report 模块零耦合
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

_SEARCH_CONCURRENCY = 4   # 同时最多 N 个 eZ-PLM 搜索请求
_ENRICH_CONCURRENCY = 3   # 同时最多 N 个详情请求
_SEARCH_BATCH_SIZE  = 3   # 每 SearchAgent 负责的关键词数


# ══════════════════════════════════════════════════════════════════
# SearchOrchestrator — 并行搜索（替换原 search_parts 串行循环）
# ══════════════════════════════════════════════════════════════════

async def parallel_search_parts(constraints) -> List:
    """
    多 SearchAgent 并行查询 eZ-PLM，替代原 search_parts() 串行循环。

    原版：N 关键词 × (HTTP + sleep) ≈ O(N) 时间
    新版：ceil(N / batch_size) 个 Agent 并发 ≈ O(1) 时间（受并发限制）

    预期加速：搜索阶段从 ~5s → ~1.5s（15 个关键词 / batch=3 → 5 个 Agent 并发）
    """
    from .ezplm_client import (
        _generate_keywords, _search_keyword,
        _map_api_part, _part_matches, _get_api_key,
    )

    if not _get_api_key():
        return []

    keywords = _generate_keywords(constraints)
    if not keywords:
        return []

    batches = [keywords[i:i + _SEARCH_BATCH_SIZE]
               for i in range(0, len(keywords), _SEARCH_BATCH_SIZE)]

    sem = asyncio.Semaphore(_SEARCH_CONCURRENCY)

    async def _search_batch(batch: List[str]) -> List:
        results, seen = [], set()
        async with sem:
            for kw in batch:
                try:
                    raw_items, _ = await asyncio.to_thread(_search_keyword, kw)
                    for raw in raw_items:
                        part = _map_api_part(raw)
                        if part and part.part_number not in seen:
                            seen.add(part.part_number)
                            if _part_matches(part, constraints):
                                results.append(part)
                except Exception as exc:
                    logger.debug(f"[SearchAgent] kw={kw} skip: {exc}")
        return results

    batch_results = await asyncio.gather(
        *[_search_batch(b) for b in batches],
        return_exceptions=True,
    )

    seen_pns: set = set()
    merged = []
    for res in batch_results:
        if isinstance(res, Exception):
            continue
        for part in res:
            if part.part_number not in seen_pns:
                seen_pns.add(part.part_number)
                merged.append(part)

    logger.debug(
        f"[SearchOrchestrator] {len(batches)} agents × {_SEARCH_BATCH_SIZE} kws"
        f" → {len(merged)} parts"
    )
    return merged


# ══════════════════════════════════════════════════════════════════
# EnrichOrchestrator — 并行详情富化（替换原 time.sleep 串行循环）
# ══════════════════════════════════════════════════════════════════

async def parallel_enrich_candidates(candidates: List, max_enrich: int = 8) -> List:
    """
    多 EnrichAgent 并发获取 eZ-PLM 详情，替代原 enrich_candidates_with_details()。

    原版：max_enrich × (HTTP + sleep) ≈ O(max_enrich) 时间
    新版：并发限速下 ≈ O(ceil(max_enrich/concurrency)) 时间
    """
    from .ezplm_client import fetch_part_detail, _get_api_key

    if not _get_api_key():
        return candidates

    to_enrich = [p for p in candidates[:max_enrich]
                 if getattr(p, 'ezplm_part_id', None)]

    if not to_enrich:
        return candidates

    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def _enrich_one(part) -> Tuple[str, Any]:
        async with sem:
            try:
                detail = await asyncio.to_thread(fetch_part_detail, part.ezplm_part_id)
            except Exception:
                return part.part_number, part

        if detail is None:
            return part.part_number, part

        merged = part.dict() if hasattr(part, "dict") else {}
        for f in ("switching_frequency_khz", "quiescent_current_ua", "efficiency_pct",
                  "input_voltage_min_v", "input_voltage_max_v",
                  "output_voltage_v", "output_current_max_a",
                  "temperature_min_c", "temperature_max_c",
                  "package", "lifecycle_status", "datasheet_url",
                  "footprint_file", "symbol_file"):
            orig = merged.get(f)
            new_val = getattr(detail, f, None)
            if (orig is None or orig == "" or orig == []) and new_val:
                merged[f] = new_val

        orig_feats = merged.get("features") or []
        new_feats  = getattr(detail, "features", []) or []
        merged["features"] = list(dict.fromkeys(orig_feats + new_feats))

        try:
            from .schemas import PartIR
            return part.part_number, PartIR(**merged)
        except Exception:
            return part.part_number, part

    enrich_results = await asyncio.gather(
        *[_enrich_one(p) for p in to_enrich],
        return_exceptions=True,
    )

    enriched_map: Dict[str, Any] = {}
    for res in enrich_results:
        if isinstance(res, Exception):
            continue
        pn, enriched = res
        enriched_map[pn] = enriched

    return [enriched_map.get(p.part_number, p) for p in candidates]


# ══════════════════════════════════════════════════════════════════
# AlertOrchestrator — 并发生命周期告警替代料查询
# ══════════════════════════════════════════════════════════════════

async def parallel_lifecycle_replacements(alerts: List[Dict]) -> List[Dict]:
    """
    并发为 HIGH severity 器件查询替代料。

    原版：串行最多 2 个；新版：全部 HIGH 器件并发查询
    """
    from .ezplm_client import find_replacements

    high = [a for a in alerts if a.get("severity") == "HIGH"]
    if not high:
        return alerts

    async def _find_one(alert: Dict) -> Tuple[str, List[str]]:
        try:
            alts = await asyncio.to_thread(find_replacements, alert["part_number"])
            return alert["part_number"], [a.part_number for a in (alts or [])[:3]]
        except Exception:
            return alert["part_number"], []

    results = await asyncio.gather(*[_find_one(a) for a in high])
    alt_map = dict(results)
    for alert in alerts:
        alert["alternatives"] = alt_map.get(alert["part_number"], alert.get("alternatives", []))
    return alerts


# ══════════════════════════════════════════════════════════════════
# ReportOrchestrator — 并发获取参考设计
# ══════════════════════════════════════════════════════════════════

async def parallel_fetch_ref_designs_from_scored(scored: List) -> Dict[str, List]:
    """
    并发获取 Top-5 推荐器件的参考设计。

    原版：串行 3 个；新版：5 个并发 ≈ 速度提升 3-5×
    """
    from .ezplm_client import fetch_reference_designs

    top = [s for s in scored[:5] if getattr(s.part, 'ezplm_part_id', None)]
    if not top:
        return {}

    async def _fetch(sp) -> Tuple[str, List]:
        try:
            designs = await asyncio.to_thread(
                fetch_reference_designs, sp.part.ezplm_part_id
            )
            return sp.part.part_number, designs or []
        except Exception:
            return sp.part.part_number, []

    results = await asyncio.gather(*[_fetch(sp) for sp in top])
    return {pn: rds for pn, rds in results if rds}
