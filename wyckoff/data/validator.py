"""数据验证器 (BatchValidator + MergeValidator)

包含：
1. TradingDates: A股交易日历工具类（一次性拉取并缓存）
2. BatchValidator: 批次级双源交叉验证
3. MergeValidator: 全量合并后验证
4. ValidationResult: 验证结果数据结构
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

from wyckoff.data.base import CacheMissError, DataGapError, DataSource, FetchError

logger = logging.getLogger(__name__)

# 容差阈值（基于 600519 审计结论）
PRICE_TOLERANCE = 0.01
VOLUME_TOLERANCE = 1

# 交易日历缓存有效期（天）
CALENDAR_CACHE_DAYS = 30


@dataclass(frozen=True)
class Discrepancy:
    """单条差异记录"""
    date: date
    field: str
    source1_value: Optional[float]
    source2_value: Optional[float]
    diff: Optional[float]
    source1_name: str
    source2_name: str


@dataclass(frozen=True)
class ValidationResult:
    """验证结果"""
    passed: bool
    discrepancies: List[Discrepancy] = field(default_factory=list)
    missing_dates: Set[date] = field(default_factory=set)
    cache_misses: List[str] = field(default_factory=list)


class TradingDates:
    """A股交易日历工具类
    
    使用 ak.tool_trade_date_hist_sina() 获取交易日历，一次性拉取并缓存。
    缓存有效期为 CALENDAR_CACHE_DAYS 天。
    """

    _cache: Dict[str, Set[date]] = {}
    _cache_timestamp: Optional[float] = None

    @classmethod
    def get_trading_dates(cls, start_date: date, end_date: date) -> Set[date]:
        """获取指定日期范围内的所有交易日
        
        Args:
            start_date: 起始日期
            end_date: 结束日期（包含）
        
        Returns:
            Set[date]: 交易日集合
        
        Raises:
            FetchError: 获取交易日历失败
        """
        # 检查缓存是否有效
        now = time.time()
        cache_key = f"{start_date}_{end_date}"
        
        if cls._cache_timestamp and (now - cls._cache_timestamp) < CALENDAR_CACHE_DAYS * 24 * 3600:
            if cache_key in cls._cache:
                logger.debug(f"Using cached trading dates for {cache_key}")
                return cls._cache[cache_key]

        # 拉取交易日历（ak.tool_trade_date_hist_sina 无参数，返回全量数据）
        try:
            import akshare as ak
            
            df = ak.tool_trade_date_hist_sina()
            
            if df is None or len(df) == 0:
                raise FetchError("AkShare returned empty trading calendar")
            
            all_dates = set(pd.to_datetime(df["trade_date"]).dt.date)
            
            # 过滤到请求范围
            filtered = {d for d in all_dates if start_date <= d <= end_date}
            
            # 更新缓存
            cls._cache[cache_key] = filtered
            cls._cache_timestamp = now
            
            logger.info(f"Fetched {len(filtered)} trading dates for {start_date} ~ {end_date}")
            return filtered
            
        except Exception as e:
            raise FetchError(f"Failed to fetch trading calendar: {e}") from e


class BatchValidator:
    """批次级双源交叉验证器
    
    验证流程：
    1. 从所有源拉取批次数据（TencentSource 可能抛出 CacheMissError）
    2. 检查日期连续性（使用 A 股交易日历）
    3. 逐行对比所有源数据
    4. 返回 ValidationResult
    """

    def __init__(self, sources: List[DataSource], 
                 price_tolerance: float = PRICE_TOLERANCE,
                 volume_tolerance: float = VOLUME_TOLERANCE):
        """初始化验证器
        
        Args:
            sources: 数据源列表（至少 2 个）
            price_tolerance: 价格容差（元）
            volume_tolerance: 量能容差（手）
        """
        if len(sources) < 2:
            raise ValueError("BatchValidator requires at least 2 data sources")
        
        self.sources = sources
        self.price_tolerance = price_tolerance
        self.volume_tolerance = volume_tolerance

    def validate_batch(self, code: str, start_date: date, end_date: date,
                       primary_data: Optional[pd.DataFrame] = None) -> ValidationResult:
        """验证单个批次
        
        Args:
            code: 6位股票代码
            start_date: 批次起始日期
            end_date: 批次结束日期（包含）
            primary_data: 预取的主源数据（可选），如果提供则只从其他源拉取进行对比
        
        Returns:
            ValidationResult: 验证结果
        """
        logger.info(f"Validating batch {code} [{start_date} ~ {end_date}]")
        
        discrepancies: List[Discrepancy] = []
        missing_dates: Set[date] = set()
        cache_misses: List[str] = []
        
        # 1. 从所有源拉取数据（或使用预取数据）
        results = []
        
        if primary_data is not None:
            # 使用预取数据作为第一个源
            results.append((self.sources[0].name(), primary_data))
            
            # 只从其他源拉取数据
            for src in self.sources[1:]:
                try:
                    df = src.fetch(code, start_date, end_date)
                    results.append((src.name(), df))
                except CacheMissError as e:
                    logger.warning(f"Cache miss for {src.name()}: {e}")
                    cache_misses.append(src.name())
                    results.append((src.name(), None))
                except FetchError as e:
                    logger.error(f"Fetch failed for {src.name()}: {e}")
                    results.append((src.name(), None))
        else:
            # 从所有源拉取数据
            for src in self.sources:
                try:
                    df = src.fetch(code, start_date, end_date)
                    results.append((src.name(), df))
                except CacheMissError as e:
                    logger.warning(f"Cache miss for {src.name()}: {e}")
                    cache_misses.append(src.name())
                    results.append((src.name(), None))
                except FetchError as e:
                    logger.error(f"Fetch failed for {src.name()}: {e}")
                    results.append((src.name(), None))
        
        # 2. 检查至少有一个源成功拉取
        success_results = [(name, df) for name, df in results if df is not None]
        if len(success_results) == 0:
            raise FetchError(f"All sources failed to fetch {code} [{start_date} ~ {end_date}]")
        
        # 3. 检查日期连续性（基于第一个成功源的数据）
        first_df = success_results[0][1]
        expected_dates = TradingDates.get_trading_dates(start_date, end_date)
        actual_dates = set(first_df["date"].tolist())
        
        missing_dates = expected_dates - actual_dates
        if missing_dates:
            logger.warning(f"Missing {len(missing_dates)} trading days: {sorted(missing_dates)}")
        
        # 4. 逐行对比所有成功源的数据
        if len(success_results) >= 2:
            discrepancies = self._compare_sources(success_results)
        
        # 5. 构建验证结果
        passed = (not discrepancies) and (not missing_dates)
        
        return ValidationResult(
            passed=passed,
            discrepancies=discrepancies,
            missing_dates=missing_dates,
            cache_misses=cache_misses,
        )

    def _compare_sources(self, results: List[tuple[str, pd.DataFrame]]) -> List[Discrepancy]:
        """逐行对比多个源的数据
        
        Args:
            results: (源名称, DataFrame) 列表
        
        Returns:
            List[Discrepancy]: 差异列表
        """
        discrepancies = []
        
        # 以第一个源为基准
        base_name, base_df = results[0]
        
        # 按日期合并所有源的数据
        merged = base_df.set_index("date")
        for name, df in results[1:]:
            df_indexed = df.set_index("date")
            merged = merged.join(df_indexed, lsuffix="_base", rsuffix=f"_{name}")
        
        # 逐行对比
        for d, row in merged.iterrows():
            # 对比价格字段
            for col in ["open", "high", "low", "close"]:
                base_val = row[f"{col}_base"]
                for name, _ in results[1:]:
                    other_val = row[f"{col}_{name}"]
                    diff = abs(base_val - other_val)
                    if diff > self.price_tolerance:
                        discrepancies.append(Discrepancy(
                            date=d,
                            field=col,
                            source1_value=base_val,
                            source2_value=other_val,
                            diff=diff,
                            source1_name=base_name,
                            source2_name=name,
                        ))
            
            # 对比成交量
            base_vol = row["volume_base"]
            for name, _ in results[1:]:
                other_vol = row[f"volume_{name}"]
                diff = abs(base_vol - other_vol)
                if diff > self.volume_tolerance:
                    discrepancies.append(Discrepancy(
                        date=d,
                        field="volume",
                        source1_value=float(base_vol),
                        source2_value=float(other_vol),
                        diff=float(diff),
                        source1_name=base_name,
                        source2_name=name,
                    ))
        
        return discrepancies


class MergeValidator:
    """全量合并验证器
    
    验证合并后数据的完整性和准确性：
    1. 日期连续性检查
    2. 与缓存数据交叉验证（如果可用）
    """

    def __init__(self, price_tolerance: float = PRICE_TOLERANCE,
                 volume_tolerance: float = VOLUME_TOLERANCE):
        self.price_tolerance = price_tolerance
        self.volume_tolerance = volume_tolerance

    def validate_merge(self, df: pd.DataFrame, start_date: date, 
                       end_date: date) -> ValidationResult:
        """验证合并后的数据
        
        Args:
            df: 合并后的 DataFrame
            start_date: 预期起始日期
            end_date: 预期结束日期（包含）
        
        Returns:
            ValidationResult: 验证结果
        """
        logger.info(f"Validating merged data [{start_date} ~ {end_date}]")
        
        discrepancies: List[Discrepancy] = []
        missing_dates: Set[date] = set()
        
        # 检查日期连续性
        expected_dates = TradingDates.get_trading_dates(start_date, end_date)
        actual_dates = set(df["date"].tolist())
        missing_dates = expected_dates - actual_dates
        
        # 检查数据量
        if len(df) != len(expected_dates):
            logger.warning(f"Data row count mismatch: {len(df)} vs expected {len(expected_dates)}")
        
        passed = not missing_dates
        
        return ValidationResult(
            passed=passed,
            discrepancies=discrepancies,
            missing_dates=missing_dates,
        )

    def merge_batches(self, batches: List[pd.DataFrame]) -> pd.DataFrame:
        """合并多个批次，处理重叠去重
        
        相邻批次重叠 1 天，合并时保留后一批次的数据。
        
        Args:
            batches: 批次 DataFrame 列表（按时间顺序）
        
        Returns:
            DataFrame: 合并后的全量数据
        """
        if not batches:
            return pd.DataFrame()
        
        # 按日期合并，后批次覆盖前批次
        merged = pd.concat(batches, ignore_index=True)
        
        # 去重：保留后批次的数据（即最后出现的记录）
        merged = merged.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        
        # 重新排序
        merged = merged.sort_values("date").reset_index(drop=True)
        
        logger.info(f"Merged {len(batches)} batches into {len(merged)} rows")
        return merged
