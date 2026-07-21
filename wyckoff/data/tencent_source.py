"""腾讯财经数据源 (在线模式 + 本地缓存 fallback)

优先从腾讯在线接口拉取前复权日线数据；网络不可用时回退到本地 CSV 缓存。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from wyckoff.data.base import (
    CacheMissError,
    DataSource,
    FetchError,
    normalize_ohlcv,
)

logger = logging.getLogger(__name__)

# 数据缓存根目录
# 默认缓存目录（威科夫系统数据目录）
DEFAULT_CACHE_DIR = Path(__file__).parent / "cache"

# 腾讯 K 线 API
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def load_csv(
    code: str,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """从本地 CSV 加载指定股票的数据（兼容性保留）

    Args:
        code: 6 位股票代码 (不带 sh/sz 前缀)
        cache_dir: CSV 所在目录

    Returns:
        DataFrame: date, code, name, open, high, low, close, volume
        按 date 升序, 单只股票
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR

    candidates = [
        cache_dir / f"{code}.csv",
        cache_dir / f"{code}_full.csv",
    ]
    csv_path = None
    for p in candidates:
        if p.exists():
            csv_path = p
            break

    if csv_path is None:
        raise FileNotFoundError(
            f"No cached data for {code} in {cache_dir}. "
            f"Run fetch_tencent.sh or WebFetch + manual save first."
        )

    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    logger.info("Loaded %d rows for %s from %s", len(df), code, csv_path.name)
    return df


def load_600519() -> pd.DataFrame:
    """便捷: 加载 600519 贵州茅台 (Phase B 测试用)"""
    return load_csv("600519")


class TencentSource(DataSource):
    """腾讯财经数据源

    在线模式：调用腾讯 K 线接口获取前复权数据。
    缓存回退：在线失败时尝试加载本地 CSV。
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._session = requests.Session()
        self._session.trust_env = False

    def fetch(
        self,
        code: str,
        start_date: date,
        end_date: date,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """拉取指定日期范围的数据

        Args:
            code: 6位股票代码（不带 sh/sz 前缀）
            start_date: 起始日期
            end_date: 结束日期（包含）
            adjust: 复权方式，默认前复权 "qfq"

        Returns:
            DataFrame: 包含 date, code, open, high, low, close, volume

        Raises:
            FetchError: 在线和缓存均失败
        """
        try:
            return self._fetch_online(code, start_date, end_date, adjust)
        except FetchError as e:
            logger.warning(f"Tencent online fetch failed: {e}, falling back to cache")
            return self._fetch_cache(code, start_date, end_date)

    def name(self) -> str:
        return "Tencent"

    def _fetch_online(
        self,
        code: str,
        start_date: date,
        end_date: date,
        adjust: str,
    ) -> pd.DataFrame:
        """在线拉取腾讯 K 线数据"""
        exchange = "sh" if code.startswith("6") else "sz"
        symbol = f"{exchange}{code}"
        adjust_map = {"qfq": "qfq", "hfq": "hfq", "": ""}
        adjust_key = adjust_map.get(adjust, "qfq")

        # 腾讯接口单次最多返回 640 条；计算天数并留足余量
        days = (end_date - start_date).days + 1
        count = max(days, 640)

        param = f"{symbol},day,{start_date.strftime('%Y-%m-%d')},{end_date.strftime('%Y-%m-%d')},{count},{adjust_key}"
        params = {"param": param}

        logger.info(f"Tencent fetching {code} [{start_date} ~ {end_date}]")

        try:
            r = self._session.get(TENCENT_KLINE_URL, params=params, timeout=20)
            r.raise_for_status()
        except Exception as e:
            raise FetchError(f"Tencent HTTP request failed: {e}") from e

        try:
            data = r.json()
        except Exception as e:
            raise FetchError(f"Tencent response is not valid JSON: {e}") from e

        stock_data = data.get("data", {}).get(symbol, {})
        key = f"{adjust_key}day" if adjust_key else "day"
        raw_rows = stock_data.get(key) or stock_data.get("day") or stock_data.get("qfqday")

        if not raw_rows:
            raise FetchError(f"Tencent returned no kline data for {code}")

        df = pd.DataFrame(raw_rows, columns=["date", "open", "close", "high", "low", "volume"])
        df = self._normalize(df, code)
        self.validate_response(df)

        logger.info(f"Tencent fetched {len(df)} rows for {code}")
        return df

    def _fetch_cache(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """从本地缓存加载"""
        candidates = [
            self._cache_dir / f"{code}.csv",
            self._cache_dir / f"{code}_full.csv",
        ]
        csv_path = None
        for p in candidates:
            if p.exists():
                csv_path = p
                break

        if csv_path is None:
            raise CacheMissError(f"No cached data for {code} in {self._cache_dir}")

        try:
            df = pd.read_csv(csv_path)
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df.sort_values("date").reset_index(drop=True)
        except Exception as e:
            raise FetchError(f"Failed to read cache file {csv_path}: {e}") from e

        cache_start = df["date"].iloc[0]
        cache_end = df["date"].iloc[-1]
        if start_date < cache_start or end_date > cache_end:
            raise CacheMissError(
                f"Requested [{start_date} ~ {end_date}] exceeds cache range "
                f"[{cache_start} ~ {cache_end}]"
            )

        mask = (df["date"] >= start_date) & (df["date"] <= end_date)
        df = df[mask].copy().reset_index(drop=True)
        df = self._normalize(df, code)
        self.validate_response(df)
        return df

    def _normalize(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        return normalize_ohlcv(df, code, volume_divisor=1)
