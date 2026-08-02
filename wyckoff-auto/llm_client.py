#!/usr/bin/env python3
"""LLM API 调用封装 -- 多 provider 支持 + 多轮对话 + JSON 输出。

支持两种 provider：
- gemini（默认）：Google Generative Language API，直连公网，GitHub 境外
  runner 稳定，消除之前 agnes 跨境超时的问题。
- agnes：OpenAI 兼容网关（国内节点，作为可切换的备选）。

用法：
    from llm_client import LLMClient

    client = LLMClient()                 # 读 LLM_PROVIDER 决定后端
    client = LLMClient(provider="gemini")
    messages = [{"role": "system", "content": "..."}]
    resp = client.chat(messages)         # 返回文本或 None
    resp = client.chat_json(messages)    # 返回 dict 或 None
"""
from __future__ import annotations

import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config import (
    AGNES_API_KEY,
    AGNES_BASE_URL,
    AGNES_MODEL,
    GEMINI_API_KEY,
    GEMINI_BASE_URL,
    GEMINI_MODEL,
    LLM_PROVIDER,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    TEMPERATURE,
)

logger = logging.getLogger("wyckoff_auto.llm_client")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[llm_client] %(levelname)s %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


def log(*a):
    """兼容壳：统一路由到标准 logging（info 级），便于分级过滤。"""
    logger.info(" ".join(str(x) for x in a))


def _safe_read(err) -> str:
    """安全读取 HTTPError 的响应体（可能为空、非文本或超大）。"""
    try:
        return err.read().decode("utf-8", errors="replace")[:300]
    except Exception:
        return ""


class LLMClient:
    """多 provider LLM 客户端。

    支持多轮对话：传入完整 messages 数组，返回模型文本。
    每次调用独立重试，失败返回 None。
    """

    def __init__(
        self,
        provider: str = "",
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ):
        # provider 优先级：显式参数 > 环境变量 LLM_PROVIDER > 默认 gemini
        self.provider = (provider or LLM_PROVIDER).lower()
        if self.provider not in ("gemini", "agnes"):
            log(f"未知 provider '{self.provider}'，回退到 gemini")
            self.provider = "gemini"

        if self.provider == "gemini":
            self.api_key = api_key or GEMINI_API_KEY
            self.base_url = (base_url or GEMINI_BASE_URL).rstrip("/")
            self.model = model or GEMINI_MODEL
        else:  # agnes / OpenAI 兼容
            self.api_key = api_key or AGNES_API_KEY
            self.base_url = (base_url or AGNES_BASE_URL).rstrip("/")
            self.model = model or AGNES_MODEL

    # ── 统一入口 ───────────────────────────────────────────
    def chat(
        self,
        messages: list[dict],
        temperature: float = TEMPERATURE,
        json_mode: bool = True,
    ) -> str | None:
        """调用模型，返回文本。失败返回 None。

        Args:
            messages: OpenAI 格式的消息数组（role/content）
            temperature: 采样温度
            json_mode: 是否要求 JSON 格式输出
        """
        if self.provider == "gemini":
            return self._chat_gemini(messages, temperature, json_mode)
        return self._chat_agnes(messages, temperature, json_mode)

    def chat_json(
        self,
        messages: list[dict],
        temperature: float = TEMPERATURE,
    ) -> dict | None:
        """调用 chat 并解析 JSON 输出。返回 dict 或 None。"""
        raw = self.chat(messages, temperature=temperature, json_mode=True)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            log(f"JSON 解析失败: {e}")
            log(f"原始输出前500字符: {raw[:500]}")
            return None

    # ── Gemini provider ───────────────────────────────────
    def _build_gemini_payload(
        self, messages: list[dict], temperature: float, json_mode: bool
    ) -> dict:
        """将 OpenAI 风格 messages 转为 Gemini generateContent 格式。

        - system 消息 → systemInstruction
        - user / assistant 交替 → contents（assistant 映射为 model）
        """
        system_text = ""
        contents = []
        for m in messages:
            role = m.get("role", "user")
            text = m.get("content", "")
            if role == "system":
                system_text = text
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": text}]})

        gen_config: dict = {
            "temperature": temperature,
            # 限制单轮最大输出，防止超长失控；gemini-flash 系列均支持
            "maxOutputTokens": 8192,
        }
        if json_mode:
            gen_config["responseMimeType"] = "application/json"

        payload: dict = {"contents": contents, "generationConfig": gen_config}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        return payload

    def _chat_gemini(
        self, messages: list[dict], temperature: float, json_mode: bool
    ) -> str | None:
        """调用 Gemini :generateContent，返回模型文本。失败返回 None。"""
        if not self.api_key:
            log("GEMINI_API_KEY 未设置，无法调用模型")
            return None

        payload = self._build_gemini_payload(messages, temperature, json_mode)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        last_status = None

        for attempt in range(1, MAX_RETRIES + 1):
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "X-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                    "User-Agent": "WyckoffAuto/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                    last_status = getattr(r, "status", None)
                    raw_body = r.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                body = _safe_read(e)
                if e.code in (400, 401, 403):
                    log(f"Gemini 鉴权/请求错误({e.code})，请检查 GEMINI_API_KEY 或请求格式：{body}")
                    return None
                if e.code == 429:
                    wait = min(2 ** attempt, 30)
                    log(f"Gemini 限流(429)，退避 {wait}s 后重试(第{attempt}次)：{body}")
                    time.sleep(wait)
                    continue
                log(f"Gemini HTTP 错误({e.code})，重试(第{attempt}次)：{body}")
                time.sleep(2 * attempt)
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                log(f"Gemini 网络错误(第{attempt}次): {e}")
                time.sleep(2 * attempt)
                continue

            # 解析响应（结构异常为非重试性）
            try:
                resp = json.loads(raw_body)
                candidates = resp.get("candidates", [])
                if not candidates:
                    fb = resp.get("promptFeedback", {})
                    log(f"Gemini 无候选输出（可能被安全策略拦截）: {fb}")
                    return None
                c0 = candidates[0]
                parts = c0.get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                if c0.get("finishReason") == "MAX_TOKENS":
                    log("Gemini 因 MAX_TOKENS 截断，输出可能不完整，建议检查 maxOutputTokens")
                if not text or not text.strip():
                    log(f"Gemini 返回空文本（HTTP {last_status}）| body前300: {raw_body[:300]}")
                    return None
                return text
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                log(f"Gemini 响应结构异常（非重试性）: {e} | HTTP {last_status} | body前300: {raw_body[:300]}")
                return None

        log(f"Gemini 调用最终失败（已重试 {MAX_RETRIES} 次），最后 HTTP 状态: {last_status}")
        return None

    # ── Agnes (OpenAI 兼容) provider ──────────────────────
    def _chat_agnes(
        self, messages: list[dict], temperature: float, json_mode: bool
    ) -> str | None:
        """调用 chat/completions（OpenAI 兼容），返回模型文本。失败返回 None。"""
        if not self.api_key:
            log("AGNES_API_KEY 未设置，无法调用模型")
            return None

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        last_status = None

        for attempt in range(1, MAX_RETRIES + 1):
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "WyckoffAuto/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                    last_status = getattr(r, "status", None)
                    raw_body = r.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                body = _safe_read(e)
                if e.code in (401, 403):
                    log(f"API 鉴权失败({e.code})，请检查 AGNES_API_KEY：{body}")
                    return None
                if e.code == 429:
                    wait = min(2 ** attempt, 30)
                    log(f"API 限流(429)，退避 {wait}s 后重试(第{attempt}次)：{body}")
                    time.sleep(wait)
                    continue
                log(f"API HTTP 错误({e.code})，重试(第{attempt}次)：{body}")
                time.sleep(2 * attempt)
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                log(f"API 网络错误(第{attempt}次): {e}")
                time.sleep(2 * attempt)
                continue
            # 网络层正常，但需校验响应结构（结构异常不应重试）
            try:
                resp = json.loads(raw_body)
                content = resp["choices"][0]["message"]["content"]
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                log(f"API 响应结构异常（非重试性）: {e} | HTTP {last_status} | body前300: {raw_body[:300]}")
                return None
            # 空响应诊断：HTTP 200 但 content 为空 == 静默失败，必须显式记录
            if not content or not content.strip():
                log(f"API 返回空响应（HTTP {last_status}）| body前300: {raw_body[:300]}")
                return None
            return content
        log(f"API 调用最终失败（已重试 {MAX_RETRIES} 次），最后 HTTP 状态: {last_status}")
        return None


# ── 观察名单读取 ────────────────────────────────────────────
def load_watchlist(path: str | None = None) -> list[dict]:
    """读取观察名单，返回 active 股票列表。

    Returns:
        [{"code": "002279", "name": "久其软件", ...}, ...]
    """
    from config import WATCHLIST_PATH

    wl_path = Path(path) if path else WATCHLIST_PATH
    if not wl_path.exists():
        log(f"观察名单不存在: {wl_path}")
        return []
    try:
        data = json.loads(wl_path.read_text(encoding="utf-8"))
        stocks = data.get("stocks", [])
        return [s for s in stocks if s.get("status") == "active"]
    except Exception as e:
        log(f"观察名单解析失败: {e}")
        return []


# ── 通知推送 ────────────────────────────────────────────────
def send_ntfy(title: str, message: str, priority: str = "default", tags: str = "") -> bool:
    """发送 ntfy 推送通知（纯 urllib，无外部依赖）。

    Args:
        title: 通知标题
        message: 通知正文
        priority: default | high | urgent
        tags: 标签图标，如 "chart_with_upwards_trend"
    """
    from config import NTFY_TOPIC_URL

    if not NTFY_TOPIC_URL:
        return False

    # HTTP header 只支持 latin-1，中文需 URL 编码
    headers = {"Title": urllib.parse.quote(title, safe=""), "Priority": priority}
    if tags:
        headers["Tags"] = tags

    try:
        req = urllib.request.Request(
            NTFY_TOPIC_URL,
            data=message.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        log(f"ntfy 推送失败: {e}")
        return False


# ── 自测入口 ────────────────────────────────────────────────
if __name__ == "__main__":
    if "--test" in sys.argv:
        client = LLMClient()
        if not client.api_key:
            print(f"{client.provider.upper()}_API_KEY 未设置")
            sys.exit(1)
        print(f"测试 API 连通性: provider={client.provider} model={client.model}")
        messages = [
            {"role": "system", "content": "你是测试助手。输出 JSON: {\"status\": \"ok\"}"},
            {"role": "user", "content": "请回复"},
        ]
        result = client.chat_json(messages)
        if result:
            print(f"API 连通正常: {result}")
        else:
            print("API 调用失败")
            sys.exit(1)
    else:
        print(__doc__)
