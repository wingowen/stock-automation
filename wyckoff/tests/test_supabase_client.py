"""SupabaseClient 单元测试（mock requests，不依赖真实 DB）"""
import unittest
from unittest.mock import patch

import requests

from wyckoff.supabase_client import (
    PAGE_SIZE,
    SupabaseClient,
    SupabaseError,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else []
        self.text = text

    def json(self):
        return self._json


class MockSession:
    """按脚本顺序返回响应；记录全部调用供断言"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(session, max_retries=3):
    return SupabaseClient(
        url="https://demo.supabase.co",
        key="sb_secret_test",
        session=session,
        max_retries=max_retries,
        retry_delay=0,
    )


class TestFromEnv(unittest.TestCase):
    def test_missing_env_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SupabaseError):
                SupabaseClient.from_env()

    def test_from_env_constructs(self):
        env = {
            "SUPABASE_URL": "https://demo.supabase.co/",
            "SUPABASE_SECRET_KEY": "sb_secret_test",
        }
        with patch.dict("os.environ", env, clear=True):
            client = SupabaseClient.from_env()
        self.assertEqual(client._url, "https://demo.supabase.co")


class TestAuthAndHeaders(unittest.TestCase):
    def test_headers_carry_apikey_and_bearer(self):
        session = MockSession([FakeResponse(200, [])])
        client = make_client(session)
        client.upsert_progress([{"trade_date": "2026-08-14", "code": "600000", "status": "done"}])
        _, _, kwargs = session.calls[0]
        self.assertEqual(kwargs["headers"]["apikey"], "sb_secret_test")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sb_secret_test")


class TestScanRuns(unittest.TestCase):
    def test_insert_run_returns_id(self):
        session = MockSession([FakeResponse(201, [{"id": 42, "status": "running"}])])
        client = make_client(session)
        run_id = client.insert_run("2026-08-14", total_stocks=100)
        self.assertEqual(run_id, 42)
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/rest/v1/scan_runs"))
        self.assertEqual(kwargs["json"]["trade_date"], "2026-08-14")
        self.assertEqual(kwargs["json"]["total_stocks"], 100)
        self.assertEqual(kwargs["headers"]["Prefer"], "return=representation")

    def test_finish_run_patches_stats(self):
        session = MockSession([FakeResponse(204)])
        client = make_client(session)
        client.finish_run(42, {"status": "success", "signal_count": 3})
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "PATCH")
        self.assertIn("scan_runs?id=eq.42", url)
        self.assertEqual(kwargs["json"]["status"], "success")
        self.assertIn("finished_at", kwargs["json"])


class TestLoadDoneCodes(unittest.TestCase):
    def test_pagination_beyond_single_page(self):
        page1 = [{"code": f"{i:06d}"} for i in range(PAGE_SIZE)]
        page2 = [{"code": "999999"}, {"code": "888888"}, {"code": "777777"}]
        session = MockSession([
            FakeResponse(200, page1),
            FakeResponse(200, page2),
        ])
        client = make_client(session)
        done = client.load_done_codes("2026-08-14")
        self.assertEqual(len(done), PAGE_SIZE + 3)
        # 第二页请求应带 offset
        _, _, kwargs = session.calls[1]
        self.assertEqual(kwargs["params"]["offset"], PAGE_SIZE)
        self.assertEqual(kwargs["params"]["trade_date"], "eq.2026-08-14")
        self.assertEqual(kwargs["params"]["status"], "eq.done")

    def test_single_page_short_result(self):
        session = MockSession([FakeResponse(200, [{"code": "600000"}])])
        client = make_client(session)
        self.assertEqual(client.load_done_codes("2026-08-14"), {"600000"})
        self.assertEqual(len(session.calls), 1)


class TestUpsert(unittest.TestCase):
    def test_upsert_sends_merge_duplicates_prefer(self):
        session = MockSession([FakeResponse(201, [])])
        client = make_client(session)
        rows = [{"trade_date": "2026-08-14", "code": "600000", "status": "done"}]
        client.upsert_progress(rows)
        _, url, kwargs = session.calls[0]
        self.assertTrue(url.endswith("/rest/v1/scan_progress"))
        self.assertEqual(kwargs["headers"]["Prefer"], "resolution=merge-duplicates")
        self.assertEqual(kwargs["json"], rows)

    def test_upsert_empty_rows_noop(self):
        session = MockSession([])
        client = make_client(session)
        client.upsert_progress([])
        client.upsert_signals([])
        self.assertEqual(len(session.calls), 0)


class TestRetryPolicy(unittest.TestCase):
    def test_retry_on_500_then_success(self):
        session = MockSession([
            FakeResponse(500, text="server error"),
            FakeResponse(200, [{"id": 1}]),
        ])
        client = make_client(session)
        run_id = client.insert_run("2026-08-14", 10)
        self.assertEqual(run_id, 1)
        self.assertEqual(len(session.calls), 2)

    def test_retry_on_connection_error(self):
        session = MockSession([
            requests.ConnectionError("boom"),
            FakeResponse(200, [{"id": 9}]),
        ])
        client = make_client(session)
        self.assertEqual(client.insert_run("2026-08-14", 10), 9)
        self.assertEqual(len(session.calls), 2)

    def test_no_retry_on_400(self):
        session = MockSession([FakeResponse(400, text="bad request")])
        client = make_client(session)
        with self.assertRaises(SupabaseError):
            client.insert_run("2026-08-14", 10)
        # 4xx 立即失败，不应有第二次请求
        self.assertEqual(len(session.calls), 1)

    def test_exhausted_retries_raise(self):
        session = MockSession([
            FakeResponse(503, text="unavailable"),
            FakeResponse(503, text="unavailable"),
            FakeResponse(503, text="unavailable"),
        ])
        client = make_client(session)
        with self.assertRaises(SupabaseError):
            client.insert_run("2026-08-14", 10)
        self.assertEqual(len(session.calls), 3)


class TestPurgeExpired(unittest.TestCase):
    def test_purge_builds_cutoff_filters(self):
        session = MockSession([FakeResponse(204), FakeResponse(204)])
        client = make_client(session)
        client.purge_expired()
        self.assertEqual(len(session.calls), 2)
        method_progress, url_progress, _ = session.calls[0]
        method_runs, url_runs, _ = session.calls[1]
        self.assertEqual(method_progress, "DELETE")
        self.assertEqual(method_runs, "DELETE")
        self.assertRegex(url_progress, r"scan_progress\?trade_date=lt\.\d{4}-\d{2}-\d{2}")
        self.assertRegex(url_runs, r"scan_runs\?trade_date=lt\.\d{4}-\d{2}-\d{2}")


if __name__ == "__main__":
    unittest.main()
