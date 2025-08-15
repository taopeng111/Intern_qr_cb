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

## 🕐 分钟级回测功能

### 新增功能
- **双事件循环**: 外层读取分钟Bar，内层15秒或1秒触发ClockEvent
- **模块解耦**: DataHandler.update_bars() 返回list，一次可能推多条MarketEvent
- **实时估值**: Portfolio估值频率可保持日终，也可分钟级实时滚动
- **时钟事件**: 支持定时触发策略检查、风险控制等

### 分钟级数据处理器
```python
from framework.data_handler import DataHandlerMinute

# 初始化分钟级数据处理器
data_handler = DataHandlerMinute(
    cb_minute_path="data/cb_minute.parquet",
    stk_minute_path="data/stock_minute.parquet",
    cb_info_path="data/cb_info_full.parquet",
    start_date="2024-01-01",
    end_date="2024-01-31",
    clock_interval=15,  # 15秒时钟事件
)
```

### 运行分钟级回测
```bash
# 使用模拟数据测试
python test_minute_backtest.py

# 使用真实分钟数据
python run_minute_backtest.py
```

### 时钟事件处理
```python
from framework.events import ClockEvent

# 在策略中处理时钟事件
def _process_clock(self, clock_event: ClockEvent) -> None:
    # 定时检查、风险控制等
    pass
```

### 分钟级 vs 日线级对比
- **波动率**: 分钟级更贴近真实市场波动
- **下单价**: 分钟级提供更精确的成交价格
- **方向一致性**: 整体策略方向与日线版本一致
- **性能**: 分钟级回测计算量更大，但提供更精细的结果

## 📈 性能指标

### 基础指标
- **总收益率**: 整个回测期间的总收益
- **年化收益率**: 年化后的收益率
- **最大回撤**: 最大亏损幅度
- **夏普比率**: 风险调整后收益
- **波动率**: 收益率的波动程度

### 高级指标
- **卡玛比率**: 最大回撤调整后收益
- **索提诺比率**: 下行风险调整后收益
- **VaR**: 风险价值
- **CVaR**: 条件风险价值
- **胜率**: 盈利交易占比

## 🔧 配置说明

### 流动性约束参数
```python
# constants.py
PRICE_LIMIT_CB = 0.20      # 可转债涨跌幅限制 20%
PRICE_LIMIT_STK = 0.10     # 股票涨跌幅限制 10%
MAX_PCT_VOL = 0.15         # 单笔不超过当日成交量 15%
IMPACT_COEFF = 0.0005      # 冲击成本系数
```

### 债券现金流参数
```python
DEFAULT_COUPON_RATE = 0.02     # 默认票面利率 2%
DEFAULT_MATURITY_YEARS = 6     # 默认到期年限 6年
DEFAULT_REDEEM_PRICE = 100.0   # 默认赎回价（面值）
DEFAULT_PUT_PRICE = 100.0      # 默认回售价（面值）
```

## 🐛 故障排除

### 常见问题
1. **数据文件不存在**: 检查 `data/` 目录下的parquet文件
2. **内存不足**: 减少回测时间范围或债券数量
3. **性能指标计算错误**: 检查净值数据是否连续

### 调试模式
```python
# 启用详细日志
logging.basicConfig(level=logging.DEBUG)

# 在数据处理器中启用调试
data_handler = DailyBarDataHandler(..., log_level=logging.DEBUG)
```

## 📝 更新日志

### v0.4 (最新)
- ✅ 新增分钟级回测功能
- ✅ 实现双事件循环架构
- ✅ 添加时钟事件支持
- ✅ 支持分钟级实时估值

### v0.3
- ✅ 实现债券现金流功能（利息、强赎、回售、到期兑付）
- ✅ 添加流动性约束（涨跌停、成交量限制、冲击成本）
- ✅ 完善事件驱动架构

### v0.2
- ✅ 修复股票持仓估值更新问题
- ✅ 优化数据处理器性能
- ✅ 增强错误处理

### v0.1
- ✅ 基础回测框架
- ✅ 三低策略实现
- ✅ 绩效评估系统

## 🤝 贡献指南

欢迎提交Issue和Pull Request！

### 开发环境
```bash
git clone <repository>
cd debts
pip install -r requirements.txt
```

### 代码规范
- 使用类型注解
- 添加文档字符串
- 遵循PEP 8规范
- 编写单元测试

## 📄 许可证

MIT License

## 📞 联系方式

如有问题，请提交Issue或联系维护者。


