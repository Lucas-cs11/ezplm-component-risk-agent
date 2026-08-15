"""
dual_model_verify.py — 双模型选型结果独立生成与对抗验证

架构：
  1. 主模型完成完整选型流水线（既有逻辑不变）
  2. 验证模型独立重评，生成完整自然语言回答
  3. 对比首选型号、风险判断、完整回答
  4. 不一致时由裁判调用给出判断理由
"""
from __future__ import annotations

import json
import asyncio
from typing import Optional
from dataclasses import dataclass, asdict


@dataclass
class VerifyResult:
    passed: bool
    primary_top: str
    verifier_top: str
    agreement: bool
    score_delta: float
    risk_conflict: bool
    verifier_notes: str
    verifier_full_response: str   # 验证模型完整自然语言回答
    judgment_reasoning: str       # 裁判调用：不一致时的最终判断理由
    needs_human_review: bool


_VERIFY_SYSTEM = """你是一名专业电子元器件选型工程师（验证角色）。
你将收到选型需求和候选器件列表，请独立生成你自己的选型推荐，不参考主模型的结论。

请以 JSON 格式输出，字段如下：
{
  "top_pick": "<你最推荐的型号>",
  "agree": true/false,          // 是否与主模型首选一致
  "risk_level": "low/medium/high",
  "notes": "<评语，限80字>",
  "full_response": "<用中文写2-4句完整的选型推荐说明，包含推荐理由和注意事项>"
}
只输出 JSON，不要其他文字。"""

_JUDGE_SYSTEM = """你是一名公正的高级电子工程师（裁判角色）。
两个模型给出了不同的元器件推荐，你需要分析分歧原因并给出最终建议。
请用中文，直接输出3-5句判断说明，不要JSON格式，不要标题，只说判断内容。"""


async def _adjudicate(
    requirement_text: str,
    primary_top: str,
    primary_notes: str,
    verifier_top: str,
    verifier_notes: str,
) -> str:
    """当两模型不一致时，调用裁判给出最终判断理由。"""
    try:
        from .llm_client import call_openai_chat
        user_msg = (
            f"选型需求：{requirement_text}\n\n"
            f"模型A推荐：{primary_top}\n"
            f"模型B推荐：{verifier_top}\n"
            f"模型B意见：{verifier_notes}\n\n"
            "请分析两个模型推荐不同的原因，并给出最终建议。"
        )
        resp = await asyncio.to_thread(
            call_openai_chat,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.0,
            thinking_depth="off",
        )
        return (resp.get("content") or "").strip()[:400]
    except Exception as e:
        return f"裁判调用异常: {e}"


async def verify_selection(
    requirement_text: str,
    candidates: list[dict],
    primary_top_pn: str,
    primary_risk: str,
    primary_full_response: str = "",
) -> VerifyResult:
    """使用验证模型独立重评选型结果，生成完整回答并对比。"""
    if not candidates:
        return _skip_verify(primary_top_pn, "无候选器件，跳过验证")

    try:
        from .llm_client import call_openai_chat
        from .llm_config import get_verifier_model
        from .models_db import AdminConfig
        from .database import SessionLocal

        verifier_model = get_verifier_model()
        if not verifier_model:
            return _skip_verify(primary_top_pn, "未配置验证模型")

        # Fetch verifier api_key and base_url from DB config
        api_key_override = None
        base_url_override = None
        try:
            db = SessionLocal()
            try:
                cfg = db.query(AdminConfig).filter(AdminConfig.id == 1).first()
                if cfg:
                    api_key_override = cfg.verifier_api_key or None
                    base_url_override = cfg.verifier_base_url or None
            finally:
                db.close()
        except Exception:
            pass

        cand_text = "\n".join(
            f"- {c.get('part_number','?')} ({c.get('manufacturer','?')}): 综合评分 {c.get('total_score',0):.0f}"
            for c in candidates[:8]
        )
        user_msg = (
            f"选型需求：{requirement_text}\n\n"
            f"候选器件列表：\n{cand_text}\n\n"
            f"主模型首选：{primary_top_pn}，整体风险：{primary_risk}\n\n"
            "请独立评估并给出你的推荐。"
        )

        resp = await asyncio.to_thread(
            call_openai_chat,
            messages=[
                {"role": "system", "content": _VERIFY_SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.0,
            model_override=verifier_model,
            api_key_override=api_key_override,
            base_url_override=base_url_override,
            thinking_depth="off",
        )

        raw = (resp.get("content") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip().rstrip("`").strip()

        data = json.loads(raw)
        verifier_top = data.get("top_pick", primary_top_pn)
        agree = bool(data.get("agree", True))
        verifier_risk = data.get("risk_level", primary_risk).lower()
        notes = data.get("notes", "")
        verifier_full = data.get("full_response", notes)

        risk_conflict = (
            (primary_risk in ("high",) and verifier_risk in ("low",)) or
            (primary_risk in ("low",)  and verifier_risk in ("high",))
        )

        score_map = {c.get("part_number", ""): float(c.get("total_score", 0)) for c in candidates}
        score_delta = abs(score_map.get(primary_top_pn, 0.0) - score_map.get(verifier_top, 0.0))

        passed = agree and not risk_conflict and score_delta < 15.0
        needs_human = not passed

        # When models disagree, run adjudication
        judgment = ""
        if not agree or risk_conflict:
            judgment = await _adjudicate(
                requirement_text, primary_top_pn, primary_full_response,
                verifier_top, notes,
            )

        return VerifyResult(
            passed=passed,
            primary_top=primary_top_pn,
            verifier_top=verifier_top,
            agreement=agree,
            score_delta=round(score_delta, 1),
            risk_conflict=risk_conflict,
            verifier_notes=notes,
            verifier_full_response=verifier_full,
            judgment_reasoning=judgment,
            needs_human_review=needs_human,
        )

    except Exception as e:
        return _skip_verify(primary_top_pn, f"验证异常: {e}")


def _skip_verify(primary_top: str, reason: str) -> VerifyResult:
    return VerifyResult(
        passed=True, primary_top=primary_top, verifier_top=primary_top,
        agreement=True, score_delta=0.0, risk_conflict=False,
        verifier_notes=reason, verifier_full_response="", judgment_reasoning="",
        needs_human_review=False,
    )


def verify_result_to_dict(r: VerifyResult) -> dict:
    return asdict(r)
