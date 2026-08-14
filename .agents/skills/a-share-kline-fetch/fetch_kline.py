#!/usr/bin/env python3
"""A股K线数据获取 + 威科夫分析格式化输出

数据源：腾讯财经 K 线接口（前复权日线）
依赖：pandas + requests（由 setup_env.sh 自动准备）

用法：
    python fetch_kline.py <code> [背景月数] [本周一日期YYYY-MM-DD]

示例：
    # 默认：取6个月背景 + 自动识别本周
    python fetch_kline.py 002279

    # 取3个月背景 + 指定本周一日期
    python fetch_kline.py 600519 3 2026-07-27

输出（stdout）：
    - 本周日线数据（含威科夫维度：振幅、收位）
    - 近背景期数据
    - 统计摘要（涨跌幅、振幅、量比、高低点）
    - 最近30个交易日明细
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# ── 常量 ────────────────────────────────────────────────────
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SCRIPT_DIR = Path(__file__).parent.resolve()
PYTHON_PATH_FILE = SCRIPT_DIR / ".python_path"


def resolve_exchange(code: str) -> str:
    """根据6位代码推断交易所前缀（沪/深，不处理北交所）"""
    if code.startswith("6"):
        return "sh"
    return "sz"


def fetch_kline(code: str, start: str, end: str, adjust: str = "qfq") -> tuple[pd.DataFrame, str]:
    """调用腾讯 K 线接口拉取前复权日线

    Returns:
        (DataFrame, stock_name): 数据 + 股票名称（取不到名称时为空串）
    """
    exchange = resolve_exchange(code)
    symbol = f"{exchange}{code}"

    days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days + 1
    count = max(days, 640)
    param = f"{symbol},day,{start},{end},{count},{adjust}"

    session = requests.Session()
    # 沙箱环境通过 HTTP(S)_PROXY 代理访问外网；设置 KLINE_SKIP_PROXY=1 可绕过（如 macOS 直连场景）
    session.trust_env = os.environ.get("KLINE_SKIP_PROXY", "") != "1"

    r = session.get(TENCENT_KLINE_URL, params={"param": param}, timeout=20)
    r.raise_for_status()
    data = r.json()

    stock_data = data.get("data", {}).get(symbol, {})
    raw_rows = stock_data.get(f"{adjust}day") or stock_data.get("day")

    if not raw_rows:
        print(f"[ERROR] 无数据返回，原始响应前500字符：", file=sys.stderr)
        print(json.dumps(data, ensure_ascii=False)[:500], file=sys.stderr)
        sys.exit(2)

    # 从 qt 字段提取股票名称（qt[symbol][1] 为名称）
    stock_name = ""
    qt = stock_data.get("qt") or {}
    if isinstance(qt, dict):
        qt_list = qt.get(symbol)
        if isinstance(qt_list, list) and len(qt_list) > 1:
            stock_name = str(qt_list[1]) if qt_list[1] else ""

    # 腾讯接口可能在某些行（除权除息日）多返回一个分红信息字典，只取前6列
    raw_rows = [row[:6] if len(row) > 6 else row for row in raw_rows]

    df = pd.DataFrame(raw_rows, columns=["date", "open", "close", "high", "low", "volume"])
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").round(2)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64").astype(int)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df, stock_name


def enrich_row(row: pd.Series, prev_close: float | None = None) -> dict:
    """计算威科夫分析需要的单行维度"""
    rng = row["high"] - row["low"]
    chg = row["close"] - row["open"]
    pct = (chg / row["open"] * 100) if row["open"] else 0.0
    amp = (rng / row["open"] * 100) if row["open"] else 0.0
    close_pos = (row["close"] - row["low"]) / rng if rng > 0 else 0.5
    return {
        "date": row["date"],
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "volume": row["volume"],
        "chg": chg,
        "pct": pct,
        "amp": amp,
        "close_pos": close_pos,
        "prev_close": prev_close,
    }


def print_block(title: str, df: pd.DataFrame, prev_close_map: dict | None = None) -> None:
    """打印一个数据块"""
    print("=" * 70)
    print(title)
    print("=" * 70)
    prev_close_map = prev_close_map or {}
    for _, row in df.iterrows():
        prev = prev_close_map.get(row["date"])
        e = enrich_row(row, prev)
        print(
            f"{e['date']} | O:{e['open']:6.2f} H:{e['high']:6.2f} L:{e['low']:6.2f} C:{e['close']:6.2f} | "
            f"Vol:{e['volume']:>10,} | 涨跌:{e['chg']:+.2f}({e['pct']:+.2f}%) 振幅:{e['amp']:.2f}% 收位:{e['close_pos']:.2f}"
        )


def print_summary(week_df: pd.DataFrame, bg_df: pd.DataFrame, code: str) -> None:
    """打印统计分析"""
    print()
    print("=" * 70)
    print("统计分析")
    print("=" * 70)

    if len(week_df) > 0:
        wk_high = week_df["high"].max()
        wk_low = week_df["low"].min()
        wk_vol_avg = week_df["volume"].mean()
        wk_vol_sum = week_df["volume"].sum()
        wk_open = week_df["open"].iloc[0]
        wk_close = week_df["close"].iloc[-1]
        wk_chg = wk_close - wk_open
        wk_pct = (wk_chg / wk_open * 100) if wk_open else 0.0
        print(f"本周 开:{wk_open:.2f} 收:{wk_close:.2f} 高:{wk_high:.2f} 低:{wk_low:.2f}")
        print(f"本周 涨跌:{wk_chg:+.2f} ({wk_pct:+.2f}%)")
        print(f"本周 振幅:{(wk_high-wk_low):.2f} ({(wk_high-wk_low)/wk_open*100:.2f}%)")
        print(f"本周 均量:{wk_vol_avg:,.0f} 总量:{wk_vol_sum:,}")

    if len(bg_df) > 0:
        bg_pre = bg_df[bg_df["date"] < week_df["date"].iloc[0]] if len(week_df) > 0 else bg_df
        if len(bg_pre) > 0:
            recent_vol = bg_pre["volume"].mean()
            print(f"\n背景期日均量:{recent_vol:,.0f}")
            if len(week_df) > 0:
                vol_ratio = week_df["volume"].mean() / recent_vol if recent_vol else 0
                print(f"本周日均量:{week_df['volume'].mean():,.0f} 量比:{vol_ratio:.2f}")
        print(f"\n背景期 高点:{bg_df['high'].max():.2f} 低点:{bg_df['low'].min():.2f}")


def get_monday(today: date | None = None) -> date:
    """返回本周一的日期"""
    today = today or date.today()
    return today - timedelta(days=today.weekday())


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    code = sys.argv[1].strip()
    bg_months = int(sys.argv[2]) if len(sys.argv) >= 3 else 6
    week_monday_str = sys.argv[3] if len(sys.argv) >= 4 else None

    # 计算日期范围
    if week_monday_str:
        week_monday = datetime.strptime(week_monday_str, "%Y-%m-%d").date()
    else:
        week_monday = get_monday()
    week_friday = week_monday + timedelta(days=4)

    bg_start = (week_monday - timedelta(days=bg_months * 30)).strftime("%Y-%m-%d")
    bg_end = week_friday.strftime("%Y-%m-%d")

    # 拉取
    df, stock_name = fetch_kline(code, bg_start, bg_end)

    print(f"股票: {code}{('  ' + stock_name) if stock_name else ''}  本周: {week_monday} ~ {week_friday}  背景: {bg_start} ~ {bg_end}")
    if stock_name:
        print(f"STOCK_NAME={stock_name}")
    print()
    print(f"总行数: {len(df)}  日期范围: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
    print()

    # 切分本周 / 背景
    week_mask = (df["date"] >= week_monday) & (df["date"] <= week_friday)
    week_df = df[week_mask].copy()

    bg_df = df[df["date"] < week_monday].copy()

    # 输出本周
    if len(week_df) > 0:
        print_block(f"本周 ({week_monday} ~ {week_friday}) 日线数据", week_df)
    else:
        print(f"本周 ({week_monday} ~ {week_friday}) 暂无数据")
    print()

    # 输出背景
    if len(bg_df) > 0:
        print_block(f"近{bg_months}个月背景数据", bg_df)

    # 统计
    print_summary(week_df, df, code)

    # 最近30个交易日明细
    print()
    recent30 = df.tail(30)
    print_block("最近30个交易日完整数据", recent30)


if __name__ == "__main__":
    main()
