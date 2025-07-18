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

---

如需更详细的说明或英文版，可随时联系作者。
