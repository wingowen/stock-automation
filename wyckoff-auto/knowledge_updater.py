#!/usr/bin/env python3
"""知识库更新模块 -- LLM 辅助更新 KNOWLEDGE.md。

在所有股票分析完成后，调用一次 LLM：
- 附带所有简报摘要 + 当前 KNOWLEDGE.md 内容
- 让 LLM 判断是否有新的模式总结/个股迭代/规则修正/失败案例
- 追加条目到 KNOWLEDGE.md 的对应章节
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from config import KNOWLEDGE_FILE

sys.path.insert(0, str(Path(__file__).resolve().parent))


def log(*a):
    print("[knowledge_updater]", *a, file=sys.stderr, flush=True)


SYSTEM_PROMPT = """你是威科夫分析知识库管理员。基于今日的多只股票分析结果，判断是否需要更新知识库。

知识库 KNOWLEDGE.md 有 4 个维度：
1. 模式总结：跨个股共性规律（A 股环境下的威科夫形态变种）
2. 个股跨期迭代：每只股票历史预判 vs 实际走势的偏差
3. 规则修正：对原书规则的 A 股本地化调整
4. 失败案例库：被市场证伪的判断

更新规则：
- 只在有新发现时更新，无则返回空更新
- 新增条目追加到对应章节表格末尾
- 每条必须标注来源简报路径
- 不修改历史条目

输出 JSON：
{
  "updates": {
    "patterns": [{"pattern": "...", "description": "...", "case": "...", "source": "..."}],
    "stock_iteration": [{"code": "...", "prediction": "...", "actual": "...", "deviation": "...", "lesson": "..."}],
    "rule_corrections": [{"original": "...", "correction": "...", "reason": "...", "source": "..."}],
    "failures": [{"stock": "...", "prediction": "...", "actual": "...", "root_cause": "...", "prevention": "..."}]
  },
  "summary": "一句话总结今日更新（如'无更新'或'新增2条模式总结'）"
}
"""


def update_knowledge(client, results: list[dict], trade_date: str) -> None:
    """调用 LLM 更新 KNOWLEDGE.md。

    Args:
        client: LLMClient 实例
        results: analyzer.py 的所有股票分析结果
        trade_date: 分析日期
    """
    # 读取当前知识库
    if KNOWLEDGE_FILE.exists():
        current_kb = KNOWLEDGE_FILE.read_text(encoding="utf-8")
    else:
        log("KNOWLEDGE.md 不存在，跳过更新")
        return

    # 构建简报摘要
    brief_summaries = []
    for r in results:
        if r.get("completed_rounds", 0) == 0:
            continue

        code = r["code"]
        name = r.get("name", "")
        rounds = r.get("rounds", [])

        # 提取关键信息
        r1 = rounds[0] if len(rounds) > 0 else {}
        r4 = rounds[3] if len(rounds) > 3 else {}
        pred = r4.get("prediction", {}) if r4 else {}

        summary = f"股票: {code} {name}\n"
        summary += f"背景: {r1.get('background', '?')} / {r1.get('phase', '?')} (Stage {r1.get('phase_stage', '?')})\n"
        summary += f"方向: {r4.get('direction', '?')}\n"
        summary += f"预判: {pred.get('direction', '?')}, 目标 {pred.get('target_price', '?')}, 置信度 {pred.get('confidence', '?')}\n"
        summary += f"关键价位: 阻力 {r4.get('key_levels', {}).get('resistance', '?')} / 支撑 {r4.get('key_levels', {}).get('support', '?')}\n"

        # 历史对照
        hc = r4.get("history_comparison", {}) if r4 else {}
        if hc and hc.get("has_history"):
            summary += f"上次预判: {hc.get('last_prediction', '?')}\n"
            summary += f"实际走势: {hc.get('actual_result', '?')}\n"
            summary += f"偏差: {hc.get('deviation', '?')}\n"

        brief_summaries.append(summary)

    if not brief_summaries:
        log("无已完成的简报，跳过知识库更新")
        return

    user_content = f"""分析日期: {trade_date}

今日完成 {len(brief_summaries)} 只股票的威科夫分析。

## 简报摘要

{chr(10).join(brief_summaries)}

## 当前知识库内容

{current_kb[:8000]}

---

请基于今日分析结果，判断是否需要更新知识库。严格按 JSON schema 输出。
"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    log("调用 LLM 更新知识库...")
    update_result = client.chat_json(messages)

    if not update_result:
        log("LLM 调用失败，跳过知识库更新")
        return

    updates = update_result.get("updates", {})

    # 应用更新
    new_content = current_kb
    changed = False

    # 1. 模式总结
    patterns = updates.get("patterns", [])
    if patterns:
        for p in patterns:
            entry = f"| {trade_date} | {p.get('pattern', '')} | {p.get('description', '')} | {p.get('case', '')} | {p.get('source', '')} |"
            new_content = _append_to_table(new_content, "一、模式总结", entry)
            changed = True

    # 2. 个股跨期迭代
    iterations = updates.get("stock_iteration", [])
    if iterations:
        for it in iterations:
            code = it.get("code", "")
            # 找到对应股票的章节
            section_marker = f"### {code}.SZ" if code.startswith("0") else f"### {code}.SH"
            entry = f"| {trade_date} | {it.get('prediction', '')} | {it.get('actual', '')} | {it.get('deviation', '')} | {it.get('lesson', '')} |"
            new_content = _append_to_stock_section(new_content, section_marker, code, entry, trade_date)
            changed = True

    # 3. 规则修正
    corrections = updates.get("rule_corrections", [])
    if corrections:
        for c in corrections:
            entry = f"| {c.get('original', '')} | {c.get('correction', '')} | {c.get('reason', '')} | {c.get('source', '')} |"
            new_content = _append_to_table(new_content, "三、规则修正", entry)
            changed = True

    # 4. 失败案例
    failures = updates.get("failures", [])
    if failures:
        for f in failures:
            entry = f"| {trade_date} | {f.get('stock', '')} | {f.get('prediction', '')} | {f.get('actual', '')} | {f.get('root_cause', '')} | {f.get('prevention', '')} |"
            new_content = _append_to_table(new_content, "四、失败案例库", entry)
            changed = True

    if changed:
        KNOWLEDGE_FILE.write_text(new_content, encoding="utf-8")
        log(f"知识库已更新: {update_result.get('summary', '')}")
    else:
        log(f"知识库无更新: {update_result.get('summary', '无新发现')}")


def _append_to_table(content: str, section_name: str, entry: str) -> str:
    """在指定章节的表格末尾追加一行。

    简单策略：找到章节标题后的第一个表格，在最后一行后追加。
    """
    lines = content.split("\n")
    in_section = False
    last_table_row = -1
    found_section = False

    for i, line in enumerate(lines):
        if section_name in line:
            in_section = True
            found_section = True
            continue
        if in_section:
            # 检测下一个章节（## 开头）
            if line.startswith("## ") and section_name not in line:
                break
            if line.startswith("|") and not line.startswith("|---"):
                last_table_row = i

    if last_table_row >= 0:
        lines.insert(last_table_row + 1, entry)
        return "\n".join(lines)

    if not found_section:
        log(f"未找到章节: {section_name}")

    return content


def _append_to_stock_section(content: str, section_marker: str, code: str, entry: str, trade_date: str) -> str:
    """在个股跨期迭代章节中追加条目。"""
    lines = content.split("\n")
    section_idx = -1
    last_table_row = -1

    for i, line in enumerate(lines):
        if section_marker in line or f"### {code}" in line:
            section_idx = i
            continue
        if section_idx >= 0:
            if line.startswith("### ") and section_marker not in line:
                break
            if line.startswith("|") and not line.startswith("|---"):
                last_table_row = i

    if last_table_row >= 0:
        lines.insert(last_table_row + 1, entry)
        return "\n".join(lines)

    # 如果没有找到该股票的章节，在"二、个股跨期迭代"章节末尾添加新章节
    if section_idx < 0:
        new_section = f"\n### {code}\n\n| 分析日期 | 预判 | 实际走势 | 偏差原因 | 教训 |\n|---|---|---|---|---|\n{entry}\n"
        # 找到"三、规则修正"之前插入
        for i, line in enumerate(lines):
            if "三、规则修正" in line:
                lines.insert(i, new_section)
                return "\n".join(lines)

    return content
