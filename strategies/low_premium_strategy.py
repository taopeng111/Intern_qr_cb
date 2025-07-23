import pandas as pd

# 1. 读取指定日期的可转债日线数据（合并沪深两市）
def get_day_data(date):
    """
    从 data/cb_SH_full.parquet 和 data/cb_SZ_full.parquet 读取指定日期的可转债日线数据，合并后返回。
    参数：
        date (str): 日期字符串，格式 'yyyy-mm-dd'
    返回：
        DataFrame: 当日所有可转债行情数据
    """
    df_sh = pd.read_parquet('data/cb_SH_full.parquet')
    df_sz = pd.read_parquet('data/cb_SZ_full.parquet')
    df = pd.concat([df_sh, df_sz], ignore_index=True)
    return df[df['date'] == date].copy()

# 2. 读取指定日期的正股日线数据（mock 版）
def get_equity_day_data(date):
    """
    mock 版：返回一个包含 stock_code 和 close 字段的 DataFrame。
    实际项目中应从 data/equity_full.parquet 读取。
    参数：
        date (str): 日期字符串，格式 'yyyy-mm-dd'
    返回：
        DataFrame: 当日所有正股行情数据（仅含 stock_code, close）
    """
    # mock: 生成10个正股，close价格为10~20
    data = {
        'stock_code': [f'6000{i:02d}' for i in range(10)],
        'close': [10 + i for i in range(10)]
    }
    return pd.DataFrame(data)

# 3. 读取转股价映射表

def load_conversion_table():
    """
    从 data/conversion_price_table.csv 读取 symbol、stock_code、conversion_price 映射表。
    返回：
        DataFrame: 包含 symbol, stock_code, conversion_price
    """
    return pd.read_csv('data/conversion_price_table.csv', dtype={'symbol':str, 'stock_code':str})

# 4. 核心选券函数

def select_low_premium_cb(df_cb, df_equity, conversion_table, date, top_n=10):
    """
    选出指定日期低转股溢价+破净的前 top_n 只可转债。
    参数：
        df_cb: 可转债日线数据（含 close 字段）
        df_equity: 正股日线数据（含 close 字段）
        conversion_table: DataFrame, 含 symbol → stock_code 映射 + conversion_price
        date: 当前评估日期
        top_n: 选取数量
    返回：
        list: 选中的可转债 symbol 列表
    """
    # 当日数据筛选
    cb_today = df_cb[df_cb['date'] == date].copy() if 'date' in df_cb.columns else df_cb.copy()
    eq_today = df_equity[df_equity['date'] == date].copy() if 'date' in df_equity.columns else df_equity.copy()

    # 合并正股价格
    cb_today = cb_today.merge(conversion_table, on='symbol', how='left')
    cb_today = cb_today.merge(eq_today[['stock_code', 'close']], left_on='stock_code', right_on='stock_code', how='left', suffixes=('', '_stock'))

    # 计算转股价值、溢价率、双低因子
    cb_today['conversion_value'] = cb_today['conversion_price'] * cb_today['close_stock']
    cb_today['premium'] = (cb_today['close'] / cb_today['conversion_value'] - 1) * 100
    cb_today['double_low'] = cb_today['premium'] + cb_today['close']

    # 排序取前 N
    cb_today = cb_today[cb_today['conversion_value'] > 0]
    selected = cb_today.sort_values('double_low').head(top_n)['symbol'].tolist()
    return selected

# 注意：实际项目中，df_equity 应为真实正股日线数据。当前为 mock 数据接口，后续可替换。 