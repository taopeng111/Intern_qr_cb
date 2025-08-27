# data/prepare_cb_all.py
"""
Convertible bond data preprocessing script
Merge daily data (cb_all.parquet) and static information (cb_info_full.parquet) into framework-required format
"""
import sys
from pathlib import Path

# Add project root directory to Python path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from constants import rename_columns, normalise_cb_code

parquet_cb   = ROOT / "data" / "cb_all.parquet"
parquet_info = ROOT / "data" / "cb_info_full.parquet"

print("🔍 Starting convertible bond data processing...")

# 1) Read daily data
print("📊 Reading daily data...")
df_cb = pd.read_parquet(parquet_cb)
print(f"   Original data shape: {df_cb.shape}")
print(f"   Original column names: {df_cb.columns.tolist()}")

# 2) Rename columns to English (rename first to avoid conflicts later)
print("📝 Renaming columns to English...")
df_cb = rename_columns(df_cb, to_en=True)
print(f"   Column names after renaming: {df_cb.columns.tolist()}")

# 3) Standardize code format
print("🔄 Standardizing bond code format...")
df_cb["cb_code"] = df_cb["cb_code"].apply(normalise_cb_code)
print(f"   Code examples: {df_cb['cb_code'].head().tolist()}")

# 4) Read static information table
print("📋 Reading static information table...")
df_info = pd.read_parquet(parquet_info, columns=[
    "SECURITY_CODE",
    "CONVERT_STOCK_CODE", 
    "TRANSFER_PRICE",
    "TRANSFER_PREMIUM_RATIO",
    "ACTUAL_ISSUE_SCALE"  # Add convertible bond balance data
])
print(f"   Static table shape: {df_info.shape}")

# 5) Standardize static table codes
print("🔄 Standardizing static table codes...")
df_info["cb_code"] = df_info["SECURITY_CODE"].apply(normalise_cb_code)
df_info["stk_code"] = df_info["CONVERT_STOCK_CODE"].apply(
    lambda x: f"{str(x).zfill(6)}.SH" if str(x).startswith(("60","68")) else f"{str(x).zfill(6)}.SZ"
)
print(f"   Bond code examples: {df_info['cb_code'].head().tolist()}")
print(f"   Stock code examples: {df_info['stk_code'].head().tolist()}")

# 6) Prepare merge data
print("🔗 Preparing merge data...")
# Select needed columns from static table and rename
df_info_for_merge = df_info[["cb_code", "stk_code", "TRANSFER_PRICE", "TRANSFER_PREMIUM_RATIO", "ACTUAL_ISSUE_SCALE"]].copy()
df_info_for_merge = df_info_for_merge.rename(columns={
    "TRANSFER_PRICE": "convert_price",
    "TRANSFER_PREMIUM_RATIO": "premium",
    "ACTUAL_ISSUE_SCALE": "balance",  # Convertible bond balance (100 million yuan)
})

# 7) Clean duplicate columns in daily data
print("🧹 Cleaning duplicate columns...")
columns_to_drop = ['stk_code', 'convert_price', 'premium']
for col in columns_to_drop:
    if col in df_cb.columns:
        df_cb = df_cb.drop(columns=[col])
        print(f"   Deleted duplicate column: {col}")

# 8) Merge data
print("🔗 Merging daily and static data...")
df_merged = df_cb.merge(
    df_info_for_merge,
    on="cb_code",
    how="left"
)

print(f"   Shape after merge: {df_merged.shape}")
print(f"   Final column names: {df_merged.columns.tolist()}")

# 9) Check key fields
required_fields = ["cb_code", "trade_date", "close", "volume", "premium", "balance"]
missing_fields = [field for field in required_fields if field not in df_merged.columns]
if missing_fields:
    print(f"❌ Missing key fields: {missing_fields}")
else:
    print("✅ All key fields exist")

# 10) Save results
print("💾 Saving processed data...")
df_merged.to_parquet(parquet_cb, index=False)
print(f"✔ Saved to: {parquet_cb}")

# 11) Verify results
print("🔍 Verifying data format...")
df_check = pd.read_parquet(parquet_cb)
print(f"   Verification - shape: {df_check.shape}")
print(f"   Verification - bond code format: {df_check['cb_code'].str.contains(r'\.\w{2}$').all()}")
print(f"   Verification - sample data:")
print(df_check[["cb_code", "trade_date", "close", "premium", "balance"]].head())

# 12) Check balance data
print("📊 Balance data statistics:")
balance_stats = df_check['balance'].describe()
print(balance_stats)
print(f"   Non-null balance data count: {df_check['balance'].notna().sum()}")

print("🎉 Data processing completed!") 