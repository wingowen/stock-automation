"""统一的事件数据结构 (Event Schema)

所有威科夫形态检测器的输出都必须符合这个 schema。
详见 SPEC.md §7.2。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Optional


# 阶段机状态 (Phase A)
Phase = Literal["A", "B", "C", "D", "E"]

# 已知形态 (v1)
EventType = Literal[
    # 吸筹
    "PS", "SC", "AR", "ST", "Spring", "Test", "SOS", "LPS", "BU", "JAC",
    # 派发
    "BC", "UTAD", "SOW", "LPSY",
    # 复合
    "Shakeout", "NoSC", "CauseEffect", "EffortResult",
]


@dataclass(frozen=True)
class Event:
    """单个形态事件 (见 SPEC §7.2)"""
    date: date
    code: str
    name: str
    type: EventType
    strength: float  # 0-1, 检测器自评
    rvol: float      # 相对量能 = 当日量 / N日均量

    # 可选字段
    phase: Optional[Phase] = None
    price: Optional[float] = None
    range_high: Optional[float] = None
    range_low: Optional[float] = None
    range_days: Optional[int] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """序列化为 JSON 友好字典"""
        d = {
            "date": self.date.isoformat(),
            "code": self.code,
            "name": self.name,
            "type": self.type,
            "strength": self.strength,
            "rvol": self.rvol,
        }
        if self.phase is not None:
            d["phase"] = self.phase
        if self.price is not None:
            d["price"] = self.price
        if self.range_high is not None:
            d["context"] = {
                "range_high": self.range_high,
                "range_low": self.range_low,
                "range_days": self.range_days,
            }
        if self.extra:
            d["extra"] = self.extra
        return d
