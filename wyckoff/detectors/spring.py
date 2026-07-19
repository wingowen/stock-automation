"""Spring (弹簧效应) 检测器 (Phase B 第一个 TDD 闭环)

威科夫 Spring 定义 (来自《威科夫操盘法》第四章):
  1. 处于吸筹阶段 (A-B-C 阶段)
  2. 价格跌破交易区间的支撑位 (range low)
  3. 跌破后 1-3 根 K 线内收回区间内
  4. 量能放大（恐慌抛售特征）

简化版检测规则 (v1):
  - 找到最近 N (默认 20) 根 K 线的 range low
  - 当前 K 最低价 < range low (跌破)
  - 当前 K 收盘价 >= range low (收回)
  - 跌破幅度 < 5% (避免大盘崩盘误报)
  - 当前 K 量能 > 20日均量 * 1.5 (恐慌特征)

注意: 阶段机 (Phase D) 暂未实现，所以不检查 "是否在吸筹阶段"。
       这是 Phase B 的简化版，Phase D 接入后会加严。

详见: SPEC.md §6 形态事件清单
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..schemas import Event


def detect_spring(
    df: pd.DataFrame,
    lookback: int = 20,
    min_break_pct: float = 0.005,   # 至少跌破 0.5%
    max_break_pct: float = 0.05,    # 最多跌破 5%
    rvol_threshold: float = 1.5,    # 量能 > 20日均量 * 1.5
) -> list[Event]:
    """检测 Spring 事件

    Args:
        df: 包含 date, open, high, low, close, volume 列的 DataFrame
        lookback: 回看窗口（用于计算 range low 和均量）
        min_break_pct: 跌破最小幅度 (相对于 range low)
        max_break_pct: 跌破最大幅度 (避免崩盘误报)
        rvol_threshold: 恐慌量能阈值

    Returns:
        Spring Event 列表
    """
    if len(df) < lookback + 1:
        return []

    # 确保按日期排序
    df = df.sort_values("date").reset_index(drop=True)
    if "date" not in df.columns or df["date"].dtype == object:
        df["date"] = pd.to_datetime(df["date"]).dt.date

    code = str(df["code"].iloc[0]) if "code" in df.columns else "?"
    name = str(df["name"].iloc[0]) if "name" in df.columns else code

    events: list[Event] = []

    for i in range(lookback, len(df)):
        window = df.iloc[i - lookback : i]
        current = df.iloc[i]

        range_low = window["low"].min()
        range_high = window["high"].max()
        range_days = lookback
        avg_volume = window["volume"].mean()

        if avg_volume <= 0:
            continue

        cur_low = float(current["low"])
        cur_close = float(current["close"])
        cur_volume = float(current["volume"])
        rvol = cur_volume / avg_volume

        # 规则 1: 最低价跌破 range low
        break_pct = (range_low - cur_low) / range_low
        if break_pct < min_break_pct:
            continue

        # 规则 2: 跌幅不能太大 (避免大盘崩盘)
        if break_pct > max_break_pct:
            continue

        # 规则 3: 收盘价回到区间内
        if cur_close < range_low:
            continue

        # 规则 4: 量能放大
        if rvol < rvol_threshold:
            continue

        # 计算 strength (0-1):
        # 跌破幅度适中 (1-3%) + 量能充足 (>2x)
        strength_break = min(break_pct / 0.03, 1.0)  # 3% 为满分
        strength_rvol = min((rvol - rvol_threshold) / 1.5, 1.0)  # +1.5x 为满分
        strength = round(0.5 * strength_break + 0.5 * strength_rvol, 3)

        # 价格区间有效 (高低差 > 1%)
        if (range_high - range_low) / range_low < 0.01:
            continue

        event = Event(
            date=current["date"],
            code=code,
            name=name,
            type="Spring",
            strength=strength,
            rvol=round(rvol, 3),
            price=cur_close,
            range_high=float(range_high),
            range_low=float(range_low),
            range_days=range_days,
        )
        events.append(event)

    return events
