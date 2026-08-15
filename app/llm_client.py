import os
import json as _json
import requests
import time as _time
from typing import List, Dict, Any, Optional

from .llm_config import get_api_key, get_base_url, get_model


class ContextLengthExceededError(Exception):
    """上下文窗口超限异常，上层可捕获后触发压缩降级。"""
    pass

# ── Function Calling Tool Schema（P2：结构化需求提取）───────────

REQUIREMENT_TOOLS = [{
    "type": "function",
    "function": {
        "name": "extract_requirement",
        "description": "从自然语言中提取电子元器件选型的结构化需求参数",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["dc_dc_converter", "ldo", "mosfet", "op_amp", "interface_ic", "other"],
                    "description": "器件类别"
                },
                "topology": {
                    "type": "string",
                    "enum": ["buck", "boost", "buck_boost", "ldo", "other"],
                    "description": "电路拓扑，DC-DC 必填，LDO 填 ldo"
                },
                "application": {
                    "type": "string",
                    "description": "应用场景描述，如'车载电源''通信设备'"
                },
                "input_voltage_nominal_v": {
                    "type": "number",
                    "description": "标称输入电压 (V)"
                },
                "input_voltage_min_v": {
                    "type": "number",
                    "description": "最小输入电压 (V)"
                },
                "input_voltage_max_v": {
                    "type": "number",
                    "description": "最大输入电压 (V)"
                },
                "output_voltage_v": {
                    "type": "number",
                    "description": "输出电压 (V)"
                },
                "output_current_a": {
                    "type": "number",
                    "description": "输出电流 (A)，注意 mA 需转换为 A"
                },
                "temperature_min_c": {
                    "type": "number",
                    "description": "最低工作温度 (°C)"
                },
                "temperature_max_c": {
                    "type": "number",
                    "description": "最高工作温度 (°C)"
                },
                "grade": {
                    "type": "string",
                    "enum": ["automotive", "industrial", "commercial", "military"],
                    "description": "器件等级，注意'非车规''不要车规'意味着 industrial"
                },
                "package_preference": {
                    "type": "string",
                    "description": "封装偏好，如'SOT-23''QFN'"
                },
                "preferences": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "偏好列表，如 domestic_alternative, low_cost, high_efficiency, small_package"
                }
            },
            "required": ["category", "topology", "output_voltage_v", "output_current_a"]
        }
    }
}]


def call_openai_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.0,
    tools: Optional[List[dict]] = None,
    tool_choice: Optional[str] = None,
    thinking_depth: str = "default",
    model_override: Optional[str] = None,
    api_key_override: Optional[str] = None,
    base_url_override: Optional[str] = None,
) -> dict:
    """Call OpenAI-compatible /v1/chat/completions endpoint.

    支持 DeepSeek 思考模式：
    - thinking_depth="off"     → 显式关闭思考模式
    - thinking_depth="default" → 启用思考（reasoning_effort="high"）
    - thinking_depth="contemplation" → 同 default
    - thinking_depth="exhaustive"    → reasoning_effort="max"

    Returns: {"content": str, "tool_calls": list, "reasoning_content": Optional[str]}
    """
    api_key = api_key_override or get_api_key()
    if not api_key:
        raise RuntimeError("LLM API key not set in environment (checked ANTHROPIC_* then OPENAI_*)")
    model = model_override or model or get_model() or "gpt-3.5-turbo"
    base_url = base_url_override or get_base_url() or "https://api.openai.com"
    _base = base_url.rstrip("/")
    # Strip API endpoint suffixes users may accidentally include in the base URL
    for _sfx in ("/v1/messages", "/v1/chat/completions", "/v1/chat", "/v1/completions", "/messages"):
        if _base.endswith(_sfx):
            _base = _base[: -len(_sfx)]
            break
    url = (_base + "/chat/completions") if _base.endswith("/v1") else (_base + "/v1/chat/completions")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": 800,
    }

    # ── DeepSeek 思考模式处理 ────────────────────────────
    is_deepseek = model.startswith("deepseek")
    if is_deepseek:
        if thinking_depth == "off":
            payload["temperature"] = temperature
            payload["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            effort = "max" if thinking_depth == "exhaustive" else "high"
            payload["reasoning_effort"] = effort
            payload["extra_body"] = {"thinking": {"type": "enabled"}}
    else:
        payload["temperature"] = temperature

    if tools:
        payload["tools"] = tools
    if tool_choice and not is_deepseek:
        payload["tool_choice"] = tool_choice

    # ── 重试逻辑：应对 DeepSeek API 临时故障 / 限流 ──────────
    max_retries = 3
    last_exc = None
    # 思考模式下 API 响应可能较慢，放宽超时
    request_timeout = 300 if thinking_depth != "off" else 60
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=request_timeout)
            resp.raise_for_status()
            break  # 成功则跳出重试循环
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                # 仅对可重试错误重试：超时、429、5xx
                status = getattr(e.response if hasattr(e, 'response') else None, 'status_code', 0) if hasattr(e, 'response') else 0
                if status in (429, 500, 502, 503, 504) or isinstance(e, (requests.Timeout, ConnectionError)):
                    sleep_s = 1.5 ** (attempt + 1)  # 指数退避：1.5s, 2.25s, 3.375s
                    _time.sleep(sleep_s)
                    continue
            # 不可重试的错误或最后一次尝试失败时记录日志
            try:
                from .log_util import log_error
                body = resp.text if 'resp' in dir() and hasattr(resp, 'text') else str(e)
                log_error("llm_client", e, "call_openai_chat", extra={
                    "downstream_status": getattr(resp, 'status_code', 'unknown') if 'resp' in dir() else 'unknown',
                    "downstream_body": body[:2000],
                    "attempt": attempt + 1,
                })
            except Exception:
                pass
            # 检测上下文窗口超限，抛出专用异常供上层降级处理
            status_code = getattr(resp, 'status_code', 0) if 'resp' in dir() else 0
            error_body = body[:300] if 'body' in dir() else str(e)
            if isinstance(error_body, str) and any(kw in error_body.lower() for kw in (
                'context_length_exceeded', 'maximum context length',
                'too many tokens', 'prompt is too long', '413'
            )):
                raise ContextLengthExceededError(f"上下文超限: {error_body}") from e
            raise RuntimeError(f"Error code: {status_code} - {error_body}") from e
    data = resp.json()

    result: dict = {"content": "", "tool_calls": [], "reasoning_content": None}
    if "choices" in data and len(data["choices"]) > 0:
        choice = data["choices"][0]
        msg = choice.get("message", {})
        raw_content = msg.get("content")
        # Handle Claude-style content array (thinking + text blocks)
        if isinstance(raw_content, list):
            text_parts = [b.get("text", "") for b in raw_content
                          if isinstance(b, dict) and b.get("type") == "text"]
            think_parts = [b.get("thinking", "") for b in raw_content
                           if isinstance(b, dict) and b.get("type") in ("thinking", "reasoning_content")]
            content = "".join(filter(None, text_parts))
            thinking_from_blocks = "".join(filter(None, think_parts))
        else:
            content = raw_content or ""
            thinking_from_blocks = ""
        reasoning = msg.get("reasoning_content") or thinking_from_blocks or ""
        result["content"] = content or reasoning
        result["tool_calls"] = msg.get("tool_calls", [])
        result["reasoning_content"] = reasoning or None
    return result


def call_openai_chat_text(messages: List[Dict[str, str]], model: Optional[str] = None, temperature: float = 0.0, thinking_depth: str = "default") -> str:
    """向后兼容：返回纯文本。"""
    return call_openai_chat(messages, model, temperature, thinking_depth=thinking_depth)["content"]


def score_part_with_llm(
    requirement_text: str,
    part_info: Dict[str, Any],
    reference_designs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """用 LLM 对器件进行应用场景适配度和设计成熟度评分。"""
    pn = part_info.get("part_number", "Unknown")
    mfr = part_info.get("manufacturer") or "-"
    desc = part_info.get("description") or "-"
    vin = f"{part_info.get('vin_min', '?')}-{part_info.get('vin_max', '?')}V"
    iout = f"{part_info.get('iout_max', '?')}A"
    temp = f"{part_info.get('temp_min', '?')}-{part_info.get('temp_max', '?')}C"

    rd_lines = []
    for i, rd in enumerate(reference_designs[:3], 1):
        name = rd.get("name", "")
        desc_rd = (rd.get("description") or "").strip()[:300]
        rd_lines.append(f"{i}. [{name}] {desc_rd}")
    rd_text = "\n".join(rd_lines) if rd_lines else "(no reference designs)"

    system = (
        "You are a senior electronic engineer specializing in power IC component evaluation. "
        "Respond with JSON only, no extra text."
    )
    user = (
        f"## Requirement\n{requirement_text}\n\n"
        f"## Part\n"
        f"MPN: {pn} ({mfr})\n"
        f"Desc: {desc}\n"
        f"Params: Vin {vin}, Iout {iout}, Temp {temp}\n\n"
        f"## Reference Designs\n{rd_text}\n\n"
        "Score 0-100 for:\n"
        "1. application_score: design scenario match\n"
        "2. design_risk_score: reliability & maturity\n"
        'Return: {"application_score": 85, "design_risk_score": 78, "reasoning": "..."}'
    )
    try:
        resp = call_openai_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
        )
        content = resp["content"]
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            result = _json.loads(content[start:end + 1])
            for key in ("application_score", "design_risk_score"):
                if key in result:
                    result[key] = max(0.0, min(100.0, float(result[key])))
            return result
    except Exception as e:
        from .log_util import warn_swallow; warn_swallow("llm_client", e, "score_part")
    return {}


def parse_requirement_with_fc(text: str) -> Dict[str, Any]:
    """P2: 使用 DeepSeek Function Calling 进行结构化需求提取。

    替代原来的 JSON 字符串解析，强制 LLM 按 Tool Schema 输出结构化字段。
    减少解析错误（格式异常、字段遗漏、类型错误）。

    Returns:
        dict with keys matching RequirementConstraints fields.
    """
    try:
        resp = call_openai_chat(
            messages=[{
                "role": "system",
                "content": "你是一个电子元器件选型需求解析器。从用户输入中提取结构化参数。注意：mA 必须转为 A（除以1000），'非车规'意味着 industrial 等级。"
            }, {
                "role": "user",
                "content": text,
            }],
            tools=REQUIREMENT_TOOLS,
            tool_choice="required",
            temperature=0.0,
        )

        tool_calls = resp.get("tool_calls", [])
        if tool_calls:
            args = _json.loads(tool_calls[0]["function"]["arguments"])
            # 清理 null 值
            return {k: v for k, v in args.items() if v is not None}

        # Fallback: 尝试从 content 解析
        content = resp.get("content", "")
        if content:
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                return _json.loads(content[start:end + 1])

    except Exception as e:
        from .log_util import warn_swallow; warn_swallow("llm_client", e, "parse_fc")

    return {}


def parse_requirement_with_llm(text: str) -> Dict[str, Any]:
    """旧版 JSON 字符串解析（保留作为 fallback）。"""
    system = (
        "你是一个结构化信息提取助手。接收电子工程师的元器件选型需求，输出 JSON 格式的字段。\n"
        "请只输出 JSON，不要额外说明。字段示例：application, category, topology, input_voltage_nominal_v, output_voltage_v, output_current_a, temperature_min_c, temperature_max_c, grade, preferences (list)。"
    )
    user = f"解析以下需求为 JSON：\n{text}\n要求返回满足字段示例，只返回 JSON。"
    try:
        resp = call_openai_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        content = resp["content"]
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            return _json.loads(content[start:end + 1])
        return _json.loads(content)
    except Exception:
        return {"raw_llm": resp.get("content", "") if 'resp' in dir() else ""}

