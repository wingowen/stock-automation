#!/usr/bin/env python3
"""模型评估 —— 编排入口。

用法：
  python model-eval/eval/cli.py                 # 按 eval_config.json 跑全部模型对比
  python model-eval/eval/cli.py --models gemini-flash   # 只跑指定模型
  python model-eval/eval/cli.py --trade-date 2026-08-01 # 指定交易日

产出：
  model-eval/reports/<date>.md      人类可读排行榜 + 明细
  model-eval/reports/<date>.json    机器可读（含每模型每股票分数）
  model-eval/reports/raw/<date>_<model>.json  原始 rounds 存档
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# 确保能 import 同目录模块与 wyckoff-auto
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
WA_DIR = REPO_ROOT / "wyckoff-auto"
for p in (str(HERE), str(WA_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from config import latest_trade_day  # noqa: E402
from runner import run_model_eval  # noqa: E402
from scorer import score_model  # noqa: E402
from report import build_leaderboard, render_markdown, render_json, save_raw  # noqa: E402

CONFIG_PATH = HERE.parent / "eval_config.json"


def log(*a):
    print("[model-eval]", *a, file=sys.stderr, flush=True)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"配置缺失: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def resolve_stocks(cfg: dict) -> list[dict]:
    stocks = cfg.get("stocks") or []
    norm = []
    for s in stocks:
        if isinstance(s, str):
            norm.append({"code": s, "name": ""})
        elif isinstance(s, dict) and s.get("code"):
            norm.append(s)
    if not norm:
        raise SystemExit("eval_config.json 的 stocks 为空或格式错误")
    return norm


def main() -> int:
    ap = argparse.ArgumentParser(description="威科夫分析 —— 多模型对比评估")
    ap.add_argument("--models", help="只跑指定模型 id（逗号分隔），默认跑全部")
    ap.add_argument("--trade-date", help="指定交易日 YYYY-MM-DD（默认最近交易日）")
    args = ap.parse_args()

    cfg = load_config()
    stocks = resolve_stocks(cfg)

    trade_date = args.trade_date or cfg.get("trade_date") or latest_trade_day().strftime("%Y-%m-%d")
    max_rounds = int(cfg.get("max_rounds", 5))
    log(f"交易日: {trade_date} | 标的: {[s['code'] for s in stocks]} | 轮次: {max_rounds}")

    model_specs = cfg.get("models", [])
    if args.models:
        want = {m.strip() for m in args.models.split(",")}
        model_specs = [m for m in model_specs if m.get("id") in want]
    if not model_specs:
        raise SystemExit("没有可评估的模型（检查 --models 或 eval_config.json）")

    # 逐模型评估 + 打分
    model_results = []
    rounds_by_model: dict[str, dict] = {}
    for spec in model_specs:
        log(f"=== 评估模型: {spec.get('label', spec['id'])} ({spec['provider']}) ===")
        raw = run_model_eval(spec, stocks, trade_date, max_rounds=max_rounds)
        rounds_by_model[spec["id"]] = raw
        scored = score_model(raw)
        scored["model_id"] = raw["model_id"]
        scored["label"] = raw["label"]
        scored["provider"] = raw["provider"]
        scored["model"] = raw["model"]
        if raw.get("error"):
            scored["error"] = raw["error"]
        model_results.append(scored)
        log(f"  -> 总分 {scored['total']}（{scored['avg']}）" + (f" | 跳过: {raw['error']}" if raw.get("error") else ""))

    # 报告
    leaderboard = build_leaderboard(model_results)
    out_dir = REPO_ROOT / cfg.get("output_dir", "model-eval/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = date.today().strftime("%Y-%m-%d")

    raw_paths = save_raw(rounds_by_model, out_dir, date_str)
    md = render_markdown(trade_date, leaderboard, model_results, raw_paths)
    js = render_json(trade_date, leaderboard, model_results)

    md_path = out_dir / f"{date_str}.md"
    js_path = out_dir / f"{date_str}.json"
    md_path.write_text(md, encoding="utf-8")
    js_path.write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"报告已写入: {md_path} / {js_path}")

    # 摘要输出（CI 日志可见）
    print("\n" + "=" * 64)
    print("模型对比评估摘要")
    print("=" * 64)
    print(f"{'排名':<4}{'模型':<22}{'总分':<8}{'结构':<7}{'schema':<8}{'一致':<7}{'约束':<7}")
    for r in leaderboard:
        a = r["avg"]
        print(f"{r['rank']:<4}{r['label'][:20]:<22}{r['total']:<8}"
              f"{a.get('structural','-'):<7}{a.get('schema','-'):<8}{a.get('consistency','-'):<7}{a.get('constraint','-'):<7}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
