#!/usr/bin/env python3
"""Supabase REST (PostgREST) 轻量客户端

为每日 LPS 扫描提供断点续扫与信号历史持久化：
  - scan_runs     运行登记（每次扫描一行）
  - scan_progress 每股进度（断点续扫核心表）
  - lps_signals   信号历史（永久保留）

设计约束（见 docs/system/SPEC_SUPABASE_PERSISTENCE.md）：
  - 纯 requests 实现，零新依赖
  - 认证使用新式 secret key（sb_secret_，绕过 RLS，等效旧 service_role）
  - 每请求最多 3 次尝试，指数退避；4xx（非 429）不重试
  - trust_env=False，沿袭 macOS 代理处理约定
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger("wyckoff.supabase_client")

# 可重试的 HTTP 状态码：限流与网关类错误
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# PostgREST 单页默认上限（Supabase 默认 max-rows=1000），分页读取用
PAGE_SIZE = 1000


class SupabaseError(Exception):
    """Supabase REST 请求最终失败（重试耗尽或不可重试的 4xx）"""


class SupabaseClient:
    """Supabase PostgREST 薄封装，仅覆盖本仓库三张表的读写"""

    def __init__(
        self,
        url: str,
        key: str,
        session: Optional[requests.Session] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: float = 15.0,
    ):
        self._url = url.rstrip("/")
        self._key = key
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.trust_env = False

    @classmethod
    def from_env(cls) -> "SupabaseClient":
        """从环境变量 SUPABASE_URL / SUPABASE_SECRET_KEY 构造

        Raises:
            SupabaseError: 环境变量缺失（fail fast，不静默降级）
        """
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        if not url or not key:
            raise SupabaseError("SUPABASE_URL / SUPABASE_SECRET_KEY 未配置")
        return cls(url=url, key=key)

    # ------------------------------------------------------------------
    # 底层请求（带重试）
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json: Any = None,
        prefer: str = "",
    ) -> requests.Response:
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer

        url = f"{self._url}/rest/v1/{path}"
        last_err: Optional[Exception] = None

        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._session.request(
                    method, url,
                    params=params, json=json,
                    headers=headers, timeout=self._timeout,
                )
                if resp.status_code < 400:
                    return resp
                last_err = SupabaseError(
                    f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}"
                )
                if resp.status_code not in RETRYABLE_STATUS:
                    # 4xx（非 429）是请求本身的问题，重试无意义
                    raise last_err
            except requests.RequestException as e:
                last_err = SupabaseError(f"{method} {path} 网络错误: {e}")

            if attempt < self._max_retries:
                delay = self._retry_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Supabase 请求失败(第 %d/%d 次), %.1fs 后重试: %s",
                    attempt, self._max_retries, delay, last_err,
                )
                time.sleep(delay)

        raise SupabaseError(f"Supabase 请求最终失败(共 {self._max_retries} 次): {last_err}")

    # ------------------------------------------------------------------
    # scan_runs：运行登记
    # ------------------------------------------------------------------

    def insert_run(self, trade_date: str, total_stocks: int) -> int:
        """登记一次扫描（status=running），返回 run id"""
        resp = self._request(
            "POST", "scan_runs",
            json={
                "trade_date": trade_date,
                "status": "running",
                "total_stocks": total_stocks,
            },
            prefer="return=representation",
        )
        data = resp.json()
        return int(data[0]["id"])

    def finish_run(self, run_id: int, stats: dict[str, Any]) -> None:
        """扫描结束时 PATCH 统计与最终状态（success / degraded / failed）"""
        payload = dict(stats)
        payload["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._request("PATCH", f"scan_runs?id=eq.{run_id}", json=payload)

    # ------------------------------------------------------------------
    # scan_progress：断点续扫
    # ------------------------------------------------------------------

    def load_done_codes(self, trade_date: str) -> set[str]:
        """读取指定交易日已成功扫描（status=done）的股票代码集合

        PostgREST 单页上限 1000 行，全市场 ~3200 只需分页。
        """
        done: set[str] = set()
        offset = 0
        while True:
            resp = self._request(
                "GET", "scan_progress",
                params={
                    "select": "code",
                    "trade_date": f"eq.{trade_date}",
                    "status": "eq.done",
                    "order": "code",
                    "limit": PAGE_SIZE,
                    "offset": offset,
                },
            )
            rows = resp.json()
            done.update(r["code"] for r in rows)
            if len(rows) < PAGE_SIZE:
                return done
            offset += PAGE_SIZE

    def upsert_progress(self, rows: list[dict]) -> None:
        """批量 upsert 每股进度（PK: trade_date+code，幂等）"""
        if rows:
            self._request(
                "POST", "scan_progress",
                json=rows, prefer="resolution=merge-duplicates",
            )

    def upsert_signals(self, rows: list[dict]) -> None:
        """批量 upsert LPS 信号（PK: trade_date+code，幂等）"""
        if rows:
            self._request(
                "POST", "lps_signals",
                json=rows, prefer="resolution=merge-duplicates",
            )

    def purge_expired(self, keep_progress_days: int = 14, keep_runs_days: int = 90) -> None:
        """清理过期数据：进度保留 14 天，运行记录保留 90 天"""
        today = date.today()
        progress_cutoff = (today - timedelta(days=keep_progress_days)).isoformat()
        runs_cutoff = (today - timedelta(days=keep_runs_days)).isoformat()
        self._request("DELETE", f"scan_progress?trade_date=lt.{progress_cutoff}")
        self._request("DELETE", f"scan_runs?trade_date=lt.{runs_cutoff}")
