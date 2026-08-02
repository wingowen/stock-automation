#!/usr/bin/env python3
"""模型评估 —— 评分器（纯函数，零依赖，可本地单测）。

输入：单只股票 5 轮分析返回的 rounds（list[dict]，由 analyze_stock 产出）。
输出：4 维度分数（0-100）+ 加权总分 + 明细（便于报告与调试）。

维度：
  structural  结构合规   = 完成轮数 / 5
  schema      schema完整 = 必填字段「存在且合法」占比（嵌套/列表逐字段）
  consistency 轮间一致   = 跨轮逻辑检查通过率
  constraint  约束遵守   = 主板禁推 / 价格正数 / 枚举合法 通过率
"""
from __future__ import annotations

import re

from rubric import (
    FORBIDDEN_CODE_RE,
    ROUND_SCHEMAS,
    STRICT_POSITIVE_PRICE_KEYS,
    WEIGHTS,
)


# ── 基础校验 helper ─────────────────────────────────────────
def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _field_present_and_valid(spec: dict, value) -> tuple[bool, bool]:
    """返回 (present, valid)。

    present: 字段是否出现（dict 有该 key）。
    valid  : 类型/枚举/非空 是否合法：
             - str : 非空字符串
             - num : 数值
             - bool: 布尔
             - dict/list: 类型匹配（内容合法性由递归检查计入）
    """
    kind = spec.get("kind")
    enum = spec.get("enum")
    if kind == "str":
        # 注意：prompt 通篇允许「无法判断的字段填空字符串 "" 或 null」，
        # 因此空串是合法的「诚实未知」信号，不算缺陷；仅校验类型与枚举。
        ok = isinstance(value, str)
        if enum is not None and ok:
            ok = value in enum
        return True, ok
    if kind == "num":
        return True, _is_num(value)
    if kind == "bool":
        return True, isinstance(value, bool)
    if kind == "dict":
        return True, isinstance(value, dict)
    if kind == "list":
        return True, isinstance(value, list)
    return True, True


def _walk(schema: dict, obj) -> tuple[int, int]:
    """递归统计 (必填字段总数, 合法字段数)。obj 应为 dict。"""
    total = 0
    valid = 0
    if not isinstance(obj, dict):
        # 对象本身非法，schema 顶层所有字段记为缺失
        for _k, sub in schema.items():
            total += _count_fields(sub)
        return total, 0

    for key, subspec in schema.items():
        present, ok = _field_present_and_valid(subspec, obj.get(key, "__MISSING__"))
        if not present or not ok:
            # 缺失或非法：本字段及其全部嵌套子字段均记为未达标
            total += _count_fields(subspec)
            continue
        # 字段合法：记 1 分，并递归校验其嵌套/列表元素
        total += 1
        valid += 1
        nested = subspec.get("nested")
        if nested is not None and isinstance(obj.get(key), dict):
            t, v = _walk(nested, obj[key])
            total += t
            valid += v
        item = subspec.get("item")
        if item is not None and isinstance(obj.get(key), list):
            for elem in obj[key]:
                t, v = _walk(item, elem)
                total += t
                valid += v
    return total, valid


def _count_fields(spec: dict) -> int:
    """仅统计某字段子树下的必填字段总数（用于缺失时的 total 累加）。"""
    total = 1
    if spec.get("nested") is not None:
        for sub in spec["nested"].values():
            total += _count_fields(sub)
    if spec.get("item") is not None:
        for sub in spec["item"].values():
            total += _count_fields(sub)
    return total


# ── 四个维度 ───────────────────────────────────────────────
def score_structural(rounds: list) -> float:
    """结构合规：完成轮数 / 5。"""
    completed = sum(1 for r in rounds if isinstance(r, dict))
    return completed / 5 * 100.0


def score_schema(rounds: list) -> tuple[float, list]:
    """schema 完整度：所有轮必填字段「存在且合法」占比。"""
    total = 0
    valid = 0
    for i, r in enumerate(rounds):
        schema = ROUND_SCHEMAS.get(i + 1)
        if schema is None:
            continue
        if not isinstance(r, dict):
            # 该轮缺失，整轮必填计入 total 但不计 valid
            for sub in schema.values():
                total += _count_fields(sub)
            continue
        t, v = _walk(schema, r)
        total += t
        valid += v
    score = (valid / total * 100.0) if total else 100.0
    return score, [f"必填字段 {valid}/{total} 合法"]


def score_consistency(rounds: list) -> tuple[float, list]:
    """轮间一致性：跨轮逻辑自洽检查。"""
    d = {i + 1: r for i, r in enumerate(rounds) if isinstance(r, dict)}
    checks: list[tuple[str, bool]] = []

    # C1: R4 direction == R4 prediction.direction（历史截断/枚举不一致 bug）
    if 4 in d:
        r4 = d[4]
        a = r4.get("direction")
        b = (r4.get("prediction") or {}).get("direction")
        checks.append(("R4.direction == prediction.direction", a is not None and b is not None and a == b))

    # C2: R5 position_size 枚举合法
    if 5 in d:
        ps = d[5].get("position_size")
        checks.append(("R5.position_size ∈ {1/3仓,1/2仓,空仓}", ps in {"1/3仓", "1/2仓", "空仓"}))

    # C3: R5 risk_reward.ratio 可计算（数值）
    if 5 in d:
        rr = (d[5].get("risk_reward") or {}).get("ratio")
        checks.append(("R5.risk_reward.ratio 为数值", _is_num(rr)))

    # C4: R1 phase 枚举合法
    if 1 in d:
        phase = d[1].get("phase")
        checks.append(("R1.phase 枚举合法", phase in {"accumulation", "distribution", "spring", "none"}))

    # C5: R3 effort_result 字段类型/枚举合法
    if 3 in d:
        er = d[3].get("effort_result") or {}
        sa = er.get("stopping_action")
        cons = er.get("consistent")
        checks.append(("R3.effort_result.stopping_action 枚举合法", sa in {"none", "spring", "upthrust", "no_demand", None}))
        checks.append(("R3.effort_result.consistent 为布尔", isinstance(cons, bool)))

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    score = (passed / total * 100.0) if total else 100.0
    return score, [f"{name}: {'✓' if ok else '✗'}" for name, ok in checks]


def _collect_strings(obj, out: list) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_strings(v, out)


def _collect_price_values(obj, out: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in STRICT_POSITIVE_PRICE_KEYS and _is_num(v):
                out.append((k, v))
            else:
                _collect_price_values(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_price_values(v, out)


def score_constraint(rounds: list) -> tuple[float, list]:
    """约束遵守：主板禁推 / 价格正数 / confidence 枚举。"""
    checks: list[tuple[str, bool]] = []

    # D1: 扫描所有字符串，禁止出现科创板/创业板/北交所代码
    strings: list[str] = []
    for r in rounds:
        _collect_strings(r, strings)
    forbidden_hits = []
    for s in strings:
        hits = re.findall(FORBIDDEN_CODE_RE, s)
        if hits:
            forbidden_hits.extend(hits)
    checks.append(("主板禁推(无 688/30xxxx/8xxxxx 代码)", len(forbidden_hits) == 0))

    # D2: 关键价格字段必须为正数
    prices: list[tuple[str, float]] = []
    for r in rounds:
        _collect_price_values(r, prices)
    bad_prices = [(k, v) for k, v in prices if not (v > 0)]
    checks.append(("价格字段均为正数", len(bad_prices) == 0))

    # D3: R4 prediction.confidence 枚举合法
    conf = None
    for r in rounds:
        if isinstance(r, dict) and isinstance(r.get("prediction"), dict):
            conf = r["prediction"].get("confidence")
            break
    checks.append(("R4.prediction.confidence ∈ {high,mid,low}", conf in {"high", "mid", "low"}))

    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    score = (passed / total * 100.0) if total else 100.0
    notes = [f"{name}: {'✓' if ok else '✗'}" for name, ok in checks]
    if forbidden_hits:
        notes.append(f"命中禁推代码: {sorted(set(forbidden_hits))}")
    if bad_prices:
        notes.append(f"非正价格: {bad_prices[:5]}")
    return score, notes


# ── 汇总 ───────────────────────────────────────────────────
def score_stock(rounds: list) -> dict:
    """对单只股票打分，返回 4 维分数 + 总分 + 明细。"""
    s_struct = score_structural(rounds)
    s_schema, n_schema = score_schema(rounds)
    s_cons, n_cons = score_consistency(rounds)
    s_constr, n_constr = score_constraint(rounds)

    total = (
        s_struct * WEIGHTS["structural"]
        + s_schema * WEIGHTS["schema"]
        + s_cons * WEIGHTS["consistency"]
        + s_constr * WEIGHTS["constraint"]
    )

    return {
        "structural": round(s_struct, 1),
        "schema": round(s_schema, 1),
        "consistency": round(s_cons, 1),
        "constraint": round(s_constr, 1),
        "total": round(total, 1),
        "completed_rounds": sum(1 for r in rounds if isinstance(r, dict)),
        "notes": {
            "schema": n_schema,
            "consistency": n_cons,
            "constraint": n_constr,
        },
    }


def score_model(model_result: dict) -> dict:
    """对单模型多股票结果汇总评分。

    返回：每股票分 + 维度均值 + 模型总分（各维度跨股票均值加权）。
    """
    stocks = model_result.get("stocks", [])
    per_stock = []
    for s in stocks:
        rounds = s.get("rounds", []) if isinstance(s, dict) else []
        sc = score_stock(rounds)
        per_stock.append({
            "code": s.get("code"),
            "name": s.get("name", ""),
            "completed_rounds": sc["completed_rounds"],
            "error": s.get("error"),
            "scores": sc,
        })

    if not per_stock:
        return {"per_stock": [], "avg": {k: 0.0 for k in WEIGHTS}, "total": 0.0}

    avg = {}
    for dim in WEIGHTS:
        vals = [p["scores"][dim] for p in per_stock]
        avg[dim] = round(sum(vals) / len(vals), 1)

    total = sum(avg[dim] * w for dim, w in WEIGHTS.items())
    avg["total"] = round(total, 1)

    return {
        "per_stock": per_stock,
        "avg": avg,
        "total": avg["total"],
    }
