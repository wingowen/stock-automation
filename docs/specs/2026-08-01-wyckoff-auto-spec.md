# Spec: 威科夫自动化分析系统（wyckoff-auto）

## Objective

构建一个基于 LLM API 的威科夫操盘法自动化分析系统，每日收盘后在 GitHub Actions 中自动运行。系统读取手动维护的观察名单，对每只股票执行多轮 LLM 分析（对应看盘五步法），生成结构化分析简报，并供价格扫描器读取触发点。

**用户故事：**
- 作为交易者，我希望每天收盘后自动生成观察名单中所有股票的威科夫分析简报，无需手动逐只分析
- 作为交易者，我希望价格扫描器基于观察名单运行，而非扫描简报目录
- 作为交易者，我希望分析过程是多轮推理（而非单次调用），以保证分析深度和质量

**成功标准：**
- GitHub Actions 每日收盘后自动运行，对 watchlist.json 中所有 active 股票生成分析简报
- 每只股票的分析经过 5 轮 LLM 调用，每轮对应看盘五步法的一个步骤
- 生成的简报格式与现有 analysis-brief/ 下的简报兼容（scanner/brief_parser.py 可直接解析）
- scanner 从 watchlist.json 读取股票列表，不再依赖扫描 analysis-brief/ 目录
- 系统在 LLM 调用失败时有降级处理（标注数据不可用，不阻塞其他股票）

## Tech Stack

- **语言**: Python 3.11（与现有 web_brief.py 一致）
- **LLM API**: Agnes AI（OpenAI 兼容），通过 urllib 调用（零 SDK 依赖）
- **K线数据**: 复用 `.agents/skills/a-share-kline-fetch/fetch_kline.py`（需 pandas + requests）
- **CI**: GitHub Actions（ubuntu-latest）
- **核心依赖**: 仅 Python 标准库（urllib, json, pathlib, datetime）；K线拉取子进程使用 pandas/requests

## Commands

```bash
# 本地运行（分析观察名单中所有股票）
python wyckoff-auto/analyzer.py

# 分析指定股票（单只）
python wyckoff-auto/analyzer.py --code 002279

# 指定分析日期（回测用）
python wyckoff-auto/analyzer.py --trade-date 2026-08-01

# Dry run（仅打印不落盘）
python wyckoff-auto/analyzer.py --dry-run

# 验证 scanner 能从 watchlist 读取
python -m scanner.price_scanner --watchlist wyckoff-auto/watchlist.json
```

## Project Structure

```
stock-automation/
├── wyckoff-auto/                  # 新模块：自动化分析系统
│   ├── watchlist.json             # 手动维护的观察名单
│   ├── analyzer.py                # 主入口：多轮分析引擎
│   ├── context_builder.py         # 上下文构建（K线数据 + skill知识 + 历史简报）
│   ├── llm_client.py              # LLM API 调用封装（复用 web_brief.py 模式）
│   ├── brief_writer.py            # 简报写入（复用 workflow.py 的模板和归档逻辑）
│   ├── knowledge_updater.py       # KNOWLEDGE.md 更新（LLM 输出 -> 知识库条目）
│   ├── config.py                  # 配置（环境变量 + 路径约定）
│   └── prompts/                   # 各轮次的 prompt 模板
│       ├── round1_background.txt  # 第①步 背景判断
│       ├── round2_patterns.txt    # 第②步 价量形态
│       ├── round3_nature.txt      # 第③步 形态性质
│       ├── round4_conclusion.txt  # 第④步 结论/预测
│       └── round5_action.txt      # 第⑤步 措施和行动
│
├── scanner/                       # 修改：从 watchlist 读取股票列表
│   ├── brief_parser.py            # 不变：仍解析简报提取触发点
│   └── price_scanner.py           # 修改：增加 --watchlist 参数
│
├── .github/workflows/
│   └── wyckoff-auto.yml           # 新增：收盘后自动分析
│
├── .agents/skills/
│   ├── a-share-kline-fetch/       # 复用：fetch_kline.py + workflow.py
│   └── wyckoff-trading/           # 复用：知识库 Markdown 文件
│
├── analysis-brief/                # 不变：简报输出目录
│   ├── KNOWLEDGE.md               # 自动更新
│   ├── archive/                   # 自动归档
│   └── YYYY-MM/                   # 当月简报
│
└── stockexpert-daily-brief/       # 不变：每日看板系统
```

## 核心设计

### 1. 观察名单 (watchlist.json)

```json
{
  "stocks": [
    {
      "code": "002279",
      "name": "久其软件",
      "added": "2026-08-01",
      "status": "active",
      "note": "JOC回测闭环模式"
    },
    {
      "code": "002611",
      "name": "东方精工",
      "added": "2026-08-01",
      "status": "active"
    }
  ]
}
```

- **手动维护**：用户直接编辑 JSON 文件添加/删除/停用股票
- **status**: `active`（参与分析和扫描）/ `paused`（暂停跟踪）
- **scanner 和 analyzer 共用**：两个模块都读同一个文件

### 2. 多轮分析流程

对 watchlist 中每只 active 股票，执行 5 轮 LLM 调用：

```
┌─────────────────────────────────────────────────────────┐
│  Round 1: 背景判断                                       │
│  输入: K线数据 + skill核心框架(SKILL.md)                  │
│  输出: {background, phase, trend}                        │
│  → 根据背景判断选择性加载章节                             │
├─────────────────────────────────────────────────────────┤
│  Round 2: 价量形态                                       │
│  输入: K线数据 + Round1输出 + 相关章节(ch01-ch04)         │
│  输出: {patterns: [{name, date, description, significance}]}
├─────────────────────────────────────────────────────────┤
│  Round 3: 形态性质                                       │
│  输入: K线数据 + Round1+2输出 + 三大原则章节               │
│  输出: {effort_result, stopping_action, absorption, ...} │
├─────────────────────────────────────────────────────────┤
│  Round 4: 结论/预测                                      │
│  输入: Round1+2+3输出 + 历史简报(若有)                    │
│  输出: {background_conclusion, short_term, key_levels,   │
│         current_phase, prediction}                       │
├─────────────────────────────────────────────────────────┤
│  Round 5: 措施和行动                                     │
│  输入: Round1+2+3+4输出                                  │
│  输出: {entry_conditions, abandon_conditions,            │
│         follow_up_conditions, position_size}             │
└─────────────────────────────────────────────────────────┘
```

**上下文管理策略：**

1. **对话历史累积**：使用 OpenAI messages 数组，每轮的 user+assistant 消息都追加到数组中
2. **K线数据压缩**：Round 1 传入完整 K 线数据（30 日明细 + 统计摘要）；Round 2-5 只传统计摘要 + Round 1 的背景结论（避免重复传大量数据）
3. **Skill 知识选择性加载**：
   - Round 1：SKILL.md 核心框架（三大原则 + 看盘五步法 + 吸筹/派发四阶段）
   - Round 2：根据 Round 1 判断的 phase 加载对应章节（吸筹→ch02，派发→ch03，Spring→ch04）
   - Round 3：ch01（三大原则详述）+ ch05（市场本质交易法）
   - Round 4-5：ch05 + ch06（综合分析）
4. **历史简报注入**：Round 4 注入历史简报全文（从 workflow.py 的归档机制获取），用于对照上次预判

### 3. 每轮 LLM 调用模式

```python
# 伪代码 - 与 web_brief.py 的 call_agnes() 同一模式
messages = [
    {"role": "system", "content": skill_knowledge},  # Round 1 的 system prompt
]

# Round 1
messages.append({"role": "user", "content": kline_data + round1_prompt})
messages.append({"role": "assistant", "content": llm_call(messages)})  # Round 1 输出

# Round 2（system prompt 已含在 messages[0]，后续轮次只需追加 user 消息）
# 选择性加载章节并追加到 system context
messages.append({"role": "user", "content": round2_prompt + selective_chapter})
messages.append({"role": "assistant", "content": llm_call(messages)})  # Round 2 输出

# ... Round 3-5 同理
```

**关键点：**
- 每轮要求 LLM 输出结构化 JSON（`response_format: json_object`）
- 最后一轮（Round 5）结束后，把 5 轮的 JSON 输出合并为一个完整简报
- 如果某轮 LLM 调用失败，后续轮次跳过，简报标注"分析不完整"

### 4. 简报生成

5 轮分析完成后，`brief_writer.py` 将结果组装为 Markdown 简报：
- 复用 `workflow.py` 的模板格式和归档机制
- 各轮 JSON 输出映射到简报的对应章节
- 简报中的"关键价位"和"措施和行动"表格格式与现有模板一致（scanner/brief_parser.py 可解析）

### 5. KNOWLEDGE.md 更新

`knowledge_updater.py` 在所有股票分析完成后：
- 调用一次 LLM（附带所有简报摘要 + 当前 KNOWLEDGE.md 内容）
- 让 LLM 判断是否有新的模式总结/个股迭代/规则修正/失败案例
- 输出更新后的 KNOWLEDGE.md（或追加条目）

### 6. Scanner 集成

修改 `scanner/price_scanner.py`：
- 增加 `--watchlist` 参数，默认指向 `wyckoff-auto/watchlist.json`
- 从 watchlist 读取 active 股票代码列表
- 仍用 `brief_parser.py` 解析对应简报提取触发点（简报路径不变）
- 过滤：只扫描 watchlist 中 active 股票的简报

### 7. GitHub Actions Workflow

```yaml
# wyckoff-auto.yml
name: 威科夫自动分析
on:
  schedule:
    - cron: "0 9 * * 1-5"  # 北京时间 17:00（收盘后30分钟）
  workflow_dispatch:
    inputs:
      trade_date:
        description: "指定交易日 YYYY-MM-DD"
        required: false
```

- 在 daily-brief.yml 之后运行（16:35 + 30min = 17:05）
- Secrets 复用：`AGNES_API_KEY`, `AGNES_BASE_URL`, `AGNES_MODEL`
- 提交生成的简报和更新的 KNOWLEDGE.md 到仓库

## Code Style

遵循 `web_brief.py` 的风格：

```python
#!/usr/bin/env python3
"""威科夫自动化分析引擎 -- 多轮 LLM 调用 + 结构化输出。

独立脚本，不依赖 TRAE 运行时。设计目标：
- 在 GitHub Actions 每日收盘后运行；
- 读取 watchlist.json 中的观察名单；
- 对每只股票执行 5 轮 LLM 分析（看盘五步法）；
- 生成结构化简报到 analysis-brief/ 目录。

环境变量（GitHub Actions secrets）：
  AGNES_API_KEY    Agnes AI 的 API Key（必填）
  AGNES_BASE_URL   Agnes API 网关
  AGNES_MODEL      文本模型名
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


def log(*a):
    print("[wyckoff-auto]", *a, file=sys.stderr, flush=True)
```

**约定：**
- 纯 stdlib 为主（urllib, json, pathlib, datetime）
- 子进程调用 `fetch_kline.py` 拉取 K 线数据（复用现有代码，不重复实现）
- LLM 调用封装在 `llm_client.py`，与 `web_brief.py` 的 `call_agnes()` 同模式
- 所有 LLM 输出要求 JSON 格式（`response_format: json_object`）
- 失败降级：单只股票分析失败不阻塞其他股票

## Testing Strategy

- **单元测试**：`wyckoff-auto/tests/`
  - `test_context_builder.py`：K线数据格式化、skill知识加载、章节选择逻辑
  - `test_llm_client.py`：mock API 响应，验证重试和降级
  - `test_brief_writer.py`：5轮 JSON -> Markdown 简报的映射
  - `test_watchlist.py`：观察名单读取和过滤
- **集成测试**：
  - 使用 `--dry-run` 对单只股票跑完整 5 轮（需 AGNES_API_KEY）
  - 验证生成的简报能被 `brief_parser.py` 正确解析
- **CI 验证**：GitHub Actions workflow 手动触发验证

## Boundaries

- **Always**:
  - 每轮 LLM 输出必须为 JSON 格式
  - 单只股票失败不阻塞其他股票
  - 简报格式兼容现有 scanner/brief_parser.py 的正则解析
  - 归档旧简报后再写新简报（复用 workflow.py 逻辑）
- **Ask first**:
  - 修改现有 `scanner/price_scanner.py` 的入口逻辑
  - 修改 `analysis-brief/KNOWLEDGE.md` 的更新方式
  - 新增 GitHub Actions secrets
- **Never**:
  - 不修改 `wyckoff/` 工程模块（detectors/data pipeline）
  - 不修改 `.agents/skills/` 下的 skill 文件内容
  - 不修改 `stockexpert-daily-brief/` 模块
  - 不引入 LangChain/LlamaIndex 等框架

## Success Criteria

1. `python wyckoff-auto/analyzer.py --code 002279 --dry-run` 能完成 5 轮 LLM 调用并打印结构化结果
2. 生成的简报被 `brief_parser.py` 解析后能提取出支撑/阻力/止损/进场价位
3. `python -m scanner.price_scanner --watchlist wyckoff-auto/watchlist.json` 能正常运行
4. GitHub Actions workflow 手动触发后能生成简报并提交到仓库
5. LLM 调用失败时简报标注"分析不完整"，不崩溃

## Open Questions

1. **Token 预算**：5 轮 × N 只股票的 API 调用成本？需要限制每日分析的股票数量吗？
2. **KNOWLEDGE.md 自动更新**：让 LLM 自动更新知识库的风险--是否需要人工审核后再提交？
3. **每日看板联动**：是否需要把 daily-brief 的中军候选自动加入 watchlist？（当前规格为手动维护，但未来可扩展）
