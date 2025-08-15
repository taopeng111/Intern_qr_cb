"""
broker.py · SimpleBroker v0.2
--------------------------------------------------------------------
功能
1. 市价秒成交：SignalEvent → FillEvent / ConvertEvent
2. 费用
   ★ 佣金率、上限、过户费依交易所区别 (EXCHANGE_RULES)
   ★ 股票卖出时加印花税 (STAMP_DUTY_STOCK_SELL)
3. 滑点：买 +bps、卖 −bps
4. 转股：SignalEvent.action == "CONVERT" ➜ ConvertEvent
5. 预留：强赎 / 回售自动触发钩子 (TODO)
6. Step3: 分板块涨跌幅 + Tick Size(¥0.01) 校验
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List

from framework.events import (
    SignalEvent,
    FillEvent,
    ConvertEvent,
    CashEvent,
    ForceRedeemEvent,
    PutEvent,
)
from constants import (
    Config,
    EXCHANGE_RULES,
    STAMP_DUTY_STOCK_SELL,
    MAX_PCT_VOL,
    IMPACT_COEFF,
    PRICE_LIMIT_PCT,
    TICK_SIZE,
    round_to_tick,
)

# -----------------------------------------------------------
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


def detect_board(symbol: str) -> str:
    """
    快速识别板块
    """
    _board_re = {
        "CB": re.compile(r"^1[123]\d{4}(\.SZ|\.SH)?$"),  # 扩展到118/119/123/127等全部券，后缀可选
        "STAR": re.compile(r"^688\d{3}\.SH$"),
        "CHINEXT": re.compile(r"^(30\d{4}|3\d{5})\.SZ$"),
        "BE": re.compile(r"^8\d{5}\.BJ$"),
    }
    
    for board, pat in _board_re.items():
        if pat.match(symbol):
            return board
    return "MAIN"


class SimpleBroker:
    """
    Parameters
    ----------
    slippage_bps    : 买入 +bps / 卖出 −bps
    """

    def __init__(
        self,
        slippage_bps: int = Config.DEFAULT_SLIPPAGE_BPS,
        max_pct_vol: float = MAX_PCT_VOL,
        impact_coeff: float = IMPACT_COEFF,
        log_level: int = logging.INFO,
    ):
        self.slippage_bps = slippage_bps
        self.max_pct_vol = max_pct_vol
        self.impact_coeff = impact_coeff

        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())

        # 最新行情 (由 Engine 在 MarketEvent 到来后注入)
        self._latest_bars: Dict[str, Dict[str, float]] = {}
        self._session: str = "continuous"

    # -------- 引擎接口 --------
    def update_market(
        self,
        bars: Dict[str, Dict[str, float]],
        session: str = "continuous",
    ) -> None:
        self._latest_bars = bars
        self._session = session              # 记录当前时段

    def execute_signals(self, signals: List[SignalEvent]) -> List[FillEvent | ConvertEvent]:
        events: list = []

        for sig in signals:
            symbol = sig.symbol
            action = sig.action.upper()
            qty    = sig.size or EXCHANGE_RULES[_detect_exchange(symbol)]["LOT"]

            # -- 校验行情 -----------------------
            if symbol not in self._latest_bars:
                self.logger.warning("No market data for %s — signal skipped.", symbol)
                continue
            mkt_price = self._latest_bars[symbol]["close"]

            exch = _detect_exchange(symbol)
            rule = EXCHANGE_RULES[exch]

            # -- 处理转股 -----------------------
            if action == "CONVERT":
                # 生成 ConvertEvent ➜ Portfolio 负责把债券换成股票
                events.append(
                    ConvertEvent(
                        dt=sig.dt,
                        symbol=symbol,
                        quantity=qty,
                        stk_price=self._latest_bars[symbol].get("stk_close", 0.0),
                        meta={"orig_signal_id": sig.id},
                    )
                )
                continue

            # ------- Call Auction 处理 ----------
            if self._session.startswith("auction"):
                # 竞价价 = bars['close']，不加/减滑点
                fill_price = mkt_price
                side = "BUY" if action in ("BUY", "LONG") else "SELL"
                # 竞价价也需要四舍五入到tick size
                fill_price = round_to_tick(fill_price)
                #   ⬇ 复用后面的费用/风控逻辑
            else:
                # --- 连续竞价旧逻辑 ---
                # -- 处理买 / 卖 债券 ----------------
                if action in ("LONG", "BUY"):
                    side = "BUY"
                    fill_price = mkt_price * (1 + self.slippage_bps / 10_000)
                elif action in ("EXIT", "SHORT", "SELL"):
                    side = "SELL"
                    fill_price = mkt_price * (1 - self.slippage_bps / 10_000)
                else:
                    self.logger.warning("Unknown action %s — skipped.", action)
                    continue

                # -- 价格四舍五入到 Tick Size -------------------
                fill_price = round_to_tick(fill_price)

            # -- 流动性约束检查 -------------------
            # 1. 涨跌停检查
            if symbol in self._latest_bars and "pre_close" in self._latest_bars[symbol]:
                last_close = self._latest_bars[symbol]["pre_close"]
                if not self._check_price_limit(symbol, fill_price, last_close):
                    continue  # 超出涨跌停限制，跳过此订单
                
            # 2. 成交量约束和冲击成本（集合竞价阶段跳过）
            if self._session.startswith("auction"):
                # 竞价阶段：批量撮合，无需成交量约束和冲击成本
                adjusted_price = fill_price
                actual_qty = qty
            else:
                # 连续竞价阶段：应用成交量约束和冲击成本
                adjusted_price, actual_qty = self._apply_volume_constraint(symbol, qty, fill_price)
                if actual_qty == 0:
                    continue  # 成交量约束导致无法成交，跳过此订单

            trade_value = adjusted_price * actual_qty * rule["LOT"]

            # 佣金 = min( max(成交金额×费率, MIN_COMMISSION), 成交金额×上限 )
            commission = max(
                trade_value * rule["COMMISSION_RATE"],
                Config.MIN_COMMISSION
            )
            commission = min(commission, trade_value * rule["COMMISSION_MAX"])

            # 过户费（上交所 / 深交所均有，但费率不同）
            handling_fee = trade_value * rule["HANDLING_FEE"]

            # 印花税：仅"SELL 且是股票"才收
            is_stock = not symbol.startswith(("11", "12"))
            stamp_duty = (
                trade_value * STAMP_DUTY_STOCK_SELL
                if side == "SELL" and is_stock
                else 0.0
            )

            total_fee = commission + handling_fee + stamp_duty

            events.append(
                FillEvent(
                    dt=sig.dt,
                    symbol=symbol,
                    action=side,
                    quantity=actual_qty,
                    fill_price=round(adjusted_price, 4),
                    commission=round(total_fee, 4),
                    slippage=round(abs(adjusted_price - mkt_price), 4),
                    meta={"orig_signal_id": sig.id},
                )
            )
            # 成交日志（INFO）：便于用户观察进度
            self.logger.info(
                "%s %s x%d @ %.2f (dt=%s)",
                side, symbol, int(actual_qty), float(adjusted_price), str(sig.dt.date())
            )

        return events

    def _check_price_limit(self, symbol: str, order_px: float, last_close: float) -> bool:
        """
        返回 True 表示订单价格合法
        """
        board = detect_board(symbol)
        limit_pct = PRICE_LIMIT_PCT.get(board, 0.10)

        # 缺失/无效昨收时跳过涨跌幅校验（避免 [0,0] 区间导致全部拦截）
        try:
            lc = float(last_close)
        except Exception:
            lc = 0.0
        if not (lc > 0):
            # 仅做 tick 检查
            tick_ok = abs(order_px - round(order_px / TICK_SIZE) * TICK_SIZE) < 1e-9
            if not tick_ok:
                self.logger.warning("%s: price %.3f not multiple of %.2f", symbol, order_px, TICK_SIZE)
            return tick_ok

        # ① Tick size
        tick_ok = abs(order_px - round(order_px / TICK_SIZE) * TICK_SIZE) < 1e-9

        # ② 涨跌幅
        upper = lc * (1 + limit_pct)
        lower = lc * (1 - limit_pct)
        limit_ok = lower <= order_px <= upper

        if not tick_ok:
            self.logger.warning("%s: price %.3f not multiple of %.2f", symbol, order_px, TICK_SIZE)
        if not limit_ok:
            self.logger.warning("%s: price %.2f 超过涨跌幅限制 [%.2f, %.2f]",
                                symbol, order_px, lower, upper)
        return tick_ok and limit_ok

    def _apply_volume_constraint(self, symbol: str, qty: int, fill_price: float) -> tuple[float, int]:
        """
        应用成交量约束和冲击成本
        返回 (adjusted_price, actual_qty)
        """
        if symbol not in self._latest_bars:
            return fill_price, qty
            
        bar = self._latest_bars[symbol]
        if "volume" not in bar or bar["volume"] <= 0:
            return fill_price, qty
            
        volume = bar["volume"]
        lot = EXCHANGE_RULES[_detect_exchange(symbol)]["LOT"]
        
        # 计算最大可成交数量
        max_qty = int(volume * self.max_pct_vol / lot)
        
        if qty <= max_qty:
            # 数量在限制内，无需调整
            return fill_price, qty
        else:
            # 数量超出限制，按比例成交并计算冲击成本
            actual_qty = max_qty
            if actual_qty == 0:
                self.logger.warning(f"Order for {symbol} rejected: volume constraint results in 0 quantity")
                return fill_price, 0
                
            # 计算冲击成本
            impact_multiplier = 1 + self.impact_coeff * (qty / actual_qty) ** 0.5
            adjusted_price = fill_price * impact_multiplier
            
            self.logger.info(f"Volume constraint applied to {symbol}: {qty} -> {actual_qty}, price {fill_price:.2f} -> {adjusted_price:.2f}")
            return adjusted_price, actual_qty 