#!/usr/bin/env python3
"""模型评估 —— 运行器（复用 wyckoff-auto 分析引擎，跨模型跑 5 轮分析）。

关键设计：
- 不改写 wyckoff-auto 的任何逻辑，仅 import 其 analyze_stock + LLMClient；
- 通过 wyckoff-auto/ 加到 sys.path 实现跨目录复用（其路径均为绝对解析，不依赖 CWD）；
- dry_run=True：只取分析 rounds，不写生产简报 / 不更新知识库，评估与生产解耦；
- 每个模型从环境变量读取自己的 key（gemini→GEMINI_API_KEY，agnes→AGNES_API_KEY），
  因此一次评估可同时对比多个 provider。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 把 wyckoff-auto 加入导入路径（analyzer/config/llm_client 都在那里）
REPO_ROOT = Path(__file__).resolve().parents[2]
WA_DIR = REPO_ROOT / "wyckoff-auto"
if str(WA_DIR) not in sys.path:
    sys.path.insert(0, str(WA_DIR))

from analyzer import analyze_stock  # noqa: E402
from llm_client import LLMClient  # noqa: E402


def _resolve_key(spec: dict) -> tuple[str, str]:
    """返回 (api_key, base_url)，按 provider 自动选 env 名，支持 config 覆盖。"""
    provider = spec["provider"]
    key_env = spec.get("api_key_env") or f"{provider.upper()}_API_KEY"
    url_env = spec.get("base_url_env") or f"{provider.upper()}_BASE_URL"
    return os.environ.get(key_env, ""), os.environ.get(url_env, "")


def run_model_eval(spec: dict, stocks: list, trade_date: str) -> dict:
    """对单模型跑所有标的的 5 轮分析，返回原始结果（含 rounds）。

    Args:
        spec: {"id","label","provider","model","api_key_env"?,"base_url_env"?}
        stocks: [{"code":..,"name":..}, ...] 或 ["002279", ...]
        trade_date: YYYY-MM-DD
    Returns:
        {"model_id","label","provider","model","error"?, "stocks":[analyze_stock 结果...]}
    """
    provider = spec["provider"]
    model = spec.get("model") or ""
    api_key, base_url = _resolve_key(spec)

    if not api_key:
        return {
            "model_id": spec["id"],
            "label": spec.get("label", spec["id"]),
            "provider": provider,
            "model": model,
            "error": f"缺少 API Key 环境变量（已配置 {spec.get('api_key_env') or provider.upper()+'_API_KEY'} 的 secret 后重试）",
            "stocks": [],
        }

    client = LLMClient(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
    )

    results = []
    for s in stocks:
        code = s["code"] if isinstance(s, dict) else s
        name = s.get("name", "") if isinstance(s, dict) else ""
        try:
            res = analyze_stock(client, code, trade_date, dry_run=True)
            if not res.get("name"):
                res["name"] = name
            results.append(res)
        except Exception as e:  # 单只异常不拖垮整模型
            results.append({
                "code": code,
                "name": name,
                "trade_date": trade_date,
                "rounds": [],
                "completed_rounds": 0,
                "error": f"分析异常: {e}",
            })

    return {
        "model_id": spec["id"],
        "label": spec.get("label", spec["id"]),
        "provider": provider,
        "model": model,
        "stocks": results,
    }
