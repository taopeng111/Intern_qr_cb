"""
events.py · 标准事件定义（v0.3）
--------------------------------------------------------------------
新增
✓ ConvertEvent      —— 债→股转股
✓ ForceRedeemEvent  —— 发行人强赎
✓ PutEvent          —— 投资者回售
保留
✓ Market / Signal / Order / Fill / Clock / Settlement
--------------------------------------------------------------------
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict

import pandas as pd

# ------------------------------------------------------------------
# 1. 事件类型枚举
# ------------------------------------------------------------------
class EventType(Enum):
    MARKET       = auto()
    SIGNAL       = auto()
    ORDER        = auto()
    FILL         = auto()
    CONVERT      = auto()     # 转股
    FORCE_REDEEM = auto()     # 强赎
    PUT          = auto()     # 回售
    CASH         = auto()     # 现金流（利息、到期兑付）
    CLOCK        = auto()     # 预留
    SETTLEMENT   = auto()     # 预留


# ------------------------------------------------------------------
# 2. 抽象父类：BaseEvent
# ------------------------------------------------------------------
_event_counter = itertools.count()          # 全局自增 ID

@dataclass(slots=True)
class BaseEvent:
    dt: pd.Timestamp
    id: int = field(default_factory=lambda: next(_event_counter), init=False)

    # 关键字参数，不影响位置顺序
    meta: Dict[str, Any] | None = field(default=None, kw_only=True)

    type: EventType = field(init=False, repr=False)

    def __repr__(self) -> str:
        attrs = ", ".join(
            f"{k}={v}" for k, v in self.__dict__.items()
            if k not in {"meta"} and v is not None
        )
        return f"<{self.__class__.__name__} {attrs}>"


# ------------------------------------------------------------------
# 3. 通用回测事件
# ------------------------------------------------------------------
@dataclass(slots=True)
class MarketEvent(BaseEvent):
    data: Dict[str, Dict[str, Any]]         # {'110057': {'close': 102.3, ...}, ...}
    type: EventType = field(init=False, default=EventType.MARKET, repr=False)


@dataclass(slots=True)
class SignalEvent(BaseEvent):
    symbol: str
    action: str                             # 'LONG' / 'SHORT' / 'EXIT'
    size: int = 0                           # 张数；0 = 让 broker 决定
    type: EventType = field(init=False, default=EventType.SIGNAL, repr=False)


@dataclass(slots=True)
class OrderEvent(BaseEvent):
    symbol: str
    order_type: str                         # 'MKT' / 'LMT'
    action: str                             # 'BUY' / 'SELL'
    quantity: int
    limit_price: float | None = None
    type: EventType = field(init=False, default=EventType.ORDER, repr=False)


@dataclass(slots=True)
class FillEvent(BaseEvent):
    symbol: str
    action: str                             # 'BUY' / 'SELL'
    quantity: int
    fill_price: float
    commission: float = 0.0
    slippage: float = 0.0
    type: EventType = field(init=False, default=EventType.FILL, repr=False)


# ------------------------------------------------------------------
# 4. 可转债特有事件
# ------------------------------------------------------------------
@dataclass(slots=True)
class ConvertEvent(BaseEvent):
    """
    转股事件：债券面值按转股价转换为股票
    quantity  : 转股张数
    stk_price : 转股当日正股收盘价（用于计算价值）
    """
    symbol: str
    quantity: int
    stk_price: float
    type: EventType = field(init=False, default=EventType.CONVERT, repr=False)


@dataclass(slots=True)
class ForceRedeemEvent(BaseEvent):
    """
    强赎：发行人按约定赎回价 + 应计利息回购债券
    """
    symbol: str
    quantity: int
    redeem_price: float                     # 含利息的赎回价
    type: EventType = field(init=False, default=EventType.FORCE_REDEEM, repr=False)


@dataclass(slots=True)
class PutEvent(BaseEvent):
    """
    回售：投资者按面值或约定价卖回给发行人
    """
    symbol: str
    quantity: int
    put_price: float                        # 回售价
    type: EventType = field(init=False, default=EventType.PUT, repr=False)


@dataclass(slots=True)
class CashEvent(BaseEvent):
    """
    现金流事件：利息、到期兑付等
    """
    symbol: str
    cash_amount: float                      # 现金流入金额
    event_type: str                         # 'coupon' / 'maturity' / 'other'
    quantity: int = 0                       # 相关债券数量（可选）
    type: EventType = field(init=False, default=EventType.CASH, repr=False)


@dataclass(slots=True)
class ClockEvent(BaseEvent):
    """
    时钟事件：用于分钟级回测中的定时触发
    可用于策略的定时检查、风险控制等
    """
    interval_seconds: int = 15              # 时钟间隔（秒）
    type: EventType = field(init=False, default=EventType.CLOCK, repr=False) 