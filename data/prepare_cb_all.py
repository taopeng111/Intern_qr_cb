# data/prepare_cb_all.py
"""
可转债数据预处理脚本
将日线数据(cb_all.parquet)和静态信息(cb_info_full.parquet)合并成框架需要的格式
"""
import sys
from pathlib import Path

# 添加项目根目录到Python路径
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from constants import rename_columns, normalise_cb_code

parquet_cb   = ROOT / "data" / "cb_all.parquet"
parquet_info = ROOT / "data" / "cb_info_full.parquet"

print("🔍 开始处理可转债数据...")

# 1) 读日线数据
print("📊 读取日线数据...")
df_cb = pd.read_parquet(parquet_cb)
print(f"   原始数据形状: {df_cb.shape}")
print(f"   原始列名: {df_cb.columns.tolist()}")

# 2) 重命名列为英文（先重命名，避免后续冲突）
print("📝 重命名列为英文...")
df_cb = rename_columns(df_cb, to_en=True)
print(f"   重命名后列名: {df_cb.columns.tolist()}")

# 3) 标准化代码格式
print("🔄 标准化债券代码格式...")
df_cb["cb_code"] = df_cb["cb_code"].apply(normalise_cb_code)
print(f"   代码示例: {df_cb['cb_code'].head().tolist()}")

# 4) 读静态信息表
print("📋 读取静态信息表...")
df_info = pd.read_parquet(parquet_info, columns=[
    "SECURITY_CODE",
    "CONVERT_STOCK_CODE", 
    "TRANSFER_PRICE",
    "TRANSFER_PREMIUM_RATIO",
    "ACTUAL_ISSUE_SCALE"  # 添加转债余额数据
])
print(f"   静态表形状: {df_info.shape}")

# 5) 标准化静态表代码
print("🔄 标准化静态表代码...")
df_info["cb_code"] = df_info["SECURITY_CODE"].apply(normalise_cb_code)
df_info["stk_code"] = df_info["CONVERT_STOCK_CODE"].apply(
    lambda x: f"{str(x).zfill(6)}.SH" if str(x).startswith(("60","68")) else f"{str(x).zfill(6)}.SZ"
)
print(f"   债券代码示例: {df_info['cb_code'].head().tolist()}")
print(f"   正股代码示例: {df_info['stk_code'].head().tolist()}")

# 6) 准备合并数据
print("🔗 准备合并数据...")
# 从静态表中选择需要的列，并重命名
df_info_for_merge = df_info[["cb_code", "stk_code", "TRANSFER_PRICE", "TRANSFER_PREMIUM_RATIO", "ACTUAL_ISSUE_SCALE"]].copy()
df_info_for_merge = df_info_for_merge.rename(columns={
    "TRANSFER_PRICE": "convert_price",
    "TRANSFER_PREMIUM_RATIO": "premium",
    "ACTUAL_ISSUE_SCALE": "balance",  # 转债余额（亿元）
})

# 7) 清理日线数据中的重复列
print("🧹 清理重复列...")
columns_to_drop = ['stk_code', 'convert_price', 'premium']
for col in columns_to_drop:
    if col in df_cb.columns:
        df_cb = df_cb.drop(columns=[col])
        print(f"   删除了重复列: {col}")

# 8) 合并数据
print("🔗 合并日线和静态数据...")
df_merged = df_cb.merge(
    df_info_for_merge,
    on="cb_code",
    how="left"
)

print(f"   合并后形状: {df_merged.shape}")
print(f"   最终列名: {df_merged.columns.tolist()}")

# 9) 检查关键字段
required_fields = ["cb_code", "trade_date", "close", "volume", "premium", "balance"]
missing_fields = [field for field in required_fields if field not in df_merged.columns]
if missing_fields:
    print(f"❌ 缺少关键字段: {missing_fields}")
else:
    print("✅ 所有关键字段都存在")

# 10) 保存结果
print("💾 保存处理后的数据...")
df_merged.to_parquet(parquet_cb, index=False)
print(f"✔ 已保存到: {parquet_cb}")

# 11) 验证结果
print("🔍 验证数据格式...")
df_check = pd.read_parquet(parquet_cb)
print(f"   验证 - 形状: {df_check.shape}")
print(f"   验证 - 债券代码格式: {df_check['cb_code'].str.contains(r'\.\w{2}$').all()}")
print(f"   验证 - 样本数据:")
print(df_check[["cb_code", "trade_date", "close", "premium", "balance"]].head())

# 12) 检查余额数据
print("📊 余额数据统计:")
balance_stats = df_check['balance'].describe()
print(balance_stats)
print(f"   非空余额数据数量: {df_check['balance'].notna().sum()}")

print("🎉 数据处理完成!") 