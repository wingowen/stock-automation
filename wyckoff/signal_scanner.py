#!/usr/bin/env python3
"""威科夫信号扫描器

从 daily_stock_analysis 移植的综合信号扫描引擎。
结合技术指标分析 + 量能形态检测 + 策略规则判断，
对观察名单中的股票进行多维度扫描，生成结构化信号报告。

用法：
    python -m wyckoff.signal_scanner --watchlist wyckoff-auto/watchlist.json
    python -m wyckoff.signal_scanner --code 002279
    python -m wyckoff.signal_scanner --dry-run --days 120
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

# 确保模块导入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.trading_calendar import latest_trade_day
from wyckoff.analyzers.trend import TrendAnalyzer, TrendResult, TrendStatus, BuySignal
from wyckoff.analyzers.volume import VolumeAnalyzer, VolumeResult, VolumePattern
from wyckoff.detectors.spring import detect_spring
from wyckoff.schemas import Event

logger = logging.getLogger("wyckoff.signal_scanner")


@dataclass
class SignalReport:
    """单只股票的完整信号报告"""
    code: str
    name: str
    trade_date: str
    scan_time: str

    # 基础信息
    current_price: float = 0.0
    price_change_pct: float = 0.0

    # 趋势分析
    trend: Optional[dict] = None

    # 量能分析
    volume: Optional[dict] = None

    # 形态事件
    events: list[dict] = field(default_factory=list)

    # 综合信号
    signal_score: int = 0          # 0-100
    signal_label: str = "等待"     # 强烈买入/买入/持有/观望/卖出
    signal_reasons: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)

    # 威科夫特定
    wyckoff_phase: str = ""        # A/B/C/D/E
    wyckoff_confidence: str = ""   # 高/中/低

    # 建议
    suggestion: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SignalScanner:
    """威科夫信号扫描器

    整合多维度分析：
    1. 趋势分析（均线、MACD、RSI）
    2. 量能分析（底部放量、放量突破、恐慌抛售）
    3. 形态检测（Spring）
    4. 策略评分
    """

    def __init__(self, days: int = 120):
        self.days = days
        self.trend_analyzer = TrendAnalyzer()
        self.volume_analyzer = VolumeAnalyzer()

    def scan_stock(
        self,
        df: pd.DataFrame,
        code: str,
        name: str = "",
        trade_date: str = "",
    ) -> SignalReport:
        """扫描单只股票

        Args:
            df: OHLCV DataFrame
            code: 股票代码
            name: 股票名称
            trade_date: 交易日

        Returns:
            SignalReport
        """
        report = SignalReport(
            code=code,
            name=name or code,
            trade_date=trade_date or date.today().isoformat(),
            scan_time=datetime.now().strftime("%H:%M:%S"),
        )

        if df is None or df.empty:
            report.signal_label = "数据不足"
            report.risk_factors.append("无可用数据")
            return report

        df = df.sort_values("date").reset_index(drop=True)
        latest = df.iloc[-1]
        report.current_price = float(latest["close"])

        if len(df) >= 2:
            prev = df.iloc[-2]
            report.price_change_pct = float(
                (latest["close"] - prev["close"]) / prev["close"] * 100
            )

        # 1. 趋势分析
        trend_result = self._analyze_trend(df, code)
        report.trend = trend_result.to_dict() if trend_result else None

        # 2. 量能分析
        volume_result = self._analyze_volume(df, code)
        report.volume = volume_result.to_dict() if volume_result else None

        # 3. 形态检测
        events = self._detect_patterns(df, code, name)
        report.events = [e.to_dict() if isinstance(e, Event) else e for e in events]

        # 4. 综合评分
        self._score_signal(report, trend_result, volume_result)

        # 5. 威科夫阶段判断
        report.wyckoff_phase = self._infer_wyckoff_phase(trend_result, volume_result, events)

        # 6. 生成建议
        report.suggestion = self._generate_suggestion(report)

        return report

    def _analyze_trend(self, df: pd.DataFrame, code: str) -> Optional[TrendResult]:
        """执行趋势分析"""
        try:
            return self.trend_analyzer.analyze(df, code)
        except Exception as e:
            logger.warning("%s 趋势分析异常: %s", code, e)
            return None

    def _analyze_volume(self, df: pd.DataFrame, code: str) -> Optional[VolumeResult]:
        """执行量能分析"""
        try:
            return self.volume_analyzer.analyze(df, code)
        except Exception as e:
            logger.warning("%s 量能分析异常: %s", code, e)
            return None

    def _detect_patterns(
        self, df: pd.DataFrame, code: str, name: str
    ) -> list[Event | dict]:
        """执行形态检测"""
        events: list[Event | dict] = []
        try:
            springs = detect_spring(df)
            events.extend(springs)
        except Exception as e:
            logger.warning("%s Spring 检测异常: %s", code, e)
        return events

    def _score_signal(
        self,
        report: SignalReport,
        trend: Optional[TrendResult],
        volume: Optional[VolumeResult],
    ) -> None:
        """综合评分"""
        score = 50
        reasons = []
        risks = []

        # 趋势评分
        if trend:
            score = trend.signal_score
            reasons.extend(trend.signal_reasons)
            risks.extend(trend.risk_factors)

        # 量能评分
        if volume:
            if volume.is_bottom_surge:
                score += 15
                reasons.append(f"底部放量(强度{volume.bottom_surge_score:.2f})")
            if volume.is_breakout:
                score += 10
                reasons.append(f"放量突破(强度{volume.breakout_score:.2f})")
            if volume.is_shrink_pullback:
                score += 5
                reasons.append("缩量回调")
            if volume.is_panic_selling:
                score -= 20
                risks.append("恐慌抛售")

        # 形态事件评分
        for evt in report.events:
            if isinstance(evt, dict):
                etype = evt.get("type", "")
                strength = evt.get("strength", 0)
            else:
                etype = evt.type
                strength = evt.strength

            if etype == "Spring":
                score += 15 * strength
                reasons.append(f"Spring(强度{strength:.2f})")

        report.signal_score = max(0, min(100, score))
        report.signal_reasons = reasons
        report.risk_factors = risks

        # 信号标签
        if score >= 80:
            report.signal_label = "强烈买入"
            report.wyckoff_confidence = "高"
        elif score >= 65:
            report.signal_label = "买入"
            report.wyckoff_confidence = "中"
        elif score >= 45:
            report.signal_label = "持有"
            report.wyckoff_confidence = "中"
        elif score >= 25:
            report.signal_label = "观望"
            report.wyckoff_confidence = "低"
        else:
            report.signal_label = "卖出"
            report.wyckoff_confidence = "高"

    def _infer_wyckoff_phase(
        self,
        trend: Optional[TrendResult],
        volume: Optional[VolumeResult],
        events: list,
    ) -> str:
        """推断威科夫阶段"""
        if not trend:
            return ""

        # 检测 Spring → Phase C
        has_spring = any(
            (e.type if isinstance(e, Event) else e.get("type", "")) == "Spring"
            for e in events
        )

        if trend.trend_status in (TrendStatus.STRONG_BULL, TrendStatus.BULL):
            if volume and volume.is_breakout:
                return "D-E (突破/脱离)"
            return "D (确认/上升)"

        if has_spring:
            return "C (终极震仓)"

        if trend.trend_status == TrendStatus.CONSOLIDATION:
            if volume and volume.is_bottom_surge:
                return "A (停止行为)"
            return "B (震荡建仓)"

        if trend.trend_status in (TrendStatus.BEAR, TrendStatus.STRONG_BEAR):
            if volume and volume.is_panic_selling:
                return "A-PS (恐慌抛售)"
            return "下跌趋势中"

        return "方向不明"

    def _generate_suggestion(self, report: SignalReport) -> str:
        """生成操作建议"""
        parts = []

        if report.signal_score >= 65:
            parts.append("可考虑分批建仓")
            if report.wyckoff_phase:
                parts.append(f"当前处于{report.wyckoff_phase}阶段")
        elif report.signal_score >= 45:
            parts.append("持有观察，等待进一步信号确认")
            if report.events:
                parts.append("已有形态信号，关注后续确认")
        elif report.signal_score >= 25:
            parts.append("暂时观望，等待更好时机")
            if report.risk_factors:
                parts.append("注意风险因素")
        else:
            parts.append("风险较高，建议回避")

        if report.risk_factors:
            parts.append(f"风险: {'; '.join(report.risk_factors[:3])}")

        return "。".join(parts)


def _fetch_data(
    code: str,
    days: int,
    trade_date: str,
) -> Optional[pd.DataFrame]:
    """获取股票数据

    尝试使用 wyckoff 数据管道，回退到 akshare 直接拉取。
    """
    try:
        from wyckoff.data.akshare_source import AkShareSource
        src = AkShareSource()
        start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=days)).date()
        end = datetime.strptime(trade_date, "%Y-%m-%d").date()
        return src.fetch(code, start, end)
    except Exception as e:
        logger.warning("%s AkShare 拉取失败: %s", code, e)

    # 回退：通过 akshare 直接拉取
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq",
        )
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "最高": "high",
            "最低": "low", "收盘": "close", "成交量": "volume",
            "股票代码": "code",
        })
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["code"] = code
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
        df["volume"] = df["volume"].astype(int)
        return df[["date", "code", "open", "high", "low", "close", "volume"]]
    except Exception as e:
        logger.error("%s 数据回退拉取也失败: %s", code, e)
        return None


def load_watchlist(path: str) -> list[dict]:
    """加载观察名单"""
    p = Path(path)
    if not p.exists():
        logger.error("观察名单文件不存在: %s", path)
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [s for s in data.get("stocks", []) if s.get("status") == "active"]
    except Exception as e:
        logger.error("观察名单解析失败: %s", e)
        return []


def _notify_results(
    trade_date: str,
    reports: list[SignalReport],
    total: int,
    success: int,
    failed: int,
) -> None:
    """发送 ntfy 通知"""
    from wyckoff.notify import send_ntfy

    header = f"威科夫信号扫描 {trade_date}"
    lines = [header, ""]

    for r in reports:
        emoji = "\U0001f7e2" if r.signal_score >= 65 else "\U0001f7e1" if r.signal_score >= 45 else "\U0001f534"
        name_part = f" {r.name}" if r.name else ""
        lines.append(
            f"{emoji} {r.code}{name_part} [{r.signal_label}] "
            f"评分:{r.signal_score} | {r.current_price:.2f}"
        )
        if r.signal_reasons:
            lines.append(f"   信号: {'; '.join(r.signal_reasons[:2])}")
        if r.wyckoff_phase:
            lines.append(f"   阶段: {r.wyckoff_phase}")

    lines.append("")
    lines.append(f"总计: {total} | 积极: {success} | 负面: {failed}")

    body = "\n".join(lines)
    send_ntfy(header, body, priority="default", tags="chart_with_upwards_trend")


def main() -> int:
    """主入口"""
    ap = argparse.ArgumentParser(description="威科夫信号扫描器")
    ap.add_argument("--watchlist", default="wyckoff-auto/watchlist.json")
    ap.add_argument("--code", help="指定单个股票代码")
    ap.add_argument("--trade-date", help="交易日 YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=120, help="拉取数据天数")
    ap.add_argument("--dry-run", action="store_true", help="仅打印不落盘")
    ap.add_argument("--output", help="输出文件路径")
    args = ap.parse_args()

    # 日志
    logging.basicConfig(
        level=logging.INFO,
        format="[signal_scanner] %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    # 交易日
    td = args.trade_date or latest_trade_day().strftime("%Y-%m-%d")
    logger.info("扫描日期: %s, 数据天数: %d", td, args.days)

    # 股票列表
    if args.code:
        stocks = [{"code": args.code, "name": ""}]
    else:
        stocks = load_watchlist(args.watchlist)

    if not stocks:
        logger.error("无待扫描股票")
        return 1

    logger.info("待扫描股票: %d 只", len(stocks))

    scanner = SignalScanner(days=args.days)
    reports: list[SignalReport] = []

    for stock in stocks:
        code = stock["code"]
        name = stock.get("name", "")
        logger.info("=== 扫描 %s %s ===", code, name)

        df = _fetch_data(code, args.days, td)
        if df is None:
            logger.error("%s 数据拉取失败", code)
            reports.append(SignalReport(
                code=code, name=name,
                trade_date=td, scan_time=datetime.now().strftime("%H:%M:%S"),
            ))
            continue

        try:
            report = scanner.scan_stock(df, code, name, td)
            reports.append(report)

            if args.dry_run:
                print(f"\n{'='*60}")
                print(f"{code} {name} | {report.signal_label} | 评分: {report.signal_score}")
                print(f"  价格: {report.current_price:.2f} ({report.price_change_pct:+.2f}%)")
                if report.signal_reasons:
                    print(f"  信号: {'; '.join(report.signal_reasons)}")
                if report.risk_factors:
                    print(f"  风险: {'; '.join(report.risk_factors)}")
                if report.wyckoff_phase:
                    print(f"  阶段: {report.wyckoff_phase}")
                if report.events:
                    print(f"  形态: {len(report.events)} 个事件")
                print(f"  建议: {report.suggestion}")
        except Exception as e:
            logger.error("%s 扫描异常: %s", code, e)
            traceback.print_exc()
            reports.append(SignalReport(
                code=code, name=name,
                trade_date=td, scan_time=datetime.now().strftime("%H:%M:%S"),
            ))

    # 汇总
    total = len(reports)
    positive = sum(1 for r in reports if r.signal_score >= 65)
    neutral = sum(1 for r in reports if 45 <= r.signal_score < 65)
    negative = sum(1 for r in reports if r.signal_score < 45)
    failed = sum(1 for r in reports if r.signal_score == 0 and not r.signal_reasons)

    logger.info(
        "扫描完成: %d 只 | 积极: %d | 中性: %d | 负面: %d | 失败: %d",
        total, positive, neutral, negative, failed,
    )

    # 输出
    if args.output:
        output = {
            "trade_date": td,
            "scan_time": datetime.now().isoformat(),
            "summary": {"total": total, "positive": positive, "neutral": neutral, "negative": negative, "failed": failed},
            "reports": [r.to_dict() for r in reports],
        }
        Path(args.output).write_text(
            json.dumps(output, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("结果已写入: %s", args.output)

    # 通知
    if not args.dry_run:
        _notify_results(td, reports, total, positive + neutral, negative)

    return 0 if failed < total else 1


if __name__ == "__main__":
    sys.exit(main())
