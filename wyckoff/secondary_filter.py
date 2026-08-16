"""威科夫 LPS 信号二次过滤模块（扫描后二次过滤）

消费 mainboard_scanner 产出的 scan_<date>.json，对当日信号子集重拉 OHLCV，
执行硬过滤（H2/H3/H4）+ 四维加权评分（I1/I2/I4/I7）+ A/B/C 分档与数量控制，
产出 filtered_<date>.json 并推送 A/B 档（ntfy）。

设计文档: docs/specs/2026-08-16-wyckoff-secondary-filter-action-spec.md（v0.2）
阈值口径: spec §4.5（推荐默认值，投产前需回测校准）
降级规则: spec §5.5（旧 JSON 缺字段 / 重拉失败 → 中性分降级，不剔除不报错）
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wyckoff.data.tencent_source import TencentSource
from wyckoff.notify import send_ntfy

logger = logging.getLogger("wyckoff.secondary_filter")

# ---------------------------------------------------------------------------
# 常量（阈值依据 spec §4.5，均为推荐默认值，投产前需回测校准）
# ---------------------------------------------------------------------------
DEFAULT_MAX_POOL = 15          # A 档主池上限，超出按综合分降序截断
GRADE_A = 7.5                  # A 档下界（含）
GRADE_B = 6.0                  # B 档下界（含）
W_I1, W_I2, W_I4, W_I7 = 0.317, 0.286, 0.238, 0.159  # 归一化权重（和=1.0）

H3A_SOS_VOL_RATIO = 1.2        # H3a: SOS 棒量 / MA20_VOL 下限（含）
H3B_LPS_VOL_RATIO = 1.5        # H3b: LPS 棒量 / MA20_VOL 上限（含）
H4_MIN_AVG_AMOUNT = 50_000_000.0   # H4: 20 日均成交额下限（元，近似口径）

NEUTRAL_SCORE = 5.0            # 缺失维度中性分
TREND_NEUTRAL = 2.5            # I7 子项（趋势/回撤各满分 5）缺失中性分
DEGRADED_WARN_RATIO = 0.30     # 数据降级标的占比超过该值 → 顶层 degraded
FETCH_RETRY = 1                # 重拉失败重试次数
SLEEP_BETWEEN = 0.5            # 重试间隔（秒），与扫描层一致
DEFAULT_FETCH_DAYS = 200       # 拉取日历日数（≈120+ 交易日，满足 MA120/drawdown120）
DRAWDOWN_WINDOW = 120          # 回撤窗口（交易日）
DEMAND_WINDOW = 10             # 供求偏度窗口（交易日）
DEMAND_INF_CAP = 99.0          # 跌日量为 0 时的比值封顶

BASE_DIR = Path(__file__).resolve().parents[1]
INPUT_DIR = BASE_DIR / "wyckoff" / "scan_results"
OUTPUT_DIR = BASE_DIR / "wyckoff" / "filtered_results"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SCAN_FILE_RE = re.compile(r"^scan_(\d{4}-\d{2}-\d{2})\.json$")


class FilterError(Exception):
    """二次过滤模块级异常（输入非法等不可降级错误）"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _parse_date(s: str) -> date:
    """校验并解析 YYYY-MM-DD 日期串，非法时抛 FilterError。"""
    if not _DATE_RE.match(s):
        raise FilterError(f"日期格式非法（应为 YYYY-MM-DD）: {s!r}")
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise FilterError(f"日期无效: {s!r}") from e


def _to_date(v: Any) -> date:
    """df 的 date 单元格值转 date（兼容 date/str/Timestamp）。"""
    if isinstance(v, date):
        return v
    return pd.to_datetime(v).date()


def _round(v: Optional[float], nd: int = 2) -> Optional[float]:
    return None if v is None else round(float(v), nd)


# ---------------------------------------------------------------------------
# 输入加载（spec §5 / §5.5 A）
# ---------------------------------------------------------------------------
def _load_scan_input(trade_date: Optional[str]) -> dict:
    """加载扫描结果 JSON。

    Args:
        trade_date: 指定交易日；None 时自动取 scan_results 下最新一份。

    Returns:
        {"status": "empty"} 表示无输入（安静退出 0）；
        否则返回 scan JSON 全文（附 "_input_file" 键）。
    """
    if not INPUT_DIR.exists():
        return {"status": "empty"}

    if trade_date is not None:
        path = INPUT_DIR / f"scan_{trade_date}.json"
        if not path.exists():
            return {"status": "empty"}
    else:
        dated = []
        for p in INPUT_DIR.glob("scan_*.json"):
            m = _SCAN_FILE_RE.match(p.name)
            if m:
                dated.append((m.group(1), p))
        if not dated:
            return {"status": "empty"}
        _, path = max(dated, key=lambda x: x[0])

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise FilterError(f"读取扫描结果失败 {path.name}: {e}") from e
    data["_input_file"] = path.name
    return data


def _detect_missing_fields(sig: dict) -> list[str]:
    """检测旧 scan JSON 缺失的关键字段（spec §5.5 A）。

    - sos_date 缺失 → 无法定位 SOS 棒（I1 走中性分，H3a 跳过）；
    - is_spring 键缺失 → 旧文件格式；
    - is_spring=False 且 spring_date/spring_strength 为 None → 新格式正常态，不算缺失；
    - is_spring=True 但 Spring 子字段为 None → 数据不一致，记缺失。
    """
    missing: list[str] = []
    if sig.get("sos_date") is None:
        missing.append("sos_date")
    if sig.get("is_spring") is None:
        missing.append("is_spring")
    elif sig["is_spring"]:
        if sig.get("spring_date") is None:
            missing.append("spring_date")
        if sig.get("spring_strength") is None:
            missing.append("spring_strength")
    return missing


# ---------------------------------------------------------------------------
# 特征自算（spec §5"需自算"表；只允许使用 signal_date 及之前的数据）
# ---------------------------------------------------------------------------
def _trend_state(df: pd.DataFrame) -> Optional[float]:
    """I7 趋势状态分（0–5，spec §4.5）。

    MA60 走平（5 日斜率 |Δ|≤0.5%）→ 4；
    MA60 上行且 close ≥ MA60 → 5（上行但 close < MA60 记 3）；
    MA60 下行：close ≥ MA120 → 3，否则 → 1；
    数据不足（MA60/MA120 无法计算）→ None（中性）。
    """
    close = df["close"]
    if len(close) < 65:  # MA60 + 5 根斜率窗口
        return None
    ma60 = close.rolling(60).mean()
    ma_now, ma_prev = ma60.iloc[-1], ma60.iloc[-6]
    if pd.isna(ma_now) or pd.isna(ma_prev) or ma_prev <= 0:
        return None
    slope = ma_now / ma_prev - 1
    c = close.iloc[-1]
    if abs(slope) <= 0.005:
        return 4.0
    if slope > 0.005:
        return 5.0 if c >= ma_now else 3.0
    # 下行
    ma120 = close.rolling(120).mean().iloc[-1]
    if pd.isna(ma120):
        return None
    return 3.0 if c >= ma120 else 1.0


def _derive_features(df: pd.DataFrame, sig: dict) -> dict:
    """对单只信号自算缺失特征（df 已截取至 signal_date 及之前）。

    Returns:
        特征字典；无法计算的项为 None（评分走中性/闸门跳过）。
    """
    feats: dict[str, Any] = {}
    volume, close = df["volume"], df["close"]
    last_idx = len(df) - 1

    ma20_vol = volume.rolling(20).mean().iloc[-1]
    feats["ma20_vol"] = None if pd.isna(ma20_vol) else float(ma20_vol)

    # 成交额近似：close × volume × 100（volume 单位为手，spec §0.5）
    amount = close * volume * 100.0
    avg_amount_20 = amount.rolling(20).mean().iloc[-1]
    feats["avg_amount_20"] = None if pd.isna(avg_amount_20) else float(avg_amount_20)

    if feats["ma20_vol"] and feats["ma20_vol"] > 0:
        feats["lps_bar_vol_ratio"] = float(volume.iloc[-1]) / feats["ma20_vol"]
    else:
        feats["lps_bar_vol_ratio"] = None

    # SOS 棒量能比（旧 JSON 缺 sos_date 或数据中定位不到 → None）
    feats["sos_bar_vol_ratio"] = None
    sos_date = sig.get("sos_date")
    if sos_date is not None:
        dates = df["date"].map(_to_date)
        hits = df.index[dates == _parse_date(str(sos_date))]
        if len(hits) > 0 and feats["ma20_vol"]:
            feats["sos_bar_vol_ratio"] = float(volume.iloc[hits[0]]) / feats["ma20_vol"]

    # 供求偏度：近 10 日 Σ涨日量 / Σ跌日量（平盘日不计入；
    # 方向按全量 diff 对齐，尾窗首棒相对其前一棒判定，避免窗口内 diff 丢首棒方向）
    diff = df["close"].diff()
    tail_vol = df["volume"].iloc[-DEMAND_WINDOW:]
    tail_diff = diff.iloc[-DEMAND_WINDOW:]
    up_vol = float(tail_vol[tail_diff > 0].sum())
    down_vol = float(tail_vol[tail_diff < 0].sum())
    if down_vol > 0:
        feats["demand_ratio_10"] = up_vol / down_vol
    elif up_vol > 0:
        feats["demand_ratio_10"] = DEMAND_INF_CAP  # 跌日无量 → 供给侧极弱
    else:
        feats["demand_ratio_10"] = None

    # 回撤（120 日窗口，相对区间最高收盘，负值）
    win = close.iloc[-min(DRAWDOWN_WINDOW, len(close)):]
    hh = float(win.max())
    feats["drawdown_from_high"] = (float(close.iloc[-1]) - hh) / hh * 100 if hh > 0 else None

    feats["trend_state"] = _trend_state(df)

    # 回收速度：Spring 日（含）起 close 收回 MA20 的棒数；无 Spring / 未收回 → None
    feats["recovery_bars"] = None
    spring_date = sig.get("spring_date")
    if sig.get("is_spring") and spring_date is not None:
        dates = df["date"].map(_to_date)
        hits = df.index[dates == _parse_date(str(spring_date))]
        if len(hits) > 0:
            spring_idx = int(hits[0])
            ma20 = close.rolling(20).mean()
            for j in range(spring_idx, len(df)):
                if not pd.isna(ma20.iloc[j]) and close.iloc[j] >= ma20.iloc[j]:
                    feats["recovery_bars"] = j - spring_idx
                    break

    return feats


# ---------------------------------------------------------------------------
# 硬过滤 + 四维评分（spec §4 / §4.5，分桶统一"含下界不含上界"）
# ---------------------------------------------------------------------------
def _hard_filter(sig: dict, feats: dict) -> tuple[dict, list[str]]:
    """硬过滤（一票否决）。缺失输入的闸门跳过（None，视作通过，spec §5.5）。

    Returns:
        (闸门结果字典, 未通过原因列表)
    """
    hard: dict[str, Optional[bool]] = {"H2": True}  # 恒真：scan 仅输出 SOS→LPS 组合
    reasons: list[str] = []

    sos_ratio = feats.get("sos_bar_vol_ratio")
    hard["H3a"] = None if sos_ratio is None else sos_ratio >= H3A_SOS_VOL_RATIO
    if hard["H3a"] is False:
        reasons.append(f"H3a: SOS 放量不足 ({sos_ratio:.2f} < {H3A_SOS_VOL_RATIO})")

    lps_ratio = feats.get("lps_bar_vol_ratio")
    hard["H3b"] = None if lps_ratio is None else lps_ratio <= H3B_LPS_VOL_RATIO
    if hard["H3b"] is False:
        reasons.append(f"H3b: LPS 未有效缩量 ({lps_ratio:.2f} > {H3B_LPS_VOL_RATIO})")

    avg_amount = feats.get("avg_amount_20")
    hard["H4"] = None if avg_amount is None else avg_amount >= H4_MIN_AVG_AMOUNT
    if hard["H4"] is False:
        reasons.append(f"H4: 流动性不足 (均额 {avg_amount / 1e8:.2f} 亿 < {H4_MIN_AVG_AMOUNT / 1e8:.2f} 亿)")

    return hard, reasons


def _clamp(v: float) -> float:
    return max(0.0, min(10.0, v))


def _score_i1(feats: dict) -> float:
    """I1 量价确认：SOS 棒量能比（SOS 棒量 / MA20_VOL）。缺失 → 中性 5。"""
    r = feats.get("sos_bar_vol_ratio")
    if r is None:
        return NEUTRAL_SCORE
    if r >= 2.0:
        return _clamp(9.0 + min((r - 2.0) / 2.0, 1.0))
    if r >= 1.5:
        return _clamp(7.0 + (r - 1.5) / 0.5)
    if r >= 1.2:
        return 6.0
    if r >= 1.0:
        return 5.0
    return _clamp(4.0 * r)  # <1.0 线性 0–4


def _score_i2(feats: dict) -> float:
    """I2 供求偏度：demand_ratio_10。缺失 → 中性 5。"""
    r = feats.get("demand_ratio_10")
    if r is None:
        return NEUTRAL_SCORE
    if r >= 1.8:
        return _clamp(9.0 + min((r - 1.8) / 1.2, 1.0))
    if r >= 1.5:
        return 8.0
    if r >= 1.2:
        return 7.0
    if r >= 1.0:
        return 6.0
    if r >= 0.8:
        return _clamp(4.0 + (r - 0.8) / 0.2)
    return _clamp(3.75 * r)  # <0.8 线性 0–3


def _score_i4(sig: dict, feats: dict) -> float:
    """I4 事件质量：deviation 基础分 + Spring 加成 + 回收速度（上限 10）。"""
    dev = sig.get("deviation_pct")
    if dev is None:
        close, ma20 = sig.get("close"), sig.get("ma20")
        dev = abs(close - ma20) / ma20 * 100 if close and ma20 else None
    if dev is None:
        base = NEUTRAL_SCORE
    elif dev <= 1.0:
        base = 7.0
    elif dev < 2.0:
        base = 6.0
    elif dev < 3.0:
        base = 5.0
    else:
        base = 4.0

    score = base
    if sig.get("is_spring"):
        strength = sig.get("spring_strength")
        if strength is not None:
            score += 3.0 * float(strength)
        recovery = feats.get("recovery_bars")
        if recovery is not None and recovery <= 3:
            score += 1.0
    return _clamp(score)


def _score_i7(feats: dict) -> float:
    """I7 背景趋势：趋势状态（0–5）+ 回撤位置（0–5），缺失子项按中性 2.5。"""
    trend = feats.get("trend_state")
    trend_score = TREND_NEUTRAL if trend is None else float(trend)

    dd = feats.get("drawdown_from_high")
    if dd is None:
        dd_score = TREND_NEUTRAL
    else:
        d = abs(dd)
        if 30.0 <= d < 60.0:
            dd_score = 5.0
        elif 15.0 <= d < 30.0:
            dd_score = 4.0
        elif 60.0 <= d < 75.0:
            dd_score = 3.0
        else:  # <15%（高位结构）或 ≥75%（超深回撤）
            dd_score = 2.0
    return _clamp(trend_score + dd_score)


def _grade(composite: float) -> str:
    return "A" if composite >= GRADE_A else "B" if composite >= GRADE_B else "C"


def score_signal(sig: dict, df: Optional[pd.DataFrame]) -> dict:
    """对单个信号计算硬过滤 + 四维评分。

    Args:
        sig: scan 输出的信号元素
        df: 该标的 OHLCV（可含 signal_date 之后的数据，内部会截断防前视）；
            None 表示重拉失败（自算维度走中性分，闸门跳过，spec §5.5 B）

    Returns:
        {**sig 摘要, hard_filters, scores, composite, grade, reasons,
         features, missing_fields, data_degraded}
    """
    missing = _detect_missing_fields(sig)
    data_degraded = df is None

    feats: dict = {}
    if df is not None:
        sig_date = _parse_date(str(sig["signal_date"]))
        sliced = df[df["date"].map(_to_date) <= sig_date].copy()
        if sliced.empty:
            data_degraded = True
        else:
            feats = _derive_features(sliced, sig)

    hard, reasons = _hard_filter(sig, feats)
    if any(v is False for v in hard.values()):
        scores, composite = {}, 0.0
        grade = "C"
        reasons = reasons or ["硬过滤未通过"]
    else:
        scores = {
            "I1": round(_score_i1(feats), 2),
            "I2": round(_score_i2(feats), 2),
            "I4": round(_score_i4(sig, feats), 2),
            "I7": round(_score_i7(feats), 2),
        }
        composite = round(
            W_I1 * scores["I1"] + W_I2 * scores["I2"]
            + W_I4 * scores["I4"] + W_I7 * scores["I7"], 2)
        grade = _grade(composite)

    keep = ("code", "name", "signal_date", "close", "ma20", "vol_ratio",
            "deviation_pct", "sos_date", "is_spring", "spring_date",
            "spring_strength", "days_since_lps")
    entry = {k: sig.get(k) for k in keep}
    entry.update({
        "hard_filters": hard,
        "scores": scores,
        "composite": composite,
        "grade": grade,
        "reasons": reasons,
        "features": {k: _round(v) for k, v in feats.items()},
        "missing_fields": missing,
        "data_degraded": data_degraded,
    })
    return entry


# ---------------------------------------------------------------------------
# 数据重拉（spec §5.5 B：重试 1 次，失败降级不剔除）
# ---------------------------------------------------------------------------
def _fetch_df(source: Any, sig: dict, days: int) -> Optional[pd.DataFrame]:
    """重拉单只信号 OHLCV（截至 signal_date，杜绝未来数据）。失败返回 None。"""
    sig_date = _parse_date(str(sig["signal_date"]))
    start = sig_date - timedelta(days=days)
    for attempt in range(FETCH_RETRY + 1):
        try:
            df = source.fetch(sig["code"], start, sig_date)
            if df is None or df.empty:
                raise FilterError("empty dataframe")
            return df
        except Exception as e:  # noqa: BLE001 数据源异常统一降级
            logger.warning("  [%s] 重拉失败(第 %d 次): %s", sig.get("code"), attempt + 1, e)
            if attempt < FETCH_RETRY:
                time.sleep(SLEEP_BETWEEN)
    return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_filter(
    trade_date: Optional[str] = None,
    limit: int = 0,
    dry_run: bool = False,
    source: Any = None,
    max_pool: int = DEFAULT_MAX_POOL,
    fetch_days: int = DEFAULT_FETCH_DAYS,
) -> dict:
    """执行二次过滤全流程（加载输入 → 逐信号评分 → 分档截断 → 落盘推送）。

    Returns:
        汇总 dict；status == "empty" 表示无信号（安静退出，不写不推）。
    """
    if trade_date is not None:
        _parse_date(trade_date)  # 边界校验，非法抛 FilterError

    scan = _load_scan_input(trade_date)
    signals = scan.get("signals") or []
    if limit > 0:
        signals = signals[:limit]
    if not signals:
        logger.info("无扫描信号输入（%s），安静退出", trade_date or "自动最新")
        return {"status": "empty", "trade_date": trade_date}

    eff_date = trade_date or scan.get("trade_date")
    source = source if source is not None else TencentSource()

    results: list[dict] = []
    degraded_count = 0
    logger.info("二次过滤开始: 交易日=%s 信号数=%d", eff_date, len(signals))
    for sig in signals:
        df = _fetch_df(source, sig, fetch_days)
        entry = score_signal(sig, df)
        if entry["data_degraded"]:
            degraded_count += 1
        results.append(entry)
        logger.info("  [%s] %s grade=%s composite=%.2f%s",
                    sig.get("code"), sig.get("name", ""), entry["grade"],
                    entry["composite"],
                    " (数据降级)" if entry["data_degraded"] else "")

    # 分档与数量控制（spec §4）
    pool = sorted(
        (r for r in results if r["grade"] == "A"),
        key=lambda r: r["composite"], reverse=True)
    truncated_count = max(0, len(pool) - max_pool)
    if truncated_count:
        pool = pool[:max_pool]
    watchlist = sorted(
        (r for r in results if r["grade"] == "B"),
        key=lambda r: r["composite"], reverse=True)
    c_count = sum(1 for r in results if r["grade"] == "C")
    hard_rejected = sum(1 for r in results if r["reasons"])

    degraded_ratio = degraded_count / len(results)
    degraded = degraded_ratio > DEGRADED_WARN_RATIO
    input_degraded = any(r["missing_fields"] for r in results)

    result = {
        "trade_date": eff_date,
        "filter_time": datetime.now().isoformat(),
        "input_file": scan.get("_input_file"),
        "total_signals": len(results),
        "a_count": len(pool) + truncated_count,
        "b_count": len(watchlist),
        "c_count": c_count,
        "hard_rejected_count": hard_rejected,
        "input_degraded": input_degraded,
        "data_degraded_count": degraded_count,
        "degraded_ratio": round(degraded_ratio, 4),
        "degraded": degraded,
        "max_pool": max_pool,
        "truncated": bool(truncated_count),
        "truncated_count": truncated_count,
        "amount_approx": True,  # avg_amount_20 为 close×vol×100 近似（spec §0.5）
        "results": results,
        "pool": pool,
        "watchlist": watchlist,
    }

    if dry_run:
        logger.info("dry-run: 不写文件不推送")
        return result

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"filtered_{eff_date}.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    logger.info("结果已写入: %s", out_path)

    _push_notification(result)
    return result


def _push_notification(result: dict) -> None:
    """推送 A/B 档摘要（C 档剔除；降级超阈值附警示，spec §11.3）。"""
    pool, watchlist = result["pool"], result["watchlist"]
    if not pool and not watchlist and not result["degraded"]:
        logger.info("无 A/B 档且未降级，跳过推送")
        return

    trade_date = result["trade_date"]
    lines = [
        f"威科夫 LPS 二次过滤 {trade_date}",
        f"信号 {result['total_signals']} → A {result['a_count']} / "
        f"B {result['b_count']} / C {result['c_count']}"
        f"（硬过滤剔除 {result['hard_rejected_count']}）",
    ]
    if pool:
        lines.append("[A 档·优先池]")
        lines.extend(_format_entry(r) for r in pool)
    if watchlist:
        lines.append("[B 档·观察池]")
        lines.extend(_format_entry(r) for r in watchlist[:5])
        if len(watchlist) > 5:
            lines.append(f"  ... 还有 {len(watchlist) - 5} 只")
    if result["truncated"]:
        lines.append(f"⚠ A 档超上限，按综合分截断 {result['truncated_count']} 只")
    if result["degraded"]:
        lines.append(
            f"⚠ 数据降级 {result['data_degraded_count']}/{result['total_signals']}"
            "（部分标的按中性分计）")
    send_ntfy(lines[0], "\n".join(lines), priority="high",
              tags="chart_with_upwards_trend")


def _format_entry(r: dict) -> str:
    s = r["scores"]
    dim = " ".join(f"{k}={v:.1f}" for k, v in s.items()) if s else "评分缺失"
    return f"  {r['code']} {r.get('name', '')} 综合={r['composite']:.1f} {dim}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="威科夫 LPS 信号二次过滤")
    ap.add_argument("--trade-date", help="交易日 YYYY-MM-DD（默认取 scan_results 最新一份）")
    ap.add_argument("--limit", type=int, default=0,
                    help="限制处理的信号数量（0=全部；注意与 scan 的 --limit 限扫描股票数不同义）")
    ap.add_argument("--dry-run", action="store_true", help="仅打印，不写文件不推送")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[secondary_filter] %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    try:
        result = run_filter(
            trade_date=args.trade_date,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except FilterError as e:
        logger.error("输入错误: %s", e)
        return 1

    if result.get("status") == "empty":
        return 0  # 无信号日安静退出（spec §8 用例 6）
    return 0


if __name__ == "__main__":
    sys.exit(main())
