#!/usr/bin/env python3
"""威科夫自动化分析引擎 -- 多轮 LLM 调用 + 结构化输出。

独立脚本，不依赖 TRAE 运行时。设计目标：
- 在 GitHub Actions 每日收盘后运行；
- 读取 watchlist.json 中的观察名单；
- 对每只股票执行 5 轮 LLM 分析（看盘五步法）；
- 生成结构化简报到 analysis-brief/ 目录。

环境变量（GitHub Actions secrets）：
  LLM_PROVIDER     gemini（默认）| agnes（OpenAI 兼容）
  GEMINI_API_KEY   Google Gemini API Key（默认 provider 必填）
  GEMINI_MODEL     Gemini 模型名（默认 gemini-flash-latest）
  AGNES_API_KEY    Agnes AI 的 API Key（备选 provider 必填）
  AGNES_BASE_URL   Agnes API 网关
  AGNES_MODEL      文本模型名

用法：
  python analyzer.py                      # 分析观察名单中所有 active 股票
  python analyzer.py --code 002279        # 分析指定股票
  python analyzer.py --trade-date 2026-08-01  # 指定分析日期
  python analyzer.py --dry-run            # 仅打印不落盘
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

# 确保能 import 同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import BG_MONTHS, latest_trade_day
from context_builder import (
    compress_kline_for_later_rounds,
    fetch_kline_data,
    get_history_brief,
    load_chapters_for_round,
    load_prompt,
    load_skill_core,
    load_supporting,
)
from llm_client import LLMClient, load_watchlist


def log(*a):
    print("[analyzer]", *a, file=sys.stderr, flush=True)


# ── 5 轮分析定义 ─────────────────────────────────────────────
ROUND_NAMES = [
    "round1_background",
    "round2_patterns",
    "round3_nature",
    "round4_conclusion",
    "round5_action",
]

ROUND_LABELS = [
    "背景判断",
    "价量形态",
    "形态性质",
    "结论/预测",
    "措施和行动",
]


def analyze_stock(
    client: LLMClient,
    code: str,
    trade_date: str,
    bg_months: int = BG_MONTHS,
    dry_run: bool = False,
    max_rounds: int = 5,
) -> dict:
    """对单只股票执行多轮分析。

    Args:
        max_rounds: 1-5，限制分析轮次（用于快速测试/评估）

    Returns:
        {
            "code": code,
            "name": stock_name,
            "trade_date": trade_date,
            "rounds": [round1_json, round2_json, ...],
            "completed_rounds": 0-max_rounds,
            "history": {...},
            "kline_data": {...},
            "brief_path": "...",
            "error": None or error message,
        }
    """
    result = {
        "code": code,
        "name": "",
        "trade_date": trade_date,
        "rounds": [],
        "completed_rounds": 0,
        "history": {},
        "kline_data": {},
        "brief_path": "",
        "error": None,
    }

    # 1. 拉取 K 线数据
    log(f"[{code}] 拉取 K 线数据...")
    kline = fetch_kline_data(code, bg_months, trade_date)
    if kline.get("error"):
        result["error"] = f"K线拉取失败: {kline['error']}"
        return result
    result["kline_data"] = kline
    result["name"] = kline.get("name", "")

    # 2. 获取历史简报（传入 K线获取的股票名称）
    log(f"[{code}] 准备简报模板...")
    history = get_history_brief(code, trade_date, result["name"])
    result["history"] = history
    if not result["name"]:
        result["name"] = history.get("stock_name", "")

    # 3. 加载 skill 核心知识
    skill_core = load_skill_core()
    if not skill_core:
        result["error"] = "SKILL.md 加载失败"
        return result

    # 4. 构建 system prompt
    system_prompt = skill_core

    # 5. 5 轮分析
    messages = [{"role": "system", "content": system_prompt}]
    completed = 0
    background_phase = ""

    for i, (round_name, round_label) in enumerate(zip(ROUND_NAMES, ROUND_LABELS)):
        if i >= max_rounds:
            break
        log(f"[{code}] Round {i+1}/{max_rounds}: {round_label}...")

        # 加载 prompt 模板
        prompt_template = load_prompt(round_name)
        if not prompt_template:
            result["error"] = f"Prompt 模板缺失: {round_name}"
            break

        # 构建本轮 user 消息
        if i == 0:
            # Round 1: 完整 K 线数据
            user_content = f"股票代码: {code}\n股票名称: {result['name']}\n\n{prompt_template}\n\n---\n\n【K线数据】\n\n{kline.get('raw_output', '')}"
        elif i == 1:
            # Round 2: 压缩数据 + 选择性章节
            selective_chapter = load_chapters_for_round(2, background_phase)
            user_content = prompt_template.replace("{selective_chapter}", selective_chapter)
            compressed = compress_kline_for_later_rounds(kline, result["rounds"][0] if result["rounds"] else {})
            user_content += f"\n\n---\n\n【数据摘要】\n{compressed}"
        elif i == 2:
            # Round 3: 三大原则章节
            selective_chapter = load_chapters_for_round(3, background_phase)
            user_content = prompt_template.replace("{selective_chapter}", selective_chapter)
            compressed = compress_kline_for_later_rounds(kline, result["rounds"][0] if result["rounds"] else {})
            user_content += f"\n\n---\n\n【数据摘要】\n{compressed}"
        elif i == 3:
            # Round 4: 注入历史简报 + 综合章节(ch05/ch06)
            history_content = history.get("history_content", "（无历史简报）")
            selective_chapter = load_chapters_for_round(4, background_phase)
            user_content = prompt_template.replace("{history_brief}", history_content)
            user_content = user_content.replace("{selective_chapter}", selective_chapter)
            compressed = compress_kline_for_later_rounds(kline, result["rounds"][0] if result["rounds"] else {})
            user_content += f"\n\n---\n\n【数据摘要】\n{compressed}"
        else:
            # Round 5: 注入综合章节(ch05/ch06) + 决策纪律速查表
            selective_chapter = load_chapters_for_round(5, background_phase)
            cheatsheet = load_supporting("cheatsheet.md")
            user_content = prompt_template.replace("{selective_chapter}", selective_chapter)
            user_content = user_content.replace("{cheatsheet}", cheatsheet)

        messages.append({"role": "user", "content": user_content})

        # 调用 LLM
        log(f"[{code}] Round {i+1} 消息长度: system={len(messages[0]['content'])} user={len(user_content)}")
        round_result = client.chat_json(messages)
        if round_result is None:
            log(f"[{code}] Round {i+1} LLM 调用未返回有效结果（详见上方 llm_client 诊断日志）")
            result["error"] = f"Round {i+1} LLM 调用失败"
            break

        # 记录结果
        result["rounds"].append(round_result)
        messages.append({"role": "assistant", "content": json.dumps(round_result, ensure_ascii=False)})
        completed = i + 1

        # 提取背景判断结果用于后续章节选择
        if i == 0:
            phase = round_result.get("phase", "")
            background_phase = phase
            log(f"[{code}] 背景判断: {phase} / {round_result.get('background', '')}")

    result["completed_rounds"] = completed

    # 6. 写入简报（非 dry-run 时）
    if not dry_run and completed > 0:
        try:
            from brief_writer import write_brief
            brief_path = write_brief(result)
            result["brief_path"] = str(brief_path)
            log(f"[{code}] 简报已写入: {brief_path}")
        except Exception as e:
            log(f"[{code}] 简报写入失败: {e}")
            result["error"] = f"简报写入失败: {e}"

    return result


def run(watchlist_path: str | None = None, code: str | None = None,
        trade_date: str | None = None, dry_run: bool = False) -> int:
    """主入口：分析股票并生成简报。"""

    # 确定分析日期
    if trade_date:
        td = trade_date
    else:
        td = latest_trade_day().strftime("%Y-%m-%d")

    log(f"分析日期: {td}, dry_run={dry_run}")

    # 确定分析哪些股票
    if code:
        stocks = [{"code": code, "name": ""}]
    else:
        stocks = load_watchlist(watchlist_path)

    if not stocks:
        log("观察名单为空或无 active 股票")
        return 0

    log(f"待分析股票: {[s['code'] for s in stocks]}")

    # 初始化 LLM 客户端
    client = LLMClient()
    if not client.api_key:
        log(f"{client.provider.upper()}_API_KEY 未设置，无法分析")
        return 1

    # 逐只分析
    results = []
    for stock in stocks:
        stock_code = stock["code"]
        stock_name = stock.get("name", "")
        log(f"=== 分析 {stock_code} {stock_name} ===")

        try:
            result = analyze_stock(client, stock_code, td, dry_run=dry_run)
            results.append(result)

            if dry_run:
                print(f"\n{'='*70}")
                print(f"分析结果: {stock_code} {result.get('name', '')}")
                print(f"完成轮次: {result['completed_rounds']}/5")
                if result.get("error"):
                    print(f"错误: {result['error']}")
                for i, r in enumerate(result["rounds"]):
                    print(f"\n--- Round {i+1}: {ROUND_LABELS[i]} ---")
                    print(json.dumps(r, ensure_ascii=False, indent=2)[:2000])
                print(f"{'='*70}")

        except Exception as e:
            log(f"[{stock_code}] 分析异常: {e}")
            traceback.print_exc()
            results.append({
                "code": stock_code,
                "name": stock_name,
                "error": str(e),
                "completed_rounds": 0,
                "rounds": [],
            })

    # 更新知识库（非 dry-run 且有完成的简报）
    if not dry_run and any(r["completed_rounds"] > 0 for r in results):
        try:
            from knowledge_updater import update_knowledge
            update_knowledge(client, results, td)
        except Exception as e:
            log(f"知识库更新失败: {e}")

    # 汇总
    total = len(results)
    success = sum(1 for r in results if r["completed_rounds"] == 5)
    partial = sum(1 for r in results if 0 < r["completed_rounds"] < 5)
    failed = sum(1 for r in results if r["completed_rounds"] == 0)

    log(f"完成: {total} 只 | 成功(5轮): {success} | 部分: {partial} | 失败: {failed}")

    # ntfy 通知（非 dry-run）
    if not dry_run:
        _notify_results(td, results, total, success, partial, failed)

    return 0 if failed < total else 1


# 方向与置信的中文/图标映射，供 ntfy 通知使用
_DIR_EMOJI = {"bullish": "📈", "bearish": "📉", "neutral": "➖"}
_DIR_CN = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
_CONF_CN = {"high": "高", "mid": "中", "low": "低"}

# ntfy.sh 默认正文上限约 4096 字节，预留尾部与余量，超出则停止追加股票明细
_NTFY_MAX_BYTES = 3800


def _fmt(v, suffix: str = "") -> str:
    """格式化数值/字符串，空值统一显示破折号（0.0 视为有效价格）。"""
    if v is None:
        return "-"
    if isinstance(v, str) and v.strip() == "":
        return "-"
    return f"{v}{suffix}"


def _truncate(s, n: int = 42) -> str:
    """单行截断：去换行、超长加省略号（用于摘要类长文本）。"""
    if not s:
        return ""
    s = str(s).replace("\n", " ").strip()
    return s if len(s) <= n else s[:n] + "…"


def _format_stock_block(r: dict, name_part: str, rounds_done: int) -> list[str]:
    """把单只股票的多轮分析结果格式化为 ntfy 的多行摘要块。

    优先用 round4(结论) 与 round5(行动) 的结构化字段，缺失则回落占位符，
    避免上游字段不全时崩溃。
    """
    rounds = r.get("rounds", [])
    r4 = rounds[3] if len(rounds) > 3 else {}
    r5 = rounds[4] if len(rounds) > 4 else {}
    pred = r4.get("prediction", {}) or {}
    direction = pred.get("direction") or r4.get("direction") or "neutral"
    kl = r4.get("key_levels", {}) or {}
    entry = r5.get("entry_conditions", {}) or {}
    er = entry.get("price_range", {}) or {}
    rr = r5.get("risk_reward", {}) or {}
    abandon = r5.get("abandon_conditions", {}) or {}

    emoji = _DIR_EMOJI.get(direction, "➖")
    head = (
        f"{emoji} {r['code']}{name_part} [{rounds_done}/5] "
        f"{_DIR_CN.get(direction, direction)} | "
        f"阶段:{_truncate(r4.get('current_phase', ''), 16) or '-'} | "
        f"置信:{_CONF_CN.get(pred.get('confidence', ''), pred.get('confidence', '')) or '-'}"
    )
    block = [head]

    price_line = f"  目标:{_fmt(pred.get('target_price'))}"
    tw = r4.get("time_window") or pred.get("time_window")
    if tw:
        price_line += f"({_truncate(tw, 10)})"
    price_line += (
        f" | 支撑:{_fmt(kl.get('support'))}/{_fmt(kl.get('support_2'))} "
        f"阻力:{_fmt(kl.get('resistance'))}/{_fmt(kl.get('resistance_2'))}"
    )
    block.append(price_line)

    low, high = er.get("low"), er.get("high")
    if low is not None and high is not None:
        entry_range = f"{low}-{high}"
    elif low is not None:
        entry_range = f"{low}+"
    elif high is not None:
        entry_range = f"≤{high}"
    else:
        entry_range = "-"
    entry_line = f"  进场:{entry_range} | 仓位:{_fmt(r5.get('position_size'))}"
    if rr.get("ratio") is not None:
        entry_line += f" | 盈亏比:{_fmt(rr.get('ratio'))}"
    if abandon.get("break_level") is not None:
        entry_line += f" | 放弃破:{_fmt(abandon.get('break_level'))}"
    block.append(entry_line)

    short_term = r4.get("short_term", "")
    if short_term:
        block.append(f"  短期:{_truncate(short_term, 60)}")
    act = r5.get("action_summary", "")
    if act:
        block.append(f"  行动:{_truncate(act, 60)}")
    return block


def _notify_results(trade_date: str, results: list[dict], total: int, success: int, partial: int, failed: int) -> None:
    """分析完成后发送 ntfy 通知；每只股票展开方向/阶段/关键位/进场/仓位等结构化摘要。"""
    from llm_client import send_ntfy

    header = f"威科夫自动分析简报 {trade_date}"
    lines: list[str] = [header, ""]
    # 预留尾部（总计行）与余量；超长则停止追加股票明细，避免 ntfy 截断
    budget = _NTFY_MAX_BYTES - len(header.encode("utf-8")) - 200

    for r in results:
        code = r["code"]
        name = r.get("name", "")
        rounds_done = r["completed_rounds"]
        name_part = f" {name}" if name else ""

        if rounds_done >= 4:
            block = _format_stock_block(r, name_part, rounds_done)
        elif rounds_done > 0:
            block = [f"⚠️ {code}{name_part} [{rounds_done}/5] 部分完成 - {r.get('error', '')}"]
        else:
            block = [f"❌ {code}{name_part} [0/5] 失败 - {r.get('error', '未知错误')}"]

        candidate = lines + [""] + block
        if len("\n".join(candidate).encode("utf-8")) > budget and len(lines) > 2:
            lines.append("… 更多股票明细见 GitHub Pages / analysis-brief")
            break
        lines = candidate

    lines.append(f"\n总计: {total} | 成功: {success} | 部分: {partial} | 失败: {failed}")

    priority = "urgent" if failed == total else "high" if failed > 0 else "default"
    tags = "chart_with_upwards_trend" if failed == 0 else "warning"
    title = f"威科夫分析 {trade_date} ({success}/{total})"

    ok = send_ntfy(title, "\n".join(lines), priority=priority, tags=tags)
    if ok:
        log(f"ntfy 通知已发送: {title}")
    else:
        log(f"ntfy 通知发送失败（请检查 NTFY_TOPIC_URL 是否配置正确）: {title}")


def main() -> int:
    ap = argparse.ArgumentParser(description="威科夫自动化分析引擎")
    ap.add_argument("--code", help="分析指定股票代码（6位数字）")
    ap.add_argument("--trade-date", help="指定分析日期 YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="仅打印不落盘")
    ap.add_argument("--watchlist", help="观察名单文件路径（默认 wyckoff-auto/watchlist.json）")
    args = ap.parse_args()

    return run(
        watchlist_path=args.watchlist,
        code=args.code,
        trade_date=args.trade_date,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
