"""config 模块中纯逻辑函数的单测（不依赖网络/密钥）。"""
from __future__ import annotations

import datetime as dt

import config


def test_is_trade_day_weekend():
    # 2026-01-03 是周六，2026-01-04 是周日
    assert config.is_trade_day(dt.date(2026, 1, 3)) is False
    assert config.is_trade_day(dt.date(2026, 1, 4)) is False


def test_is_trade_day_holiday():
    # 元旦假期
    assert config.is_trade_day(dt.date(2026, 1, 1)) is False


def test_is_trade_day_normal():
    # 2026-01-05 周一且非假期
    assert config.is_trade_day(dt.date(2026, 1, 5)) is True


def test_latest_trade_day_from_weekend_rolls_back():
    # 从周日(2026-01-11)回溯应得到上一个交易日周五(2026-01-09)
    # 注：不用 01-04，因其跨元旦假期(01-01~01-03 均休市)，会回溯到 12-31
    sun = dt.date(2026, 1, 11)
    assert config.latest_trade_day(sun) == dt.date(2026, 1, 9)


def test_latest_trade_day_crosses_new_year_holiday():
    # 2026-01-04 周日：跳过周六(01-03)与元旦假期(01-01~01-03)，回到 2025-12-31
    assert config.latest_trade_day(dt.date(2026, 1, 4)) == dt.date(2025, 12, 31)


def test_paths_resolve_relative_to_project():
    # 路径必须基于 __file__ 解析，不依赖运行 cwd
    assert config.WATCHLIST_PATH.name == "watchlist.json"
    assert config.PROMPTS_DIR.name == "prompts"
    assert "wyckoff-trading" in str(config.WYCKOFF_SKILL_DIR)
