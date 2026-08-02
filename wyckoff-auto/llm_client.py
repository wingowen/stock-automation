#!/usr/bin/env python3
"""LLM API 调用封装 -- 支持多轮对话 + JSON 输出。

复用 web_brief.py 的 call_agnes() 模式，扩展为支持 messages 数组累积。
通过 OpenAI 兼容 API 调用 Agnes AI。

用法：
    from llm_client import LLMClient

    client = LLMClient()
    messages = [{"role": "system", "content": "..."}]
    resp = client.chat(messages)  # 返回文本或 None
    resp = client.chat_json(messages)  # 返回 dict 或 None
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
    """Agnes AI（OpenAI 兼容）调用客户端。

    支持多轮对话：传入完整 messages 数组，返回模型文本。
    每次调用独立重试，失败返回 None。
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ):
        self.api_key = api_key or AGNES_API_KEY
        self.base_url = (base_url or AGNES_BASE_URL).rstrip("/")
        self.model = model or AGNES_MODEL

    def chat(
        self,
        messages: list[dict],
        temperature: float = TEMPERATURE,
        json_mode: bool = True,
    ) -> str | None:
        """调用 chat/completions，返回模型文本。失败返回 None。

        Args:
            messages: OpenAI 格式的消息数组
            temperature: 采样温度
            json_mode: 是否要求 JSON 格式输出
        """
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
            print("AGNES_API_KEY 未设置")
            sys.exit(1)
        print(f"测试 API 连通性: {client.base_url} / {client.model}")
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
