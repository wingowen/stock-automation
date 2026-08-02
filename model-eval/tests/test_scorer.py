#!/usr/bin/env python3
"""scorer 单元测试 —— 确定性、零网络依赖。

覆盖：
- 满分样例（5 轮全合法）→ 四维度均应为 100
- 缺失轮次 → 结构合规按比例下降
- schema 不完整 → schema 维度 < 100
- 轮间不一致（R4 direction != prediction.direction，历史 bug 回归）
- 约束违反：禁推代码 / 负价格 / confidence 枚举非法
- 模型聚合
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "eval"))

from scorer import score_stock, score_model  # noqa: E402


def _perfect_rounds():
    return [
        {  # Round 1
            "background": "trading_range",
            "background_reasoning": "近 3 月在 10-12 区间横盘",
            "phase": "accumulation",
            "phase_stage": "B",
            "phase_reasoning": "低位横盘出现 SC/AR",
            "trend": "sideways",
            "trend_description": "横盘",
            "key_levels": {"support": 10.0, "resistance": 12.0},
            "trading_range": {"exists": True, "high": 12.0, "low": 10.0, "duration_days": 30},
        },
        {  # Round 2
            "patterns": [{"name": "SC", "date": "2026-07-01", "description": "x",
                          "significance": "high", "volume_character": "放量", "price_action": "大阳"}],
            "recent_key_pattern": {"name": "SC", "date": "2026-07-01", "description": "关键底部"},
            "volume_analysis": {"recent_trend": "放量", "vs_average": 1.5, "interpretation": "放量止跌"},
        },
        {  # Round 3
            "supply_demand": {"dominant_force": "demand", "strength": "moderate", "reasoning": "x"},
            "cause_effect": {"has_cause": True, "cause_description": "x",
                             "potential_effect": "x", "cause_sufficient": True},
            "effort_result": {"consistent": True, "stopping_action": "none",
                              "stopping_description": "x", "absorption": "none", "absorption_description": "x"},
            "nature_summary": "需求主导",
        },
        {  # Round 4
            "background_conclusion": "震荡吸筹",
            "short_term": "偏多",
            "direction": "bullish",
            "key_levels": {"resistance": 12.0, "resistance_2": 13.0, "support": 10.0, "support_2": 9.0},
            "current_phase": "accumulation B",
            "prediction": {"direction": "bullish", "target_price": 13.0, "time_window": "1周",
                           "confidence": "mid", "conditions": "放量突破"},
            "history_comparison": {"has_history": False, "last_prediction": "", "actual_result": "",
                                   "deviation": "", "lesson": ""},
        },
        {  # Round 5
            "entry_conditions": {"scenario": "回踩支撑", "price_range": {"low": 10.0, "high": 10.5},
                                 "volume_requirement": "放量", "confirmation_signal": "阳线"},
            "abandon_conditions": {"scenario": "破位", "break_level": 9.0, "reason": "跌破支撑"},
            "follow_up_conditions": {"add_position": "突破加仓",
                                     "take_profit": {"level": 13.0, "description": "前高"},
                                     "trailing_stop": 12.0, "time_stop": "2周"},
            "position_size": "1/3仓",
            "risk_reward": {"risk": 1.0, "reward": 3.0, "ratio": 3.0},
            "action_summary": "观望为主，回踩不破可轻仓",
        },
    ]


def test_perfect_scores_100():
    sc = score_stock(_perfect_rounds())
    assert sc["structural"] == 100.0
    assert sc["schema"] == 100.0
    assert sc["consistency"] == 100.0
    assert sc["constraint"] == 100.0
    assert sc["total"] == 100.0
    assert sc["completed_rounds"] == 5


def test_missing_rounds_lowers_structural():
    rounds = _perfect_rounds()[:3]
    sc = score_stock(rounds)
    assert sc["structural"] == 60.0  # 3/5
    assert sc["completed_rounds"] == 3


def test_schema_incompleteness():
    rounds = _perfect_rounds()
    # Round 1 删除 trend 字段，并置 background_reasoning 为空（非法）
    rounds[0].pop("trend", None)
    rounds[0]["background_reasoning"] = ""
    sc = score_stock(rounds)
    assert sc["schema"] < 100.0
    assert sc["schema"] > 0.0


def test_consistency_direction_mismatch():
    rounds = _perfect_rounds()
    # 制造历史 bug：direction 与 prediction.direction 不一致
    rounds[3]["direction"] = "bullish"
    rounds[3]["prediction"]["direction"] = "bearish"
    sc = score_stock(rounds)
    assert sc["consistency"] < 100.0
    # C1 应标记失败
    assert any("direction == prediction.direction" in n and "✗" in n
               for n in sc["notes"]["consistency"])


def test_consistency_perfect_passes():
    sc = score_stock(_perfect_rounds())
    assert all("✓" in n for n in sc["notes"]["consistency"])


def test_constraint_forbidden_code():
    rounds = _perfect_rounds()
    rounds[0]["background_reasoning"] = "参考 688001 走势"
    sc = score_stock(rounds)
    assert sc["constraint"] < 100.0
    assert any("禁推" in n for n in sc["notes"]["constraint"])


def test_constraint_negative_price():
    rounds = _perfect_rounds()
    rounds[0]["key_levels"]["support"] = -5.0
    sc = score_stock(rounds)
    assert sc["constraint"] < 100.0
    assert any("正数" in n for n in sc["notes"]["constraint"])


def test_constraint_confidence_enum():
    rounds = _perfect_rounds()
    rounds[3]["prediction"]["confidence"] = "super"
    sc = score_stock(rounds)
    assert sc["constraint"] < 100.0


def test_score_model_aggregation():
    good = _perfect_rounds()
    bad = _perfect_rounds()
    bad[3]["direction"] = "bullish"
    bad[3]["prediction"]["direction"] = "bearish"  # 一致性破坏
    model_result = {
        "model_id": "t", "label": "t", "provider": "gemini", "model": "m",
        "stocks": [
            {"code": "002279", "name": "A", "rounds": good, "completed_rounds": 5},
            {"code": "002611", "name": "B", "rounds": bad, "completed_rounds": 5},
        ],
    }
    scored = score_model(model_result)
    # good 总分 100；bad 仅 C1(方向不一致)失败：一致性检查共 6 项、过 5 项 = 83.3
    # → bad 总分 = (100+100+83.3+100)*0.25 = 95.8；模型均值 = (100+95.8)/2 = 97.9
    assert scored["avg"]["total"] == 97.9
    assert scored["total"] == 97.9


def test_score_model_no_stocks():
    scored = score_model({"model_id": "x", "stocks": []})
    assert scored["total"] == 0.0
