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
    "TRANSFER_PREMIUM_RATIO"
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

# 6) 准备合并数据 - 避免重复列名
print("🔗 准备合并数据...")
# 从静态表中选择需要的列，并重命名
df_info_for_merge = df_info[["cb_code", "stk_code", "TRANSFER_PRICE", "TRANSFER_PREMIUM_RATIO"]].copy()
df_info_for_merge = df_info_for_merge.rename(columns={
    "TRANSFER_PRICE": "convert_price",
    "TRANSFER_PREMIUM_RATIO": "premium",
})

# 7) 合并数据
print("🔗 合并日线和静态数据...")
df_merged = df_cb.merge(
    df_info_for_merge,
    on="cb_code",
    how="left"
)

print(f"   合并后形状: {df_merged.shape}")
print(f"   最终列名: {df_merged.columns.tolist()}")

# 8) 检查关键字段
required_fields = ["cb_code", "trade_date", "close", "volume"]
missing_fields = [field for field in required_fields if field not in df_merged.columns]
if missing_fields:
    print(f"❌ 缺少关键字段: {missing_fields}")
else:
    print("✅ 所有关键字段都存在")

# 9) 保存结果
print("💾 保存处理后的数据...")
df_merged.to_parquet(parquet_cb, index=False)
print(f"✔ 已保存到: {parquet_cb}")

# 10) 验证结果
print("🔍 验证数据格式...")
df_check = pd.read_parquet(parquet_cb)
print(f"   验证 - 形状: {df_check.shape}")
print(f"   验证 - 债券代码格式: {df_check['cb_code'].str.contains(r'\.\w{2}$').all()}")
print(f"   验证 - 样本数据:")
print(df_check[["cb_code", "trade_date", "close", "premium"]].head())

print("🎉 数据处理完成!") 