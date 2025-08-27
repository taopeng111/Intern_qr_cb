"""
constants.py · v0.3
--------------------------------------------------------------------
Global Constants & Field Mapping
✓ Bidirectional column name mapping (Chinese ↔ English)
✓ Trading parameters: default values + exchange-specific fee rates
✓ Tax rates / forced redemption / put options and other institutional thresholds
✓ Key parameters can be overridden via environment variables
✓ Trading calendar loading tools (local CSV or tushare online)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict
from datetime import time

import pandas as pd

# ------------------------------------------------------------------
# 1. Field Mapping  —— Complete according to your CB_Data_Dictionary.txt
# ------------------------------------------------------------------
FIELD_MAP_CN2EN: Dict[str, str] = {
    "symbol": "cb_code",
    "date": "trade_date",
    "close": "close",
    "volume": "volume",
    # Add more columns as needed
    "transfer_price": "convert_price",
    "transfer_premium_ratio": "premium",
    "convert_stock_pricehq": "stk_close",
    # Static table related fields
    "SECUCODE": "cb_code",
    "CONVERT_STOCK_CODE": "stk_code",
    "TRANSFER_PRICE": "convert_price",
    "TRANSFER_PREMIUM_RATIO": "premium",
    # Underlying stock data Chinese column name mapping
    "日期": "trade_date",
    "股票代码": "stk_code",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct_change",
    "涨跌额": "price_change",
    "换手率": "turnover_rate",
    # …（add as needed）
}

FIELD_MAP_EN2CN: Dict[str, str] = {v: k for k, v in FIELD_MAP_CN2EN.items()}


def rename_columns(df: pd.DataFrame, to_en: bool = True) -> pd.DataFrame:
    """
    DataFrame column name batch Chinese⇄English translation
    Parameters
    ----------
    df    : Original DataFrame
    to_en : True=Chinese→English; False=English→Chinese
    """
    mapper = FIELD_MAP_CN2EN if to_en else FIELD_MAP_EN2CN
    return df.rename(columns=mapper)


# Convertible bond code standardization tool
_CB_PAT = re.compile(r"^(?P<ex>[a-z]{2})?(?P<code>\d{6})$", re.I)

def normalise_cb_code(raw: str) -> str:
    """
    Standardize various convertible bond code formats to "110059.SH / 128044.SZ" format (6 digits + dot + exchange suffix).
    Currently supports:
      • "sz128044" / "SZ128044"
      • "110059.SH" / "110059" (default exchange distinction by first digit)
    """
    raw = str(raw).strip().lower()
    
    # If already contains exchange suffix, process directly
    if '.' in raw:
        parts = raw.split('.')
        if len(parts) == 2:
            code, ex = parts[0], parts[1]
            if ex in ['sh', 'sz']:
                return f"{code}.{ex.upper()}"
    
    # Remove all dots before processing
    raw_clean = raw.replace('.', '')
    m = _CB_PAT.match(raw_clean)
    if not m:
        raise ValueError(f"Un-recognised CB code: {raw}")

    code, ex = m["code"], m["ex"]
    if ex in {"sz", "szse"} or code.startswith("12"):
        return f"{code}.SZ"
    # Default to Shanghai exchange
    return f"{code}.SH"


# ------------------------------------------------------------------
# 2. Trading Parameters  —— Defaults & Exchange Differences
# ------------------------------------------------------------------
# 2.1 Exchange fee rates / units
EXCHANGE_RULES: Dict[str, dict] = {
    # LOT: minimum lot size; all rates expressed as "decimals" (0.0001 = 0.01%)
    "SSE": {
        "LOT": 10,
        "COMMISSION_RATE": 0.0001,      # Transaction amount × 0.01%
        "COMMISSION_MAX": 0.0002,       # Upper limit 0.02%
        "HANDLING_FEE": 0.000001,       # Transfer fee
    },
    "SZSE": {
        "LOT": 10,
        "COMMISSION_RATE": 0.0001,
        "COMMISSION_MAX": 0.001,        # Upper limit 0.10%
        "HANDLING_FEE": 0.00004,
    },
}

# 2.2 Tax rates (nationwide unified)
STAMP_DUTY_STOCK_SELL: float = 0.0005      # Stock selling stamp duty 0.05%

# 2.3 Institutional thresholds
FORCE_REDEEM_TRIGGER: float = 1.30         # Forced redemption trigger price ≥130% of face value
FORCE_REDEEM_DAYS: int = 30                # Consecutive n trading days
PUT_TRIGGER: float = 0.70                  # Put option trigger price ≤70% of face value
PUT_DAYS: int = 30

# 2.5 Bond cash flow parameters
DEFAULT_COUPON_RATE: float = 0.02          # Default coupon rate 2%
DEFAULT_MATURITY_YEARS: int = 6            # Default maturity years 6 years
DEFAULT_REDEEM_PRICE: float = 100.0        # Default redemption price (face value)
DEFAULT_PUT_PRICE: float = 100.0           # Default put price (face value)

# 2.4 Liquidity constraint parameters
MAX_PCT_VOL = 0.15         # Single transaction not exceeding 15% of daily volume
IMPACT_COEFF = 0.0005      # Impact cost coefficient

# —— Price limits and step sizes —— #
TICK_SIZE = 0.01                              # Minimum price change for whole shares/lots

# Sector → Daily price limit (%)
PRICE_LIMIT_PCT = {
    "MAIN": 0.10,     # Shanghai-Shenzhen main board
    "STAR": 0.20,     # STAR board (688xxx)
    "CHINEXT": 0.20,  # ChiNext (3xxxxx / 30xxxx)
    "BE": 0.30,       # Beijing Exchange (8xxxxx)
    "CB": 0.20,       # Convertible bonds (11/12xxxxx)
}

def round_to_tick(price: float, tick_size: float = TICK_SIZE) -> float:
    """
    Round to nearest price by tick_size (round half up)
    Example: round_to_tick(100.005) = 100.01
    """
    # Use math.floor to implement round half up
    import math
    return math.floor(price / tick_size + 0.5) * tick_size

# --------- Auction periods ---------
OPEN_AUC_START  = time(9, 15)
OPEN_AUC_END    = time(9, 25)
CLOSE_AUC_START = time(14, 57)
CLOSE_AUC_END   = time(15, 0)      # Shanghai exchange CB deadline 15:00

# ------------------------------------------------------------------
# 3. Config: Runnable default parameters (can be overridden by env)
# ------------------------------------------------------------------
@dataclass
class Config:
    """Framework runtime level defaults"""
    LOT_SIZE: int = 10
    DEFAULT_SLIPPAGE_BPS: int = 2          # Buy +2bps / Sell −2bps
    COMMISSION_RATE: float = 0.0001
    MIN_COMMISSION: float = 5.0
    TRADING_CALENDAR: List[str] = None     # Auto-assigned after loading

    @classmethod
    def override_from_env(cls) -> None:
        """Allow overriding LOT_SIZE / COMMISSION etc. via environment variables"""
        for attr in ("LOT_SIZE", "DEFAULT_SLIPPAGE_BPS",
                     "COMMISSION_RATE", "MIN_COMMISSION"):
            if attr in os.environ:
                val_type = type(getattr(cls, attr))
                setattr(cls, attr, val_type(os.environ[attr]))


Config.override_from_env()

# ------------------------------------------------------------------
# 4. Trading Calendar Tools
# ------------------------------------------------------------------
def load_trading_calendar(
    csv_path: str | Path | None = None,
    force_reload: bool = False
) -> None:
    """
    Load trading day list to Config.TRADING_CALENDAR
    - Priority: read local CSV (single column dates, format YYYYMMDD or YYYY-MM-DD)
    - If csv_path is empty, try tushare online interface
    """
    if Config.TRADING_CALENDAR and not force_reload:
        return

    dates: List[str] = []
    if csv_path and Path(csv_path).exists():
        df = pd.read_csv(csv_path, header=None)
        dates = df.iloc[:, 0].astype(str).str.replace("-", "").tolist()
    else:
        try:
            import tushare as ts
            pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))
            df = pro.trade_cal(exchange="", start_date="19900101")
            dates = df[df["is_open"] == 1]["cal_date"].astype(str).tolist()
        except Exception:
            pass  # Online loading failed, leave empty, let caller decide whether to skip holiday checks

    Config.TRADING_CALENDAR = dates


def is_trading_day(date_str: str) -> bool:
    """
    Determine if it's a trading day
    Please ensure load_trading_calendar() has been called first
    """
    if not Config.TRADING_CALENDAR:   # If not loaded, default to trading day
        return True
    return date_str.replace("-", "") in Config.TRADING_CALENDAR 