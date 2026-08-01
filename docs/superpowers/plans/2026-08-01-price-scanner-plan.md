# 价格触发扫描器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 GitHub Actions 上以 5 分钟为间隔、交易时段内自动扫描股票实时价格，比对威科夫简报中的买入/卖出触发点，触发时通过飞书/ntfy 通知并写日志留痕。

**Architecture:** 四个独立模块（简报解析、实时行情、通知+日志、主入口），通过 `@dataclass` 数据类传递结构化数据，由主脚本编排调用。

**Tech Stack:** Python 3.10+, requests, GitHub Actions

---

## 文件结构

```
scanner/
├── __init__.py          # 空
├── config.py            # 环境变量配置
├── brief_parser.py      # 解析简报 → TriggerPoint[]
├── realtime_quote.py    # 腾讯接口 → Quote[]
├── alert.py             # 飞书/ntfy 通知 + 日志
├── price_scanner.py     # 主入口
└── logs/
    └── .gitkeep

.github/workflows/
└── price-scan.yml       # 定时扫描工作流
```

---

### Task 1: scanner 目录 + config.py

**Files:**
- Create: `scanner/__init__.py`
- Create: `scanner/logs/.gitkeep`
- Create: `scanner/config.py`

- [ ] **Step 1: 创建目录**

```bash
mkdir -p scanner/logs
touch scanner/__init__.py
touch scanner/logs/.gitkeep
```

- [ ] **Step 2: 写入 config.py**

```python
"""价格扫描器配置——从环境变量读取"""

import os

# 飞书 Webhook（可选，空字符串 = 不启用）
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")

# ntfy 主题 URL（可选，空字符串 = 不启用）
NTFY_TOPIC_URL = os.environ.get("NTFY_TOPIC_URL", "")

# 日志目录（相对于项目根）
LOG_DIR = "scanner/logs"

# 防重复通知窗口（秒）
SUPPRESS_WINDOW_SEC = 3600  # 60 分钟
```

- [ ] **Step 3: Commit**

```bash
git add scanner/__init__.py scanner/logs/.gitkeep scanner/config.py
git commit -m "feat(scanner): 初始化目录结构和配置模块"
```

---

### Task 2: brief_parser.py — 简报解析

**Files:**
- Create: `scanner/brief_parser.py`

- [ ] **Step 1: 写入完整代码**

```python
"""解析 analysis-brief/*.md 提取触发点"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

BRIEF_DIR = Path(__file__).resolve().parent.parent / "analysis-brief"


@dataclass
class TriggerPoint:
    """单个触发点"""
    code: str          # 股票代码，如 "002611"
    name: str          # 股票名称，如 "东方精工"
    direction: str     # "buy" | "sell"
    price_level: float # 触发价位
    condition: str     # 条件描述，如 "缩量回测14.00-14.50不破"
    source: str        # 来源简报文件（相对路径）


def parse_all_briefs(brief_dir: str | Path | None = None) -> List[TriggerPoint]:
    """扫描 analysis-brief 下所有 Markdown 文件，提取触发点"""
    brief_dir = Path(brief_dir) if brief_dir else BRIEF_DIR
    if not brief_dir.exists():
        return []

    triggers: List[TriggerPoint] = []
    for md_file in sorted(brief_dir.rglob("*.md")):
        # 跳过 KNOWLEDGE.md
        if md_file.name == "KNOWLEDGE.md":
            continue
        content = md_file.read_text(encoding="utf-8")
        # 从第一行标题提取股票代码和名称
        # 格式: # 002611 东方精工 威科夫分析简报
        header_match = re.search(r"# (\d{6})\s+(\S+)", content)
        if not header_match:
            continue
        code = header_match.group(1)
        name = header_match.group(2)

        # --- 提取触发点 ---
        file_triggers = _extract_triggers(content, code, name, str(md_file))
        triggers.extend(file_triggers)

    return triggers


def _extract_triggers(
    content: str, code: str, name: str, source: str
) -> List[TriggerPoint]:
    """从单篇简报内容中提取所有触发点"""
    triggers: List[TriggerPoint] = []

    # 1. 从"关键价位"行提取阻力/支撑
    # 格式: | 关键价位 | 阻力：XX  支撑：XX |
    kv_match = re.search(r"关键价位.*?阻力[：:]\s*([\d.]+).*?支撑[：:]\s*([\d.]+)", content)
    if kv_match:
        resist = float(kv_match.group(1))
        support = float(kv_match.group(2))
        # 阻力 = sell 信号（到阻力可能回落）
        triggers.append(TriggerPoint(
            code=code, name=name, direction="sell",
            price_level=resist,
            condition=f"阻力位 {resist}",
            source=source,
        ))
        # 支撑 = buy 信号（到支撑可能反弹）
        triggers.append(TriggerPoint(
            code=code, name=name, direction="buy",
            price_level=support,
            condition=f"支撑位 {support}",
            source=source,
        ))

    # 2. 从"措施和行动"表格中提取进场/止损价位
    # 格式: | 进场 | 条件 | 1/3 仓进场，止损放 XX |
    entry_match = re.search(r"\|.*?止损[放设]?\s*([\d.]+)", content)
    if entry_match:
        stop_loss = float(entry_match.group(1))
        triggers.append(TriggerPoint(
            code=code, name=name, direction="sell",
            price_level=stop_loss,
            condition=f"止损线 {stop_loss}",
            source=source,
        ))

    # 3. 从"进场"情景行提取买点价位
    for m in re.finditer(r"\|.*?进场.*?([\d.]+)[\s-]*([\d.]+)\s*[区区间]", content):
        # 找到区间低位
        lo = float(m.group(1))
        triggers.append(TriggerPoint(
            code=code, name=name, direction="buy",
            price_level=lo,
            condition=f"进场区间 {m.group(1)}-{m.group(2)}",
            source=source,
        ))
        break  # 只取第一个进场区间

    # 4. 从"放弃"情景行提取破位卖出价
    for m in re.finditer(r"\|.*?放弃.*?跌破\s*([\d.]+)", content):
        break_level = float(m.group(1))
        triggers.append(TriggerPoint(
            code=code, name=name, direction="sell",
            price_level=break_level,
            condition=f"跌破 {break_level} 放弃做多",
            source=source,
        ))
        break

    return triggers
```

- [ ] **Step 2: Commit**

```bash
git add scanner/brief_parser.py
git commit -m "feat(scanner): 简报解析模块，提取触发点"
```

---

### Task 3: realtime_quote.py — 实时行情

**Files:**
- Create: `scanner/realtime_quote.py`

- [ ] **Step 1: 写入完整代码**

```python
"""腾讯行情接口获取实时价格"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

# 腾讯行情接口
QT_URL = "https://qt.gtimg.cn/q="

# 交易所前缀
EXCHANGE_PREFIX = {"6": "sh"}
EXCHANGE_PREFIX_DEFAULT = "sz"

# 会话（复用连接）
_session = requests.Session()
_session.trust_env = False  # 规避 macOS 系统代理


@dataclass
class Quote:
    """单只股票实时行情"""
    code: str    # 6 位代码
    name: str    # 股票名称
    price: float # 当前价
    high: float  # 今日最高
    low: float   # 今日最低
    open: float  # 今日开盘
    pre_close: float  # 昨收
    volume: int  # 成交量（手）


def resolve_symbol(code: str) -> str:
    """6 位代码 → 腾讯接口符号（如 sz002611）"""
    prefix = EXCHANGE_PREFIX.get(code[0], EXCHANGE_PREFIX_DEFAULT)
    return f"{prefix}{code}"


def parse_qt_response(text: str) -> Optional[Quote]:
    """解析单行腾讯行情响应

    v_sz002611="51~东方精工~002611~15.97~15.69~15.95~...~16.20~15.77~396424~..."
    字段用 ~ 分割，索引见下表。
    """
    # 提取引号内的内容
    m = re.search(r'"(.+)"', text)
    if not m:
        return None
    parts = m.group(1).split("~")
    if len(parts) < 38:
        return None

    try:
        code = parts[2]
        name = parts[1]
        price = float(parts[3]) if parts[3] else 0.0
        pre_close = float(parts[4]) if parts[4] else 0.0
        open_p = float(parts[5]) if parts[5] else 0.0
        high = float(parts[34]) if parts[34] else 0.0
        low = float(parts[35]) if parts[35] else 0.0
        volume_str = parts[36] if len(parts) > 36 else "0"
        volume = int(float(volume_str)) if volume_str else 0
    except (ValueError, IndexError):
        return None

    if price == 0.0:
        return None

    return Quote(
        code=code, name=name, price=price,
        high=high, low=low, open=open_p,
        pre_close=pre_close, volume=volume,
    )


def fetch_quotes(codes: List[str]) -> Dict[str, Quote]:
    """批量获取多只股票实时行情

    Args:
        codes: 6 位代码列表，如 ["002611", "002279"]

    Returns:
        {code: Quote} 字典，只包含成功获取的股票
    """
    if not codes:
        return {}

    symbols = ",".join(resolve_symbol(c) for c in codes)
    url = f"{QT_URL}{symbols}"

    try:
        resp = _session.get(url, timeout=10)
        resp.encoding = "utf-8"
        resp.raise_for_status()
    except requests.RequestException:
        return {}

    result: Dict[str, Quote] = {}
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        quote = parse_qt_response(line)
        if quote and quote.price > 0:
            result[quote.code] = quote

    return result
```

- [ ] **Step 2: Commit**

```bash
git add scanner/realtime_quote.py
git commit -m "feat(scanner): 腾讯行情接口，实时价格获取"
```

---

### Task 4: alert.py — 通知 + 日志

**Files:**
- Create: `scanner/alert.py`

- [ ] **Step 1: 写入完整代码**

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add scanner/alert.py
git commit -m "feat(scanner): 飞书+ntfy通知与日志写入模块"
```

---

### Task 5: price_scanner.py — 主入口

**Files:**
- Create: `scanner/price_scanner.py`

- [ ] **Step 1: 写入完整代码**

```python
#!/usr/bin/env python3
"""价格触发扫描器主入口

在 GitHub Actions 上定时运行，扫描简报中的触发点 vs 实时价格。
适合 cron 调度（每 5 分钟一次），非交易时段自动跳过。

用法：
    python scanner/price_scanner.py
"""

from __future__ import annotations

import sys
from datetime import datetime, time

from scanner.brief_parser import parse_all_briefs
from scanner.realtime_quote import fetch_quotes
from scanner.alert import notify


def is_trading_time() -> bool:
    """判断当前是否为 A 股交易时段

    交易时段：
      上午: 09:30 - 11:30
      下午: 13:00 - 15:00
    """
    now = datetime.now()
    # 非交易日（周六/周日）跳过
    if now.weekday() >= 5:
        return False

    t = now.time()
    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)

    if morning_start <= t <= morning_end:
        return True
    if afternoon_start <= t <= afternoon_end:
        return True
    return False


def main() -> int:
    """主流程"""
    # 1. 交易时段检查
    if not is_trading_time():
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 非交易时段，跳过")
        return 0

    # 2. 解析简报
    triggers = parse_all_briefs()
    if not triggers:
        print("无可监控的股票（analysis-brief 下无简报文件）")
        return 0

    # 3. 提取需要扫描的股票代码去重
    codes = sorted(set(t.code for t in triggers))
    print(f"监控股票: {', '.join(codes)}  (触发点: {len(triggers)} 个)")

    # 4. 获取实时行情
    quotes = fetch_quotes(codes)
    if not quotes:
        print("获取实时行情失败，跳过本轮")
        return 1

    print(f"获取到 {len(quotes)} 只股票行情")

    # 5. 比对触发条件
    alerts = 0
    for trigger in triggers:
        quote = quotes.get(trigger.code)
        if quote is None:
            continue

        # 买入触发：当前价 <= 支撑位/进场价（允许 1% 的容差）
        if trigger.direction == "buy" and quote.price <= trigger.price_level * 1.01:
            notify(trigger, quote)
            alerts += 1

        # 卖出触发：当前价 >= 阻力位（允许 1% 的容差）
        # 或当前价 <= 止损位（跌破止损）
        if trigger.direction == "sell":
            if quote.price >= trigger.price_level * 0.99:
                notify(trigger, quote)
                alerts += 1
            elif quote.price <= trigger.price_level * 1.01:
                notify(trigger, quote)
                alerts += 1

    if alerts == 0:
        print("无触发条件满足")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Commit**

```bash
git add scanner/price_scanner.py
git commit -m "feat(scanner): 价格扫描主入口"
```

---

### Task 6: GitHub Actions 工作流

**Files:**
- Create: `.github/workflows/price-scan.yml`

- [ ] **Step 1: 写入工作流**

```yaml
name: 价格扫描

on:
  schedule:
    # 周一到周五，北京时间 9:30-11:30 (UTC 1:30-3:30), 13:00-15:00 (UTC 5:00-7:00)
    # cron 不支持分钟偏移，所以覆盖 1:00-3:59 + 5:00-6:59，精确判断在脚本内
    - cron: '*/5 1-3,5-6 * * 1-5'
  workflow_dispatch:  # 允许手动触发

permissions:
  contents: write

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: 设置 Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: 安装依赖
        run: pip install requests

      - name: 运行价格扫描
        env:
          FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}
          NTFY_TOPIC_URL: ${{ secrets.NTFY_TOPIC_URL }}
        run: python scanner/price_scanner.py

      - name: 提交日志
        run: |
          git config user.name "price-scanner-bot"
          git config user.email "bot@price-scanner"
          git add scanner/logs/
          if git diff --staged --quiet; then
            echo "日志无变化，跳过提交"
          else
            git commit -m "chore(scanner): 更新扫描日志 $(date +%Y-%m-%d)"
            git push
          fi
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/price-scan.yml
git commit -m "ci: 价格扫描 GitHub Actions 工作流"
```

---

## 自检清单

- [x] **Spec 覆盖度**：
  - 简报解析 → Task 2 ✓
  - 实时行情 → Task 3 ✓
  - 飞书通知 → Task 4 ✓
  - ntfy 通知 → Task 4 ✓
  - 日志留痕 → Task 4 ✓
  - 交易时段判断 → Task 5 ✓
  - 防重复通知 → Task 4 (suppress_cache) ✓
  - GitHub Actions 工作流 → Task 6 ✓
  - 边界条件（无简报、无行情、非交易时段）→ 全部覆盖 ✓
- [x] **无占位符**：所有代码完整，无 TBD/TODO
- [x] **类型一致性**：TriggerPoint、Quote、notify 签名在各模块间一致