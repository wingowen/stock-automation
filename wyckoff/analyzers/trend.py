"""趋势技术分析器

从 daily_stock_analysis/src/stock_analyzer.py 移植，
适配 wyckoff 模块的数据格式（date, code, open, high, low, close, volume）。

核心功能：
1. 趋势判断 - MA5>MA10>MA20 多头排列
2. 乖离率检测 - 不追高，偏离 MA5 超过 5% 不买
3. 量能分析 - 偏好缩量回调
4. 买点识别 - 回踩 MA5/MA10 支撑
5. MACD 指标 - 趋势确认和金叉死叉信号
6. RSI 指标 - 超买超卖判断
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class TrendStatus(Enum):
    """趋势状态"""
    STRONG_BULL = "强势多头"
    BULL = "多头排列"
    WEAK_BULL = "弱势多头"
    CONSOLIDATION = "盘整"
    WEAK_BEAR = "弱势空头"
    BEAR = "空头排列"
    STRONG_BEAR = "强势空头"


class VolumeStatus(Enum):
    """量能状态"""
    HEAVY_VOLUME_UP = "放量上涨"
    HEAVY_VOLUME_DOWN = "放量下跌"
    SHRINK_VOLUME_UP = "缩量上涨"
    SHRINK_VOLUME_DOWN = "缩量回调"
    NORMAL = "量能正常"


class MACDStatus(Enum):
    """MACD 状态"""
    GOLDEN_CROSS_ZERO = "零轴上金叉"
    GOLDEN_CROSS = "金叉"
    BULLISH = "多头"
    CROSSING_UP = "上穿零轴"
    CROSSING_DOWN = "下穿零轴"
    BEARISH = "空头"
    DEATH_CROSS = "死叉"


class RSIStatus(Enum):
    """RSI 状态"""
    OVERBOUGHT = "超买"
    STRONG_BUY = "强势买入"
    NEUTRAL = "中性"
    WEAK = "弱势"
    OVERSOLD = "超卖"


class BuySignal(Enum):
    """买入信号"""
    STRONG_BUY = "强烈买入"
    BUY = "买入"
    HOLD = "持有"
    WAIT = "观望"
    SELL = "卖出"
    STRONG_SELL = "强烈卖出"


@dataclass
class TrendResult:
    """趋势分析结果"""
    code: str

    # 趋势判断
    trend_status: TrendStatus = TrendStatus.CONSOLIDATION
    ma_alignment: str = ""
    trend_strength: float = 0.0

    # 均线数据
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: float = 0.0
    current_price: float = 0.0

    # 乖离率
    bias_ma5: float = 0.0
    bias_ma10: float = 0.0
    bias_ma20: float = 0.0

    # 量能
    volume_status: VolumeStatus = VolumeStatus.NORMAL
    volume_ratio_5d: float = 0.0
    volume_trend: str = ""

    # 支撑压力
    support_ma5: bool = False
    support_ma10: bool = False
    resistance_levels: list[float] = field(default_factory=list)
    support_levels: list[float] = field(default_factory=list)

    # MACD
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_bar: float = 0.0
    macd_status: MACDStatus = MACDStatus.BULLISH
    macd_signal: str = ""

    # RSI
    rsi_6: float = 0.0
    rsi_12: float = 0.0
    rsi_24: float = 0.0
    rsi_status: RSIStatus = RSIStatus.NEUTRAL
    rsi_signal: str = ""

    # 信号
    buy_signal: BuySignal = BuySignal.WAIT
    signal_score: int = 0
    signal_reasons: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "trend_status": self.trend_status.value,
            "ma_alignment": self.ma_alignment,
            "trend_strength": self.trend_strength,
            "ma5": self.ma5,
            "ma10": self.ma10,
            "ma20": self.ma20,
            "ma60": self.ma60,
            "current_price": self.current_price,
            "bias_ma5": self.bias_ma5,
            "bias_ma10": self.bias_ma10,
            "bias_ma20": self.bias_ma20,
            "volume_status": self.volume_status.value,
            "volume_ratio_5d": self.volume_ratio_5d,
            "volume_trend": self.volume_trend,
            "support_ma5": self.support_ma5,
            "support_ma10": self.support_ma10,
            "buy_signal": self.buy_signal.value,
            "signal_score": self.signal_score,
            "signal_reasons": self.signal_reasons,
            "risk_factors": self.risk_factors,
            "macd_dif": self.macd_dif,
            "macd_dea": self.macd_dea,
            "macd_bar": self.macd_bar,
            "macd_status": self.macd_status.value,
            "macd_signal": self.macd_signal,
            "rsi_6": self.rsi_6,
            "rsi_12": self.rsi_12,
            "rsi_24": self.rsi_24,
            "rsi_status": self.rsi_status.value,
            "rsi_signal": self.rsi_signal,
        }


class TrendAnalyzer:
    """股票趋势分析器

    基于交易理念实现：
    1. 趋势判断 - MA5>MA10>MA20 多头排列
    2. 乖离率检测 - 不追高，偏离 MA5 超过 5% 不买
    3. 量能分析 - 偏好缩量回调
    4. 买点识别 - 回踩 MA5/MA10 支撑
    5. MACD 指标 - 趋势确认
    6. RSI 指标 - 超买超卖判断
    """

    # 交易参数
    VOLUME_SHRINK_RATIO = 0.7
    VOLUME_HEAVY_RATIO = 1.5
    MA_SUPPORT_TOLERANCE = 0.02
    BIAS_THRESHOLD = 5.0

    # MACD 参数
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9

    # RSI 参数
    RSI_SHORT = 6
    RSI_MID = 12
    RSI_LONG = 24
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30

    def analyze(self, df: pd.DataFrame, code: str) -> TrendResult:
        """分析股票趋势

        Args:
            df: 包含 date, open, high, low, close, volume 的 DataFrame
            code: 股票代码

        Returns:
            TrendResult 分析结果
        """
        result = TrendResult(code=code)

        if df is None or df.empty or len(df) < 20:
            logger.warning("%s 数据不足，无法进行趋势分析", code)
            result.risk_factors.append("数据不足，无法完成分析")
            return result

        df = df.sort_values("date").reset_index(drop=True)

        # 计算均线
        df = self._calculate_mas(df)
        # 计算 MACD 和 RSI
        df = self._calculate_macd(df)
        df = self._calculate_rsi(df)

        # 获取最新数据
        latest = df.iloc[-1]
        result.current_price = float(latest["close"])
        result.ma5 = float(latest["MA5"])
        result.ma10 = float(latest["MA10"])
        result.ma20 = float(latest["MA20"])
        result.ma60 = float(latest.get("MA60", 0))

        self._analyze_trend(df, result)
        self._calculate_bias(result)
        self._analyze_volume(df, result)
        self._analyze_support_resistance(df, result)
        self._analyze_macd(df, result)
        self._analyze_rsi(df, result)
        self._generate_signal(result)

        return result

    def _calculate_mas(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算均线"""
        df = df.copy()
        df["MA5"] = df["close"].rolling(window=5).mean()
        df["MA10"] = df["close"].rolling(window=10).mean()
        df["MA20"] = df["close"].rolling(window=20).mean()
        if len(df) >= 60:
            df["MA60"] = df["close"].rolling(window=60).mean()
        else:
            df["MA60"] = df["MA20"]
        return df

    def _calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算 MACD 指标"""
        df = df.copy()
        ema_fast = df["close"].ewm(span=self.MACD_FAST, adjust=False).mean()
        ema_slow = df["close"].ewm(span=self.MACD_SLOW, adjust=False).mean()
        df["MACD_DIF"] = ema_fast - ema_slow
        df["MACD_DEA"] = df["MACD_DIF"].ewm(span=self.MACD_SIGNAL, adjust=False).mean()
        df["MACD_BAR"] = (df["MACD_DIF"] - df["MACD_DEA"]) * 2
        return df

    def _calculate_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算 RSI 指标（Wilder's EMA 口径）"""
        df = df.copy()
        for period in [self.RSI_SHORT, self.RSI_MID, self.RSI_LONG]:
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            df[f"RSI_{period}"] = rsi.fillna(50)
        return df

    def _analyze_trend(self, df: pd.DataFrame, result: TrendResult) -> None:
        """分析趋势状态"""
        ma5, ma10, ma20 = result.ma5, result.ma10, result.ma20

        if ma5 > ma10 > ma20:
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (prev["MA5"] - prev["MA20"]) / prev["MA20"] * 100 if prev["MA20"] > 0 else 0
            curr_spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0

            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BULL
                result.ma_alignment = "强势多头排列，均线发散上行"
                result.trend_strength = 90
            else:
                result.trend_status = TrendStatus.BULL
                result.ma_alignment = "多头排列 MA5>MA10>MA20"
                result.trend_strength = 75

        elif ma5 > ma10 and ma10 <= ma20:
            result.trend_status = TrendStatus.WEAK_BULL
            result.ma_alignment = "弱势多头，MA5>MA10 但 MA10≤MA20"
            result.trend_strength = 55

        elif ma5 < ma10 < ma20:
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (prev["MA20"] - prev["MA5"]) / prev["MA5"] * 100 if prev["MA5"] > 0 else 0
            curr_spread = (ma20 - ma5) / ma5 * 100 if ma5 > 0 else 0

            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BEAR
                result.ma_alignment = "强势空头排列，均线发散下行"
                result.trend_strength = 10
            else:
                result.trend_status = TrendStatus.BEAR
                result.ma_alignment = "空头排列 MA5<MA10<MA20"
                result.trend_strength = 25

        elif ma5 < ma10 and ma10 >= ma20:
            result.trend_status = TrendStatus.WEAK_BEAR
            result.ma_alignment = "弱势空头，MA5<MA10 但 MA10≥MA20"
            result.trend_strength = 40

        else:
            result.trend_status = TrendStatus.CONSOLIDATION
            result.ma_alignment = "均线缠绕，趋势不明"
            result.trend_strength = 50

    def _calculate_bias(self, result: TrendResult) -> None:
        """计算乖离率"""
        price = result.current_price
        if result.ma5 > 0:
            result.bias_ma5 = (price - result.ma5) / result.ma5 * 100
        if result.ma10 > 0:
            result.bias_ma10 = (price - result.ma10) / result.ma10 * 100
        if result.ma20 > 0:
            result.bias_ma20 = (price - result.ma20) / result.ma20 * 100

    def _analyze_volume(self, df: pd.DataFrame, result: TrendResult) -> None:
        """分析量能"""
        if len(df) < 5:
            return

        latest = df.iloc[-1]
        vol_5d_avg = df["volume"].iloc[-6:-1].mean()

        if vol_5d_avg > 0:
            result.volume_ratio_5d = float(latest["volume"]) / vol_5d_avg

        prev_close = df.iloc[-2]["close"]
        price_change = (latest["close"] - prev_close) / prev_close * 100

        if result.volume_ratio_5d >= self.VOLUME_HEAVY_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_UP
                result.volume_trend = "放量上涨，多头力量强劲"
            else:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_DOWN
                result.volume_trend = "放量下跌，注意风险"
        elif result.volume_ratio_5d <= self.VOLUME_SHRINK_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_UP
                result.volume_trend = "缩量上涨，上攻动能不足"
            else:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_DOWN
                result.volume_trend = "缩量回调，洗盘特征明显"
        else:
            result.volume_status = VolumeStatus.NORMAL
            result.volume_trend = "量能正常"

    def _analyze_support_resistance(self, df: pd.DataFrame, result: TrendResult) -> None:
        """分析支撑压力位"""
        price = result.current_price

        if result.ma5 > 0:
            ma5_dist = abs(price - result.ma5) / result.ma5
            if ma5_dist <= self.MA_SUPPORT_TOLERANCE and price >= result.ma5:
                result.support_ma5 = True
                result.support_levels.append(result.ma5)

        if result.ma10 > 0:
            ma10_dist = abs(price - result.ma10) / result.ma10
            if ma10_dist <= self.MA_SUPPORT_TOLERANCE and price >= result.ma10:
                result.support_ma10 = True
                result.support_levels.append(result.ma10)

        # 近期高点作为阻力
        if len(df) >= 20:
            recent = df.iloc[-20:]
            result.resistance_levels = [float(recent["high"].max())]

        # 近期低点作为支撑
        if len(df) >= 20:
            recent = df.iloc[-20:]
            low = float(recent["low"].min())
            if low not in result.support_levels:
                result.support_levels.append(low)

    def _analyze_macd(self, df: pd.DataFrame, result: TrendResult) -> None:
        """分析 MACD 指标"""
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else latest
        result.macd_dif = float(latest["MACD_DIF"])
        result.macd_dea = float(latest["MACD_DEA"])
        result.macd_bar = float(latest["MACD_BAR"])

        dif = result.macd_dif
        dea = result.macd_dea
        prev_dif = float(prev["MACD_DIF"])
        prev_dea = float(prev["MACD_DEA"])

        if dif > 0 and dea > 0:
            if dif > dea and prev_dif <= prev_dea:
                result.macd_status = MACDStatus.GOLDEN_CROSS_ZERO
                result.macd_signal = "零轴上金叉，强势信号"
            elif dif > dea:
                result.macd_status = MACDStatus.BULLISH
                result.macd_signal = "多头运行，DIF>DEA>0"
            else:
                result.macd_status = MACDStatus.BEARISH
                result.macd_signal = "多头回落，DIF<DEA"
        elif dif < 0 and dea < 0:
            if dif < dea and prev_dif >= prev_dea:
                result.macd_status = MACDStatus.DEATH_CROSS
                result.macd_signal = "死叉，空头信号"
            elif dif < dea:
                result.macd_status = MACDStatus.BEARISH
                result.macd_signal = "空头运行，DIF<DEA<0"
            else:
                result.macd_status = MACDStatus.GOLDEN_CROSS
                result.macd_signal = "零轴下金叉，反弹信号"
        elif dif > 0 and dea < 0:
            result.macd_status = MACDStatus.CROSSING_UP
            result.macd_signal = "DIF 上穿零轴，趋势转多"
        else:
            result.macd_status = MACDStatus.CROSSING_DOWN
            result.macd_signal = "DIF 下穿零轴，趋势转空"

    def _analyze_rsi(self, df: pd.DataFrame, result: TrendResult) -> None:
        """分析 RSI 指标"""
        latest = df.iloc[-1]
        result.rsi_6 = float(latest["RSI_6"])
        result.rsi_12 = float(latest["RSI_12"])
        result.rsi_24 = float(latest["RSI_24"])

        rsi = result.rsi_6  # 用短期 RSI 做主判断
        if rsi >= self.RSI_OVERBOUGHT:
            result.rsi_status = RSIStatus.OVERBOUGHT
            result.rsi_signal = f"超买区域(RSI={rsi:.0f})，注意回调风险"
        elif rsi >= 50:
            result.rsi_status = RSIStatus.STRONG_BUY
            result.rsi_signal = f"强势区域(RSI={rsi:.0f})，多头主导"
        elif rsi > self.RSI_OVERSOLD:
            result.rsi_status = RSIStatus.NEUTRAL
            result.rsi_signal = f"中性区域(RSI={rsi:.0f})，方向不明"
        else:
            result.rsi_status = RSIStatus.OVERSOLD
            result.rsi_signal = f"超卖区域(RSI={rsi:.0f})，关注反弹机会"

    def _generate_signal(self, result: TrendResult) -> None:
        """综合生成买卖信号"""
        score = 50
        reasons = []
        risks = []

        # 趋势评分
        if result.trend_status in (TrendStatus.STRONG_BULL, TrendStatus.BULL):
            score += 20
            reasons.append(f"趋势{result.trend_status.value}")
        elif result.trend_status in (TrendStatus.STRONG_BEAR, TrendStatus.BEAR):
            score -= 20
            risks.append(f"趋势{result.trend_status.value}")

        # 乖离率评分
        if result.bias_ma5 < 0:
            score += 10
            reasons.append(f"回踩MA5(乖离率{result.bias_ma5:.1f}%)")
        elif result.bias_ma5 > self.BIAS_THRESHOLD:
            score -= 10
            risks.append(f"乖离率过大({result.bias_ma5:.1f}%)")

        # 量能评分
        if result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN:
            score += 10
            reasons.append("缩量回调，洗盘特征")
        elif result.volume_status == VolumeStatus.HEAVY_VOLUME_UP:
            score += 5
            reasons.append("放量上涨")
        elif result.volume_status == VolumeStatus.HEAVY_VOLUME_DOWN:
            score -= 15
            risks.append("放量下跌，资金出逃")

        # MACD 评分
        if result.macd_status in (MACDStatus.GOLDEN_CROSS_ZERO, MACDStatus.GOLDEN_CROSS):
            score += 10
            reasons.append(f"MACD {result.macd_status.value}")
        elif result.macd_status == MACDStatus.DEATH_CROSS:
            score -= 10
            risks.append("MACD 死叉")

        # RSI 评分
        if result.rsi_status == RSIStatus.OVERSOLD:
            score += 10
            reasons.append(f"RSI 超卖({result.rsi_6:.0f})")
        elif result.rsi_status == RSIStatus.OVERBOUGHT:
            score -= 5
            risks.append("RSI 超买")

        # 支撑评分
        if result.support_ma5 or result.support_ma10:
            score += 5
            reasons.append("均线支撑位附近")

        result.signal_score = max(0, min(100, score))
        result.signal_reasons = reasons
        result.risk_factors = risks

        # 信号判定
        if score >= 80:
            result.buy_signal = BuySignal.STRONG_BUY
        elif score >= 60:
            result.buy_signal = BuySignal.BUY
        elif score >= 40:
            result.buy_signal = BuySignal.HOLD
        elif score >= 20:
            result.buy_signal = BuySignal.WAIT
        elif score >= 0:
            result.buy_signal = BuySignal.SELL
        else:
            result.buy_signal = BuySignal.STRONG_SELL