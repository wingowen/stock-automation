"""LLMClient 的 Gemini provider 单测（mock 网络，不依赖真实 API）。

回归守卫：
- LLMClient 必须暴露所有 provider 方法（防止模块级函数误插入类体导致方法丢失）
- Gemini payload 必须正确映射 OpenAI 风格 messages（system->systemInstruction，assistant->model）
- 成功响应能解析出文本
- 鉴权失败 / 空候选 / 空文本 必须返回 None（不得抛异常）
"""
from __future__ import annotations

import json
from unittest import mock

import llm_client
import urllib.error


def _fake_response(status: int, body: str):
    # 用 MagicMock 以支持 `with urlopen(...) as r:` 上下文协议
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = body.encode("utf-8")
    # 让 `with` 返回自身，确保 r.read() / r.status 可用
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_client_defaults_to_gemini_provider():
    # 未传 provider 时跟随 config.LLM_PROVIDER（默认 gemini）
    c = llm_client.LLMClient(api_key="k", model="m")
    assert c.provider == "gemini"


def test_llmclient_exposes_all_provider_methods():
    """回归守卫：_recover_json 误插入类体导致 _chat_gemini/_chat_agnes 丢失。

    确保 LLMClient 实例可访问所有 provider 分发方法，且 _recover_json
    是模块级函数（不是类方法）。
    """
    c = llm_client.LLMClient(provider="gemini", api_key="k", model="m")
    # chat 入口必须能分发到 provider 方法
    assert callable(getattr(c, "_chat_gemini", None)), "_chat_gemini 不是 LLMClient 方法"
    assert callable(getattr(c, "_chat_agnes", None)), "_chat_agnes 不是 LLMClient 方法"
    assert callable(getattr(c, "_build_gemini_payload", None)), "_build_gemini_payload 不是 LLMClient 方法"
    # _recover_json 应为模块级函数，不是类方法
    assert not hasattr(c, "_recover_json"), "_recover_json 不应是 LLMClient 的方法"
    assert callable(llm_client._recover_json), "_recover_json 应为模块级函数"


def test_build_gemini_payload_maps_roles():
    c = llm_client.LLMClient(provider="gemini", api_key="k", model="m")
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": '{"a":1}'},
        {"role": "user", "content": "U2"},
    ]
    payload = c._build_gemini_payload(messages, temperature=0.3, json_mode=True)

    # system → systemInstruction
    assert payload["systemInstruction"]["parts"][0]["text"] == "SYS"
    # 其余按角色映射，assistant 转为 model
    assert [c["role"] for c in payload["contents"]] == ["user", "model", "user"]
    # json_mode 使用 responseMimeType，而非 OpenAI 的 response_format
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["maxOutputTokens"] == 8192


def test_build_gemini_payload_without_system():
    c = llm_client.LLMClient(provider="gemini", api_key="k", model="m")
    payload = c._build_gemini_payload(
        [{"role": "user", "content": "hi"}], temperature=0.5, json_mode=False
    )
    assert "systemInstruction" not in payload
    assert "responseMimeType" not in payload["generationConfig"]


def test_chat_gemini_parses_text():
    c = llm_client.LLMClient(provider="gemini", api_key="k", model="m")
    body = json.dumps(
        {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
    )
    with mock.patch(
        "llm_client.urllib.request.urlopen",
        return_value=_fake_response(200, body),
    ):
        out = c._chat_gemini([{"role": "user", "content": "hi"}], 0.3, True)
    assert out == '{"ok": true}'


def test_chat_gemini_empty_candidates_returns_none():
    c = llm_client.LLMClient(provider="gemini", api_key="k", model="m")
    body = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}})
    with mock.patch(
        "llm_client.urllib.request.urlopen",
        return_value=_fake_response(200, body),
    ):
        out = c._chat_gemini([{"role": "user", "content": "hi"}], 0.3, True)
    assert out is None


def test_chat_gemini_empty_text_returns_none():
    c = llm_client.LLMClient(provider="gemini", api_key="k", model="m")
    body = json.dumps({"candidates": [{"content": {"parts": [{"text": "   "}]}}]})
    with mock.patch(
        "llm_client.urllib.request.urlopen",
        return_value=_fake_response(200, body),
    ):
        out = c._chat_gemini([{"role": "user", "content": "hi"}], 0.3, True)
    assert out is None


def test_chat_gemini_auth_error_returns_none():
    c = llm_client.LLMClient(provider="gemini", api_key="k", model="m")
    err = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
    with mock.patch(
        "llm_client.urllib.request.urlopen", side_effect=err
    ):
        out = c._chat_gemini([{"role": "user", "content": "hi"}], 0.3, True)
    assert out is None


def test_chat_gemini_rate_limit_retries_then_succeeds():
    c = llm_client.LLMClient(provider="gemini", api_key="k", model="m")
    body = json.dumps(
        {"candidates": [{"content": {"parts": [{"text": '{"ok":1}'}]}}]}
    )
    # 第一次 429，第二次成功
    side = [
        urllib.error.HTTPError("url", 429, "Too Many", {}, None),
        _fake_response(200, body),
    ]
    with mock.patch(
        "llm_client.urllib.request.urlopen", side_effect=side
    ):
        out = c._chat_gemini([{"role": "user", "content": "hi"}], 0.3, True)
    assert out == '{"ok":1}'
