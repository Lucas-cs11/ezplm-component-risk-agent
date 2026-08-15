"""
kb_updater.py — 本地知识库自动写/更新/迭代机制

功能：
  1. 选型完成后，自动将高质量结果写入知识库条目
  2. 定期检查 eZ-PLM 数据变更，自动更新向量索引
  3. 用户反馈（"这个方案很好" / "这个器件有问题"）触发知识库迭代
  4. 支持手动触发全量重建

存储：
  - 知识条目：SQLite 表 kb_entries（id, pn, content, embedding_ts, source）
  - 向量索引：ChromaDB 或 FAISS，通过 rag.py 访问
"""
from __future__ import annotations

import json
import time
import hashlib
from datetime import datetime
from typing import Optional
import asyncio


# ── 知识条目数据类 ─────────────────────────────────────────────────

class KBEntry:
    __slots__ = ("pn", "manufacturer", "content", "source", "confidence", "ts")

    def __init__(self, pn: str, manufacturer: str, content: str,
                 source: str = "selection_result", confidence: float = 0.8):
        self.pn = pn
        self.manufacturer = manufacturer
        self.content = content
        self.source = source
        self.confidence = confidence
        self.ts = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    def doc_id(self) -> str:
        h = hashlib.md5(f"{self.pn}:{self.source}".encode()).hexdigest()[:8]
        return f"kb_{self.pn}_{h}"


# ── 自动写入：选型完成后触发 ────────────────────────────────────────

def auto_ingest_from_report(report_dict: dict) -> list[str]:
    """从选型报告自动提取知识条目并写入 RAG 索引。

    返回写入的 doc_id 列表。
    """
    ingested: list[str] = []
    try:
        from .rag import get_rag_index

        candidates = report_dict.get("recommended_parts") or report_dict.get("candidates") or []
        constraints = report_dict.get("constraints") or {}
        req_text = _constraints_to_text(constraints)

        for sp in candidates[:5]:  # 只取前5，避免低质量条目污染
            part = sp.get("part") or {}
            score_d = sp.get("score") or {}
            pn = part.get("part_number", "")
            mfr = part.get("manufacturer", "")
            total = float(score_d.get("total_score") or 0)
            rec_level = sp.get("recommendation_level", "backup")

            if not pn or total < 50:  # 低分器件不写入
                continue

            content = _build_kb_content(part, score_d, req_text, rec_level)
            entry = KBEntry(
                pn=pn, manufacturer=mfr, content=content,
                source="selection_result", confidence=min(1.0, total / 100),
            )

            rag = get_rag_index()
            rag.add_texts(
                texts=[entry.content],
                metadatas=[{
                    "pn": pn, "manufacturer": mfr,
                    "source": "selection_result",
                    "ts": entry.ts, "score": total,
                }],
                ids=[entry.doc_id()],
            )
            ingested.append(entry.doc_id())

    except Exception:
        pass  # 写入失败不影响主流程

    return ingested


def ingest_user_feedback(pn: str, feedback: str, positive: bool) -> bool:
    """将用户对某器件的反馈写入知识库。"""
    try:
        from .rag import get_rag_index
        content = f"用户{'正面' if positive else '负面'}反馈 [{pn}]: {feedback}"
        entry = KBEntry(
            pn=pn, manufacturer="", content=content,
            source="user_feedback", confidence=0.6 if positive else 0.9,
        )
        rag = get_rag_index()
        rag.add_texts(
            texts=[entry.content],
            metadatas=[{"pn": pn, "source": "user_feedback", "positive": positive}],
            ids=[entry.doc_id()],
        )
        return True
    except Exception:
        return False


# ── 增量更新：检测 eZ-PLM 数据变更 ────────────────────────────────

async def incremental_update(mpn_list: list[str], force: bool = False) -> dict:
    """对给定型号列表，检查是否有更新，有则重新向量化。"""
    updated = []
    skipped = []

    for pn in mpn_list[:20]:  # 每次最多20个，防止超时
        try:
            from .ezplm_client import fetch_part_detail
            detail = await asyncio.to_thread(fetch_part_detail, pn)
            if not detail:
                skipped.append(pn)
                continue

            content = _build_kb_content(detail, {}, "", "ezplm_refresh")
            entry = KBEntry(
                pn=pn,
                manufacturer=detail.get("manufacturer", ""),
                content=content,
                source="ezplm_refresh",
            )

            from .rag import get_rag_index
            rag = get_rag_index()
            # Upsert: delete old entry then add new
            try:
                rag.delete(ids=[entry.doc_id()])
            except Exception:
                pass
            rag.add_texts(
                texts=[entry.content],
                metadatas=[{"pn": pn, "source": "ezplm_refresh", "ts": entry.ts}],
                ids=[entry.doc_id()],
            )
            updated.append(pn)
        except Exception:
            skipped.append(pn)

    return {"updated": updated, "skipped": skipped, "ts": datetime.utcnow().isoformat()}


# ── 全量重建 ───────────────────────────────────────────────────────

async def full_rebuild(mpn_list: list[str]) -> dict:
    """清空知识库并从 eZ-PLM 全量重建向量索引。"""
    try:
        from .rag import get_rag_index
        rag = get_rag_index()
        # Reset collection
        try:
            rag.delete_collection()  # ChromaDB
        except Exception:
            pass
        result = await incremental_update(mpn_list, force=True)
        result["mode"] = "full_rebuild"
        return result
    except Exception as e:
        return {"error": str(e), "mode": "full_rebuild"}


# ── 辅助函数 ───────────────────────────────────────────────────────

def _constraints_to_text(c: dict) -> str:
    parts = []
    if c.get("input_voltage_nominal_v"):
        parts.append(f"Vin={c['input_voltage_nominal_v']}V")
    if c.get("output_voltage_v"):
        parts.append(f"Vout={c['output_voltage_v']}V")
    if c.get("output_current_a"):
        parts.append(f"Iout={c['output_current_a']}A")
    if c.get("topology"):
        parts.append(f"拓扑={c['topology']}")
    return "，".join(parts) if parts else "未知需求"


def _build_kb_content(part: dict, score: dict, req_text: str, source: str) -> str:
    pn  = part.get("part_number", "?")
    mfr = part.get("manufacturer", "?")
    pkg = part.get("package", "")
    vin = part.get("input_voltage_max_v", "")
    vout = part.get("output_voltage_v", "")
    iout = part.get("output_current_a", "")
    grade = part.get("grade", "")
    lifecycle = part.get("lifecycle_status", "Active")
    total = score.get("total_score", "")

    lines = [
        f"型号：{pn}  厂商：{mfr}  封装：{pkg}",
        f"输入电压最大值：{vin}V  输出电压：{vout}V  输出电流：{iout}A",
        f"等级：{grade}  生命周期：{lifecycle}",
    ]
    if req_text:
        lines.append(f"适用场景：{req_text}")
    if total:
        lines.append(f"综合评分：{total}分（来源：{source}）")
    return "\n".join(filter(None, lines))
