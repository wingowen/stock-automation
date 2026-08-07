#!/usr/bin/env python3
"""沪金主力(AU0) 日线抓取 + 威科夫分析格式化输出

数据源: 新浪财经 内部期货日线接口 (沪金主力连续 AU0)
        完整 OHLCV + 持仓量, 适合威科夫量价分析

用法:
    python fetch_gold_kline.py [背景月数] [本周一日期YYYY-MM-DD]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta

import os
from pathlib import Path

import pandas as pd
import requests

SINA_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var_AU0=/InnerFuturesNewService.getDailyKLine?symbol=AU0"
)
# 本地缓存（由 curl 拉取，规避 sandbox 代理差异）
LOCAL_CACHE = Path(__file__).parent / "gold_au0_raw.txt"


def fetch_au0() -> pd.DataFrame:
    """拉取沪金主力连续 AU0 全部日线

    优先读本地缓存 gold_au0_raw.txt；不存在则走网络（使用系统代理）。
    """
    if LOCAL_CACHE.exists():
        text = LOCAL_CACHE.read_text(encoding="utf-8")
    else:
        s = requests.Session()
        # sandbox 有 HTTP_PROXY, 需信任环境变量走代理
        r = s.get(SINA_URL, timeout=30)
        r.raise_for_status()
        text = r.text
    # 形如 var_AU0=([{...},{...}])
    m = re.search(r"var_AU0=\((\[.*\])\)", text, re.S)
    if not m:
        raise RuntimeError(f"无法解析新浪响应, 前200字: {text[:200]}")
    rows = json.loads(m.group(1))
    df = pd.DataFrame(rows)
    # 字段: d/o/h/l/c/v/p/s
    df = df.rename(columns={"d": "date", "o": "open", "h": "high",
                            "l": "low", "c": "close", "v": "volume",
                            "p": "open_interest", "s": "settle"})
    for c in ["open", "high", "low", "close", "settle"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").round(2)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64").astype(int)
    df["open_interest"] = pd.to_numeric(df["open_interest"], errors="coerce").astype("Int64").astype(int)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "settle",
               "volume", "open_interest"]]


def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """日线聚合成周线 (周一为周首)"""
    d = df.copy()
    d["week"] = pd.to_datetime(d["date"]).dt.to_period("W-MON").apply(lambda p: p.start_time.date())
    w = d.groupby("week").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), settle=("settle", "last"),
        volume=("volume", "sum"), open_interest=("open_interest", "last"),
        days=("date", "count"),
    ).reset_index().rename(columns={"week": "date"})
    return w


def enrich(row, prev_close=None):
    rng = row["high"] - row["low"]
    chg = row["close"] - row["open"]
    pct = (chg / row["open"] * 100) if row["open"] else 0.0
    amp = (rng / row["open"] * 100) if row["open"] else 0.0
    close_pos = (row["close"] - row["low"]) / rng if rng > 0 else 0.5
    return dict(open=row["open"], high=row["high"], low=row["low"], close=row["close"],
                volume=row["volume"], chg=chg, pct=pct, amp=amp, close_pos=close_pos,
                prev_close=prev_close)


def print_block(title, df, with_oi=False):
    print("=" * 78)
    print(title)
    print("=" * 78)
    for _, row in df.iterrows():
        e = enrich(row)
        extra = ""
        if with_oi and "open_interest" in row:
            extra = f" OI:{row['open_interest']:>8,}"
        pc = f" prevC:{e['prev_close']:.2f}" if e["prev_close"] else ""
        print(f"{row['date']} | O:{e['open']:7.2f} H:{e['high']:7.2f} "
              f"L:{e['low']:7.2f} C:{e['close']:7.2f} | Vol:{e['volume']:>10,}{extra} | "
              f"涨跌:{e['chg']:+.2f}({e['pct']:+.2f}%) 振幅:{e['amp']:.2f}% 收位:{e['close_pos']:.2f}{pc}")


def main():
    bg_months = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    week_monday_str = sys.argv[2] if len(sys.argv) > 2 else None
    if week_monday_str:
        week_monday = datetime.strptime(week_monday_str, "%Y-%m-%d").date()
    else:
        today = date.today()
        week_monday = today - timedelta(days=today.weekday())
    week_friday = week_monday + timedelta(days=4)

    df = fetch_au0()
    print(f"品种: 沪金主力 AU0 (上海期货交易所)  本周: {week_monday} ~ {week_friday}")
    print(f"数据范围: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}  共 {len(df)} 个交易日")
    print()

    # 最近价格
    last = df.iloc[-1]
    print(f"最新日线: {last['date']}  收:{last['close']:.2f}  "
          f"高:{last['high']:.2f}  低:{last['low']:.2f}  量:{last['volume']:,}  "
          f"持仓:{last['open_interest']:,}")
    print()

    # 切分
    week_mask = (df["date"] >= week_monday) & (df["date"] <= week_friday)
    week_df = df[week_mask].copy()
    bg_start = week_monday - timedelta(days=bg_months * 30)
    bg_df = df[(df["date"] >= bg_start) & (df["date"] < week_monday)].copy()

    # 本周日线
    if len(week_df) > 0:
        prev_map = {}
        prev_idx = df.index[df["date"] < week_monday]
        if len(prev_idx) > 0:
            prev_map[week_df["date"].iloc[0]] = df.loc[prev_idx[-1], "close"]
        print_block(f"本周 ({week_monday} ~ {week_friday}) 日线", week_df, with_oi=True)
    else:
        print(f"本周 ({week_monday} ~ {week_friday}) 暂无数据 (可能尚未开盘/已收盘)")
    print()

    # 近 N 个月背景 (日线)
    if len(bg_df) > 0:
        print_block(f"近{bg_months}个月背景日线 (尾部40行)", bg_df.tail(40), with_oi=True)
    print()

    # 周线 (近16周)
    wk = to_weekly(df)
    wk_recent = wk.tail(16).copy()
    print_block("近16周周线 (中期背景)", wk_recent)
    print()

    # 统计
    print("=" * 78)
    print("统计分析")
    print("=" * 78)
    if len(week_df) > 0:
        wk_o, wk_c = week_df["open"].iloc[0], week_df["close"].iloc[-1]
        wk_h, wk_l = week_df["high"].max(), week_df["low"].min()
        print(f"本周 开:{wk_o:.2f} 收:{wk_c:.2f} 高:{wk_h:.2f} 低:{wk_l:.2f}")
        print(f"本周 涨跌:{wk_c-wk_o:+.2f} ({(wk_c-wk_o)/wk_o*100:+.2f}%)")
        print(f"本周 振幅:{wk_h-wk_l:.2f} ({(wk_h-wk_l)/wk_o*100:.2f}%)")
        print(f"本周 均量:{week_df['volume'].mean():,.0f} 总量:{week_df['volume'].sum():,}")
    if len(bg_df) >= 5:
        bg_v = bg_df["volume"].mean()
        print(f"\n背景期({bg_months}月) 日均量:{bg_v:,.0f}  高点:{bg_df['high'].max():.2f}  低点:{bg_df['low'].min():.2f}")
        if len(week_df) > 0 and bg_v:
            print(f"本周/背景 量比:{week_df['volume'].mean()/bg_v:.2f}")
        print(f"背景期 持仓: {bg_df['open_interest'].iloc[0]:,} -> {bg_df['open_interest'].iloc[-1]:,} "
              f"(变化 {bg_df['open_interest'].iloc[-1]-bg_df['open_interest'].iloc[0]:+,d})")

    # 最近30日
    print()
    print_block("最近30个交易日明细", df.tail(30), with_oi=True)


if __name__ == "__main__":
    main()
