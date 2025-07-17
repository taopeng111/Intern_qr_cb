import akshare as ak
import pandas as pd
from typing import Optional

start, end = "20240101", "20241231"

# 1) 获取全部可转债代码清单（含退市）
spot_df = ak.bond_zh_hs_cov_spot()            # 字段可能在不同版本中略有变化
codes = spot_df["symbol"].tolist()     # 例如 sh110059 / sh110060

# 2) 定义抓取函数

def fetch_one(code: str) -> Optional[pd.DataFrame]:
    df = ak.bond_zh_hs_cov_daily(symbol=code)
    if df is None or df.empty:
        return None
    # 日期字段为"日期"，代码字段为"代码"
    df = df.query("date >= @start and date <= @end")
    df["symbol"] = code
    return df

# 3) 批量抓取并合并
all_data_list = []
for c in codes:
    df = fetch_one(c)
    if df is not None and not df.empty:
        all_data_list.append(df)

if all_data_list:
    all_data = pd.concat(all_data_list, ignore_index=True)
    # 4) 根据代码后缀拆分沪 / 深
    all_data["交易所"] = all_data["代码"].str[-2:]
    df_sh = all_data[all_data["交易所"] == "SH"]
    df_sz = all_data[all_data["交易所"] == "SZ"]
    # 5) 导出 CSV
    df_sh.to_csv("cb_SH_2024.csv", index=False)
    df_sz.to_csv("cb_SZ_2024.csv", index=False)
else:
    print("未获取到任何可转债日行情数据！")
