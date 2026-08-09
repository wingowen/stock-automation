"""策略加载器

从 daily_stock_analysis/strategies/ 移植的 YAML 策略系统。
自动加载 strategies/ 目录下所有 .yaml 文件，提供策略定义查询。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 策略缓存
_STRATEGIES: dict[str, dict] = {}
_LOADED = False


def _load_all() -> None:
    """加载所有 YAML 策略文件"""
    global _LOADED, _STRATEGIES
    if _LOADED:
        return

    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML 未安装，策略加载已禁用")
        _LOADED = True
        return

    strategies_dir = Path(__file__).parent
    for f in sorted(strategies_dir.glob("*.yaml")):
        if f.name == "__init__.yaml":
            continue
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if data and "name" in data:
                _STRATEGIES[data["name"]] = data
                logger.debug("已加载策略: %s (%s)", data["name"], data.get("display_name", ""))
        except Exception as e:
            logger.warning("策略文件加载失败 %s: %s", f.name, e)

    _LOADED = True
    logger.info("共加载 %d 个策略", len(_STRATEGIES))


def get_strategy(name: str) -> Optional[dict]:
    """获取指定策略的定义"""
    _load_all()
    return _STRATEGIES.get(name)


def get_all_strategies() -> dict[str, dict]:
    """获取所有已加载的策略"""
    _load_all()
    return dict(_STRATEGIES)


def get_strategies_by_category(category: str) -> list[dict]:
    """按分类获取策略"""
    _load_all()
    return [s for s in _STRATEGIES.values() if s.get("category") == category]


def get_strategies_by_regime(regime: str) -> list[dict]:
    """按市场状态获取策略"""
    _load_all()
    return [s for s in _STRATEGIES.values() if regime in s.get("market_regimes", [])]


def reload() -> None:
    """重新加载策略"""
    global _LOADED, _STRATEGIES
    _LOADED = False
    _STRATEGIES = {}
    _load_all()