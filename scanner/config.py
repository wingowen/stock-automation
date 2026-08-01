"""价格扫描器配置——从环境变量读取"""

import os

# 飞书 Webhook（可选，空字符串 = 不启用）
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")

# ntfy 主题 URL（可选，空字符串 = 不启用）
NTFY_TOPIC_URL = os.environ.get("NTFY_TOPIC_URL", "")

# 日志目录（相对于项目根）
LOG_DIR = "scanner/logs"

# 防重复通知窗口（秒）
SUPPRESS_WINDOW_SEC = 3600  # 60 分钟