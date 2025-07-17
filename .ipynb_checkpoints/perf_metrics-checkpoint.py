"""
perf_metrics.py
================
量化策略绩效评估函数集合

包含：
    - daily_return       日收益率计算
    - cum_return        累积收益率计算
    - annual_return      年化收益率（几何复合）
    - sharpe_ratio       年化 Sharpe Ratio
    - sortino_ratio      年化 Sortino Ratio
    - max_drawdown       最大回撤
    - max_drawdown_duration 最大回撤持续时间
    - calmar_ratio       Calmar Ratio（年化收益/最大回撤）
    - historical_var     历史 VaR (Value at Risk)
    - cvar               CVaR (Conditional Value at Risk)
    - trade_stats        交易胜率、盈亏比、Profit Factor
"""

import numpy as np
import pandas as pd
from typing import Union, Dict, Any, Optional
import matplotlib.pyplot as plt
import seaborn as sns
from contextlib import contextmanager

# 类型别名
ArrayLike = Union[np.ndarray, pd.Series]

def daily_return(returns: ArrayLike) -> np.ndarray:
    """
    计算日收益率序列。
    
    参数：
    - returns: 单期收益率序列 (array-like)
    
    返回：
    - 处理后的日收益率数组（numpy.ndarray）
    """
    returns = np.asarray(returns)
    returns = returns[~np.isnan(returns)]
    return returns

def cum_return(returns: ArrayLike) -> np.ndarray:
    """
    计算累积收益率序列。
    
    参数：
    - returns: 单期收益率序列 (array-like)
    
    返回：
    - 累积收益率数组（numpy.ndarray）
    """
    returns = daily_return(returns)
    
    if len(returns) == 0:
        return np.array([])
    
    return np.cumprod(1.0 + returns)

def annual_return(returns: ArrayLike, periods_per_year: int = 252) -> float:
    """
    计算年化收益率（几何复合）。
    
    参数：
    - returns: 单期收益率序列 (array-like)
    - periods_per_year: 每年有多少个观测期（日频=252，周频=52，月频=12）

    返回：
    - 年化复合收益率（float）
    """
    returns = daily_return(returns)
    
    if len(returns) == 0:
        return np.nan
    
    cum_returns = cum_return(returns)
    if len(cum_returns) == 0:
        return np.nan
    
    gross_total = cum_returns[-1]  # 最终累积收益率
    n_periods = len(returns)
    return float(gross_total ** (periods_per_year / n_periods) - 1)


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
    returns = daily_return(returns)
    
    if len(returns) == 0:
        return np.nan
    
    if annualized_rf:
        risk_free_rate /= periods_per_year

    excess_ret = returns - risk_free_rate
    mean_excess = excess_ret.mean()
    std_excess = excess_ret.std(ddof=1)

    if std_excess == 0:
        return np.nan

    return float((mean_excess / std_excess) * np.sqrt(periods_per_year))

def sortino_ratio(
    returns: ArrayLike,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
    annualized_rf: bool = True,
) -> float:
    """
    计算年化 Sortino Ratio（仅使用下行波动）。
    """
    returns = daily_return(returns)
    
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

    return float((mean_excess / std_downside) * np.sqrt(periods_per_year))

def max_drawdown(returns: ArrayLike) -> float:
    """
    计算最大回撤。
    
    参数：
    - returns: 单期收益率序列 (array-like)
    
    返回：
    - 最大回撤（float）
    """
    returns = daily_return(returns)
    
    if len(returns) == 0:
        return np.nan

    cum_nav = cum_return(returns)    # 资金净值
    running_max = np.maximum.accumulate(cum_nav)    
    drawdowns = cum_nav / running_max - 1.0
    return float(drawdowns.min())

def max_drawdown_duration(returns: ArrayLike) -> int:
    """
    计算最大回撤持续时间（从峰值到恢复的天数）。
    
    参数：
    - returns: 单期收益率序列 (array-like)
    
    返回：
    - 最大回撤持续时间（天数，int）
    """
    returns = daily_return(returns)
    
    if len(returns) == 0:
        return 0

    cum_nav = cum_return(returns)    # 资金净值
    running_max = np.maximum.accumulate(cum_nav)
    drawdowns = cum_nav / running_max - 1.0
    
    # 找到最大回撤的位置
    max_dd_idx = np.argmin(drawdowns)
    
    # 从最大回撤位置开始，找到恢复到之前峰值的时间
    peak_before_dd = running_max[max_dd_idx]
    
    # 从最大回撤位置向后查找，找到第一个恢复到峰值的位置
    recovery_idx = None
    for i in range(max_dd_idx, len(cum_nav)):
        if cum_nav[i] >= peak_before_dd:
            recovery_idx = i
            break
    
    # 如果没有恢复，返回从最大回撤到结束的天数
    if recovery_idx is None:
        return int(len(cum_nav) - max_dd_idx)
    
    return int(recovery_idx - max_dd_idx)

def calmar_ratio(nav: ArrayLike, freq: int = 252) -> float:
    """
    计算 Calmar Ratio（年化收益 / 最大回撤）。
    
    参数：
    - nav: 净值曲线序列 (array-like)，必须单调正数
    - freq: 一年包含的观测期数量（日频=252，周频=52，月频=12）
    
    返回：
    - Calmar Ratio（float）
    
    说明：
    - 结果 > 1 表示年化收益高于历史最大回撤
    - 一般 1-3 属于可接受，> 3 说明收益相对回撤非常优越
    """
    # 1. 准备阶段：清洗数据
    nav = np.asarray(nav, dtype=float)
    nav = nav[~np.isnan(nav)]  # 去掉 NaN
    
    if len(nav) <= 1:
        return np.nan
    
    # 检查净值是否为正数
    if np.any(nav <= 0):
        return np.nan
    
    # 2. 年化收益（分子部分）
    gross = nav[-1] / nav[0]  # 区间累计收益
    n = len(nav)  # 观测期数
    
    # 年化换算（几何复合）
    annual_ret = gross ** (freq / n) - 1
    
    # 3. 最大回撤（分母部分）
    running_max = np.maximum.accumulate(nav)  # 历史峰值
    drawdowns = nav / running_max - 1  # 每期回撤（结果 ≤ 0）
    max_dd = drawdowns.min()  # 取最小值（负值，如 -0.215）
    
    # 4. 计算 Calmar Ratio
    if max_dd == 0 or np.isnan(max_dd):
        return np.nan  # 避免除零
    
    calmar = annual_ret / abs(max_dd)
    
    return float(calmar)

def cumulative_return(returns: ArrayLike) -> float:
    """
    计算累计收益率。
    参数：
    - returns: 单期收益率序列 (array-like)
    返回：
    - 累计收益率（float）
    """
    returns = daily_return(returns)
    if len(returns) == 0:
        return np.nan
    return float(np.prod(1 + returns) - 1)

def annual_volatility(returns: ArrayLike, periods_per_year: int = 252) -> float:
    """
    计算年化波动率。
    参数：
    - returns: 单期收益率序列 (array-like)
    - periods_per_year: 年内观测期数
    返回：
    - 年化波动率（float）
    """
    returns = daily_return(returns)
    if len(returns) == 0:
        return np.nan
    return float(np.std(returns, ddof=1) * np.sqrt(periods_per_year))

def omega_ratio(returns: ArrayLike, risk_free_rate: float = 0.0, required_return: float = 0.0, periods_per_year: int = 252) -> float:
    """
    计算 Omega Ratio。
    参数：
    - returns: 单期收益率序列 (array-like)
    - risk_free_rate: 无风险利率（默认0.0）
    - required_return: 目标收益率（默认0.0）
    - periods_per_year: 年内观测期数
    返回：
    - Omega Ratio（float）
    """
    returns = daily_return(returns)
    if len(returns) == 0:
        return np.nan
    excess = returns - required_return
    positive = excess[excess > 0].sum()
    negative = -excess[excess < 0].sum()
    if negative == 0:
        return np.nan
    return float(positive / negative)

def tail_ratio(returns: ArrayLike) -> float:
    """
    计算 Tail Ratio（极端上涨/下跌）。
    参数：
    - returns: 单期收益率序列 (array-like)
    返回：
    - Tail Ratio（float）
    """
    returns = daily_return(returns)
    if len(returns) == 0:
        return np.nan
    upper = np.percentile(returns, 95)
    lower = abs(np.percentile(returns, 5))
    if lower == 0:
        return np.nan
    return float(upper / lower)

def plotting_context(context="notebook", font_scale=1.5, rc=None):
    if rc is None:
        rc = {}
    rc_default = {"lines.linewidth": 1.5}
    for name, val in rc_default.items():
        rc.setdefault(name, val)
    # type: ignore
    return sns.plotting_context(context=context, font_scale=font_scale, rc=rc)

def axes_style(style="darkgrid", rc=None):
    if rc is None:
        rc = {}
    rc_default = {}
    for name, val in rc_default.items():
        rc.setdefault(name, val)
    # type: ignore
    return sns.axes_style(style=style, rc=rc)

@contextmanager
def pyfolio_style():
    with plotting_context(context="notebook", font_scale=1.5), axes_style(style="darkgrid"):
        yield

def plot_nav_drawdown(df_returns, ax=None):
    nav = (1 + df_returns).cumprod()
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 4))
    else:
        fig = ax.figure
    nav.plot(ax=ax, lw=2, color='navy', label='Net Asset Value')
    running_max = nav.cummax()
    drawdown = nav / running_max - 1
    ax2 = ax.twinx()
    drawdown.plot(ax=ax2, color='red', alpha=0.3, label='Drawdown')
    ax.set_title('NAV & Drawdown')
    ax.set_ylabel('Net Asset Value')
    ax2.set_ylabel('Drawdown')
    ax.legend(loc='upper left')
    ax2.legend(loc='upper right')
    return fig, ax

def plot_return_histogram(df_returns, ax=None):
    returns = df_returns.values.flatten()
    if ax is None:
        fig, ax = plt.subplots(figsize=(6, 4))
    else:
        fig = ax.figure
    sns.histplot(returns, bins=30, kde=True, ax=ax, color='dodgerblue')
    ax.set_title('Return Histogram')
    ax.set_xlabel('Return')
    return fig, ax

def plot_monthly_returns_heatmap(df_returns, ax=None):
    # 只取第一个策略
    if isinstance(df_returns, pd.DataFrame):
        returns = df_returns.iloc[:, 0]
    else:
        returns = df_returns
    if not isinstance(returns.index, pd.DatetimeIndex):
        returns.index = pd.to_datetime(returns.index)
    monthly_ret_table = returns.resample('M').apply(lambda x: (1 + x).prod() - 1)
    monthly_ret_table = monthly_ret_table.to_frame('return')
    period_idx = monthly_ret_table.index.to_period('M')
    monthly_ret_table['Year'] = period_idx.to_timestamp().year.astype(str)
    monthly_ret_table['Month'] = period_idx.to_timestamp().month.astype(str)
    heatmap_data = monthly_ret_table.pivot(index='Year', columns='Month', values='return')
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 4))
    else:
        fig = ax.figure
    sns.heatmap(heatmap_data * 100, annot=True, fmt='.1f', cmap=plt.get_cmap('RdYlGn'), center=0, ax=ax, cbar=False, annot_kws={"size": 9})
    ax.set_title('Monthly Returns (%)')
    ax.set_xlabel('Month')
    ax.set_ylabel('Year')
    return fig, ax

def plot_trade_stats(trade_pnl_list, ax=None):
    pnl = np.asarray(trade_pnl_list, dtype=float)
    pnl = pnl[~np.isnan(pnl)]
    if ax is None:
        fig, ax = plt.subplots(figsize=(6, 4))
    else:
        fig = ax.figure
    sns.histplot(pnl, bins=30, kde=True, ax=ax, color='seagreen')
    ax.set_title('Trade PnL Distribution')
    ax.set_xlabel('Trade PnL')
    return fig, ax

def plot_tearsheet(df_returns, trade_pnl_dict=None):
    with pyfolio_style():
        # 1. NAV & Drawdown
        plot_nav_drawdown(df_returns)
        plt.show()
        # 2. Return Histogram
        plot_return_histogram(df_returns)
        plt.show()
        # 3. Monthly Heatmap
        if isinstance(df_returns.index, pd.DatetimeIndex):
            plot_monthly_returns_heatmap(df_returns)
            plt.show()
        # 4. Trade Stats
        if trade_pnl_dict is not None:
            for name, pnl in trade_pnl_dict.items():
                plot_trade_stats(pnl)
                plt.title(f'Trade PnL Distribution: {name}')
                plt.show()

def calc_metrics(
    df_returns: pd.DataFrame,
    trade_pnl_dict: Optional[dict] = None,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
    annualized_rf: bool = True,
    picture: bool = False,
) -> pd.DataFrame:
    """
    汇总所有绩效与交易级指标，输出顺序与行业标准一致。
    参数
    ----
    df_returns : pd.DataFrame
        列为不同策略，行为同一频率的收益率。
    trade_pnl_dict : dict, optional
        {策略名: trade_pnl_list}
    risk_free_rate : float, default 0.0
        无风险利率（年化或单期，受 annualized_rf 控制）。
    periods_per_year : int, default 252
        一年观测期数。
    annualized_rf : bool, default True
        risk_free_rate 是否为年化值。
    picture : bool, default False
        是否输出图表。
    返回
    ----
    pd.DataFrame
        指标汇总表，index 为策略名，columns 顺序为：
        ['cumulative_return', 'annual_return', 'annual_volatility', 'sharpe_ratio', 'sortino_ratio',
         'max_drawdown', 'max_drawdown_duration', 'calmar_ratio', 'omega_ratio',
         'var_95', 'cvar_95', 'tail_ratio', 'win_rate', 'payoff_ratio', 'profit_factor']
    """
    results: Dict[str, Dict[str, Any]] = {}
    for col in df_returns:
        arr = np.asarray(df_returns[col].dropna())
        trade_pnls = None
        if trade_pnl_dict is not None and col in trade_pnl_dict:
            trade_pnls = trade_pnl_dict[col]
            trade_stats_dict = trade_stats(trade_pnls)
        else:
            trade_stats_dict = {'win_rate': np.nan, 'payoff_ratio': np.nan, 'profit_factor': np.nan}
        results[col] = {
            "cumulative_return": cumulative_return(arr),
            "annual_return": annual_return(arr, periods_per_year),
            "annual_volatility": annual_volatility(arr, periods_per_year),
            "sharpe_ratio": sharpe_ratio(arr, risk_free_rate, periods_per_year, annualized_rf),
            "sortino_ratio": sortino_ratio(arr, risk_free_rate, periods_per_year, annualized_rf),
            "max_drawdown": max_drawdown(arr),
            "max_drawdown_duration": max_drawdown_duration(arr),
            "calmar_ratio": calmar_ratio(cum_return(arr), periods_per_year),
            "omega_ratio": omega_ratio(arr, risk_free_rate, 0.0, periods_per_year),
            "var_95": historical_var(arr, level=0.95),
            "cvar_95": cvar(arr, level=0.95),
            "tail_ratio": tail_ratio(arr),
            "win_rate": trade_stats_dict['win_rate'],
            "payoff_ratio": trade_stats_dict['payoff_ratio'],
            "profit_factor": trade_stats_dict['profit_factor'],
        }
    if picture:
        plot_tearsheet(df_returns, trade_pnl_dict)
    return pd.DataFrame(results).T

def trade_stats(trade_pnl_list) -> dict:
    """
    计算交易级别的胜率、盈亏比、Profit Factor。

    参数
    ----
    trade_pnl_list : list/np.ndarray/pd.Series
        每笔平仓后的净PnL（正为盈利，负为亏损，0可忽略）

    返回
    ----
    dict
        {'win_rate': ..., 'payoff_ratio': ..., 'profit_factor': ...}
    """
    pnl = np.asarray(trade_pnl_list, dtype=float)
    pnl = pnl[~np.isnan(pnl)]
    pnl = pnl[pnl != 0]  # 可选：忽略0盈亏

    total_trades = len(pnl)
    if total_trades == 0:
        return {'win_rate': np.nan, 'payoff_ratio': np.nan, 'profit_factor': np.nan}

    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]

    win_rate = len(winners) / total_trades if total_trades > 0 else np.nan
    payoff_ratio = winners.mean() / abs(losers.mean()) if len(losers) > 0 else np.nan
    profit_factor = winners.sum() / abs(losers.sum()) if len(losers) > 0 else np.nan

    return {
        'win_rate': win_rate,
        'payoff_ratio': payoff_ratio,
        'profit_factor': profit_factor,
    }

def historical_var(returns: ArrayLike, level: float = 0.95) -> float:
    """
    计算历史 VaR (Value at Risk)。
    
    参数：
    - returns: 收益率序列 (array-like)
    - level: 置信水平，默认 0.95 (95%)
    
    返回：
    - 历史 VaR（float）
    """
    returns = daily_return(returns)
    
    if len(returns) == 0:
        return np.nan
    
    # 计算分位数，VaR 是负的分位数
    var = np.percentile(returns, (1 - level) * 100)
    return float(var)

def cvar(returns: ArrayLike, level: float = 0.95) -> float:
    """
    计算 CVaR (Conditional Value at Risk) / Expected Shortfall。
    
    参数：
    - returns: 收益率序列 (array-like)
    - level: 置信水平，默认 0.95 (95%)
    
    返回：
    - CVaR（float）
    """
    returns = daily_return(returns)
    
    if len(returns) == 0:
        return np.nan
    
    # 计算 VaR
    var = historical_var(returns, level)
    
    # CVaR 是超过 VaR 的损失的平均值
    tail_losses = returns[returns <= var]
    
    if len(tail_losses) == 0:
        return np.nan
    
    cvar_value = tail_losses.mean()
    return float(cvar_value)