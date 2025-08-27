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
        t0 = time.time()
        
        # （若有）重置数据处理器内部游标
        if hasattr(self.data_handler, "reset"):
            self.data_handler.reset()
        
        try:
            while True:
                # 1) 先推进一根bar
                if hasattr(self.data_handler, "continue_backtest") and not self.data_handler.continue_backtest:
                    break
                self.data_handler.update_bars()  # ★ 关键：先推进

                # 2) 再取"当前"bar数据（日期 + 当日字典/表）
                try:
                    bar_data = self.data_handler.get_latest_bars()
                    dt, bars = bar_data["timestamp"], bar_data["bars"]
                except (StopIteration, RuntimeError) as e:
                    # 如果没有更多数据或出现错误，退出循环
                    if "Call update_bars() before get_latest_bars()" in str(e):
                        # 如果是因为顺序问题，继续下一次迭代
                        continue
                    break

                # 3) 播行情给券商 + 转发现金/强赎/回售事件到组合
                session = getattr(bar_data, "get", lambda k, d=None: d)("session", "continuous") if isinstance(bar_data, dict) else "continuous"
                if hasattr(self.broker, "update_market"):
                    self.broker.update_market(bars, session=session)  # ★ 把当日行情播给券商（撮合要用） 

                # 转发现金/强赎/回售事件到组合（若 data_handler 当天产出了这些）
                for evt in (bar_data.get("cash_events") or []):
                    self.portfolio.update_from_cash([evt])
                for evt in (bar_data.get("force_redeem_put_events") or []):
                    if evt.type.name == "FORCE_REDEEM":
                        self.portfolio.update_from_force_redeem([evt])
                    elif evt.type.name == "PUT":
                        self.portfolio.update_from_put([evt])

                # 4) 把当日bar字典塞进MarketEvent给策略
                me = MarketEvent(dt=dt, data=bars)  # ★ 直接传data参数

                # 4) 让策略产出信号
                signals = self.strategy.calculate_signals(me)

                # 5) 生成委托并在"次日开盘"执行
                next_info = getattr(self.data_handler, "peek_next_bars", None)
                if callable(next_info):
                    try:
                        next_dt, next_bars = next_info()
                    except:
                        next_dt, next_bars = dt, bars  # 没有peek就用当日
                else:
                    next_dt, next_bars = dt, bars  # 没有peek就用当日

                # 执行信号
                for signal in signals:
                    self.events.append(signal)

                # 6) 处理队列事件
                while self.events:
                    event = self.events.popleft()
                    self._handle_event(event)

                # 7) 组合记账 & 记录净值
                self.portfolio.mark_to_market(dt, bars)
                if hasattr(self.reporter, "collect_nav"):
                    self.reporter.collect_nav(dt, self.portfolio.current_nav())
                
                # 进度日志：每天打印一次日期和可交易标的数
                self.logger.info(
                    "Market %s — instruments: %d",
                    str(dt.date()),
                    len(bars),
                )

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
        # 添加调试信息
        print(f"DEBUG: 处理市场事件 {market_event.dt.date()}, 数据条数: {len(market_event.data) if hasattr(market_event, 'data') else 'N/A'}")
        
        signals = self.strategy.calculate_signals(market_event)
        print(f"DEBUG: 策略生成信号数量: {len(signals)}")
        
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