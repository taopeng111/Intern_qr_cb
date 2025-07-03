"""
perf_metrics.py
================
量化策略绩效评估函数集合

包含：
    - annual_return      年化收益率（几何复合）
    - sharpe_ratio       年化 Sharpe Ratio
    - sortino_ratio      年化 Sortino Ratio
    - max_drawdown       最大回撤
    - calc_metrics       汇总接口（DataFrame → 指标表）
"""

import numpy as np
import pandas as pd
from typing import Union, Dict, Any

# 类型别名
ArrayLike = Union[np.ndarray, pd.Series]

def annual_return(returns: ArrayLike, periods_per_year: int = 252) -> float:
    """
    计算年化收益率（几何复合）。
    
    参数：
    - returns: 单期收益率序列 (array-like)
    - periods_per_year: 每年有多少个观测期（日频=252，周频=52，月频=12）

    返回：
    - 年化复合收益率（float）
    """
    returns = np.asarray(returns)
    returns = returns[~np.isnan(returns)]
    
    if len(returns) == 0:
        return np.nan
    
    gross_total = np.prod(1 + returns)
    n_periods = len(returns)
    return gross_total ** (periods_per_year / n_periods) - 1


def sharpe_ratio(
    returns: ArrayLike,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
    annualized_rf: bool = True
) -> float:
    """
    计算年化 Sharpe Ratio。

    参数：
    - returns: 单期收益率序列 (array-like)
    - risk_free_rate: 无风险利率（默认 0.0）。如果是年化的，请设置 annualized_rf=True。
    - periods_per_year: 每年观测期数量（日频=252）
    - annualized_rf: 若为 True，则 risk_free_rate 会自动除以 periods_per_year

    返回：
    - 年化 Sharpe Ratio（float）
    """
    returns = np.asarray(returns)
    returns = returns[~np.isnan(returns)]
    
    if len(returns) == 0:
        return np.nan
    
    if annualized_rf:
        risk_free_rate /= periods_per_year

    excess_ret = returns - risk_free_rate
    mean_excess = excess_ret.mean()
    std_excess = excess_ret.std(ddof=1)

    if std_excess == 0:
        return np.nan

    return (mean_excess / std_excess) * np.sqrt(periods_per_year)

def sortino_ratio(
    returns: ArrayLike,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
    annualized_rf: bool = True,
) -> float:
    """
    计算年化 Sortino Ratio（仅使用下行波动）。
    """
    returns = np.asarray(returns)
    returns = returns[~np.isnan(returns)]
    
    if len(returns) == 0:
        return np.nan
    
    if annualized_rf:
        risk_free_rate /= periods_per_year

    excess_ret = returns - risk_free_rate
    mean_downside = np.where(excess_ret < 0.0, excess_ret, 0.0)
    std_downside = np.sqrt((mean_downside ** 2).mean())

    if std_downside == 0.0 or np.isnan(std_downside):
        return np.nan

    mean_excess = excess_ret.mean()

    return (mean_excess / std_downside) * np.sqrt(periods_per_year)

def max_drawdown(returns: ArrayLike) -> float:
    returns = np.asarray(returns)
    returns = returns[~np.isnan(returns)]
    
    if len(returns) == 0:
        return np.nan

    cum_nav = np.cumprod(1.0 + returns)    #资金净值
    running_max = np.maximum.accumulate(cum_nav)    
    drawdowns = cum_nav / running_max - 1.0
    return drawdowns.min()

def calc_metrics(
    df_returns: pd.DataFrame,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
    annualized_rf: bool = True,
) -> pd.DataFrame:
    """
    针对多策略收益率 DataFrame 计算常见绩效指标。
    
    参数
    ----
    df_returns : pd.DataFrame
        列为不同策略，行为同一频率的收益率。
    risk_free_rate : float, default 0.0
        无风险利率（年化或单期，受 annualized_rf 控制）。
    periods_per_year : int, default 252
        一年观测期数。
    annualized_rf : bool, default True
        risk_free_rate 是否为年化值。
    
    返回
    ----
    pd.DataFrame
        指标汇总表，index 为策略名，columns 为
        ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown']。
    """
    results: Dict[str, Dict[str, Any]] = {}
    for col in df_returns:
        arr = df_returns[col].values
        results[col] = {
            "annual_return": annual_return(arr, periods_per_year),
            "sharpe_ratio": sharpe_ratio(
                arr, risk_free_rate, periods_per_year, annualized_rf
            ),
            "sortino_ratio": sortino_ratio(
                arr, risk_free_rate, periods_per_year, annualized_rf
            ),
            "max_drawdown": max_drawdown(arr),
        }
    return pd.DataFrame(results).T