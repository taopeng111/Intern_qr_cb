# 中国可转债量化回测与绩效评估框架

本项目为中国可转债量化策略研究提供数据抓取、绩效评估与市场信息支持。

## 目录结构

- `data/`  
  - `fetch_cb_data.py`：可转债日行情数据抓取脚本（基于 akshare）。
  - `cb_SH_2024.csv`、`cb_SZ_2024.csv`：沪深两市可转债日行情数据。
- `perf_metrics.py`  
  量化策略绩效评估函数，包括年化收益、Sharpe/Sortino/Calmar 比率、最大回撤、VaR、CVaR 等常用指标及可视化工具。
- `CB_Data_Dictionary.txt`  
  可转债市场基础知识、交易规则、常用字段说明与数据字典。
- `strategies/`  
  预留策略开发目录。
- `framwork/`  
  预留回测框架目录。

## 依赖环境

- Python 3.7+
- pandas
- numpy
- matplotlib
- seaborn
- akshare

## 快速开始

1. 安装依赖：
   ```bash
   pip install pandas numpy matplotlib seaborn akshare
   ```
2. 抓取可转债数据：
   ```bash
   python data/fetch_cb_data.py
   ```
   数据将自动保存至 `data/` 目录下。
3. 使用 `perf_metrics.py` 进行策略绩效分析。

## 数据说明

详见 `CB_Data_Dictionary.txt`，包括可转债市场规则、常用字段、数据来源等。

## 可转债日线数据说明

本数据包包含两份已清洗、结构统一的可转债日线数据（沪市 + 深市），适用于量化回测、因子研究、行情分析等多种场景。

### 数据覆盖范围
- 覆盖全部沪深两市在交易所挂牌的可转债（不含退市标的，若需补全可联系维护人）
- 时间跨度：约 2019 年至今，随脚本每日自动增量更新
- 总计约 470–480 只可转债，单市场数据量约 10–20 万行

### 文件列表
| 文件名              | 内容               | 行数范围         |
|---------------------|--------------------|-----------------|
| cb_SH_full.parquet  | 沪市可转债日线数据 | ~10万–20万行    |
| cb_SZ_full.parquet  | 深市可转债日线数据 | ~10万–20万行    |

### 文件格式
- 格式：Parquet（二进制高效列式存储，推荐 pyarrow 读取）
- 编码：UTF-8
- 压缩：部分文件使用 ZSTD 压缩（`pip install pyarrow` 可自动解压）
- 读取建议：
  ```python
  import pandas as pd
  df = pd.read_parquet('cb_SH_full.parquet')
  ```

### 字段说明
| 字段名   | 类型      | 单位/示例         | 含义                 |
|----------|-----------|-------------------|----------------------|
| date     | datetime  | 2024-01-02        | 交易日期（yyyy-mm-dd）|
| open     | float     | 123.45            | 开盘价（元）         |
| high     | float     | 125.67            | 最高价（元）         |
| low      | float     | 120.00            | 最低价（元）         |
| close    | float     | 124.00            | 收盘价（元）         |
| volume   | int       | 1000              | 成交量（张）         |
| symbol   | string    | sh110059          | 债券代码             |

### 使用示例（Python）
```python
import pandas as pd

# 读取沪市
df_sh = pd.read_parquet("cb_SH_full.parquet")
# 读取深市
df_sz = pd.read_parquet("cb_SZ_full.parquet")

# 合并为一个 DataFrame（如有需要）
df_all = pd.concat([df_sh, df_sz], ignore_index=True)

# 按某只可转债取出所有日线
df_one = df_all[df_all["symbol"] == "sh110059"]

# 按日期区间筛选
df_period = df_all[(df_all["date"] >= "2023-01-01") & (df_all["date"] <= "2023-12-31")]

# 计算每日收盘均价（横截面均值）
daily_mean = df_all.groupby("date")["close"].mean()
```

### 注意事项
- 数据已标准化，无需额外预处理，字段缺失极少（如遇极端停牌日可自行补全）
- 每日运行的抓取脚本自动去重和增量更新，无需担心重复数据
- 数据中不包含退市标的，如需补全请联系维护人
- 如需对接回测框架，可在 `data_handler.py` 中直接加载以上文件，按 symbol + date 切片并逐日推送

---

