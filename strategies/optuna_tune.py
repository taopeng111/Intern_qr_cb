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
from optuna.trial import TrialState

# 框架与策略
import sys
import os
# Add parent directory to path to import framework modules
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

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

    # 更细粒度的premium范围，确保有足够间隔
    premium_low = trial.suggest_float("premium_low", -10.0, 0.0, step=2.5)
    premium_high = trial.suggest_float("premium_high", 40.0, 55.0, step=2.5)
    if premium_high < premium_low + 10.0:
        raise optuna.TrialPruned()

    # extreme_gate 拆分为两个数值参数，增加更多选择
    extreme_px_gate = trial.suggest_float("extreme_px_gate", 145.0, 155.0, step=2.5)
    extreme_prem_gate = trial.suggest_float("extreme_prem_gate", 55.0, 65.0, step=2.5)

    cfg = StrategyConfig(
        max_positions=trial.suggest_int("max_positions", 80, 200, step=20),
        lots_per_trade=1,
        rotation=rotation,
        rotation_day=rotation_day,
        min_price=90.0,
        max_price=trial.suggest_float("max_price", 130.0, 145.0, step=2.5),
        min_balance_billion=trial.suggest_float("min_balance_billion", 1.0, 3.0, step=0.5),
        max_realized_vol_20d=trial.suggest_float("max_realized_vol_20d", 45.0, 60.0, step=2.5),
        premium_bounds=(premium_low, premium_high),
        extreme_price_premium_gate=(extreme_px_gate, extreme_prem_gate),
        blacklist=None,
        take_profit=1e9,  # 默认关闭硬止盈
        stop_loss=trial.suggest_int("stop_loss", 85, 90),
        momentum_mode=trial.suggest_categorical("momentum_mode", ["trend", "reversal"]),
        momentum_lookback=trial.suggest_categorical("momentum_lookback", [20, 40, 60, 120]),
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
        # 显式单位配置，避免歧义
        self.quantity_unit_hint = kwargs.pop("quantity_unit_hint", "zhang")  # "hand" 或 "zhang"
        super().__init__(*args, **kwargs)
        self.traded_value_by_day: Dict[pd.Timestamp, float] = {}

    def execute_signals(self, signals):  # type: ignore[override]
        events = super().execute_signals(signals)
        # 只处理真实的 FillEvent，忽略其他类型的事件
        for evt in events:
            if hasattr(evt, 'fill_price') and hasattr(evt, 'quantity'):  # 确认是 FillEvent
                self._record_trade(evt)
        return events
    
    def _record_trade(self, fill_event) -> None:
        """记录真实成交的交易量"""
        try:
            # 兼容不同的属性命名
            price = float(getattr(fill_event, "fill_price", getattr(fill_event, "price", 0.0)))
            qty = float(getattr(fill_event, "quantity", getattr(fill_event, "filled_qty", 0.0)))

            # 单位处理：优先事件字段，次选显式配置，最后启发式
            unit = getattr(fill_event, "quantity_unit", None) or self.quantity_unit_hint
            if unit in ("hand", "lot"):
                qty_in_zhang = qty * 10.0
            else:
                qty_in_zhang = qty  # "zhang" 或其他，保持原样

            trade_val = price * qty_in_zhang  # 价格按每张计价时，金额=价格×张数
            
            # 更健壮的日期字段获取
            day_field = getattr(fill_event, "dt", getattr(fill_event, "timestamp", None))
            if day_field is None:
                import logging
                logging.getLogger(__name__).warning("FillEvent missing datetime; trade not counted.")
                return
            
            day = pd.Timestamp(day_field).normalize()
            self.traded_value_by_day[day] = self.traded_value_by_day.get(day, 0.0) + float(trade_val)
            
        except Exception as e:
            # 避免 self.logger 可能不存在
            import logging
            logging.getLogger(__name__).warning(f"record_trade failed: {e}")
            pass


def _compute_metrics_from_nav(nav_history: List[Tuple[pd.Timestamp, float]], traded_value_by_day: Dict[pd.Timestamp, float], initial_capital: float = 1_000_000.0) -> Dict[str, float]:
    if not nav_history or len(nav_history) < 2:
        return {"calmar": 0.0, "sortino": 0.0, "sharpe": 0.0, "max_dd": 1.0, "turnover": 0.0, "ann_ret": 0.0}

    nav_df = pd.DataFrame(nav_history, columns=["datetime", "nav"]).set_index("datetime").sort_index()
    rets = nav_df["nav"].pct_change().dropna()

    # —— 关键：用净值曲线算 MDD —— #
    # 使用更新后的 perf_metrics.max_drawdown 函数，直接传入净值曲线
    max_dd = abs(float(perf_metrics.max_drawdown(nav_df["nav"].values, is_nav=True)))
    
    # 其他指标
    sortino = float(perf_metrics.sortino_ratio(rets.values, 0.0, 252, True))
    sharpe = float(perf_metrics.sharpe_ratio(rets.values, 0.0, 252, True))
    ann_ret = float(perf_metrics.annual_return(rets.values, 252))
    
    # —— Calmar 口径统一：自己算，确保与同一条净值的回撤对齐 —— #
    calmar = ann_ret / max_dd if max_dd > 1e-12 else 0.0

    # 换手率估算（年化）：总成交额 / 平均权益 / (天数/252)
    # 用真实交易日数，而不是估算
    trading_days = len(nav_df.index.unique())
    
    # —— Turnover 分母统一用绝对权益 —— #
    # 如果 nav 是净值（从1.x起跳），把它转成绝对权益再取均值
    nav_series = nav_df["nav"]
    if nav_series.max() < 20:   # 粗判：小于20基本不可能是"万元级"绝对权益
        abs_equity = nav_series * initial_capital
    else:
        abs_equity = nav_series
    avg_equity = float(abs_equity.mean())
    
    total_traded = float(sum(traded_value_by_day.get(d, 0.0) for d in nav_df.index.normalize().unique()))
    turnover = 0.0
    if avg_equity > 0:
        turnover = (total_traded / avg_equity) * (252.0 / max(1, trading_days))

    return {
        "calmar": max(0.0, calmar) if np.isfinite(calmar) else 0.0,
        "sortino": max(0.0, sortino) if np.isfinite(sortino) else 0.0,
        "sharpe": max(0.0, sharpe) if np.isfinite(sharpe) else 0.0,
        "max_dd": max_dd if np.isfinite(max_dd) else 1.0,
        "turnover": max(0.0, turnover) if np.isfinite(turnover) else 0.0,
        "ann_ret": max(0.0, ann_ret) if np.isfinite(ann_ret) else 0.0,
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
    # 【重要】quantity_unit_hint 必须与你的撮合层数量单位一致！
    # - 如果 SimpleBroker.execute_signals 返回的 quantity 是"张"，用 "zhang"
    # - 如果 SimpleBroker.execute_signals 返回的 quantity 是"手"(1手=10张)，用 "hand"
    broker = TrackingBroker(slippage_bps=2, max_pct_vol=0.15, impact_coeff=0.0005, quantity_unit_hint="zhang")

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

    # 尝试获取策略遥测数据（用于诊断参数有效性）
    telemetry = {}
    if hasattr(strategy, 'telemetry'):
        telemetry = {
            "hit_premium": strategy.telemetry.get("hit_premium", None),
            "hit_volcap": strategy.telemetry.get("hit_volcap", None),
            "hit_extreme": strategy.telemetry.get("hit_extreme", None),
            "n_trades": strategy.telemetry.get("n_trades", None),
        }

    # 返回增强数据，支持拼接计分
    result = {
        **metrics,
        **telemetry,  # 包含遥测数据
        "nav_history": portfolio.nav_history,   # List[(datetime, nav)]
        "traded_value_by_day": broker.traded_value_by_day
    }

    # 清理
    del engine, data_handler, strategy, portfolio, broker, reporter
    gc.collect()

    return result


# ============== 参数规范化与去重剪枝 ==============
def _normalize_params_for_dedup(p: dict) -> dict:
    """规范化参数，只保留有效参数组合用于去重判断"""
    p = dict(p)
    rot = p.get("rotation")
    day_w = p.pop("rotation_day_weekly", None)
    day_m = p.pop("rotation_day_monthly", None)
    # 根据rotation类型，只保留有效的rotation_day
    p["rotation_day"] = day_w if rot == "weekly" else day_m

    # 浮点参数按搜索步长取整，避免 129.999999 vs 130.0 被当成不同
    def r(x, step=0.000001):
        return None if x is None else round(float(x)/step)*step
    
    # 【重要】新增浮点超参时，必须在此列表中加入对应步长！
    float_params_steps = [
        ("premium_low", 2.5), ("premium_high", 2.5),
        ("extreme_px_gate", 2.5), ("extreme_prem_gate", 2.5), 
        ("max_price", 2.5), ("min_balance_billion", 0.5),
        ("max_realized_vol_20d", 2.5),
        # 如果后续新增了其他 suggest_float 参数，请在此处添加
    ]
    
    for k, step in float_params_steps:
        if k in p: 
            p[k] = r(p[k], step)
    return p


def _is_duplicate_trial(trial: optuna.Trial) -> bool:
    """检查当前trial的参数是否与已完成的trial重复（基于规范化参数）"""
    cur = _normalize_params_for_dedup(trial.params)
    for completed_trial in trial.study.get_trials(deepcopy=False):
        if completed_trial.state != TrialState.COMPLETE:
            continue
        # 兼容旧trial：也做一次规范化
        prev = _normalize_params_for_dedup(completed_trial.params)
        if prev == cur:
            return True
    return False


# ============== Optuna 目标函数 ==============
def objective(trial: optuna.Trial) -> float:
    # 去重剪枝：如果参数组合已经测试过，直接跳过
    if _is_duplicate_trial(trial):
        raise optuna.TrialPruned()
    
    cfg = build_config_from_trial(trial)

    oos_calmar: List[float] = []
    oos_sortino: List[float] = []
    oos_sharpe: List[float] = []
    oos_maxdd: List[float] = []
    oos_turnover: List[float] = []
    
    # 用于拼接的全局数据
    oos_nav_all: List[Tuple[pd.Timestamp, float]] = []
    oos_traded_all: Dict[pd.Timestamp, float] = {}
    
    # 遥测数据收集
    telemetry_agg = {
        "hit_premium": [],
        "hit_volcap": [],
        "hit_extreme": [],
        "n_trades": []
    }

    for step_idx, (tr_s, tr_e, te_s, te_e) in enumerate(WALK_WINDOWS, start=1):
        result = run_backtest(cfg, tr_s, tr_e, te_s, te_e)
        
        # 提取当前窗口的指标
        calmar = float(result.get("calmar", 0.0))
        sortino = float(result.get("sortino", 0.0))
        sharpe = float(result.get("sharpe", 0.0))
        max_dd = float(result.get("max_dd", 0.0))
        turnover = float(result.get("turnover", 0.0))
        
        # 收集遥测数据
        for key in telemetry_agg:
            val = result.get(key)
            if val is not None:
                telemetry_agg[key].append(val)

        step_score = _score_with_penalty(calmar, sortino, max_dd, turnover)
        trial.report(step_score, step=step_idx)
        if trial.should_prune():
            raise optuna.TrialPruned()

        oos_calmar.append(calmar)
        oos_sortino.append(sortino)
        oos_sharpe.append(sharpe)
        oos_maxdd.append(max_dd)
        oos_turnover.append(turnover)
        
        # 收集净值和成交数据用于拼接
        oos_nav_all.extend(result.get("nav_history", []))
        for d, v in result.get("traded_value_by_day", {}).items():
            oos_traded_all[d] = oos_traded_all.get(d, 0.0) + float(v)

    calmar_mean = sum(oos_calmar) / len(oos_calmar)
    sortino_mean = sum(oos_sortino) / len(oos_sortino)
    sharpe_mean = sum(oos_sharpe) / len(oos_sharpe)
    maxdd_mean = sum(oos_maxdd) / len(oos_maxdd)
    turnover_mean = sum(oos_turnover) / len(oos_turnover)

    # —— 拼接 OOS 净值计分：更细腻地区分路径差异 —— #
    # 用拼接后的整段 OOS 再算一次"全局"指标
    global_metrics = _compute_metrics_from_nav(oos_nav_all, oos_traded_all)
    score = _score_with_penalty(global_metrics["calmar"], global_metrics["sortino"], 
                               global_metrics["max_dd"], global_metrics["turnover"])
    
    # —— 微型 tie-breaker：在极小差距时用回撤和换手率细粒度排序 —— #
    # 不改变主目标，只在得分非常接近时微调
    tie_breaker = -0.001 * global_metrics["max_dd"] - 0.0001 * global_metrics["turnover"]
    score += tie_breaker

    trial.set_user_attr("cfg", asdict(cfg))
    trial.set_user_attr(
        "oos_metrics",
        dict(calmar=calmar_mean, sortino=sortino_mean, sharpe=sharpe_mean, max_dd=maxdd_mean, turnover=turnover_mean),
    )
    # 拼接后的全局指标
    trial.set_user_attr("oos_metrics_global", global_metrics)
    
    # 遥测数据：参数影响力诊断
    telemetry_summary = {}
    for key, values in telemetry_agg.items():
        if values:
            telemetry_summary[f"{key}_mean"] = np.mean(values)
            telemetry_summary[f"{key}_range"] = f"{min(values):.3f}-{max(values):.3f}"
    trial.set_user_attr("telemetry", telemetry_summary)
    
    # 添加遥测数据，帮助诊断参数有效性
    trial.set_user_attr("window_count", len(WALK_WINDOWS))
    trial.set_user_attr("calmar_range", f"{min(oos_calmar):.3f}-{max(oos_calmar):.3f}")
    trial.set_user_attr("sharpe_range", f"{min(oos_sharpe):.3f}-{max(oos_sharpe):.3f}")
    trial.set_user_attr("dd_range", f"{min(oos_maxdd):.3f}-{max(oos_maxdd):.3f}")
    trial.set_user_attr("turnover_range", f"{min(oos_turnover):.3f}-{max(oos_turnover):.3f}")
    
    # 统计指标一致性（帮助识别异常trial）
    trial.set_user_attr("calmar_std", f"{np.std(oos_calmar):.3f}")
    trial.set_user_attr("sharpe_std", f"{np.std(oos_sharpe):.3f}")
    trial.set_user_attr("dd_std", f"{np.std(oos_maxdd):.3f}")
    
    return score


def main() -> None:
    storage = "sqlite:///optuna_cb.sqlite3"
    study = optuna.create_study(
        study_name="cb_five_factor_singleobj",
        direction="maximize",
        storage=storage,
        load_if_exists=True,
        sampler=TPESampler(seed=42, multivariate=True, n_ei_candidates=64),
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
        # 保存窗口均值指标
        o.update(t.user_attrs.get("oos_metrics") or {})
        # 保存全局拼接指标
        o["global"] = t.user_attrs.get("oos_metrics_global") or {}
        # 保存遥测数据
        o["telemetry"] = t.user_attrs.get("telemetry") or {}
        out.append(o)
    with open("top10_trials.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()


