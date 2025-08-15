"""
five_factor_enhanced_strategy.py
---------------------------------
双低五因子增强策略（在“低价 + 转股溢价率”的双低基础上增强三类信号）

特性概览：
- 雷禁过滤（容量与风控）：黑名单、价格阈值、余额阈值、波动率上限、极端价-溢价联检；
- 五因子：
  1) 双低值（价格 + 100×溢价率），越低越好；
  2) 双低历史分位（个券历史低位更优）；
  3) 隐含波动率代理（20日历史波动率），越低越稳；
  4) 价格动量（支持趋势/反转两种模式）；
  5) 余额（可选，越低越优或按配置）；
- 因子标准化（z-score）后加权求和，按综合分自上而下选券；
- 调仓：默认周度（周一），先卖（止盈/止损/触发雷禁），再买（补至最大持仓数，不强制“跌出排名”卖出以减少换手）。

依赖：
- data/cb_all.parquet（最少需要列：trade_date, cb_code, close, premium；可选：balance）
- framework.events.MarketEvent / SignalEvent
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import json
import math
import pandas as pd
import numpy as np

from framework.events import MarketEvent, SignalEvent
from constants import normalise_cb_code


# -------------------------------------------------------------
# 配置
# -------------------------------------------------------------
@dataclass
class StrategyConfig:
    # 基础与容量
    max_positions: int = 200              # 最大持仓数（容量友好）
    lots_per_trade: int = 1               # 每次下单“手数”（1 手 = 10 张）

    # 调仓节奏
    rotation: str = "weekly"              # daily / weekly / monthly
    rotation_day: int = 0                 # weekly: 0=周一；monthly: 日号

    # 雷禁过滤（硬过滤）
    min_price: float = 90.0               # 收盘价下限
    max_price: float = 140.0              # 收盘价上限（过高价容易不稳）
    min_balance_billion: float = 2.0      # 转债余额下限（亿元），缓解流动性
    max_realized_vol_20d: float = 60.0    # 20日历史波动率上限（%年化近似）
    premium_bounds: Tuple[float, float] = (-10.0, 50.0)  # 溢价率区间（%）
    extreme_price_premium_gate: Tuple[float, float] = (150.0, 60.0)  # 价>150且溢价>60 剔除
    blacklist: List[str] = None

    # 止盈/止损
    take_profit: float = 125.0            # 价格止盈
    stop_loss: float = 90.0               # 价格止损

    # 动量参数
    momentum_mode: str = "trend"          # 'trend' or 'reversal'
    momentum_lookback: int = 60           # 动量窗口（交易日）

    # 历史分位参数
    hist_percentile_lookback: int = 252   # 历史分位回溯窗口

    # 隐波代理参数
    iv_window: int = 20                   # 波动率窗口（交易日）

    # 因子权重（和为1较好，但不强制）
    w_dual_low: float = 0.35
    w_hist_pct: float = 0.20
    w_low_iv: float   = 0.20
    w_momentum: float = 0.20
    w_balance: float  = 0.05              # 若无 balance 列则自动归零

    def __post_init__(self) -> None:
        if self.blacklist is None:
            self.blacklist = []


# -------------------------------------------------------------
# 策略实现
# -------------------------------------------------------------
class DualLowFiveFactorStrategy:
    def __init__(self, config: StrategyConfig | None = None):
        self.config = config or StrategyConfig()
        self.positions: Dict[str, Dict] = {}
        self.data_cache: Dict[str, pd.DataFrame] = {}
        self.last_rotation_date: Optional[str] = None

        self.cb_data: pd.DataFrame = pd.DataFrame()
        self._load_data()

    # ------------------------------
    # 数据加载与日内取数
    # ------------------------------
    def _load_data(self) -> None:
        data_path = Path(__file__).resolve().parent.parent / "data" / "cb_all.parquet"
        if data_path.exists():
            df = pd.read_parquet(data_path)
            # 统一类型
            if not np.issubdtype(df["trade_date"].dtype, np.datetime64):
                df["trade_date"] = pd.to_datetime(df["trade_date"])  # type: ignore
            df["cb_code"] = df["cb_code"].astype(str)
            self.cb_data = df.sort_values(["trade_date", "cb_code"]).reset_index(drop=True)
            print(f"[FiveFactor] Loaded cb_data: {self.cb_data.shape}")
        else:
            print("[FiveFactor] cb_all.parquet not found; strategy will rely on injected data if any.")
            self.cb_data = pd.DataFrame()

    def _get_daily_snapshot(self, date_str: str) -> pd.DataFrame:
        if self.cb_data.empty:
            return pd.DataFrame()
        dt = pd.to_datetime(date_str)
        daily = self.cb_data[self.cb_data["trade_date"].dt.normalize() == dt.normalize()].copy()
        return daily

    def _get_historical(self, symbol: str, end_date: str, days: int) -> pd.DataFrame:
        cache_key = f"{symbol}_{end_date}_{days}"
        if cache_key in self.data_cache:
            return self.data_cache[cache_key]
        if self.cb_data.empty:
            return pd.DataFrame()
        end_dt = pd.to_datetime(end_date)
        hist = self.cb_data[self.cb_data["cb_code"] == symbol]
        if hist.empty:
            return pd.DataFrame()
        hist = hist[hist["trade_date"] <= end_dt].sort_values("trade_date")
        if len(hist) > days:
            hist = hist.tail(days)
        self.data_cache[cache_key] = hist
        return hist

    # ------------------------------
    # 调仓节奏
    # ------------------------------
    def _should_rotate(self, current_date: str) -> bool:
        if self.last_rotation_date == current_date:
            return False
        rotation = self.config.rotation.lower()
        dt = pd.to_datetime(current_date)
        if rotation == "daily":
            return True
        if rotation == "weekly":
            return dt.weekday() == self.config.rotation_day
        if rotation == "monthly":
            return dt.day == max(1, int(self.config.rotation_day))
        return False

    # ------------------------------
    # 雷禁过滤
    # ------------------------------
    def _apply_hard_filters(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        cfg = self.config

        # 基础价格与溢价区间
        lo_prem, hi_prem = cfg.premium_bounds
        mask = (
            (df["close"].between(cfg.min_price, cfg.max_price, inclusive="both")) &
            (df["premium"].between(lo_prem, hi_prem, inclusive="both"))
        )

        # 余额阈值（若有该列）
        if "balance" in df.columns:
            mask &= (df["balance"].fillna(0) >= cfg.min_balance_billion)

        # 黑名单
        if cfg.blacklist:
            bl = {normalise_cb_code(x) for x in cfg.blacklist}
            mask &= ~df["cb_code"].isin(bl)

        # 极端高价 + 高溢价联检
        px_gate, prem_gate = cfg.extreme_price_premium_gate
        mask &= ~((df["close"] > px_gate) & (df["premium"] > prem_gate))

        return df.loc[mask].copy()

    # ------------------------------
    # 因子计算
    # ------------------------------
    @staticmethod
    def _zscore(series: pd.Series) -> pd.Series:
        s = series.astype(float)
        mu = s.mean()
        sd = s.std(ddof=0)
        if not np.isfinite(sd) or sd == 0:
            return pd.Series(np.zeros(len(s)), index=s.index)
        return (s - mu) / sd

    def _compute_factor_snapshot(self, daily: pd.DataFrame, date_str: str) -> pd.DataFrame:
        if daily.empty:
            return daily

        daily = daily.copy()
        # 双低值（越低越好）
        daily["dual_low"] = daily["close"] + 100.0 * daily["premium"].astype(float)

        # 历史分位（个券内 expanding/rolling 近似）：当前值在历史中的百分位，越低越好
        hist_pct_list: List[float] = []
        iv_list: List[float] = []
        mom_list: List[float] = []

        for _, row in daily.iterrows():
            sym = row["cb_code"]
            hist = self._get_historical(sym, date_str, days=max(self.config.hist_percentile_lookback, self.config.momentum_lookback, self.config.iv_window) + 5)
            if hist.empty:
                hist_pct_list.append(np.nan)
                iv_list.append(np.nan)
                mom_list.append(np.nan)
                continue

            # 历史分位：基于双低值
            hist = hist.copy()
            hist["dual_low"] = hist["close"] + 100.0 * hist["premium"].astype(float)
            # 仅用当前之前的历史
            hist_only = hist[hist["trade_date"] < pd.to_datetime(date_str)]
            if hist_only.empty:
                hist_pct = np.nan
            else:
                values = hist_only["dual_low"].values
                current_val = row["dual_low"]
                rank = (values < current_val).sum()
                hist_pct = rank / max(1, len(values))  # 0~1，越小越好
            hist_pct_list.append(hist_pct)

            # 隐波代理：20日收益率标准差 × sqrt(244)（年化近似）
            ret = hist["close"].pct_change().dropna()
            if len(ret) >= self.config.iv_window:
                iv = ret.tail(self.config.iv_window).std(ddof=0) * math.sqrt(244)
            else:
                iv = np.nan
            iv_list.append(iv * 100 if np.isfinite(iv) else np.nan)  # 转百分比

            # 动量：过去 N 日收益率
            if len(hist) >= self.config.momentum_lookback + 1:
                p0 = hist["close"].iloc[-self.config.momentum_lookback - 1]
                p1 = hist["close"].iloc[-1]
                mom = (p1 / p0 - 1.0) * 100.0
            else:
                mom = np.nan
            mom_list.append(mom)

        daily["hist_pct"] = pd.Series(hist_pct_list, index=daily.index)
        daily["iv20"] = pd.Series(iv_list, index=daily.index)
        daily["momentum"] = pd.Series(mom_list, index=daily.index)

        # 再应用波动率硬性上限（若有）
        if self.config.max_realized_vol_20d is not None:
            daily = daily[(daily["iv20"].isna()) | (daily["iv20"] <= self.config.max_realized_vol_20d)].copy()

        # 标准化并合成分数（方向：dual_low↓、hist_pct↓、iv20↓、momentum根据模式）
        z_dual_low = self._zscore(-daily["dual_low"])  # 低更好 → 取负
        z_hist_pct = self._zscore(-daily["hist_pct"])  # 低更好 → 取负
        z_iv20 = self._zscore(-daily["iv20"])           # 低更稳 → 取负

        if self.config.momentum_mode.lower() == "reversal":
            z_mom = self._zscore(-daily["momentum"])    # 反转：近期差更好
        else:
            z_mom = self._zscore(daily["momentum"])     # 趋势：近期强更好

        # 余额方向：一般低余额流动性更差，但也可能估值更便宜；默认轻权重且低更优
        if "balance" in daily.columns:
            z_bal = self._zscore(-daily["balance"].fillna(daily["balance"].median()))
            w_bal = self.config.w_balance
        else:
            z_bal = pd.Series(0.0, index=daily.index)
            w_bal = 0.0

        cfg = self.config
        daily["score"] = (
            cfg.w_dual_low * z_dual_low +
            cfg.w_hist_pct * z_hist_pct +
            cfg.w_low_iv   * z_iv20 +
            cfg.w_momentum * z_mom +
            w_bal          * z_bal
        )

        return daily

    # ------------------------------
    # 卖出与买入信号
    # ------------------------------
    def _generate_sell_signals(self, daily: pd.DataFrame, date_str: str) -> List[SignalEvent]:
        signals: List[SignalEvent] = []
        idx = daily.set_index("cb_code") if not daily.empty else pd.DataFrame()
        for symbol in list(self.positions.keys()):
            should_sell = False
            if not idx.empty and symbol in idx.index:
                px = float(idx.loc[symbol, "close"])  # type: ignore
                prem = float(idx.loc[symbol, "premium"])  # type: ignore
                # 止盈/止损
                if px >= self.config.take_profit:
                    should_sell = True
                elif px <= self.config.stop_loss:
                    should_sell = True
                # 极端价-溢价联检
                px_gate, prem_gate = self.config.extreme_price_premium_gate
                if (px > px_gate) and (prem > prem_gate):
                    should_sell = True
            else:
                # 当日无行情（停牌/缺数），保留持仓
                should_sell = False

            if should_sell:
                qty = self.positions[symbol]["quantity"]
                if qty > 0:
                    signals.append(SignalEvent(
                        dt=pd.Timestamp(date_str),
                        symbol=symbol,
                        action="EXIT",
                        size=qty,
                    ))
        return signals

    def _generate_buy_signals(self, daily_scored: pd.DataFrame, date_str: str) -> List[SignalEvent]:
        signals: List[SignalEvent] = []
        # 去掉已持仓的
        candidates = daily_scored[~daily_scored["cb_code"].isin(self.positions.keys())]
        if candidates.empty:
            return signals
        candidates = candidates.sort_values("score", ascending=False)

        available = max(0, self.config.max_positions - len(self.positions))
        if available <= 0:
            return signals

        for _, row in candidates.head(available).iterrows():
            signals.append(SignalEvent(
                dt=pd.Timestamp(date_str),
                symbol=row["cb_code"],
                action="LONG",
                size=int(self.config.lots_per_trade),
            ))
        return signals

    # ------------------------------
    # 对外接口
    # ------------------------------
    def calculate_signals(self, market_event: MarketEvent) -> List[SignalEvent]:
        dt = market_event.dt
        date_str = dt.strftime("%Y%m%d")

        if not self._should_rotate(date_str):
            # 非轮动日也打印心跳，便于观察
            if not hasattr(self, "_heartbeat"):
                self._heartbeat = 0
            self._heartbeat += 1
            if self._heartbeat % 50 == 0:
                print(f"[FiveFactor] {date_str} heartbeat — no rebalance today")
            return []
        self.last_rotation_date = date_str

        # 1) 当日候选池快照 + 雷禁
        daily = self._get_daily_snapshot(date_str)
        if daily.empty:
            return []
        daily = self._apply_hard_filters(daily)
        if daily.empty:
            return []

        # 2) 计算因子并打分
        scored = self._compute_factor_snapshot(daily, date_str)
        if scored.empty or scored["score"].isna().all():
            return []

        # 3) 先卖后买
        signals: List[SignalEvent] = []
        sell_sigs = self._generate_sell_signals(scored, date_str)
        buy_sigs = self._generate_buy_signals(scored, date_str)
        signals += sell_sigs
        signals += buy_sigs

        print(f"[FiveFactor] {date_str} signals — sell: {len(sell_sigs)}, buy: {len(buy_sigs)}, pos: {len(self.positions)}")
        if buy_sigs[:3]:
            preview = ", ".join([s.symbol for s in buy_sigs[:3]])
            print(f"  top buys: {preview} ...")
        return signals

    def update_position(self, symbol: str, quantity: int, action: str, fill_price: float | None = None, fill_dt: pd.Timestamp | None = None) -> None:
        if action == "BUY":
            pos = self.positions.setdefault(symbol, {"quantity": 0, "buy_price": 0.0, "buy_date": ""})
            pos["quantity"] += int(quantity)
            if fill_price is not None:
                pos["buy_price"] = float(fill_price)
            if fill_dt is not None:
                pos["buy_date"] = fill_dt.strftime("%Y%m%d")
        elif action == "SELL":
            if symbol in self.positions:
                self.positions[symbol]["quantity"] -= int(quantity)
                if self.positions[symbol]["quantity"] <= 0:
                    del self.positions[symbol]

    def get_strategy_info(self) -> Dict:
        return {
            "strategy_name": self.__class__.__name__,
            "config": self.config.__dict__,
            "current_positions": len(self.positions),
            "max_positions": self.config.max_positions,
        }


# -------------------------------------------------------------
# 配置加载 / 输出
# -------------------------------------------------------------
def load_strategy_config(config_path: str | None = None) -> StrategyConfig:
    if config_path is None:
        config_path = Path(__file__).with_name("five_factor_config.json")
    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return StrategyConfig(**d)
    cfg = StrategyConfig()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg.__dict__, f, indent=2, ensure_ascii=False)
    print(f"[FiveFactor] Created default config at: {path}")
    return cfg


if __name__ == "__main__":
    cfg = load_strategy_config()
    strat = DualLowFiveFactorStrategy(cfg)
    print("Initialized:", strat.get_strategy_info())

