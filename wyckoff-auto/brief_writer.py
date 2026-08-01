#!/usr/bin/env python3
"""简报写入模块 -- 5 轮 JSON -> Markdown 简报。

将 analyzer.py 的 5 轮 LLM 输出组装为结构化 Markdown 简报，
写入 workflow.py 已生成的模板路径。

简报格式兼容 scanner/brief_parser.py 的正则解析：
- 头部: # {code} {name} 威科夫分析简报
- 关键价位行: | 关键价位 | 阻力：XX  支撑：XX |
- 措施和行动表: | 进场 | ... | / | 放弃 | ... | / | 跟进 | ... |
"""
from __future__ import annotations

import json
from pathlib import Path


def log(*a):
    print("[brief_writer]", *a, file=__import__("sys").stderr, flush=True)


def _safe_float(val, default=0.0) -> float:
    """安全提取浮点数。"""
    try:
        f = float(val)
        return f if f == f else default  # NaN 检查
    except (TypeError, ValueError):
        return default


def _fmt_price(val) -> str:
    """格式化价格显示。"""
    f = _safe_float(val, 0)
    return f"{f:.2f}" if f else "__"


def write_brief(result: dict) -> Path:
    """将 5 轮分析结果写入 Markdown 简报。

    Args:
        result: analyzer.py 的 analyze_stock() 返回值

    Returns:
        写入的简报文件路径
    """
    code = result["code"]
    name = result.get("name", "")
    trade_date = result.get("trade_date", "")
    rounds = result.get("rounds", [])
    completed = result.get("completed_rounds", 0)
    history = result.get("history", {})
    kline = result.get("kline_data", {})

    brief_path = Path(history.get("new_brief_path", ""))
    if not brief_path or not brief_path.exists():
        # 如果 workflow.py 没有生成模板，创建一个
        from config import BRIEF_ROOT
        import datetime as dt
        d = dt.datetime.strptime(trade_date, "%Y-%m-%d").date()
        month_dir = BRIEF_ROOT / f"{d.year:04d}-{d.month:02d}"
        month_dir.mkdir(parents=True, exist_ok=True)
        name_seg = f"_{name}" if name else ""
        brief_path = month_dir / f"{code}{name_seg}_{trade_date}.md"

    # 提取各轮数据
    r1 = rounds[0] if len(rounds) > 0 else {}
    r2 = rounds[1] if len(rounds) > 1 else {}
    r3 = rounds[2] if len(rounds) > 2 else {}
    r4 = rounds[3] if len(rounds) > 3 else {}
    r5 = rounds[4] if len(rounds) > 4 else {}

    name_part = f" {name}" if name else ""
    version = history.get("version", 1)
    history_ref = history.get("archived", "NONE")
    if history_ref and history_ref != "NONE":
        history_ref_str = f"[{history_ref}](../{history_ref})"
    else:
        history_ref_str = "无（首次分析）"

    # 提取关键价位
    r1_levels = r1.get("key_levels", {}) if r1 else {}
    r4_levels = r4.get("key_levels", {}) if r4 else {}
    resistance = _fmt_price(r4_levels.get("resistance") or r1_levels.get("resistance"))
    support = _fmt_price(r4_levels.get("support") or r1_levels.get("support"))
    resistance_2 = _fmt_price(r4_levels.get("resistance_2")) if r4_levels.get("resistance_2") else ""
    support_2 = _fmt_price(r4_levels.get("support_2")) if r4_levels.get("support_2") else ""

    # 构建简报内容
    lines: list[str] = []

    # 头部
    lines.append(f"# {code}{name_part} 威科夫分析简报")
    lines.append("")
    lines.append(f"> 分析日期：{trade_date}  |  版本：v{version}  |  完成轮次：{completed}/5")
    lines.append(f"> 历史简报：{history_ref_str}")
    lines.append(f"> 知识库：[KNOWLEDGE.md](../KNOWLEDGE.md)")
    lines.append("")

    # 〇 历史上下文
    lines.append("## 〇、历史上下文")
    lines.append("")
    if history.get("history_content") and history["history_content"] != "（无历史简报，首次分析）":
        lines.append(f"参考上一版简报的核心预判：")
        lines.append("")
        # 提取上次预判摘要
        hc = history["history_content"]
        # 尝试提取上次的关键价位和预判
        import re
        kv = re.search(r"关键价位.*?阻力[：:]\s*([\d.]+).*?支撑[：:]\s*([\d.]+)", hc)
        if kv:
            lines.append(f"- 上次预判阻力：{kv.group(1)} / 支撑：{kv.group(2)}")
        lines.append(f"- 完整历史简报见：{history_ref_str}")
    else:
        lines.append("_首次分析，无历史上下文_")
    lines.append("")

    # 一 数据摘要
    lines.append("## 一、数据摘要")
    lines.append("")
    summary = kline.get("summary", "")
    if summary:
        lines.append("```")
        lines.append(summary)
        lines.append("```")
    else:
        lines.append("_（K线数据获取不完整）_")
    lines.append("")

    # 二 看盘五步法分析
    lines.append("## 二、看盘五步法分析")
    lines.append("")

    # 第①步 背景判断
    lines.append("### 第①步 背景判断")
    lines.append("")
    if r1:
        bg = r1.get("background", "未知")
        phase = r1.get("phase", "none")
        stage = r1.get("phase_stage", "none")
        trend = r1.get("trend", "unknown")
        lines.append(f"- **背景**：{bg}")
        lines.append(f"- **阶段**：{phase}（Stage {stage}）")
        lines.append(f"- **趋势**：{trend}")
        lines.append(f"- **判断依据**：{r1.get('background_reasoning', '')}")
        lines.append(f"- **阶段依据**：{r1.get('phase_reasoning', '')}")
        tr = r1.get("trading_range", {})
        if tr and tr.get("exists"):
            lines.append(f"- **震荡区间**：{tr.get('low', '?')} ~ {tr.get('high', '?')}（约 {tr.get('duration_days', '?')} 天）")
    else:
        lines.append("_（本轮分析未完成）_")
    lines.append("")

    # 第②步 价量形态
    lines.append("### 第②步 价量形态")
    lines.append("")
    if r2:
        patterns = r2.get("patterns", [])
        if patterns:
            lines.append("| 形态 | 日期 | 描述 | 量能 | 显著性 |")
            lines.append("|---|---|---|---|---|")
            for p in patterns:
                lines.append(
                    f"| {p.get('name', '')} | {p.get('date', '')} | {p.get('description', '')} "
                    f"| {p.get('volume_character', '')} | {p.get('significance', '')} |"
                )
            lines.append("")
        rkp = r2.get("recent_key_pattern", {})
        if rkp:
            lines.append(f"**最近关键形态**：{rkp.get('name', '')}（{rkp.get('date', '')}）")
            lines.append(f"- {rkp.get('description', '')}")
        va = r2.get("volume_analysis", {})
        if va:
            lines.append(f"\n**量能分析**：近期{va.get('recent_trend', '')}，量比 {va.get('vs_average', '?')}")
            lines.append(f"- {va.get('interpretation', '')}")
    else:
        lines.append("_（本轮分析未完成）_")
    lines.append("")

    # 第③步 形态性质
    lines.append("### 第③步 形态性质")
    lines.append("")
    if r3:
        sd = r3.get("supply_demand", {})
        ce = r3.get("cause_effect", {})
        er = r3.get("effort_result", {})
        lines.append(f"- **供求关系**：{sd.get('dominant_force', '')}（力度：{sd.get('strength', '')}）")
        lines.append(f"  - {sd.get('reasoning', '')}")
        lines.append(f"- **因果关系**：{'有充分准备过程' if ce.get('has_cause') else '准备过程不足'}")
        lines.append(f"  - {ce.get('cause_description', '')}")
        lines.append(f"  - 预期效果：{ce.get('potential_effect', '')}")
        lines.append(f"- **努力-结果**：{'一致' if er.get('consistent') else '不一致（停止行为）'}")
        sa = er.get("stopping_action", "none")
        if sa and sa != "none":
            lines.append(f"  - 停止行为：{sa} - {er.get('stopping_description', '')}")
        ab = er.get("absorption", "none")
        if ab and ab != "none":
            lines.append(f"  - 吸收行为：{ab} - {er.get('absorption_description', '')}")
        lines.append(f"\n**性质总结**：{r3.get('nature_summary', '')}")
    else:
        lines.append("_（本轮分析未完成）_")
    lines.append("")

    # 第④步 结论/预测
    lines.append("### 第④步 结论/预测")
    lines.append("")
    if r4:
        lines.append("| 维度 | 结论 |")
        lines.append("|---|---|")
        lines.append(f"| 大背景 | {r4.get('background_conclusion', '')} |")
        lines.append(f"| 短期 | {r4.get('short_term', '')} |")
        res_str = f"阻力：{resistance}"
        if resistance_2 and resistance_2 != "__":
            res_str += f" / {resistance_2}"
        sup_str = f"支撑：{support}"
        if support_2 and support_2 != "__":
            sup_str += f" / {support_2}"
        lines.append(f"| 关键价位 | {res_str}  {sup_str} |")
        lines.append(f"| 当前阶段 | {r4.get('current_phase', '')} |")
        lines.append(f"| 方向判断 | {r4.get('direction', '')} |")
        lines.append("")

        pred = r4.get("prediction", {})
        if pred:
            lines.append(f"**预判**：{pred.get('direction', '')}，目标 {pred.get('target_price', '?')}，"
                         f"窗口 {pred.get('time_window', '?')}，置信度 {pred.get('confidence', '?')}")
            lines.append(f"- 条件：{pred.get('conditions', '')}")

        hc = r4.get("history_comparison", {})
        if hc and hc.get("has_history"):
            lines.append(f"\n**历史对照**：{hc.get('deviation', '')}")
            if hc.get("lesson"):
                lines.append(f"- 教训：{hc.get('lesson', '')}")
    else:
        lines.append("_（本轮分析未完成）_")
    lines.append("")

    # 第⑤步 措施和行动
    lines.append("### 第⑤步 措施和行动")
    lines.append("")
    if r5:
        entry = r5.get("entry_conditions", {})
        abandon = r5.get("abandon_conditions", {})
        follow = r5.get("follow_up_conditions", {})

        entry_range = entry.get("price_range", {})
        entry_lo = _fmt_price(entry_range.get("low"))
        entry_hi = _fmt_price(entry_range.get("high"))

        lines.append("| 情景 | 条件 | 行动 |")
        lines.append("|---|---|---|")
        lines.append(
            f"| 进场 | {entry.get('scenario', '')}，"
            f"区间 {entry_lo}-{entry_hi}，{entry.get('volume_requirement', '')} "
            f"| {entry.get('confirmation_signal', '')}，{r5.get('position_size', '')} |"
        )
        lines.append(
            f"| 放弃 | {abandon.get('scenario', '')}，跌破 {abandon.get('break_level', '?')} "
            f"| {abandon.get('reason', '')} |"
        )
        tp = follow.get("take_profit", {})
        lines.append(
            f"| 跟进 | 加仓：{follow.get('add_position', '')} "
            f"| 止盈 {tp.get('level', '?')}（{tp.get('description', '')}），"
            f"移动止损 {follow.get('trailing_stop', '?')}，"
            f"时间止损 {follow.get('time_stop', '')} |"
        )

        rr = r5.get("risk_reward", {})
        if rr:
            lines.append(f"\n**风险收益比**：{rr.get('ratio', '?')}（风险 {rr.get('risk', '?')} / 收益 {rr.get('reward', '?')}）")
        lines.append(f"\n**行动总结**：{r5.get('action_summary', '')}")
    else:
        lines.append("_（本轮分析未完成）_")
    lines.append("")

    # 三 本次预判
    lines.append("## 三、本次预判（用于后续迭代验证）")
    lines.append("")
    if r4:
        pred = r4.get("prediction", {})
        lines.append("| 项目 | 预判 |")
        lines.append("|---|---|")
        lines.append(f"| 方向 | {pred.get('direction', r4.get('direction', ''))} |")
        lines.append(f"| 关键价位 | 阻力 {resistance} / 支撑 {support} |")
        lines.append(f"| 时间窗口 | {pred.get('time_window', '')} |")
        lines.append(f"| 触发条件 | {pred.get('conditions', '')} |")
        lines.append(f"| 目标价 | {pred.get('target_price', '')} |")
        lines.append(f"| 置信度 | {pred.get('confidence', '')} |")
    else:
        lines.append("_（分析未完成，无预判）_")
    lines.append("")

    # 四 迭代知识更新清单
    lines.append("## 四、迭代知识更新清单")
    lines.append("")
    lines.append("本次分析对 [KNOWLEDGE.md](../KNOWLEDGE.md) 的更新：")
    lines.append("")
    lines.append("- [ ] 模式总结：_（自动更新由 knowledge_updater.py 完成）_")
    lines.append("- [ ] 个股跨期迭代：_（自动更新由 knowledge_updater.py 完成）_")
    lines.append("- [ ] 规则修正：_（自动更新由 knowledge_updater.py 完成）_")
    lines.append("- [ ] 失败案例库：_（自动更新由 knowledge_updater.py 完成）_")
    lines.append("")

    # 五 风险提示
    lines.append("## 五、风险提示")
    lines.append("")
    lines.append("1. 本分析由 AI 自动生成，仅供参考，不构成投资建议")
    lines.append("2. 威科夫分析法基于历史数据，未来走势可能偏离预判")
    lines.append("3. A 股有涨跌幅限制、T+1 等特殊规则，实际交易需考虑流动性风险")
    if completed < 5:
        lines.append(f"4. ⚠️ 本次分析仅完成 {completed}/5 轮，结论可能不完整")
    lines.append("")

    # 写入文件
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text("\n".join(lines), encoding="utf-8")
    log(f"简报已写入: {brief_path}")

    return brief_path
