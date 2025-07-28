# 中国可转债量化回测框架

一个完整的可转债量化策略回测框架，支持事件驱动架构、多策略开发和专业绩效评估。

## 🚀 项目特点

- **完整回测框架**: 事件驱动架构，支持可转债特有功能（转股、强赎、回售）
- **数据集成**: 自动处理470只可转债数据（2018-2025年）
- **专业绩效评估**: 15个关键指标，包含风险调整收益和风险度量
- **策略开发友好**: 标准化接口，易于开发新策略
- **报告生成**: 自动生成图表、Excel报告和CSV数据

## 📁 目录结构

```
debts/
├── framework/                 # 回测框架核心
│   ├── data_handler.py       # 数据加载和处理
│   ├── engine.py             # 回测引擎
│   ├── events.py             # 事件定义
│   ├── broker.py             # 交易执行
│   ├── portfolio.py          # 组合管理
│   └── reporting.py          # 报告生成
├── strategies/               # 策略目录
│   └── low_premium_strategy.py  # 低溢价策略示例
├── data/                     # 数据目录
│   ├── cb_all.parquet       # 统一的可转债数据
│   ├── cb_info_full.parquet # 静态信息表
│   ├── stock_full.parquet   # 正股数据
│   └── prepare_cb_all.py    # 数据预处理脚本
├── constants.py              # 全局常量和字段映射
├── perf_metrics.py           # 绩效评估函数库
├── run_backtest.py          # 回测运行入口
└── README.md                # 项目说明
```

## 🎯 快速开始

### 1. 环境准备
```bash
pip install pandas numpy matplotlib seaborn
```

### 2. 数据预处理（首次运行）
```bash
python data/prepare_cb_all.py
```

### 3. 运行回测
```bash
python run_backtest.py
```

### 4. 查看结果
回测结果将保存在 `output/YYYYMMDD_HHMMSS/` 目录下：
- `nav_history.csv` - NAV历史数据
- `perf_metrics.csv` - 绩效指标
- `report.xlsx` - Excel报告
- `cumulative_nav.png` - 累计收益曲线
- `drawdown.png` - 回撤曲线

## 📊 数据概览

- **时间区间**: 2018-09-04 到 2025-07-23
- **债券数量**: 470只
- **总记录数**: 344,867条
- **交易所分布**: 深交所55.7%，上交所44.3%

### 关键字段
- `cb_code`: 债券代码（如110059.SH）
- `trade_date`: 交易日期
- `close`: 收盘价
- `volume`: 成交量
- `premium`: 溢价率
- `convert_price`: 转股价
- `stk_code`: 对应正股代码

## 🛠️ 策略开发

### 创建新策略
```python
# strategies/my_strategy.py
from framework.events import MarketEvent, SignalEvent
from typing import List

class MyStrategy:
    def __init__(self, param1=100, param2=0.5):
        self.param1 = param1
        self.param2 = param2
        self.positions = {}
    
    def calculate_signals(self, market_event: MarketEvent) -> List[SignalEvent]:
        dt = market_event.dt
        bars = market_event.data
        signals = []
        
        for symbol, bar in bars.items():
            price = bar["close"]
            premium = bar.get("premium")
            
            # 你的策略逻辑
            if self.should_buy(price, premium):
                signals.append(SignalEvent(
                    dt=dt, symbol=symbol, action="LONG", size=10
                ))
        
        return signals
```

### 修改回测配置
```python
# run_backtest.py
from strategies.my_strategy import MyStrategy

strategy = MyStrategy(param1=110, param2=0.3)
```

## 📈 绩效指标

框架提供15个专业绩效指标：

### 收益指标
- `cumulative_return`: 累积收益率
- `annual_return`: 年化收益率

### 风险指标
- `annual_volatility`: 年化波动率
- `max_drawdown`: 最大回撤
- `max_drawdown_duration`: 最大回撤持续时间

### 风险调整收益
- `sharpe_ratio`: 夏普比率
- `sortino_ratio`: 索提诺比率
- `calmar_ratio`: 卡玛比率
- `omega_ratio`: 欧米伽比率

### 风险度量
- `var_95`: 95%置信度VaR
- `cvar_95`: 95%置信度CVaR
- `tail_ratio`: 尾部比率

### 交易统计
- `win_rate`: 胜率
- `payoff_ratio`: 盈亏比
- `profit_factor`: 盈利因子

## 🔧 框架特性

### 事件驱动架构
- `MarketEvent`: 市场数据事件
- `SignalEvent`: 策略信号事件
- `FillEvent`: 成交事件
- `ConvertEvent`: 转股事件

### 可转债特有功能
- **转股**: 债券按转股价转换为股票
- **强赎**: 发行人按约定价格赎回
- **回售**: 投资者按面值卖回给发行人
- **交易所差异化费率**: 上交所/深交所不同费率

### 数据标准化
- 代码格式统一: `110059.SH` / `128044.SZ`
- 字段映射: 中文↔英文自动转换
- 数据质量: 100%完整性检查

## 📋 依赖环境

- Python 3.7+
- pandas
- numpy
- matplotlib
- seaborn

## 🤝 贡献

欢迎提交Issue和Pull Request来改进这个框架！

## 📄 许可证

MIT License

