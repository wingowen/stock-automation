"""数据流水线端到端测试

使用 mock 数据源验证 DataPipeline 的核心流程：
- 批次划分
- 逐批次处理
- 多批次合并
- 回退逻辑
- 持久化
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from wyckoff.data.base import CacheMissError, DataSource, FetchError
from wyckoff.data.pipeline import DataPipeline
from wyckoff.data.validator import TradingDates


def make_ohlc_row(d: date, code: str = "600519", open_: float = 100.0,
                  high: float = 101.0, low: float = 99.0, close: float = 100.5,
                  volume: int = 1000) -> dict:
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
    df = pd.DataFrame(rows)
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float).round(2)
    df["volume"] = df["volume"].astype(int)
    return df


class MockSource(DataSource):
    """可编程 mock 数据源"""

    def __init__(self, name: str, data: Optional[pd.DataFrame] = None,
                 raise_on_fetch: Optional[type] = None):
        self._name = name
        self._data = data
        self._raise = raise_on_fetch

    def fetch(self, code: str, start_date: date, end_date: date,
              adjust: str = "qfq") -> pd.DataFrame:
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


class TestDataPipeline(unittest.TestCase):
    """DataPipeline 端到端测试"""

    def _trading_dates(self, start: date, end: date) -> set[date]:
        """简单交易日集合（仅排除周末）"""
        dates = set()
        d = start
        while d <= end:
            if d.weekday() < 5:
                dates.add(d)
            d += timedelta(days=1)
        return dates

    def _patch_trading_dates(self, start: date, end: date):
        TradingDates._cache[f"{start}_{end}"] = self._trading_dates(start, end)
        TradingDates._cache_timestamp = 1.0

    def _make_full_data(self, start: date, end: date) -> pd.DataFrame:
        """生成覆盖指定日期范围的 mock 数据"""
        rows = []
        d = start
        while d <= end:
            if d.weekday() < 5:
                rows.append(make_ohlc_row(d))
            d += timedelta(days=1)
        return make_ohlc_df(rows)

    def test_split_batches_default_90_days(self):
        """默认按 90 天划分批次"""
        data = self._make_full_data(date(2020, 1, 2), date(2020, 6, 30))
        src1 = MockSource("A", data)
        src2 = MockSource("B", data)

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = DataPipeline(
                sources=[src1, src2],
                cache_dir=Path(tmpdir),
                batch_days=90,
            )
            batches = pipeline._split_batches(date(2020, 1, 2), date(2020, 6, 30))
            self.assertGreaterEqual(len(batches), 2)
            self.assertEqual(batches[0][0], date(2020, 1, 2))
            self.assertEqual(batches[-1][1], date(2020, 6, 30))

    def test_run_full_pipeline_with_mock_sources(self):
        """使用 mock 数据源跑完整流水线"""
        start = date(2024, 1, 2)
        end = date(2024, 1, 15)
        data = self._make_full_data(start, end)
        src1 = MockSource("A", data)
        src2 = MockSource("B", data)

        self._patch_trading_dates(start, end)

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = DataPipeline(
                sources=[src1, src2],
                cache_dir=Path(tmpdir),
                batch_days=7,
            )
            merged = pipeline.run("600519", start, end)

            self.assertEqual(len(merged), len(data))
            self.assertTrue((merged["date"].sort_values().values == data["date"].sort_values().values).all())

            # 检查持久化文件
            cache_file = Path(tmpdir) / "600519_full.csv"
            self.assertTrue(cache_file.exists())

    def test_fallback_to_secondary_source(self):
        """主源失败时回退到次源"""
        start = date(2024, 1, 2)
        end = date(2024, 1, 5)
        data = self._make_full_data(start, end)
        primary = MockSource("Primary", raise_on_fetch=FetchError)
        secondary = MockSource("Secondary", data)

        self._patch_trading_dates(start, end)

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = DataPipeline(
                sources=[primary, secondary],
                cache_dir=Path(tmpdir),
                batch_days=7,
            )
            merged = pipeline.run("600519", start, end)
            self.assertEqual(len(merged), len(data))


if __name__ == "__main__":
    unittest.main(verbosity=2)
