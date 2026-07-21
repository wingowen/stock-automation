"""数据管道主流程 (DataPipeline)

负责：
1. 配置解析
2. 批次划分（5年数据按季度分批）
3. 批次级拉取 + 双源交叉验证（每批落盘）
4. 从磁盘文件合并 + 全量验证
5. 持久化到缓存
"""
from __future__ import annotations

import logging
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from wyckoff.data.base import CacheMissError, DataSource, FetchError
from wyckoff.data.validator import BatchValidator, MergeValidator, ValidationResult

logger = logging.getLogger(__name__)

# 默认批次大小（季度）
DEFAULT_BATCH_DAYS = 90

# 默认缓存目录（威科夫系统数据目录）
DEFAULT_CACHE_DIR = Path(__file__).parent / "cache"


class DataPipeline:
    """多源数据管道

    内存使用策略：
    - 每批数据处理完立即写入临时 CSV，释放内存
    - 合并时从磁盘文件逐批读取，不保留所有批次在内存中
    - 最终合并结果经验证后写入缓存目录

    完整流程：
    1. 划分批次（默认每季度一批）
    2. 对每个批次：从主源拉取 → 双源交叉验证 → 落盘到 tmp/
    3. 从 tmp/ 读取所有批次 → 合并（重叠去重）→ 全量验证
    4. 持久化到缓存 → 清理 tmp/
    """

    def __init__(self,
                 sources: List[DataSource],
                 cache_dir: Optional[Path] = None,
                 batch_days: int = DEFAULT_BATCH_DAYS,
                 price_tolerance: float = 0.01,
                 volume_tolerance: int = 1):
        """初始化数据管道

        Args:
            sources: 数据源列表（至少 2 个）
            cache_dir: 缓存目录
            batch_days: 批次大小（天）
            price_tolerance: 价格容差（元）
            volume_tolerance: 量能容差（手）
        """
        if len(sources) < 2:
            raise ValueError("DataPipeline requires at least 2 data sources")

        self.sources = sources
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.batch_days = batch_days
        self.price_tolerance = price_tolerance
        self.volume_tolerance = volume_tolerance

        self.batch_validator = BatchValidator(
            sources=sources,
            price_tolerance=price_tolerance,
            volume_tolerance=volume_tolerance,
        )
        self.merge_validator = MergeValidator(
            price_tolerance=price_tolerance,
            volume_tolerance=volume_tolerance,
        )

    # ── 临时目录 ──────────────────────────────────────────────

    @property
    def _tmp_dir(self) -> Path:
        """批次临时文件目录"""
        return self.cache_dir / "tmp"

    def _clean_tmp(self) -> None:
        """清理临时目录"""
        if self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir)
            logger.debug("Cleaned tmp directory")

    # ── 主入口 ────────────────────────────────────────────────

    def run(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """运行完整数据管道

        Args:
            code: 6位股票代码
            start_date: 起始日期
            end_date: 结束日期（包含）

        Returns:
            DataFrame: 合并验证后的全量数据

        Raises:
            FetchError: 所有批次均失败
        """
        logger.info(f"Starting data pipeline for {code} [{start_date} ~ {end_date}]")

        # 0. 清理上次残留
        self._clean_tmp()
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

        # 1. 划分批次
        batches = self._split_batches(start_date, end_date)
        logger.info(f"Split into {len(batches)} batches")

        # 2. 逐批次处理 → 直接落盘
        batch_files: List[Path] = []
        for idx, (batch_start, batch_end) in enumerate(batches, 1):
            logger.info(f"\n--- Batch {idx}/{len(batches)}: {batch_start} ~ {batch_end} ---")

            try:
                batch_data = self._process_batch(code, batch_start, batch_end)
            except Exception as e:
                logger.error(f"Batch {idx} primary failed: {e}")
                batch_data = self._fallback_batch(code, batch_start, batch_end)
                if batch_data is None:
                    raise FetchError(f"Batch {idx} failed with no fallback: {e}")

            # 立即落盘，释放内存
            batch_file = self._tmp_dir / f"batch_{idx:04d}.csv"
            batch_data.to_csv(batch_file, index=False)
            del batch_data  # 显式释放
            batch_files.append(batch_file)
            logger.info(f"Batch {idx} persisted to {batch_file.name}")

        # 3. 从磁盘合并 → 逐批读取，不保留全部在内存中
        logger.info(f"\n--- Merging {len(batch_files)} batches from disk ---")
        merged = self._merge_from_disk(batch_files)

        # 4. 全量验证
        logger.info("--- Validating merged data ---")
        merge_result = self.merge_validator.validate_merge(merged, start_date, end_date)
        if not merge_result.passed:
            logger.warning(f"Merge validation: {len(merge_result.missing_dates)} missing dates")

        # 5. 持久化
        logger.info("--- Persisting to cache ---")
        self._persist(code, merged)

        # 6. 清理临时文件
        self._clean_tmp()

        logger.info(f"\nPipeline completed: {len(merged)} rows for {code}")
        return merged

    # ── 批次划分 ──────────────────────────────────────────────

    def _split_batches(self, start_date: date, end_date: date) -> List[tuple[date, date]]:
        """将日期范围划分为批次

        相邻批次重叠 1 天，确保合并时日期连续。
        """
        batches = []
        current_start = start_date
        while current_start <= end_date:
            current_end = min(current_start + timedelta(days=self.batch_days - 1), end_date)
            batches.append((current_start, current_end))
            current_start = current_end  # 重叠 1 天
        return batches

    # ── 单批处理 ──────────────────────────────────────────────

    def _process_batch(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """处理单个批次：拉取 + 双源交叉验证"""
        primary_src = self.sources[0]
        df = primary_src.fetch(code, start_date, end_date)

        result = self.batch_validator.validate_batch(code, start_date, end_date, primary_data=df)
        if not result.passed:
            logger.warning(f"Batch validation warnings:")
            if result.discrepancies:
                logger.warning(f"  - {len(result.discrepancies)} discrepancies")
            if result.missing_dates:
                logger.warning(f"  - {len(result.missing_dates)} missing dates")
        return df

    # ── 回退 ──────────────────────────────────────────────────

    def _fallback_batch(self, code: str, start_date: date, end_date: date) -> Optional[pd.DataFrame]:
        """回退处理：尝试使用其他源"""
        for src in self.sources[1:]:
            try:
                df = src.fetch(code, start_date, end_date)
                logger.info(f"Fallback succeeded with {src.name()}")
                return df
            except (FetchError, CacheMissError):
                continue
        return None

    # ── 磁盘合并 ──────────────────────────────────────────────

    def _merge_from_disk(self, batch_files: List[Path]) -> pd.DataFrame:
        """从磁盘文件合并批次，处理重叠去重

        逐批读取，只保留最终合并结果在内存中，避免所有批次同时驻留。
        """
        if not batch_files:
            return pd.DataFrame()

        merged = None
        for f in batch_files:
            chunk = pd.read_csv(f)
            if merged is None:
                merged = chunk
            else:
                # 合并后去重：后批次覆盖前批次
                merged = pd.concat([merged, chunk], ignore_index=True)
                merged = merged.sort_values("date").drop_duplicates(
                    subset=["date"], keep="last"
                )
            del chunk  # 显式释放

        merged = merged.sort_values("date").reset_index(drop=True)
        logger.info(f"Merged {len(batch_files)} batches into {len(merged)} rows")
        return merged

    # ── 持久化 ────────────────────────────────────────────────

    def _persist(self, code: str, df: pd.DataFrame) -> None:
        """持久化数据到缓存"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        file_path = self.cache_dir / f"{code}_full.csv"
        df.to_csv(file_path, index=False)
        logger.info(f"Data persisted to {file_path}")