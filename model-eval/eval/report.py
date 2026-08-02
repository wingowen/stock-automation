#!/usr/bin/env python3
"""模型评估 —— 报告生成（markdown 排行榜 + JSON，纯函数）。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def build_leaderboard(scored_models: list[dict]) -> list[dict]:
    """按模型总分降序排名。"""
    rows = []
    for m in scored_models:
        rows.append({
            "model_id": m["model_id"],
            "label": m["label"],
            "provider": m["provider"],
            "model": m["model"],
            "total": m["total"],
            "avg": m["avg"],
            "error": m.get("error"),
        })
    rows.sort(key=lambda r: (r["total"] if r["total"] is not None else -1), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def render_markdown(
    trade_date: str,
    leaderboard: list[dict],
    scored_models: list[dict],
    raw_paths: dict[str, str],
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# 模型对比评估报告\n")
    L.append(f"- 评估日期（交易日）：`{trade_date}`")
    L.append(f"- 生成时间：`{now}`")
    L.append("- 评分维度权重：结构 25% / schema 25% / 一致性 25% / 约束 25%")
    L.append(f"- 评估标的：{', '.join(r['model_id'] for r in scored_models) and ', '.join(sorted({s['code'] for m in scored_models for s in m.get('stocks', []) if s.get('code')}))}")
    L.append("")

    # 排行榜
    L.append("## 🏆 排行榜（按总分降序）\n")
    L.append("| 排名 | 模型 | 后端 | 模型名 | 总分 | 结构 | schema | 一致性 | 约束 |")
    L.append("|------|------|------|--------|------|------|--------|--------|------|")
    for r in leaderboard:
        avg = r["avg"]
        L.append(
            f"| {r['rank']} | {r['label']} | {r['provider']} | {r['model'] or '-'} "
            f"| **{r['total']}** | {avg.get('structural','-')} | {avg.get('schema','-')} "
            f"| {avg.get('consistency','-')} | {avg.get('constraint','-')} |"
        )
    L.append("")

    # 切换建议
    if leaderboard:
        best = leaderboard[0]
        L.append(f"> **切换建议**：当前综合最优为 **{best['label']}**（{best['provider']} / {best['model'] or '-'}），"
                 f"总分 {best['total']}。若需切换默认模型，将 `.github/workflows/wyckoff-auto.yml` 的 "
                 f"`LLM_PROVIDER` 与该模型的 key/model 配置对齐即可。\n")

    # 逐模型明细
    L.append("## 逐模型明细\n")
    for m in scored_models:
        L.append(f"### {m['label']}（{m['provider']} / {m['model'] or '-'}）\n")
        if m.get("error"):
            L.append(f"⚠️ 评估跳过：{m['error']}\n")
            continue
        if m["model_id"] in raw_paths:
            L.append(f"- 原始 rounds 存档：`{raw_paths[m['model_id']]}`")
        for ps in m["per_stock"]:
            sc = ps["scores"]
            L.append(f"\n**{ps['code']} {ps['name']}** — 完成 {ps['completed_rounds']}/5 轮，总分 {sc['total']}")
            if ps.get("error"):
                L.append(f"  - 错误：{ps['error']}")
            L.append(f"  - 维度：结构 {sc['structural']} / schema {sc['schema']} / 一致性 {sc['consistency']} / 约束 {sc['constraint']}")
            for dim, notes in sc["notes"].items():
                if notes:
                    L.append(f"  - {dim}：{'；'.join(notes)}")
        L.append("")

    # 方法论
    L.append("## 评分方法论\n")
    L.append("- **结构合规**：完成轮数 / 5，直接反映 LLM 调用稳定性与 JSON 解析成功率。")
    L.append("- **schema 完整度**：对 5 轮 prompt 定义的必填字段（含嵌套/列表）逐字段校验「存在且合法（类型/枚举/非空）」。")
    L.append("- **轮间一致性**：R4 `direction`==`prediction.direction`、R5 仓位枚举、R5 盈亏比可计算、R1/R3 枚举与布尔合法等跨轮逻辑自洽检查。")
    L.append("- **约束遵守**：主板禁推（扫描 688/30xxxx/8xxxxx 代码）、关键价格字段为正、confidence 枚举合法。")
    L.append("\n> 注：本评分聚焦「结构正确性与规则遵守」，属于可确定性复现的底线质量。\n"
             "> 若要评估「分析深度的语义质量」，可后续接入 LLM-judge（见 README），当前版本默认关闭。")
    return "\n".join(L)


def render_json(
    trade_date: str,
    leaderboard: list[dict],
    scored_models: list[dict],
) -> dict:
    return {
        "trade_date": trade_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "weights": {"structural": 0.25, "schema": 0.25, "consistency": 0.25, "constraint": 0.25},
        "leaderboard": leaderboard,
        "models": scored_models,
    }


def save_raw(rounds_by_model: dict[str, dict], out_dir: Path, date_str: str) -> dict[str, str]:
    """把每个模型的原始 rounds 单独存盘（避免报告 JSON 过大），返回 model_id->相对路径。"""
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for model_id, model_result in rounds_by_model.items():
        p = raw_dir / f"{date_str}_{model_id}.json"
        p.write_text(json.dumps(model_result, ensure_ascii=False, indent=2), encoding="utf-8")
        paths[model_id] = str(p.relative_to(out_dir.parent))
    return paths
