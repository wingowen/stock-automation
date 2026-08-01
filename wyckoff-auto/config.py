"""威科夫自动化分析系统 -- 配置模块。

路径约定 + 环境变量读取。所有模块从此处获取配置。
"""
from __future__ import annotations

import os
from pathlib import Path

# ── 路径约定 ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = Path(__file__).resolve().parent

WATCHLIST_PATH = MODULE_DIR / "watchlist.json"
PROMPTS_DIR = MODULE_DIR / "prompts"

# 复用现有模块的路径
SKILL_DIR = PROJECT_ROOT / ".agents" / "skills"
KLINE_FETCH_SCRIPT = SKILL_DIR / "a-share-kline-fetch" / "fetch_kline.py"
WORKFLOW_SCRIPT = SKILL_DIR / "a-share-kline-fetch" / "workflow.py"
WYCKOFF_SKILL_DIR = SKILL_DIR / "wyckoff-trading"

BRIEF_ROOT = PROJECT_ROOT / "analysis-brief"
KNOWLEDGE_FILE = BRIEF_ROOT / "KNOWLEDGE.md"
ARCHIVE_ROOT = BRIEF_ROOT / "archive"

# ── LLM API 配置 ────────────────────────────────────────────
DEFAULT_BASE_URL = "https://api.agnes-ai.com/v1"
DEFAULT_MODEL = "agnes-text"

AGNES_API_KEY = os.environ.get("AGNES_API_KEY", "")
AGNES_BASE_URL = os.environ.get("AGNES_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
AGNES_MODEL = os.environ.get("AGNES_MODEL", DEFAULT_MODEL)

# ── 分析参数 ────────────────────────────────────────────────
BG_MONTHS = 6          # 背景数据月数
MAX_RETRIES = 2        # LLM 调用重试次数
REQUEST_TIMEOUT = 120  # API 超时（秒）
TEMPERATURE = 0.3      # 采样温度

# A 股交易日历（与 web_brief.py 同步）
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02", "2026-01-03",
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    "2026-04-04", "2026-04-05", "2026-04-06",
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-19", "2026-06-20", "2026-06-21",
    "2026-09-25", "2026-09-26", "2026-09-27",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
    "2026-10-06", "2026-10-07",
}


def is_trade_day(d) -> bool:
    import datetime as dt
    if d.weekday() >= 5:
        return False
    if d.strftime("%Y-%m-%d") in HOLIDAYS_2026:
        return False
    return True


def latest_trade_day(from_date=None) -> "dt.date":
    import datetime as dt
    d = from_date or dt.date.today()
    while not is_trade_day(d):
        d -= dt.timedelta(days=1)
    return d
