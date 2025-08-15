"""
run_five_factor_backtest.py
---------------------------
双低五因子增强策略 回测入口脚本

用法示例：
  python run_five_factor_backtest.py --start 2024-01-01 --end 2024-06-30 --cash 1000000

可选参数：
  --cb data/cb_all.parquet
  --stk data/stock_full.parquet
  --cb-info data/cb_info_full.parquet
  --config strategies/five_factor_config.json
  --slippage 2 --max-pct-vol 0.15 --impact 0.0005
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from framework.engine import BacktestEngine
from framework.data_handler import DailyBarDataHandler
from framework.portfolio import BasicPortfolio
from framework.broker import SimpleBroker
from framework.reporting import TearSheetReporter
from strategies.five_factor_enhanced_strategy import (
    DualLowFiveFactorStrategy,
    load_strategy_config,
)
import perf_metrics


def _default_path(p: str) -> str | None:
    path = Path(p)
    return str(path) if path.exists() else None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run backtest for DualLowFiveFactorStrategy")
    p.add_argument("--start", type=str, default="2024-01-01", help="YYYY-MM-DD")
    p.add_argument("--end", type=str, default="2024-12-31", help="YYYY-MM-DD")
    p.add_argument("--cash", type=float, default=1_000_000.0, help="initial capital")
    p.add_argument("--cb", type=str, default=str(Path("data") / "cb_all.parquet"))
    p.add_argument("--stk", type=str, default=str(Path("data") / "stock_full.parquet"))
    p.add_argument("--cb-info", dest="cb_info", type=str, default=str(Path("data") / "cb_info_full.parquet"))
    p.add_argument("--config", type=str, default=str(Path("strategies") / "five_factor_config.json"))
    p.add_argument("--slippage", type=int, default=2, help="bps: buy +bps / sell -bps")
    p.add_argument("--max-pct-vol", dest="max_pct_vol", type=float, default=0.15)
    p.add_argument("--impact", type=float, default=0.0005, help="impact cost coeff")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    print("🚀 启动双低五因子增强策略回测")
    print("=" * 60)

    # 1) 数据处理器
    cb_path = _default_path(args.cb)
    if cb_path is None:
        raise FileNotFoundError(f"缺少可转债数据: {args.cb}")
    stk_path = _default_path(args.stk)
    cb_info_path = _default_path(args.cb_info)

    print("📊 初始化数据处理器…")
    data_handler = DailyBarDataHandler(
        cb_parquet_path=cb_path,
        stk_parquet_path=stk_path,
        cb_info_path=cb_info_path,
        start_date=args.start,
        end_date=args.end,
    )

    # 2) 策略
    print("📈 初始化策略…")
    # load_strategy_config 会在默认路径不存在时创建默认配置
    cfg_path = Path(args.config)
    config = load_strategy_config(str(cfg_path) if cfg_path.exists() else None)
    strategy = DualLowFiveFactorStrategy(config=config)

    # 3) 组合与交易
    print("💰 初始化投资组合…")
    portfolio = BasicPortfolio(initial_capital=float(args.cash))

    print("🔄 初始化交易执行器…")
    broker = SimpleBroker(
        slippage_bps=int(args.slippage),
        max_pct_vol=float(args.max_pct_vol),
        impact_coeff=float(args.impact),
    )

    # 4) 报告
    print("📋 初始化报告生成器…")
    reporter = TearSheetReporter()

    # 5) 引擎
    print("⚙️ 初始化回测引擎…")
    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,
        broker=broker,
        portfolio=portfolio,
        reporter=reporter,
    )

    # 6) 运行
    print("🏃 开始回测…")
    t0 = datetime.now()
    try:
        engine.run()
        t1 = datetime.now()
        print(f"✅ 回测完成！耗时: {t1 - t0}")

        # 汇总指标
        nav = portfolio.nav_history
        if nav:
            nav_df = pd.DataFrame(nav, columns=["datetime", "nav"]).set_index("datetime").sort_index()
            rets = nav_df["nav"].pct_change().dropna().to_frame("strategy")
            m = perf_metrics.calc_metrics(rets)
            row = m.iloc[0] if isinstance(m, pd.DataFrame) else m
            print("\n" + "=" * 60)
            print("📈 回测指标（perf_metrics）")
            print("=" * 60)
            def _p(x, pct=False, nd=2):
                try:
                    return f"{x:.{nd}%}" if pct else f"{x:.{nd}f}"
                except Exception:
                    return "N/A"
            metric_specs = [
                ("累计收益", "cumulative_return", True),
                ("年化收益", "annual_return", True),
                ("年化波动", "annual_volatility", True),
                ("夏普比率", "sharpe_ratio", False),
                ("索提诺比率", "sortino_ratio", False),
                ("最大回撤", "max_drawdown", True),
                ("最大回撤持续天数", "max_drawdown_duration", False),
                ("Calmar比率", "calmar_ratio", False),
                ("Omega比率", "omega_ratio", False),
                ("VaR(95%)", "var_95", True),
                ("CVaR(95%)", "cvar_95", True),
                ("Tail比率", "tail_ratio", False),
                ("胜率", "win_rate", True),
                ("盈亏比", "payoff_ratio", False),
                ("盈利因子", "profit_factor", False),
            ]
            for label, key, as_pct in metric_specs:
                val = row.get(key, float("nan"))
                print(f"{label}: {_p(val, pct=as_pct)}")
        else:
            print("⚠️ 未收集到净值序列")
    except Exception as e:
        print(f"❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

