"""Baostock 数据加载封装 (Phase B 子任务: T1 数据源验证)

提供两个核心函数:
    fetch_daily(code, start, end) -> pd.DataFrame
    test_connection() -> bool

输出 DataFrame 字段 (前复权):
    date, code, name, open, high, low, close, volume, amount

异常:
    DataFetchError: 任何网络/解析问题
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import baostock as bs
import pandas as pd

logger = logging.getLogger(__name__)


class DataFetchError(RuntimeError):
    """数据获取失败"""


# ---- 模块级单例 (Baostock 客户端) ----
_bs_logged_in = False


def _ensure_login() -> None:
    """确保 Baostock 已登录 (全局只一次)"""
    global _bs_logged_in
    if not _bs_logged_in:
        lg = bs.login()
        if lg.error_code != "0":
            raise DataFetchError(f"Baostock login failed: {lg.error_msg}")
        _bs_logged_in = True


def _fmt_date(d: date | str) -> str:
    """Baostock 接受 'YYYY-MM-DD' 格式"""
    if isinstance(d, str):
        return d
    return d.strftime("%Y-%m-%d")


def fetch_daily(
    code: str,
    start: date | str,
    end: date | str,
    adjustflag: int = 2,  # 2=前复权
) -> pd.DataFrame:
    """拉取单只股票的日线数据

    Args:
        code: 6 位股票代码, 如 "600519" 或 "sh.600519"
        start: 起始日期
        end: 结束日期
        adjustflag: 复权方式 1=后复权 2=前复权 3=不复权

    Returns:
        DataFrame, columns: date, code, name, open, high, low, close, volume, amount
        (按 date 升序)
    """
    _ensure_login()

    # Baostock 接受 sh.600519 格式
    if "." not in code:
        if code.startswith(("5", "6", "9", "110", "113", "127")):
            code = f"sh.{code}"
        else:
            code = f"sz.{code}"

    rs = bs.query_history_k_data_plus(
        code,
        "date,code,open,high,low,close,volume,amount",
        start_date=_fmt_date(start),
        end_date=_fmt_date(end),
        frequency="d",
        adjustflag=adjustflag,
    )
    if rs.error_code != "0":
        raise DataFetchError(
            f"Baostock query failed for {code}: {rs.error_msg}"
        )

    rows = []
    while (rs.error_code == "0") and rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        raise DataFetchError(f"No data returned for {code} ({start} - {end})")

    df = pd.DataFrame(rows, columns=[
        "date", "code", "open", "high", "low", "close", "volume", "amount"
    ])

    # 类型转换
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Baostock 不返回 name 字段 (避免额外 query 性能开销)
    # 解析 code 为市场 + 6 位数字
    market, ticker = code.split(".")
    df["name"] = ticker  # 占位; 真实名称需另查

    df = df[["date", "code", "name", "open", "high", "low", "close", "volume", "amount"]]
    df = df.sort_values("date").reset_index(drop=True)
    return df


def test_connection() -> bool:
    """测试 Baostock 是否可用 (Phase B 冒烟测试)"""
    try:
        _ensure_login()
        return True
    except Exception as e:
        logger.error("Baostock connection test failed: %s", e)
        return False
