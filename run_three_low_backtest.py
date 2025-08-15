"""
三低策略回测脚本
使用修复后的框架运行回测
"""
import pandas as pd
import numpy as np
from datetime import datetime

from framework.engine import BacktestEngine
from framework.data_handler import DailyBarDataHandler
from framework.portfolio import BasicPortfolio
from framework.broker import SimpleBroker
from framework.reporting import TearSheetReporter
from strategies.three_low_strategy import ThreeLowStrategy
import perf_metrics

def main():
    """主函数"""
    print("🚀 开始三低策略回测")
    print("=" * 50)
    
    # 1. 数据处理器
    print("📊 初始化数据处理器...")
    data_handler = DailyBarDataHandler(
        cb_parquet_path="data/cb_all.parquet",
        stk_parquet_path="data/stock_full.parquet",
        cb_info_path="data/cb_info.parquet",  # 债券静态信息（可选）
        start_date="2024-01-01",
        end_date="2024-06-30"
    )
    
    # 2. 策略
    print("📈 初始化策略...")
    from strategies.three_low_strategy import load_strategy_config
    config = load_strategy_config("strategies/three_low_config.json")
    strategy = ThreeLowStrategy(config=config)
    
    # 3. 投资组合
    print("💰 初始化投资组合...")
    portfolio = BasicPortfolio(
        initial_capital=1_000_000.0
    )
    
    # 4. 交易执行器
    print("🔄 初始化交易执行器...")
    broker = SimpleBroker(
        slippage_bps=2,
        max_pct_vol=0.15,  # 单笔不超过当日成交量15%
        impact_coeff=0.0005  # 冲击成本系数
    )
    
    # 5. 报告生成器
    print("📋 初始化报告生成器...")
    reporter = TearSheetReporter()
    
    # 6. 回测引擎
    print("⚙️ 初始化回测引擎...")
    engine = BacktestEngine(
        data_handler=data_handler,
        strategy=strategy,
        portfolio=portfolio,
        broker=broker,
        reporter=reporter
    )
    
    # 7. 运行回测
    print("🏃 开始回测...")
    start_time = datetime.now()
    
    try:
        engine.run()
        
        end_time = datetime.now()
        duration = end_time - start_time
        
        print(f"✅ 回测完成! 耗时: {duration}")
        print(f"📊 最终净值: {portfolio.current_nav():,.2f}")
        
        # 输出关键回测指标
        print("\n" + "="*50)
        print("📈 回测指标汇总")
        print("="*50)
        
        # 直接使用perf_metrics计算指标
        try:
            # 获取净值历史数据
            nav_history = portfolio.nav_history
            if nav_history:
                # 转换为DataFrame
                nav_df = pd.DataFrame(nav_history, columns=['datetime', 'nav']).set_index('datetime')
                nav_df.sort_index(inplace=True)
                
                # 计算日收益率
                returns = nav_df['nav'].pct_change().dropna()
                returns_df = returns.to_frame('strategy')
                
                # 使用perf_metrics计算所有指标
                metrics = perf_metrics.calc_metrics(returns_df)
                metrics_series = metrics.iloc[0] if isinstance(metrics, pd.DataFrame) else metrics
                
                print(f"📊 累计收益率: {metrics_series.get('cumulative_return', 'N/A'):.2%}")
                print(f"📈 年化收益率: {metrics_series.get('annual_return', 'N/A'):.2%}")
                print(f"📉 年化波动率: {metrics_series.get('annual_volatility', 'N/A'):.2%}")
                print(f"🎯 夏普比率: {metrics_series.get('sharpe_ratio', 'N/A'):.2f}")
                print(f"🛡️ 索提诺比率: {metrics_series.get('sortino_ratio', 'N/A'):.2f}")
                print(f"📊 最大回撤: {metrics_series.get('max_drawdown', 'N/A'):.2%}")
                print(f"⏱️ 最大回撤持续天数: {metrics_series.get('max_drawdown_duration', 'N/A'):.0f}")
                print(f"📊 Calmar比率: {metrics_series.get('calmar_ratio', 'N/A'):.2f}")
                print(f"📊 Omega比率: {metrics_series.get('omega_ratio', 'N/A'):.2f}")
                print(f"📊 VaR(95%): {metrics_series.get('var_95', 'N/A'):.2%}")
                print(f"📊 CVaR(95%): {metrics_series.get('cvar_95', 'N/A'):.2%}")
                print(f"📊 胜率: {metrics_series.get('win_rate', 'N/A'):.2%}")
                print(f"📊 盈亏比: {metrics_series.get('payoff_ratio', 'N/A'):.2f}")
                print(f"📊 盈利因子: {metrics_series.get('profit_factor', 'N/A'):.2f}")
                
                # 显示回测期间
                start_date = nav_df.index[0].strftime('%Y-%m-%d')
                end_date = nav_df.index[-1].strftime('%Y-%m-%d')
                print(f"\n📅 回测期间: {start_date} 至 {end_date}")
                print(f"📊 总交易日数: {len(nav_df)}")
            else:
                print("⚠️ 未找到净值历史数据")
        except Exception as e:
            print(f"⚠️ 计算指标失败: {e}")
            import traceback
            traceback.print_exc()
        
    except Exception as e:
        print(f"❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main() 