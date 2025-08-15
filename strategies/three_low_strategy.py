"""
strategies/three_low_strategy.py
----------------------------------
可转债三低策略 - 进化版

基于三低值 = 价格 + 100 × 转股溢价率 + 转债余额
整合技术分析、风险控制、轮动机制等功能
适配本地数据源和回测框架

⚠️ 重要修复 (2024-01-XX):
- 修复三低值公式：溢价率需要乘以100倍权重
- 调整阈值：three_low_min/max/exit 相应调整
- 添加调试输出：显示三低值分布和计算过程
- 修复策略持仓状态同步：Engine 处理 FillEvent 时同步更新策略持仓
- 修复部分成交处理：策略不再提前删除持仓，由 FillEvent 统一管理
- 修复过度换手问题：改为周度轮动，移除"跌出排名"卖出规则
- 修复止盈止损不对称：调整止盈125/止损95，更严格的风控
- 修复收益率筛选：min_return提高到0%，增加收益率趋势分析
"""

from __future__ import annotations
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import json
import os
from pathlib import Path

import pandas as pd
import numpy as np
from tqdm import tqdm

import sys
from pathlib import Path

# 添加项目根目录到Python路径
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from framework.events import MarketEvent, SignalEvent
from constants import Config, normalise_cb_code


@dataclass
class StrategyConfig:
    """策略配置参数"""
    # 三低筛选参数
    max_price: float = 110.0              # 最高价格
    min_price: float = 90.0               # 最低价格
    max_premium: float = 20.0             # 最高溢价率(%)
    min_premium: float = -10.0            # 最低溢价率(%)
    max_balance: float = 10.0             # 最大转债余额(亿元)
    min_balance: float = 0.8              # 最小转债余额(亿元)
    three_low_min: float = 1000.0         # 三低最小值 (修复后)
    three_low_max: float = 1300.0         # 三低最大值 (修复后)
    three_low_exit: float = 2000.0        # 三低平仓值 (修复后)
    
    # 技术分析参数
    enable_trend_analysis: bool = True    # 是否启用趋势分析
    ma_periods: List[int] = None          # 均线周期
    min_ma_score: int = 50                # 均线最低分数
    down_ma_sell_period: int = 20         # 跌破N日均线卖出
    
    # 收益率分析参数
    return_analysis_days: int = 20        # 收益率分析天数
    min_return: float = 0.0               # 最小收益率(%) (修复：不允许亏损)
    max_return: float = 15.0              # 最大收益率(%)
    max_drawdown: float = -8.0            # 最大回撤(%)
    enable_return_trend: bool = True      # 是否启用收益率趋势分析
    min_trend_score: int = 60             # 收益率趋势最低分数
    
    # 交易参数
    lot_size: int = 10                    # 每次交易手数
    max_positions: int = 10               # 最大持仓数量
    take_profit: float = 125.0            # 止盈价格 (修复：更保守的止盈)
    stop_loss: float = 95.0               # 止损价格 (修复：更严格的止损)
    
    # 轮动参数
    rotation_type: str = "weekly"         # 轮动类型: daily/weekly/monthly (改为周度轮动)
    rotation_day: int = 0                 # 轮动日期(周几或每月几号) (0=周一)
    
    # 风险控制
    enable_force_redeem_filter: bool = True  # 是否过滤强制赎回
    force_redeem_days: int = 30           # 距离强制赎回天数
    blacklist: List[str] = None           # 黑名单
    
    def __post_init__(self):
        if self.ma_periods is None:
            self.ma_periods = [5, 10, 20, 30, 60]
        if self.blacklist is None:
            self.blacklist = []


class ThreeLowStrategy:
    """
    可转债三低策略 - 进化版
    
    功能特性：
    1. 三低值计算和筛选 (价格 + 溢价率 + 转债余额)
    2. 技术分析（均线、形态）
    3. 收益率分析
    4. 风险控制（强制赎回、黑名单）
    5. 轮动机制
    6. 仓位管理
    """
    
    def __init__(self, config: StrategyConfig = None):
        if config is None:
            # 尝试加载配置文件
            try:
                self.config = load_strategy_config()
            except:
                self.config = StrategyConfig()
        else:
            self.config = config
        
        # 内部状态
        self.positions: Dict[str, Dict] = {}  # 持仓信息
        self.data_cache: Dict[str, pd.DataFrame] = {}  # 数据缓存
        self.last_rotation_date: Optional[str] = None  # 上次轮动日期
        
        # 加载数据
        self._load_data()
        
    def _load_data(self):
        """加载可转债数据"""
        data_path = Path(__file__).parent.parent / "data" / "cb_all.parquet"
        if data_path.exists():
            self.cb_data = pd.read_parquet(data_path)
            print(f"✅ 加载可转债数据: {self.cb_data.shape}")
        else:
            print("❌ 未找到可转债数据文件")
            self.cb_data = pd.DataFrame()
            
    def _get_latest_data(self, date: str) -> pd.DataFrame:
        """获取指定日期的可转债数据"""
        if self.cb_data.empty:
            return pd.DataFrame()
            
        # 转换日期格式
        try:
            if len(date) == 8:  # YYYYMMDD格式
                date_obj = pd.to_datetime(date, format='%Y%m%d')
            else:  # 其他格式
                date_obj = pd.to_datetime(date)
        except:
            print(f"❌ 无法解析日期: {date}")
            return pd.DataFrame()
            
        # 筛选指定日期的数据
        daily_data = self.cb_data[self.cb_data['trade_date'].dt.date == date_obj.date()].copy()
        
        if daily_data.empty:
            return pd.DataFrame()
            
        # 计算三低值
        # 三低 = 价格 + 100*转股溢价率 + 转债余额
        # 如果没有余额数据，则使用双低计算
        if 'balance' in daily_data.columns:
            daily_data['three_low'] = (
                daily_data['close'] + 
                100 * daily_data['premium'] + 
                daily_data['balance'].fillna(0)
            )
        else:
            # 如果没有余额数据，使用双低计算
            daily_data['three_low'] = daily_data['close'] + 100 * daily_data['premium']
            if not hasattr(self, '_balance_warning_shown'):
                print("⚠️  未找到转债余额数据，使用双低计算")
                self._balance_warning_shown = True
        
        # 基础筛选
        mask = (
            (daily_data['close'] >= self.config.min_price) &
            (daily_data['close'] <= self.config.max_price) &
            (daily_data['premium'] >= self.config.min_premium) &
            (daily_data['premium'] <= self.config.max_premium) &
            (daily_data['three_low'] >= self.config.three_low_min) &
            (daily_data['three_low'] <= self.config.three_low_max)
        )
        
        # 如果有余额数据，添加余额筛选
        if 'balance' in daily_data.columns:
            mask = mask & (
                (daily_data['balance'] >= self.config.min_balance) &
                (daily_data['balance'] <= self.config.max_balance)
            )
        
        filtered_data = daily_data[mask].copy()
        
        # 剔除黑名单
        if self.config.blacklist:
            blacklist_codes = [normalise_cb_code(code) for code in self.config.blacklist]
            filtered_data = filtered_data[~filtered_data['cb_code'].isin(blacklist_codes)]
        
        # 调试：打印三低值分布（仅在第一次运行时）
        if not hasattr(self, '_debug_shown') and not filtered_data.empty:
            print("\n🔍 三低值分布调试信息:")
            print(f"   数据量: {len(filtered_data)}")
            print(f"   三低值范围: [{filtered_data['three_low'].min():.1f}, {filtered_data['three_low'].max():.1f}]")
            print(f"   三低值分位数:")
            percentiles = [10, 25, 50, 75, 90]
            for p in percentiles:
                value = filtered_data['three_low'].quantile(p/100)
                print(f"     {p}%: {value:.1f}")
            
            # 显示前5个样本的详细计算
            print(f"\n   前5个样本详细计算:")
            for i, (_, row) in enumerate(filtered_data.head().iterrows()):
                print(f"     {i+1}. {row['cb_code']}: 价格={row['close']:.1f}, "
                      f"溢价率={row['premium']:.1f}%, 余额={row.get('balance', 0):.1f}, "
                      f"三低值={row['three_low']:.1f}")
            self._debug_shown = True
            
        return filtered_data.sort_values('three_low')
    
    def _calculate_ma_score(self, hist_data: pd.DataFrame) -> int:
        """计算均线得分"""
        if len(hist_data) < max(self.config.ma_periods):
            return 0
            
        # 计算各周期均线
        ma_data = {}
        for period in self.config.ma_periods:
            ma_data[f'ma_{period}'] = hist_data['close'].rolling(window=period).mean()
            
        # 获取最新均线值
        latest_ma = {k: v.iloc[-1] for k, v in ma_data.items()}
        
        # 计算得分（均线多头排列加分）
        score = 0
        ma_values = list(latest_ma.values())
        
        for i in range(len(ma_values) - 1):
            if ma_values[i] > ma_values[i + 1]:
                score += 100 // (len(ma_values) - 1)
                
        return score
    
    def _check_down_ma_sell(self, hist_data: pd.DataFrame, symbol: str) -> bool:
        """检查是否跌破均线"""
        if len(hist_data) < self.config.down_ma_sell_period:
            return False
            
        current_price = hist_data['close'].iloc[-1]
        ma_price = hist_data['close'].rolling(window=self.config.down_ma_sell_period).mean().iloc[-1]
        
        return current_price < ma_price
    
    def _calculate_return_metrics(self, hist_data: pd.DataFrame) -> Tuple[float, float, int]:
        """计算收益率指标"""
        if len(hist_data) < self.config.return_analysis_days:
            return 0.0, 0.0, 0
            
        # 计算收益率
        recent_data = hist_data.tail(self.config.return_analysis_days)
        start_price = recent_data['close'].iloc[0]
        end_price = recent_data['close'].iloc[-1]
        total_return = ((end_price / start_price) - 1) * 100
        
        # 计算最大回撤
        cumulative_max = recent_data['close'].expanding().max()
        drawdown = ((recent_data['close'] / cumulative_max) - 1) * 100
        max_drawdown = drawdown.min()
        
        # 计算收益率趋势分数
        trend_score = self._calculate_return_trend_score(recent_data)
        
        return total_return, max_drawdown, trend_score
    
    def _calculate_return_trend_score(self, recent_data: pd.DataFrame) -> int:
        """计算收益率趋势分数"""
        if not self.config.enable_return_trend or len(recent_data) < 10:
            return 100  # 默认满分
            
        # 计算日收益率序列
        daily_returns = recent_data['close'].pct_change().dropna()
        
        if len(daily_returns) < 5:
            return 100
            
        # 计算趋势指标
        score = 100
        
        # 1. 正收益天数占比 (权重40%)
        positive_days = (daily_returns > 0).sum()
        positive_ratio = positive_days / len(daily_returns)
        score += (positive_ratio - 0.5) * 40  # 基准50%，每10%增减4分
        
        # 2. 最近5天趋势 (权重30%)
        recent_5d = daily_returns.tail(5)
        recent_trend = recent_5d.mean() * 100  # 转换为百分比
        score += recent_trend * 3  # 每1%增减3分
        
        # 3. 价格位置 (权重30%)
        current_price = recent_data['close'].iloc[-1]
        price_range = recent_data['close'].max() - recent_data['close'].min()
        if price_range > 0:
            price_position = (current_price - recent_data['close'].min()) / price_range
            score += (price_position - 0.5) * 30  # 基准50%，每10%增减3分
        
        return max(0, min(100, int(score)))  # 限制在0-100范围内
    
    def _get_historical_data(self, symbol: str, end_date: str, days: int = 100) -> pd.DataFrame:
        """获取历史数据"""
        cache_key = f"{symbol}_{end_date}"
        
        if cache_key in self.data_cache:
            return self.data_cache[cache_key]
            
        # 从数据中筛选
        symbol_data = self.cb_data[self.cb_data['cb_code'] == symbol].copy()
        if symbol_data.empty:
            return pd.DataFrame()
            
        # 按日期排序
        symbol_data = symbol_data.sort_values('trade_date')
        
        # 找到结束日期
        end_date_obj = pd.to_datetime(end_date)
        symbol_data['trade_date'] = pd.to_datetime(symbol_data['trade_date'])
        
        # 获取结束日期之前的数据
        hist_data = symbol_data[symbol_data['trade_date'] <= end_date_obj].copy()
        
        if hist_data.empty:
            return pd.DataFrame()
            
        # 获取最近N天的数据
        if len(hist_data) > days:
            hist_data = hist_data.tail(days)
            
        # 缓存数据
        self.data_cache[cache_key] = hist_data
        return hist_data
    
    def _should_rotate(self, current_date: str) -> bool:
        """判断是否应该轮动"""
        if self.last_rotation_date == current_date:
            return False
            
        if self.config.rotation_type == "daily":
            return True
        elif self.config.rotation_type == "weekly":
            # 简单实现：每周一轮动
            date_obj = pd.to_datetime(current_date)
            return date_obj.weekday() == 0
        elif self.config.rotation_type == "monthly":
            # 每月指定日期轮动
            date_obj = pd.to_datetime(current_date)
            return date_obj.day == self.config.rotation_day
            
        return False
    
    def _filter_force_redeem(self, data: pd.DataFrame) -> pd.DataFrame:
        """过滤强制赎回"""
        if not self.config.enable_force_redeem_filter:
            return data
            
        # 这里需要根据实际数据结构实现强制赎回检测
        # 暂时返回原数据
        return data
    
    def calculate_signals(self, market_event: MarketEvent) -> List[SignalEvent]:
        """
        计算交易信号
        
        Args:
            market_event: 市场事件，包含当日所有可转债数据
            
        Returns:
            交易信号列表
        """
        dt = market_event.dt
        current_date = dt.strftime('%Y%m%d')
        
        # 检查是否需要轮动
        if not self._should_rotate(current_date):
            return []
            
        self.last_rotation_date = current_date
        
        # 获取当日数据
        daily_data = self._get_latest_data(current_date)
        if daily_data.empty:
            return []
            
        # 过滤强制赎回
        daily_data = self._filter_force_redeem(daily_data)
        
        signals = []
        
        # 处理卖出信号
        sell_signals = self._generate_sell_signals(daily_data, current_date)
        signals.extend(sell_signals)
        
        # 处理买入信号
        buy_signals = self._generate_buy_signals(daily_data, current_date)
        signals.extend(buy_signals)
        
        return signals
    
    def _generate_sell_signals(self, daily_data: pd.DataFrame, current_date: str) -> List[SignalEvent]:
        """生成卖出信号"""
        signals = []
        
        for symbol in list(self.positions.keys()):
            should_sell = False
            reason = ""
            
            # 获取当前价格
            symbol_data = daily_data[daily_data['cb_code'] == symbol]
            if symbol_data.empty:
                continue
                
            current_price = symbol_data['close'].iloc[0]
            current_three_low = symbol_data['three_low'].iloc[0]
            
            # 止盈止损
            if current_price >= self.config.take_profit:
                should_sell = True
                reason = "止盈"
            elif current_price <= self.config.stop_loss:
                should_sell = True
                reason = "止损"
            elif current_three_low > self.config.three_low_exit:
                should_sell = True
                reason = "三低值过高"
            
            # 技术分析卖出
            if self.config.enable_trend_analysis:
                hist_data = self._get_historical_data(symbol, current_date)
                if not hist_data.empty and self._check_down_ma_sell(hist_data, symbol):
                    should_sell = True
                    reason = "跌破均线"
            
            # 移除"跌出排名"卖出规则，避免过度换手
            # 仅在轮动时重新选择标的，平时只做止盈止损
            
            if should_sell:
                signals.append(SignalEvent(
                    dt=pd.Timestamp(current_date),
                    symbol=symbol,
                    action="EXIT",
                    size=self.positions[symbol]['quantity']
                ))
                # 不在这里删除持仓，让 FillEvent 来更新持仓状态
                print(f"卖出信号: {symbol} - {reason}")
        
        return signals
    
    def _generate_buy_signals(self, daily_data: pd.DataFrame, current_date: str) -> List[SignalEvent]:
        """生成买入信号"""
        signals = []
        
        # 计算可用仓位
        current_positions = len(self.positions)
        available_slots = self.config.max_positions - current_positions
        
        if available_slots <= 0:
            return signals
        
        # 对候选标的进行技术分析
        candidates = []
        
        for _, row in daily_data.iterrows():
            symbol = row['cb_code']
            
            # 跳过已持仓的
            if symbol in self.positions:
                continue
                
            # 技术分析评分
            hist_data = self._get_historical_data(symbol, current_date)
            if hist_data.empty:
                continue
                
            ma_score = self._calculate_ma_score(hist_data)
            total_return, max_drawdown, trend_score = self._calculate_return_metrics(hist_data)
            
            # 综合评分
            candidate_score = {
                'symbol': symbol,
                'three_low': row['three_low'],
                'ma_score': ma_score,
                'total_return': total_return,
                'max_drawdown': max_drawdown,
                'trend_score': trend_score,
                'price': row['close'],
                'premium': row['premium']
            }
            
            # 如果有余额数据，添加到评分中
            if 'balance' in row:
                candidate_score['balance'] = row['balance']
            
            # 应用筛选条件
            if (ma_score >= self.config.min_ma_score and
                total_return >= self.config.min_return and
                total_return <= self.config.max_return and
                max_drawdown >= self.config.max_drawdown and
                trend_score >= self.config.min_trend_score):
                candidates.append(candidate_score)
        
        # 按三低值排序，选择最优标的
        candidates.sort(key=lambda x: x['three_low'])
        
        for candidate in candidates[:available_slots]:
            symbol = candidate['symbol']
            
            signals.append(SignalEvent(
                dt=pd.Timestamp(current_date),
                symbol=symbol,
                action="LONG",
                size=self.config.lot_size
            ))
            
            # 不在这里更新持仓，让 FillEvent 来更新持仓状态
            print(f"买入信号: {symbol} - 三低值: {candidate['three_low']:.2f}, "
                  f"趋势分数: {candidate['trend_score']}, 收益率: {candidate['total_return']:.1f}%")
        
        return signals
    
    def update_position(self, symbol: str, quantity: int, action: str, fill_price: float = None, fill_dt: pd.Timestamp = None):
        """更新持仓信息（由回测引擎调用）"""
        if action == "BUY":
            if symbol not in self.positions:
                self.positions[symbol] = {'quantity': 0, 'buy_price': 0, 'buy_date': ''}
            self.positions[symbol]['quantity'] += quantity
            # 更新买入价格和日期（使用最新成交价）
            if fill_price is not None:
                self.positions[symbol]['buy_price'] = fill_price
            if fill_dt is not None:
                self.positions[symbol]['buy_date'] = fill_dt.strftime('%Y%m%d')
        elif action == "SELL":
            if symbol in self.positions:
                self.positions[symbol]['quantity'] -= quantity
                # 只有当持仓完全清空时才删除记录
                if self.positions[symbol]['quantity'] <= 0:
                    del self.positions[symbol]
                # 如果还有剩余持仓，保留记录（处理部分成交）
    
    def get_strategy_info(self) -> Dict:
        """获取策略信息"""
        return {
            'strategy_name': 'ThreeLowStrategy',
            'config': self.config.__dict__,
            'current_positions': len(self.positions),
            'max_positions': self.config.max_positions
        }


# 便捷的配置加载函数
def load_strategy_config(config_path: str = None) -> StrategyConfig:
    """从JSON文件加载策略配置"""
    if config_path is None:
        config_path = Path(__file__).parent / "three_low_config.json"
    
    if Path(config_path).exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config_dict = json.load(f)
        return StrategyConfig(**config_dict)
    else:
        # 创建默认配置文件
        default_config = StrategyConfig()
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(default_config.__dict__, f, indent=2, ensure_ascii=False)
        print(f"✅ 已创建默认配置文件: {config_path}")
        return default_config


# 使用示例
if __name__ == "__main__":
    # 加载配置
    config = load_strategy_config()
    
    # 创建策略实例
    strategy = ThreeLowStrategy(config)
    
    print("🎯 三低策略初始化完成!")
    print(f"配置信息: {strategy.get_strategy_info()}") 