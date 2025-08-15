"""
optuna_tune.py
---------------------------------
基于现有五因子增强策略做超参搜索（单目标 + 软惩罚）。

用法示例：
  python optuna_tune.py

说明：
- 仅使用测试窗(OOS)成绩计分；窗口配置见 WALK_WINDOWS。
- 搜索空间仅覆盖最敏感、与实盘直接相关的参数；第二轮再放开因子权重。
- Study 持久化到 sqlite，方便断点续跑与结果复现。
"""

from __future__ import annotations

import json
import math
import os
import gc
from dataclasses import asdict
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

import optuna
from optuna.samplers import TPESampler
from optuna.pruners import MedianPruner

# 框架与策略
from framework.engine import BacktestEngine
from framework.data_handler import DailyBarDataHandler
from framework.portfolio import BasicPortfolio
from framework.broker import SimpleBroker
from framework.reporting import TearSheetReporter
from strategies.five_factor_enhanced_strategy import (
    StrategyConfig,
    DualLowFiveFactorStrategy,
)
import perf_metrics


# ============== Walk-forward 窗口（示例） ==============
# 使用你的实际数据区间进行调整；只用测试窗计分
WALK_WINDOWS: List[Tuple[str, str, str, str]] = [
    ("2018-01-01", "2018-12-31", "2019-01-01", "2019-06-30"),
    ("2018-07-01", "2019-06-30", "2019-07-01", "2019-12-31"),
    ("2019-01-01", "2019-12-31", "2020-01-01", "2020-06-30"),
]


# ============== 评分：单目标 + 软惩罚 ==============
def _score_with_penalty(calmar: float, sortino: float, max_dd: float, turnover: float) -> float:
    calmar_c = max(0.0, float(calmar))
    sortino_c = max(0.0, float(sortino))
    base = 0.6 * calmar_c + 0.4 * sortino_c

    penalty = 0.0
    # 回撤软约束（>18% 开始扣分）
    if max_dd > 0.18:
        penalty += (max_dd - 0.18) * 8.0
    # 年化换手软约束（>4 开始扣分）
    if turnover > 4.0:
        penalty += (turnover - 4.0) * 0.5
    return base - penalty


# ============== 搜索空间 ==============
def build_config_from_trial(trial: optuna.Trial) -> StrategyConfig:
    rotation = trial.suggest_categorical("rotation", ["weekly", "monthly"])
    # 使用固定分布名称，避免不同 trial 的分布类型/候选集冲突
    rotation_day_weekly = trial.suggest_categorical("rotation_day_weekly", [0, 1, 2, 3, 4])
    rotation_day_monthly = trial.suggest_categorical("rotation_day_monthly", [5, 10, 15, 20, 25])
    rotation_day = rotation_day_weekly if rotation == "weekly" else rotation_day_monthly

    premium_low = trial.suggest_float("premium_low", -10.0, 0.0, step=2.5)
    premium_high = trial.suggest_float("premium_high", 40.0, 55.0, step=5.0)
    if premium_high < premium_low + 10.0:
        raise optuna.TrialPruned()

    # extreme_gate 拆分为两个数值参数，避免 Optuna 对“非标量 choices”的持久化警告
    extreme_px_gate = trial.suggest_categorical("extreme_px_gate", [145.0, 150.0])
    extreme_prem_gate = trial.suggest_categorical("extreme_prem_gate", [55.0, 60.0])

    cfg = StrategyConfig(
        max_positions=trial.suggest_categorical("max_positions", [80, 100, 120, 150, 180, 200]),
        lots_per_trade=1,
        rotation=rotation,
        rotation_day=rotation_day,
        min_price=90.0,
        max_price=trial.suggest_categorical("max_price", [130.0, 135.0, 140.0]),
        min_balance_billion=trial.suggest_categorical("min_balance_billion", [1.0, 1.5, 2.0, 3.0]),
        max_realized_vol_20d=trial.suggest_categorical("max_realized_vol_20d", [45.0, 50.0, 55.0, 60.0]),
        premium_bounds=(premium_low, premium_high),
        extreme_price_premium_gate=(extreme_px_gate, extreme_prem_gate),
        blacklist=None,
        take_profit=1e9,  # 默认关闭硬止盈
        stop_loss=trial.suggest_int("stop_loss", 85, 90),
        momentum_mode=trial.suggest_categorical("momentum_mode", ["trend", "reversal"]),
        momentum_lookback=trial.suggest_categorical("momentum_lookback", [20, 60, 120]),
        hist_percentile_lookback=252,
        iv_window=20,
        w_dual_low=0.35,
        w_hist_pct=0.20,
        w_low_iv=0.20,
        w_momentum=0.20,
        w_balance=0.05,
    )
    return cfg


# ============== Backtest 适配器 ==============
def _detect_exchange(symbol: str) -> str:
    if symbol.endswith(".SH"):
        return "SSE"
    if symbol.endswith(".SZ"):
        return "SZSE"
    return "SSE" if symbol.startswith(("5", "6", "9", "11")) else "SZSE"


class TrackingBroker(SimpleBroker):
    """在 SimpleBroker 基础上累计成交额，以估算换手率。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.traded_value_by_day: Dict[pd.Timestamp, float] = {}

    def execute_signals(self, signals):  # type: ignore[override]
        events = super().execute_signals(signals)
        for evt in events:
            try:
                lot = 10 if _detect_exchange(evt.symbol) in ("SSE", "SZSE") else 10
                trade_val = float(evt.fill_price) * int(evt.quantity) * lot
                day = pd.Timestamp(evt.dt).normalize()
                self.traded_value_by_day[day] = self.traded_value_by_day.get(day, 0.0) + trade_val
            except Exception:
                pass
        return events


def _compute_metrics_from_nav(nav_history: List[Tuple[pd.Timestamp, float]], traded_value_by_day: Dict[pd.Timestamp, float]) -> Dict[str, float]:
    if not nav_history or len(nav_history) < 2:
        return {"calmar": 0.0, "sortino": 0.0, "max_dd": 1.0, "turnover": 0.0, "ann_ret": 0.0}

    nav_df = pd.DataFrame(nav_history, columns=["datetime", "nav"]).set_index("datetime").sort_index()
    rets = nav_df["nav"].pct_change().dropna()

    # 核心指标
    calmar = float(perf_metrics.calmar_ratio(nav_df["nav"].values, 252))
    sortino = float(perf_metrics.sortino_ratio(rets.values, 0.0, 252, True))
    max_dd = abs(float(perf_metrics.max_drawdown(rets.values)))  # 转正数
    ann_ret = float(perf_metrics.annual_return(rets.values, 252))

    # 换手率估算（年化）：总成交额 / 平均权益 / (天数/252)
    days = max(1, rets.shape[0] + 1)
    avg_equity = float(nav_df["nav"].mean())
    total_traded = float(sum(traded_value_by_day.get(d, 0.0) for d in nav_df.index.normalize().unique()))
    turnover = 0.0
    if avg_equity > 0:
        turnover = (total_traded / avg_equity) * (252.0 / days)

    return {
        "calmar": calmar if np.isfinite(calmar) else 0.0,
        "sortino": sortino if np.isfinite(sortino) else 0.0,
        "max_dd": max_dd if np.isfinite(max_dd) else 1.0,
        "turnover": turnover if np.isfinite(turnover) else 0.0,
        "ann_ret": ann_ret if np.isfinite(ann_ret) else 0.0,
    }


def run_backtest(cfg: StrategyConfig, train_start: str, train_end: str, test_start: str, test_end: str) -> Dict[str, float]:
    """
    执行一次 OOS 测试回测，仅在 [test_start, test_end] 期间计分。
    训练窗参数此版本未用于拟合，仅为接口保留。
    """
    # 数据路径（按需调整）
    cb_path = os.path.join("data", "cb_all.parquet")
    stk_path = os.path.join("data", "stock_full.parquet")
    cb_info_path = os.path.join("data", "cb_info_full.parquet")

    # 1) 数据
    data_handler = DailyBarDataHandler(
        cb_parquet_path=cb_path,
        stk_parquet_path=stk_path if os.path.exists(stk_path) else None,
        cb_info_path=cb_info_path if os.path.exists(cb_info_path) else None,
        start_date=test_start,
        end_date=test_end,
    )

    # 2) 策略
    strategy = DualLowFiveFactorStrategy(config=cfg)

    # 3) 组合 & 交易
    portfolio = BasicPortfolio(initial_capital=1_000_000.0)
    broker = TrackingBroker(slippage_bps=2, max_pct_vol=0.15, impact_coeff=0.0005)

    # 4) 报告（Optuna 批量运行时关闭文件输出，避免生成大量目录）
    reporter = None

    # 5) 引擎
    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,
        broker=broker,
        portfolio=portfolio,
        reporter=reporter,
    )

    # 6) 运行
    engine.run()

    # 7) 指标
    metrics = _compute_metrics_from_nav(portfolio.nav_history, broker.traded_value_by_day)

    # 清理
    del engine, data_handler, strategy, portfolio, broker, reporter
    gc.collect()

    return metrics


# ============== Optuna 目标函数 ==============
def objective(trial: optuna.Trial) -> float:
    cfg = build_config_from_trial(trial)

    oos_calmar: List[float] = []
    oos_sortino: List[float] = []
    oos_maxdd: List[float] = []
    oos_turnover: List[float] = []

    for step_idx, (tr_s, tr_e, te_s, te_e) in enumerate(WALK_WINDOWS, start=1):
        metrics = run_backtest(cfg, tr_s, tr_e, te_s, te_e)
        calmar = float(metrics.get("calmar", 0.0))
        sortino = float(metrics.get("sortino", 0.0))
        max_dd = float(metrics.get("max_dd", 0.0))
        turnover = float(metrics.get("turnover", 0.0))

        step_score = _score_with_penalty(calmar, sortino, max_dd, turnover)
        trial.report(step_score, step=step_idx)
        if trial.should_prune():
            raise optuna.TrialPruned()

        oos_calmar.append(calmar)
        oos_sortino.append(sortino)
        oos_maxdd.append(max_dd)
        oos_turnover.append(turnover)

    calmar_mean = sum(oos_calmar) / len(oos_calmar)
    sortino_mean = sum(oos_sortino) / len(oos_sortino)
    maxdd_mean = sum(oos_maxdd) / len(oos_maxdd)
    turnover_mean = sum(oos_turnover) / len(oos_turnover)

    score = _score_with_penalty(calmar_mean, sortino_mean, maxdd_mean, turnover_mean)

    trial.set_user_attr("cfg", asdict(cfg))
    trial.set_user_attr(
        "oos_metrics",
        dict(calmar=calmar_mean, sortino=sortino_mean, max_dd=maxdd_mean, turnover=turnover_mean),
    )
    return score


def main() -> None:
    storage = "sqlite:///optuna_cb.sqlite3"
    study = optuna.create_study(
        study_name="cb_five_factor_singleobj",
        direction="maximize",
        storage=storage,
        load_if_exists=True,
        sampler=TPESampler(seed=42),
        pruner=MedianPruner(n_warmup_steps=max(1, len(WALK_WINDOWS) // 2)),
    )
    study.optimize(objective, n_trials=120, show_progress_bar=True)

    best = study.best_trial
    print("Best score:", best.value)
    print("Best params:", best.params)
    print("Best OOS metrics:", best.user_attrs.get("oos_metrics"))

    # 导出 TopK
    top = sorted(
        study.trials,
        key=lambda t: (t.value if t.value is not None else -1),
        reverse=True,
    )[:10]
    out: List[Dict] = []
    for t in top:
        o = {"value": t.value, "params": t.params}
        o.update(t.user_attrs.get("oos_metrics") or {})
        out.append(o)
    with open("top10_trials.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()


