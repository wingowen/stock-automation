#!/usr/bin/env python3
"""模型评估 —— 评分 rubric 数据层（纯数据 + 校验 helper，零依赖）。

定义威科夫 5 轮分析的「必填字段结构」「枚举取值」「4 维度权重」。
评分器 scorer.py 消费这里的结构，对模型输出的 rounds 逐字段校验打分。

设计原则：
- 字段结构严格对齐 wyckoff-auto/prompts/round{1..5}_*.txt 的 JSON Schema，
  改 prompt 时这里要同步，保证评估与实际要求一致。
- 不 import 任何项目模块，确保可脱离网络单独单测。
"""
from __future__ import annotations

# ── 字段类型标记 ───────────────────────────────────────────
# kind: str | num | bool | dict | list
# enum: 合法取值集合（str 类型字段适用）
# nested: dict 子字段结构（kind=dict 时）
# item: list 元素的结构（kind=list 时，元素视为单条 dict）
FIELD = dict

def S(enum=None, nested=None, item=None) -> FIELD:
    """便捷构造字段描述。"""
    return {"kind": "str", "enum": enum, "nested": nested, "item": item}


def N(nested=None, item=None) -> FIELD:
    return {"kind": "num", "enum": None, "nested": nested, "item": item}


def B() -> FIELD:
    return {"kind": "bool", "enum": None, "nested": None, "item": None}


def D(nested: dict) -> FIELD:
    return {"kind": "dict", "enum": None, "nested": nested, "item": None}


def L(item: dict) -> FIELD:
    return {"kind": "list", "enum": None, "nested": None, "item": item}


# ── 5 轮必填结构 ───────────────────────────────────────────
ROUND_SCHEMAS: dict[int, dict] = {
    1: {
        "background": S(enum={"bull", "bear", "trading_range"}),
        "background_reasoning": S(),
        "phase": S(enum={"accumulation", "distribution", "spring", "none"}),
        "phase_stage": S(),
        "phase_reasoning": S(),
        "trend": S(enum={"up", "down", "sideways"}),
        "trend_description": S(),
        "key_levels": D({
            "support": N(),
            "resistance": N(),
        }),
        "trading_range": D({
            "exists": B(),
            "high": N(),
            "low": N(),
            "duration_days": N(),
        }),
    },
    2: {
        "patterns": L({
            "name": S(),
            "date": S(),
            "description": S(),
            "significance": S(enum={"high", "mid", "low"}),
            "volume_character": S(enum={"放量", "缩量", "正常"}),
            "price_action": S(),
        }),
        "recent_key_pattern": D({
            "name": S(),
            "date": S(),
            "description": S(),
        }),
        "volume_analysis": D({
            "recent_trend": S(enum={"放量", "缩量", "平稳"}),
            "vs_average": N(),
            "interpretation": S(),
        }),
    },
    3: {
        "supply_demand": D({
            "dominant_force": S(enum={"demand", "supply", "balanced"}),
            "strength": S(enum={"strong", "moderate", "weak"}),
            "reasoning": S(),
        }),
        "cause_effect": D({
            "has_cause": B(),
            "cause_description": S(),
            "potential_effect": S(),
            "cause_sufficient": B(),
        }),
        "effort_result": D({
            "consistent": B(),
            "stopping_action": S(enum={"none", "spring", "upthrust", "no_demand"}),
            "stopping_description": S(),
            "absorption": S(enum={"none", "buying", "selling"}),
            "absorption_description": S(),
        }),
        "nature_summary": S(),
    },
    4: {
        "background_conclusion": S(),
        "short_term": S(),
        "direction": S(enum={"bullish", "bearish", "neutral"}),
        "key_levels": D({
            "resistance": N(),
            "resistance_2": N(),
            "support": N(),
            "support_2": N(),
        }),
        "current_phase": S(),
        "prediction": D({
            "direction": S(enum={"bullish", "bearish", "neutral"}),
            "target_price": N(),
            "time_window": S(),
            "confidence": S(enum={"high", "mid", "low"}),
            "conditions": S(),
        }),
        "history_comparison": D({
            "has_history": B(),
            "last_prediction": S(),
            "actual_result": S(),
            "deviation": S(),
            "lesson": S(),
        }),
    },
    5: {
        "entry_conditions": D({
            "scenario": S(),
            "price_range": D({
                "low": N(),
                "high": N(),
            }),
            "volume_requirement": S(),
            "confirmation_signal": S(),
        }),
        "abandon_conditions": D({
            "scenario": S(),
            "break_level": N(),
            "reason": S(),
        }),
        "follow_up_conditions": D({
            "add_position": S(),
            "take_profit": D({
                "level": N(),
                "description": S(),
            }),
            "trailing_stop": N(),
            "time_stop": S(),
        }),
        "position_size": S(enum={"1/3仓", "1/2仓", "空仓"}),
        "risk_reward": D({
            "risk": N(),
            "reward": N(),
            "ratio": N(),
        }),
        "action_summary": S(),
    },
}

# ── 4 维度权重 ─────────────────────────────────────────────
# structural : 结构合规（完成轮数 / 5）
# schema     : schema 完整度（必填字段存在且非空/合法）
# consistency: 轮间一致性（跨轮逻辑自洽）
# constraint : 约束遵守（主板禁推、价格正数、枚举合法）
WEIGHTS: dict[str, float] = {
    "structural": 0.25,
    "schema": 0.25,
    "consistency": 0.25,
    "constraint": 0.25,
}

# 必须为正数的关键价格字段（用于 constraint 维度价格合理性检查）
STRICT_POSITIVE_PRICE_KEYS = {
    "support", "resistance", "resistance_2", "support_2",
    "target_price", "break_level", "low", "high", "level",
}

# 主板权限之外的禁推代码前缀（科创板 688 / 创业板 30xxxx / 北交所 8xxxxx）
FORBIDDEN_CODE_RE = r"\b(688\d{3}|30\d{4}|8\d{5})\b"
