"""腾讯财经数据加载 (Phase B 数据源)

通过 WebFetch/手动下载的本地 CSV 提供数据。
原因: 当前沙箱阻断 Python urllib 访问 gtimg.cn，但 WebFetch 工具可用。
未来: 可重写为在线 API 调用。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 数据缓存根目录
DEFAULT_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"


def load_csv(
    code: str,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """从本地 CSV 加载指定股票的数据

    Args:
        code: 6 位股票代码 (不带 sh/sz 前缀)
        cache_dir: CSV 所在目录

    Returns:
        DataFrame: date, code, name, open, high, low, close, volume
        按 date 升序, 单只股票
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR

    # 查找文件: 优先 {code}.csv，否则 {code}_full.csv
    candidates = [
        cache_dir / f"{code}.csv",
        cache_dir / f"{code}_full.csv",
    ]
    csv_path = None
    for p in candidates:
        if p.exists():
            csv_path = p
            break

    if csv_path is None:
        raise FileNotFoundError(
            f"No cached data for {code} in {cache_dir}. "
            f"Run fetch_tencent.sh or WebFetch + manual save first."
        )

    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    logger.info("Loaded %d rows for %s from %s", len(df), code, csv_path.name)
    return df


def load_600519() -> pd.DataFrame:
    """便捷: 加载 600519 贵州茅台 (Phase B 测试用)"""
    return load_csv("600519")
