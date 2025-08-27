# 可转债量化回测框架

## 项目概述

这是一个专门针对中国可转债市场的量化投资回测框架，集成了多种投资策略、完整的回测引擎和性能分析工具。项目旨在为量化投资者提供一个完整的可转债策略研究、回测和优化平台。

## 核心特性

- **多策略支持**: 包含三低策略、五因子增强策略、双阶段策略等多种可转债投资策略
- **完整回测框架**: 事件驱动的回测引擎，支持真实交易成本、滑点、涨跌停等市场约束
- **数据管理**: 自动数据获取、预处理和标准化，支持增量更新
- **策略优化**: 基于Optuna的超参数优化，支持多目标优化
- **性能分析**: 全面的风险收益指标计算和可视化

## 项目结构

```
debts/
├── constants.py                    # 全局常量和配置
├── CB_Data_Dictionary.txt         # 可转债数据字典和交易规则
├── data/                          # 数据相关模块
│   ├── fetch_cb_data.py          # 可转债数据获取
│   ├── get_bond_info.py          # 债券信息获取
│   ├── prepare_cb_all.py         # 数据预处理
│   └── strategy_config.json      # 策略配置
├── framework/                     # 回测框架核心
│   ├── __init__.py
│   ├── engine.py                 # 回测主引擎
│   ├── data_handler.py           # 数据处理器
│   ├── broker.py                 # 交易执行器
│   ├── portfolio.py              # 投资组合管理
│   ├── events.py                 # 事件系统
│   └── reporting.py              # 报告生成
├── strategies/                    # 投资策略
│   ├── three_low_strategy.py     # 三低策略
│   ├── five_factor_enhanced_strategy.py  # 五因子增强策略
│   ├── two_stage_enhanced_strategy.py    # 双阶段策略
│   ├── optuna_tune.py            # 策略优化
│   └── quick_weight_test.py      # 权重测试
├── run_*.py                      # 各种回测运行脚本
├── portfolio_backtest.py         # 投资组合回测框架
├── perf_metrics.py               # 性能指标计算
└── optuna_factor_weights.py      # 因子权重优化
```

## 主要策略

### 1. 三低策略 (Three Low Strategy)
- **核心逻辑**: 选择价格低、溢价率低、余额低的可转债
- **筛选条件**: 价格90-110元，溢价率-10%到20%，余额0.8-10亿元
- **轮动频率**: 周度轮动
- **风控**: 止盈125元，止损95元

### 2. 五因子增强策略 (Five Factor Enhanced Strategy)
- **因子构成**:
  - 双低值 (价格 + 100×溢价率)
  - 历史分位数
  - 隐含波动率代理
  - 价格动量
  - 转债余额
- **优化目标**: 通过Optuna优化因子权重
- **容量**: 支持最多160只转债持仓

### 3. 双阶段策略 (Two Stage Strategy)
- **第一阶段**: 基于多因子综合评分的选股
- **第二阶段**: 基于趋势强度的择时
- **执行**: 周度调仓，最小持仓5天

## 回测框架

### 核心组件
- **DataHandler**: 处理可转债和正股数据，支持日频数据
- **Strategy**: 策略逻辑实现，生成买卖信号
- **Broker**: 模拟交易执行，包含滑点、手续费等成本
- **Portfolio**: 投资组合管理，计算净值变化
- **Engine**: 事件驱动的主循环，协调各组件

### 事件系统
- **MarketEvent**: 市场数据更新事件
- **SignalEvent**: 策略生成的交易信号
- **OrderEvent**: 订单事件
- **FillEvent**: 成交事件
- **CashEvent**: 现金事件（分红、利息等）

## 数据管理

### 数据源
- **可转债数据**: 通过akshare获取日线行情
- **债券信息**: 包含转股价、溢价率、余额等静态信息
- **正股数据**: 支持对冲和相关性分析

### 数据格式
- 支持Parquet格式，提高读写效率
- 自动标准化代码格式（如110059.SH, 128044.SZ）
- 增量更新，避免重复下载

## 性能分析

### 核心指标
- **收益指标**: 年化收益率、累积收益
- **风险指标**: 年化波动率、最大回撤、VaR、CVaR
- **风险调整收益**: Sharpe比率、Sortino比率、Calmar比率
- **交易统计**: 胜率、盈亏比、Profit Factor

### 可视化
- 净值曲线图
- 回撤分析图
- 收益分布图
- 相关性热力图

## 使用方法

### 1. 环境准备
```bash
pip install pandas numpy matplotlib seaborn optuna akshare
```

### 2. 数据获取
```bash
cd data
python fetch_cb_data.py
python prepare_cb_all.py
```

### 3. 运行回测
```bash
# 三低策略回测
python run_three_low_backtest.py

# 五因子策略回测
python run_five_factor_backtest.py

# 双阶段策略回测
python run_two_stage_backtest.py
```

### 4. 策略优化
```bash
# 优化五因子权重
python optuna_factor_weights.py --n-trials 200
```

## 配置说明

### 策略参数
- `max_positions`: 最大持仓数量
- `rotation`: 调仓频率 (daily/weekly/monthly)
- `take_profit`: 止盈价格
- `stop_loss`: 止损价格
- `commission_rate`: 手续费率
- `slippage`: 滑点成本

### 风控参数
- `min_price/max_price`: 价格区间限制
- `premium_bounds`: 溢价率范围
- `min_balance`: 最小余额要求
- `max_volatility`: 最大波动率限制

## 注意事项

1. **数据质量**: 确保数据完整性和准确性，特别是停牌、退市等特殊情况
2. **交易成本**: 回测中已考虑手续费、滑点等成本，但实际交易成本可能更高
3. **流动性约束**: 大资金使用时需考虑流动性约束和冲击成本
4. **风险控制**: 建议设置合理的止损和仓位控制

## 扩展方向

- **多策略组合**: 支持策略组合和动态权重调整
- **实时交易**: 集成实盘交易接口
- **机器学习**: 引入ML模型进行因子挖掘和择时
- **风险管理**: 更复杂的风险模型和压力测试

## 贡献指南

欢迎提交Issue和Pull Request来改进这个框架。在贡献代码前，请确保：
1. 代码符合项目规范
2. 添加必要的测试
3. 更新相关文档

## 许可证

本项目采用MIT许可证，详见LICENSE文件。

## 联系方式

如有问题或建议，请通过GitHub Issues联系。

---

*本项目仅供学习和研究使用，不构成投资建议。投资有风险，入市需谨慎。*
