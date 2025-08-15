"""
data_handler.py · DailyBarDataHandler v0.2
------------------------------------------------------------
✓ 读取 ① 可转债日线 parquet ② 正股日线 parquet（可选）
✓ 中文列自动映射为英文列（rename_columns）
✓ 每日推送 dict → MarketEvent：close／volume／premium／stk_close
✓ 支持交易日历过滤、停牌过滤
✓ Step2: 连续N日强赎/回售判定（含公告延迟逻辑）
------------------------------------------------------------
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from collections import defaultdict

import numpy as np
import pandas as pd

from constants import (
    rename_columns, 
    load_trading_calendar, 
    is_trading_day,
    FORCE_REDEEM_TRIGGER,
    FORCE_REDEEM_DAYS,
    PUT_TRIGGER,
    PUT_DAYS,
    DEFAULT_COUPON_RATE,
    DEFAULT_MATURITY_YEARS,
    DEFAULT_REDEEM_PRICE,
    DEFAULT_PUT_PRICE,
    OPEN_AUC_START, OPEN_AUC_END,
    CLOSE_AUC_START, CLOSE_AUC_END,
)


class DailyBarDataHandler:
    """
    Parameters
    ----------
    cb_parquet_path   : str | Path  可转债日线 parquet
    stk_parquet_path  : str | Path | None  正股日线 parquet（可选）
    symbols           : List[str] | None   若 None = 全部
    date_col          : str                parquet 中日期列名
    skip_suspended    : bool               volume==0 视为停牌
    calendar_csv      : str | None         本地交易日 csv（若有）
    """

    def __init__(
        self,
        cb_parquet_path: str | Path,
        stk_parquet_path: str | Path | None = None,
        cb_info_path: str | Path | None = None,  # 债券静态信息
        symbols: List[str] | None = None,
        date_col: str = "trade_date",
        skip_suspended: bool = True,
        calendar_csv: str | Path | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        log_level: int = logging.INFO,
    ):
        # ---- logger ----
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())

        # ---- load calendar ----
        load_trading_calendar(calendar_csv)

        # ---- load data ----
        self.cb_df = self._load_parquet(cb_parquet_path, date_col)
        self.stk_df = (
            self._load_parquet(stk_parquet_path, date_col) if stk_parquet_path else None
        )
        # 预计算前收价：按每只券的上一个可用交易日close（不依赖交易日历）
        if not self.cb_df.empty:
            try:
                self.cb_df = self.cb_df.sort_values(["cb_code", date_col])
                self.cb_df["pre_close"] = (
                    self.cb_df.groupby("cb_code")["close"].shift(1).astype(float)
                )
            except Exception as e:
                self.logger.warning(f"Failed to precompute pre_close: {e}")
        
        # 加载债券静态信息
        self.cb_info = self._load_cb_info(cb_info_path) if cb_info_path else {}

        self.symbols = symbols or sorted(self.cb_df["cb_code"].unique().tolist())
        self.skip_suspended = skip_suspended
        self.date_col = date_col

        # 应用日期范围过滤
        if start_date or end_date:
            if start_date:
                start_dt = pd.to_datetime(start_date)
                self.cb_df = self.cb_df[self.cb_df[date_col] >= start_dt]
                if self.stk_df is not None:
                    self.stk_df = self.stk_df[self.stk_df[date_col] >= start_dt]
            
            if end_date:
                end_dt = pd.to_datetime(end_date)
                self.cb_df = self.cb_df[self.cb_df[date_col] <= end_dt]
                if self.stk_df is not None:
                    self.stk_df = self.stk_df[self.stk_df[date_col] <= end_dt]

        self.dates = self.cb_df[date_col].sort_values().unique()
        self._cursor: int = 0
        self.continue_backtest: bool = True
        self.latest_bar: Dict[str, Any] | None = None

        # --- Step2: 连续天计数 & 延迟事件池 ---
        self._redeem_counter = defaultdict(int)   # cb_code -> 连续满足天数
        self._put_counter    = defaultdict(int)
        self._pending_force  = []  # [(effective_date, ForceRedeemEvent)]
        self._pending_put    = []  # 同上

        self.logger.info(
            "Loaded CB %d rows (%d days), %d symbols.",
            len(self.cb_df),
            len(self.dates),
            len(self.symbols),
        )
        if self.stk_df is not None:
            self.logger.info("Loaded STK %d rows.", len(self.stk_df))

    # ------------------------------------------------------------------
    # private helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _load_parquet(path: str | Path | None, date_col: str) -> pd.DataFrame:
        if path is None:
            return pd.DataFrame()

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

        df = rename_columns(pd.read_parquet(path), to_en=True)
        if date_col not in df.columns:
            df.reset_index(inplace=True)
            date_col = df.columns[0]

        df[date_col] = pd.to_datetime(df[date_col])
        return df

    # ------------------------------------------------------------------
    # engine interface
    # ------------------------------------------------------------------
    def update_bars(self) -> None:
        """前进一步；若到尾部就标记完成"""
        while True:
            if self._cursor >= len(self.dates):
                self.continue_backtest = False
                return

            current_date = pd.Timestamp(self.dates[self._cursor])
            self._cursor += 1

            # 非交易日直接跳过
            if not is_trading_day(current_date.strftime("%Y-%m-%d")):
                continue

            daily_cb = self.cb_df[self.cb_df[self.date_col] == current_date]

            if daily_cb.empty:
                continue  # 数据缺口也跳过

            bars: Dict[str, Dict[str, Any]] = {}
            zero_preclose_count = 0
            zero_preclose_examples: list[str] = []
            for _, row in daily_cb.iterrows():
                code = row["cb_code"]
                if code not in self.symbols:
                    continue

                # 停牌过滤
                if self.skip_suspended and row.get("volume", 1) == 0:
                    continue

                # 优先使用预计算的 pre_close，缺失时再回溯查找
                pre_close = row.get("pre_close")
                if pre_close is None or (isinstance(pre_close, float) and not np.isfinite(pre_close)):
                    pre_close = self._lookup_cb_prev_close(code, current_date)
                if not pre_close or (isinstance(pre_close, (int, float)) and float(pre_close) <= 0.0):
                    zero_preclose_count += 1
                    if len(zero_preclose_examples) < 5:
                        zero_preclose_examples.append(code)

                bar: Dict[str, Any] = {
                    "close": row["close"],
                    "volume": row.get("volume", 0),
                    "premium": row.get("premium", None),
                    "convert_price": row.get("convert_price", None),
                    "pre_close": float(pre_close) if pre_close is not None else 0.0,
                }

                # 正股收盘价
                if self.stk_df is not None:
                    stk_code = row.get("stk_code")
                    if stk_code is not None:
                        stk_price = self._lookup_stock_close(stk_code, current_date)
                        if stk_price is not None:
                            bar["stk_close"] = stk_price
                            
                            # ★新增：将正股代码也放入bars字典
                            bars[stk_code] = {
                                "close": stk_price,
                                "volume": row.get("stk_volume", 0),
                                "pre_close": self._lookup_stock_close(stk_code, current_date - pd.Timedelta(days=1)),
                            }
                            # 调试信息
                            if len(bars) <= 5:  # 只在前几个显示调试信息
                                self.logger.debug(f"Added stock {stk_code} to bars with price {stk_price}")
                        else:
                            # 设置为NaN便于下游统一处理
                            bar["stk_close"] = np.nan
                            # 调试：为什么没有找到股票价格
                            if len(bars) <= 5:
                                self.logger.debug(f"No stock price found for {stk_code} on {current_date}")
                    else:
                        # 调试：为什么stk_code为空
                        if len(bars) <= 5:
                            self.logger.debug(f"No stk_code for bond {code}")

                bars[code] = bar

            # 若当天 bars 为空（停牌等原因），继续下一天
            if not bars:
                continue

            # 生成现金流事件
            cash_events = self._generate_cash_events(current_date)
            
            # 检查强赎和回售触发
            force_redeem_put_events = self._check_force_redeem_put(current_date, bars)
            
            self.latest_bar = {
                "timestamp": current_date, 
                "bars": bars,
                "cash_events": cash_events,
                "force_redeem_put_events": force_redeem_put_events
            }

            # 诊断：当日有昨收为0/缺失的债券
            if zero_preclose_count > 0:
                self.logger.debug(
                    "pre_close missing/zero: %d (examples: %s)",
                    zero_preclose_count,
                    ", ".join(zero_preclose_examples)
                )
            break

    def get_latest_bars(self) -> Dict[str, Any]:
        if self.latest_bar is None:
            raise RuntimeError("Call update_bars() before get_latest_bars().")
        return self.latest_bar

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _lookup_stock_close(
        self, stk_code: str, dt: pd.Timestamp
    ) -> Optional[float]:
        if self.stk_df is None:
            return None
        
        # 处理股票代码格式差异：从 "000001.SZ" 转换为 "000001"
        if '.' in stk_code:
            stk_code_clean = stk_code.split('.')[0]
        else:
            stk_code_clean = stk_code
            
        row = self.stk_df[
            (self.stk_df[self.date_col] == dt) & (self.stk_df["stk_code"] == stk_code_clean)
        ]
        if row.empty:
            return None
        return float(row.iloc[0]["close"])

    def _lookup_cb_prev_close(self, cb_code: str, current_date: pd.Timestamp) -> float:
        """查找可转债前一交易日收盘价"""
        # 向前查找最近的交易日数据
        for days_back in range(1, 10):  # 最多回溯10天
            prev_date = current_date - pd.Timedelta(days=days_back)
            if not is_trading_day(prev_date.strftime("%Y-%m-%d")):
                continue
                
            row = self.cb_df[
                (self.cb_df[self.date_col] == prev_date) & (self.cb_df["cb_code"] == cb_code)
            ]
            if not row.empty:
                return float(row.iloc[0]["close"])
        
        return 0.0  # 如果找不到则返回0

    def _load_cb_info(self, cb_info_path: str | Path) -> Dict[str, Dict[str, Any]]:
        """
        加载债券静态信息：票面利率、到期日、转股价等
        """
        if not cb_info_path or not Path(cb_info_path).exists():
            self.logger.warning("CB info file not found, using default values")
            return {}
            
        try:
            df = rename_columns(pd.read_parquet(cb_info_path), to_en=True)
            cb_info = {}
            
            for _, row in df.iterrows():
                cb_code = row.get("cb_code")
                if cb_code:
                    # 自动检测交易所和LOT大小
                    from constants import EXCHANGE_RULES
                    exchange = self._detect_cb_exchange(cb_code)
                    lot_size = EXCHANGE_RULES[exchange]['LOT']
                    
                    cb_info[cb_code] = {
                        "coupon_rate": row.get("coupon_rate", DEFAULT_COUPON_RATE),
                        "maturity_date": pd.to_datetime(row.get("maturity_date", "2029-12-31")),
                        "convert_price": row.get("convert_price", 10.0),
                        "issue_date": pd.to_datetime(row.get("issue_date", "2023-01-01")),
                        "redeem_price": row.get("redeem_price", DEFAULT_REDEEM_PRICE),
                        "put_price": row.get("put_price", DEFAULT_PUT_PRICE),
                        "exchange": exchange,    # 新增：避免多处硬编码
                        "lot_size": lot_size,    # 新增：避免多处硬编码
                    }
                    
            self.logger.info(f"Loaded CB info for {len(cb_info)} bonds")
            return cb_info
            
        except Exception as e:
            self.logger.error(f"Failed to load CB info: {e}")
            return {}

    def _detect_cb_exchange(self, cb_code: str) -> str:
        """
        检测可转债交易所：更稳健的识别逻辑
        """
        if cb_code.endswith('.SH'):
            return 'SSE'
        if cb_code.endswith('.SZ'):
            return 'SZSE'
        return 'SSE' if cb_code[0] in ('5', '6', '9') or cb_code.startswith('11') else 'SZSE'

    def _generate_cash_events(self, current_date: pd.Timestamp) -> List[Any]:
        """
        生成现金流事件：利息、到期兑付等
        """
        events = []
        
        for cb_code, info in self.cb_info.items():
            if cb_code not in self.symbols:
                continue
                
            # 检查到期兑付
            if info["maturity_date"].date() == current_date.date():
                from framework.events import CashEvent
                events.append(CashEvent(
                    dt=current_date,
                    symbol=cb_code,
                    cash_amount=info["redeem_price"] * 10,  # 面值100元/张
                    event_type="maturity",
                    quantity=1  # 这里需要根据实际持仓计算
                ))
                
            # 检查付息日（简化：假设每年付息一次）
            issue_date = info["issue_date"]
            maturity_date = info["maturity_date"]
            coupon_rate = info["coupon_rate"]
            
            # 计算付息日
            current_year = current_date.year
            for year in range(issue_date.year, maturity_date.year + 1):
                coupon_date = pd.Timestamp(f"{year}-{issue_date.month}-{issue_date.day}")
                if coupon_date.date() == current_date.date():
                    from framework.events import CashEvent
                    events.append(CashEvent(
                        dt=current_date,
                        symbol=cb_code,
                        cash_amount=coupon_rate * 100,  # 票面利率 * 面值
                        event_type="coupon",
                        quantity=1  # 这里需要根据实际持仓计算
                    ))
                    break
                    
        return events

    def _update_counters(
        self, cb_code: str, premium: float, current_date: pd.Timestamp
    ) -> None:
        """
        更新计数器；若达到窗口天数则生成 Notice，
        并把真正的赎回/回售事件丢进 pending 列表
        """
        # --- 2.1 强赎（基于正股与转股价的比例） ---
        info = self.cb_info.get(cb_code, {})
        convert_price = info.get("convert_price")
        stk_code = None
        # 尝试从 cb_df 中找到对应正股代码
        try:
            row = self.cb_df[self.cb_df["cb_code"] == cb_code].iloc[0]
            stk_code = row.get("stk_code")
        except Exception:
            stk_code = None

        stk_price = None
        if stk_code is not None:
            stk_price = self._lookup_stock_close(stk_code, current_date)

        trigger_redeem = False
        if convert_price and stk_price:
            # 正股收盘价 >= 1.3 * 转股价
            trigger_redeem = (stk_price >= 1.3 * float(convert_price))

        if trigger_redeem:
            self._redeem_counter[cb_code] += 1
        else:
            self._redeem_counter[cb_code] = 0

        if self._redeem_counter[cb_code] == FORCE_REDEEM_DAYS:
            # ★公告日：记录赎回生效日 = 公告后 30 个自然日
            eff_date = current_date + pd.Timedelta(days=30)
            info = self.cb_info.get(cb_code, {})
            from framework.events import ForceRedeemEvent
            evt = ForceRedeemEvent(
                dt=eff_date,
                symbol=cb_code,
                quantity=1,
                redeem_price=info.get("redeem_price", DEFAULT_REDEEM_PRICE),
            )
            self._pending_force.append(evt)
            self._redeem_counter[cb_code] = 0  # 触发一次后清零

        # --- 2.2 回售（基于正股与转股价的比例） ---
        trigger_put = False
        if convert_price and stk_price:
            # 正股收盘价 <= 0.7 * 转股价
            trigger_put = (stk_price <= 0.7 * float(convert_price))

        if trigger_put:
            self._put_counter[cb_code] += 1
        else:
            self._put_counter[cb_code] = 0

        if self._put_counter[cb_code] == PUT_DAYS:
            eff_date = current_date        # 回售通常 T+0 生效，若需公告可改
            info = self.cb_info.get(cb_code, {})
            from framework.events import PutEvent
            evt = PutEvent(
                dt=eff_date,
                symbol=cb_code,
                quantity=1,
                put_price=info.get("put_price", DEFAULT_PUT_PRICE),
            )
            self._pending_put.append(evt)
            self._put_counter[cb_code] = 0

    def _check_force_redeem_put(self, current_date: pd.Timestamp, bars: Dict[str, Dict[str, Any]]) -> List[Any]:
        """
        检查强赎和回售触发条件（Step2: 连续N日 + 公告延迟）
        """
        # 1) 更新计数 & 把满足 N 日的债券放进 pending 列表
        for cb_code, bar in bars.items():
            if cb_code not in self.cb_info:
                continue
            # premium 不再直接用于强赎/回售触发；改为基于正股与转股价比例
            self._update_counters(cb_code, bar.get("premium", np.nan), current_date)

        # 2) 把 "今天生效" 的 pending 事件输出给 Engine
        today_events = []
        for lst in (self._pending_force, self._pending_put):
            i = 0
            while i < len(lst):
                evt = lst[i]
                if evt.dt.date() == current_date.date():
                    today_events.append(evt)
                    lst.pop(i)        # 删除已触发的
                else:
                    i += 1
        return today_events 

class DataHandlerMinute:
    """
    Minute-level data handler for higher frequency backtesting
    
    Parameters
    ----------
    cb_minute_path   : str | Path  可转债分钟线 parquet
    stk_minute_path  : str | Path | None  正股分钟线 parquet（可选）
    cb_info_path     : str | Path | None  债券静态信息
    symbols          : List[str] | None   若 None = 全部
    date_col         : str                parquet 中日期列名
    time_col         : str                parquet 中时间列名
    skip_suspended   : bool               volume==0 视为停牌
    start_date       : str | None         开始日期
    end_date         : str | None         结束日期
    clock_interval   : int                时钟事件间隔（秒），默认15秒
    """

    def __init__(
        self,
        cb_minute_path: str | Path,
        stk_minute_path: str | Path | None = None,
        cb_info_path: str | Path | None = None,
        symbols: List[str] | None = None,
        date_col: str = "trade_date",
        time_col: str = "time",
        skip_suspended: bool = True,
        start_date: str | None = None,
        end_date: str | None = None,
        clock_interval: int = 15,  # 15秒时钟事件
        log_level: int = logging.INFO,
    ):
        # ---- logger ----
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())

        # ---- load data ----
        self.cb_df = self._load_minute_parquet(cb_minute_path, date_col, time_col)
        self.stk_df = (
            self._load_minute_parquet(stk_minute_path, date_col, time_col) 
            if stk_minute_path else None
        )
        
        # 加载债券静态信息
        self.cb_info = self._load_cb_info(cb_info_path) if cb_info_path else {}

        self.symbols = symbols or sorted(self.cb_df["cb_code"].unique().tolist())
        self.skip_suspended = skip_suspended
        self.date_col = date_col
        self.time_col = time_col
        self.clock_interval = clock_interval

        # 应用日期范围过滤
        if start_date or end_date:
            if start_date:
                start_dt = pd.to_datetime(start_date)
                self.cb_df = self.cb_df[self.cb_df[date_col] >= start_dt]
                if self.stk_df is not None:
                    self.stk_df = self.stk_df[self.stk_df[date_col] >= start_dt]
            
            if end_date:
                end_dt = pd.to_datetime(end_date)
                self.cb_df = self.cb_df[self.cb_df[date_col] <= end_dt]
                if self.stk_df is not None:
                    self.stk_df = self.stk_df[self.stk_df[date_col] <= end_dt]

        # 创建datetime索引用于快速查找
        self.cb_df['datetime'] = pd.to_datetime(
            self.cb_df[date_col].astype(str) + ' ' + self.cb_df[time_col].astype(str)
        )
        if self.stk_df is not None:
            self.stk_df['datetime'] = pd.to_datetime(
                self.stk_df[date_col].astype(str) + ' ' + self.stk_df[time_col].astype(str)
            )

        # 按时间排序
        self.cb_df = self.cb_df.sort_values('datetime')
        if self.stk_df is not None:
            self.stk_df = self.stk_df.sort_values('datetime')

        self.timestamps = self.cb_df['datetime'].unique()
        self._cursor: int = 0
        self.continue_backtest: bool = True
        self.latest_bar: Dict[str, Any] | None = None
        
        # 时钟事件相关
        self._current_date: pd.Timestamp | None = None
        self._last_clock_time: pd.Timestamp | None = None

        # --- Step2: 连续天计数 & 延迟事件池 ---
        self._redeem_counter = defaultdict(int)   # cb_code -> 连续满足天数
        self._put_counter    = defaultdict(int)
        self._pending_force  = []  # [(effective_date, ForceRedeemEvent)]
        self._pending_put    = []  # 同上

    def _load_minute_parquet(
        self,
        path: str | Path | None, 
        date_col: str, 
        time_col: str
    ) -> pd.DataFrame:
        """加载分钟级parquet数据"""
        if not path or not Path(path).exists():
            return pd.DataFrame()
            
        try:
            df = rename_columns(pd.read_parquet(path), to_en=True)
            self.logger.info(f"Loaded minute data: {len(df)} rows from {path}")
            return df
        except Exception as e:
            self.logger.error(f"Failed to load minute data from {path}: {e}")
            return pd.DataFrame()

    def _load_cb_info(self, cb_info_path: str | Path) -> Dict[str, Dict[str, Any]]:
        """加载债券静态信息：票面利率、到期日、转股价等"""
        if not cb_info_path or not Path(cb_info_path).exists():
            self.logger.warning("CB info file not found, using default values")
            return {}
            
        try:
            df = pd.read_parquet(cb_info_path)
            cb_info = {}
            
            for _, row in df.iterrows():
                # Handle different possible column names
                cb_code = row.get("SECUCODE") or row.get("cb_code") or row.get("symbol")
                if cb_code and pd.notna(cb_code):
                    # Extract coupon rate from INTEREST_RATE_EXPLAIN or use default
                    coupon_rate = DEFAULT_COUPON_RATE
                    interest_rate = row.get("INTEREST_RATE_EXPLAIN")
                    if pd.notna(interest_rate) and isinstance(interest_rate, str):
                        try:
                            # Try to extract numeric rate from string like "第一年0.4%,第二年0.6%..."
                            import re
                            rates = re.findall(r'(\d+\.?\d*)%', interest_rate)
                            if rates:
                                coupon_rate = float(rates[0]) / 100.0
                        except:
                            pass
                    
                    cb_info[cb_code] = {
                        "coupon_rate": coupon_rate,
                        "maturity_date": pd.to_datetime(row.get("BOND_EXPIRE", "2029-12-31")),
                        "convert_price": float(row.get("TRANSFER_PRICE", 10.0)),
                        "issue_date": pd.to_datetime(row.get("LISTING_DATE", "2023-01-01")),
                        "redeem_price": float(row.get("EXECUTE_PRICE_SH", DEFAULT_REDEEM_PRICE)),
                        "put_price": float(row.get("EXECUTE_PRICE_HS", DEFAULT_PUT_PRICE)),
                    }
                    
            self.logger.info(f"Loaded CB info for {len(cb_info)} bonds")
            return cb_info
            
        except Exception as e:
            self.logger.error(f"Failed to load CB info: {e}")
            return {}

    def update_bars(self) -> List[Dict[str, Any]]:
        """
        更新分钟级数据，返回多个事件（MarketEvent + ClockEvent）
        
        Returns
        -------
        List[Dict[str, Any]]
            包含多个事件的列表，每个事件包含：
            - type: 'market' 或 'clock'
            - timestamp: 时间戳
            - bars: 行情数据（仅market事件）
        """
        if self._cursor >= len(self.timestamps):
            self.continue_backtest = False
            return []

        current_timestamp = self.timestamps[self._cursor]
        current_date = current_timestamp.date()
        
        events = []
        
        # 检查是否需要生成时钟事件
        if (self._current_date != current_date or 
            (self._last_clock_time is None or 
             (current_timestamp - self._last_clock_time).total_seconds() >= self.clock_interval)):
            
            # 生成时钟事件
            clock_event = {
                "type": "clock",
                "timestamp": current_timestamp,
                "bars": {}
            }
            events.append(clock_event)
            self._last_clock_time = current_timestamp
            self._current_date = current_date

        # 生成市场事件
        bars = {}
        current_data = self.cb_df[self.cb_df['datetime'] == current_timestamp]
        
        for _, row in current_data.iterrows():
            code = row["cb_code"]
            if code not in self.symbols:
                continue
                
            # 检查停牌
            if self.skip_suspended and row.get("volume", 0) == 0:
                continue
                
            # 构建bar数据
            bar = {
                "close": row.get("close", 0.0),
                "volume": row.get("volume", 0),
                "pre_close": self._lookup_prev_close(code, current_timestamp),
            }
            
            # 计算溢价率
            stk_code = row.get("stk_code")
            if stk_code:
                stk_price = self._lookup_stock_price(stk_code, current_timestamp)
                if stk_price is not None:
                    bar["stk_close"] = stk_price
                    convert_price = row.get("convert_price", 10.0)
                    if convert_price > 0:
                        bar["premium"] = (bar["close"] / (stk_price * convert_price / 10.0) - 1) * 100
                    else:
                        bar["premium"] = 0.0
                else:
                    bar["stk_close"] = np.nan  # 设置为NaN便于下游统一处理
                    bar["premium"] = 0.0
            else:
                bar["premium"] = 0.0
                
            bars[code] = bar
            
            # 添加对应的股票数据
            if stk_code and self.stk_df is not None:
                stk_price = self._lookup_stock_price(stk_code, current_timestamp)
                if stk_price is not None:
                    bars[stk_code] = {
                        "close": stk_price,
                        "volume": self._lookup_stock_volume(stk_code, current_timestamp) or 0,
                        "pre_close": self._lookup_stock_prev_close(stk_code, current_timestamp),
                    }

        # 生成现金流事件（仅在每日第一个时间点）
        cash_events = []
        force_redeem_put_events = []
        
        if current_timestamp.time() == pd.Timestamp('09:30:00').time():
            cash_events = self._generate_cash_events(current_timestamp)
            force_redeem_put_events = self._check_force_redeem_put(current_timestamp, bars)

        # ------- 计算 session -------
        ts = current_timestamp.time()
        if OPEN_AUC_START <= ts <= OPEN_AUC_END:
            session = "auction_open"
        elif CLOSE_AUC_START <= ts <= CLOSE_AUC_END:
            session = "auction_close"
        else:
            session = "continuous"

        # 生成市场事件
        market_event = {
            "type": "market",
            "timestamp": current_timestamp,
            "bars": bars,
            "session": session,          # ★ 新增
            "cash_events": cash_events,
            "force_redeem_put_events": force_redeem_put_events
        }
        events.append(market_event)
        
        self.latest_bar = market_event
        self._cursor += 1
        
        return events

    def get_latest_bars(self) -> Dict[str, Any]:
        """获取最新行情数据"""
        return self.latest_bar or {}

    def _lookup_prev_close(self, code: str, current_timestamp: pd.Timestamp) -> float:
        """查找前一交易日收盘价（15:00收盘价）"""
        current_date = current_timestamp.normalize()
        
        # 向前查找最近的交易日数据
        for days_back in range(1, 10):  # 最多回溯10天
            prev_date = current_date - pd.Timedelta(days=days_back)
            if not is_trading_day(prev_date.strftime("%Y-%m-%d")):
                continue
                
            # 查找前一交易日的15:00收盘数据
            prev_close_time = prev_date + pd.Timedelta(hours=15, minutes=0)
            prev_data = self.cb_df[
                (self.cb_df['cb_code'] == code) & 
                (self.cb_df['datetime'] == prev_close_time)
            ]
            if not prev_data.empty:
                return prev_data.iloc[0].get("close", 0.0)
                
            # 如果没有15:00的数据，查找当日最后一个数据
            prev_data = self.cb_df[
                (self.cb_df['cb_code'] == code) & 
                (self.cb_df['datetime'].dt.date == prev_date.date())
            ]
            if not prev_data.empty:
                return prev_data.iloc[-1].get("close", 0.0)
        
        return 0.0

    def _lookup_stock_price(self, stk_code: str, current_timestamp: pd.Timestamp) -> Optional[float]:
        """查找股票价格"""
        if self.stk_df is None:
            return None
            
        # 处理股票代码格式差异
        stk_code_clean = stk_code.split('.')[0] if '.' in stk_code else stk_code
        
        stock_data = self.stk_df[
            (self.stk_df['stk_code'].str.contains(stk_code_clean, na=False)) & 
            (self.stk_df['datetime'] == current_timestamp)
        ]
        
        if not stock_data.empty:
            return stock_data.iloc[0].get("close")
        return None

    def _lookup_stock_volume(self, stk_code: str, current_timestamp: pd.Timestamp) -> Optional[int]:
        """查找股票成交量"""
        if self.stk_df is None:
            return None
            
        stk_code_clean = stk_code.split('.')[0] if '.' in stk_code else stk_code
        
        stock_data = self.stk_df[
            (self.stk_df['stk_code'].str.contains(stk_code_clean, na=False)) & 
            (self.stk_df['datetime'] == current_timestamp)
        ]
        
        if not stock_data.empty:
            return stock_data.iloc[0].get("volume")
        return None

    def _lookup_stock_prev_close(self, stk_code: str, current_timestamp: pd.Timestamp) -> float:
        """查找股票前一交易日收盘价（15:00收盘价）"""
        if self.stk_df is None:
            return 0.0
            
        stk_code_clean = stk_code.split('.')[0] if '.' in stk_code else stk_code
        current_date = current_timestamp.normalize()
        
        # 向前查找最近的交易日数据
        for days_back in range(1, 10):  # 最多回溯10天
            prev_date = current_date - pd.Timedelta(days=days_back)
            if not is_trading_day(prev_date.strftime("%Y-%m-%d")):
                continue
                
            # 查找前一交易日的15:00收盘数据
            prev_close_time = prev_date + pd.Timedelta(hours=15, minutes=0)
            prev_data = self.stk_df[
                (self.stk_df['stk_code'].str.contains(stk_code_clean, na=False)) & 
                (self.stk_df['datetime'] == prev_close_time)
            ]
            if not prev_data.empty:
                return prev_data.iloc[0].get("close", 0.0)
                
            # 如果没有15:00的数据，查找当日最后一个数据
            prev_data = self.stk_df[
                (self.stk_df['stk_code'].str.contains(stk_code_clean, na=False)) & 
                (self.stk_df['datetime'].dt.date == prev_date.date())
            ]
            if not prev_data.empty:
                return prev_data.iloc[-1].get("close", 0.0)
        
        return 0.0

    def _generate_cash_events(self, current_date: pd.Timestamp) -> List[Any]:
        """生成现金流事件：利息、到期兑付等"""
        from .events import CashEvent
        
        events = []
        
        for cb_code, info in self.cb_info.items():
            # 检查是否持有该债券
            if cb_code not in self.symbols:
                continue
                
            # 利息事件（简化：每年付息一次）
            issue_date = info.get("issue_date")
            if issue_date:
                years_held = (current_date - issue_date).days / 365.25
                if years_held >= 1 and current_date.day == 1:  # 每年1月1日付息
                    coupon_rate = info.get("coupon_rate", DEFAULT_COUPON_RATE)
                    cash_amount = 100 * coupon_rate  # 面值100元
                    
                    events.append(CashEvent(
                        dt=current_date,
                        symbol=cb_code,
                        cash_amount=cash_amount,
                        event_type="coupon"
                    ))
                    
            # 到期兑付事件
            maturity_date = info.get("maturity_date")
            if maturity_date and current_date >= maturity_date:
                events.append(CashEvent(
                    dt=current_date,
                    symbol=cb_code,
                    cash_amount=100.0,  # 面值
                    event_type="maturity",
                    quantity=1  # 假设持有1张
                ))
                
        return events

    def _update_counters(
        self, cb_code: str, premium: float, current_date: pd.Timestamp
    ) -> None:
        """
        更新计数器；若达到窗口天数则生成 Notice，
        并把真正的赎回/回售事件丢进 pending 列表
        """
        # --- 2.1 强赎（基于正股与转股价） ---
        info = self.cb_info.get(cb_code, {})
        convert_price = info.get("convert_price")
        stk_code = None
        try:
            row = self.cb_df[self.cb_df["cb_code"] == cb_code].iloc[0]
            stk_code = row.get("stk_code")
        except Exception:
            stk_code = None

        stk_price = None
        if stk_code is not None:
            stk_price = self._lookup_stock_close(stk_code, current_date)

        trigger_redeem = False
        trigger_put = False
        if convert_price and stk_price:
            trigger_redeem = (stk_price >= 1.3 * float(convert_price))
            trigger_put = (stk_price <= 0.7 * float(convert_price))

        if trigger_redeem:
            self._redeem_counter[cb_code] += 1
        else:
            self._redeem_counter[cb_code] = 0

        if self._redeem_counter[cb_code] == FORCE_REDEEM_DAYS:
            eff_date = current_date + pd.Timedelta(days=30)
            from framework.events import ForceRedeemEvent
            evt = ForceRedeemEvent(
                dt=eff_date,
                symbol=cb_code,
                quantity=1,
                redeem_price=info.get("redeem_price", DEFAULT_REDEEM_PRICE),
            )
            self._pending_force.append(evt)
            self._redeem_counter[cb_code] = 0

        # --- 2.2 回售 ---
        if trigger_put:
            self._put_counter[cb_code] += 1
        else:
            self._put_counter[cb_code] = 0

        if self._put_counter[cb_code] == PUT_DAYS:
            eff_date = current_date
            from framework.events import PutEvent
            evt = PutEvent(
                dt=eff_date,
                symbol=cb_code,
                quantity=1,
                put_price=info.get("put_price", DEFAULT_PUT_PRICE),
            )
            self._pending_put.append(evt)
            self._put_counter[cb_code] = 0

    def _check_force_redeem_put(self, current_date: pd.Timestamp, bars: Dict[str, Dict[str, Any]]) -> List[Any]:
        """检查强赎和回售触发条件（Step2: 连续N日 + 公告延迟）"""
        # 1) 更新计数 & 把满足 N 日的债券放进 pending 列表
        for cb_code, bar in bars.items():
            if cb_code not in self.cb_info:
                continue
            premium = bar.get("premium")
            if premium is None:
                continue
            self._update_counters(cb_code, premium, current_date)

        # 2) 把 "今天生效" 的 pending 事件输出给 Engine
        today_events = []
        for lst in (self._pending_force, self._pending_put):
            i = 0
            while i < len(lst):
                evt = lst[i]
                if evt.dt.date() == current_date.date():
                    today_events.append(evt)
                    lst.pop(i)        # 删除已触发的
                else:
                    i += 1
        return today_events 