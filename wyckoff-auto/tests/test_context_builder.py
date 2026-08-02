"""wyckoff-auto 提示词/上下文构建层的单元测试。

这些用例同时是「回归守卫」：
- 锁定 round1 不得残留未替换的 {skill_core} 占位符（历史 bug）
- 锁定 round4/round5 的 {selective_chapter} / {cheatsheet} 占位符必须可替换且无残留
"""
from __future__ import annotations

import re

import context_builder as cb

ROUND_FILES = {
    "round1_background": [],
    "round2_patterns": ["{selective_chapter}"],
    "round3_nature": ["{selective_chapter}"],
    "round4_conclusion": ["{history_brief}", "{selective_chapter}"],
    "round5_action": ["{selective_chapter}", "{cheatsheet}"],
}

PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_]+\}")


def test_load_skill_core_nonempty_and_roles():
    core = cb.load_skill_core()
    assert isinstance(core, str) and core.strip()
    # system prompt 必须前置角色与铁律（仅主板 / 禁编造）
    assert core.startswith("你是威科夫操盘法分析助手")
    assert "主板" in core
    # 已剔除面向人工导航的噪声段
    assert "## Chapter Index" not in core
    assert "## Topic Index" not in core


def test_all_round_prompts_load_and_no_skill_core_leak():
    for name in ROUND_FILES:
        text = cb.load_prompt(name)
        assert text.strip(), f"{name} 加载为空"
        # 历史 bug 回归：round1 曾残留未替换的 {skill_core}
        assert "{skill_core}" not in text, f"{name} 仍残留 {{skill_core}} 占位符"


def test_round_placeholders_substitute_without_leftover():
    """每个 round 的已知占位符替换后，不得残留任何 {xxx}。"""
    for name, placeholders in ROUND_FILES.items():
        text = cb.load_prompt(name)
        simulated = text
        for ph in placeholders:
            assert ph in text, f"{name} 缺少预期占位符 {ph}"
            simulated = simulated.replace(ph, "[INJECTED]")
        leftover = PLACEHOLDER_RE.findall(simulated)
        assert not leftover, f"{name} 替换后残留占位符: {leftover}"


def test_chapters_injected_for_analysis_rounds():
    # 第②③④⑤步应各自注入非空章节；第④⑤步拿综合框架 ch05+ch06
    for rnd in (2, 3, 4, 5):
        ch = cb.load_chapters_for_round(rnd, "accumulation")
        assert ch.strip(), f"Round {rnd} 章节注入为空（综合框架丢失）"


def test_supporting_cheatsheet_loads():
    cheat = cb.load_supporting("cheatsheet.md")
    assert cheat.strip(), "cheatsheet.md 未加载（第⑤步行动纪律缺失）"
    assert "Spring" in cheat or "止损" in cheat


def test_compress_kline_handles_missing_fields():
    # brief_writer 用 .get()，compress 也应对缺字段健壮
    kline = {"code": "002429", "name": "兆驰股份"}
    out = cb.compress_kline_for_later_rounds(kline, {})
    assert isinstance(out, str)
