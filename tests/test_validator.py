"""数据验证器测试

覆盖 BatchValidator 和 MergeValidator 的核心逻辑：
- 批次级双源交叉验证
- 价格/量能差异检测
- 缺日检测
- 多批次合并与去重
"""
from __future__ import annotations

import unittest
from datetime import date, timedelta

import pandas as pd

from wyckoff.data.base import CacheMissError, DataSource
from wyckoff.data.validator import (
    BatchValidator,
    MergeValidator,
    TradingDates,
    ValidationResult,
)

from tests.helpers import (
    MockSource,
    make_ohlc_row,
    make_ohlc_df,
    patch_trading_dates,
)


class TestBatchValidator(unittest.TestCase):
    """批次级验证器测试"""

    def test_two_identical_sources_pass(self):
        """两个完全一致的数据源应通过验证"""
        rows = [make_ohlc_row(date(2024, 1, 2) + timedelta(days=i)) for i in range(5)]
        df = make_ohlc_df(rows)
        src1 = MockSource("A", df)
        src2 = MockSource("B", df)

        validator = BatchValidator(sources=[src1, src2])
        patch_trading_dates(date(2024, 1, 2), date(2024, 1, 8))

        result = validator.validate_batch("600519", date(2024, 1, 2), date(2024, 1, 8))
        self.assertTrue(result.passed)
        self.assertEqual(len(result.discrepancies), 0)
        self.assertEqual(len(result.missing_dates), 0)

    def test_price_discrepancy_detected(self):
        """价格差异超过容差应被检出"""
        rows_a = [make_ohlc_row(date(2024, 1, 2) + timedelta(days=i)) for i in range(5)]
        df_a = make_ohlc_df(rows_a)
        rows_b = [r.copy() for r in rows_a]
        rows_b[2]["close"] = rows_a[2]["close"] + 0.05  # 差异 0.05 > 0.01
        df_b = make_ohlc_df(rows_b)

        src1 = MockSource("A", df_a)
        src2 = MockSource("B", df_b)

        validator = BatchValidator(sources=[src1, src2])
        patch_trading_dates(date(2024, 1, 2), date(2024, 1, 8))

        result = validator.validate_batch("600519", date(2024, 1, 2), date(2024, 1, 8))
        self.assertFalse(result.passed)
        self.assertEqual(len(result.discrepancies), 1)
        self.assertEqual(result.discrepancies[0].field, "close")

    def test_volume_discrepancy_detected(self):
        """量能差异超过容差应被检出"""
        rows_a = [make_ohlc_row(date(2024, 1, 2) + timedelta(days=i)) for i in range(5)]
        df_a = make_ohlc_df(rows_a)
        rows_b = [r.copy() for r in rows_a]
        rows_b[2]["volume"] = rows_a[2]["volume"] + 5  # 差异 5 > 1
        df_b = make_ohlc_df(rows_b)

        src1 = MockSource("A", df_a)
        src2 = MockSource("B", df_b)

        validator = BatchValidator(sources=[src1, src2])
        patch_trading_dates(date(2024, 1, 2), date(2024, 1, 8))

        result = validator.validate_batch("600519", date(2024, 1, 2), date(2024, 1, 8))
        self.assertFalse(result.passed)
        self.assertEqual(len(result.discrepancies), 1)
        self.assertEqual(result.discrepancies[0].field, "volume")

    def test_missing_dates_detected(self):
        """缺失交易日应被检出"""
        rows = [make_ohlc_row(date(2024, 1, 2) + timedelta(days=i)) for i in range(5)
                if i != 2]  # 缺 1/4
        df = make_ohlc_df(rows)
        src1 = MockSource("A", df)
        src2 = MockSource("B", df)

        validator = BatchValidator(sources=[src1, src2])
        patch_trading_dates(date(2024, 1, 2), date(2024, 1, 8))

        result = validator.validate_batch("600519", date(2024, 1, 2), date(2024, 1, 8))
        self.assertFalse(result.passed)
        self.assertGreater(len(result.missing_dates), 0)

    def test_cache_miss_tolerated(self):
        """只读缓存未命中不应导致整批失败"""
        rows = [make_ohlc_row(date(2024, 1, 2) + timedelta(days=i)) for i in range(5)]
        df = make_ohlc_df(rows)
        src1 = MockSource("A", df)
        src2 = MockSource("B", raise_on_fetch=CacheMissError)

        validator = BatchValidator(sources=[src1, src2])
        patch_trading_dates(date(2024, 1, 2), date(2024, 1, 8))

        result = validator.validate_batch("600519", date(2024, 1, 2), date(2024, 1, 8))
        self.assertTrue(result.passed)
        self.assertEqual(len(result.cache_misses), 1)


class TestMergeValidator(unittest.TestCase):
    """全量合并验证器测试"""

    def test_merge_batches_deduplicates_overlap(self):
        """相邻批次重叠 1 天，合并后保留后一批次数据"""
        batch1 = make_ohlc_df([
            make_ohlc_row(date(2024, 1, 2), close=100.0),
            make_ohlc_row(date(2024, 1, 3), close=101.0),
            make_ohlc_row(date(2024, 1, 4), close=102.0),
        ])
        batch2 = make_ohlc_df([
            make_ohlc_row(date(2024, 1, 4), close=102.5),  # 重叠日，不同价格
            make_ohlc_row(date(2024, 1, 5), close=103.0),
        ])

        validator = MergeValidator()
        merged = validator.merge_batches([batch1, batch2])

        self.assertEqual(len(merged), 4)
        row_0104 = merged[merged["date"] == date(2024, 1, 4)].iloc[0]
        self.assertEqual(row_0104["close"], 102.5)

    def test_validate_merge_detects_gap(self):
        """合并后缺日应被检出"""
        rows = [
            make_ohlc_row(date(2024, 1, 2)),
            make_ohlc_row(date(2024, 1, 4)),  # 缺 1/3
        ]
        df = make_ohlc_df(rows)

        validator = MergeValidator()
        TradingDates._cache["2024-01-02_2024-01-04"] = {
            date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
        }
        TradingDates._cache_timestamp = 1.0

        result = validator.validate_merge(df, date(2024, 1, 2), date(2024, 1, 4))
        self.assertFalse(result.passed)
        self.assertIn(date(2024, 1, 3), result.missing_dates)


if __name__ == "__main__":
    unittest.main(verbosity=2)
