"""
engine.py · 回测主引擎（v0.1+补充）
-------------------------------------------------
职责：
    1. 管理事件队列
    2. 调用 data_handler 推行情
    3. 调用 strategy 生成 SignalEvent
    4. 调用 broker 撮合并产生 FillEvent
    5. 调用 portfolio 更新净值
    6. 将 NAV 等送入 reporter 汇总

后期扩展：
    • 支持分钟级、盘后结算、并行多策略
    • 针对 “同步 / 异步” 模式可做参数化
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Deque, Protocol, List, Dict, Any
import time
import pandas as pd

from .events import (
    BaseEvent,
    EventType,
    MarketEvent,
    SignalEvent,
    OrderEvent,
    FillEvent,
    CashEvent,
    ForceRedeemEvent,
    PutEvent,
    ClockEvent,
)

# ---------- 依赖接口（简单用 Protocol 约束） ----------
class DataHandlerLike(Protocol):
    continue_backtest: bool

    def update_bars(self) -> None | List[Dict[str, Any]]: ...
    def get_latest_bars(self) -> dict[str, dict[str, float]]: ...

class StrategyLike(Protocol):
    def calculate_signals(self, market_event: MarketEvent) -> list[SignalEvent]: ...
    

class BrokerLike(Protocol):
    def execute_signals(self, signals: list[SignalEvent]) -> list[FillEvent]: ...

class PortfolioLike(Protocol):
    nav_history: list[tuple[pd.Timestamp, float]]

    def update_from_fill(self, fills: list[FillEvent]) -> None: ...
    def update_from_force_redeem(self, evts: list[ForceRedeemEvent]) -> None: ...
    def update_from_put(self, evts: list[PutEvent]) -> None: ...
    def update_from_cash(self, evts: list[CashEvent]) -> None: ...

class ReporterLike(Protocol):
    def collect_nav(self, dt: pd.Timestamp, nav: float) -> None: ...
    def output(self) -> None: ...

# ---------- Engine ----------
class BacktestEngine:
    """最小可运行的事件驱动引擎"""

    def __init__(
        self,
        data_handler: DataHandlerLike,
        strategy: StrategyLike,
        broker: BrokerLike,
        portfolio: PortfolioLike,
        reporter: ReporterLike | None = None,
        log_level: int = logging.INFO,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            h = logging.StreamHandler()
            fmt = "%(asctime)s | %(levelname)s | %(name)s - %(message)s"
            h.setFormatter(logging.Formatter(fmt))
            self.logger.addHandler(h)

        self.events: Deque[BaseEvent] = deque()

        self.data_handler = data_handler
        self.strategy = strategy
        self.broker = broker
        self.portfolio = portfolio
        self.reporter = reporter

    # ----- 主循环 -----
    def run(self) -> None:
        self.logger.info("Backtest started.")
        i = 0
        t0 = time.time()
        try:
            while self.data_handler.continue_backtest or self.events:
                # 1) 推送最新 MarketEvent
                if self.data_handler.continue_backtest:
                    events_data = self.data_handler.update_bars()
                    
                    # 支持分钟级数据处理器返回多个事件
                    if isinstance(events_data, list):
                        # 分钟级：处理多个事件（MarketEvent + ClockEvent）
                        for event_data in events_data:
                            if event_data["type"] == "market":
                                # 把最新行情缓存给 broker
                                self.broker.update_market(
                                    bars=event_data["bars"],
                                    session=event_data["session"],      # ★ 新增
                                )
                                
                                market_event = MarketEvent(
                                    dt=event_data["timestamp"],
                                    data=event_data["bars"],
                                )
                                self.events.append(market_event)
                                
                                # 处理现金流事件
                                cash_events = event_data.get("cash_events", [])
                                force_redeem_put_events = event_data.get("force_redeem_put_events", [])
                                
                                for cash_event in cash_events:
                                    self.events.append(cash_event)
                                for force_redeem_put_event in force_redeem_put_events:
                                    self.events.append(force_redeem_put_event)
                                    
                            elif event_data["type"] == "clock":
                                # 时钟事件
                                clock_event = ClockEvent(
                                    dt=event_data["timestamp"],
                                    interval_seconds=15
                                )
                                self.events.append(clock_event)
                    else:
                        # 日线级：原有逻辑
                        bar_data = self.data_handler.get_latest_bars()

                        # ★ 把最新行情缓存给 broker（新增）
                        self.broker.update_market(bar_data["bars"])

                        market_event = MarketEvent(
                            dt=bar_data["timestamp"],
                            data=bar_data["bars"],
                        )
                        self.events.append(market_event)
                        # 进度日志：每天打印一次日期和可交易标的数
                        self.logger.info(
                            "Market %s — instruments: %d",
                            str(bar_data["timestamp"].date()),
                            len(bar_data["bars"]),
                        )

                # 2) 处理队列事件
                while self.events:
                    event = self.events.popleft()
                    self._handle_event(event)

        finally:
            # 3) 输出报告
            if self.reporter:
                self.reporter.output()
            t1 = time.time()
            self.logger.info(f"Backtest finished. Total time: {t1-t0:.2f}s")

    # ----- 事件路由 -----
    def _handle_event(self, event: BaseEvent) -> None:
        if event.type is EventType.MARKET:
            self._process_market(event)          # 生成 Signal

        elif event.type is EventType.SIGNAL:
            orders = self.broker.execute_signals([event])  # M→FillEvent
            for fill in orders:
                self.events.append(fill)         # 放回队列

        elif event.type is EventType.FILL:
            self.portfolio.update_from_fill([event])
            # 同步更新策略持仓状态
            if hasattr(self.strategy, 'update_position'):
                self.strategy.update_position(
                    event.symbol, 
                    event.quantity, 
                    event.action,
                    event.fill_price,
                    event.dt
                )
            
        elif event.type is EventType.FORCE_REDEEM:
            self.portfolio.update_from_force_redeem([event])
            
        elif event.type is EventType.PUT:
            self.portfolio.update_from_put([event])
            
        elif event.type is EventType.CASH:
            self.portfolio.update_from_cash([event])
            
        elif event.type is EventType.CLOCK:
            self._process_clock(event)           # 处理时钟事件

        # 预留：支持 SettlementEvent 等
        else:
            self.logger.warning("Unhandled event type: %s", event.type)

    # ----- 策略调用 -----
    def _process_market(self, market_event: MarketEvent) -> None:
        signals = self.strategy.calculate_signals(market_event)
        for sig in signals:
            self.events.append(sig)

        # 已经有的估值（保留）
        self.portfolio.mark_to_market(market_event.dt, market_event.data)

        # ★ 新增：每天写一条净值记录（仅日线模式）
        if self.reporter and hasattr(self.data_handler, 'date_col'):
            # 检查是否为日线模式（通过是否有date_col属性判断）
            self.reporter.collect_nav(
                market_event.dt,
                self.portfolio.current_nav()
            )

    # ----- 时钟事件处理 -----
    def _process_clock(self, clock_event: 'ClockEvent') -> None:
        """
        处理时钟事件：可用于分钟级策略的定时检查
        """
        # 这里可以添加分钟级策略的定时逻辑
        # 例如：风险控制、仓位检查等
        self.logger.debug(f"Clock event at {clock_event.dt}")
        
        # 可选：在时钟事件时也更新净值（分钟级实时估值）
        if hasattr(self.data_handler, 'clock_interval'):
            # 分钟级模式：实时更新净值
            if self.reporter:
                self.reporter.collect_nav(
                    clock_event.dt,
                    self.portfolio.current_nav()
                )

        

    # ----- 未来扩展点 -----
    # 支持多策略、多数据源、异步事件处理等
    # def add_strategy(self, strategy: StrategyLike): ...
    # def add_data_handler(self, data_handler: DataHandlerLike): ...
    # def run_async(self): ... 