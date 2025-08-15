# list_columns.py
from pathlib import Path
import pandas as pd

files = [
    ("cb_all", Path("data/cb_all.parquet")),
    ("stock_full", Path("data/stock_full.parquet")),
    ("cb_info_full", Path("data/cb_info_full.parquet")),
]

for name, p in files:
    print(f"\n=== {name}: {p} ===")
    if not p.exists():
        print("File not found")
        continue
    df = pd.read_parquet(p)
    print(f"shape: {df.shape}")
    print("columns:", list(df.columns))
    print("dtypes:\n", df.dtypes.to_string())