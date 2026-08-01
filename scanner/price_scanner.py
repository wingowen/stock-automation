#!/usr/bin/env python3
"""价格触发扫描器主入口

在 GitHub Actions 上定时运行，扫描简报中的触发点 vs 实时价格。
适合 cron 调度（每 5 分钟一次），非交易时段自动跳过。

用法：
    python scanner/price_scanner.py
"""

from __future__ import annotations

import sys
from datetime import datetime, time

from scanner.brief_parser import parse_all_briefs
from scanner.realtime_quote import fetch_quotes
from scanner.alert import notify


def is_trading_time() -> bool:
    """判断当前是否为 A 股交易时段

    交易时段：
      上午: 09:30 - 11:30
      下午: 13:00 - 15:00
    """
    now = datetime.now()
    # 非交易日（周六/周日）跳过
    if now.weekday() >= 5:
        return False

    t = now.time()
    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)

    if morning_start <= t <= morning_end:
        return True
    if afternoon_start <= t <= afternoon_end:
        return True
    return False


def main() -> int:
    """主流程"""
    # 1. 交易时段检查
    if not is_trading_time():
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 非交易时段，跳过")
        return 0

    # 2. 解析简报
    triggers = parse_all_briefs()
    if not triggers:
        print("无可监控的股票（analysis-brief 下无简报文件）")
        return 0

    # 3. 提取需要扫描的股票代码去重
    codes = sorted(set(t.code for t in triggers))
    print(f"监控股票: {', '.join(codes)}  (触发点: {len(triggers)} 个)")

    # 4. 获取实时行情
    quotes = fetch_quotes(codes)
    if not quotes:
        print("获取实时行情失败，跳过本轮")
        return 1

    print(f"获取到 {len(quotes)} 只股票行情")

    # 5. 比对触发条件
    alerts = 0
    for trigger in triggers:
        quote = quotes.get(trigger.code)
        if quote is None:
            continue

        # 买入触发：当前价 <= 支撑位/进场价（允许 1% 的容差）
        if trigger.direction == "buy" and quote.price <= trigger.price_level * 1.01:
            notify(trigger, quote)
            alerts += 1

        # 卖出触发：当前价 >= 阻力位（允许 1% 的容差）
        # 或当前价 <= 止损位（跌破止损）
        if trigger.direction == "sell":
            if quote.price >= trigger.price_level * 0.99:
                notify(trigger, quote)
                alerts += 1
            elif quote.price <= trigger.price_level * 1.01:
                notify(trigger, quote)
                alerts += 1

    if alerts == 0:
        print("无触发条件满足")

    return 0


if __name__ == "__main__":
    sys.exit(main())