"""威科夫交易系统 (Wyckoff Trading System)

把《威科夫操盘法》一书中的 16+ 形态量化为可检测算法。
严格分层：Python 硬检测 → LLM 解释。LLM 永远不做检测。

模块结构：
- analyzers/   技术指标分析器（趋势、量能、MACD、RSI）
- data/        数据管道（AkShare/Baostock 数据源）
- detectors/   形态检测器（Spring、SpringBoard 等）
- strategies/  YAML 策略定义文件
- signal_scanner.py  综合信号扫描器
"""

__version__ = "0.2.0"

from .analyzers import TrendAnalyzer, TrendResult, VolumeAnalyzer, VolumeResult
from .schemas import Event
from .signal_scanner import SignalScanner, SignalReport

__all__ = [
    "TrendAnalyzer", "TrendResult",
    "VolumeAnalyzer", "VolumeResult",
    "Event",
    "SignalScanner", "SignalReport",
]