"""
optuna_factor_weights.py
------------------------
Specialized optimization for five-factor enhanced strategy weight configuration

Usage example:
  python optuna_factor_weights.py --n-trials 200

Features:
- Focus on five-factor weight optimization (w_dual_low, w_hist_pct, w_low_iv, w_momentum, w_balance)
- Keep other strategy parameters fixed, only adjust weight allocation
- Support constraint conditions: weight sum=1, individual weight ranges reasonable
- Multiple evaluation metrics: Sharpe, Calmar, Sortino, maximum drawdown, etc.
"""

from __future__ import annotations

import argparse
import json
import gc
from dataclasses import asdict
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

import optuna
from optuna.samplers import TPESampler
from optuna.pruners import MedianPruner
from optuna.trial import TrialState

# Framework and Strategy
from framework.engine import BacktestEngine
from framework.data_handler import DailyBarDataHandler
from framework.portfolio import BasicPortfolio
from framework.broker import SimpleBroker
from strategies.five_factor_enhanced_strategy import (
    StrategyConfig,
    DualLowFiveFactorStrategy,
)
import perf_metrics


# ============== Default Strategy Configuration (Fixed except weights) ==============
DEFAULT_CONFIG = StrategyConfig(
    max_positions=160,
    lots_per_trade=1,
    rotation="weekly",
    rotation_day_weekly=3,  # Thursday rebalancing
    min_price=90.0,
    max_price=130.0,
    min_balance_billion=3.0,
    max_realized_vol_20d=60.0,
    premium_bounds=(-2.5, 52.5),
    extreme_price_premium_gate=(145.0, 60.0),
    blacklist=[],
    take_profit=125.0,
    stop_loss=87.0,
    momentum_mode="reversal",
    momentum_lookback=120,
    hist_percentile_lookback=252,
    iv_window=20,
    # Weights will be determined by optimizer
    w_dual_low=0.35,
    w_hist_pct=0.20,
    w_low_iv=0.20,
    w_momentum=0.20,
    w_balance=0.05,
)


# ============== Backtesting Time Windows ==============
EVALUATION_WINDOWS = [
    ("2022-01-01", "2022-06-30"),
    ("2022-07-01", "2022-12-31"), 
    ("2023-01-01", "2023-06-30"),
    ("2023-07-01", "2023-12-31"),
    ("2024-01-01", "2024-06-30"),
]


# ============== Weight Constraints and Sampling ==============
def sample_factor_weights(trial: optuna.Trial) -> Tuple[float, float, float, float, float]:
    """
    Sample five-factor weights, ensuring weight sum equals 1 and each weight is in reasonable range
    Use Dirichlet distribution approximation: sample independently first, then normalize
    """
    # Method 1: Sample proportion parameters first, then normalize
    raw_dual_low = trial.suggest_float("raw_dual_low", 0.1, 0.6)  # Dual-low factor weight range
    raw_hist_pct = trial.suggest_float("raw_hist_pct", 0.05, 0.4)  # Historical percentile weight range
    raw_low_iv = trial.suggest_float("raw_low_iv", 0.05, 0.4)    # Low volatility weight range
    raw_momentum = trial.suggest_float("raw_momentum", 0.05, 0.4) # Momentum weight range
    raw_balance = trial.suggest_float("raw_balance", 0.0, 0.2)   # Balance weight range
    
    # Normalize to ensure sum equals 1
    total = raw_dual_low + raw_hist_pct + raw_low_iv + raw_momentum + raw_balance
    if total <= 0:
        # If total is 0, use default weights
        return 0.35, 0.20, 0.20, 0.20, 0.05
    
    # Normalize
    w_dual_low = raw_dual_low / total
    w_hist_pct = raw_hist_pct / total
    w_low_iv = raw_low_iv / total
    w_momentum = raw_momentum / total
    w_balance = raw_balance / total
    
    return w_dual_low, w_hist_pct, w_low_iv, w_momentum, w_balance


def objective(trial: optuna.Trial) -> float:
    """
    Optimization objective function
    Returns negative Sharpe ratio (minimization problem)
    """
    # Sample weights
    w_dual_low, w_hist_pct, w_low_iv, w_momentum, w_balance = sample_factor_weights(trial)
    
    # Update strategy configuration
    config = StrategyConfig(
        **{**asdict(DEFAULT_CONFIG), 
           "w_dual_low": w_dual_low,
           "w_hist_pct": w_hist_pct,
           "w_low_iv": w_low_iv,
           "w_momentum": w_momentum,
           "w_balance": w_balance}
    )
    
    # Run backtesting on multiple time windows
    total_sharpe = 0.0
    valid_windows = 0
    
    for start_date, end_date in EVALUATION_WINDOWS:
        try:
            # Initialize components
            strategy = DualLowFiveFactorStrategy(config)
            data_handler = DailyBarDataHandler(
                start_date=start_date,
                end_date=end_date
            )
            portfolio = BasicPortfolio(initial_cash=1000000)
            broker = SimpleBroker()
            
            # Run backtesting
            engine = BacktestEngine(
                data_handler=data_handler,
                strategy=strategy,
                broker=broker,
                portfolio=portfolio
            )
            engine.run()
            
            # Calculate performance metrics
            returns = portfolio.get_returns()
            sharpe = perf_metrics.sharpe_ratio(returns)
            
            if not np.isnan(sharpe):
                total_sharpe += sharpe
                valid_windows += 1
                
        except Exception as e:
            print(f"Error in window {start_date}-{end_date}: {e}")
            continue
    
    if valid_windows == 0:
        return -999.0  # Penalty for failed backtesting
    
    avg_sharpe = total_sharpe / valid_windows
    return -avg_sharpe  # Negative for minimization


def main():
    """Main optimization function"""
    parser = argparse.ArgumentParser(description="Optimize five-factor strategy weights")
    parser.add_argument("--n-trials", type=int, default=100, help="Number of optimization trials")
    parser.add_argument("--study-name", type=str, default="five_factor_weights", help="Study name")
    args = parser.parse_args()
    
    # Create or load study
    study = optuna.create_study(
        study_name=args.study_name,
        storage="sqlite:///optuna_weights.sqlite3",
        load_if_exists=True,
        sampler=TPESampler(seed=42),
        pruner=MedianPruner()
    )
    
    # Run optimization
    study.optimize(objective, n_trials=args.n_trials, show_progress_bar=True)
    
    # Print results
    print("\n" + "="*50)
    print("Optimization Results")
    print("="*50)
    
    print(f"Best trial: {study.best_trial.number}")
    print(f"Best value: {-study.best_trial.value:.4f}")
    print("Best parameters:")
    for key, value in study.best_trial.params.items():
        print(f"  {key}: {value:.4f}")
    
    # Save top trials
    top_trials = sorted(study.trials, key=lambda t: t.value)[:10]
    top_results = []
    
    for i, trial in enumerate(top_trials):
        if trial.state == TrialState.COMPLETE:
            top_results.append({
                "rank": i + 1,
                "trial": trial.number,
                "sharpe": -trial.value,
                "params": trial.params
            })
    
    with open("top10_trials.json", "w") as f:
        json.dump(top_results, f, indent=2, ensure_ascii=False)
    
    print(f"\nTop 10 trials saved to top10_trials.json")
    print("Study saved to optuna_weights.sqlite3")


if __name__ == "__main__":
    main()
