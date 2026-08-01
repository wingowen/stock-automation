"""腾讯行情接口获取实时价格"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

# 腾讯行情接口
QT_URL = "https://qt.gtimg.cn/q="

# 交易所前缀
EXCHANGE_PREFIX = {"6": "sh"}
EXCHANGE_PREFIX_DEFAULT = "sz"

# 会话（复用连接）
_session = requests.Session()
_session.trust_env = False  # 规避 macOS 系统代理


@dataclass
class Quote:
    """单只股票实时行情"""
    code: str    # 6 位代码
    name: str    # 股票名称
    price: float # 当前价
    high: float  # 今日最高
    low: float   # 今日最低
    open: float  # 今日开盘
    pre_close: float  # 昨收
    volume: int  # 成交量（手）


def resolve_symbol(code: str) -> str:
    """6 位代码 → 腾讯接口符号（如 sz002611）"""
    prefix = EXCHANGE_PREFIX.get(code[0], EXCHANGE_PREFIX_DEFAULT)
    return f"{prefix}{code}"


def parse_qt_response(text: str) -> Optional[Quote]:
    """解析单行腾讯行情响应

    v_sz002611="51~东方精工~002611~15.97~15.69~15.95~...~16.20~15.77~396424~..."
    字段用 ~ 分割，索引见下表。
    """
    # 提取引号内的内容
    m = re.search(r'"(.+)"', text)
    if not m:
        return None
    parts = m.group(1).split("~")
    if len(parts) < 38:
        return None

    try:
        code = parts[2]
        name = parts[1]
        price = float(parts[3]) if parts[3] else 0.0
        pre_close = float(parts[4]) if parts[4] else 0.0
        open_p = float(parts[5]) if parts[5] else 0.0
        high = float(parts[34]) if parts[34] else 0.0
        low = float(parts[35]) if parts[35] else 0.0
        volume_str = parts[36] if len(parts) > 36 else "0"
        volume = int(float(volume_str)) if volume_str else 0
    except (ValueError, IndexError):
        return None

    if price == 0.0:
        return None

    return Quote(
        code=code, name=name, price=price,
        high=high, low=low, open=open_p,
        pre_close=pre_close, volume=volume,
    )


def fetch_quotes(codes: List[str]) -> Dict[str, Quote]:
    """批量获取多只股票实时行情

    Args:
        codes: 6 位代码列表，如 ["002611", "002279"]

    Returns:
        {code: Quote} 字典，只包含成功获取的股票
    """
    if not codes:
        return {}

    symbols = ",".join(resolve_symbol(c) for c in codes)
    url = f"{QT_URL}{symbols}"

    try:
        resp = _session.get(url, timeout=10)
        resp.encoding = "utf-8"
        resp.raise_for_status()
    except requests.RequestException:
        return {}

    result: Dict[str, Quote] = {}
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        quote = parse_qt_response(line)
        if quote and quote.price > 0:
            result[quote.code] = quote

    return result