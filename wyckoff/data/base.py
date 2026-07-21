"""统一数据源接口 (DataSource ABC)

所有数据源必须实现此接口，确保验证器可以对任意数据源组合进行验证。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

import pandas as pd


def normalize_ohlcv(
    df: pd.DataFrame,
    code: str,
    *,
    column_mapping: Optional[dict[str, str]] = None,
    volume_divisor: int = 1,
) -> pd.DataFrame:
    """规范化 OHLCV 数据为统一标准格式

    所有数据源的 _normalize() 应调用此函数，避免重复逻辑。
    标准输出列：date, code, open, high, low, close, volume

    Args:
        df: 原始 DataFrame
        code: 6 位股票代码
        column_mapping: 列名映射 {原始列名: 标准列名}，AkShare 等中文列名需要
        volume_divisor: 成交量除数（Baostock 单位是"股"，需除以 100 转为"手"）

    Returns:
        规范化后的 DataFrame，按 date 升序排列

    Raises:
        DataFormatError: 缺少必需列或类型错误
    """
    if column_mapping:
        df = df.rename(columns=column_mapping)

    required_cols = ["date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise DataFormatError(f"Missing columns after normalization: {missing}")

    df["date"] = pd.to_datetime(df["date"]).dt.date

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    df["volume"] = (pd.to_numeric(df["volume"], errors="coerce") / volume_divisor).astype(int)

    df["code"] = code
    df = df[["date", "code", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("date").reset_index(drop=True)
    return df


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
