"""
fetch_cb_data.py — Full / Incremental dual-purpose
---------------------------------
First run: Generate data/cb_SH_full.parquet & data/cb_SZ_full.parquet
Subsequent runs: Only fetch new rows after max(date)+1 for each target and append
"""

import os, time, akshare as ak, pandas as pd
from pathlib import Path

# ------------------------------------------------------------
# 0. Parameters
# ------------------------------------------------------------
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
PARQ_ALL = DATA_DIR / "cb_all.parquet"
N_RETRY  = 3                      # Single bond retry count

# ------------------------------------------------------------
# 1. Get required code list
# ------------------------------------------------------------
spot_df  = ak.bond_zh_hs_cov_spot()
code_col = "symbol" if "symbol" in spot_df.columns else "bond_code"
codes    = spot_df[code_col].astype(str).tolist()

# ------------------------------------------------------------
# 2. Read old data (if exists), prepare "latest date" dictionary
# ------------------------------------------------------------
def load_parq(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() and path.stat().st_size else pd.DataFrame()

old_all = load_parq(PARQ_ALL)

last_date = (
    old_all.groupby("symbol")["date"].max()
           .to_dict()
) if not old_all.empty else {}

print(f"📜  Old data: {old_all.shape}, contains {len(last_date)} convertible bonds")

# ------------------------------------------------------------
# 3. Fetch function: After getting full table, only keep new rows "> last_date"
# ------------------------------------------------------------
def fetch_one(code, retry=N_RETRY):
    for _ in range(retry):
        try:
            raw = ak.bond_zh_hs_cov_daily(symbol=code)
            break
        except Exception as e:
            print(f"{code} access failed: {e}; retrying…")
            time.sleep(0.5)
    else:
        return None                        # Multiple failures

    if raw is None or raw.empty:
        return None

    # --- Unify date column ---
    for cand in ["date", "trade_date", "日期", "day", "datetime"]:
        if cand in raw.columns:
            raw = raw.rename(columns={cand: "date"})
            break
    else:
        print(f"{code} no date column, skipping")
        return None

    raw["date"]   = pd.to_datetime(raw["date"], errors="coerce")
    raw["symbol"] = code

    # --- Incremental filtering ---
    cutoff = last_date.get(code)
    if cutoff is not None:
        raw = raw[raw["date"] > cutoff]

    return raw if not raw.empty else None

# ------------------------------------------------------------
# 4. Execute fetching & aggregation
# ------------------------------------------------------------
new_frames = []
for code in codes:
    df_new = fetch_one(code)
    if df_new is not None:
        new_frames.append(df_new)

print(f"🆕  This round fetched {len(new_frames)} bonds, total {sum(len(df) for df in new_frames):,} incremental rows")

if not new_frames and old_all.empty:
    raise SystemExit("No data available, possible interface exception; try again later.")

# ------------------------------------------------------------
# 5. Merge old table + new table → deduplicate → split and write
# ------------------------------------------------------------
all_data = (
    pd.concat([old_all, *new_frames], ignore_index=True)
      .drop_duplicates(subset=["date", "symbol"])
      .assign(exchange=lambda d: d["symbol"].str[:2].str.lower())
      .sort_values(["date", "symbol"])
)

# —— Write —— #
all_data.to_parquet(PARQ_ALL, compression="zstd", index=False)

print(f"✅  Update completed! Total rows: {len(all_data):,} — Shanghai {len(all_data.query('exchange=="sh"')):,} / Shenzhen {len(all_data.query('exchange=="sz"')):,}")

# Future possible improvements:
# - Field validation: If need to ensure open/high/low/close/volume fields are complete, add assertions or try/except checks
# - Incremental updates: If need daily incremental updates, read local data first then append new data
# - Outlier handling: Handle extreme prices or missing values in subsequent analysis/backtesting
