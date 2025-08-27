# -*- coding: utf-8 -*-
"""
真实数据回测：周频Top-N动量（可转债）
- 数据：可转债日线 parquet（必须），正股 parquet（可选，不需要本策略）
- 逻辑：每周五选 10 只"本周涨幅最高"的转债，等权持有至下周五；停牌/无昨收剔除
- 成交：当日收盘信号，次日按市价（含滑点/费用/涨跌停/成交量约束）成交
"""

import os
from pathlib import Path
import re
import pandas as pd

# 如果你的框架在 package 目录 "framework/" 下，用下面这一组 import
from framework.engine import BacktestEngine
from framework.data_handler import DailyBarDataHandler
from framework.broker import SimpleBroker
from framework.portfolio import BasicPortfolio
from framework.reporting import TearSheetReporter
from framework.events import SignalEvent, MarketEvent

# -------- 策略：周频Top-N动量（仅用转债自身行情） --------
class WeeklyTopNMomentumStrategy:
    def __init__(self, top_n=10, rebalance_weekday=4, min_volume=1):
        self.top_n = top_n
        self.rebalance_weekday = rebalance_weekday  # 0~4=Mon~Fri
        self.min_volume = min_volume
        self.hold = set()

    @staticmethod
    def _is_cb(code: str) -> bool:
        # 只选可转债：以 11/12 开头（可带 .SH/.SZ 后缀）
        return re.match(r'^(11|12)\d{4}(?:\.(?:SZ|SH))?$', str(code)) is not None

    def calculate_signals(self, market_event: MarketEvent):
        dt = market_event.dt
        bars = market_event.data

        # 只在周五调仓
        if dt.weekday() != self.rebalance_weekday:
            return []

        # 计算"本日涨幅"（近似替代本周动量，演示用；你可改成过去N日累计）
        universe = []
        for code, b in bars.items():
            if not self._is_cb(code):
                continue
            close = b.get("close", None)
            pre_close = b.get("pre_close", None)
            vol = b.get("volume", 0)
            if close is None or pre_close is None or not (pre_close > 0) or vol < self.min_volume:
                continue
            ret = close / pre_close - 1.0
            universe.append((code, ret))

        if not universe:
            return []

        # 取 Top-N
        universe.sort(key=lambda x: x[1], reverse=True)
        pick = [c for c, _ in universe[: self.top_n]]

        signals = []

        # 先退出不在名单里的
        for code in list(self.hold):
            if code not in pick:
                signals.append(SignalEvent(dt=dt, symbol=code, action="EXIT"))
                self.hold.remove(code)

        # 再买入新入选的（每票默认 1 手=10张；如需固定资金权重，请在 broker/portfolio 层改造）
        for code in pick:
            if code not in self.hold:
                signals.append(SignalEvent(dt=dt, symbol=code, action="LONG", size=1))
                self.hold.add(code)

        return signals

# -------- 数据路径（使用你现有的数据文件） --------
CB_PARQUET_CANDIDATES = [
    "data/cb_all.parquet",           # 你现有的可转债数据
    "data/cb_strategy_ready.parquet", # 备选
    "data/cb_sample.parquet",        # 备选
]
cb_path = next((p for p in CB_PARQUET_CANDIDATES if Path(p).exists()), None)
if cb_path is None:
    raise FileNotFoundError("未找到可转债日线 parquet。请把文件放到 data/cb_all.parquet 或修改脚本顶部 CB_PARQUET_CANDIDATES。")

# 可选：正股数据（本策略不需要，但可以用于过滤）
STK_PARQUET = "data/stock_full.parquet" if Path("data/stock_full.parquet").exists() else None
# 可选：可转债发行条款信息
CB_INFO_PARQUET = "data/cb_info_full.parquet" if Path("data/cb_info_full.parquet").exists() else None

# -------- 拼装回测并运行 --------
if __name__ == "__main__":
    handler = DailyBarDataHandler(
        cb_parquet_path=cb_path,
        stk_parquet_path=STK_PARQUET,
        cb_info_path=CB_INFO_PARQUET,
        start_date="2023-01-01",
        end_date="2023-12-31",
        skip_suspended=True,
        calendar_csv=None,   # 如本机无 tushare 或想固定日历，可填本地 CSV（单列日期）
        log_level=20,
    )

    strategy = WeeklyTopNMomentumStrategy(top_n=10, rebalance_weekday=4)
    broker   = SimpleBroker(slippage_bps=2, log_level=20)
    pf       = BasicPortfolio(initial_capital=1_000_000, log_level=20)
    rpt      = TearSheetReporter(out_dir="output/cb_weekly_mom")

    engine   = BacktestEngine(handler, strategy, broker, pf, rpt, log_level=20)
    engine.run()

    print(f"记录的净值点数: {len(pf.nav_history)}")
    if pf.nav_history:
        print(f"区间最终资产: {pf.current_nav():.2f}")
        print(f"报告输出目录: {rpt.out_dir}")
