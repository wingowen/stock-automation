"""测试工具类

包含 MockSource、OHLCV 数据构造器等共享测试组件。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd

from wyckoff.data.base import CacheMissError, DataSource, FetchError
from wyckoff.data.validator import TradingDates


def make_ohlc_row(
    d: date,
    code: str = "600519",
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: int = 1000,
) -> dict:
    """生成单条 OHLCV 记录"""
    return {
        "date": d,
        "code": code,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def make_ohlc_df(rows: list[dict]) -> pd.DataFrame:
    """从记录列表生成规范 DataFrame"""
    df = pd.DataFrame(rows)
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float).round(2)
    df["volume"] = df["volume"].astype(int)
    return df


def make_trading_dates(start: date, end: date) -> set[date]:
    """生成简单交易日集合（仅排除周末）"""
    dates = set()
    d = start
    while d <= end:
        if d.weekday() < 5:
            dates.add(d)
        d += timedelta(days=1)
    return dates


def patch_trading_dates(start: date, end: date) -> None:
    """将 TradingDates 缓存替换为简单周末过滤结果"""
    TradingDates._cache[f"{start}_{end}"] = make_trading_dates(start, end)
    TradingDates._cache_timestamp = 1.0


class MockSource(DataSource):
    """可编程 mock 数据源

    Args:
        name: 数据源名称
        data: 预设 DataFrame，为 None 时 fetch 抛 FetchError
        raise_on_fetch: 如果设置，fetch 时抛出指定异常（用于模拟故障）
    """

    def __init__(
        self,
        name: str,
        data: Optional[pd.DataFrame] = None,
        raise_on_fetch: Optional[type] = None,
    ):
        self._name = name
        self._data = data
        self._raise = raise_on_fetch

    def fetch(
        self,
        code: str,
        start_date: date,
        end_date: date,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        if self._raise:
            raise self._raise(f"{self._name} fetch failed")
        if self._data is None:
            raise FetchError(f"{self._name} has no data")

        mask = (self._data["date"] >= start_date) & (self._data["date"] <= end_date)
        df = self._data[mask].copy()
        df["code"] = code
        return df.reset_index(drop=True)

    def name(self) -> str:
        return self._name
