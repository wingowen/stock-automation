#!/usr/bin/env python3
"""llm_client._recover_json 容错解析测试（覆盖 Gemini 偶发尾部冗余/代码块包裹）。"""
import sys
from pathlib import Path

WA = Path(__file__).resolve().parents[2] / "wyckoff-auto"
if str(WA) not in sys.path:
    sys.path.insert(0, str(WA))

from llm_client import _recover_json  # noqa: E402


def test_plain():
    assert _recover_json('{"a": 1}') == {"a": 1}


def test_extra_data():
    # Gemini 偶发在 JSON 后附加冗余文本（"Extra data" 场景）
    assert _recover_json('{"a": 1} 补充说明文字') == {"a": 1}
    assert _recover_json('{"direction":"bullish"}\n\n') == {"direction": "bullish"}


def test_fenced():
    assert _recover_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _recover_json('```\n{"a": 1}\n```') == {"a": 1}


def test_nested_with_trailing():
    assert _recover_json('prefix {"a": {"b": 2}} suffix text') == {"a": {"b": 2}}


def test_unparseable():
    assert _recover_json("not json at all") is None
    assert _recover_json("") is None
    assert _recover_json("   ") is None
    assert _recover_json('{"a":') is None  # 不完整 JSON
