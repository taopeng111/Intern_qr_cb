"""
five_factor_enhanced_strategy.py
---------------------------------
Dual-low five-factor enhanced strategy (enhanced with three types of signals based on "low price + conversion premium ratio")

Feature overview:
- Risk filtering (capacity and risk control): blacklist, price thresholds, balance thresholds, volatility limits, extreme price-premium joint checks;
- Five factors:
  1) Dual-low value (price + 100×premium ratio), lower is better;
  2) Dual-low historical percentile (individual bond historical lows preferred);
  3) Implied volatility proxy (20-day historical volatility), lower is more stable;
  4) Price momentum (supports trend/reversal two modes);
  5) Balance (optional, lower is better or as configured);
- Factor standardization (z-score) then weighted sum, select bonds top-down by composite score;
- Rebalancing: default weekly (Monday), sell first (take profit/stop loss/trigger risk filter), then buy (fill to max positions, no forced "drop out of ranking" selling to reduce turnover).

Dependencies:
- data/cb_all.parquet (minimum required columns: trade_date, cb_code, close, premium; optional: balance)
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
# Configuration
# -------------------------------------------------------------
@dataclass
class StrategyConfig:
    # Basic and capacity
    max_positions: int = 100              # Maximum number of positions (capacity friendly)
    lots_per_trade: int = 10              # "Lots" per order (1 lot = 10 bonds)

    # Rebalancing rhythm
    rotation: str = "weekly"              # daily / weekly / monthly
    rotation_day_weekly: int = 4          # weekly: 0=Monday,1=Tuesday,2=Wednesday,3=Thursday,4=Friday
    rotation_day_monthly: int = 15         # monthly: day of month

    # Risk filtering (hard filtering)
    min_price: float = 70.0               # Closing price lower limit
    max_price: float = 140.0              # Closing price upper limit (too high prices tend to be unstable)
    min_balance_billion: float = 3.0      # Convertible bond balance lower limit (100M yuan), ease liquidity
    max_realized_vol_20d: float = 60.0    # 20-day historical volatility upper limit (% annualized approximation)
    premium_bounds: Tuple[float, float] = (-2.5, 52.5)  # Premium ratio range (%)
    extreme_price_premium_gate: Tuple[float, float] = (145.0, 60.0)  # Price>145 and premium>60 exclude
    blacklist: List[str] = None

    # Take profit/Stop loss
    take_profit: float = 135.0            # Price take profit
    stop_loss: float = 80.0               # Price stop loss

    # Momentum parameters
    momentum_mode: str = "reversal"       # 'trend' or 'reversal'
    momentum_lookback: int = 120          # Momentum window (trading days)

    # Historical percentile parameters
    hist_percentile_lookback: int = 252   # Historical percentile lookback window

    # Implied volatility proxy parameters
    iv_window: int = 20                   # Volatility window (trading days)

    # Factor weights (sum to 1 is good, but not enforced)
    w_dual_low: float = 0.0042
    w_hist_pct: float = 0.8488
    w_low_iv: float   = 0.1132
    w_momentum: float = 0.0331
    w_balance: float  = 0.0007              # Auto-zero if no balance column
    


    def __post_init__(self) -> None:
        if self.blacklist is None:
            self.blacklist = []


# -------------------------------------------------------------
# Strategy Implementation
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
    # Data loading and intraday data retrieval
    # ------------------------------
    def _load_data(self) -> None:
        data_path = Path(__file__).resolve().parent.parent / "data" / "cb_all.parquet"
        if data_path.exists():
            df = pd.read_parquet(data_path)
            # Unify types
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
        
        # Handle both string and datetime inputs
        if isinstance(date_str, str):
            dt = pd.to_datetime(date_str)
        else:
            dt = date_str
            
        daily = self.cb_data[self.cb_data["trade_date"].dt.normalize() == dt.normalize()].copy()
        return daily

    def _get_historical(self, symbol: str, end_date: str, days: int) -> pd.DataFrame:
        cache_key = f"{symbol}_{end_date}_{days}"
        if cache_key in self.data_cache:
            return self.data_cache[cache_key]
        if self.cb_data.empty:
            return pd.DataFrame()
            
        # Handle both string and datetime inputs
        if isinstance(end_date, str):
            end_dt = pd.to_datetime(end_date)
        else:
            end_dt = end_date
            
        hist = self.cb_data[self.cb_data["cb_code"] == symbol]
        if hist.empty:
            return pd.DataFrame()
        hist = hist[hist["trade_date"] <= end_dt].sort_values("trade_date")
        if len(hist) > days:
            hist = hist.tail(days)
        self.data_cache[cache_key] = hist
        return hist

    # ------------------------------
    # Rebalancing rhythm
    # ------------------------------
    def _should_rotate(self, current_date: str) -> bool:
        if self.last_rotation_date == current_date:
            return False
        rotation = self.config.rotation.lower()
        
        # Handle both string and datetime inputs
        if isinstance(current_date, str):
            dt = pd.to_datetime(current_date)
        else:
            dt = current_date
            
        if rotation == "daily":
            return True
        if rotation == "weekly":
            return dt.weekday() == self.config.rotation_day_weekly
        if rotation == "monthly":
            return dt.day == max(1, int(self.config.rotation_day_monthly))
        return False

    # ------------------------------
    # Risk filtering
    # ------------------------------
    def _apply_hard_filters(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        cfg = self.config

        # Basic price and premium range
        lo_prem, hi_prem = cfg.premium_bounds
        price_mask = df["close"].between(cfg.min_price, cfg.max_price, inclusive="both")
        premium_mask = df["premium"].between(lo_prem, hi_prem, inclusive="both")
        
        mask = price_mask & premium_mask

        # Balance threshold (if column exists)
        if "balance" in df.columns:
            mask &= (df["balance"].fillna(0) >= cfg.min_balance_billion)

        # Blacklist
        if cfg.blacklist:
            bl = {normalise_cb_code(x) for x in cfg.blacklist}
            mask &= ~df["cb_code"].isin(bl)

        # Extreme high price + high premium joint check
        px_gate, prem_gate = cfg.extreme_price_premium_gate
        mask &= ~((df["close"] > px_gate) & (df["premium"] > prem_gate))

        return df.loc[mask].copy()

    # ------------------------------
    # Factor calculation
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
        # Dual-low value (lower is better)
        daily["dual_low"] = daily["close"] + 100.0 * daily["premium"].astype(float)

        # Historical percentile (expanding/rolling within individual bond): current value's percentile in history, lower is better
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

            # Historical percentile: based on dual-low value
            hist = hist.copy()
            hist["dual_low"] = hist["close"] + 100.0 * hist["premium"].astype(float)
            # Use history only before the current date
            hist_only = hist[hist["trade_date"] < pd.to_datetime(date_str)]
            if hist_only.empty:
                hist_pct = np.nan
            else:
                values = hist_only["dual_low"].values
                current_val = row["dual_low"]
                rank = (values < current_val).sum()
                hist_pct = rank / max(1, len(values))  # 0~1, lower is better
            hist_pct_list.append(hist_pct)

            # Implied volatility proxy: 20-day return standard deviation × sqrt(244) (annualized approximation)
            ret = hist["close"].pct_change().dropna()
            if len(ret) >= self.config.iv_window:
                iv = ret.tail(self.config.iv_window).std(ddof=0) * math.sqrt(244)
            else:
                iv = np.nan
            iv_list.append(iv * 100 if np.isfinite(iv) else np.nan)  # Convert to percentage

            # Momentum: past N-day return
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

        # Re-apply volatility hard upper limit (if any)
        if self.config.max_realized_vol_20d is not None:
            daily = daily[(daily["iv20"].isna()) | (daily["iv20"] <= self.config.max_realized_vol_20d)].copy()

        # Standardize and combine scores (direction: dual_low↓, hist_pct↓, iv20↓, momentum based on mode)
        z_dual_low = self._zscore(-daily["dual_low"])  # Lower is better → negative
        z_hist_pct = self._zscore(-daily["hist_pct"])  # Lower is better → negative
        z_iv20 = self._zscore(-daily["iv20"])           # Lower is more stable → negative

        if self.config.momentum_mode.lower() == "reversal":
            z_mom = self._zscore(-daily["momentum"])    # Reversal: worse recent performance is better
        else:
            z_mom = self._zscore(daily["momentum"])     # Trend: better recent performance is better

        # Balance direction: generally lower balance liquidity is worse, but it might also be undervalued; default to light weight and lower is better
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
    # Sell and buy signals
    # ------------------------------
    def _generate_sell_signals(self, daily: pd.DataFrame, date_str: str) -> List[SignalEvent]:
        signals: List[SignalEvent] = []
        idx = daily.set_index("cb_code") if not daily.empty else pd.DataFrame()
        for symbol in list(self.positions.keys()):
            should_sell = False
            if not idx.empty and symbol in idx.index:
                px = float(idx.loc[symbol, "close"])  # type: ignore
                prem = float(idx.loc[symbol, "premium"])  # type: ignore
                # Take profit/Stop loss
                if px >= self.config.take_profit:
                    should_sell = True
                elif px <= self.config.stop_loss:
                    should_sell = True
                # Extreme price-premium joint check
                px_gate, prem_gate = self.config.extreme_price_premium_gate
                if (px > px_gate) and (prem > prem_gate):
                    should_sell = True
            else:
                # No market data for the day (trading halt/missing data), keep position
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
        # Exclude already held positions
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
    # External interface
    # ------------------------------
    def calculate_signals(self, market_event: MarketEvent) -> List[SignalEvent]:
        dt = market_event.dt
        date_str = dt.strftime("%Y%m%d")

        if not self._should_rotate(dt):  # Pass the datetime object directly
            # Print heartbeat even on non-rebalancing days for observation
            if not hasattr(self, "_heartbeat"):
                self._heartbeat = 0
            self._heartbeat += 1
            if self._heartbeat % 50 == 0:
                print(f"[FiveFactor] {date_str} heartbeat — no rebalance today")
            return []
        self.last_rotation_date = date_str

        # 1) Daily candidate pool snapshot + risk filter
        daily = self._get_daily_snapshot(date_str)
        if daily.empty:
            return []
        daily = self._apply_hard_filters(daily)
        if daily.empty:
            return []

        # 2) Calculate factors and score
        scored = self._compute_factor_snapshot(daily, date_str)
        if scored.empty or scored["score"].isna().all():
            return []

        # 3) Sell first, then buy
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
# Configuration loading / output
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

