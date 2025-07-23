import akshare as ak
import pandas as pd

# 拉取可转债基础信息（沪深两市）
def fetch_conversion_table():
    # 获取全部可转债基础信息（沪深两市）
    cb_all = ak.bond_cb_jsl()
    # 只保留需要的字段
    df = cb_all[["bond_id", "stock_id", "convert_price"]].copy()
    df.columns = ["symbol", "stock_code", "conversion_price"]
    df.to_csv("data/conversion_price_table.csv", index=False, encoding="utf-8-sig")
    print("已保存到 data/conversion_price_table.csv")

if __name__ == "__main__":
    fetch_conversion_table()
    print("Done.") 