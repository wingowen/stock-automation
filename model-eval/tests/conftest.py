"""pytest 配置：把 wyckoff-auto 加入导入路径，便于测试其 llm_client 容错逻辑。"""
import sys
from pathlib import Path

WA = Path(__file__).resolve().parents[2] / "wyckoff-auto"
if str(WA) not in sys.path:
    sys.path.insert(0, str(WA))
