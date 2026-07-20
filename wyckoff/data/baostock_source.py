"""Baostock 数据源实现 (主拉取源)

基于 Baostock 库获取 A 股行情数据，免费、免注册、覆盖完整。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import pandas as pd

from wyckoff.data.base import DataFormatError, DataSource, FetchError

logger = logging.getLogger(__name__)


class BaostockSource(DataSource):
    """Baostock 数据源

    使用 Baostock 的 query_history_k_data_plus 接口获取前复权日线数据。
    注意：Baostock 返回的成交量单位是"股"，统一契约要求"手"，因此内部会除以 100。
    """

    def __init__(self):
        self._logged_in = False

    def _ensure_login(self) -> None:
        """确保已登录 Baostock"""
        if self._logged_in:
            return
        import baostock as bs
        result = bs.login()
        if result.error_code != "0":
            raise FetchError(f"Baostock login failed: {result.error_msg}")
        self._logged_in = True
        logger.info("Baostock login success")

    def fetch(
        self,
        code: str,
        start_date: date,
        end_date: date,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """拉取指定日期范围的数据

        Args:
            code: 6位股票代码（不带 sh/sz 前缀）
            start_date: 起始日期
            end_date: 结束日期（包含）
            adjust: 复权方式，默认前复权 "qfq"

        Returns:
            DataFrame: 包含 date, code, open, high, low, close, volume
            按 date 升序排列
        """
        self._ensure_login()

        import baostock as bs

        adjust_flag = {"qfq": "2", "hfq": "1", "": "0"}.get(adjust, "2")
        exchange = "sh" if code.startswith("6") else "sz"
        symbol = f"{exchange}.{code}"
        fields = "date,open,high,low,close,volume"

        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")

        logger.info(f"Baostock fetching {code} from {start_str} to {end_str}")

        rs = bs.query_history_k_data_plus(
            code=symbol,
            fields=fields,
            start_date=start_str,
            end_date=end_str,
            frequency="d",
            adjustflag=adjust_flag,
        )

        if rs.error_code != "0":
            raise FetchError(f"Baostock query failed: {rs.error_msg}")

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            raise FetchError(f"Baostock returned empty data for {code}")

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df = self._normalize(df, code)
        self.validate_response(df)

        logger.info(f"Baostock fetched {len(df)} rows for {code}")
        return df

    def name(self) -> str:
        return "Baostock"

    def _normalize(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        """规范化 Baostock 返回的数据格式"""
        df["date"] = pd.to_datetime(df["date"]).dt.date

        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

        # Baostock 成交量单位是股，转换为手（1 手 = 100 股）
        df["volume"] = (pd.to_numeric(df["volume"], errors="coerce") / 100).astype(int)

        df["code"] = code
        df = df[["date", "code", "open", "high", "low", "close", "volume"]]
        df = df.sort_values("date").reset_index(drop=True)
        return df
