"""secondary_filter 单元测试（合成数据 + mock 数据源，不联网）

覆盖 spec §8 的 8 类用例：字段映射 / 硬过滤 / 评分分桶 / 综合分与分档 /
数量控制 / 空信号 / 无前视 / 输入容错降级。
"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from wyckoff import secondary_filter
from wyckoff.secondary_filter import (
    DEMAND_INF_CAP,
    GRADE_A,
    GRADE_B,
    W_I1,
    W_I2,
    W_I4,
    W_I7,
    FilterError,
    _grade,
    _parse_date,
    _score_i1,
    _score_i2,
    _score_i4,
    _score_i7,
    run_filter,
    score_signal,
)

SIGNAL_DATE = "2026-08-14"
END_DT = date(2026, 8, 14)
SOS_DT = (END_DT - timedelta(days=10)).isoformat()   # good_df 中 SOS 位于倒数第 11 行
SPRING_DT = (END_DT - timedelta(days=9)).isoformat()


# ---------------------------------------------------------------------------
# 合成数据构造
# ---------------------------------------------------------------------------
def make_dates(n: int) -> list[date]:
    return [END_DT - timedelta(days=n - 1 - i) for i in range(n)]


def good_df(n: int = 80) -> pd.DataFrame:
    """构造可整体通过硬过滤且综合分为 A 档的日线。

    - 基线：close=10.0, vol=100_000 手（均额 ≈ 1.2 亿 ≥ 5000 万，过 H4）
    - SOS（倒数第 11 行）：vol=300_000、close 维持 10.0（SOS 定位只看量能）
      → SOS 量能比 ≈ 2.4（过 H3a）
    - 末 10 行涨跌交替（涨日 vol=200_000 / 跌日 vol=50_000，首棒较 SOS 棒上涨）
      → demand = 5×200k / (4×50k+50k) = 4.0 → I2 满分
    - LPS（末行）：close=10.0, vol=50_000 → LPS 量能比 ≈ 0.4（过 H3b）
    """
    closes = [10.0] * n
    volumes = [100_000.0] * n
    sos_idx = n - 11
    volumes[sos_idx] = 300_000.0
    for k in range(10):
        i = n - 10 + k
        if k % 2 == 0:
            closes[i], volumes[i] = 10.2, 200_000.0
        else:
            closes[i], volumes[i] = 10.0, 50_000.0
    volumes[-1] = 50_000.0  # LPS 缩量（跌日）
    return pd.DataFrame({"date": make_dates(n), "close": closes, "volume": volumes})


def hf_df(sos_vol: float, lps_vol: float, base_vol: float = 100_000.0, n: int = 60) -> pd.DataFrame:
    """硬过滤专用构造：SOS（倒数 11 行）与 LPS（末行）量能可控，其余基线恒量。"""
    closes = [10.0] * n
    volumes = [base_vol] * n
    closes[n - 11] = 11.0
    volumes[n - 11] = sos_vol
    volumes[-1] = lps_vol
    return pd.DataFrame({"date": make_dates(n), "close": closes, "volume": volumes})


def make_sig(
    code: str = "600001",
    name: str = "测试股",
    *,
    dev: float = 0.5,
    with_sos: bool = True,
    spring: bool | None = False,
) -> dict:
    """构造 scan 信号元素。spring=None 模拟旧文件（键整体缺失）。"""
    sig = {
        "code": code,
        "name": name,
        "signal_date": SIGNAL_DATE,
        "close": 10.0,
        "ma20": 10.0,
        "vol_ratio": 0.55,
        "deviation_pct": dev,
        "days_since_lps": 0,
    }
    if with_sos:
        sig["sos_date"] = SOS_DT
    if spring is None:
        return sig
    if spring:
        sig.update({"is_spring": True, "spring_date": SPRING_DT, "spring_strength": 0.6})
    else:
        sig.update({"is_spring": False, "spring_date": None, "spring_strength": None})
    return sig


class FakeSource:
    """可注入失败/首败重试的假数据源"""

    def __init__(self, df_by_code: dict[str, pd.DataFrame] | None = None,
                 fail_codes: set[str] | None = None, fail_first_attempt: set[str] | None = None):
        self.df_by_code = df_by_code or {}
        self.fail_codes = fail_codes or set()
        self.fail_first_attempt = fail_first_attempt or set()
        self.attempts: dict[str, int] = {}

    def fetch(self, code, start_dt, end_dt):
        self.attempts[code] = self.attempts.get(code, 0) + 1
        if code in self.fail_codes:
            raise RuntimeError("fetch boom")
        if code in self.fail_first_attempt and self.attempts[code] == 1:
            raise RuntimeError("transient boom")
        return self.df_by_code.get(code, good_df()).copy()


# ---------------------------------------------------------------------------
# 1. 字段映射（特征自算正确性）
# ---------------------------------------------------------------------------
class TestFeatureDerivation(unittest.TestCase):
    def _feats(self, df, sig):
        sig_date = date.fromisoformat(sig["signal_date"])
        sliced = df[df["date"] <= sig_date]
        return secondary_filter._derive_features(sliced, sig)

    def test_ma20_vol_and_amount_unit(self):
        """ma20_vol / avg_amount_20 按手→股 ×100 换算（n=40，手算对照）"""
        n = 40
        closes = [10.0] * n
        volumes = [100_000.0] * n
        closes[10] = 12.0                      # 区间最高（回撤基准）
        volumes[20:39] = [200_000.0] * 19      # 末 20 日窗口：19 根 20 万 + LPS 5 万
        volumes[39] = 50_000.0
        df = pd.DataFrame({"date": make_dates(n), "close": closes, "volume": volumes})
        sig = make_sig(spring=False)
        sig["sos_date"] = (END_DT - timedelta(days=9)).isoformat()  # idx=30，vol=20 万

        feats = self._feats(df, sig)

        self.assertAlmostEqual(feats["ma20_vol"], 192_500.0, places=1)
        # 成交额 = close × volume × 100（volume 单位为手）
        self.assertAlmostEqual(feats["avg_amount_20"], 10.0 * 192_500.0 * 100, places=0)
        self.assertAlmostEqual(feats["lps_bar_vol_ratio"], 50_000.0 / 192_500.0, places=4)
        self.assertAlmostEqual(feats["sos_bar_vol_ratio"], 200_000.0 / 192_500.0, places=4)

    def test_demand_ratio_no_down_volume_capped(self):
        """末 10 日仅涨日有量（涨后横盘，无跌日）→ 跌日量为 0，比值封顶 99"""
        n = 40
        closes = [10.0] * n
        closes[30] = 11.0
        closes[31:] = [11.0] * (n - 31)  # 涨后横盘：窗口内无跌日
        df = pd.DataFrame({"date": make_dates(n), "close": closes,
                           "volume": [100_000.0] * n})
        feats = self._feats(df, make_sig(spring=False))
        self.assertEqual(feats["demand_ratio_10"], DEMAND_INF_CAP)

    def test_drawdown_from_high(self):
        """回撤 = (末收盘 − 区间最高) / 区间最高 ×100（负值）"""
        n = 40
        closes = [10.0] * n
        closes[10] = 12.0
        df = pd.DataFrame({"date": make_dates(n), "close": closes,
                           "volume": [100_000.0] * n})
        feats = self._feats(df, make_sig(spring=False))
        self.assertAlmostEqual(feats["drawdown_from_high"], (10.0 - 12.0) / 12.0 * 100, places=3)

    def test_recovery_bars(self):
        """Spring 日跌破 MA20，次日收回 → recovery_bars = 1"""
        n = 60
        closes = [10.0] * n
        closes[50], closes[51] = 9.0, 10.5
        df = pd.DataFrame({"date": make_dates(n), "close": closes,
                           "volume": [100_000.0] * n})
        sig = make_sig(spring=True)
        sig["spring_date"] = (END_DT - timedelta(days=9)).isoformat()  # idx=50
        feats = self._feats(df, sig)
        self.assertEqual(feats["recovery_bars"], 1)

    def test_trend_state_short_history_none(self):
        """数据不足 65 行 → MA60 无法计算 → None（中性）"""
        feats = self._feats(good_df(60), make_sig(spring=False))
        self.assertIsNone(feats["trend_state"])

    def test_trend_state_flat(self):
        """恒平收盘 → MA60 斜率为 0 → 走平记 4 分"""
        feats = self._feats(good_df(80), make_sig(spring=False))
        self.assertEqual(feats["trend_state"], 4.0)


# ---------------------------------------------------------------------------
# 2. 硬过滤（H2 恒真 / H3a / H3b / H4）
# ---------------------------------------------------------------------------
class TestHardFilter(unittest.TestCase):
    def test_h3a_sos_volume_rejects(self):
        """SOS 量能比 1.17 < 1.2 → 一票否决，grade=C，reasons 含 H3a"""
        df = hf_df(sos_vol=115_000.0, lps_vol=50_000.0)  # ma20≈98,250 → ratio≈1.17
        entry = score_signal(make_sig(), df)
        self.assertIs(entry["hard_filters"]["H2"], True)
        self.assertIs(entry["hard_filters"]["H3a"], False)
        self.assertEqual(entry["grade"], "C")
        self.assertEqual(entry["composite"], 0.0)
        self.assertTrue(any("H3a" in r for r in entry["reasons"]))

    def test_h3b_lps_volume_rejects(self):
        """LPS 量能比 2.5 > 1.5 → 一票否决"""
        df = hf_df(sos_vol=300_000.0, lps_vol=300_000.0)  # ma20=120,000 → ratio=2.5
        entry = score_signal(make_sig(), df)
        self.assertIs(entry["hard_filters"]["H3b"], False)
        self.assertEqual(entry["grade"], "C")
        self.assertTrue(any("H3b" in r for r in entry["reasons"]))

    def test_h4_liquidity_rejects(self):
        """均成交额 ≈ 1640 万 < 5000 万 → 一票否决（其余闸门通过）"""
        df = hf_df(sos_vol=300_000.0, lps_vol=10_000.0, base_vol=1_000.0)
        entry = score_signal(make_sig(), df)
        self.assertIs(entry["hard_filters"]["H4"], False)
        self.assertIs(entry["hard_filters"]["H3a"], True)
        self.assertIs(entry["hard_filters"]["H3b"], True)
        self.assertTrue(any("H4" in r for r in entry["reasons"]))

    def test_all_gates_pass_scores_computed(self):
        """全部闸门通过 → 有四维得分与综合分"""
        entry = score_signal(make_sig(), good_df())
        self.assertTrue(all(v is not False for v in entry["hard_filters"].values()))
        self.assertEqual(set(entry["scores"]), {"I1", "I2", "I4", "I7"})
        self.assertGreater(entry["composite"], 0.0)


# ---------------------------------------------------------------------------
# 3. 评分分桶与边界值（含下界不含上界）
# ---------------------------------------------------------------------------
class TestScoreBuckets(unittest.TestCase):
    def test_i1_buckets(self):
        cases = [
            (None, 5.0), (0.5, 2.0), (0.99, 3.96), (1.0, 5.0), (1.19, 5.0),
            (1.2, 6.0), (1.49, 6.0), (1.5, 7.0), (1.99, 7.98),
            (2.0, 9.0), (3.0, 9.5), (4.0, 10.0), (100.0, 10.0),
        ]
        for r, expect in cases:
            self.assertAlmostEqual(_score_i1({"sos_bar_vol_ratio": r}), expect,
                                   places=2, msg=f"I1 ratio={r}")

    def test_i2_buckets(self):
        cases = [
            (None, 5.0), (0.4, 1.5), (0.79, 2.9625), (0.8, 4.0), (0.9, 4.5),
            (1.0, 6.0), (1.19, 6.0), (1.2, 7.0), (1.5, 8.0),
            (1.8, 9.0), (2.4, 9.5), (3.0, 10.0),
        ]
        for r, expect in cases:
            self.assertAlmostEqual(_score_i2({"demand_ratio_10": r}), expect,
                                   places=3, msg=f"I2 ratio={r}")

    def test_i4_base_buckets(self):
        cases = [(0.5, 7.0), (1.0, 7.0), (1.5, 6.0), (2.0, 5.0), (2.99, 5.0), (3.0, 4.0)]
        for dev, expect in cases:
            self.assertAlmostEqual(_score_i4({"deviation_pct": dev}, {}), expect,
                                   places=2, msg=f"I4 dev={dev}")

    def test_i4_spring_bonus(self):
        base = {"deviation_pct": 0.5, "is_spring": True, "spring_strength": 1.0}
        self.assertAlmostEqual(_score_i4(base, {}), 10.0, places=2)          # 7+3 截断
        sig = {"deviation_pct": 0.5, "is_spring": True, "spring_strength": 0.5}
        self.assertAlmostEqual(_score_i4(sig, {"recovery_bars": 2}), 9.5, places=2)  # 7+1.5+1
        self.assertAlmostEqual(_score_i4(sig, {"recovery_bars": 5}), 8.5, places=2)  # 回收慢无加成

    def test_i7_combinations(self):
        cases = [
            ({"trend_state": 5.0}, {"drawdown_from_high": -40.0}, 10.0),
            ({"trend_state": 4.0}, {"drawdown_from_high": -20.0}, 8.0),  # 4+4（15–30% 档）
            ({"trend_state": None}, {"drawdown_from_high": -40.0}, 7.5),
            ({"trend_state": 3.0}, {"drawdown_from_high": -65.0}, 6.0),
            ({"trend_state": 1.0}, {"drawdown_from_high": -10.0}, 3.0),
            ({}, {}, 5.0),                                                    # 双缺失中性
            ({"trend_state": 4.0}, {}, 6.5),
            ({}, {"drawdown_from_high": -80.0}, 4.5),                         # ≥75% → 2
        ]
        for tf, df_, expect in cases:
            feats = {**tf, **df_}
            self.assertAlmostEqual(_score_i7(feats), expect, places=2, msg=str(feats))


# ---------------------------------------------------------------------------
# 4. 综合分与分档
# ---------------------------------------------------------------------------
class TestCompositeAndGrade(unittest.TestCase):
    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(W_I1 + W_I2 + W_I4 + W_I7, 1.0, places=9)

    def test_grade_thresholds(self):
        self.assertEqual(_grade(7.5), "A")
        self.assertEqual(_grade(10.0), "A")
        self.assertEqual(_grade(7.49), "B")
        self.assertEqual(_grade(6.0), "B")
        self.assertEqual(_grade(5.99), "C")
        self.assertEqual(_grade(0.0), "C")

    def test_composite_within_range_and_consistent(self):
        """composite = 加权和且 ∈ [0,10]，good_df 场景为 A 档"""
        entry = score_signal(make_sig(), good_df())
        s = entry["scores"]
        expect = W_I1 * s["I1"] + W_I2 * s["I2"] + W_I4 * s["I4"] + W_I7 * s["I7"]
        self.assertAlmostEqual(entry["composite"], round(expect, 2), places=2)
        self.assertGreaterEqual(entry["composite"], 0.0)
        self.assertLessEqual(entry["composite"], 10.0)
        self.assertEqual(entry["grade"], "A")

    def test_parse_date_rejects_bad_format(self):
        for bad in ("2026/08/14", "20260814", "2026-13-01", ""):
            with self.assertRaises(FilterError, msg=bad):
                _parse_date(bad)


# ---------------------------------------------------------------------------
# 5. 数量控制（A 档 Top-15 截断）
# ---------------------------------------------------------------------------
class PoolTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self._patches = [
            patch.object(secondary_filter, "INPUT_DIR", self.tmp),
            patch.object(secondary_filter, "OUTPUT_DIR", self.tmp / "out"),
            patch.object(secondary_filter, "SLEEP_BETWEEN", 0),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def write_scan(self, trade_date: str, signals: list[dict]):
        path = self.tmp / f"scan_{trade_date}.json"
        path.write_text(json.dumps(
            {"trade_date": trade_date, "signals": signals, "lps_count": len(signals)},
            ensure_ascii=False), encoding="utf-8")
        return path


class TestPoolControl(PoolTestBase):
    def test_a_grade_truncated_to_top15(self):
        """18 只 A 档 → pool 取 Top-15，truncated 标记与计数正确"""
        signals = [make_sig(code=f"6000{i:02d}") for i in range(1, 19)]
        self.write_scan(SIGNAL_DATE, signals)
        result = run_filter(trade_date=SIGNAL_DATE, dry_run=True, source=FakeSource())

        self.assertEqual(result["a_count"], 18)
        self.assertEqual(len(result["pool"]), 15)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["truncated_count"], 3)
        # pool 按综合分降序
        composites = [r["composite"] for r in result["pool"]]
        self.assertEqual(composites, sorted(composites, reverse=True))

    def test_a_grade_under_limit_not_truncated(self):
        signals = [make_sig(code=f"6000{i:02d}") for i in range(1, 6)]
        self.write_scan(SIGNAL_DATE, signals)
        result = run_filter(trade_date=SIGNAL_DATE, dry_run=True, source=FakeSource())
        self.assertFalse(result["truncated"])
        self.assertEqual(len(result["pool"]), 5)

    def test_hard_rejected_not_in_pool(self):
        """硬过滤剔除的标的不进任何池"""
        good, bad = make_sig(code="600001"), make_sig(code="600002")
        self.write_scan(SIGNAL_DATE, [good, bad])
        df_by_code = {
            "600001": good_df(),
            "600002": hf_df(sos_vol=115_000.0, lps_vol=50_000.0),  # H3a 拒
        }
        result = run_filter(trade_date=SIGNAL_DATE, dry_run=True,
                            source=FakeSource(df_by_code=df_by_code))
        self.assertEqual(result["hard_rejected_count"], 1)
        codes = {r["code"] for r in result["pool"] + result["watchlist"]}
        self.assertEqual(codes, {"600001"})


# ---------------------------------------------------------------------------
# 6. 空信号（安静退出 0，不推送不写文件）
# ---------------------------------------------------------------------------
class TestEmptyInput(PoolTestBase):
    def test_no_scan_file_for_date(self):
        with patch.object(secondary_filter, "send_ntfy") as m_push:
            result = run_filter(trade_date=SIGNAL_DATE, dry_run=False)
        self.assertEqual(result["status"], "empty")
        m_push.assert_not_called()
        self.assertFalse(any(self.tmp.glob("filtered_*.json")))

    def test_scan_file_with_empty_signals(self):
        self.write_scan(SIGNAL_DATE, [])
        with patch.object(secondary_filter, "send_ntfy") as m_push:
            result = run_filter(trade_date=SIGNAL_DATE, dry_run=False)
        self.assertEqual(result["status"], "empty")
        m_push.assert_not_called()

    def test_auto_pick_latest_scan_file(self):
        """未指定日期时自动取最新一份 scan_<date>.json"""
        self.write_scan("2026-08-12", [make_sig(code="600001")])
        self.write_scan(SIGNAL_DATE, [make_sig(code="600002")])
        result = run_filter(trade_date=None, dry_run=True, source=FakeSource())
        self.assertEqual(result["trade_date"], SIGNAL_DATE)
        self.assertEqual(result["total_signals"], 1)
        self.assertEqual(result["results"][0]["code"], "600002")

    def test_invalid_trade_date_raises(self):
        with self.assertRaises(FilterError):
            run_filter(trade_date="2026/08/14")


# ---------------------------------------------------------------------------
# 7. 无前视（仅用 signal_date 及之前数据）
# ---------------------------------------------------------------------------
class TestNoLookahead(unittest.TestCase):
    def test_future_bars_do_not_affect_score(self):
        """追加 signal_date 之后的极端行情 → 评分与截断后完全一致"""
        sig = make_sig()
        df = good_df()
        future = pd.DataFrame({
            "date": [END_DT + timedelta(days=i) for i in range(1, 31)],
            "close": [100.0] * 30,
            "volume": [10_000_000.0] * 30,
        })
        with_future = score_signal(sig, pd.concat([df, future], ignore_index=True))
        sliced = score_signal(sig, df)
        self.assertEqual(with_future["scores"], sliced["scores"])
        self.assertEqual(with_future["composite"], sliced["composite"])
        self.assertEqual(with_future["features"], sliced["features"])


# ---------------------------------------------------------------------------
# 8. 输入容错与降级（spec §5.5）
# ---------------------------------------------------------------------------
class TestDegradation(PoolTestBase):
    def test_missing_sos_date_i1_neutral(self):
        """旧 JSON 缺 sos_date → I1 中性 5 分、H3a 跳过、标 input_degraded"""
        sig = make_sig(with_sos=False, spring=False)
        entry = score_signal(sig, good_df())
        self.assertAlmostEqual(entry["scores"]["I1"], 5.0, places=2)
        self.assertIsNone(entry["hard_filters"]["H3a"])
        self.assertIn("sos_date", entry["missing_fields"])

    def test_old_file_without_spring_keys_degrades(self):
        """旧 JSON 整体缺 Spring 键 → 不崩溃，I4 走基础分，missing 含 is_spring"""
        sig = make_sig(spring=None, dev=0.5)
        entry = score_signal(sig, good_df())
        self.assertAlmostEqual(entry["scores"]["I4"], 7.0, places=2)
        self.assertIn("is_spring", entry["missing_fields"])

    def test_new_file_spring_false_not_degraded(self):
        """新格式 is_spring=False 且子字段 None → 正常态，不算缺失"""
        entry = score_signal(make_sig(spring=False), good_df())
        self.assertEqual(entry["missing_fields"], [])

    def test_run_filter_marks_input_degraded(self):
        self.write_scan(SIGNAL_DATE, [make_sig(with_sos=False, spring=None)])
        result = run_filter(trade_date=SIGNAL_DATE, dry_run=True, source=FakeSource())
        self.assertTrue(result["input_degraded"])

    def test_fetch_fail_degrades_not_excludes(self):
        """重拉失败 → 降级评分（中性分）不剔除；占比 >30% → 顶层 degraded"""
        signals = [make_sig(code="600001"), make_sig(code="600002")]
        self.write_scan(SIGNAL_DATE, signals)
        src = FakeSource(fail_codes={"600002"})
        result = run_filter(trade_date=SIGNAL_DATE, dry_run=True, source=src)

        self.assertEqual(result["total_signals"], 2)  # 未剔除
        entry = next(r for r in result["results"] if r["code"] == "600002")
        self.assertTrue(entry["data_degraded"])
        self.assertAlmostEqual(entry["scores"]["I1"], 5.0, places=2)
        self.assertTrue(all(v is not False for v in entry["hard_filters"].values()))
        self.assertTrue(result["degraded"])  # 1/2 = 50% > 30%

    def test_fetch_retry_recovers(self):
        """首次失败重试成功 → 不降级"""
        self.write_scan(SIGNAL_DATE, [make_sig()])
        src = FakeSource(fail_first_attempt={"600001"})
        result = run_filter(trade_date=SIGNAL_DATE, dry_run=True, source=src)
        self.assertFalse(result["results"][0]["data_degraded"])
        self.assertFalse(result["degraded"])
        self.assertEqual(src.attempts["600001"], 2)

    def test_low_degrade_ratio_no_warning(self):
        """降级占比 ≤30% → 顶层不标 degraded（10 只中 1 只失败）"""
        signals = [make_sig(code=f"6000{i:02d}") for i in range(1, 11)]
        self.write_scan(SIGNAL_DATE, signals)
        src = FakeSource(fail_codes={"600001"})
        result = run_filter(trade_date=SIGNAL_DATE, dry_run=True, source=src)
        self.assertFalse(result["degraded"])
        self.assertEqual(result["data_degraded_count"], 1)

    def test_write_output_and_push_ab_only(self):
        """非 dry-run：写 filtered_<date>.json；推送仅含 A/B 档（C 档剔除）"""
        good, fail = make_sig(code="600001", name="优质A"), make_sig(code="600002", name="劣质C")
        self.write_scan(SIGNAL_DATE, [good, fail])
        src = FakeSource(fail_codes={"600002"})
        with patch.object(secondary_filter, "send_ntfy") as m_push:
            result = run_filter(trade_date=SIGNAL_DATE, dry_run=False, source=src)

        out = self.tmp / "out" / f"filtered_{SIGNAL_DATE}.json"
        self.assertTrue(out.exists())
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["trade_date"], SIGNAL_DATE)
        for r in data["results"]:
            for key in ("composite", "grade", "scores", "hard_filters", "reasons"):
                self.assertIn(key, r)

        m_push.assert_called_once()
        title, body = m_push.call_args.args[:2]
        self.assertIn("600001", body)
        self.assertNotIn("劣质C", body)  # C 档不进推送
        self.assertIn("数据降级", body)   # 50% 降级 → 附警示


if __name__ == "__main__":
    unittest.main()
