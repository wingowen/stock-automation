"""量能分析器

从 daily_stock_analysis 的转债策略中提取量能分析逻辑，
专门检测威科夫相关的量能形态：
- 底部放量（Bottom Volume Surge）
- 放量突破（Volume Breakout）
- 缩量回调（Volume Shrink Pullback）
- 恐慌抛售（PS - Panic Selling）
- 无量阴跌（No Volume Decline）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class VolumePattern(Enum):
    """量能形态"""
    BOTTOM_SURGE = "底部放量"         # 长期下跌后放量
    BREAKOUT = "放量突破"             # 突破阻力位放量
    SHRINK_PULLBACK = "缩量回调"      # 回调缩量（好）
    PANIC_SELLING = "恐慌抛售"        # 大跌放量（PS）
    CLIMAX = "高潮放量"               # 上涨高潮（BC）
    NO_VOLUME_DECLINE = "无量阴跌"    # 下跌无量
    NORMAL = "量能正常"


@dataclass
class VolumeResult:
    """量能分析结果"""
    code: str

    # 当前量能
    current_volume: float = 0.0
    vol_5d_avg: float = 0.0
    vol_10d_avg: float = 0.0
    vol_20d_avg: float = 0.0
    volume_ratio_5d: float = 0.0    # 当日量 / 5日均量
    volume_ratio_10d: float = 0.0   # 当日量 / 10日均量
    volume_ratio_20d: float = 0.0   # 当日量 / 20日均量

    # 量能形态
    pattern: VolumePattern = VolumePattern.NORMAL
    pattern_description: str = ""

    # 底部放量检测
    is_bottom_surge: bool = False
    bottom_surge_score: float = 0.0       # 0-1
    decline_from_high: float = 0.0        # 从高点跌幅(%)
    days_since_high: int = 0

    # 放量突破检测
    is_breakout: bool = False
    breakout_score: float = 0.0
    resistance_level: float = 0.0
    breakout_pct: float = 0.0

    # 缩量回调检测
    is_shrink_pullback: bool = False
    shrink_score: float = 0.0
    pullback_pct: float = 0.0

    # 恐慌抛售检测
    is_panic_selling: bool = False
    panic_score: float = 0.0
    price_change_pct: float = 0.0

    # 信号
    signals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "current_volume": self.current_volume,
            "volume_ratio_5d": self.volume_ratio_5d,
            "volume_ratio_10d": self.volume_ratio_10d,
            "volume_ratio_20d": self.volume_ratio_20d,
            "pattern": self.pattern.value,
            "pattern_description": self.pattern_description,
            "is_bottom_surge": self.is_bottom_surge,
            "bottom_surge_score": self.bottom_surge_score,
            "decline_from_high": self.decline_from_high,
            "is_breakout": self.is_breakout,
            "breakout_score": self.breakout_score,
            "resistance_level": self.resistance_level,
            "is_shrink_pullback": self.is_shrink_pullback,
            "shrink_score": self.shrink_score,
            "is_panic_selling": self.is_panic_selling,
            "panic_score": self.panic_score,
            "price_change_pct": self.price_change_pct,
            "signals": self.signals,
            "warnings": self.warnings,
        }


class VolumeAnalyzer:
    """量能分析器

    检测威科夫相关的量能形态：
    - 底部放量：长期下跌后，成交量突然放大 3 倍以上
    - 放量突破：价格突破阻力位时成交量放大 2 倍以上
    - 缩量回调：上涨趋势中回调缩量至 0.7 倍以下
    - 恐慌抛售：大跌 3% 以上同时放量 2 倍以上
    """

    # 底部放量参数
    BOTTOM_SURGE_VOL_RATIO = 3.0       # 放量倍数
    BOTTOM_DECLINE_PCT = 15.0          # 从高点跌幅(%)
    BOTTOM_HIGH_LOOKBACK = 30          # 回看高点天数

    # 放量突破参数
    BREAKOUT_VOL_RATIO = 2.0
    BREAKOUT_LOOKBACK = 20

    # 缩量回调参数
    SHRINK_VOL_RATIO = 0.7
    SHRINK_PRICE_CHANGE = -2.0         # 回调跌幅阈值(%)

    # 恐慌抛售参数
    PANIC_VOL_RATIO = 2.0
    PANIC_PRICE_CHANGE = -3.0

    def analyze(self, df: pd.DataFrame, code: str) -> VolumeResult:
        """分析量能形态

        Args:
            df: 包含 date, open, high, low, close, volume 的 DataFrame
            code: 股票代码

        Returns:
            VolumeResult 分析结果
        """
        result = VolumeResult(code=code)

        if df is None or df.empty or len(df) < 20:
            logger.warning("%s 数据不足，无法进行量能分析", code)
            return result

        df = df.sort_values("date").reset_index(drop=True)
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else latest

        result.current_volume = float(latest["volume"])
        result.vol_5d_avg = float(df["volume"].iloc[-6:-1].mean()) if len(df) >= 6 else 0
        result.vol_10d_avg = float(df["volume"].iloc[-11:-1].mean()) if len(df) >= 11 else 0
        result.vol_20d_avg = float(df["volume"].iloc[-21:-1].mean()) if len(df) >= 21 else 0

        if result.vol_5d_avg > 0:
            result.volume_ratio_5d = result.current_volume / result.vol_5d_avg
        if result.vol_10d_avg > 0:
            result.volume_ratio_10d = result.current_volume / result.vol_10d_avg
        if result.vol_20d_avg > 0:
            result.volume_ratio_20d = result.current_volume / result.vol_20d_avg

        price_change = (latest["close"] - prev["close"]) / prev["close"] * 100
        result.price_change_pct = float(price_change)

        # 按顺序检测各种形态
        self._detect_bottom_surge(df, result)
        self._detect_breakout(df, result)
        self._detect_shrink_pullback(df, result)
        self._detect_panic_selling(df, result)

        # 确定主要形态
        self._determine_pattern(result)

        return result

    def _detect_bottom_surge(self, df: pd.DataFrame, result: VolumeResult) -> None:
        """检测底部放量

        条件：
        1. 从 20 日高点到近期低点跌幅 > 15%
        2. 当日成交量 > 5 日均量的 3 倍
        3. K线收阳（收盘价 > 开盘价）
        """
        if len(df) < self.BOTTOM_HIGH_LOOKBACK:
            return

        latest = df.iloc[-1]
        lookback = df.iloc[-self.BOTTOM_HIGH_LOOKBACK:]

        high_20d = float(lookback["high"].max())
        recent_low = float(lookback["low"].min())

        if high_20d <= 0:
            return

        result.decline_from_high = (high_20d - recent_low) / high_20d * 100

        # 找高点位置
        high_idx = lookback["high"].idxmax()
        if isinstance(high_idx, int):
            result.days_since_high = len(df) - 1 - high_idx

        # 条件 1: 跌幅足够
        if result.decline_from_high < self.BOTTOM_DECLINE_PCT:
            return

        # 条件 2: 放量 3 倍
        if result.volume_ratio_5d < self.BOTTOM_SURGE_VOL_RATIO:
            return

        # 条件 3: 收阳
        if float(latest["close"]) <= float(latest["open"]):
            return

        # 计算强度
        vol_score = min(result.volume_ratio_5d / 5.0, 1.0)
        decline_score = min(result.decline_from_high / 30.0, 1.0)
        result.bottom_surge_score = round(0.5 * vol_score + 0.5 * decline_score, 3)
        result.is_bottom_surge = True

        result.signals.append(
            f"底部放量(跌幅{result.decline_from_high:.1f}%, "
            f"量比{result.volume_ratio_5d:.1f}x)"
        )

    def _detect_breakout(self, df: pd.DataFrame, result: VolumeResult) -> None:
        """检测放量突破

        条件：
        1. 收盘价站上 20 日最高点
        2. 成交量 > 5 日均量的 2 倍
        """
        if len(df) < self.BREAKOUT_LOOKBACK + 1:
            return

        latest = df.iloc[-1]
        lookback = df.iloc[-self.BREAKOUT_LOOKBACK:-1]  # 不含当日
        resistance = float(lookback["high"].max())

        result.resistance_level = resistance

        # 条件 1: 突破阻力位
        close = float(latest["close"])
        if close <= resistance:
            return

        result.breakout_pct = (close - resistance) / resistance * 100

        # 条件 2: 放量 2 倍
        if result.volume_ratio_5d < self.BREAKOUT_VOL_RATIO:
            return

        vol_score = min(result.volume_ratio_5d / 4.0, 1.0)
        price_score = min(result.breakout_pct / 5.0, 1.0)
        result.breakout_score = round(0.5 * vol_score + 0.5 * price_score, 3)
        result.is_breakout = True

        result.signals.append(
            f"放量突破(突破{result.breakout_pct:.1f}%, "
            f"量比{result.volume_ratio_5d:.1f}x)"
        )

    def _detect_shrink_pullback(self, df: pd.DataFrame, result: VolumeResult) -> None:
        """检测缩量回调

        条件：
        1. 价格下跌（收盘价 < 前一日收盘价）
        2. 成交量 < 5 日均量的 0.7 倍
        """
        if len(df) < 6:
            return

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        price_change = (latest["close"] - prev["close"]) / prev["close"] * 100
        result.pullback_pct = float(price_change)

        if price_change >= 0:
            return

        if result.volume_ratio_5d > self.SHRINK_VOL_RATIO:
            return

        vol_score = 1.0 - min(result.volume_ratio_5d / self.SHRINK_VOL_RATIO, 1.0)
        result.shrink_score = round(vol_score, 3)
        result.is_shrink_pullback = True

        result.signals.append(
            f"缩量回调(跌{abs(price_change):.1f}%, "
            f"量比{result.volume_ratio_5d:.2f}x)"
        )

    def _detect_panic_selling(self, df: pd.DataFrame, result: VolumeResult) -> None:
        """检测恐慌抛售 (PS)

        条件：
        1. 价格大跌 3% 以上
        2. 成交量放大 2 倍以上
        """
        if result.price_change_pct >= self.PANIC_PRICE_CHANGE:
            return

        if result.volume_ratio_5d < self.PANIC_VOL_RATIO:
            return

        vol_score = min(result.volume_ratio_5d / 4.0, 1.0)
        price_score = min(abs(result.price_change_pct) / 7.0, 1.0)
        result.panic_score = round(0.5 * vol_score + 0.5 * price_score, 3)
        result.is_panic_selling = True

        result.warnings.append(
            f"恐慌抛售(跌{abs(result.price_change_pct):.1f}%, "
            f"量比{result.volume_ratio_5d:.1f}x)"
        )

    def _determine_pattern(self, result: VolumeResult) -> None:
        """确定主要量能形态"""
        if result.is_panic_selling:
            result.pattern = VolumePattern.PANIC_SELLING
            result.pattern_description = "恐慌抛售，注意短期风险"
        elif result.is_bottom_surge:
            result.pattern = VolumePattern.BOTTOM_SURGE
            result.pattern_description = "底部放量，关注反转机会"
        elif result.is_breakout:
            result.pattern = VolumePattern.BREAKOUT
            result.pattern_description = "放量突破，趋势延续信号"
        elif result.is_shrink_pullback:
            result.pattern = VolumePattern.SHRINK_PULLBACK
            result.pattern_description = "缩量回调，洗盘特征"
        else:
            result.pattern = VolumePattern.NORMAL
            result.pattern_description = "量能正常"