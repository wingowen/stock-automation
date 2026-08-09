#!/usr/bin/env python3
"""ntfy 推送通知模块

独立于 wyckoff-auto 模块，可直接被 wyckoff 各子模块使用。
从环境变量 NTFY_TOPIC_URL 读取推送地址，纯 urllib 实现，无外部依赖。
"""

from __future__ import annotations

import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger("wyckoff.notify")


def send_ntfy(
    title: str,
    message: str,
    priority: str = "default",
    tags: str = "",
    topic_url: str | None = None,
) -> bool:
    """发送 ntfy 推送通知

    Args:
        title: 通知标题
        message: 通知正文
        priority: default | high | urgent
        tags: 标签图标，如 "chart_with_upwards_trend"
        topic_url: 可选，覆盖环境变量 NTFY_TOPIC_URL

    Returns:
        bool: 发送成功返回 True
    """
    url = topic_url or os.environ.get("NTFY_TOPIC_URL", "")
    if not url:
        logger.info("ntfy 推送跳过: NTFY_TOPIC_URL 未配置或为空")
        return False

    # HTTP header 只支持 latin-1，中文需 URL 编码
    headers = {
        "Title": urllib.parse.quote(title, safe=""),
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = tags

    try:
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                logger.info("ntfy 通知已发送: %s", title)
                return True
            logger.warning("ntfy 返回非 200: %s", r.status)
            return False
    except urllib.error.URLError as e:
        logger.warning("ntfy 网络错误: %s", e)
        return False
    except Exception as e:
        logger.warning("ntfy 发送异常: %s", e)
        return False
