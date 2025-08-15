"""
fetch_cb_info_final.py — 可靠的断点续传且能处理数据类型问题的最终版本
----------------------------------------------------------------------
- 增加数据清洗函数，将接口返回的占位符'-'转换成标准的缺失值 NaN。
- 显式转换列类型为数值型，解决 Parquet 无法保存混合类型列的问题。
- 建议运行前，先删除旧的、可能已损坏的 parquet 文件。
"""

import pandas as pd
import numpy as np # 引入 numpy 用于处理 NaN
import akshare as ak
import time
from pathlib import Path
from tqdm import tqdm

# ------------------------------------------------------------
# 0. 参数设置
# ------------------------------------------------------------
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
PARQ_FILE = DATA_DIR / "cb_info_full.parquet"
print(PARQ_FILE)
N_RETRY = 3
BATCH_SIZE = 50

# ------------------------------------------------------------
# 1. 抓取函数
# ------------------------------------------------------------
def fetch_one_info(code: str, retry: int = N_RETRY) -> pd.DataFrame | None:
    for i in range(retry):
        try:
            raw_df = ak.bond_zh_cov_info(symbol=code[2:], indicator="基本信息")
            if raw_df is None or raw_df.empty: return None
            info_series = raw_df.iloc[0].copy()
            info_series['symbol'] = code
            return info_series.to_frame().T
        except Exception:
            if i < retry - 1: time.sleep(1)
            else: tqdm.write(f"❌ 代码 {code} 抓取失败，已达最大重试次数。")
    return None

# ------------------------------------------------------------
# 2. 数据清洗与类型转换函数
# ------------------------------------------------------------
def clean_and_convert_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    对 DataFrame进行清洗，处理占位符 '-' 并转换数据类型。
    """
    # 定义需要转换为数值类型的列名列表
    # (根据之前的字段列表，我们把所有价格、比率、金额相关的列都包含进来)
    numeric_cols = [
        'FIRST_PER_PREPLACING', 'ISSUE_PRICE', 'ACTUAL_ISSUE_SCALE',
        'CONVERT_STOCK_PRICE', 'PBV_RATIO', 'CURRENT_BOND_PRICE',
        'TRANSFER_PRICE', 'TRANSFER_VALUE', 'TRANSFER_PREMIUM_RATIO',
        'RESALE_TRIG_PRICE', 'REDEEM_TRIG_PRICE', 'EXECUTE_PRICE_HS',
        'EXECUTE_PRICE_SH', 'ONLINE_GENERAL_LWR'
    ]
    
    # 遍历这些列，进行处理
    for col in numeric_cols:
        if col in df.columns:
            # 1. 将列中的 '-' 替换为标准的缺失值 np.nan
            df[col] = df[col].replace('-', np.nan)
            # 2. 将整列转换为数值类型，无法转换的会变成 NaN
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
    return df

# ------------------------------------------------------------
# 3. 写入/追加函数 (已集成数据清洗)
# ------------------------------------------------------------
def append_to_parquet(df_to_append: pd.DataFrame, target_path: Path):
    """安全地将数据追加到 Parquet 文件中，并在追加前进行清洗"""
    
    # 【核心改动】在合并数据前，先对新抓取的数据进行清洗
    cleaned_df_to_append = clean_and_convert_data(df_to_append)
    
    # 读取旧数据
    if target_path.exists() and target_path.stat().st_size > 0:
        try:
            existing_df = pd.read_parquet(target_path)
            combined_df = pd.concat([existing_df, cleaned_df_to_append], ignore_index=True)
        except Exception as e:
            tqdm.write(f"⚠️ 读取旧文件失败: {e}。将只使用新数据创建文件。")
            combined_df = cleaned_df_to_append
    else:
        combined_df = cleaned_df_to_append
    
    # 去重和排序
    combined_df.drop_duplicates(subset=['symbol'], keep='last', inplace=True)
    combined_df.sort_values(by="symbol", inplace=True)
    
    # 保存到文件
    combined_df.to_parquet(target_path, compression='zstd', index=False)


# ------------------------------------------------------------
# 4. 主程序
# ------------------------------------------------------------
def main():
    print("🔄 获取线上代码列表...")
    try:
        spot_df = ak.bond_zh_hs_cov_spot()
        code_col = "symbol" if "symbol" in spot_df.columns else "bond_code"
        all_online_codes = set(spot_df[code_col].astype(str).tolist())
        print(f"✅ 线上目标共 {len(all_online_codes)} 只。")
    except Exception as e:
        raise SystemExit(f"❌ 获取列表失败: {e}")

    processed_codes = set()
    if PARQ_FILE.exists() and PARQ_FILE.stat().st_size > 0:
        print(f"🔍 检查本地文件: {PARQ_FILE}")
        processed_df = pd.read_parquet(PARQ_FILE)
        if 'symbol' in processed_df.columns:
            processed_codes = set(processed_df['symbol'].astype(str).tolist())
            print(f"📊 本地已存 {len(processed_codes)} 条。")

    codes_to_fetch = sorted(list(all_online_codes - processed_codes))

    if not codes_to_fetch:
        print("\n🎉 所有数据均已是最新，无需更新。")
        return

    print(f"🚀 需抓取 {len(codes_to_fetch)} 只新增或遗漏的债券。")
    
    new_data_batch = []
    pbar = tqdm(codes_to_fetch, desc="增量抓取进度")
    for code in pbar:
        df_info = fetch_one_info(code)
        if df_info is not None:
            new_data_batch.append(df_info)
        
        if len(new_data_batch) >= BATCH_SIZE or (code == codes_to_fetch[-1] and new_data_batch):
            pbar.set_description(f"💾 正在清洗并保存 {len(new_data_batch)} 条数据...")
            batch_df = pd.concat(new_data_batch, ignore_index=True)
            append_to_parquet(batch_df, PARQ_FILE)
            new_data_batch = []

    print("\n" + "="*50)
    final_df = pd.read_parquet(PARQ_FILE)
    print(f"✅ 数据更新完成！当前总记录数: {len(final_df):,}")
    print(f"   数据文件路径: {PARQ_FILE}")
    print("="*50)


if __name__ == "__main__":
    # ###############################################################
    # # 重要提示！！！
    # ###############################################################
    # # 如果您之前的文件是未清洗的，可能会导致读取时出错。
    # # 建议在第一次运行此新版脚本前，手动删除旧的 parquet 文件：
    # # cb_info_full.parquet
    # ###############################################################
    main()