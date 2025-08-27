# run_cb_backtest.py
"""
Convertible Bond Five-Factor Enhanced Strategy — Minimal Backtest Runner

- Pulls CB daily data via AkShare (preferred) or TuShare (fallback) to build `data/cb_all.parquet`
- Wires your uploaded strategy `five_factor_enhanced_strategy.py`
- Runs a simple EOD backtest with weekly rotation, market-on-close fills, position sizing by lots
- Outputs basic performance stats and an equity curve CSV under `output/`

Usage (pick one data source):

1) AkShare (no token required)
   pip install akshare pandas pyarrow numpy
   python run_cb_backtest.py --api akshare --start 2022-01-01 --end 2025-08-15

2) TuShare (requires token; set env var TS_TOKEN first)
   pip install tushare pandas pyarrow numpy
   set TS_TOKEN=your_token_here   # Windows PowerShell: $env:TS_TOKEN="your_token"
   python run_cb_backtest.py --api tushare --start 2022-01-01 --end 2025-08-15

Notes
- The backtest is EOD, executes signals at the close on rebalance days.
- The strategy expects a parquet file at ./data/cb_all.parquet with columns at least:
  [trade_date, cb_code, close, premium] and optionally [balance].
- This runner will build that parquet for you from the selected API.
- Works out-of-the-box with your uploaded five_factor_enhanced_strategy.py
"""
from __future__ import annotations
import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------
# Light-weight framework bits to satisfy your strategy's imports
# ---------------------------------------------------------------------
@dataclass
class MarketEvent:
    dt: pd.Timestamp

@dataclass
class SignalEvent:
    dt: pd.Timestamp
    symbol: str
    action: str  # "LONG" or "EXIT"
    size: int

# Provide the constants.normalise_cb_code expected by the strategy
# (Here we simply zero-pad to 6 digits; customize if your convention differs.)
class constants:
    @staticmethod
    def normalise_cb_code(code: str) -> str:
        s = str(code).strip()
        # Keep last 6 digits if code contains exchange suffix
        return s[-6:].zfill(6)

# Make these names importable for the strategy module
import types
framework = types.SimpleNamespace(events=types.SimpleNamespace(MarketEvent=MarketEvent, SignalEvent=SignalEvent))

# Also make constants available
import constants

# ---------------------------------------------------------------------
# Import your uploaded strategy (must be placed in strategies/ subdirectory)
# ---------------------------------------------------------------------
from importlib import import_module
import sys
from pathlib import Path

# Add strategies directory to Python path
strategies_path = Path(__file__).parent / "strategies"
sys.path.insert(0, str(strategies_path))

STRATEGY_MODULE = "five_factor_enhanced_strategy"
ff = import_module(STRATEGY_MODULE)
StrategyConfig = ff.StrategyConfig
DualLowFiveFactorStrategy = ff.DualLowFiveFactorStrategy

# ---------------------------------------------------------------------
# Data adapters
# ---------------------------------------------------------------------
class DataAdapter:
    def build_cb_all(self, start: str, end: str, save_path: Path) -> pd.DataFrame:
        raise NotImplementedError

class AkShareAdapter(DataAdapter):
    def build_cb_all(self, start: str, end: str, save_path: Path) -> pd.DataFrame:
        try:
            import akshare as ak
        except Exception as e:
            raise RuntimeError("AkShare is not installed: pip install akshare") from e

        # 1) Get current CB list (code + name); fallback if endpoint changes
        try:
            df_list = ak.bond_zh_cov_spot()  # common endpoint listing CBs
            # Typical columns include: symbol, code, name, premium_rt, price, remain_size, ...
        except Exception:
            # Fallback to alternative endpoint names (AkShare API names sometimes evolve)
            df_list = ak.get_bond_zh_cov_spot()  # type: ignore

        if df_list is None or df_list.empty:
            raise RuntimeError("AkShare returned empty CB list; please check your network or AkShare version.")

        # Normalize list
        df_list = df_list.rename(columns={
            "code": "cb_code",
            "symbol": "cb_code",
            "价格": "close",
            "price": "close",
            "转股溢价率": "premium",
            "premium_rt": "premium",
            "剩余规模(亿元)": "balance",
            "remain_size": "balance",
        })
        df_list["cb_code"] = df_list["cb_code"].astype(str).str[-6:].str.zfill(6)

        # 2) For each CB, fetch daily history; build union dataframe
        rows: List[pd.DataFrame] = []
        codes = df_list["cb_code"].dropna().unique().tolist()

        for code in codes:
            # Try a few plausible daily-history endpoints
            history_df = None
            # Newer AkShare often has bond_zh_cov_daily(symbol="...", start_date=YYYYMMDD, end_date=YYYYMMDD)
            for fn in [
                "bond_zh_cov_daily",
                "bond_cov_zh_daily",
                "bond_zh_cov_his",
            ]:
                try:
                    func = getattr(ak, fn)
                    # Attempt with common parameter styles
                    try:
                        h = func(symbol=code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
                    except TypeError:
                        h = func(code)
                    if h is not None and not h.empty:
                        history_df = h
                        break
                except Exception:
                    continue

            if history_df is None or history_df.empty:
                continue

            # Normalize daily columns
            history_df = history_df.rename(columns={
                "日期": "trade_date",
                "date": "trade_date",
                "收盘": "close",
                "close": "close",
                "转股溢价率": "premium",
                "premium": "premium",
                "剩余规模(亿元)": "balance",
                "balance": "balance",
                "代码": "cb_code",
                "symbol": "cb_code",
            })
            # Add code if missing
            if "cb_code" not in history_df.columns:
                history_df["cb_code"] = code

            # Ensure types
            history_df["trade_date"] = pd.to_datetime(history_df["trade_date"])
            history_df["cb_code"] = history_df["cb_code"].astype(str).str[-6:].str.zfill(6)
            history_df = history_df[(history_df["trade_date"] >= pd.to_datetime(start)) & (history_df["trade_date"] <= pd.to_datetime(end))]

            # Premium often in percent string like "12.34%"; coerce to float percent
            if history_df["premium"].dtype == object:
                history_df["premium"] = (history_df["premium"].astype(str)
                                           .str.replace("%", "", regex=False)
                                           .str.replace(",", "", regex=False)
                                           .astype(float))
            # Balance may be missing; keep as float
            if "balance" in history_df.columns:
                history_df["balance"] = pd.to_numeric(history_df["balance"], errors="coerce")

            rows.append(history_df[["trade_date", "cb_code", "close", "premium"] + (["balance"] if "balance" in history_df.columns else [])])

        if not rows:
            raise RuntimeError("No CB history fetched from AkShare; endpoints may have changed.")

        df_all = pd.concat(rows, ignore_index=True)
        df_all = df_all.sort_values(["trade_date", "cb_code"]).reset_index(drop=True)

        save_path.parent.mkdir(parents=True, exist_ok=True)
        df_all.to_parquet(save_path)
        return df_all

class TuShareAdapter(DataAdapter):
    def build_cb_all(self, start: str, end: str, save_path: Path) -> pd.DataFrame:
        try:
            import tushare as ts
        except Exception as e:
            raise RuntimeError("TuShare is not installed: pip install tushare") from e
        token = os.environ.get("TS_TOKEN")
        if not token:
            raise RuntimeError("TuShare requires TS_TOKEN in environment.")
        ts.set_token(token)
        pro = ts.pro_api()

        # 1) Get CB basic list
        basic = pro.cb_basic(fields="ts_code,bond_short_name,list_date,maturity_date,remain_size")
        basic = basic.rename(columns={"ts_code": "cb_code", "remain_size": "balance"})
        basic["cb_code"] = basic["cb_code"].astype(str).str.split(".").str[0].str.zfill(6)

        # 2) Pull daily close/premium where available
        # TuShare has limited premium fields for CB; if missing, approximate premium as NaN.
        rows: List[pd.DataFrame] = []
        for code in basic["cb_code"].unique().tolist():
            try:
                # Fetch daily: adj close if available
                df = pro.cb_daily(ts_code=f"{code}.SH, {code}.SZ".split(",")[0], start_date=start.replace("-", ""), end_date=end.replace("-", ""))
            except Exception:
                # Try each exchange suffix
                df = pd.DataFrame()
                for suf in ["SH", "SZ"]:
                    try:
                        part = pro.cb_daily(ts_code=f"{code}.{suf}", start_date=start.replace("-", ""), end_date=end.replace("-", ""))
                        df = pd.concat([df, part], ignore_index=True)
                    except Exception:
                        continue
            if df is None or df.empty:
                continue
            df = df.rename(columns={"trade_date": "trade_date", "close": "close"})
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df["cb_code"] = code
            # Premium not generally available; keep NaN
            df["premium"] = np.nan
            rows.append(df[["trade_date", "cb_code", "close", "premium"]])

        if not rows:
            raise RuntimeError("No CB history fetched from TuShare; check permissions.")

        df_all = pd.concat(rows, ignore_index=True).sort_values(["trade_date", "cb_code"]).reset_index(drop=True)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        df_all.to_parquet(save_path)
        return df_all

# ---------------------------------------------------------------------
# Simple portfolio/broker and backtest loop
# ---------------------------------------------------------------------
@dataclass
class Position:
    qty: int = 0
    cost: float = 0.0

class Portfolio:
    def __init__(self, init_cash: float = 1_000_000.0):
        self.cash = init_cash
        self.positions: Dict[str, Position] = {}
        self.equity_curve: List[Dict] = []

    def update_mark(self, dt: pd.Timestamp, prices: pd.Series):
        equity = self.cash
        for sym, pos in self.positions.items():
            px = float(prices.get(sym, np.nan))
            if np.isfinite(px):
                equity += pos.qty * 10 * px  # 1 lot = 10 bonds; qty here is lots
        self.equity_curve.append({"date": dt, "equity": equity, "cash": self.cash, "pos_count": len(self.positions)})

    def transact(self, dt: pd.Timestamp, symbol: str, action: str, lots: int, fill_price: float):
        notional = lots * 10 * fill_price
        if action == "BUY":
            if self.cash < notional:
                return False
            pos = self.positions.setdefault(symbol, Position())
            # average cost by lots
            total_qty = pos.qty + lots
            if total_qty > 0:
                pos.cost = (pos.cost * pos.qty + fill_price * lots) / total_qty
            pos.qty = total_qty
            self.cash -= notional
            return True
        elif action == "SELL":
            pos = self.positions.get(symbol)
            if not pos or pos.qty <= 0:
                return False
            sell_lots = min(lots, pos.qty)
            pos.qty -= sell_lots
            self.cash += sell_lots * 10 * fill_price
            if pos.qty == 0:
                del self.positions[symbol]
            return True
        return False

# ---------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------

def run(api: str, start: str, end: str, init_cash: float = 1_000_000.0):
    data_path = Path("data/cb_all.parquet")

    # Build data if missing
    if not data_path.exists():
        adapter: DataAdapter
        if api.lower() == "akshare":
            adapter = AkShareAdapter()
        elif api.lower() == "tushare":
            adapter = TuShareAdapter()
        else:
            raise SystemExit("Unsupported --api; choose akshare or tushare")
        print(f"[Data] Building cb_all.parquet via {api}…")
        df_all = adapter.build_cb_all(start=start, end=end, save_path=data_path)
    else:
        df_all = pd.read_parquet(data_path)

    # Ensure required columns
    for col in ["trade_date", "cb_code", "close", "premium"]:
        if col not in df_all.columns:
            raise RuntimeError(f"Missing column in cb_all.parquet: {col}")
    if not np.issubdtype(df_all["trade_date"].dtype, np.datetime64):
        df_all["trade_date"] = pd.to_datetime(df_all["trade_date"])  # type: ignore

    # Init strategy & portfolio
    cfg = StrategyConfig()
    strat = DualLowFiveFactorStrategy(cfg)
    # Inject freshly built data directly (in case parquet path differs)
    strat.cb_data = df_all.sort_values(["trade_date", "cb_code"]).reset_index(drop=True)

    pf = Portfolio(init_cash=init_cash)

    # Iterate daily calendar
    dates = strat.cb_data["trade_date"].dt.normalize().drop_duplicates().sort_values().tolist()

    for dt in dates:
        day = strat.cb_data[strat.cb_data["trade_date"].dt.normalize() == dt].copy()
        # Mark-to-market before trading (start-of-day equity)
        px_series = day.set_index("cb_code")["close"]
        pf.update_mark(dt, px_series)

        # Generate signals (rebalance days)
        signals: List[SignalEvent] = strat.calculate_signals(MarketEvent(dt=dt))
        if not signals:
            continue

        # Execute signals at close
        for sig in signals:
            if sig.action == "EXIT":
                fill_px = float(px_series.get(sig.symbol, np.nan))
                if np.isfinite(fill_px):
                    ok = pf.transact(dt, sig.symbol, "SELL", sig.size, fill_px)
                    if ok:
                        strat.update_position(sig.symbol, sig.size, action="SELL", fill_price=fill_px, fill_dt=dt)
            elif sig.action == "LONG":
                fill_px = float(px_series.get(sig.symbol, np.nan))
                if np.isfinite(fill_px):
                    ok = pf.transact(dt, sig.symbol, "BUY", sig.size, fill_px)
                    if ok:
                        strat.update_position(sig.symbol, sig.size, action="BUY", fill_price=fill_px, fill_dt=dt)

    # Final mark
    if dates:
        last_day = strat.cb_data[strat.cb_data["trade_date"].dt.normalize() == dates[-1]].copy()
        pf.update_mark(dates[-1], last_day.set_index("cb_code")["close"])

    # Results
    eq = pd.DataFrame(pf.equity_curve)
    eq = eq.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    eq["ret"] = eq["equity"].pct_change().fillna(0.0)
    total_ret = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1.0 if len(eq) > 1 else 0.0
    ann = (1 + eq["ret"]).prod() ** (244 / max(1, len(eq))) - 1 if len(eq) > 1 else 0.0
    vol = eq["ret"].std(ddof=0) * (244 ** 0.5)
    sharpe = ann / vol if vol and np.isfinite(vol) and vol != 0 else np.nan

    out_dir = Path("output"); out_dir.mkdir(exist_ok=True)
    curve_path = out_dir / "equity_curve.csv"
    eq.to_csv(curve_path, index=False)

    print("\n=== Backtest Summary ===")
    print(f"Start: {eq['date'].iloc[0].date() if not eq.empty else 'N/A'}  End: {eq['date'].iloc[-1].date() if not eq.empty else 'N/A'}  Days: {len(eq)}")
    print(f"Final Equity: {eq['equity'].iloc[-1]:,.2f}")
    print(f"Total Return: {total_ret*100:.2f}%  Ann: {ann*100:.2f}%  Vol: {vol*100:.2f}%  Sharpe: {sharpe:.2f}")
    print(f"Equity curve saved to: {curve_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", choices=["akshare", "tushare"], default="akshare")
    ap.add_argument("--start", type=str, required=True)
    ap.add_argument("--end", type=str, required=True)
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    args = ap.parse_args()

    run(api=args.api, start=args.start, end=args.end, init_cash=args.cash)
