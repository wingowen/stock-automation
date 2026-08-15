"""mainboard_scanner 断点续扫与攒批 flush 单元测试（mock client + 假数据源）"""
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

from wyckoff import mainboard_scanner
from wyckoff.mainboard_scanner import _annotate_spring, run_scan
from wyckoff.supabase_client import SupabaseError

TRADE_DATE = "2026-08-14"
END_DT = date(2026, 8, 14)


def make_df(signal: bool) -> pd.DataFrame:
    """构造 25 行日线：收盘价恒为 10（紧贴 MA20），末日缩量则触发 LPS"""
    dates = [END_DT - timedelta(days=24 - i) for i in range(25)]
    closes = [10.0] * 25
    volumes = [50.0 if (signal and i == 24) else 100.0 for i in range(25)]
    return pd.DataFrame({"date": dates, "close": closes, "volume": volumes})


def make_spring_df(spring_days_ago: int = 5) -> pd.DataFrame:
    """构造 60 行日线，在 spring_days_ago 天前制造一次 Spring：
    前 40 行区间横盘（low=9.8, vol=100），Spring 日 low 跌破至 9.4
    但收盘收回 10.0 且放量 3x，之后恢复正常。
    末日缩量收回 MA20 附近（LPS 形态）。
    """
    rows = []
    for i in range(60):
        d = END_DT - timedelta(days=59 - i)
        open_ = high = low = close = 10.0
        volume = 100.0
        if i == 60 - 1 - spring_days_ago:
            low, close, volume = 9.4, 10.0, 300.0  # Spring: 跌破→收回→放量
            open_, high = 9.9, 10.05
        elif i >= 40:
            low, high = 9.85, 10.1  # 后段轻微区间
        else:
            low, high = 9.8, 10.2   # 前段主区间（range low = 9.8）
        rows.append({"date": d, "open": open_, "high": high, "low": low,
                     "close": close, "volume": volume})
    df = pd.DataFrame(rows)
    # 末日缩量（LPS）
    df.loc[df.index[-1], "volume"] = 50.0
    return df


class FakeSource:
    """6 开头的股票有信号，可指定必然失败的代码"""

    def __init__(self, fail_codes=()):
        self.fail_codes = set(fail_codes)
        self.fetched: list[str] = []

    def fetch(self, code, start_dt, end_dt):
        self.fetched.append(code)
        if code in self.fail_codes:
            raise RuntimeError("fetch boom")
        return make_df(signal=code.startswith("6"))


class FakeClient:
    def __init__(self, done_codes=None, fail_upsert=False, fail_startup=False):
        self.done_codes = set(done_codes or [])
        self.fail_upsert = fail_upsert
        self.fail_startup = fail_startup
        self.purged = False
        self.inserted_run = None
        self.progress_rows: list[dict] = []
        self.signal_rows: list[dict] = []
        self.upsert_calls = 0
        self.finished = None

    def purge_expired(self):
        if self.fail_startup:
            raise SupabaseError("db unreachable")
        self.purged = True

    def load_done_codes(self, trade_date):
        return set(self.done_codes)

    def insert_run(self, trade_date, total_stocks):
        self.inserted_run = (trade_date, total_stocks)
        return 7

    def upsert_progress(self, rows):
        self.upsert_calls += 1
        if self.fail_upsert:
            raise SupabaseError("upsert boom")
        self.progress_rows.extend(rows)

    def upsert_signals(self, rows):
        if self.fail_upsert:
            raise SupabaseError("upsert boom")
        self.signal_rows.extend(rows)

    def finish_run(self, run_id, stats):
        self.finished = (run_id, dict(stats))


STOCKS = [
    {"code": "600000", "name": "浦发银行"},
    {"code": "600001", "name": "样板一"},
    {"code": "000001", "name": "平安银行"},
]


def run_with(stocks, source, client, **kw):
    """公共入口：patch 股票清单与 sleep，dry_run 防止写结果文件"""
    with patch.object(mainboard_scanner, "_fetch_mainboard_list", return_value=stocks), \
         patch.object(mainboard_scanner, "SLEEP_BETWEEN", 0):
        return run_scan(
            trade_date=TRADE_DATE,
            dry_run=True,
            source=source,
            client=client,
            **kw,
        )


class TestResumeScan(unittest.TestCase):
    def test_skip_done_codes(self):
        """当日已 done 的股票不再拉数，计入 skipped"""
        source = FakeSource()
        client = FakeClient(done_codes={"600000"})
        result = run_with(STOCKS, source, client)

        self.assertNotIn("600000", source.fetched)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["scanned_count"], 2)
        self.assertEqual(result["total_stocks"], 3)
        self.assertFalse(result["degraded"])

    def test_failed_stock_recorded_for_rescan(self):
        """拉数失败的股票进度记为 failed（重试时不在 done 集合，会重扫）"""
        source = FakeSource(fail_codes={"600001"})
        client = FakeClient()
        result = run_with(STOCKS, source, client)

        self.assertEqual(result["failed_count"], 1)
        by_code = {r["code"]: r["status"] for r in client.progress_rows}
        self.assertEqual(by_code["600001"], "failed")
        self.assertEqual(by_code["600000"], "done")
        self.assertEqual(by_code["000001"], "done")

    def test_signals_upserted_to_db(self):
        """信号股票进入 signal_rows，字段与表结构一致"""
        source = FakeSource()
        client = FakeClient()
        run_with(STOCKS, source, client)

        self.assertEqual(len(client.signal_rows), 2)
        row = client.signal_rows[0]
        self.assertEqual(row["trade_date"], TRADE_DATE)
        self.assertEqual(row["code"], "600000")
        self.assertEqual(row["name"], "浦发银行")
        for field in ("close", "ma20", "vol_ratio", "deviation_pct"):
            self.assertIn(field, row)


class TestFlushBatches(unittest.TestCase):
    def test_flush_every_batches_calls(self):
        """flush_every=2 时 3 只股票分两批写入（2+1）"""
        source = FakeSource()
        client = FakeClient()
        run_with(STOCKS, source, client, flush_every=2, flush_interval=9999)

        self.assertEqual(client.upsert_calls, 2)
        self.assertEqual(len(client.progress_rows), 3)

    def test_flush_failure_degrades_but_completes(self):
        """flush 失败不中断扫描：扫完 3 只，标记 degraded，finish_run 状态 degraded"""
        source = FakeSource()
        client = FakeClient(fail_upsert=True)
        result = run_with(STOCKS, source, client, flush_every=2, flush_interval=9999)

        self.assertEqual(result["scanned_count"], 3)
        self.assertTrue(result["degraded"])
        run_id, stats = client.finished
        self.assertEqual(run_id, 7)
        self.assertEqual(stats["status"], "degraded")

    def test_finish_run_called_with_stats(self):
        source = FakeSource()
        client = FakeClient()
        result = run_with(STOCKS, source, client, flush_every=999, flush_interval=9999)

        run_id, stats = client.finished
        self.assertEqual(run_id, 7)
        self.assertEqual(stats["status"], "success")
        self.assertEqual(stats["scanned_count"], 3)
        self.assertEqual(stats["skipped_count"], 0)
        self.assertEqual(stats["signal_count"], result["lps_count"])
        self.assertEqual(client.inserted_run, (TRADE_DATE, 3))
        self.assertTrue(client.purged)


class TestStartupFailFast(unittest.TestCase):
    def test_db_unreachable_raises(self):
        """启动阶段 DB 不可达直接抛错（由 main 捕获 fail fast）"""
        source = FakeSource()
        client = FakeClient(fail_startup=True)
        with self.assertRaises(SupabaseError):
            run_with(STOCKS, source, client)

    def test_no_client_runs_without_persistence(self):
        """client=None 时向后兼容：正常扫描，无持久化路径"""
        source = FakeSource()
        result = run_with(STOCKS, source, client=None)
        self.assertEqual(result["scanned_count"], 3)
        self.assertEqual(result["skipped_count"], 0)
        self.assertFalse(result["degraded"])


class TestSpringAnnotation(unittest.TestCase):
    def test_spring_within_window_annotated(self):
        """窗口内（5 天前）出现 Spring → is_spring=True，字段完整"""
        df = make_spring_df(spring_days_ago=5)
        ann = _annotate_spring(df, TRADE_DATE)
        self.assertTrue(ann["is_spring"])
        self.assertIsNotNone(ann["spring_date"])
        self.assertIsNotNone(ann["spring_strength"])

    def test_spring_outside_window_not_annotated(self):
        """窗口外（40 天前 > SPRING_WINDOW=30）的 Spring 不标注"""
        df = make_spring_df(spring_days_ago=40)
        ann = _annotate_spring(df, TRADE_DATE)
        self.assertFalse(ann["is_spring"])
        self.assertIsNone(ann["spring_date"])
        self.assertIsNone(ann["spring_strength"])

    def test_no_spring_at_all(self):
        """全程无 Spring（普通横盘）→ 不标注"""
        df = make_spring_df()
        # 去掉 Spring 行（spring_days_ago=5 → index 54）：改为普通日线
        df.loc[df.index[54], ["low", "close", "volume", "high"]] = [9.8, 10.0, 100.0, 10.1]
        ann = _annotate_spring(df, TRADE_DATE)
        self.assertFalse(ann["is_spring"])

    def test_run_scan_signal_carries_spring_fields(self):
        """run_scan 输出的信号与 DB 行都带 Spring 三字段"""
        source = FakeSource()  # 600000/600001 触发 LPS（横盘数据，无 Spring）
        client = FakeClient()
        result = run_with(STOCKS, source, client)
        self.assertGreaterEqual(result["lps_count"], 1)
        for s in result["signals"]:
            self.assertIn("is_spring", s)
            self.assertIn("spring_date", s)
            self.assertIn("spring_strength", s)
            self.assertFalse(s["is_spring"])  # 合成数据无 Spring
        for row in client.signal_rows:
            self.assertIn("is_spring", row)
            self.assertIn("spring_date", row)
            self.assertIn("spring_strength", row)


if __name__ == "__main__":
    unittest.main()
