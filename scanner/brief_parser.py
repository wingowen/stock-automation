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