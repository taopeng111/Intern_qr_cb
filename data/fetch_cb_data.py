import akshare as ak
import pandas as pd
from typing import Optional

start, end = "2024-01-01", "2024-12-31"
start_dt = pd.to_datetime(start)
end_dt = pd.to_datetime(end)

# 1) 获取全部可转债代码清单（含退市）
spot_df = ak.bond_zh_hs_cov_spot()            # 字段可能在不同版本中略有变化
codes = spot_df["symbol"].tolist()     # 例如 sh110059 / sh110060

# 2) 定义抓取函数

def fetch_one(code: str) -> Optional[pd.DataFrame]:
    try:
        df = ak.bond_zh_hs_cov_daily(symbol=code)
    except Exception as e:
        print(f"抓取 {code} 时出错: {e}")
        return None
    if df is None or df.empty:
        # 只在无数据时记录
        print(f"无数据: {code}")
        return None
    # 兼容不同的日期字段名
    date_col = None
    for col in ['date', '日期', 'trade_date', 'datetime']:
        if col in df.columns:
            date_col = col
            break
    if date_col is None:
        print(f"无日期字段: {code}")
        return None
    df['date'] = pd.to_datetime(df[date_col])
    df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    df["symbol"] = code
    return df

# 3) 批量抓取并合并
all_data_list = []
for idx, c in enumerate(codes, 1):
    df = fetch_one(c)
    if df is not None and not df.empty:
        all_data_list.append(df)
    if idx % 20 == 0:
        print(f"已抓取 {idx}/{len(codes)} 只可转债...")

if all_data_list:
    all_data = pd.concat(all_data_list, ignore_index=True)
    # 4) 根据代码后缀拆分沪 / 深
    all_data["exchange"] = all_data["symbol"].str[:2]
    df_sh = all_data[all_data["exchange"] == "sh"]
    df_sz = all_data[all_data["exchange"] == "sz"]
    # 5) 导出 CSV
    df_sh.to_csv("data/cb_SH_2024.csv", index=False)
    df_sz.to_csv("data/cb_SZ_2024.csv", index=False)
else:
    print("未获取到任何可转债日行情数据！")
