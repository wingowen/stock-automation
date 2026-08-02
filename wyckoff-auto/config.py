"""威科夫自动化分析系统 -- 配置模块。

路径约定 + 环境变量读取。所有模块从此处获取配置。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── 路径约定 ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = Path(__file__).resolve().parent

# 让项目根进入导入路径，以便引用 common/ 共享包
sys.path.insert(0, str(PROJECT_ROOT))

# 交易日历统一数据源（消除与 web_brief.py 的重复定义）
from common.trading_calendar import (  # noqa: F401  (再导出给下游 `from config import`)
    HOLIDAYS_2026,
    is_trade_day,
    latest_trade_day,
)

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

# ── 通知配置 ────────────────────────────────────────────────
NTFY_TOPIC_URL = os.environ.get("NTFY_TOPIC_URL", "")
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")

# 注：HOLIDAYS_2026 / is_trade_day / latest_trade_day 已迁移至
#     common/trading_calendar.py 并由本模块再导出，请勿在此重复定义。
