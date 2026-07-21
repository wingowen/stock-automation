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

import pandas as pd

from wyckoff.data.base import FetchError
from wyckoff.data.pipeline import DataPipeline

from tests.helpers import (
    MockSource,
    make_ohlc_row,
    make_ohlc_df,
    patch_trading_dates,
)


class TestDataPipeline(unittest.TestCase):
    """DataPipeline 端到端测试"""

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

        patch_trading_dates(start, end)

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

        patch_trading_dates(start, end)

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
