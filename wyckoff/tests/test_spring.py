"""Spring 检测器测试 (Phase B 第一个 TDD 闭环)

参考 TDD 原则:
  1. 写失败测试
  2. 写最小实现让测试通过
  3. 重构

测试方法约定:
  - test_xxx_yyy: xxx = 场景, yyy = 期望
"""
import unittest
from datetime import date, timedelta

import pandas as pd

from wyckoff.detectors.spring import detect_spring


def make_synthetic_ohlc(
    start_date: date = date(2024, 1, 1),
    days: int = 30,
    base_price: float = 100.0,
    range_pct: float = 0.10,  # 区间宽度 ±10%
) -> pd.DataFrame:
    """生成一段横向盘整的合成 K 线数据

    默认: 30 天, 价格 100 ± 10% 区间内随机波动
    返回连续 30 行 (含周末, 模拟时不严格)
    """
    import random
    random.seed(42)

    rows = []
    for i in range(days):
        d = start_date + timedelta(days=i)
        # 跳开周末
        if d.weekday() >= 5:
            continue
        noise = random.uniform(-range_pct / 2, range_pct / 2)
        price = base_price * (1 + noise)
        rows.append({
            "date": d,
            "code": "TEST",
            "name": "TestStock",
            "open": price * 0.99,
            "high": price * 1.02,
            "low": price * 0.98,
            "close": price,
            "volume": 1000,
        })
    return pd.DataFrame(rows)


def insert_spring(
    df: pd.DataFrame,
    insert_idx: int,
    break_low: float,
    recover_close: float,
    volume_mult: float = 2.0,
) -> pd.DataFrame:
    """在 df 的 insert_idx 位置插入一个 Spring K 线

    Spring 特征:
      - 最低价 < range low
      - 收盘价 >= range low
      - 量能 > 平均
    """
    df = df.copy()
    avg_volume = df["volume"].mean()
    df.loc[df.index[insert_idx], "low"] = break_low
    df.loc[df.index[insert_idx], "close"] = recover_close
    df.loc[df.index[insert_idx], "open"] = break_low * 1.005  # 开盘略高于最低
    df.loc[df.index[insert_idx], "high"] = max(recover_close, break_low) * 1.01
    df.loc[df.index[insert_idx], "volume"] = avg_volume * volume_mult
    return df


class TestSpringSynthetic(unittest.TestCase):
    """合成数据测试 - 验证检测器逻辑正确性"""

    def test_spring_basic_detected(self):
        """场景 1: 经典 Spring - 跌破 + 收回 + 放量 → 应该被检出"""
        df = make_synthetic_ohlc(days=60, base_price=100.0, range_pct=0.10)
        # 在倒数第 5 根 (索引 len-5) 插入 Spring, 确保 lookback=20 有足够数据
        idx = len(df) - 5
        df = insert_spring(df, insert_idx=idx, break_low=92.0, recover_close=99.0, volume_mult=2.5)
        events = detect_spring(df)
        self.assertGreaterEqual(len(events), 1, "Should detect at least 1 Spring")
        e = events[0]
        self.assertEqual(e.type, "Spring")
        self.assertEqual(e.code, "TEST")
        self.assertGreater(e.rvol, 1.5, "RVOL should be > 1.5 for high-volume Spring")

    def test_no_spring_in_flat_market(self):
        """场景 2: 横盘无 Spring → 应该返回空"""
        df = make_synthetic_ohlc(days=60, base_price=100.0, range_pct=0.05)
        events = detect_spring(df)
        self.assertEqual(len(events), 0, "Flat market should yield 0 Springs")

    def test_no_spring_when_collapse_too_deep(self):
        """场景 3: 跌幅 > 5% (崩盘) → 不应被检测"""
        df = make_synthetic_ohlc(days=60, base_price=100.0, range_pct=0.10)
        idx = len(df) - 5
        # 7% 跌幅 (崩盘), 收盘略收回, 大放量 - 不应被检为 Spring
        df = insert_spring(df, insert_idx=idx, break_low=93.0, recover_close=94.0, volume_mult=3.0)
        events = detect_spring(df)
        self.assertEqual(len(events), 0, "Deep collapse >5% should not be Spring")


class TestSpringRealData(unittest.TestCase):
    """真实数据测试 - 验证在 600519 上能跑通"""

    @classmethod
    def setUpClass(cls):
        """加载 600519 真实数据 (可能因网络不可用而跳过)"""
        cls.df = None
        cls.skip_reason = None
        try:
            from wyckoff.data.tencent_source import load_600519
            cls.df = load_600519()
        except FileNotFoundError as e:
            cls.skip_reason = f"数据文件未生成: {e}"
        except Exception as e:
            cls.skip_reason = f"数据加载失败: {e}"

    def test_runs_on_600519(self):
        """在 600519 真实数据上能跑通, 不会抛异常"""
        if self.df is None:
            self.skipTest(self.skip_reason)
        # 只要不抛异常就算过
        events = detect_spring(self.df)
        self.assertIsInstance(events, list)
        print(f"  → 600519 检测到 {len(events)} 个 Spring")


if __name__ == "__main__":
    unittest.main(verbosity=2)
