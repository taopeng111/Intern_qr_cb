"""
optuna_factor_weights.py
------------------------
专门优化五因子增强策略的权重配置

用法示例：
  python optuna_factor_weights.py --n-trials 200

特点：
- 专注于五因子权重优化（w_dual_low, w_hist_pct, w_low_iv, w_momentum, w_balance）
- 保持其他策略参数固定，只调整权重分配
- 支持约束条件：权重和=1，单个权重范围合理
- 多个评估指标：Sharpe、Calmar、Sortino、最大回撤等
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

# 框架与策略
from framework.engine import BacktestEngine
from framework.data_handler import DailyBarDataHandler
from framework.portfolio import BasicPortfolio
from framework.broker import SimpleBroker
from strategies.five_factor_enhanced_strategy import (
    StrategyConfig,
    DualLowFiveFactorStrategy,
)
import perf_metrics


# ============== 默认策略配置（除权重外固定） ==============
DEFAULT_CONFIG = StrategyConfig(
    max_positions=160,
    lots_per_trade=1,
    rotation="weekly",
    rotation_day_weekly=3,  # 周四调仓
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
    # 权重将由优化器决定
    w_dual_low=0.35,
    w_hist_pct=0.20,
    w_low_iv=0.20,
    w_momentum=0.20,
    w_balance=0.05,
)


# ============== 回测时间窗口 ==============
EVALUATION_WINDOWS = [
    ("2022-01-01", "2022-06-30"),
    ("2022-07-01", "2022-12-31"), 
    ("2023-01-01", "2023-06-30"),
    ("2023-07-01", "2023-12-31"),
    ("2024-01-01", "2024-06-30"),
]


# ============== 权重约束和采样 ==============
def sample_factor_weights(trial: optuna.Trial) -> Tuple[float, float, float, float, float]:
    """
    采样五因子权重，确保权重和为1且每个权重在合理范围内
    使用Dirichlet分布的近似：先独立采样，再归一化
    """
    # 方法1：先采样比例参数，再归一化
    raw_dual_low = trial.suggest_float("raw_dual_low", 0.1, 0.6)  # 双低因子权重范围
    raw_hist_pct = trial.suggest_float("raw_hist_pct", 0.05, 0.4)  # 历史分位权重范围
    raw_low_iv = trial.suggest_float("raw_low_iv", 0.05, 0.4)    # 低波动率权重范围
    raw_momentum = trial.suggest_float("raw_momentum", 0.05, 0.4) # 动量权重范围
    raw_balance = trial.suggest_float("raw_balance", 0.0, 0.2)   # 余额权重范围
    
    # 归一化确保和为1
    total = raw_dual_low + raw_hist_pct + raw_low_iv + raw_momentum + raw_balance
    if total <= 0:
        # 如果总和为0，使用默认权重
        return 0.35, 0.20, 0.20, 0.20, 0.05
    
    w_dual_low = raw_dual_low / total
    w_hist_pct = raw_hist_pct / total
    w_low_iv = raw_low_iv / total
    w_momentum = raw_momentum / total
    w_balance = raw_balance / total
    
    return w_dual_low, w_hist_pct, w_low_iv, w_momentum, w_balance


def build_config_with_weights(trial: optuna.Trial) -> StrategyConfig:
    """构建配置，只改变因子权重"""
    w_dual_low, w_hist_pct, w_low_iv, w_momentum, w_balance = sample_factor_weights(trial)
    
    # 复制默认配置
    config = StrategyConfig(**asdict(DEFAULT_CONFIG))
    
    # 更新权重
    config.w_dual_low = w_dual_low
    config.w_hist_pct = w_hist_pct
    config.w_low_iv = w_low_iv
    config.w_momentum = w_momentum
    config.w_balance = w_balance
    
    return config


# ============== 多目标评分函数 ==============
def calculate_composite_score(metrics: Dict[str, float]) -> float:
    """
    综合评分函数，平衡收益、风险和回撤
    """
    sharpe = max(0.0, float(metrics.get("sharpe", 0.0)))
    calmar = max(0.0, float(metrics.get("calmar", 0.0)))
    sortino = max(0.0, float(metrics.get("sortino", 0.0)))
    max_dd = float(metrics.get("max_dd", 1.0))
    ann_ret = max(0.0, float(metrics.get("ann_ret", 0.0)))
    
    # 基础得分：风险调整收益指标的加权平均
    base_score = 0.4 * sharpe + 0.4 * calmar + 0.2 * sortino
    
    # 回撤惩罚：超过15%开始扣分
    dd_penalty = 0.0
    if max_dd > 0.15:
        dd_penalty = (max_dd - 0.15) * 3.0
    
    # 收益奖励：年化收益超过10%有额外奖励
    ret_bonus = 0.0
    if ann_ret > 0.10:
        ret_bonus = (ann_ret - 0.10) * 0.5
    
    final_score = base_score - dd_penalty + ret_bonus
    return max(0.0, final_score)


# ============== 回测执行器 ==============
def run_single_backtest(config: StrategyConfig, start_date: str, end_date: str) -> Dict[str, float]:
    """执行单次回测"""
    try:
        # 数据处理器
        data_handler = DailyBarDataHandler(
            cb_parquet_path="data/cb_all.parquet",
            stk_parquet_path="data/stock_full.parquet" if pd.io.common.file_exists("data/stock_full.parquet") else None,
            cb_info_path="data/cb_info_full.parquet" if pd.io.common.file_exists("data/cb_info_full.parquet") else None,
            start_date=start_date,
            end_date=end_date,
        )
        
        # 策略
        strategy = DualLowFiveFactorStrategy(config=config)
        
        # 投资组合
        portfolio = BasicPortfolio(initial_capital=1_000_000.0)
        
        # 交易器
        broker = SimpleBroker(slippage_bps=2, max_pct_vol=0.15, impact_coeff=0.0005)
        
        # 引擎
        engine = BacktestEngine(
            data_handler=data_handler,
            strategy=strategy,
            broker=broker,
            portfolio=portfolio,
            reporter=None,  # 优化时不生成报告
        )
        
        # 运行回测
        engine.run()
        
        # 计算绩效指标
        nav_history = portfolio.nav_history
        if not nav_history or len(nav_history) < 2:
            return {"sharpe": 0.0, "calmar": 0.0, "sortino": 0.0, "max_dd": 1.0, "ann_ret": 0.0}
        
        nav_df = pd.DataFrame(nav_history, columns=["datetime", "nav"]).set_index("datetime")
        nav_df = nav_df.sort_index()
        rets = nav_df["nav"].pct_change().dropna()
        
        if len(rets) < 10:  # 数据太少
            return {"sharpe": 0.0, "calmar": 0.0, "sortino": 0.0, "max_dd": 1.0, "ann_ret": 0.0}
        
        # 计算各项指标
        sharpe = float(perf_metrics.sharpe_ratio(rets.values, risk_free=0.0, periods=252, annualized=True))
        sortino = float(perf_metrics.sortino_ratio(rets.values, risk_free=0.0, periods=252, annualized=True))
        ann_ret = float(perf_metrics.annual_return(rets.values, periods=252))
        max_dd = abs(float(perf_metrics.max_drawdown(nav_df["nav"].values, is_nav=True)))
        calmar = ann_ret / max_dd if max_dd > 1e-6 else 0.0
        
        # 清理内存
        del engine, data_handler, strategy, portfolio, broker
        gc.collect()
        
        return {
            "sharpe": max(0.0, sharpe) if np.isfinite(sharpe) else 0.0,
            "calmar": max(0.0, calmar) if np.isfinite(calmar) else 0.0,
            "sortino": max(0.0, sortino) if np.isfinite(sortino) else 0.0,
            "max_dd": max_dd if np.isfinite(max_dd) else 1.0,
            "ann_ret": max(0.0, ann_ret) if np.isfinite(ann_ret) else 0.0,
        }
        
    except Exception as e:
        print(f"回测执行失败: {e}")
        return {"sharpe": 0.0, "calmar": 0.0, "sortino": 0.0, "max_dd": 1.0, "ann_ret": 0.0}


# ============== Optuna目标函数 ==============
def objective(trial: optuna.Trial) -> float:
    """优化目标函数"""
    config = build_config_with_weights(trial)
    
    # 在多个时间窗口上评估
    all_metrics = []
    for start_date, end_date in EVALUATION_WINDOWS:
        metrics = run_single_backtest(config, start_date, end_date)
        all_metrics.append(metrics)
        
        # 中间报告（用于剪枝）
        score = calculate_composite_score(metrics)
        trial.report(score, step=len(all_metrics))
        
        if trial.should_prune():
            raise optuna.TrialPruned()
    
    # 计算平均指标
    avg_metrics = {}
    for key in ["sharpe", "calmar", "sortino", "max_dd", "ann_ret"]:
        values = [m[key] for m in all_metrics if np.isfinite(m[key])]
        avg_metrics[key] = np.mean(values) if values else 0.0
    
    # 计算最终得分
    final_score = calculate_composite_score(avg_metrics)
    
    # 保存权重信息和指标
    trial.set_user_attr("weights", {
        "w_dual_low": config.w_dual_low,
        "w_hist_pct": config.w_hist_pct,
        "w_low_iv": config.w_low_iv,
        "w_momentum": config.w_momentum,
        "w_balance": config.w_balance,
    })
    trial.set_user_attr("avg_metrics", avg_metrics)
    trial.set_user_attr("all_metrics", all_metrics)
    
    return final_score


# ============== 主函数 ==============
def main():
    parser = argparse.ArgumentParser(description="优化五因子权重")
    parser.add_argument("--n-trials", type=int, default=100, help="优化轮数")
    parser.add_argument("--study-name", type=str, default="factor_weights_optimization", help="study名称")
    parser.add_argument("--db-file", type=str, default="optuna_factor_weights.sqlite3", help="数据库文件")
    args = parser.parse_args()
    
    print("🚀 开始五因子权重优化")
    print(f"优化轮数: {args.n_trials}")
    print(f"评估窗口: {len(EVALUATION_WINDOWS)}个")
    print("=" * 60)
    
    # 创建study
    storage = f"sqlite:///{args.db_file}"
    study = optuna.create_study(
        study_name=args.study_name,
        direction="maximize",
        storage=storage,
        load_if_exists=True,
        sampler=TPESampler(seed=42, multivariate=True),
        pruner=MedianPruner(n_warmup_steps=2),
    )
    
    # 运行优化
    study.optimize(objective, n_trials=args.n_trials, show_progress_bar=True)
    
    # 输出结果
    print("\n" + "=" * 60)
    print("🎯 优化结果")
    print("=" * 60)
    
    best = study.best_trial
    print(f"最佳得分: {best.value:.4f}")
    print(f"最佳权重:")
    weights = best.user_attrs["weights"]
    for factor, weight in weights.items():
        print(f"  {factor}: {weight:.3f}")
    
    print(f"\n最佳指标:")
    metrics = best.user_attrs["avg_metrics"]
    for metric, value in metrics.items():
        if metric == "max_dd":
            print(f"  {metric}: {value:.3f}")
        else:
            print(f"  {metric}: {value:.3f}")
    
    # 保存Top10结果
    top_trials = sorted(study.trials, key=lambda t: t.value or -np.inf, reverse=True)[:10]
    results = []
    
    for i, trial in enumerate(top_trials, 1):
        if trial.value is None:
            continue
            
        result = {
            "rank": i,
            "score": trial.value,
            "weights": trial.user_attrs.get("weights", {}),
            "metrics": trial.user_attrs.get("avg_metrics", {}),
        }
        results.append(result)
    
    # 保存结果到文件
    output_file = "factor_weights_optimization_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n📊 Top 10结果已保存到: {output_file}")
    
    # 显示权重分布统计
    print(f"\n📈 权重分布统计 (Top 10):")
    if results:
        weight_stats = {}
        for factor in ["w_dual_low", "w_hist_pct", "w_low_iv", "w_momentum", "w_balance"]:
            values = [r["weights"].get(factor, 0) for r in results]
            weight_stats[factor] = {
                "mean": np.mean(values),
                "std": np.std(values),
                "min": np.min(values),
                "max": np.max(values),
            }
        
        for factor, stats in weight_stats.items():
            print(f"  {factor}: {stats['mean']:.3f} ± {stats['std']:.3f} [{stats['min']:.3f}, {stats['max']:.3f}]")


if __name__ == "__main__":
    main()
