"""
portfolio.py · BasicPortfolio v0.1
-----------------------------------------------------------------
✓ 记录现金、债券持仓、股票持仓
✓ 处理 FillEvent           —— 买/卖债券
✓ 处理 ConvertEvent        —— 债→股转股（0 现金流，持仓变化）
✓ 每日收盘计算 NAV         —— 市值 + 现金
★ TODO：处理 ForceRedeemEvent / PutEvent / 利息 / 到期兑付
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import pandas as pd

from framework.events import (
    FillEvent,
    ConvertEvent,
    ForceRedeemEvent,
    PutEvent,
    CashEvent,
)

from constants import EXCHANGE_RULES, TICK_SIZE, round_to_tick


@dataclass
class Position:
    symbol: str = ""       # 债券代码或股票代码
    qty: int = 0           # 张（债券）或 股（股票）
    cost: float = 0.0      # 均价（含费用）
    mkt_price: float = 0.0 # 最新市价
    lots: list[tuple[int, pd.Timestamp]] = field(default_factory=list)  # (qty, 买入日期)

    def market_value(self) -> float:
        # 修复：qty是"张"，mkt_price是"单份价格"，需要乘以LOT得到正确市值
        if self.symbol.startswith(("11", "12", "13")):  # 可转债（扩展覆盖）
            lot = EXCHANGE_RULES[_detect_exchange(self.symbol)]['LOT']  # 10
            return self.qty * lot * self.mkt_price      # 乘 LOT 修正
        else:  # 股票
            return self.qty * self.mkt_price


class BasicPortfolio:
    """
    Parameters
    ----------
    initial_capital : float  初始资金
    """

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        log_level: int = logging.INFO,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())

        self.cash: float = initial_capital

        # asset_id -> Position   (债券代码、股票代码分别存)
        self.bond_positions: Dict[str, Position] = {}
        self.stock_positions: Dict[str, Position] = {}

        # (timestamp, nav)
        self.nav_history: List[Tuple[pd.Timestamp, float]] = []

    # ------------------------------------------------------------------
    # 1. 接收事件
    # ------------------------------------------------------------------
    def update_from_fill(self, fills: List[FillEvent]) -> None:
        for fill in fills:
            code = fill.symbol
            qty  = fill.quantity
            px   = fill.fill_price
            
            # 判断是债券还是股票
            is_bond = code.startswith(("11", "12"))
            
            if is_bond:
                cost_with_fee = px * qty * EXCHANGE_RULES[_detect_exchange(code)]["LOT"]
                positions = self.bond_positions
            else:
                cost_with_fee = px * qty  # 股票不需要乘以LOT
                positions = self.stock_positions

            if fill.action == "BUY":
                pos = positions.setdefault(code, Position(symbol=code))
                if is_bond:
                    # 债券：成本均价需要按份数计算，除以LOT
                    rule = EXCHANGE_RULES[_detect_exchange(code)]
                    pos.cost = ((pos.qty * pos.cost) + cost_with_fee) / (pos.qty + qty) / rule['LOT']
                else:
                    # 股票：直接计算成本均价
                    pos.cost = ((pos.qty * pos.cost) + cost_with_fee) / (pos.qty + qty)
                pos.qty += qty
                pos.lots.append((qty, fill.dt.normalize()))
                self.cash -= cost_with_fee + fill.commission

            elif fill.action == "SELL":
                pos = positions.get(code)
                if pos is None:
                    self.logger.warning("No position to sell %s", code)
                    continue

                today = fill.dt.normalize()
                
                if is_bond:   # 可转债 T+0
                    sellable = pos.qty
                    if qty > sellable:
                        self.logger.warning("Not enough position: only %d/%d available for %s",
                                            sellable, qty, code)
                        qty = sellable
                    cost_with_fee = px * qty * EXCHANGE_RULES[_detect_exchange(code)]["LOT"]
                    
                    # 可转债直接减持仓，不需要批次管理
                    pos.qty -= qty
                    
                else:         # 股票 T+1
                    # 1) 统计可卖（前日及更早买的批次）
                    sellable = sum(q for q, d in pos.lots if d < today)
                    if sellable == 0:
                        self.logger.warning("T+1 restriction: 0 shares available to sell for %s", code)
                        continue
                    if qty > sellable:
                        self.logger.warning("T+1: only %d/%d shares sellable for %s — partial fill",
                                            sellable, qty, code)
                        qty = sellable
                    cost_with_fee = px * qty

                    # 2) 扣批次 (FIFO)
                    remain = qty
                    new_lots = []
                    for lot_qty, lot_day in pos.lots:
                        if lot_day < today and remain:
                            used = min(lot_qty, remain)
                            lot_qty -= used
                            remain -= used
                        if lot_qty:  # 剩余批次保留
                            new_lots.append((lot_qty, lot_day))
                    pos.lots = new_lots
                    
                    # 3) 核心数值更新
                    pos.qty -= qty

                self.cash += cost_with_fee - fill.commission

                if pos.qty == 0:
                    del positions[code]

            pos.mkt_price = px  # 更新最新价

            self.logger.debug("%s %s %d @ %.2f", fill.action, code, qty, px)

    def update_from_convert(self, evts: List[ConvertEvent]) -> None:
        """
        债券转股：按转股价换股，假设无现金流
        """
        for evt in evts:
            cb_code = evt.symbol
            qty = evt.quantity
            stk_price = evt.stk_price
            stk_code = _cb_to_stock(cb_code)            # 简易映射

            # 1) 减债
            bond_pos = self.bond_positions.get(cb_code)
            if bond_pos is None or bond_pos.qty < qty:
                self.logger.warning("No enough CB to convert %s", cb_code)
                continue
            bond_pos.qty -= qty
            if bond_pos.qty == 0:
                del self.bond_positions[cb_code]

            # 2) 加股
            share_qty = _bond_to_share_qty(cb_code, qty)  # 转股张数→股数
            stk_pos = self.stock_positions.setdefault(stk_code, Position(symbol=stk_code))
            stk_pos.cost = stk_price    # 简化处理
            stk_pos.qty += share_qty
            stk_pos.lots.append((share_qty, evt.dt.normalize()))  # 记录转股批次
            stk_pos.mkt_price = stk_price

            self.logger.debug("CONVERT %s %d张 → %s %d股", cb_code, qty, stk_code, share_qty)

    def update_from_force_redeem(self, evts: List[ForceRedeemEvent]) -> None:
        """
        强赎：发行人按约定赎回价回购债券
        """
        for evt in evts:
            cb_code = evt.symbol
            qty = evt.quantity
            redeem_price = evt.redeem_price
            
            # 获取持仓
            bond_pos = self.bond_positions.get(cb_code)
            if bond_pos is None or bond_pos.qty < qty:
                self.logger.warning("No enough CB to force redeem %s", cb_code)
                continue
                
            # 计算现金流入
            rule = EXCHANGE_RULES[_detect_exchange(cb_code)]
            cash_in = qty * redeem_price * rule['LOT']
            self.cash += cash_in
            
            # 减少持仓
            bond_pos.qty -= qty
            if bond_pos.qty == 0:
                del self.bond_positions[cb_code]
                
            self.logger.info("FORCE_REDEEM %s %d张 @ %.2f, cash_in=%.2f", 
                           cb_code, qty, redeem_price, cash_in)

    def update_from_put(self, evts: List[PutEvent]) -> None:
        """
        回售：投资者按约定价卖回给发行人
        """
        for evt in evts:
            cb_code = evt.symbol
            qty = evt.quantity
            put_price = evt.put_price
            
            # 获取持仓
            bond_pos = self.bond_positions.get(cb_code)
            if bond_pos is None or bond_pos.qty < qty:
                self.logger.warning("No enough CB to put %s", cb_code)
                continue
                
            # 计算现金流入
            rule = EXCHANGE_RULES[_detect_exchange(cb_code)]
            cash_in = qty * put_price * rule['LOT']
            self.cash += cash_in
            
            # 减少持仓
            bond_pos.qty -= qty
            if bond_pos.qty == 0:
                del self.bond_positions[cb_code]
                
            self.logger.info("PUT %s %d张 @ %.2f, cash_in=%.2f", 
                           cb_code, qty, put_price, cash_in)

    def update_from_cash(self, evts: List[CashEvent]) -> None:
        """
        现金流：利息、到期兑付等
        """
        for evt in evts:
            self.cash += evt.cash_amount
            self.logger.info("CASH %s %s %.2f", 
                           evt.symbol, evt.event_type, evt.cash_amount)

    # ------------------------------------------------------------------
    # 2. 收盘时计算 NAV
    # ------------------------------------------------------------------
    def mark_to_market(
        self,
        dt: pd.Timestamp,
        latest_prices: Dict[str, Dict[str, float]],
    ) -> None:
        # 更新债券
        for code, pos in self.bond_positions.items():
            if code in latest_prices:
                pos.mkt_price = round_to_tick(latest_prices[code]["close"])
            # 调试：打印批次信息
            if pos.lots:
                self.logger.debug(f"Bond {code} lots: {pos.lots}")

        # 更新股票
        for code, pos in self.stock_positions.items():
            if code in latest_prices:
                pos.mkt_price = round_to_tick(latest_prices[code]["close"])
            # 调试：打印批次信息
            if pos.lots:
                self.logger.debug(f"Stock {code} lots: {pos.lots}")

        net_asset = (
            self.cash
            + sum(p.market_value() for p in self.bond_positions.values())
            + sum(p.market_value() for p in self.stock_positions.values())
        )
        self.nav_history.append((dt, net_asset))

    # ------------------------------------------------------------------
    # helper
    # ------------------------------------------------------------------
    def current_nav(self) -> float:
        return self.nav_history[-1][1] if self.nav_history else self.cash


# ==============================================================
# util helpers
# ==============================================================

def _detect_exchange(symbol: str) -> str:
    """
    更稳健的交易所识别：优先看后缀，再用首位数字兜底
    - .SH 后缀 → SSE (上交所)  
    - .SZ 后缀 → SZSE (深交所)
    - 无后缀时按首位数字：5/6/9/11开头 → SSE，其他 → SZSE
    """
    if symbol.endswith('.SH'):
        return 'SSE'
    if symbol.endswith('.SZ'):
        return 'SZSE'
    return 'SSE' if symbol[0] in ('5', '6', '9') or symbol.startswith('11') else 'SZSE'


def _cb_to_stock(cb_code: str) -> str:
    """示例：110057.SZ → 600000.SH；真实应查静态表"""
    return cb_code[1:]  # 仅示例


def _bond_to_share_qty(cb_code: str, bond_qty: int) -> int:
    """
    1 张面值 100 元，转股价假设固定 10 元
    ——> 100 / 10 = 10 股；如需精确，查 CB 静态表
    """
    return bond_qty * 10 