#!/usr/bin/env python3
"""多源数据获取入口脚本

使用 DataPipeline 获取指定标的的5年数据，自动进行批次级和全量交叉验证。

用法:
    python3 fetch_5year_data.py <股票代码> [起始日期] [结束日期]

示例:
    python3 fetch_5year_data.py 600519                    # 默认获取最近5年数据
    python3 fetch_5year_data.py 600519 2020-01-01 2024-12-31  # 指定日期范围
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# 禁用代理环境变量（解决 macOS 系统代理问题）
import os
for k in list(os.environ.keys()):
    if "proxy" in k.lower():
        os.environ.pop(k, None)

from wyckoff.data.baostock_source import BaostockSource
from wyckoff.data.pipeline import DataPipeline
from wyckoff.data.tencent_source import TencentSource


def main():
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("fetch_5year_data")

    # 解析命令行参数
    parser = argparse.ArgumentParser(description="获取指定标的的5年数据并进行多源交叉验证")
    parser.add_argument("code", help="6位股票代码（不带 sh/sz 前缀）")
    parser.add_argument("start_date", nargs="?", default=None, help="起始日期（YYYY-MM-DD）")
    parser.add_argument("end_date", nargs="?", default=None, help="结束日期（YYYY-MM-DD）")
    parser.add_argument("--batch-days", type=int, default=90, help="批次大小（天）")
    parser.add_argument("--cache-dir", type=str, default=None, help="缓存目录")
    
    args = parser.parse_args()

    # 确定日期范围
    if args.end_date:
        end_date = date.fromisoformat(args.end_date)
    else:
        end_date = date.today()
    
    if args.start_date:
        start_date = date.fromisoformat(args.start_date)
    else:
        start_date = end_date - timedelta(days=5*365)
    
    # 确定缓存目录
    if args.cache_dir:
        cache_dir = Path(args.cache_dir)
    else:
        cache_dir = Path(__file__).parent.parent / "data" / "cache"

    logger.info(f"=== 数据获取任务 ===")
    logger.info(f"股票代码: {args.code}")
    logger.info(f"日期范围: {start_date} ~ {end_date}")
    logger.info(f"批次大小: {args.batch_days} 天")
    logger.info(f"缓存目录: {cache_dir}")

    # 创建数据源：Baostock 为主源（覆盖完整），腾讯为校验源
    logger.info(f"\n=== 初始化数据源 ===")
    bs_src = BaostockSource()
    tc_src = TencentSource(cache_dir=cache_dir)

    logger.info(f"主数据源: {bs_src.name()}")
    logger.info(f"交叉验证数据源: {tc_src.name()}")

    # 创建数据管道
    # 注意：不同数据源前复权基准不同，价格可能偏差数十元；这里用相对宽松的容差避免误报
    logger.info(f"\n=== 创建数据管道 ===")
    pipeline = DataPipeline(
        sources=[bs_src, tc_src],
        cache_dir=cache_dir,
        batch_days=args.batch_days,
        price_tolerance=50.0,
        volume_tolerance=10,
    )

    # 运行管道
    logger.info(f"\n=== 开始获取数据 ===")
    try:
        df = pipeline.run(args.code, start_date, end_date)
        
        # 输出统计信息
        logger.info(f"\n=== 获取完成 ===")
        logger.info(f"总行数: {len(df)}")
        logger.info(f"日期范围: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")
        logger.info(f"数据已保存到: {cache_dir / f'{args.code}_full.csv'}")
        
        # 输出前5行
        logger.info(f"\n前 5 行数据:")
        print(df.head().to_string(index=False))
        
        return 0
        
    except Exception as e:
        logger.error(f"数据获取失败: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
