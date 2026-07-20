"""统一数据源接口 (DataSource ABC)

所有数据源必须实现此接口，确保验证器可以对任意数据源组合进行验证。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

import pandas as pd


class DataFormatError(Exception):
    """数据格式不符合契约"""
    pass


class DataGapError(Exception):
    """数据存在缺口"""
    pass


class FetchError(Exception):
    """数据拉取失败"""
    pass


class CacheMissError(FetchError):
    """缓存未命中（只读缓存模式特有）"""
    pass


class DataSource(ABC):
    """数据源抽象基类
    
    所有数据源必须实现 fetch() 和 name() 方法。
    返回的 DataFrame 必须包含以下列：
        - date: datetime.date
        - code: str (6位股票代码)
        - open: float
        - high: float
        - low: float
        - close: float
        - volume: int (单位：手)
    """

    @abstractmethod
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
            CacheMissError: 缓存未命中（只读缓存模式）
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """返回数据源名称（用于日志和报告）"""
        ...

    def validate_response(self, df: pd.DataFrame) -> None:
        """验证返回数据格式是否符合契约
        
        Args:
            df: 数据源返回的 DataFrame
        
        Raises:
            DataFormatError: 缺少必需列或数据类型错误
        """
        required_cols = ["date", "code", "open", "high", "low", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise DataFormatError(f"Missing columns: {missing}")

        if len(df) == 0:
            raise DataFormatError("Empty DataFrame returned")

        if not isinstance(df["date"].iloc[0], date):
            raise DataFormatError("date column must be datetime.date type")

        for col in ["open", "high", "low", "close"]:
            if not pd.api.types.is_float_dtype(df[col]):
                raise DataFormatError(f"{col} column must be float type")

        if not pd.api.types.is_integer_dtype(df["volume"]):
            raise DataFormatError("volume column must be int type")
