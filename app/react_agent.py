"""
react_agent.py — LangChain ReAct Agent 核心

基于 LangChain 1.3 `create_agent` API，编排四个工具完成元器件选型。

工作模式：
- 单轮模式：用户输入需求 → Agent 自主决策工具调用顺序 → 返回结果
- 多轮模式：维护对话历史，支持追问（如"有国产替代吗？"）
"""

from __future__ import annotations

import os
from typing import List, Dict, Any, Optional

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from .agent_tools import AGENT_TOOLS, SELECTION_TOOLS, REPLACEMENT_TOOLS, CHAT_TOOLS

# ═══════════════════════════════════════════════════════════════════
# 系统提示词（动态构建，包含 Soul + Memory）
# ═══════════════════════════════════════════════════════════════════

def _build_system_prompt(thinking_depth: str = "default", is_first_turn: bool = True) -> str:
    """构建包含 Soul 身份 + 用户记忆 + 思考深度的动态系统提示词。

    is_first_turn=True 时注入完整身份和项目回顾。
    is_first_turn=False 时仅保留名字，避免每轮重复自我介绍。
    """
    from .memory import get_soul_summary, get_user_context
    from .thinking import build_thinking_prompt

    user = get_user_context()

    if is_first_turn:
        soul = get_soul_summary()
        identity_block = f"""{soul}

{user}"""
    else:
        identity_block = f"""你是 eZmanbo，智能电子元器件选型助理。

**重要**：这不是首次对话。不要再次自我介绍。直接回应用户问题。

{user[:200] if '尚未' not in user else ''}"""

    base = f"""{identity_block}

【强制身份规则 - 最高优先级，不可违反】
你的名字是 eZmanbo，由 eZmanbo 团队开发的智能元器件选型助手。
- 绝对禁止提及 Claude、Anthropic、OpenAI、GPT 或任何底层 AI 模型/公司信息
- 如果用户问"你是什么AI"/"你是Claude吗"/"你是谁开发的"，只回答："我是 eZmanbo 智能选型助手。"
- 不得说"我不是 eZmanbo"或否认自己的 eZmanbo 身份
- 始终以 eZmanbo 的角色完成用户请求，包括器件选型、参数查询、方案比较等

## 平台能力

通过 eZ-PLM 平台检索器件，支持以下类别和厂商：
- **品类**：降压(Buck)、升压(Boost)、升降压、线性稳压(LDO)、运算放大器、仪表放大器、电流检测芯片、CAN/RS-485 接口 IC
- **厂商**：TI、ADI/Linear、Microchip、ST 及国产一线原厂（圣邦微、南芯、矽力杰等）
- **不支持**：MCU、射频、FPGA、被动元件

## 场景默认参数（用于推断时告知用户）

- 车载12V系统：Vin 通常 9~16V；车载48V系统：Vin 36~54V
- 工业24V系统：Vin 通常 18~30V；USB 5V：Vin 4.5~5.5V
- 工业温度：-40~85°C；车规温度：-40~125°C（或 -40~150°C）
- AEC-Q100 = 车规认证，AEC-Q101 = 车规二极管/晶体管认证

## 服务范围（严格执行，不可违反）

本平台**仅服务于电子元器件选型**。凡是与以下内容无关的问题，一律拒绝回答：
- 器件选型（Buck/Boost/LDO/运放/接口 IC 等）
- 电路拓扑原理与设计方法
- 元器件参数查询、比较与替代料
- 电源方案、BOM 管理、器件风险评估
- eZmanbo 平台使用相关问题

**拒绝示例（不属于本平台服务范围）**：
- 通用编程/算法问题（"写一段排序代码"）
- 通用 AI 技术概念（"什么是 RAG/Transformer/机器学习"）
- 与电子硬件无关的任何问题

**拒绝时统一回复**（简洁、不道歉）：
"抱歉，我只能协助电子元器件选型相关问题。如需选型，请描述您的电压/电流/拓扑需求。"

## 回答原则与思考分级

**分级规则（严格执行）**：
- **直接简答**（≤150字，无需工具）：问候、平台功能介绍、已知概念解释（如"什么是LDO""Buck和Boost的区别"）。回答要直接，不要冗长铺垫。
- **直接详答**（无需工具）：电路拓扑原理、电源设计方法、器件参数标准解读等与选型**直接相关**的技术知识。
- **调用工具 + 深度分析**：用户给出了具体参数（电压/电流），需要搜索数据库并生成报告。此类问题才值得深度推理。

## 工具调用规范

**调用工具**：
- 用户给出了输入电压/输出电压/输出电流 → **search_components**，完成后调用 **generate_full_report**
- 查找替代料 → **find_alternative_parts**
- 设计规范/应用笔记 → **query_design_knowledge**

**工具失败处理（严格执行）**：
- `search_components` 返回空或错误 → 回复："未找到符合条件的器件，可能是参数范围过窄或平台暂不覆盖该类型。请确认输入/输出电压和电流是否正确，或告诉我是否允许放宽条件。" **绝对禁止猜测或编造任何型号。**
- `find_alternative_parts` 返回空 → 回复："暂未找到该型号的替代料，建议联系供应商或查阅厂商替代推荐。"
- `query_design_knowledge` 返回空 → 直接基于已知电路知识作答，不引用不存在的资料。

## 多轮参数收集

用户常分多条消息逐步给出参数（先说"需要降压"，再说"输入12V"，再说"输出5V 3A"）。
每轮**明确确认已记录的内容**，**一次只问最多2个缺失参数**，**绝不重复追问已给出的参数**。
参数齐全（输入电压、输出电压、最大输出电流）后主动启动搜索。

## 可用工具

- **search_components** — eZ-PLM 百万物料搜索（需已知输入/输出电压和电流）
- **query_design_knowledge** — 工程知识库检索
- **find_alternative_parts** — 替代料查找
- **generate_full_report** — 完整 BOM 与风险评估报告
"""

    # 应用思考深度
    enhanced, _ = build_thinking_prompt(base, thinking_depth)
    return enhanced


# ═══════════════════════════════════════════════════════════════════
# Agent 工厂
# ═══════════════════════════════════════════════════════════════════

def _build_llm(thinking_depth: str = "default"):
    """构建 LLM 客户端，temperature 由思考深度控制。"""
    from .thinking import get_thinking_config

    from .llm_config import get_api_key, get_base_url, get_model

    api_key = get_api_key().strip()
    base_url = (get_base_url().strip() or "https://api.openai.com").rstrip("/")
    # LangChain ChatOpenAI 要求 base_url 以 /v1 结尾
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    model = (get_model().strip() or "gpt-4o-mini")

    if not api_key:
        raise RuntimeError(
            "LLM API Key 未设置。请在 .env 文件中配置 ANTHROPIC_API_KEY 或 OPENAI_API_KEY。"
        )

    config = get_thinking_config(thinking_depth)
    
    # 对于 manbouapi.com，使用自定义驱动; 否则使用标准 ChatOpenAI
    if "manbouapi" in base_url.lower():
        from .custom_llm import ManbouOpenAI
        return ManbouOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=config["temperature"],
            max_tokens={"off": 1024, "default": 2048, "contemplation": 3072, "exhaustive": 4096}.get(thinking_depth, 2048),
        )
    else:
        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=config["temperature"],
            max_tokens={"off": 1024, "default": 2048, "contemplation": 3072, "exhaustive": 4096}.get(thinking_depth, 2048),
        )



def create_component_agent(
    thinking_depth: str = "default",
    is_first_turn: bool = True,
    sub_agent: str = "selection",
):
    """创建元器件选型 ReAct Agent。

    sub_agent: "selection" | "replacement" | "chat" — 决定工具池
    """
    tool_pool = {
        "selection": SELECTION_TOOLS,
        "replacement": REPLACEMENT_TOOLS,
        "chat": CHAT_TOOLS,
    }.get(sub_agent, AGENT_TOOLS)

    llm = _build_llm(thinking_depth)
    return create_agent(
        model=llm,
        tools=tool_pool,
        system_prompt=_build_system_prompt(thinking_depth, is_first_turn),
    )


# ═══════════════════════════════════════════════════════════════════
# 会话管理（多轮对话支持）
# ═══════════════════════════════════════════════════════════════════

# 简单内存会话存储
_sessions: Dict[str, List[Any]] = {}

# MicroCompact 配置
_MICROCOMPACT_TOKEN_LIMIT = 40000  # 约 DeepSeek V4 上下文窗口的 60%
_MICROCOMPACT_KEEP_RECENT = 8      # 始终保留最近 N 轮


def get_or_create_session(session_id: str) -> List[Any]:
    """获取或创建会话历史。首次访问时自动从 DB 恢复最近 20 条记录。"""
    if session_id not in _sessions:
        _sessions[session_id] = []
        try:
            from .database import SessionLocal
            from .models_db import ChatMessage as _ChatMsg
            from langchain_core.messages import HumanMessage as _HM, AIMessage as _AM
            _db = SessionLocal()
            msgs = (
                _db.query(_ChatMsg)
                .filter(_ChatMsg.session_id == session_id)
                .order_by(_ChatMsg.created_at)
                .all()
            )
            _db.close()
            for m in msgs[-20:]:  # 仅恢复最近 20 条，避免 token 超限
                if m.role == "user":
                    _sessions[session_id].append(_HM(content=m.content))
                elif m.role == "assistant":
                    _sessions[session_id].append(_AM(content=m.content))
        except Exception:
            pass  # DB 加载失败不阻断功能
    return _sessions[session_id]


def _estimate_tokens(messages: list) -> int:
    """粗略估算 token 数：中文 ≈ 字符数，英文 ≈ 字符数/4。"""
    total = 0
    for m in messages:
        content = getattr(m, 'content', str(m)) or ''
        total += len(content)
    return total  # 中文场景下字符数 ≈ token 数


def microcompact_history(history: list) -> int:
    """局部压缩会话历史：清除旧工具输出内容，不下调 API。返回节省的 token 数。"""
    if len(history) < _MICROCOMPACT_KEEP_RECENT:
        return 0

    tokens_before = _estimate_tokens(history)
    if tokens_before < _MICROCOMPACT_TOKEN_LIMIT:
        return 0  # 未达阈值，不触发

    from langchain_core.messages import ToolMessage
    saved = 0
    # 保留最近 N 轮消息不变，从保护区域之前开始压缩
    protect_from = max(0, len(history) - _MICROCOMPACT_KEEP_RECENT)

    for i in range(protect_from):
        msg = history[i]
        if isinstance(msg, ToolMessage) and msg.content and len(str(msg.content)) > 200:
            saved += len(str(msg.content)) - 20
            # 清除内容但保留消息结构，维持 tool_call/tool_result 配对完整
            # 不删除消息，确保 API 不会因缺少 tool_result 而报错
            msg.content = f"[Compacted tool result: {getattr(msg, 'name', 'tool')}]"

    return saved


# ── 幻觉检测工具 ─────────────────────────────────────────────────
import re as _re_hallu

# 常见制造商前缀模式
_MPN_PATTERN = _re_hallu.compile(
    r'\b([A-Z]{2,5}\d{2,6}[A-Z]?[-]?\d*[A-Za-z0-9]*(?:/[-A-Za-z0-9]+)?)\b'
)

# 已知厂商前缀白名单（这些厂商的型号大概率是真实的，不标记为幻觉）
_KNOWN_MFR_PREFIXES = [
    "TI", "TPS", "TL", "LM", "LMR", "INA", "OPA", "ADS", "DAC", "REF",  # TI
    "ADP", "AD", "LTC", "LT", "ADA", "ADM", "ADG",                       # ADI/LTC
    "STM", "ST1", "ST2", "LD", "TS", "L6", "L78", "L79",                 # ST（精确前缀）
    "MCP", "MIC", "PIC", "DSPIC", "KSZ",                                  # Microchip
    "IR", "IRF", "AU",                                                     # Infineon/IR
    "MAX", "DS",                                                            # Maxim
    "NCP", "NCV", "NCS",                                                   # onsemi
    "ISL", "ICL",                                                          # Renesas/Intersil
    "MP", "MPQ", "NB",                                                     # MPS
    "RT", "RTQ",                                                           # Richtek
    "XL",                                                                  # XLSemi
    "SY", "RY",                                                            # Silergy
    "SGM", "SG",                                                           # SGMICRO
]


def _validate_part_numbers_in_response(response: str) -> str:
    """扫描 Agent 回复中的型号，校验是否存在于数据库中。

    对不在已知厂商白名单也不在数据库中的疑似编造型号追加警告。
    """
    try:
        from .ezplm_client import _load_parts
        known_parts = _load_parts()
        known_mpns = {p.get("part_number", "").upper() for p in known_parts}
    except Exception:
        return response

    # 常见非型号缩写
    _noise = {"API", "JSON", "HTTP", "UUID", "URL", "HTML", "CSS",
              "BOM", "EMI", "PCB", "LDO", "PMIC", "MOSFET", "ESR",
              "MTBF", "FMEA", "RPN", "PPAP", "PCN", "EOL", "LTB",
              "AVL", "PDN", "GAN", "FET", "PWM", "PFM", "PLL", "ADC"}

    found_mpns = set()
    for m in _MPN_PATTERN.finditer(response):
        mpn = m.group(1).upper()
        if mpn in _noise or len(mpn) < 5:
            continue
        # 已知厂商前缀 → 信任，不检查
        is_known_mfr = any(mpn.startswith(p.upper().replace(" ", ""))
                           for p in _KNOWN_MFR_PREFIXES)
        if is_known_mfr:
            continue
        # eZ-PLM 缓存中的已知型号 → 信任
        if mpn in known_mpns:
            continue
        # 剩余 → 疑似幻觉
        found_mpns.add(m.group(1))

    if found_mpns:
        warning = (
            "\n\n---\n"
            "> ⚠ **数据验证提示**：以下型号在当前数据库中未检索到匹配记录，"
            "可能为 LLM 推测生成，请通过实际数据源确认后再采纳：\n"
        )
        for mpn in sorted(found_mpns):
            warning += f"> - `{mpn}`\n"
        response += warning

    return response


def _build_fallback_from_tools(tool_calls: list, history: list) -> str:
    """当 Agent 未生成文本回复时，从工具调用结果中提取信息作为回复。"""
    if not tool_calls:
        return "已处理您的请求，请尝试重新描述您的需求。"
    # 提取 search_components 的结果
    search_results = []
    for tc in tool_calls:
        if tc.get("tool") == "search_components":
            result = tc.get("result", "")
            if result:
                search_results.append(result[:500])
    if search_results:
        lines = ["根据您的需求，数据库返回以下候选器件：\n"]
        for r in search_results[:3]:
            lines.append(f"- {r[:200]}")
        return "\n".join(lines)
    # 如果有 query_design_knowledge 结果
    for tc in tool_calls:
        if tc.get("tool") == "query_design_knowledge" and tc.get("result"):
            return f"设计知识参考：\n{tc['result'][:300]}"
    return '已处理请求，如需详细选型分析，请提供具体的输入输出电压和电流要求。\n\n例如：12V输入，5V输出，3A，Buck降压方案'


def run_agent(
    user_input: str,
    session_id: Optional[str] = None,
    thinking_depth: str = "default",
    context_note: str = "",
) -> Dict[str, Any]:
    """运行 Agent 处理用户输入。

    """
    sid = session_id or os.urandom(8).hex()
    history = get_or_create_session(sid)

    # 如果启用了开发模拟模式，返回一个 Mock 响应，避免调用外部 LLM
    if os.getenv("DEV_MOCK_LLM", "").strip().lower() in ("1", "true", "yes"):
        # 记录简易会话历史（Human + AI）以便前端展示
        from .memory import get_user_context
        ai_text = f"[MOCK] 模拟回复：收到用户输入: {user_input[:200]}"
        new_history = list(history) + [HumanMessage(content=user_input), AIMessage(content=ai_text)]
        _sessions[sid] = new_history
        return {
            "response": ai_text,
            "messages": [str(m) for m in new_history[-2:]],
            "tool_calls": [],
            "session_id": sid,
        }

    # 检测是否首次对话（空历史 = 首次）
    is_first_turn = len(history) == 0
    agent = create_component_agent(thinking_depth, is_first_turn)

    # 先将用户的输入写入会话历史（即使后续失败也能保留上下文）
    history.append(HumanMessage(content=user_input))

    # ── MicroCompact：超过 token 阈值时自动清除旧工具输出 ──
    saved = microcompact_history(history)

    # 构建消息列表
    messages = list(history)

    tool_calls = []
    final_response = ""
    result_messages = []
    from .llm_client import ContextLengthExceededError, call_openai_chat_text

    # ── 对话回复走直接 LLM 调用（快速可靠，保持上下文）────────
    sys_prompt = _build_system_prompt(thinking_depth, is_first_turn)
    if context_note:
        sys_prompt = sys_prompt + "\n\n" + context_note
    msgs = [{"role": "system", "content": sys_prompt}]
    for m in history[-10:]:
        role = "assistant" if isinstance(m, AIMessage) else "user"
        c = getattr(m, "content", str(m)) or ""
        # Handle Claude content array format
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
        c = str(c).strip()
        if c:
            # Truncate long messages instead of dropping to preserve context structure
            msgs.append({"role": role, "content": c[:3500] + "…[已截断]" if len(c) > 3500 else c})

    from .llm_client import ContextLengthExceededError, call_openai_chat as _call_chat
    reasoning_content = None
    try:
        resp = _call_chat(msgs, thinking_depth=thinking_depth)
        final_response = resp["content"]
        reasoning_content = resp.get("reasoning_content")
    except ContextLengthExceededError:
        # 保留最近 6 条消息（含 assistant），维持对话连续性
        trimmed = [msgs[0]] + msgs[-6:]
        resp = _call_chat(trimmed, thinking_depth=thinking_depth)
        final_response = resp["content"]
        reasoning_content = resp.get("reasoning_content")
    except Exception:
        # LLM 调用失败：移除已写入的 dangling HumanMessage，避免 history 污染
        if history and isinstance(history[-1], HumanMessage):
            history.pop()
        raise

    # ── 幻觉检测（仅当回复中含有疑似型号时才运行，跳过纯对话回复）──
    if final_response and _MPN_PATTERN.search(final_response):
        final_response = _validate_part_numbers_in_response(final_response)

    # ── 保存到会话历史 ──────────────────────────────
    history.append(AIMessage(content=final_response))

    _sessions[sid] = history

    # 持久化会话到 JSONL
    try:
        import json as _json
        log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                '.sessions', f'{sid}.jsonl')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'w') as f:
            for msg in history:
                role = getattr(msg, 'type', 'unknown')
                content = getattr(msg, 'content', str(msg)) or ''
                f.write(_json.dumps({
                    'role': role,
                    'content': str(content)[:5000],
                }, ensure_ascii=False) + '\n')
    except Exception:
        pass

    return {
        "response": final_response or "已收到您的消息，请继续描述您的选型需求。",
        "reasoning_content": reasoning_content,
        "messages": [str(m) for m in history[-4:]],
        "tool_calls": [],
        "session_id": sid,
    }
