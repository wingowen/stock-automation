#!/usr/bin/env python3
"""主板全市场威科夫 SOS→LPS 信号扫描器

对 A 股主板全量股票执行盘后增量扫描：
  1. 从 akshare 拉取主板股票清单
  2. 逐只拉取近 days 天日线数据
  3. 计算 MA20 与量比（当日量 / 前5日均量，不含当日）
  4. 检测 SOS→LPS 组合触发（威科夫 Phase D：突破后缩量回踩确认）：
     - SOS（放量突破）：close 创前 20 日新高 且 volume_ratio > 1.5
     - LPS（缩量回踩）：|close - ma20| / ma20 <= 0.02 且 volume_ratio < 0.8
     - 触发 = SOS 后 15 个交易日内首次出现的 LPS；一次 SOS 只匹配一个 LPS
     - 裸 LPS（无前置 SOS）不算信号，宁缺毋滥
  5. 聚合结果写入 wyckoff/scan_results/scan_YYYY-MM-DD.json
  6. 有信号时通过 ntfy 发送聚合推送

用法：
    python -m wyckoff.mainboard_scanner                      # 默认 scan 模式
    python -m wyckoff.mainboard_scanner --dry-run            # 仅打印，不写文件
    python -m wyckoff.mainboard_scanner --days 200           # 用更多历史数据
    python -m wyckoff.mainboard_scanner --limit 10           # 测试用，只扫 10 只
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyckoff.data.base import DataSource
from wyckoff.data.tencent_source import TencentSource
from wyckoff.detectors.spring import detect_spring
from wyckoff.notify import send_ntfy
from wyckoff.supabase_client import SupabaseClient, SupabaseError

logger = logging.getLogger("wyckoff.mainboard_scanner")

# ---------------------------------------------------------------------------
# 常量（与 daily_stock_analysis/filter_and_analysis/scripts/wyckoff_backtest.py 一致）
# ---------------------------------------------------------------------------
DEFAULT_DAYS = 120
DEFAULT_DB_PATH = "wyckoff/data/cache"
SOS_BREAKOUT_WINDOW = 20     # SOS 突破窗口：close > max(close[-20:])
SOS_VOLUME_RATIO_HIGH = 1.5  # SOS 放量阈值：volume_ratio > 1.5
SOS_LPS_WINDOW = 15          # SOS 后 15 个交易日内出现的首次 LPS 才算触发
LPS_PRICE_BAND = 0.02        # |close - ma20| / ma20 <= 0.02
LPS_VOL_RATIO = 0.8          # volume_ratio < 0.8（当日量 / 前5日均量，不含当日）
SLEEP_BETWEEN = 0.5          # 每只之间 sleep（秒），避免 akshare 限流
FLUSH_EVERY = 50             # 攒批阈值：每扫描 N 只向 Supabase flush 一次进度
FLUSH_INTERVAL = 60.0        # 攒批阈值：距上次 flush 超过 N 秒也触发（防超时丢进度）
SPRING_WINDOW = 30           # Spring 标注窗口：LPS 信号日前 N 个交易日内出现过 Spring
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "wyckoff" / "scan_results"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _fetch_mainboard_list() -> list[dict]:
    """拉取主板股票清单：交易所官方源优先，失败 fallback 新浪源。

    交易所官网（akshare stock_info_sh/sz_name_code）对海外 IP 不友好，
    GitHub Actions runner 上会返回非 JSON 内容导致失败（存量问题），
    因此加新浪行情列表源兜底（新浪对海外访问稳定）。
    """
    stocks = _fetch_mainboard_list_exchange()
    if stocks:
        return stocks
    logger.warning("交易所清单拉取失败, fallback 到新浪清单源")
    return _fetch_mainboard_list_sina()


def _fetch_mainboard_list_exchange() -> list[dict]:
    """从交易所官方（经 akshare）拉取沪深主板股票清单。"""
    import akshare as ak
    try:
        df = ak.stock_info_a_code_name()
        stocks = []
        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            name = str(row.get("name", ""))
            # 过滤掉科创板/创业板/北交所，只保留主板（sh/sz 前缀且不以 688/300/8 开头）
            if code.startswith(("600", "601", "603", "605")):
                stocks.append({"code": code, "name": name})
            elif code.startswith(("000", "001", "002", "003")):
                stocks.append({"code": code, "name": name})
        return stocks
    except Exception as e:
        logger.error("拉取主板股票清单失败(交易所源): %s", e)
        return []


def _fetch_mainboard_list_sina() -> list[dict]:
    """从新浪行情列表分页拉取沪深全量，本地过滤出主板。

    接口: Market_Center.getHQNodeData(node=hs_a)，每页 100 条按代码升序，
    返回空列表或不足一页即结束。symbol 前缀区分市场(sh/sz/bj)。
    """
    import requests

    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    url = (
        "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
        "/Market_Center.getHQNodeData"
    )
    stocks: list[dict] = []
    page = 1
    try:
        while True:
            r = s.get(
                url,
                params={"page": page, "num": 100, "sort": "symbol", "asc": 1, "node": "hs_a"},
                timeout=15,
            )
            rows = r.json()
            if not rows:
                break
            for row in rows:
                sym = str(row.get("symbol", ""))
                code = str(row.get("code", ""))
                if sym.startswith("sh") and code.startswith(("600", "601", "603", "605")):
                    stocks.append({"code": code, "name": str(row.get("name", ""))})
                elif sym.startswith("sz") and code.startswith(("000", "001", "002", "003")):
                    stocks.append({"code": code, "name": str(row.get("name", ""))})
            if len(rows) < 100:
                break
            page += 1
            time.sleep(0.2)
    except Exception as e:
        logger.error("拉取主板股票清单失败(新浪源, 已取 %d 只): %s", len(stocks), e)
    if not stocks:
        logger.error("主板股票清单为空(新浪源)")
    return stocks


def _prepare_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算 MA20 与量比指标列。

    量比口径：当日量 / 前5日均量（shift 1，不含当日）；
    前 5 日数据不足或均量为 0（长期停牌）时为 NaN，不满足任何一侧阈值，自然不参与判定。
    """
    df = df.copy()
    df["ma20"] = df["close"].rolling(window=20, min_periods=20).mean()
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(window=5, min_periods=5).mean().shift(1).replace(0, float("nan"))
    return df


def _iso(d: Any) -> str:
    """date 值转 ISO 字符串（兼容 str 输入）。"""
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _detect_sos(df: pd.DataFrame, idx: int) -> bool:
    """判定 idx 是否为 SOS 日（放量突破）。

    条件:
      1. idx 之前有 SOS_BREAKOUT_WINDOW 个交易日（保证有历史）
      2. close[idx] > max(close[idx-window:idx])
      3. vol_ratio[idx] > SOS_VOLUME_RATIO_HIGH
    """
    if idx < SOS_BREAKOUT_WINDOW or idx >= len(df):
        return False
    close = df["close"].iloc[idx]
    if close is None or pd.isna(close) or close <= 0:
        return False
    prev_max = df["close"].iloc[idx - SOS_BREAKOUT_WINDOW:idx].max()
    if pd.isna(prev_max) or prev_max <= 0 or close <= prev_max:
        return False
    vr = df["vol_ratio"].iloc[idx]
    if pd.isna(vr) or vr <= SOS_VOLUME_RATIO_HIGH:
        return False
    return True


def _detect_lps(df: pd.DataFrame, idx: int) -> bool:
    """判定 idx 是否为 LPS 日（缩量回踩 MA20 ±2% 且量比 < 0.8）。"""
    if idx >= len(df):
        return False
    close = df["close"].iloc[idx]
    ma20 = df["ma20"].iloc[idx]
    if close is None or pd.isna(close) or close <= 0:
        return False
    if ma20 is None or pd.isna(ma20) or ma20 <= 0:
        return False
    if abs(close - ma20) / ma20 > LPS_PRICE_BAND:
        return False
    vr = df["vol_ratio"].iloc[idx]
    if pd.isna(vr) or vr >= LPS_VOL_RATIO:
        return False
    return True


def _find_sos_lps_triggers(df: pd.DataFrame) -> list[dict]:
    """扫描所有 SOS→LPS 组合触发时点。

    算法（移植 daily_stock_analysis wyckoff_backtest.find_triggers）：
      遍历交易日找到 SOS 日后，在后续 SOS_LPS_WINDOW 个交易日内寻找第一个 LPS 日，
      找到即记一次触发（触发日 = LPS 日）；一次 SOS 只匹配首个 LPS。

    Returns:
        触发点列表，每个元素为 {"date", "close", "ma20", "vol_ratio", "deviation", "sos_date"}
    """
    n = len(df)
    triggers: list[dict] = []
    i = 0
    while i < n:
        if not _detect_sos(df, i):
            i += 1
            continue
        sos_idx = i
        found_lps = False
        for j in range(sos_idx + 1, min(sos_idx + 1 + SOS_LPS_WINDOW, n)):
            if _detect_lps(df, j):
                close = float(df["close"].iloc[j])
                ma20 = float(df["ma20"].iloc[j])
                triggers.append({
                    "date": _iso(df["date"].iloc[j]),
                    "close": round(close, 2),
                    "ma20": round(ma20, 2),
                    "vol_ratio": round(float(df["vol_ratio"].iloc[j]), 2),
                    "deviation": round(abs(close - ma20) / ma20 * 100, 2),
                    "sos_date": _iso(df["date"].iloc[sos_idx]),
                })
                found_lps = True
                # 跳过已匹配的窗口，从 LPS 日后继续扫描
                i = j + 1
                break
        if not found_lps:
            # SOS 后窗口内无 LPS，从 SOS 日后继续扫描
            i = sos_idx + 1
    return triggers


def _annotate_spring(df: pd.DataFrame, trade_date: str) -> dict:
    """检查 LPS 信号日前 SPRING_WINDOW 个交易日内是否出现过 Spring 事件

    复用 detectors/spring.py 的 detect_spring（跌破支撑→收回→放量），
    只取 trade_date 之前（含当日）且落在窗口内的最新一次。

    Returns:
        {"is_spring": bool, "spring_date": str|None, "spring_strength": float|None}
    """
    df = df[df["date"] <= datetime.strptime(trade_date, "%Y-%m-%d").date()]
    try:
        events = detect_spring(df)
    except Exception as e:
        logger.warning("Spring 检测异常(按无 Spring 处理): %s", e)
        return {"is_spring": False, "spring_date": None, "spring_strength": None}

    in_window = [
        ev for ev in events
        if ev.date.isoformat() <= trade_date
        # 窗口按交易日计：df 已截到 trade_date，取末 SPRING_WINDOW 行的起始日期
        and ev.date.isoformat() >= (
            df["date"].iloc[-SPRING_WINDOW].isoformat()
            if len(df) >= SPRING_WINDOW else df["date"].iloc[0].isoformat()
        )
    ]
    if not in_window:
        return {"is_spring": False, "spring_date": None, "spring_strength": None}
    latest = max(in_window, key=lambda ev: ev.date)
    return {
        "is_spring": True,
        "spring_date": latest.date.isoformat(),
        "spring_strength": latest.strength,
    }


# ---------------------------------------------------------------------------
# 主扫描逻辑
# ---------------------------------------------------------------------------

def run_scan(
    trade_date: str,
    days: int = DEFAULT_DAYS,
    dry_run: bool = False,
    limit: int = 0,
    client: Optional[SupabaseClient] = None,
    source: Optional[DataSource] = None,
    flush_every: int = FLUSH_EVERY,
    flush_interval: float = FLUSH_INTERVAL,
) -> dict[str, Any]:
    """执行全主板 SOS→LPS 组合信号扫描。

    Args:
        trade_date: 交易日 YYYY-MM-DD
        days: 拉取数据天数
        dry_run: 仅打印不写文件
        limit: 限制扫描数量（0 = 全部）
        client: Supabase 客户端；None 表示无持久化（dry-run/测试）
        source: 数据源；None 用默认 TencentSource（项目约定腾讯为主源）
        flush_every: 攒批 flush 的数量阈值
        flush_interval: 攒批 flush 的时间阈值（秒）

    Returns:
        扫描结果字典
    """
    logger.info("=" * 50)
    logger.info("主板 LPS 信号扫描  交易日=%s  days=%d", trade_date, days)
    logger.info("=" * 50)

    # 1. 拉取主板股票清单
    stocks = _fetch_mainboard_list()
    if not stocks:
        logger.error("主板股票清单为空，无法扫描")
        return {"error": "主板股票清单为空", "trade_date": trade_date}

    logger.info("主板股票总数: %d", len(stocks))
    if limit > 0:
        stocks = stocks[:limit]
        logger.info("限制扫描数量: %d", limit)

    # 断点续扫：启动时加载当日已完成集合并登记运行（DB 不可达则抛错 fail fast）
    done_codes: set[str] = set()
    run_id: Optional[int] = None
    if client is not None:
        client.purge_expired()
        done_codes = client.load_done_codes(trade_date)
        run_id = client.insert_run(trade_date, total_stocks=len(stocks))
        if done_codes:
            logger.info("断点续扫: 当日已完成 %d 只, 将跳过", len(done_codes))

    # 2. 逐只扫描（断点续扫：跳过当日 status=done 的股票）
    all_lps: list[dict] = []
    failed_codes: list[str] = []
    progress_buf: list[dict] = []
    signal_buf: list[dict] = []
    degraded = False
    scanned = 0
    skipped = 0
    last_flush = time.monotonic()
    end_dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
    start_dt = end_dt - timedelta(days=days + 30)
    src = source or TencentSource()

    def _flush() -> None:
        """攒批写入进度与信号；失败不中断扫描，标记 degraded"""
        nonlocal degraded, last_flush
        if client is None:
            return
        try:
            if progress_buf:
                client.upsert_progress(progress_buf)
            if signal_buf:
                client.upsert_signals(signal_buf)
            progress_buf.clear()
            signal_buf.clear()
            last_flush = time.monotonic()
        except Exception as e:
            degraded = True
            logger.warning("进度 flush 失败, 继续扫描(标记 degraded): %s", e)

    for i, stock in enumerate(stocks, 1):
        code = stock["code"]
        name = stock.get("name", "")

        if code in done_codes:
            skipped += 1
            continue

        try:
            df = src.fetch(code, start_dt, end_dt)

            # 截取到 trade_date 为止的数据
            df = df[df["date"] <= end_dt]
            if len(df) < 21:
                scanned += 1
                if client is not None:
                    progress_buf.append({"trade_date": trade_date, "code": code, "status": "done"})
                continue

            # 检查最新一天是否为 SOS→LPS 组合触发日（裸 LPS 不算信号）
            triggers = _find_sos_lps_triggers(_prepare_indicators(df))
            latest_trigger = triggers[-1] if triggers else None

            if latest_trigger and latest_trigger["date"] == trade_date:
                spring = _annotate_spring(df, trade_date)
                entry = {
                    "code": code,
                    "name": name,
                    "signal_date": trade_date,
                    "close": latest_trigger["close"],
                    "ma20": latest_trigger["ma20"],
                    "vol_ratio": latest_trigger["vol_ratio"],
                    "deviation_pct": latest_trigger["deviation"],
                    "sos_date": latest_trigger["sos_date"],
                    "days_since_lps": 0,
                    **spring,
                }
                all_lps.append(entry)
                if client is not None:
                    # DB 表结构固定（lps_signals），不新增 sos_date 列，仅写入既有字段
                    signal_buf.append({
                        "trade_date": trade_date,
                        "code": code,
                        "name": name,
                        "close": latest_trigger["close"],
                        "ma20": latest_trigger["ma20"],
                        "vol_ratio": latest_trigger["vol_ratio"],
                        "deviation_pct": latest_trigger["deviation"],
                        "is_spring": spring["is_spring"],
                        "spring_date": spring["spring_date"],
                        "spring_strength": spring["spring_strength"],
                    })
                logger.info("  [LPS] %s %s  close=%.2f ma20=%.2f vol=%.2f sos=%s",
                            code, name, entry["close"], entry["ma20"], entry["vol_ratio"],
                            entry["sos_date"])
            else:
                # 没有当日信号，记录最近一次组合触发距今天数（无历史时用 "-" 占位）
                if triggers:
                    last_trig_date = triggers[-1]["date"]
                    last_dt = datetime.strptime(last_trig_date, "%Y-%m-%d").date()
                    days_since = (end_dt - last_dt).days
                else:
                    last_trig_date = "-"
                    days_since = -1
                logger.debug("  %-6s %s  最近触发=%s %d天前", code, name, last_trig_date, days_since)

            scanned += 1
            if client is not None:
                progress_buf.append({"trade_date": trade_date, "code": code, "status": "done"})
        except Exception as e:
            logger.warning("%s 扫描失败: %s", code, e)
            failed_codes.append(code)
            if client is not None:
                progress_buf.append({"trade_date": trade_date, "code": code, "status": "failed"})

        # 限流：每只之间 sleep（跳过的不请求接口，不 sleep）
        if i < len(stocks):
            time.sleep(SLEEP_BETWEEN)

        # 攒批 flush：达到数量阈值或距上次 flush 超时
        if client is not None and (
            len(progress_buf) >= flush_every
            or time.monotonic() - last_flush >= flush_interval
        ):
            _flush()

        # 进度日志
        if i % 200 == 0 or i == len(stocks):
            logger.info(
                "进度: %d/%d, 信号: %d, 失败: %d, 跳过: %d",
                i, len(stocks), len(all_lps), len(failed_codes), skipped,
            )

    # 收尾 flush：剩余缓冲全部写入
    _flush()

    # 3. 收尾：更新运行登记（flush 失败等场景标记 degraded）
    stats = {
        "status": "degraded" if degraded else "success",
        "scanned_count": scanned,
        "skipped_count": skipped,
        "signal_count": len(all_lps),
        "failed_count": len(failed_codes),
    }
    if client is not None and run_id is not None:
        try:
            client.finish_run(run_id, stats)
        except Exception as e:
            degraded = True
            logger.error("finish_run 失败(运行记录停留在 running): %s", e)

    # 4. 汇总
    result = {
        "trade_date": trade_date,
        "scan_time": datetime.now().isoformat(),
        "total_stocks": len(stocks),
        "scanned_count": scanned,
        "skipped_count": skipped,
        "lps_count": len(all_lps),
        "failed_count": len(failed_codes),
        "degraded": degraded,
        "signals": all_lps,
        "failed_codes": failed_codes,
    }

    logger.info("=" * 50)
    logger.info("扫描完成: 股票=%d  扫描=%d  跳过=%d  信号=%d  失败=%d  降级=%s",
                result["total_stocks"], result["scanned_count"], result["skipped_count"],
                result["lps_count"], result["failed_count"], degraded)
    logger.info("=" * 50)

    # 5. 写文件
    if not dry_run and all_lps:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUT_DIR / f"scan_{trade_date}.json"
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("结果已写入: %s", out_path)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="主板全市场 LPS 信号扫描")
    ap.add_argument("--trade-date", help="交易日 YYYY-MM-DD（默认最近交易日）")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"拉取数据天数（默认 {DEFAULT_DAYS}）")
    ap.add_argument("--limit", type=int, default=0, help="限制扫描数量（0=全部，测试用）")
    ap.add_argument("--dry-run", action="store_true", help="仅打印，不写文件不推送")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[mainboard_scanner] %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    from common.trading_calendar import latest_trade_day
    trade_date = args.trade_date or latest_trade_day().strftime("%Y-%m-%d")
    logger.info("交易日: %s", trade_date)

    # 非 dry-run 必须配置 Supabase（fail fast，不静默降级为无持久化扫描）
    client: Optional[SupabaseClient] = None
    if not args.dry_run:
        try:
            client = SupabaseClient.from_env()
        except SupabaseError as e:
            logger.error("Supabase 未配置: %s", e)
            return 1

    try:
        result = run_scan(
            trade_date=trade_date,
            days=args.days,
            dry_run=args.dry_run,
            limit=args.limit,
            client=client,
        )
    except SupabaseError as e:
        # 启动阶段（purge/加载进度/登记运行）DB 不可达：直接失败并告警
        logger.error("Supabase 启动检查失败, fail fast: %s", e)
        send_ntfy(
            f"LPS 扫描失败 {trade_date}",
            f"Supabase 不可达, 扫描未执行:\n{e}",
            priority="high",
            tags="x",
        )
        return 1

    if "error" in result:
        logger.error(result["error"])
        return 1

    signals = result.get("signals", [])
    if signals:
        # 聚合推送
        header = f"威科夫 LPS 信号 {trade_date}"
        lines = [header, f"共 {len(signals)} 只触发："]
        for s in signals[:15]:
            sos_mark = f" [SOS {s['sos_date']}]"
            spring_mark = (
                f"{sos_mark} [Spring✓ {s['spring_date']}]"
                if s.get("is_spring") else sos_mark
            )
            lines.append(
                f"  {s['code']} {s.get('name', '')}  "
                f"价={s['close']:.2f} MA20={s['ma20']:.2f} "
                f"量比={s['vol_ratio']:.2f} 偏离={s['deviation_pct']:.1f}%"
                f"{spring_mark}"
            )
        if len(signals) > 15:
            lines.append(f"  ... 还有 {len(signals) - 15} 只")
        lines.append("")
        body = "\n".join(lines)
        send_ntfy(header, body, priority="high", tags="chart_with_downwards_trend")
    else:
        logger.info("当日无 LPS 信号，跳过推送")

    # 持久化降级：扫描完成但部分进度未写入，告警并返回非零让 CI 标红
    if result.get("degraded"):
        send_ntfy(
            f"LPS 扫描降级告警 {trade_date}",
            "扫描已完成, 但部分进度未能写入 Supabase, 建议重跑同交易日补齐(断点续扫只补未完成部分)",
            priority="high",
            tags="warning",
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
