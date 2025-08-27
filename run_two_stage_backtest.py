"""
Two-Stage Enhanced Convertible Bond Strategy Backtest Runner

集成 two_stage 策略到回测框架中进行回测
支持从 parquet/CSV 文件读取数据，使用现有的回测框架组件
兼容"引擎先 get_latest_bars 再 update_bars"的调用顺序（兜底）
"""

from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
from typing import Dict, Any, Tuple

import pandas as pd
import numpy as np

# 导入回测框架组件
from framework.data_handler import DailyBarDataHandler
from framework.broker import SimpleBroker
from framework.portfolio import BasicPortfolio
from framework.reporting import TearSheetReporter
from framework.engine import BacktestEngine

# 导入 two_stage 策略
import sys
strategies_path = Path(__file__).parent / "strategies"
sys.path.insert(0, str(strategies_path))
from two_stage_enhanced_strategy import TwoStageEnhancedCBStrategy, StrategyConfig


# ========= Fallback runner: 如果引擎没产出NAV，用这个本地循环补跑 =========

class _FBPosition:
    def __init__(self, lots=0, cost=0.0):
        self.lots = lots
        self.cost = cost

class _FBPortfolio:
    def __init__(self, init_cash: float, lot_size: int, commission: float, slippage: float):
        self.cash = init_cash
        self.lot_size = lot_size
        self.commission = commission
        self.slippage = slippage
        self.pos = {}   # symbol -> _FBPosition
        self.equity_curve = []
        self.trades = []

    def mark_to_close(self, dt, prices: pd.Series):
        eq = self.cash
        for sym, p in self.pos.items():
            px = float(prices.get(sym, np.nan))
            if np.isfinite(px):
                eq += p.lots * self.lot_size * px
        self.equity_curve.append({"date": dt, "equity": eq, "cash": self.cash, "pos_count": len(self.pos)})

    def _exec(self, dt, symbol: str, action: str, lots: int, open_px: float):
        import numpy as _np
        if not _np.isfinite(open_px):
            return False
        px = open_px * (1 + self.slippage if action == "BUY" else 1 - self.slippage)
        notional = lots * self.lot_size * px
        fee = notional * self.commission
        if action == "BUY":
            if self.cash < (notional + fee):
                return False
            pos = self.pos.get(symbol, _FBPosition())
            new_lots = pos.lots + lots
            pos.cost = (pos.cost * pos.lots + px * lots) / new_lots if new_lots > 0 else 0.0
            pos.lots = new_lots
            self.pos[symbol] = pos
            self.cash -= (notional + fee)
        else:  # SELL
            pos = self.pos.get(symbol)
            if pos is None or pos.lots <= 0:
                return False
            sell_lots = min(lots, pos.lots)
            pos.lots -= sell_lots
            self.cash += sell_lots * self.lot_size * px - fee
            if pos.lots == 0:
                self.pos.pop(symbol, None)
        self.trades.append({"date": dt, "symbol": symbol, "side": action, "lots": lots, "price": px, "fee": fee})
        return True

def _fallback_backtest(strategy, out_dir: Path, init_cash: float):
    """
    用 two_stage_enhanced_strategy + 本地面板数据 直接跑一遍：
    - 周度调仓（cfg.rebalance_weekday）
    - 次日开盘成交（无peek用次日open，缺失用次日close）
    - 手数等权，自动受现金约束
    - 显式佣金/滑点
    生成 output/equity_curve.csv & trades.csv
    """
    import numpy as np
    import pandas as pd
    from two_stage_enhanced_strategy import MarketEvent

    df = strategy.cb_data.copy()
    if df is None or df.empty:
        raise RuntimeError("fallback: strategy.cb_data 为空")

    # 预处理（幂等）
    strategy.preprocess()

    cfg = strategy.cfg
    pf = _FBPortfolio(init_cash, lot_size=cfg.lot_size,
                      commission=cfg.commission_rate, slippage=cfg.slippage)

    dates = df["trade_date"].dt.normalize().drop_duplicates().sort_values().tolist()

    # 用于策略持有天计数
    if not hasattr(strategy, "hold_days"):
        strategy.hold_days = {}

    for i, dt in enumerate(dates):
        day = df[df["trade_date"].dt.normalize() == dt].copy()
        close_s = day.set_index("cb_code")["close"]
        pf.mark_to_close(dt, close_s)

        # 生成信号（在当日收盘），执行在 next open
        # 创建扁平映射：cb_code -> dict(bar)
        available_columns = ["open", "close", "premium", "ytm", "term", "turnover", "is_st", "balance"]
        bars_dict = {}
        for _, r in day.iterrows():
            cb = str(r["cb_code"])[-6:].zfill(6)
            rec = {
                "open":     float(r["open"])     if "open"     in day.columns else float(r["close"]),
                "close":    float(r["close"]),
                "premium":  float(r["premium"])  if "premium"  in day.columns else 0.0,
                "ytm":      float(r["ytm"])      if "ytm"      in day.columns else np.nan,
                "term":     float(r["term"])     if "term"     in day.columns else np.nan,
                "turnover": float(r["turnover"]) if "turnover" in day.columns else 0.0,
                "is_st":    int(r["is_st"])      if "is_st"    in day.columns else 0,
                "balance":  float(r["balance"])  if "balance"  in day.columns else 0.0,
            }
            bars_dict[cb] = rec

        me = MarketEvent(dt=dt, data=bars_dict)
        sigs = strategy.calculate_signals(me)

        # 下一个交易日
        if i == len(dates) - 1:
            # 最后一天没有 next-open，直接跳过执行
            continue
        next_dt = dates[i + 1]
        next_day = df[df["trade_date"].dt.normalize() == next_dt].copy()
        open_s = next_day.set_index("cb_code")["open"].fillna(next_day.set_index("cb_code")["close"])

        # 先卖后买
        sells = [s for s in sigs if s.action == "EXIT"]
        buys  = [s for s in sigs if s.action == "LONG"]

        for s in sells:
            cur_lots = pf.pos.get(s.symbol, _FBPosition()).lots
            if cur_lots <= 0:
                continue
            px = float(open_s.get(s.symbol, np.nan))
            if pf._exec(next_dt, s.symbol, "SELL", cur_lots, px):
                strategy.update_position(s.symbol, cur_lots, action="SELL", fill_price=px, fill_dt=next_dt)

        # 计算每个新进的手数（等权、受现金约束）
        new_syms = [s.symbol for s in buys]
        lots_each = 1
        if new_syms:
            cash_per = pf.cash / len(new_syms)
            est_px = np.nanmean([open_s.get(sym, np.nan) for sym in new_syms])
            if np.isfinite(est_px) and est_px > 0:
                est = int(max(1, min(10, cash_per / (cfg.lot_size * est_px))))
                lots_each = est

        for s in buys:
            px = float(open_s.get(s.symbol, np.nan))
            if pf._exec(next_dt, s.symbol, "BUY", lots_each, px):
                strategy.update_position(s.symbol, lots_each, action="BUY", fill_price=px, fill_dt=next_dt)

        # 持有天数 +1
        for sym in list(pf.pos.keys()):
            strategy.hold_days[sym] = strategy.hold_days.get(sym, 0) + 1

    # 输出
    out_dir.mkdir(parents=True, exist_ok=True)
    eq = pd.DataFrame(pf.equity_curve).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    eq["ret"] = eq["equity"].pct_change().fillna(0.0)
    eq.to_csv(out_dir / "equity_curve.csv", index=False)
    pd.DataFrame(pf.trades).to_csv(out_dir / "trades.csv", index=False)

    # 打印摘要
    if len(eq) > 1:
        total_ret = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1
        ann = (1 + eq["ret"]).prod() ** (244 / max(1, len(eq))) - 1
        vol = eq["ret"].std(ddof=0) * np.sqrt(244)
        sharpe = ann / vol if (vol and np.isfinite(vol) and vol != 0) else np.nan
        mdd = (eq["equity"] / eq["equity"].cummax() - 1).min()
        print(f"[fallback] Ann={ann*100:.2f}% Vol={vol*100:.2f}% Sharpe={sharpe:.2f} MDD={mdd*100:.2f}% "
              f"Final={eq['equity'].iloc[-1]:,.0f}")
    else:
        print("[fallback] 无法生成有效NAV（数据太短？）")


class TwoStageDataHandler(DailyBarDataHandler):
    """
    适配 two_stage 策略的数据处理器
    - 首次 get_latest_bars() 若未 update_bars()，自动兜底推进一次，避免 RuntimeError
    - 首次 update_bars() 之后把完整面板塞给策略做 preprocess（只做一次）
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.strategy: TwoStageEnhancedCBStrategy | None = None
        self._preprocessed = False
        self._seeded = False   # 是否已至少推进过一次

    def set_strategy(self, strategy: TwoStageEnhancedCBStrategy):
        self.strategy = strategy

    def update_bars(self):
        super().update_bars()
        self._seeded = True
        # 仅首次做预处理（把全量面板交给策略，让其计算趋势强度等派生列）
        if self.strategy and not self._preprocessed:
            self.strategy.cb_data = self.cb_df
            self.strategy.preprocess()
            self._preprocessed = True

    def get_latest_bars(self) -> Tuple[pd.Timestamp, Dict[str, Dict[str, Any]]]:
        """
        兜底：若底层实现要求必须先 update_bars()，而 Engine 先调用了 get_latest_bars()，
        我们捕获异常并自动推进一次，再次获取。
        """
        try:
            return super().get_latest_bars()
        except RuntimeError as e:
            msg = str(e).lower()
            if "update_bars" in msg and "before get_latest_bars" in msg:
                # 先推进一根，再试一次
                super().update_bars()
                self._seeded = True
                return super().get_latest_bars()
            raise

    # 可选：若引擎支持 next-open 成交且调用了 peek_next_bars，这里做安全代理
    def peek_next_bars(self):
        fn = getattr(super(), "peek_next_bars", None)
        if callable(fn):
            return fn()
        # 没有就返回 StopIteration 语义
        raise StopIteration("peek_next_bars() not supported by base handler.")


def load_strategy_config(config_path: str) -> StrategyConfig:
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    return StrategyConfig(
        top_n=cfg.get('top_n', 10),
        rebalance=cfg.get('rebalance', 'weekly'),
        rebalance_weekday=cfg.get('rebalance_weekday', 4),
        min_hold_days=cfg.get('min_hold_days', 5),
        trend_in=cfg.get('trend_in', 75.0),
        trend_out=cfg.get('trend_out', 50.0),
        price_min=cfg.get('price_min', 100.0),
        price_max=cfg.get('price_max', 150.0),
        premium_min=cfg.get('premium_min', 0.0),
        premium_max=cfg.get('premium_max', 70.0),
        term_min_years=cfg.get('term_min_years', 1.0),
        turnover_min=cfg.get('turnover_min', 1_000_000.0),
        exclude_new_days=cfg.get('exclude_new_days', 3),
        blacklist=cfg.get('blacklist', []),
        factor_weights=cfg.get('factor_weights', {}),
        lot_size=cfg.get('lot_size', 10),
        target_gross_exposure=cfg.get('target_gross_exposure', 1.0),
        execution=cfg.get('execution', 'next_open'),
        commission_rate=cfg.get('commission_rate', 0.0003),
        slippage=cfg.get('slippage', 0.001),
    )


def main():
    parser = argparse.ArgumentParser(description='Two-Stage Strategy Backtest')
    parser.add_argument('--data', type=str, default='data/cb_all.parquet', help='可转债数据文件路径（parquet/csv）')
    parser.add_argument('--config', type=str, default='strategies/two_stage_cb_config.json', help='策略配置文件路径')
    parser.add_argument('--start', type=str, default='2022-01-01', help='回测开始日期')
    parser.add_argument('--end', type=str, default='2024-12-31', help='回测结束日期')
    parser.add_argument('--initial_capital', type=float, default=1_000_000.0, help='初始资金')
    parser.add_argument('--output', type=str, default='output/two_stage_backtest', help='输出目录')
    args = parser.parse_args()

    # 日志
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    logger = logging.getLogger(__name__)

    # 文件检查
    data_p = Path(args.data)
    if not data_p.exists():
        logger.error(f"数据文件不存在: {args.data}")
        return
    cfg_p = Path(args.config)
    if not cfg_p.exists():
        logger.error(f"配置文件不存在: {args.config}")
        return

    # 输出目录
    out_dir = Path(args.output); out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("开始加载 two_stage 策略回测...")

    try:
        # 1) 策略配置
        config = load_strategy_config(args.config)
        logger.info(f"策略配置加载完成: top_n={config.top_n}, rebalance={config.rebalance}")

        # 2) 策略实例
        strategy = TwoStageEnhancedCBStrategy(config)
        logger.info("策略实例创建完成")

        # 3) 数据处理器
        data_handler = TwoStageDataHandler(
            cb_parquet_path=str(data_p),
            start_date=args.start,
            end_date=args.end,
            skip_suspended=True,
            log_level=logging.INFO
        )
        data_handler.set_strategy(strategy)

        # 先行把全量面板塞给策略并做一次预处理（和首次 update_bars 里只会做一次，冗余也安全）
        strategy.cb_data = data_handler.cb_df
        strategy.preprocess()

        logger.info(f"Loaded CB {len(data_handler.cb_df)} rows, "
                    f"{data_handler.cb_df['cb_code'].nunique()} symbols.")
        logger.info(f"数据处理器创建完成，数据范围: {args.start} 到 {args.end}")
        logger.info(f"策略数据设置完成，数据形状: {strategy.cb_data.shape}")

        # 4) 经纪人
        broker = SimpleBroker(slippage_bps=int(config.slippage * 10000), log_level=logging.INFO)
        logger.info("经纪人创建完成")

        # 5) 投资组合
        portfolio = BasicPortfolio(initial_capital=args.initial_capital, log_level=logging.INFO)
        logger.info(f"投资组合创建完成，初始资金: {args.initial_capital:,.0f}")

        # 6) 报告器
        reporter = TearSheetReporter(out_dir=out_dir, log_level=logging.INFO)
        logger.info("报告器创建完成")

        # 7) 引擎
        engine = BacktestEngine(
            data_handler=data_handler,
            strategy=strategy,
            broker=broker,
            portfolio=portfolio,
            reporter=reporter,
            log_level=logging.INFO
        )
        logger.info("回测引擎创建完成")

        # ------------ 关键：预推进一根 bar（保险） ------------
        try:
            data_handler.update_bars()
        except Exception:
            # 如果底层会在 engine.run() 内部推进，也没关系
            pass

        # 8) 运行回测
        logger.info("开始运行回测...")
        engine.run()
        logger.info("回测完成！")

        eq_path = Path(args.output) / "equity_curve.csv"
        if (not eq_path.exists()) or (eq_path.stat().st_size == 0):
            logger.warning("Engine 未产出 NAV，切换到 fallback 本地回测循环…")
            _fallback_backtest(strategy, Path(args.output), init_cash=args.initial_capital)

        logger.info(f"结果保存在: {out_dir}")

    except Exception as e:
        logger.error(f"回测过程中发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
