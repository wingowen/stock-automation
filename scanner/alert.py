"""触发通知（飞书 + ntfy）与日志写入"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

import requests

from scanner.brief_parser import TriggerPoint
from scanner.realtime_quote import Quote
from scanner.config import (
    FEISHU_WEBHOOK_URL,
    NTFY_TOPIC_URL,
    LOG_DIR,
    SUPPRESS_WINDOW_SEC,
)

# 会话
_session = requests.Session()
_session.trust_env = False

# 防重复缓存: {(code, direction, round(level,1)): last_alert_time}
_suppress_cache: Dict[Tuple[str, str, float], float] = {}


def _should_suppress(trigger: TriggerPoint, now_ts: float) -> bool:
    """检查是否在静默窗口内，避免重复通知"""
    key = (trigger.code, trigger.direction, round(trigger.price_level, 1))
    last = _suppress_cache.get(key, 0.0)
    if now_ts - last < SUPPRESS_WINDOW_SEC:
        return True
    _suppress_cache[key] = now_ts
    return False


def _build_message(trigger: TriggerPoint, quote: Quote) -> str:
    """构建通知正文"""
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    direction_label = "买入信号" if trigger.direction == "buy" else "卖出信号"
    return (
        f"{trigger.name} ({trigger.code})\n"
        f"当前价: {quote.price}\n"
        f"触发类型: {direction_label}\n"
        f"触发条件: {trigger.condition}\n"
        f"扫描时间: {now}"
    )


def send_feishu(trigger: TriggerPoint, quote: Quote) -> bool:
    """发送飞书 Webhook 通知"""
    if not FEISHU_WEBHOOK_URL:
        return False

    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    direction_label = "买入信号" if trigger.direction == "buy" else "卖出信号"
    body = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "🚨 价格触发提醒"},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**{trigger.name} ({trigger.code})**\n"
                            f"当前价: **{quote.price}**\n"
                            f"触发类型: {direction_label}\n"
                            f"触发条件: {trigger.condition}\n"
                            f"扫描时间: {now}"
                        ),
                    },
                },
            ],
        },
    }

    try:
        resp = _session.post(FEISHU_WEBHOOK_URL, json=body, timeout=10)
        return resp.ok
    except requests.RequestException:
        return False


def send_ntfy(trigger: TriggerPoint, quote: Quote) -> bool:
    """发送 ntfy 推送通知"""
    if not NTFY_TOPIC_URL:
        return False

    msg = _build_message(trigger, quote)
    direction_label = "买入信号" if trigger.direction == "buy" else "卖出信号"
    tags = "warning" if trigger.direction == "sell" else "chart_with_upwards_trend"

    try:
        resp = _session.post(
            NTFY_TOPIC_URL,
            data=msg.encode("utf-8"),
            headers={
                "Title": f"🚨 {direction_label} - {trigger.name}",
                "Tags": tags,
                "Priority": "high",
            },
            timeout=10,
        )
        return resp.ok
    except requests.RequestException:
        return False


def write_log(trigger: TriggerPoint, quote: Quote) -> None:
    """写入日志文件 scanner/logs/YYYY-MM-DD.jsonl"""
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"{today}.jsonl"

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "code": trigger.code,
        "name": trigger.name,
        "price": quote.price,
        "trigger": trigger.direction,
        "level": trigger.price_level,
        "condition": trigger.condition,
        "source": trigger.source,
    }

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def notify(trigger: TriggerPoint, quote: Quote) -> None:
    """统一通知入口：飞书 + ntfy + 日志"""
    now_ts = datetime.now().timestamp()

    # 防重复检查
    if _should_suppress(trigger, now_ts):
        return

    # 飞书
    feishu_ok = send_feishu(trigger, quote)
    # ntfy
    ntfy_ok = send_ntfy(trigger, quote)

    # 日志始终写入
    write_log(trigger, quote)

    # 打印到 stdout（GitHub Actions 日志可见）
    direction_label = "买入" if trigger.direction == "buy" else "卖出"
    channels = []
    if feishu_ok:
        channels.append("飞书")
    if ntfy_ok:
        channels.append("ntfy")
    channel_str = f" [→ {'+'.join(channels)}]" if channels else " [仅日志]"
    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"{trigger.code} {trigger.name} "
        f"{direction_label}触发: {quote.price} "
        f"(条件: {trigger.condition}){channel_str}"
    )