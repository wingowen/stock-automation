# 价格触发扫描器设计文档

> 分析日期：2026-08-01 | 版本：v1 | 状态：待实现

## 一、目标

在 GitHub Actions 上以 5 分钟为间隔、交易时段内自动扫描指定股票列表的实时价格，比对威科夫分析简报中定义的买入/卖出触发点，满足条件时通过飞书机器人 Webhook 通知，并写入日志留痕。

## 二、技术栈

- Python 3.10+（已有 Anaconda 环境）
- `requests`（已有，腾讯接口依赖）
- GitHub Actions `schedule` 事件（cron 表达式）
- 飞书机器人 Webhook（HTTP POST）

## 三、系统架构

```
┌──────────────────────────────────────────────────────────────┐
│  GitHub Actions (schedule: */5 1-3,5-6 * * 1-5)             │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  price_scanner.py                                      │  │
│  │  ┌──────────────┐  ┌──────────────────────────────┐   │  │
│  │  │ 阶段1: 初始化  │→│  解析所有简报提取触发点        │   │  │
│  │  │ (parse_briefs)│  │  → 返回 TriggerPoint[]      │   │  │
│  │  └──────────────┘  └──────────────────────────────┘   │  │
│  │  ┌──────────────┐  ┌──────────────────────────────┐   │  │
│  │  │ 阶段2: 扫描    │→│  获取实时行情 → 比对触发条件  │   │  │
│  │  │ (scan_prices) │  │  → 返回 Alert[]             │   │  │
│  │  └──────────────┘  └──────────────────────────────┘   │  │
│  │  ┌──────────────┐  ┌──────────────────────────────┐   │  │
│  │  │ 阶段3: 通知    │→│  飞书 Webhook + 写入日志      │   │  │
│  │  │ (notify)      │  │  → 无返回                    │   │  │
│  │  └──────────────┘  └──────────────────────────────┘   │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 目录结构

```
scanner/
├── price_scanner.py      # 主入口：初始化→扫描→通知
├── brief_parser.py       # 模块：解析简报提取触发点
├── realtime_quote.py     # 模块：腾讯接口获取实时行情
├── alert.py              # 模块：飞书通知 + 日志写入
├── config.py             # 配置读取（环境变量）
└── logs/                 # 每日日志目录（Git 跟踪）
    └── .gitkeep
```

## 四、模块设计

### 4.1 brief_parser.py — 简报解析

**输入**：`analysis-brief/**/*.md` 所有 Markdown 文件

**输出**：`List[TriggerPoint]`

```python
@dataclass
class TriggerPoint:
    code: str          # 股票代码，如 "002611"
    name: str          # 股票名称，如 "东方精工"
    direction: str     # "buy" | "sell"
    price_level: float # 触发价位
    condition: str     # 条件描述，如 "缩量回测14.00-14.50不破"
    source: str        # 来源简报文件名
```

**提取规则**（正则匹配）：

| 简报内容 | 提取方式 |
|---|---|
| `阻力：XX` / `支撑：XX` | 提取数值，方向标记为"阻力→sell/支撑→buy" |
| `进场` 所在行含价位 | 提取价位，方向= buy |
| `止损` / `放弃` 所在行含价位 | 提取价位，方向= sell |
| `触发条件` 所在行含价位 | 按上下文判断 buy/sell |

**文件缓存**：每次运行都重新解析，保证数据最新。

### 4.2 realtime_quote.py — 实时行情

**数据源**：腾讯行情接口 `qt.gtimg.cn`

```
GET https://qt.gtimg.cn/q=sz002611
```

**响应格式**：
```
v_sz002611="51~东方精工~002611~15.97~15.69~15.95~396424~202075~194349~15.97~...~20260731161439~0.28~1.78~16.20~15.77~396424~63384~..."
```

**关键字段**（按 `~` 分割，索引从 0 开始）：

| 索引 | 含义 | 示例 |
|------|------|------|
| 3 | 当前价 | 15.97 |
| 4 | 昨收 | 15.69 |
| 5 | 开盘 | 15.95 |
| 32 | 涨跌额 | 0.28 |
| 33 | 涨跌幅 | 1.78 |
| 34 | 最高 | 16.20 |
| 35 | 最低 | 15.77 |
| 36 | 成交量(手) | 396424 |

**批量查询**：用 `,` 拼接多个股票代码
```
GET https://qt.gtimg.cn/q=sz002611,sz002279,sh600519
```

**异常处理**：
- 网络超时 → 重试 1 次，仍失败则跳过本轮
- 返回空值 → 跳过该股票
- 非交易时段返回无变化 → 正常处理

### 4.3 alert.py — 通知 + 日志

支持三种通知模式，按配置自动启用（可同时启用多个）：

| 通知渠道 | 环境变量 | 优先级 |
|----------|----------|--------|
| 飞书 Webhook | `FEISHU_WEBHOOK_URL` | 高 |
| ntfy | `NTFY_TOPIC_URL` | 高 |
| 仅日志 | 两者均未配置 | 低 |

**飞书 Webhook 消息格式**（interactive card）：

```json
{
  "msg_type": "interactive",
  "card": {
    "header": {
      "title": {"tag": "plain_text", "content": "🚨 价格触发提醒"}
    },
    "elements": [
      {"tag": "div", "text": {"tag": "lark_md", "content": "**东方精工 (002611)**\n当前价: **15.97**\n触发类型: 买入信号\n触发条件: 价格回测 14.00-14.50 区间不破\n扫描时间: 2026-08-01 14:35:00"}}
    ]
  }
}
```

**ntfy 消息格式**（HTTP POST 到 ntfy.sh）：

```bash
curl -H "Title: 🚨 价格触发提醒" \
     -H "Tags: warning" \
     -H "Priority: high" \
     -d "东方精工 (002611) 当前价: 15.97\n触发类型: 买入信号\n触发条件: 价格回测 14.00-14.50 区间不破" \
     https://ntfy.sh/{topic}
```

ntfy 消息包含标题、标签（用于图标）、优先级和正文，推送到手机/桌面端。

**日志格式**（`scanner/logs/YYYY-MM-DD.jsonl`，每行一条 JSON）：

```json
{"ts":"2026-08-01T14:35:00","code":"002611","name":"东方精工","price":15.97,"trigger":"buy","level":14.50,"condition":"缩量回测14.00-14.50不破","action":"进场","source":"analysis-brief/2026-08/002611_东方精工_2026-08-01.md"}
```

**防重复**：同一只股票同一种触发条件 60 分钟内不再重复通知。

### 4.4 config.py — 配置

```python
import os

FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
NTFY_TOPIC_URL = os.environ.get("NTFY_TOPIC_URL", "")
LOG_DIR = "scanner/logs"
```

### 4.5 price_scanner.py — 主流程

```python
def main():
    # 1. 判断是否在交易时段（9:30-11:30, 13:00-15:00）
    if not is_trading_time():
        # GitHub Actions 中即使 cron 触发了，非交易时段也跳过
        return

    # 2. 解析简报提取触发点
    triggers = parse_all_briefs()

    # 3. 获取实时行情
    quotes = fetch_realtime_quotes(triggers.codes)

    # 4. 比对触发条件
    alerts = check_triggers(triggers, quotes)

    # 5. 通知 + 日志
    for alert in alerts:
        if FEISHU_WEBHOOK_URL:
            send_feishu(alert)
        write_log(alert)
```

## 五、GitHub Actions 工作流

**文件**：`.github/workflows/price-scan.yml`

```yaml
name: 价格扫描

on:
  schedule:
    # 周一到周五，北京时间 9:30-11:30 (UTC 1:30-3:30), 13:00-15:00 (UTC 5:00-7:00)
    # cron: */5 1,2,3,5,6 * * 1-5
    # 9:30-9:35 是 UTC 1:30, 但 cron 不支持分钟偏移，所以从 1 时开始每 5 分钟
    - cron: '*/5 1-3,5-6 * * 1-5'
  workflow_dispatch:  # 手动触发

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
          git diff --staged --quiet || git commit -m "chore(scanner): 更新扫描日志 $(date +%Y-%m-%d)"
          git push
```

## 六、边界条件

| 场景 | 行为 |
|------|------|
| 非交易时段 | 脚本直接退出，不进行任何操作 |
| 无简报文件 | 脚本退出，提示"无可监控的股票" |
| 飞书 Webhook 未配置 | 仅写日志，不发送通知 |
| 腾讯接口超时 | 重试 1 次，失败后跳过本轮 |
| 某只股票无实时数据 | 跳过该股票，继续扫描其他 |
| 同一触发点重复触发 | 60 分钟内不重复通知（`suppress_window` 机制） |
| 日志文件不存在 | 自动创建目录和文件 |
| GitHub Actions push 冲突 | 忽略 push 失败，不阻塞下次扫描 |

## 七、依赖

- `requests`（已有）

无需其他外部依赖。

## 八、用户后续配置

| 项目 | 提供方式 | 说明 |
|------|----------|------|
| 飞书 Webhook URL | GitHub Secrets → `FEISHU_WEBHOOK_URL` | 可选，配置后启用飞书通知 |
| ntfy 主题 URL | GitHub Secrets → `NTFY_TOPIC_URL` | 可选，配置后启用 ntfy 推送 |
| 股票列表 | 自动从 `analysis-brief/` 提取 | 无需手动维护 |
| 触发价位 | 自动从简报中解析 | 每次分析后自动更新 |

## 九、非目标

- 不做分钟级 K 线分析（已有 `fetch_kline.py`）
- 不做形态检测（已有 `wyckoff/` 模块）
- 不做交易执行（只通知，不自动下单）
- 不做多时间框架分析