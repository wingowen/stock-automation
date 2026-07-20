"""AkShare 数据源实现 (主拉取源)

基于 AkShare 库获取 A 股行情数据，使用东财接口作为基准数据源。
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Optional

import pandas as pd
import requests

from wyckoff.data.base import CacheMissError, DataFormatError, DataSource, FetchError

logger = logging.getLogger(__name__)


class AkShareSource(DataSource):
    """AkShare 数据源
    
    使用 AkShare 的 stock_zh_a_hist 接口获取前复权日线数据。
    自动处理 macOS 系统代理问题（trust_env=False）。
    实现重试机制（最多 3 次）。
    """

    def __init__(self, max_retries: int = 3, retry_delay: float = 2.0):
        """初始化数据源
        
        Args:
            max_retries: 最大重试次数
            retry_delay: 重试间隔（秒）
        """
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        
        # 禁用 macOS 系统代理
        import requests
        self._session = requests.Session()
        self._session.trust_env = False

    def fetch(
        self,
        code: str,
        start_date: date,
        end_date: date,
        adjust: str = "qfq"
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
        
        Raises:
            FetchError: 拉取失败
            DataFormatError: 返回数据格式不符合契约
        """
        import akshare as ak
        
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")
        
        for attempt in range(self._max_retries):
            try:
                logger.info(
                    f"AkShare fetching {code} from {start_str} to {end_str} (attempt {attempt + 1}/{self._max_retries})"
                )
                
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_str,
                    end_date=end_str,
                    adjust=adjust,
                )
                
                if df is None or len(df) == 0:
                    raise FetchError(f"AkShare returned empty data for {code}")
                
                df = self._normalize(df, code)
                self.validate_response(df)
                
                logger.info(f"AkShare fetched {len(df)} rows for {code}")
                return df
                
            except Exception as e:
                logger.warning(f"AkShare fetch attempt {attempt + 1} failed: {e}")
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay)
                    continue
                raise FetchError(f"AkShare fetch failed after {self._max_retries} attempts: {e}") from e

    def name(self) -> str:
        return "AkShare"

    def _normalize(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        """规范化 AkShare 返回的数据格式
        
        将 AkShare 的列名和数据类型转换为统一格式：
        - date: datetime.date
        - code: str (6位股票代码)
        - open/high/low/close: float (保留2位小数)
        - volume: int (单位：手)
        
        Args:
            df: AkShare 返回的原始 DataFrame
            code: 股票代码
        
        Returns:
            DataFrame: 规范化后的 DataFrame
        """
        # 列名映射（处理不同版本的列名差异）
        column_mapping = {
            "日期": "date",
            "股票代码": "code",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
        }
        
        # 尝试不同的列名组合
        df = df.rename(columns=column_mapping)
        
        # 确保必需列存在
        required_cols = ["date", "code", "open", "high", "low", "close", "volume"]
        if not all(col in df.columns for col in required_cols):
            raise DataFormatError(f"Missing columns after normalization. Got: {list(df.columns)}")
        
        # 转换日期类型
        df["date"] = pd.to_datetime(df["date"]).dt.date
        
        # 转换价格类型（保留2位小数）
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)
        
        # 转换成交量类型（单位：手）
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype(int)
        
        # 设置代码列
        df["code"] = code
        
        # 只保留必需列
        df = df[["date", "code", "open", "high", "low", "close", "volume"]]
        
        # 按日期排序
        df = df.sort_values("date").reset_index(drop=True)
        
        return df
