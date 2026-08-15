"""
ezplm_client.py — eZ-PLM 真实物料数据接入层

数据来源：eZ-PLM API v1（TI / ADI(含LTC) / Microchip / ST 白名单已开放）
Mock 数据已完全移除。所有器件查询通过 eZ-PLM API 进行。

架构：
  search_parts()              → 按需求约束搜索 eZ-PLM 真实器件
    ├── _generate_keywords()  → 从约束生成 MPN 前缀关键词列表
    ├── _search_keyword()     → 带 24h 缓存的单词关键词查询
    ├── _map_api_part()       → API 响应 → PartIR 统一中间表示
    └── _part_matches()       → 约束过滤（宽松策略）

  find_replacements()         → 搜索同类替代器件（不含原型号）
  fetch_reference_designs()   → 获取 eZ-PLM 参考设计

速率限制策略：
  - 内置 24h in-memory 缓存（同一关键词不重复 API 调用）
  - 关键词之间设最小延迟 _API_DELAY_S 避免 429
  - 优先使用缓存，每次 search 最多发出 _API_MAX_CALLS 个新请求

环境变量（在 .env 中配置）：
  EZPLM_API_KEY   必填：eZ-PLM API 密钥
  EZPLM_BASE_URL  可选：默认 https://www.ezplm.cn
"""

from __future__ import annotations

import json
import os
import re as _re
import time
import threading
import uuid
import hmac
import hashlib
import base64
import logging
import urllib.parse
import urllib.request
import urllib.error
from collections import OrderedDict
from typing import List, Dict, Any, Optional, Tuple

from .schemas import PartIR, RequirementConstraints

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# eZ-PLM API 配置
# ═══════════════════════════════════════════════════════════════════

_BASE_URL: str = os.getenv("EZPLM_BASE_URL", "https://www.ezplm.cn").rstrip("/")
_API_KEY: str = os.getenv("EZPLM_API_KEY", "").strip()

_API_MAX_PER_KW: int = 50      # 每关键词最多返回条数（pageSize）
_API_MAX_TOTAL: int = 80       # 单次 search_parts 最多累计匹配条数
_API_MAX_CALLS: int = 10       # 单次 search_parts 最多新 API 请求数（防超限）
_API_DELAY_S: float = 0.25    # 关键词请求间隔（秒），避免触发 429
_API_TIMEOUT_S: int = 20       # 单次 HTTP 超时


# P2-4: 令牌桶速率限制器（线程安全），取代 time.sleep(_API_DELAY_S)
class _TokenBucket:
    """Thread-safe token bucket: allows burst then enforces steady-state rate."""
    def __init__(self, rate: float, burst: int = 4):
        self._rate = rate          # tokens refilled per second
        self._burst = float(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
            else:
                wait = (1.0 - self._tokens) / self._rate
                self._tokens = 0.0
                time.sleep(wait)


_api_rate_limiter = _TokenBucket(rate=1.0 / _API_DELAY_S, burst=4)

# ═══════════════════════════════════════════════════════════════════
# 24h in-memory 关键词缓存（keyword → (expire_ts, [raw_dict, ...])）
# ═══════════════════════════════════════════════════════════════════

_KW_CACHE: OrderedDict[str, Tuple[float, List[Dict]]] = OrderedDict()
_KW_CACHE_TTL: float = 86400.0   # 24h，对齐 API 日重置周期
_KW_CACHE_MAX: int = 500         # LRU 容量上限

# 已知真实 MPN 集合（供 react_agent.py 幻觉检测使用）
# 使用 OrderedDict 作为有序集合，支持 LRU 淘汰
_KNOWN_MPNS: OrderedDict[str, bool] = OrderedDict()
_KNOWN_MPNS_MAX: int = 10000     # LRU 容量上限


def _load_parts() -> List[dict]:
    """
    兼容旧接口（react_agent.py 幻觉检测用）。
    返回 in-memory 缓存中累积的真实 API 数据（以 dict 格式），
    无 Mock 数据。首次调用若缓存为空则返回 []。
    """
    result: List[dict] = []
    now = time.time()
    for _kw, (exp, items) in list(_KW_CACHE.items()):
        if now < exp:
            for item in items:
                pn = (item.get("mpn") or item.get("partNumber") or "")
                if pn:
                    result.append({"part_number": pn})
    return result


# ═══════════════════════════════════════════════════════════════════
# 国内品牌识别
# ═══════════════════════════════════════════════════════════════════

_DOMESTIC_MANUFACTURERS: frozenset = frozenset({
    "立锜", "圣邦", "南芯", "华润", "矽力杰", "思瑞浦", "芯朋", "英集芯",
    "纳芯微", "杰华特", "芯源系统", "美芯晟", "晶丰明源", "上海贝岭",
    "扬杰", "台基半导体", "宏微", "先芯", "必易微", "比亚迪半导体",
})


# ═══════════════════════════════════════════════════════════════════
# MPN 前缀推断：拓扑 / 类别 / 固定输出电压
# ═══════════════════════════════════════════════════════════════════

# 按前缀长度降序排列，确保长前缀优先匹配
_MPN_TOPO_MAP: List[Tuple[str, str]] = sorted([
    # TI — Buck
    ("TPS568", "buck"), ("TPS563", "buck"), ("TPS560", "buck"),
    ("TPS628", "buck"), ("TPS626", "buck"), ("TPS624", "buck"),
    ("TPS629", "buck"), ("TPS549", "buck"), ("TPS548", "buck"),
    ("TPS543", "buck"), ("TPS542", "buck"), ("TPS541", "buck"),
    ("TPS540", "buck"), ("TPS538", "buck"), ("TPS536", "buck"),
    ("TPS534", "buck"), ("TPS532", "buck"), ("TPS530", "buck"),
    ("TPS54", "buck"), ("TPS62", "buck"), ("TPS56", "buck"),
    ("LM2596", "buck"), ("LM2576", "buck"), ("LM2675", "buck"),
    ("LMR14", "buck"), ("LMR23", "buck"), ("LMR36", "buck"),
    ("LMR33", "buck"), ("LMR16", "buck"), ("LMR50", "buck"),
    # TI — Boost
    ("TPS61", "boost"), ("TPS780", "boost"),
    # TI — Buck-Boost
    ("TPS63", "buck_boost"), ("TPS65", "buck_boost"),
    # TI — LDO
    ("TPS7A47", "ldo"), ("TPS7A39", "ldo"), ("TPS7A38", "ldo"),
    ("TPS7A30", "ldo"), ("TPS7933", "ldo"), ("TPS799", "ldo"),
    ("TPS798", "ldo"), ("TPS797", "ldo"), ("TPS796", "ldo"),
    ("TPS795", "ldo"), ("TPS793", "ldo"), ("TPS792", "ldo"),
    ("TPS79", "ldo"), ("TPS72", "ldo"),
    ("TLV758", "ldo"), ("TLV756", "ldo"), ("TLV755", "ldo"),
    ("TLV754", "ldo"), ("TLV752", "ldo"), ("TLV750", "ldo"),
    ("TLV76", "ldo"), ("TLV75", "ldo"),
    ("LP5907", "ldo"), ("LP5912", "ldo"), ("LP5951", "ldo"),
    ("LP38501", "ldo"), ("LP38502", "ldo"),
    # ADI / LTC — Buck
    ("LTC3833", "buck"), ("LTC3838", "buck"), ("LTC3855", "buck"),
    ("LTC3871", "buck"), ("LTC7106", "buck"), ("LTC7816", "buck"),
    ("LTC3774", "buck"), ("LTC3773", "buck"), ("LTC3765", "buck"),
    ("LTC3782", "buck"),
    ("LTC38", "buck"), ("LTC37", "buck"), ("LTC7", "buck"),
    ("ADP2302", "buck"), ("ADP2303", "buck"), ("ADP2384", "buck"),
    ("ADP2370", "buck"), ("ADP2380", "buck"),
    ("ADP238", "buck"), ("ADP239", "buck"), ("ADP235", "buck"),
    ("ADP23", "buck"),
    # ADI / LTC — Boost
    ("LTC3426", "boost"), ("LTC3780", "boost"),
    ("LTC36", "boost"),
    # ADI — LDO
    ("ADP3120", "ldo"), ("ADP1714", "ldo"), ("ADP150", "ldo"),
    ("ADP312", "ldo"), ("ADP302", "ldo"), ("ADP125", "ldo"),
    ("LT1763", "ldo"), ("LT1965", "ldo"), ("LT3080", "ldo"),
    ("LT3083", "ldo"),
    # Microchip — Buck
    ("MCP16301", "buck"), ("MCP16311", "buck"), ("MCP16323", "buck"),
    ("MCP16331", "buck"), ("MCP1612", "buck"), ("MCP1613", "buck"),
    ("MCP16", "buck"),
    # Microchip — Boost
    ("MCP1640", "boost"), ("MCP1661", "boost"),
    # Microchip — LDO
    ("MCP1703", "ldo"), ("MCP1700", "ldo"), ("MCP1501", "ldo"),
    # ST — Buck
    ("ST1S10", "buck"), ("ST1S12", "buck"), ("ST1S14", "buck"),
    ("ST1S", "buck"),
    ("L5970", "buck"), ("L5972", "buck"), ("L5973", "buck"),
    ("L5974", "buck"), ("L6902", "buck"), ("L6952", "buck"),
    # ST — LDO
    ("LD1117", "ldo"), ("LD3985", "ldo"), ("LD3990", "ldo"),
    ("LD39020", "ldo"), ("LD39", "ldo"),
    ("L7805", "ldo"), ("L7808", "ldo"), ("L7812", "ldo"),
    ("L7815", "ldo"), ("L7824", "ldo"), ("L78", "ldo"),
    ("LDL112", "ldo"), ("LDL115", "ldo"), ("LDBL02", "ldo"),
], key=lambda x: -len(x[0]))   # 长前缀优先

_MPN_CAT_MAP: List[Tuple[str, str]] = sorted([
    # Op-Amps
    ("OPA", "op_amp"), ("OPA2", "op_amp"), ("OPA4", "op_amp"),
    ("AD8", "op_amp"), ("ADA", "op_amp"), ("AD623", "op_amp"),
    ("MCP60", "op_amp"),
    # Instrumentation Amps
    ("INA12", "instrumentation_amp"), ("INA82", "instrumentation_amp"),
    ("AD823", "instrumentation_amp"), ("LT116", "instrumentation_amp"),
    # Current Sense
    ("INA2", "current_sense"),
    # Interface
    ("SN65", "interface_ic"), ("SN75", "interface_ic"),
    ("ADM4", "interface_ic"), ("ADM2", "interface_ic"),
    # DC-DC
    ("TPS5", "dc_dc_converter"), ("TPS6", "dc_dc_converter"),
    ("LM259", "dc_dc_converter"), ("LM257", "dc_dc_converter"),
    ("LMR", "dc_dc_converter"),
    ("LTC3", "dc_dc_converter"), ("LTC7", "dc_dc_converter"),
    ("ADP23", "dc_dc_converter"), ("ADP53", "dc_dc_converter"),
    ("MCP16", "dc_dc_converter"), ("MCP1640", "dc_dc_converter"),
    ("ST1S", "dc_dc_converter"), ("L597", "dc_dc_converter"),
    ("L690", "dc_dc_converter"), ("L695", "dc_dc_converter"),
    # LDO
    ("TPS79", "ldo"), ("TPS72", "ldo"), ("TPS7A", "ldo"),
    ("TLV75", "ldo"), ("TLV76", "ldo"),
    ("LP590", "ldo"), ("LP38", "ldo"),
    ("LT17", "ldo"), ("LT19", "ldo"), ("LT30", "ldo"),
    ("ADP31", "ldo"), ("ADP30", "ldo"), ("ADP15", "ldo"), ("ADP12", "ldo"),
    ("MCP17", "ldo"), ("MCP1501", "ldo"),
    ("LD111", "ldo"), ("LD39", "ldo"), ("L78", "ldo"), ("LDL", "ldo"),
    ("LDBL", "ldo"),
], key=lambda x: -len(x[0]))


def _infer_topology_from_mpn(pn: str) -> Optional[str]:
    """从型号前缀推断拓扑（长前缀优先）。"""
    pn_up = pn.upper()
    for prefix, topo in _MPN_TOPO_MAP:
        if pn_up.startswith(prefix.upper()):
            return topo
    return None


def _infer_category_from_mpn(pn: str) -> Optional[str]:
    """从型号前缀推断器件类别（长前缀优先）。"""
    pn_up = pn.upper()
    for prefix, cat in _MPN_CAT_MAP:
        if pn_up.startswith(prefix.upper()):
            return cat
    return None


def _infer_output_voltage_from_mpn(pn: str) -> Optional[float]:
    """
    从型号中推断固定输出电压。
    支持：LM2596S-5.0 → 5.0V，L7805 → 5.0V，LD1117-3.3 → 3.3V
    ADJ 可调型返回 None。
    """
    if not pn:
        return None
    pn_up = pn.upper()
    if "ADJ" in pn_up or "ADJF" in pn_up:
        return None
    # 模式一："-X.X" 或 "-XX" 或 "/X.X" 后缀（如 LM2596S-5.0, LD1117-3.3）
    m = _re.search(r"[-/](\d+(?:\.\d+)?)[Vv]?(?:$|[-/\s_])", pn)
    if m:
        try:
            v = float(m.group(1))
            if 0.5 <= v <= 60:
                return v
        except ValueError:
            pass
    # 模式二：L78XX 系列（如 L7805 → 5V, L7812 → 12V）
    m2 = _re.match(r"L[79]\d(\d{2})[A-Za-z]?", pn_up)
    if m2:
        try:
            v = float(m2.group(1))
            if 0.5 <= v <= 60:
                return v
        except ValueError:
            pass
    return None


# ═══════════════════════════════════════════════════════════════════
# eZ-PLM API 搜索关键词表
# 按 category/topology 分组，覆盖 TI / ADI(LTC) / Microchip / ST
# ═══════════════════════════════════════════════════════════════════

_API_KEYWORDS: Dict[str, Dict[Optional[str], List[str]]] = {
    "dc_dc_converter": {
        "buck": [
            # TI
            "TPS54", "TPS62", "LM2596", "LM2576", "LMR14", "LMR23",
            "LMR36", "TPS560", "TPS563", "TPS568",
            # ADI / LTC
            "LTC3833", "LTC3855", "LTC3871", "LTC7106",
            "ADP2302", "ADP2384",
            # Microchip
            "MCP16301", "MCP16311", "MCP1612",
            # ST
            "ST1S10", "ST1S12", "L5970", "L5973", "L6902",
        ],
        "boost": [
            # TI
            "TPS61", "TPS63",
            # ADI / LTC
            "LTC3426", "LTC3780",
            # Microchip
            "MCP1640", "MCP1661",
        ],
        "buck_boost": [
            "TPS63", "LTC3780",
        ],
        "ldo": [
            # TI
            "TPS79", "TPS72", "TLV750", "TLV755", "TLV756", "TLV758",
            "TPS7A47", "LP5907", "LP5912",
            # ADI / LTC
            "ADP3120", "ADP150", "LT1763", "LT1965", "LT3080",
            # Microchip
            "MCP1703", "MCP1700", "MCP1501",
            # ST
            "LD1117", "LD3985", "LD39020", "L7805", "L7812", "LDL112",
        ],
        None: [
            # 通用：TI + ADI + Microchip + ST 主流前缀
            "TPS54", "TPS62", "LM2596", "LMR14", "LMR23",
            "LTC3833", "LTC3855", "LTC7106", "ADP2302",
            "MCP16301", "MCP1612",
            "ST1S10", "L5970",
        ],
    },
    "ldo": {
        None: [
            # TI
            "TPS79", "TPS72", "TLV750", "TLV755", "TLV756", "TLV758",
            "TPS7A47", "TPS7A39", "LP5907", "LP5912", "LP38501",
            # ADI / LTC
            "ADP3120", "ADP1714", "ADP150", "ADP125",
            "LT1763", "LT1965", "LT3080", "LT3083",
            # Microchip
            "MCP1703", "MCP1700", "MCP1501",
            # ST
            "LD1117", "LD3985", "LD3990", "LD39020",
            "L7805", "L7808", "L7812", "L7815",
            "LDL112", "LDL115", "LDBL02",
        ],
        "ldo": [
            "TPS79", "TPS72", "TLV750", "TLV755", "TLV758",
            "TPS7A47", "LP5907",
            "ADP3120", "ADP150", "LT1763", "LT1965", "LT3080",
            "MCP1703", "MCP1700", "MCP1501",
            "LD1117", "LD39020", "L7805", "L7812", "LDL112",
        ],
    },
    # ── 扩展分类（运放 / 电流检测 / 接口IC）─────────────────────
    "op_amp": {
        None: [
            "OPA", "OPA2", "OPA4",               # TI 通用/精密运放
            "AD8", "ADA", "AD623", "AD620",       # ADI 运放
            "LT606", "LT607",                     # ADI/LTC
            "MCP600", "MCP601", "MCP602", "MCP604", # Microchip
        ]
    },
    "instrumentation_amp": {
        None: [
            "INA128", "INA129", "INA821", "INA826",  # TI 精密仪表放大器
            "AD8221", "AD8237", "AD620", "AD623",     # ADI
            "LT1167", "LT1168",                       # ADI/LTC
        ]
    },
    "current_sense": {
        None: [
            "INA219", "INA220", "INA226", "INA228", "INA230",  # TI 功率/电流监控
            "INA2",                                             # TI INA2xx 电流检测
        ]
    },
    "interface_ic": {
        None: [
            "SN65HVD",                    # TI CAN 收发器
            "SN75176", "SN75ALS",         # TI RS-422/485
            "ADM485", "ADM2587", "ADM",   # ADI RS-485
        ]
    },
}

# 厂商前缀白名单（用于厂商偏好过滤）
_MFR_PREFIXES: Dict[str, List[str]] = {
    "ti":        ["TPS", "LM25", "LM26", "LMR", "TLV", "OPA", "INA", "REF", "LP58",
                  "LP59", "LP38", "UCC"],
    "adi":       ["LTC", "LT1", "LT3", "LT6", "LT7", "AD", "ADA", "ADP"],
    "microchip": ["MCP"],
    "st":        ["ST1", "L57", "L59", "L69", "L78", "LD1", "LD3", "LDL", "LDBL",
                  "VN", "STM"],
}

_MFR_ALIASES: Dict[str, str] = {
    "texas instruments": "ti", "德州仪器": "ti", "ti": "ti",
    "analog devices": "adi", "analogdevices": "adi", "adi": "adi",
    "ltc": "adi", "linear technology": "adi",
    "microchip": "microchip", "mcp": "microchip",
    "stmicroelectronics": "st", "st": "st", "stm": "st", "stmicro": "st",
}


# ═══════════════════════════════════════════════════════════════════
# HMAC-SHA256 签名（对齐 API 文档）
# ═══════════════════════════════════════════════════════════════════

def _canonical_query(params: Dict[str, Any]) -> str:
    """构造签名用的 canonical query 字符串（过滤空值，字典序排列）。"""
    items = [
        (str(k), str(v)) for k, v in params.items()
        if v is not None and v != ""
    ]
    items.sort(key=lambda x: (x[0], x[1]))
    return "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in items
    )


def _get_api_key() -> str:
    """动态读取 API 密钥：优先 env var，其次 DB（支持运行时管理员配置生效）。"""
    key = os.getenv("EZPLM_API_KEY", "").strip()
    if key:
        return key
    try:
        from .database import SessionLocal
        from .models_db import AdminConfig
        _db = SessionLocal()
        cfg = _db.query(AdminConfig).first()
        _db.close()
        return (cfg.ezplm_api_key or "").strip() if cfg else ""
    except Exception:
        return ""


def _build_signature(method: str, path: str, params: Dict[str, Any],
                     timestamp: str, nonce: str) -> str:
    """计算 HMAC-SHA256 签名，base64url 编码，去掉末尾 =。"""
    api_key = _get_api_key()
    canonical = "\n".join([
        method.upper(), path, _canonical_query(params), timestamp, nonce
    ])
    digest = hmac.new(
        api_key.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _request_json(path: str, params: Dict[str, Any]) -> Tuple[int, Dict]:
    """发起一次 eZ-PLM API GET 请求（含签名），返回 (status_code, body_dict)。"""
    api_key = _get_api_key()
    if not api_key:
        return 0, {"error": "EZPLM_API_KEY 未配置"}
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    signature = _build_signature("GET", path, params, timestamp, nonce)
    query_str = _canonical_query(params)
    url = _BASE_URL + path + (f"?{query_str}" if query_str else "")
    req = urllib.request.Request(url, method="GET", headers={
        "X-API-Key":   api_key,
        "X-Timestamp": timestamp,
        "X-Nonce":     nonce,
        "X-Signature": signature,
    })
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT_S) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw[:200]}
        return exc.code, body
    except Exception as e:
        return 0, {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# 带缓存的关键词搜索
# ═══════════════════════════════════════════════════════════════════

def _is_kw_cached(keyword: str) -> bool:
    """检查关键词是否有有效缓存（不发出 API 请求）。"""
    cached = _KW_CACHE.get(keyword)
    return bool(cached and time.time() < cached[0])


def _search_keyword(keyword: str) -> Tuple[List[Dict], bool]:
    """
    搜索单个关键词，优先返回 24h 缓存。
    返回 (results_list, from_cache)。
    """
    now = time.time()
    cached = _KW_CACHE.get(keyword)
    if cached:
        exp, items = cached
        if now < exp:
            return items, True

    status, body = _request_json(
        "/api/v1/api-key/parts",
        {"keyword": keyword, "pageSize": str(_API_MAX_PER_KW)},
    )
    if status == 200:
        results: List[Dict] = body.get("data") or []
        _KW_CACHE[keyword] = (now + _KW_CACHE_TTL, results)
        if len(_KW_CACHE) > _KW_CACHE_MAX:
            _KW_CACHE.popitem(last=False)  # LRU: 淘汰最旧条目
        return results, False
    elif status == 429:
        logger.warning(
            "eZ-PLM API 日调用次数已达上限（429）。将使用已有缓存继续。"
        )
        # 429 时若有旧缓存（已过期），仍可降级使用
        if cached:
            return cached[1], True
        return [], False
    else:
        logger.debug(f"eZ-PLM keyword='{keyword}' HTTP {status}: {body}")
        return [], False


# ═══════════════════════════════════════════════════════════════════
# eZ-PLM attributes 数组解析（双语字段名：TI/ADI/Microchip/ST 通用）
# ═══════════════════════════════════════════════════════════════════

# 匹配规则：(name 判断函数, target_field)
# 注：封装字段直接存字符串，数值字段提取第一个数字
_ATTR_NUM_RULES: List[Tuple[Any, str]] = [
    # ── 输入电压最大 ──
    (lambda n: ("输入电压" in n and ("最大" in n or "Max" in n))
               or n in ("Input Voltage (Max)", "VIN(Max)", "Vin_max"),
     "input_voltage_max_v"),
    # ── 输入电压最小 ──
    (lambda n: ("输入电压" in n and ("最小" in n or "Min" in n))
               or n in ("Input Voltage (Min)", "VIN(Min)", "Vin_min"),
     "input_voltage_min_v"),
    # ── 输入电压（仅标注"输入电压"，无最大/最小）→ 当额定电压存入 max ──
    (lambda n: n in ("输入电压", "Input Voltage", "VIN", "Vin"),
     "input_voltage_max_v"),
    # ── 输出电压 ──
    (lambda n: "输出电压" in n or "Output Voltage" in n
               or "VOUT" in n or "Vout" in n,
     "output_voltage_v"),
    # ── 输出电流最大 ──
    (lambda n: ("输出电流" in n or "Output Current" in n or "IOUT" in n)
               and ("最大" in n or "Max" in n or n.endswith("(A)")),
     "output_current_max_a"),
    # ── 输出电流（无修饰词） ──
    (lambda n: n in ("输出电流", "Output Current", "IOUT", "Iout"),
     "output_current_max_a"),
    # ── 开关频率 ──（v2 新增）
    (lambda n: ("开关频率" in n or "频率" in n
               or "Switching Frequency" in n or "Frequency" in n
               or n in ("FSW", "Fsw", "F_SW", "SW Freq")),
     "switching_frequency_khz"),
    # ── 静态电流 ──（v2 新增）
    (lambda n: ("静态电流" in n or "Quiescent" in n or "Iq" in n
               or n in ("IQ", "Iq(typ)", "Shutdown Current", "关断电流")),
     "quiescent_current_ua"),
    # ── 效率 ──（v2 新增；提取第一个数字，单位 %）
    (lambda n: ("效率" in n or "Efficiency" in n
               or n in ("Peak Efficiency", "Typical Efficiency", "η")),
     "efficiency_pct"),
]


def _extract_features_from_desc(desc: str) -> List[str]:
    """
    从器件描述文本中提取结构化特性标签。
    返回字符串列表，如 ["AEC-Q100", "Sync", "SS", "WETTABLE"]。
    """
    if not desc:
        return []
    features: List[str] = []
    d = desc.upper()
    # 车规认证
    if _re.search(r"AEC[-–]?Q100", d):
        features.append("AEC-Q100")
    if _re.search(r"AEC[-–]?Q101", d):
        features.append("AEC-Q101")
    if "WETTABLE" in d or "WETTABLE FLANKS" in d:
        features.append("WETTABLE_FLANKS")
    # 同步整流
    if "SYNC" in d or "SYNCHRONOUS" in d:
        features.append("SYNC")
    # 软启动
    if "SOFT START" in d or "SOFT-START" in d or " SS " in d:
        features.append("SS")
    # 内置开关
    if "INTEGRATED SWITCH" in d or "INTERNAL SWITCH" in d or "INTEGRATED FET" in d:
        features.append("INT_SW")
    # 强制 PWM / FPWM
    if "FORCED PWM" in d or "FPWM" in d:
        features.append("FPWM")
    # 超低噪声（LDO）
    if "ULTRA-LOW NOISE" in d or "LOW NOISE" in d or "ULTRALOW NOISE" in d:
        features.append("LOW_NOISE")
    # PSRR
    m = _re.search(r"PSRR[^\d]*(\d+)\s*DB", d)
    if m:
        features.append(f"PSRR_{m.group(1)}dB")
    # 使能引脚
    if "ENABLE" in d or "EN PIN" in d:
        features.append("EN_PIN")
    # 过温保护
    if "THERMAL SHUTDOWN" in d or "TSD" in d or "OTP" in d:
        features.append("TSD")
    # 过流保护
    if "OVERCURRENT" in d or "OCP" in d or "CURRENT LIMIT" in d:
        features.append("OCP")
    return features


def _parse_attrs(attrs: List[Dict]) -> Dict[str, Any]:
    """
    解析 eZ-PLM attributes 数组，提取电气参数。
    对于四大厂商（TI/ADI/Microchip/ST）均适用。

    v2 新增字段：switching_frequency_khz / quiescent_current_ua / efficiency_pct
    """
    result: Dict[str, Any] = {}
    for a in (attrs or []):
        name: str = str(a.get("name", "")).strip()
        raw:  str = str(a.get("value", "")).strip()
        if not raw or raw.lower() in ("none", "null", "-", "n/a", "—", "tbd"):
            continue

        # ── 数值字段匹配 ──────────────────────────────────────────
        for matcher, field in _ATTR_NUM_RULES:
            if matcher(name) and field not in result:
                num_m = _re.search(r"(-?\d+(?:\.\d+)?)", raw)
                if num_m:
                    try:
                        val = float(num_m.group(1))
                        # 单位换算：频率可能是 MHz → 转 kHz
                        if field == "switching_frequency_khz":
                            # 尝试提取带单位的精确值（支持范围，取最大值）
                            # 例："100 kHz ~ 2.5 MHz" → 最大值 2500 kHz
                            mhz_pairs = _re.findall(
                                r"(\d+(?:\.\d+)?)\s*(?:MHz|MHZ|mhz)", raw
                            )
                            khz_pairs = _re.findall(
                                r"(\d+(?:\.\d+)?)\s*(?:kHz|KHZ|khz)", raw
                            )
                            if mhz_pairs:
                                # 取最大 MHz 值转为 kHz
                                val = max(float(x) for x in mhz_pairs) * 1000.0
                            elif khz_pairs:
                                val = max(float(x) for x in khz_pairs)
                            else:
                                # 没有单位标注时：直接用数字，若 <500 可能是 kHz
                                pass  # val 已由 num_m 设置
                            # 合理性检查：10 kHz ~ 10 MHz
                            if not (10 <= val <= 10_000):
                                break
                        # 静态电流：可能是 mA → 转 μA
                        elif field == "quiescent_current_ua":
                            raw_up = raw.upper()
                            if "MA" in raw_up:
                                val = val * 1000.0
                            elif "A" in raw_up and "MA" not in raw_up and "UA" not in raw_up and "µA" not in raw_up:
                                val = val * 1_000_000.0
                            # 合理性检查：0.01 ~ 50000 μA
                            if not (0.01 <= val <= 50_000):
                                break
                        # 效率：合理性检查 50%~100%
                        elif field == "efficiency_pct":
                            if not (50.0 <= val <= 100.0):
                                break
                        result[field] = val
                    except ValueError:
                        pass
                break

        # ── 温度范围 ──────────────────────────────────────────────
        if (("温度" in name or "Temperature" in name or "Temp" in name)
                and ("范围" in name or "Range" in name
                     or "工作" in name or "Operating" in name
                     or "Junction" in name or "Storage" in name)):
            # 尝试 "TMin to TMax" 格式
            m = _re.search(
                r"(-?\d+(?:\.\d+)?)\s*(?:to|To|~|至|～|…|,)\s*(-?\d+(?:\.\d+)?)",
                raw,
            )
            if m:
                result["temperature_min_c"] = float(m.group(1))
                result["temperature_max_c"] = float(m.group(2))
            else:
                # 单值（上限）
                num_m = _re.search(r"(-?\d+(?:\.\d+)?)", raw)
                if num_m:
                    v = float(num_m.group(1))
                    if v <= 0:
                        result.setdefault("temperature_min_c", v)
                    else:
                        result.setdefault("temperature_max_c", v)

        # ── 封装 ──────────────────────────────────────────────────
        if ("封装" in name or name.lower() in ("package", "package type",
                                                "case/package", "case package",
                                                "footprint")):
            result["package"] = raw

        # ── 拓扑类型 ─────────────────────────────────────────────
        if ("拓扑" in name or "Topology" in name
                or "Converter Type" in name or "Type" in name):
            _topo_map = {
                "buck": "buck", "step-down": "buck", "step down": "buck", "降压": "buck",
                "boost": "boost", "step-up": "boost", "step up": "boost", "升压": "boost",
                "buck/boost": "buck_boost", "buck boost": "buck_boost",
                "inverting": "buck_boost",
                "ldo": "ldo", "linear": "ldo", "线性": "ldo",
                "low dropout": "ldo", "low-dropout": "ldo",
            }
            vl = raw.lower()
            for k, v in _topo_map.items():
                if k in vl:
                    result.setdefault("topology", v)
                    break

    return result


# ═══════════════════════════════════════════════════════════════════
# eZ-PLM 原始响应 → PartIR
# ═══════════════════════════════════════════════════════════════════

def _extract_package_name(val: Any) -> Optional[str]:
    """Extract a string package name from a dict (eZ-PLM structured attr) or raw string."""
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip() or None
    if isinstance(val, dict):
        # eZ-PLM returns package as {'id': '...', 'name': 'PWP0028D.step'}
        name = val.get("name") or val.get("label") or val.get("value")
        return (str(name).strip()) if name else None
    return str(val).strip() or None


def _map_api_part(api_obj: Dict) -> Optional[PartIR]:
    """
    将 eZ-PLM API 返回的单条物料 dict 映射为 PartIR。

    实际 API 字段：
      mpn, manufacturer{name,id}, category{name,id},
      attributes[], pdf{url}, id, footprint, symbol, description
    """
    if not isinstance(api_obj, dict):
        return None
    try:
        # ── 型号 ──
        part_number = (
            api_obj.get("mpn")
            or api_obj.get("partNumber")
            or api_obj.get("part_number")
        )
        if not part_number:
            return None
        part_number = str(part_number).strip()

        # ── 厂商 ──
        mfr_raw = api_obj.get("manufacturer") or {}
        if isinstance(mfr_raw, dict):
            manufacturer: Optional[str] = mfr_raw.get("name") or mfr_raw.get("id")
        else:
            manufacturer = str(mfr_raw).strip() or None

        # ── 类别（MPN 推断优先 > API 返回） ──
        category = _infer_category_from_mpn(part_number)
        if not category:
            cat_raw = api_obj.get("category") or {}
            cat_name = (cat_raw.get("name", "") if isinstance(cat_raw, dict) else str(cat_raw))
            for kw, cat in {
                "DC-DC": "dc_dc_converter", "电源管理": "dc_dc_converter",
                "PMIC": "dc_dc_converter", "Buck": "dc_dc_converter",
                "Boost": "dc_dc_converter", "LDO": "ldo", "线性": "ldo",
                "Linear Regulator": "ldo",
            }.items():
                if kw in cat_name:
                    category = cat
                    break

        # ── 属性解析 ──
        attrs = _parse_attrs(api_obj.get("attributes") or [])

        # ── 拓扑（MPN推断 > 属性解析） ──
        topology = _infer_topology_from_mpn(part_number) or attrs.get("topology")

        # ── 固定输出电压（MPN推断 > 属性解析） ──
        output_voltage_v = (
            _infer_output_voltage_from_mpn(part_number)
            or attrs.get("output_voltage_v")
        )

        # ── Datasheet URL ──
        pdf_raw = api_obj.get("pdf") or {}
        datasheet_url: Optional[str] = (
            pdf_raw.get("url") if isinstance(pdf_raw, dict)
            else api_obj.get("datasheetUrl") or api_obj.get("datasheet_url")
        )

        # ── 是否国产 ──
        is_domestic = bool(
            manufacturer and any(d in manufacturer for d in _DOMESTIC_MANUFACTURERS)
        )

        # ── 是否车规（从描述中检测 AEC-Q100/Q101） ──
        desc = api_obj.get("description") or api_obj.get("summary") or ""
        automotive_grade = bool(
            _re.search(r"AEC[-–]?Q1\d\d", desc, _re.I)
        )

        # ── 特性标签（v2 新增）───────────────────────────────────
        features = _extract_features_from_desc(desc)

        part = PartIR.parse_obj({
            "part_number":        part_number,
            "manufacturer":       manufacturer,
            "category":           category,
            "topology":           topology,
            "is_domestic":        is_domestic,
            "description":        desc or None,
            "input_voltage_min_v":  attrs.get("input_voltage_min_v"),
            "input_voltage_max_v":  attrs.get("input_voltage_max_v"),
            "output_voltage_v":     output_voltage_v,
            "output_current_max_a": attrs.get("output_current_max_a"),
            "temperature_min_c":    attrs.get("temperature_min_c"),
            "temperature_max_c":    attrs.get("temperature_max_c"),
            "package":              (_extract_package_name(attrs.get("package"))
                                     or _extract_package_name(api_obj.get("footprint"))),
            "automotive_grade":     automotive_grade,
            "lifecycle_status":  (api_obj.get("lifecycleStatus")
                                   or api_obj.get("lifecycle_status")),
            "stock":            None,   # eZ-PLM v1 不返回实时库存
            "unit_price_cny":   None,   # eZ-PLM v1 不返回价格
            "datasheet_url":    datasheet_url,
            "ezplm_part_id":    api_obj.get("id"),
            "source":           "ezplm",
            # ── v2 新增字段 ──
            "switching_frequency_khz": attrs.get("switching_frequency_khz"),
            "quiescent_current_ua":    attrs.get("quiescent_current_ua"),
            "efficiency_pct":          attrs.get("efficiency_pct"),
            "features":                features,
            "footprint_file": (
                _extract_package_name(api_obj.get("footprint"))
                if api_obj.get("footprint") else None
            ),
            "symbol_file": (
                _extract_package_name(api_obj.get("symbol"))
                if api_obj.get("symbol") else None
            ),
        })

        # 记录到 known MPNs（供幻觉检测使用，LRU 有序集合）
        mpn_upper = part_number.upper()
        if mpn_upper in _KNOWN_MPNS:
            _KNOWN_MPNS.move_to_end(mpn_upper)  # 标记最近使用
        else:
            if len(_KNOWN_MPNS) >= _KNOWN_MPNS_MAX:
                _KNOWN_MPNS.popitem(last=False)  # LRU: 淘汰最旧条目
            _KNOWN_MPNS[mpn_upper] = True
        return part

    except Exception as e:
        logger.debug(f"_map_api_part error for {api_obj.get('mpn','?')}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# 约束过滤
# ═══════════════════════════════════════════════════════════════════

def _part_matches(part: PartIR, c: RequirementConstraints) -> bool:
    """
    检查器件是否满足需求约束。
    策略：宽松过滤——只有在器件和约束双方都有明确值时才进行比较，
    缺字段不拒绝，以避免因 eZ-PLM 数据不完整导致误排除。
    """
    # ── 类别/拓扑（仅双方均有值时比较）──────────────────────────
    if c.category and part.category and part.category != c.category:
        return False
    if c.topology and part.topology and part.topology != c.topology:
        return False

    # ── 输入电压覆盖 ─────────────────────────────────────────────
    if (c.input_voltage_nominal_v is not None
            and part.input_voltage_min_v is not None
            and part.input_voltage_max_v is not None):
        if not (part.input_voltage_min_v
                <= c.input_voltage_nominal_v
                <= part.input_voltage_max_v):
            return False

    # ── 输出电压（固定输出器件，±8% 容差；可调输出不过滤）──────
    part_vout = part.output_voltage_v or _infer_output_voltage_from_mpn(part.part_number)
    if part_vout is not None and c.output_voltage_v is not None:
        # 若 part_vout 看起来像额定电压范围而非固定值（>20V 的 max 容量），宽松处理
        if not (0.92 * c.output_voltage_v <= part_vout <= 1.08 * c.output_voltage_v):
            return False

    # ── 输出电流（部件有值才比较）───────────────────────────────
    if (c.output_current_a is not None
            and part.output_current_max_a is not None
            and part.output_current_max_a < c.output_current_a):
        return False

    # ── 温度范围 ─────────────────────────────────────────────────
    if (c.temperature_min_c is not None and c.temperature_max_c is not None
            and part.temperature_min_c is not None and part.temperature_max_c is not None):
        if not (part.temperature_min_c <= c.temperature_min_c
                and part.temperature_max_c >= c.temperature_max_c):
            return False

    return True


# ═══════════════════════════════════════════════════════════════════
# 关键词生成（从约束推导搜索词列表）
# ═══════════════════════════════════════════════════════════════════

def _generate_keywords(constraints: RequirementConstraints) -> List[str]:
    """
    根据约束生成 eZ-PLM MPN 前缀关键词列表。
    - 优先使用 category/topology 对应的已知前缀
    - 若约束中含厂商偏好，只保留对应厂商的前缀
    - 去重保序
    """
    category = getattr(constraints, "category", None) or "dc_dc_converter"
    topology = getattr(constraints, "topology", None)

    # 候选关键词：category/topology → 预定义列表
    cat_map = _API_KEYWORDS.get(category) or _API_KEYWORDS.get("dc_dc_converter", {})
    raw_kws = (
        cat_map.get(topology)
        or cat_map.get("ldo" if topology == "ldo" else None)
        or cat_map.get(None)
        or []
    )
    # 同时追加通用 None 关键词作补充
    if topology and cat_map.get(None):
        extra = [k for k in cat_map[None] if k not in raw_kws]
        raw_kws = list(raw_kws) + extra

    # 检测厂商偏好
    raw_input = getattr(constraints, "raw_input", "") or ""
    prefs = " ".join(getattr(constraints, "preferences", []) or [])
    combined = (raw_input + " " + prefs).lower()

    wanted_mfrs: List[str] = []
    for alias, key in _MFR_ALIASES.items():
        if alias in combined and key not in wanted_mfrs:
            wanted_mfrs.append(key)

    if not wanted_mfrs:
        # 无偏好 → 使用全量关键词
        return list(dict.fromkeys(raw_kws))

    # 有厂商偏好 → 只保留对应前缀
    filtered: List[str] = []
    for kw in raw_kws:
        kw_up = kw.upper()
        for mfr in wanted_mfrs:
            if any(kw_up.startswith(p.upper()) for p in _MFR_PREFIXES.get(mfr, [])):
                filtered.append(kw)
                break

    return list(dict.fromkeys(filtered)) if filtered else list(dict.fromkeys(raw_kws))


# ═══════════════════════════════════════════════════════════════════
# 公共接口
# ═══════════════════════════════════════════════════════════════════

def search_parts(
    constraints: RequirementConstraints,
    use_hybrid_retrieval: bool = False,   # 保留签名兼容性，暂不使用
) -> List[PartIR]:
    """
    通过 eZ-PLM API 搜索符合约束的真实器件（TI / ADI / Microchip / ST）。

    · 优先返回 24h 缓存，减少 API 调用次数
    · 429 时降级使用过期缓存，保证可用性
    · EZPLM_API_KEY 未配置时返回空列表
    """
    if not _get_api_key():
        logger.warning("EZPLM_API_KEY 未配置，无法查询 eZ-PLM 器件数据。请在管理员设置中配置。")
        return []

    keywords = _generate_keywords(constraints)
    if not keywords:
        logger.warning(
            f"无法为约束 category={constraints.category}/"
            f"topology={getattr(constraints,'topology',None)} 生成搜索关键词"
        )
        return []

    seen_pns: set = set()
    matched: List[PartIR] = []
    new_api_calls = 0

    for kw in keywords:
        if len(matched) >= _API_MAX_TOTAL:
            break

        # 缓存命中直接取；否则先检查限额再发请求
        if not _is_kw_cached(kw):
            if new_api_calls >= _API_MAX_CALLS:
                logger.info(f"已达 {_API_MAX_CALLS} 次新 API 请求上限，停止继续搜索")
                break
            if new_api_calls > 0:
                _api_rate_limiter.acquire()   # P2-4: token bucket (was: time.sleep)
            new_api_calls += 1

        raw_items, _ = _search_keyword(kw)

        for raw in raw_items:
            part = _map_api_part(raw)
            if not part or part.part_number in seen_pns:
                continue
            seen_pns.add(part.part_number)
            if _part_matches(part, constraints):
                matched.append(part)

    logger.debug(
        f"search_parts: {len(keywords)} 关键词，"
        f"{new_api_calls} 次新请求，"
        f"{len(matched)} 条匹配"
    )
    return matched


def find_replacements(part_number: str) -> List[PartIR]:
    """
    为指定型号查找同类替代器件。
    推断原型号的 category/topology，在 eZ-PLM 中搜索同类器件（去除自身）。
    """
    if not _get_api_key() or not part_number:
        return []
    cat = _infer_category_from_mpn(part_number) or "dc_dc_converter"
    topo = _infer_topology_from_mpn(part_number)
    fake_c = RequirementConstraints(
        raw_input=f"替代料 {part_number}",
        category=cat,
        topology=topo,
    )
    candidates = search_parts(fake_c)
    return [p for p in candidates if p.part_number.upper() != part_number.upper()]


def fetch_reference_designs(part_id: str) -> List[Dict]:
    """
    根据 eZ-PLM 零件 ID 获取关联参考设计（需要先通过 search_parts 得到 ezplm_part_id）。
    """
    if not _get_api_key() or not part_id:
        return []
    status, body = _request_json(
        "/api/v1/api-key/reference-designs",
        {"partlibId": part_id, "pageSize": "5"},
    )
    if status == 200:
        return body.get("data") or []
    logger.debug(f"fetch_reference_designs partId={part_id} → HTTP {status}")
    return []


# ═══════════════════════════════════════════════════════════════════
# v2 新增：精确 MPN 查询 + 按 ID 获取详情 + 候选批量富化
# ═══════════════════════════════════════════════════════════════════

# Part detail 的 24h 缓存（part_id → (expire_ts, raw_dict)）
_DETAIL_CACHE: OrderedDict[str, Tuple[float, Dict]] = OrderedDict()
_DETAIL_CACHE_MAX: int = 200  # LRU 容量上限


def search_part_by_mpn(mpn: str) -> Optional[PartIR]:
    """
    精确 MPN 查询：以完整型号作为 keyword 搜索 eZ-PLM，返回最佳匹配的 PartIR。

    与 search_parts() 的关键词前缀搜索不同，此函数直接搜索完整型号，
    适用于：
      · 用户明确指定了型号的替代料查询
      · 需要在系统中验证某个型号是否真实存在
      · 为已知型号补充 ezplm_part_id 和 datasheet_url

    注：同样享受 24h 关键词缓存。
    """
    if not _get_api_key() or not mpn:
        return None
    results, _ = _search_keyword(mpn.strip())
    # 优先返回 MPN 完全匹配的条目
    for raw in results:
        pn = raw.get("mpn") or raw.get("partNumber") or ""
        if pn.upper() == mpn.upper():
            return _map_api_part(raw)
    # 没有完全匹配则返回第一条（可能是相近型号）
    if results:
        part = _map_api_part(results[0])
        if part:
            logger.debug(f"search_part_by_mpn: '{mpn}' 无精确匹配，返回近似 {part.part_number}")
        return part
    return None


def _detail_cache_put(k: str, v: Tuple[float, Dict]):
    """写入详情缓存，超出容量时淘汰最旧条目。"""
    _DETAIL_CACHE[k] = v
    if len(_DETAIL_CACHE) > _DETAIL_CACHE_MAX:
        _DETAIL_CACHE.popitem(last=False)


def fetch_part_detail(part_id: str) -> Optional[PartIR]:
    """
    通过 eZ-PLM 零件 ID 获取单件完整详情（GET /api/v1/api-key/parts/{id}）。

    相比关键词搜索，Detail 端点通常返回更完整的 attributes（含开关频率、效率等），
    可用于在初次搜索后对 Top-N 候选进行二次信息富化。

    · 结果缓存 24h（同一 part_id 不重复请求）
    · 端点不存在时（404/405）静默退出，不影响主流程
    """
    if not _get_api_key() or not part_id:
        return None

    now = time.time()
    cached = _DETAIL_CACHE.get(part_id)
    if cached and now < cached[0]:
        return _map_api_part(cached[1]) if cached[1] else None

    status, body = _request_json(
        f"/api/v1/api-key/parts/{part_id}",
        {},
    )
    if status == 200:
        data = body.get("data") or body  # 兼容两种响应包装
        if isinstance(data, dict) and data.get("mpn") or data.get("partNumber"):
            _detail_cache_put(part_id, (now + _KW_CACHE_TTL, data))
            return _map_api_part(data)
        # 端点存在但 data 为空或格式不符：缓存空结果，避免重复请求
        _detail_cache_put(part_id, (now + _KW_CACHE_TTL, {}))
        return None
    elif status in (404, 405, 501):
        # 端点未开放，静默忽略
        logger.debug(f"fetch_part_detail: /parts/{{id}} 端点 HTTP {status}，跳过详情富化")
        _detail_cache_put(part_id, (now + _KW_CACHE_TTL, {}))
        return None
    else:
        logger.debug(f"fetch_part_detail partId={part_id} → HTTP {status}")
        return None


def enrich_candidates_with_details(
    candidates: List[PartIR],
    max_enrich: int = 8,
) -> List[PartIR]:
    """
    对候选器件列表中前 max_enrich 个有 ezplm_part_id 的器件调用详情接口，
    将返回的更完整属性（开关频率、效率、Iq、更多特性标签）合并回 PartIR。

    · 仅富化 ezplm_part_id 非空的器件
    · 详情接口不可用时（404/405）静默跳过，不影响原始候选列表
    · 最多发出 max_enrich 次新请求（超出部分保持原样）
    · 相邻请求间隔 _API_DELAY_S 秒

    NOTE (M5): 当前使用 time.sleep() 同步阻塞，在线程池中调用时会阻塞线程。
    将来可改为 asyncio.sleep() + async 函数以释放线程池资源。
    调用方（agent_orchestrator.py / main.py）需同步改动。

    调用时机：score_candidates 之前，让评分能利用更丰富的参数。
    """
    if not _get_api_key():
        return candidates

    enriched_count = 0
    result: List[PartIR] = []

    for part in candidates:
        if not part.ezplm_part_id or enriched_count >= max_enrich:
            result.append(part)
            continue

        if enriched_count > 0:
            _api_rate_limiter.acquire()   # P2-4: token bucket

        detail = fetch_part_detail(part.ezplm_part_id)
        enriched_count += 1

        if detail is None:
            result.append(part)
            continue

        # 将 detail 中更丰富的字段合并进原 PartIR（仅填充原先为 None 的字段）
        merged_fields: Dict[str, Any] = part.dict() if hasattr(part, "dict") else {}

        def _merge(field: str):
            """仅在原值为 None/空时用 detail 中的值覆盖。"""
            orig = merged_fields.get(field)
            new_val = getattr(detail, field, None)
            if (orig is None or orig == [] or orig == "") and new_val:
                merged_fields[field] = new_val

        for f in (
            "switching_frequency_khz", "quiescent_current_ua", "efficiency_pct",
            "input_voltage_min_v", "input_voltage_max_v",
            "output_voltage_v", "output_current_max_a",
            "temperature_min_c", "temperature_max_c",
            "package", "lifecycle_status", "datasheet_url",
        ):
            _merge(f)

        # features：合并去重
        orig_feats = set(merged_fields.get("features") or [])
        new_feats  = set(getattr(detail, "features", []) or [])
        merged_fields["features"] = sorted(orig_feats | new_feats)

        try:
            enriched = PartIR.parse_obj(merged_fields)
        except Exception as e:
            logger.warning(f"enrich_candidates: PartIR 重建失败 {part.part_number}: {e}")
            enriched = part  # 合并失败则保持原样

        # ⑥ 数据手册参数补充（仅在关键字段仍缺失时触发）
        if not enriched.switching_frequency_khz or not enriched.output_current_max_a:
            enriched = enrich_part_from_datasheet(enriched)

        result.append(enriched)
        logger.debug(
            f"enrich: {part.part_number} — "
            f"fsw={getattr(enriched,'switching_frequency_khz',None)} kHz, "
            f"eff={getattr(enriched,'efficiency_pct',None)}%, "
            f"feats={getattr(enriched,'features',[])}"
        )

    logger.info(
        f"enrich_candidates_with_details: {enriched_count} 次详情请求，"
        f"{len(result)} 个候选"
    )
    return result


def get_known_mpns() -> set:
    """返回当前会话中从 eZ-PLM API 获取到的所有已知 MPN 集合（供幻觉检测用）。"""
    return frozenset(_KNOWN_MPNS.keys())


# ═══════════════════════════════════════════════════════════════════
# ⑥ 数据手册 PDF 参数深化提取（补充 eZ-PLM attributes 缺失字段）
# ═══════════════════════════════════════════════════════════════════

_DS_CACHE: dict = {}   # url → (expire_ts, params_dict)
_DS_CACHE_MAX = 200


def _extract_params_from_pdf_text(text: str, part: "PartIR") -> dict:
    """从数据手册文本中提取缺失的关键电气参数（正则 + 单位换算）。"""
    params: dict = {}
    t = text[:30000]  # 仅扫描前30k字符，足覆盖摘要页

    if getattr(part, "input_voltage_max_v", None) is None:
        m = _re.search(r"V_?IN\s*[\(（]?max[\)）]?\s*[=:\.]\s*(\d+(?:\.\d+)?)\s*V", t, _re.I)
        if m:
            params["input_voltage_max_v"] = float(m.group(1))

    if getattr(part, "output_voltage_v", None) is None:
        m = _re.search(r"V_?OUT\s*[=:\.]\s*(\d+(?:\.\d+)?)\s*V", t, _re.I)
        if m:
            val = float(m.group(1))
            if 0.5 <= val <= 60:
                params["output_voltage_v"] = val

    if getattr(part, "output_current_max_a", None) is None:
        m = _re.search(r"I_?OUT\s*[\(（]?max[\)）]?\s*[=:\.]\s*(\d+(?:\.\d+)?)\s*(A|mA)", t, _re.I)
        if m:
            val = float(m.group(1))
            if m.group(2).upper() == "MA":
                val /= 1000
            if 0.001 <= val <= 100:
                params["output_current_max_a"] = val

    if getattr(part, "switching_frequency_khz", None) is None:
        m = _re.search(r"f_?SW\s*[=:\.]\s*(\d+(?:\.\d+)?)\s*(MHz|kHz)", t, _re.I)
        if m:
            val = float(m.group(1))
            if "MHz" in m.group(2):
                val *= 1000
            if 10 <= val <= 10000:
                params["switching_frequency_khz"] = val

    if getattr(part, "efficiency_pct", None) is None:
        m = _re.search(r"efficiency[^\n]{0,30}?(\d{2,3}(?:\.\d+)?)\s*%", t, _re.I)
        if m:
            val = float(m.group(1))
            if 50 <= val <= 100:
                params["efficiency_pct"] = val

    return params


def enrich_part_from_datasheet(part: "PartIR") -> "PartIR":
    """
    下载器件数据手册 PDF 并提取缺失电气参数，补充 PartIR。
    - 24h URL 缓存，避免重复下载
    - 超时/网络失败静默跳过，不影响主流程
    """
    if not getattr(part, "datasheet_url", None):
        return part

    url = part.datasheet_url
    now = time.time()
    cached = _DS_CACHE.get(url)
    if cached and now < cached[0]:
        params = cached[1]
    else:
        try:
            import urllib.request as _ur, tempfile as _tmp, os as _os
            try:
                import fitz as _fitz
            except ImportError:
                return part  # PyMuPDF 未安装则跳过

            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0 (eZmanbo datasheet-fetcher)"})
            with _ur.urlopen(req, timeout=12) as resp:
                pdf_bytes = resp.read(5 * 1024 * 1024)   # 最多5 MB

            with _tmp.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf_bytes)
                tmp_path = f.name
            try:
                doc = _fitz.open(tmp_path)
                text = "\n".join(doc[i].get_text() for i in range(min(10, len(doc))))
                doc.close()
            finally:
                _os.unlink(tmp_path)

            params = _extract_params_from_pdf_text(text, part)
            _DS_CACHE[url] = (now + _KW_CACHE_TTL, params)
            if len(_DS_CACHE) > _DS_CACHE_MAX:
                del _DS_CACHE[next(iter(_DS_CACHE))]
        except Exception as exc:
            logger.debug(f"datasheet enrichment failed for {part.part_number}: {exc}")
            return part

    if not params:
        return part

    # 将提取到的参数合并回 PartIR（不覆盖已有值）
    try:
        updated = {**part.dict(), **{k: v for k, v in params.items() if part.dict().get(k) is None}}
        return PartIR.parse_obj(updated)
    except Exception:
        return part
