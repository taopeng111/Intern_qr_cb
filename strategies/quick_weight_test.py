#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Best Factor Weights Search (Optuna + Dirichlet, stitched OOS)
- 在单纯形上搜索因子权重（权重>=0，且和=1），用 Dirichlet 参数化
- Walk-forward 回测，拼接所有 OOS 净值后计算 Calmar/Sortino
- 去重剪枝（权重按小数4位规范化后判重）
- 导出最优权重 JSON 和 Top20 CSV

集成点（按你工程改三处）：
1) 导入路径：from optuna_tune import StrategyConfig, run_backtest, WALK_WINDOWS
2) 权重映射：apply_weights_to_cfg() 把权重写入 StrategyConfig
3) 成交数量单位：你的 TrackingBroker 若按“手”，确保用 quantity_unit_hint="hand"
"""

import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
import optuna
from optuna.trial import TrialState

# Add parent directory to path to import framework modules
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# ====== [1] 导入你的回测与配置 ======
try:
    from strategies.optuna_tune import StrategyConfig, run_backtest, WALK_WINDOWS
except Exception as e:
    raise ImportError(
        "请确认可以导入 StrategyConfig / run_backtest / WALK_WINDOWS；"
        "若模块名不同，请修改下方导入语句。\n"
        f"Import error: {e}"
    )

FACTOR_NAMES = ["dual_low", "hist_pct", "low_iv", "momentum", "balance"]


# ====== [2] 权重 -> 配置 ======
def apply_weights_to_cfg(cfg: StrategyConfig, w: dict) -> StrategyConfig:
    # 若你的字段名不同，请改这里
    cfg.w_dual_low = float(w.get("dual_low", 0.0))
    cfg.w_hist_pct = float(w.get("hist_pct", 0.0))
    cfg.w_low_iv   = float(w.get("low_iv", 0.0))
    cfg.w_momentum = float(w.get("momentum", 0.0))
    cfg.w_balance  = float(w.get("balance", 0.0))
    return cfg


# ====== [3] 拼接OOS净值 -> 指标 ======
def compute_metrics_from_nav(nav_history, traded_value_by_day=None, initial_capital: float = 1_000_000.0):
    if not nav_history or len(nav_history) < 2:
        return {"calmar": 0.0, "sortino": 0.0, "max_dd": 1.0, "turnover": 0.0, "ann_ret": 0.0}

    df = pd.DataFrame(nav_history, columns=["datetime", "nav"]).set_index("datetime").sort_index()
    nav = df["nav"].astype(float).values
    rets = pd.Series(nav).pct_change().dropna().values

    # 年化收益（252交易日）
    def ann_return(r):
        if len(r) == 0: return 0.0
        mean = float(np.mean(r))
        return (1.0 + mean) ** 252 - 1.0

    # Sortino（MAR=0）
    def sortino_ratio(r):
        if len(r) == 0: return 0.0
        downside = r[r < 0.0]
        if downside.size == 0: return 0.0
        dr = float(np.std(downside, ddof=1)) * np.sqrt(252.0)
        if dr <= 1e-12: return 0.0
        return ann_return(r) / dr

    # Max Drawdown 基于净值
    rollmax = np.maximum.accumulate(nav)
    dd = 1.0 - (nav / np.maximum(rollmax, 1e-12))
    max_dd = float(dd.max()) if dd.size else 1.0

    ann_ret = float(ann_return(rets))
    calmar = float(ann_ret / max_dd) if max_dd > 1e-12 else 0.0

    # 年化换手率：sum(traded)/avg_equity * 252/trading_days
    if traded_value_by_day is None:
        turnover = 0.0
    else:
        nav_series = df["nav"]
        abs_equity = nav_series * initial_capital if nav_series.max() < 20 else nav_series
        avg_equity = float(abs_equity.mean())
        trading_days = max(1, len(df.index.unique()))
        # traded_value_by_day 的 key 允许是 Timestamp/date/string，统一 normalize
        total_traded = 0.0
        for d in df.index.unique():
            k = pd.Timestamp(d).normalize()
            total_traded += float(traded_value_by_day.get(k, 0.0))
        turnover = (total_traded / max(avg_equity, 1e-9)) * (252.0 / trading_days)

    return {
        "calmar": max(calmar, 0.0),
        "sortino": max(sortino_ratio(rets), 0.0),
        "max_dd": max_dd,
        "turnover": max(float(turnover), 0.0),
        "ann_ret": max(ann_ret, 0.0)
    }


# ====== [4] 计分函数（含轻度惩罚） ======
def score_with_penalty(metrics: dict) -> float:
    base = 0.6 * metrics.get("calmar", 0.0) + 0.4 * metrics.get("sortino", 0.0)
    maxdd = metrics.get("max_dd", 0.0)
    turov = metrics.get("turnover", 0.0)
    penalty = 0.0
    # 过小回撤（疑似口径/bug）轻微惩罚，避免不真实的最优
    if maxdd < 0.02:
        penalty += (0.02 - maxdd) * 10.0  # up to -0.2
    # 过高换手轻微惩罚
    if turov > 8.0:
        penalty += (turov - 8.0) * 0.05
    return float(base - penalty)


# ====== [5] Dirichlet 采样权重（可设最小权重 floor） ======
def suggest_simplex_weights(trial: optuna.Trial,
                            names=FACTOR_NAMES,
                            concentration: float = 1.0,
                            floor: float = 0.0) -> dict:
    # Since optuna Trial doesn't have dirichlet method, we simulate it using uniform sampling
    # and then normalize to create simplex weights
    raw_vals = []
    for i, name in enumerate(names):
        # Sample gamma distribution parameters for Dirichlet
        val = trial.suggest_float(f"dirichlet_{name}_{i}", 0.001, 10.0, log=True)
        raw_vals.append(val)
    
    # Normalize to create simplex (sum to 1)
    w = np.array(raw_vals)
    w = w / w.sum()
    
    if floor > 0.0:
        k = len(names)
        w = np.maximum(w, 0.0)
        w = (1 - k * floor) * (w / max(w.sum(), 1e-12)) + floor
        w = np.maximum(w, 0.0)
        w = w / max(w.sum(), 1e-12)
    return {n: float(w[i]) for i, n in enumerate(names)}

def normalized_weight_key(w: dict, decimals: int = 4) -> tuple:
    # 统一四舍五入后作为去重键
    return tuple(round(float(w[k]), decimals) for k in sorted(w.keys()))


# ====== [6] Optuna 目标函数 ======
def objective(trial: optuna.Trial,
              base_cfg: StrategyConfig,
              windows,
              initial_capital: float,
              weight_floor: float,
              concentration: float,
              seen_keys: set) -> float:
    # 采样权重，并做去重
    w = suggest_simplex_weights(trial, FACTOR_NAMES, concentration=concentration, floor=weight_floor)
    key = normalized_weight_key(w, decimals=4)
    if key in seen_keys:
        raise optuna.TrialPruned()
    seen_keys.add(key)

    # 拷贝配置，写入权重
    cfg = StrategyConfig()
    if hasattr(base_cfg, "__dict__"):
        for k, v in base_cfg.__dict__.items():
            setattr(cfg, k, v)
    cfg = apply_weights_to_cfg(cfg, w)

    # walk-forward 回测，拼接 OOS 净值
    stitched_nav = []
    stitched_traded = {}
    per_window_scores = []

    for (tr_s, tr_e, te_s, te_e) in windows:
        res = run_backtest(cfg, tr_s, tr_e, te_s, te_e)
        nav_hist   = res.get("nav_history") or []
        traded_map = res.get("traded_value_by_day") or {}
        mwin       = res.get("metrics") or {}

        stitched_nav.extend(nav_hist)
        for d, v in traded_map.items():
            d_norm = pd.Timestamp(d).normalize()
            stitched_traded[d_norm] = stitched_traded.get(d_norm, 0.0) + float(v)

        if mwin:
            per_window_scores.append(score_with_penalty(mwin))

    g = compute_metrics_from_nav(stitched_nav, stitched_traded, initial_capital=initial_capital)
    score = score_with_penalty(g)

    # 记录权重与 stitched-OOS 指标
    trial.set_user_attr("weights", w)
    trial.set_user_attr("oos_metrics_global", g)
    if per_window_scores:
        trial.set_user_attr("oos_score_mean", float(np.mean(per_window_scores)))
        trial.set_user_attr("oos_score_std", float(np.std(per_window_scores)))

    return score


# ====== [7] Runner ======
def run_search(args):
    storage = f"sqlite:///{args.sqlite}"
    sampler = optuna.samplers.TPESampler(seed=args.seed, multivariate=True, n_ei_candidates=64)
    pruner  = optuna.pruners.MedianPruner(n_warmup_steps=max(1, len(WALK_WINDOWS)//2))

    study = optuna.create_study(
        study_name=args.study,
        direction="maximize",
        storage=storage,
        load_if_exists=True,
        sampler=sampler,
        pruner=pruner,
    )

    base_cfg = StrategyConfig()  # 如需固定其他超参，可先在此对 base_cfg 赋值
    seen_keys = set()

    def _obj(trial: optuna.Trial):
        return objective(
            trial=trial,
            base_cfg=base_cfg,
            windows=WALK_WINDOWS,
            initial_capital=args.initial_capital,
            weight_floor=args.weight_floor,
            concentration=args.concentration,
            seen_keys=seen_keys,
        )

    study.optimize(_obj, n_trials=args.n_trials, n_jobs=args.n_jobs, show_progress_bar=True)

    # 导出
    os.makedirs(args.outdir, exist_ok=True)

    # 最优
    bt = study.best_trial
    best_record = {
        "value": bt.value,
        "weights": bt.user_attrs.get("weights"),
        "oos_metrics_global": bt.user_attrs.get("oos_metrics_global"),
        "params": bt.params,
        "number": bt.number,
    }
    with open(os.path.join(args.outdir, "best_weights.json"), "w", encoding="utf-8") as f:
        json.dump(best_record, f, ensure_ascii=False, indent=2)

    # Top20
    trials = sorted([t for t in study.get_trials(deepcopy=False) if t.state == TrialState.COMPLETE],
                    key=lambda t: t.value, reverse=True)[:20]
    rows = []
    for t in trials:
        g = t.user_attrs.get("oos_metrics_global", {}) or {}
        rows.append({
            "number": t.number,
            "value": t.value,
            "weights": json.dumps(t.user_attrs.get("weights"), ensure_ascii=False),
            "calmar": g.get("calmar"),
            "sortino": g.get("sortino"),
            "max_dd": g.get("max_dd"),
            "turnover": g.get("turnover"),
            "ann_ret": g.get("ann_ret"),
        })
    pd.DataFrame(rows).to_csv(os.path.join(args.outdir, "top20_weights.csv"), index=False)

    print("\n=== Best weights ===")
    print(json.dumps(best_record, ensure_ascii=False, indent=2))
    print(f"\nFiles saved to: {args.outdir}")


def parse_args():
    p = argparse.ArgumentParser(description="Search best factor weights (simplex / Dirichlet)")
    p.add_argument("--sqlite", default="optuna_weights.sqlite3", help="SQLite path for Optuna study")
    p.add_argument("--study", default="cb_weight_search", help="Study name")
    p.add_argument("--n-trials", type=int, default=200, help="Number of trials")
    p.add_argument("--n-jobs", type=int, default=1, help="Parallel jobs")
    p.add_argument("--seed", type=int, default=42, help="Random seed for sampler")
    p.add_argument("--initial-capital", type=float, default=1_000_000.0, help="Initial capital for turnover calc")
    p.add_argument("--weight-floor", type=float, default=0.0, help="Min per-weight before renorm (e.g., 0.02)")
    p.add_argument("--concentration", type=float, default=1.0, help="Dirichlet alpha: <1稀疏, 1均匀, >1更平均")
    p.add_argument("--outdir", default="./weight_search_out", help="Output directory")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_search(args)
