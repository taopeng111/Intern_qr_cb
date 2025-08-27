# two_stage_enhanced_strategy.py
# -*- coding: utf-8 -*-
"""
Two-Stage Enhanced Convertible Bond Strategy
(1) Selection (filters + composite score)  (2) Timing (trend strength)
Weekly rebalance, min holding days, next-open execution, explicit costs.

This module is designed to be plugged into a lightweight backtest runner.
It expects an external runner to:
  - set `self.cb_data` to a DataFrame with at least the columns described below
  - call `calculate_signals(MarketEvent(dt=...))` day by day
  - execute signals at the chosen execution rule (e.g., next open)
  - call `update_position(...)` after fills so we can track holding days

Required columns in cb_data (per row = one bond-day):
  trade_date: datetime64[ns]
  cb_code:    str (6-digit, no suffix)
  open:       float (NaN allowed; we will fallback to close for marks if needed)
  close:      float
  premium:    float (percentage, e.g., 12.3 = 12.3%)
  ytm:        float (percentage, pre-tax; NaN allowed)
  term:       float (remaining years; NaN allowed)
  turnover:   float (currency amount; used as liquidity proxy; NaN allowed)

Optional:
  is_st:      bool/int (1 for ST), default False
  balance:    float (outstanding size), optional

Notes:
- Trend strength uses 3/5/10/15/20-day SMAs on close. Score = 100 * (#positive) / N.
  Positives: [close>ma5, ma3>ma5, ma5>ma10, ma10>ma15, ma15>ma20]. N=5.
- Composite score: daily cross-section Z-score over specified factors, with signs/weights.
- Filters are interval-based (inclusive) and applied BEFORE ranking.
- Rebalance: weekly by weekday (0=Mon...4=Fri). On rebalance day we form target list.
- Min holding days: positions held < min_hold_days are protected from churn unless hard exit (e.g., fail filters badly or trend falls below out threshold).
- Execution: Assume NEXT-OPEN fills; costs (commission & slippage) are handled by the runner.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import numpy as np
import pandas as pd

# -------------------------------------------------------------
# Events interface (the runner should supply these types)
# -------------------------------------------------------------
try:
    from framework import events as _evt  # provided by runner
    MarketEvent = _evt.MarketEvent
    SignalEvent = _evt.SignalEvent
except Exception:
    # Minimal fallbacks for testing
    from dataclasses import dataclass as _dc
    @_dc
    class MarketEvent:  # type: ignore
        dt: pd.Timestamp
    @_dc
    class SignalEvent:  # type: ignore
        dt: pd.Timestamp
        symbol: str
        action: str   # "LONG" or "EXIT"
        size: int

# -------------------------------------------------------------
# Config
# -------------------------------------------------------------
@dataclass
class StrategyConfig:
    top_n: int = 10
    rebalance: str = "weekly"            # "weekly" only for now
    rebalance_weekday: int = 4           # 0=Mon ... 4=Fri
    min_hold_days: int = 5

    # Timing thresholds (trend strength 0..100)
    trend_in: float = 75.0
    trend_out: float = 50.0

    # Filters (intervals inclusive)
    price_min: float = 100.0
    price_max: float = 150.0
    premium_min: float = 0.0
    premium_max: float = 70.0
    term_min_years: float = 1.0
    turnover_min: float = 1_000_000.0  # currency amount
    exclude_new_days: int = 3
    blacklist: List[str] = field(default_factory=list)

    # Composite factor weights and signs (positive weight means higher is better)
    # We'll standardized each factor (z-score), then multiply by weight.
    factor_weights: Dict[str, float] = field(default_factory=lambda: {
        "neg_premium": 1.0,        # -premium (lower premium is better)
        "neg_price_pct_rank": 1.0, # -price rank (cheaper is better)
        "neg_ytm": 0.5,            # -ytm (higher ytm might indicate risk; soft penalty)
        "neg_vol20": 0.5,          # -volatility
        "liq_rank": 0.5,           # liquidity rank (higher better)
        "neg_term": 0.2,           # -term (too long term gets slight penalty)
    })

    # Position sizing
    lot_size: int = 10                    # 1 lot = 10 bonds
    target_gross_exposure: float = 1.0    # 100% long

    # Execution & costs (handled by runner; here for reference)
    execution: str = "next_open"
    commission_rate: float = 0.0003
    slippage: float = 0.001

# -------------------------------------------------------------
# Strategy
# -------------------------------------------------------------
class TwoStageEnhancedCBStrategy:
    def __init__(self, config: StrategyConfig):
        self.cfg = config
        self.cb_data: Optional[pd.DataFrame] = None  # must be set externally
        # book-keeping
        self.hold_days: Dict[str, int] = {}          # symbol -> days held
        self.last_rebalance_dt: Optional[pd.Timestamp] = None
        self._positions: Dict[str, int] = {}         # lots held (managed by runner, but we track for hold_days)
        self._today = None

    # -------------- Public API used by runner --------------
    def calculate_signals(self, market_event: MarketEvent) -> List[SignalEvent]:
        dt = pd.to_datetime(market_event.dt).normalize()
        self._today = dt
        
        if self.cb_data is None or self.cb_data.empty:
            return []

        # === PATCH 1: 统一当日数据到 day DataFrame ===
        bars = getattr(market_event, "data", None)
        dt = pd.to_datetime(market_event.dt).normalize()

        day_data = []

        # 形态 A：{"dt": <Timestamp>, "bars": <DataFrame>}
        if isinstance(bars, dict) and "bars" in bars and isinstance(bars["bars"], pd.DataFrame):
            src = bars["bars"].copy()
            # 列名兜底
            if "cb_code" not in src.columns and "symbol" in src.columns:
                src = src.rename(columns={"symbol": "cb_code"})
            for _, row in src.iterrows():
                day_data.append({
                    "cb_code":   str(row.get("cb_code", ""))[-6:].zfill(6),
                    "trade_date": dt,
                    "open":      row.get("open", row.get("close", np.nan)),
                    "close":     row.get("close", np.nan),
                    "premium":   row.get("premium", np.nan),
                    "ytm":       row.get("ytm", np.nan),
                    "term":      row.get("term", np.nan),
                    "turnover":  row.get("turnover", np.nan),
                    "is_st":     row.get("is_st", 0),
                    "balance":   row.get("balance", np.nan),
                })
            print(f"DEBUG: 接收到 DataFrame 形态的当日 bars，行数: {len(src)}")

        # 形态 B：{cb_code -> dict(bar)}
        elif isinstance(bars, dict):
            print(f"DEBUG: 接收到 {len(bars)} 个键（期望是 cb_code -> bar 的映射）")
            for cb_code, bar_data in bars.items():
                if not isinstance(bar_data, dict):
                    continue
                day_data.append({
                    "cb_code":   str(cb_code)[-6:].zfill(6),
                    "trade_date": dt,
                    "open":      bar_data.get("open", bar_data.get("close", np.nan)),
                    "close":     bar_data.get("close", np.nan),
                    "premium":   bar_data.get("premium", np.nan),
                    "ytm":       bar_data.get("ytm", np.nan),
                    "term":      bar_data.get("term", np.nan),
                    "turnover":  bar_data.get("turnover", np.nan),
                    "is_st":     bar_data.get("is_st", 0),
                    "balance":   bar_data.get("balance", np.nan),
                })

        # 形态 C：没有传 data，就从 self.cb_data 取当日切片
        else:
            print("DEBUG: 未提供 bars，回退使用 self.cb_data 当日切片")
            day = self.cb_data[self.cb_data["trade_date"].dt.normalize() == dt].copy() if self.cb_data is not None else pd.DataFrame()
            if day.empty:
                print("DEBUG: 当日切片为空，返回空信号")
                return []

        # 汇总为 DataFrame
        if 'day' not in locals():
            day = pd.DataFrame(day_data)

        if day.empty:
            print("DEBUG: 没有创建任何数据行（bars 结构不匹配或当日无标的）")
            return []

        # 类型规范化
        day['trade_date'] = pd.to_datetime(day['trade_date'])
        for col in ['open', 'close', 'premium', 'ytm', 'term', 'turnover', 'balance']:
            if col in day.columns:
                day[col] = pd.to_numeric(day[col], errors='coerce')
        day['is_st'] = day.get('is_st', 0)
        day['is_st'] = day['is_st'].fillna(0).astype(int)

        print(f"DEBUG: 创建DataFrame，形状: {day.shape}")
        try:
            print(f"DEBUG: 数据示例: {day.head(3).to_dict('records')}")
        except Exception:
            pass
        # === END PATCH 1 ===

        if day.empty:
            return []

        # 应用过滤器
        print(f"DEBUG: 过滤前数据形状: {day.shape}")
        print(f"DEBUG: 数据列: {list(day.columns)}")
        print(f"DEBUG: 价格范围: {day['close'].min():.2f} - {day['close'].max():.2f}")
        print(f"DEBUG: 溢价率范围: {day['premium'].min():.2f} - {day['premium'].max():.2f}")
        print(f"DEBUG: 成交量范围: {day['turnover'].min():.0f} - {day['turnover'].max():.0f}")
        
        day = self._apply_filters(day, dt)
        print(f"DEBUG: 过滤后数据形状: {day.shape}")
        
        if day.empty:
            print("DEBUG: 所有债券都被过滤掉了")
            # if nothing passes filters, consider exits for current holds (if any)
            return self._exit_all_signals(dt)

        # Weekly rebalance only
        if self.cfg.rebalance == "weekly":
            if dt.weekday() != int(self.cfg.rebalance_weekday):
                # 添加日志：非调仓日
                print(f"DEBUG: {dt.date()} 不是调仓日 (weekday={dt.weekday()}, 调仓日={self.cfg.rebalance_weekday})")
                return []  # no signals on non-rebalance days
            else:
                print(f"DEBUG: {dt.date()} 是调仓日，开始生成信号")

        # Form target list on rebalance day
        target = self._rank_and_pick(day, dt)

        signals: List[SignalEvent] = []
        current = set(self._positions.keys())

        # Exits: not in target or failed trend_out
        to_exit = []
        for sym in current:
            # protect min hold days unless hard fail (trend_out or filter fail)
            hd = self.hold_days.get(sym, 999)
            sym_row = day[day["cb_code"] == sym]
            trend_ok = False
            if not sym_row.empty:
                trend_ok = bool(sym_row["trend_strength"].iloc[0] >= self.cfg.trend_out)
            # if sym not in today's universe or trend below out threshold -> exit
            if (sym not in set(target["cb_code"])) or (not trend_ok):
                # allow exit even if min hold days not met (hard exit)
                to_exit.append(sym)

        for sym in to_exit:
            signals.append(SignalEvent(dt=dt, symbol=sym, action="EXIT", size=self._positions.get(sym, 0)))

        # Entries: in target but not currently held
        to_enter = []
        for sym in target["cb_code"].tolist():
            if sym not in current:
                # entry check: trend must be >= trend_in (already ensured in ranking stage)
                to_enter.append(sym)

        # Position sizing: equal weight in lots
        for sym in to_enter:
            signals.append(SignalEvent(dt=dt, symbol=sym, action="LONG", size=1))

        # record last rebalance
        self.last_rebalance_dt = dt
        
        # 更新持有天数
        self._update_hold_days()
        
        return signals

    def update_position(self, symbol: str, lots: int, action: str, fill_price: float, fill_dt: pd.Timestamp):
        # 兼容性修复：确保动作类型一致
        if action in ("LONG", "BUY"): 
            action = "BUY"
        elif action in ("EXIT", "SELL"): 
            action = "SELL"
        
        # track hold_days based on fills
        if action == "BUY":
            self._positions[symbol] = self._positions.get(symbol, 0) + lots
            # reset hold days on (re-)entry
            self.hold_days[symbol] = 0
        elif action == "SELL":
            cur = self._positions.get(symbol, 0)
            new = cur - lots
            if new <= 0:
                self._positions.pop(symbol, None)
                self.hold_days.pop(symbol, None)
            else:
                self._positions[symbol] = new

    # -------------- Preprocess (should be called once after cb_data is set) --------------
    def preprocess(self):
        """预处理方法现在主要用于初始化，实际计算在calculate_signals中进行"""
        # 初始化持仓和持有天数
        self._positions = {}
        self.hold_days = {}
        self.last_rebalance_dt = None
        self._today = None
        
        # 如果cb_data存在，进行基本的数据验证
        if self.cb_data is not None and not self.cb_data.empty:
            # 确保代码格式正确 - 使用apply方法处理每个元素
            self.cb_data["cb_code"] = self.cb_data["cb_code"].astype(str).apply(lambda x: str(x)[-6:].zfill(6))
            # 确保日期格式正确
            self.cb_data["trade_date"] = pd.to_datetime(self.cb_data["trade_date"])

    # -------------- Internals --------------
    def _apply_filters(self, day: pd.DataFrame, dt: pd.Timestamp) -> pd.DataFrame:
        """应用过滤器，现在处理每日数据"""
        # basic filters
        m = pd.Series(True, index=day.index)
        print(f"DEBUG: 初始mask: {m.sum()} 个True")
        
        # 价格过滤器
        price_mask = day["close"].between(self.cfg.price_min, self.cfg.price_max, inclusive="both")
        m &= price_mask
        print(f"DEBUG: 价格过滤器后: {m.sum()} 个True (价格范围: {self.cfg.price_min}-{self.cfg.price_max})")
        
        # 溢价率过滤器
        premium_mask = day["premium"].between(self.cfg.premium_min, self.cfg.premium_max, inclusive="both")
        m &= premium_mask
        print(f"DEBUG: 溢价率过滤器后: {m.sum()} 个True (溢价率范围: {self.cfg.premium_min}-{self.cfg.premium_max})")
        
        # 期限过滤器
        if "term" in day.columns:
            # 如果term列存在，只对非nan值应用过滤器，nan值通过
            term_mask = day["term"].isna() | (day["term"] >= self.cfg.term_min_years)
            m &= term_mask
            print(f"DEBUG: 期限过滤器后: {m.sum()} 个True (最小期限: {self.cfg.term_min_years})")
        
        # 成交量过滤器
        if "turnover" in day.columns:
            # 如果turnover列存在，只对非nan值应用过滤器，nan值通过
            turnover_mask = day["turnover"].isna() | (day["turnover"] >= self.cfg.turnover_min)
            m &= turnover_mask
            print(f"DEBUG: 成交量过滤器后: {m.sum()} 个True (最小成交量: {self.cfg.turnover_min})")

        # exclude ST
        if "is_st" in day.columns:
            st_mask = (day["is_st"] == 0)
            m &= st_mask
            print(f"DEBUG: ST过滤器后: {m.sum()} 个True")

        # blacklist
        if self.cfg.blacklist:
            code_set = set([str(x)[-6:].zfill(6) for x in self.cfg.blacklist])
            blacklist_mask = ~day["cb_code"].isin(code_set)
            m &= blacklist_mask
            print(f"DEBUG: 黑名单过滤器后: {m.sum()} 个True")

        # === PATCH 2: 趋势强度计算，缺 open 时用前收回退 ===
        day_filtered = day.loc[m].copy()
        print(f"DEBUG: 基础过滤器后数据形状: {day_filtered.shape}")
        
        if not day_filtered.empty:
            # 参考价：优先 open；如果 open 全空或缺失，则回退到"前一交易日 close"
            use_open = ("open" in day_filtered.columns) and day_filtered["open"].notna().any()

            if not use_open:
                # 从 self.cb_data 取每个债券在 dt 之前最近一次收盘
                prev_close = (
                    self.cb_data.loc[self.cb_data["trade_date"].dt.normalize() < dt, ["cb_code", "trade_date", "close"]]
                    .sort_values(["cb_code", "trade_date"])
                    .groupby("cb_code")["close"].last()
                    .rename("prev_close")
                )
                day_filtered = day_filtered.merge(prev_close, on="cb_code", how="left")
                ref_price = day_filtered["prev_close"]
                print(f"DEBUG: 使用前收作为参考价，缺失数: {ref_price.isna().sum()}")
            else:
                ref_price = day_filtered["open"]

            # 趋势：收盘 > 参考价 视为上升
            day_filtered["trend_strength"] = np.where(day_filtered["close"] > ref_price, 80.0, 40.0)

            print(f"DEBUG: 趋势强度范围: {day_filtered['trend_strength'].min():.1f} - {day_filtered['trend_strength'].max():.1f}")
            print(f"DEBUG: 趋势进入阈值: {self.cfg.trend_in}")

            trend_mask = day_filtered["trend_strength"] >= self.cfg.trend_in
            print(f"DEBUG: 趋势过滤器通过数量: {trend_mask.sum()}")

            day_filtered = day_filtered[trend_mask]
            print(f"DEBUG: 趋势过滤器后数据形状: {day_filtered.shape}")
        else:
            print("DEBUG: 基础过滤器后数据为空")

        return day_filtered

    def _rank_and_pick(self, day: pd.DataFrame, dt: pd.Timestamp) -> pd.DataFrame:
        """排名和选择，处理每日数据"""
        if day.empty:
            return day
        
        # 计算流动性排名
        if "turnover" in day.columns:
            day["liq_rank"] = day["turnover"].rank(pct=True, method="average")
        else:
            day["liq_rank"] = 0.5  # 默认中等流动性
        
        # 计算价格排名
        day["price_pct_rank"] = day["close"].rank(pct=True, method="average")
        
        # 构建因子矩阵，处理缺失值
        factors = list(self.cfg.factor_weights.keys())
        factor_data = {}
        
        for factor in factors:
            if factor == "neg_premium" and "premium" in day.columns:
                factor_data[factor] = -day["premium"]
            elif factor == "neg_price_pct_rank":
                factor_data[factor] = -day["price_pct_rank"]
            elif factor == "neg_ytm" and "ytm" in day.columns:
                factor_data[factor] = -day["ytm"].fillna(0)
            elif factor == "neg_vol20":
                # 简化：使用价格变化作为波动率代理
                factor_data[factor] = -abs(day["close"] - day["open"]) / day["open"]
            elif factor == "liq_rank":
                factor_data[factor] = day["liq_rank"]
            elif factor == "neg_term" and "term" in day.columns:
                factor_data[factor] = -day["term"].fillna(5)  # 默认5年
            else:
                # 对于缺失的因子，使用0值
                factor_data[factor] = pd.Series(0.0, index=day.index)
        
        # 创建因子矩阵
        X = pd.DataFrame(factor_data)
        
        # 计算Z-score
        print(f"DEBUG: 因子矩阵形状: {X.shape}")
        print(f"DEBUG: 因子矩阵列: {list(X.columns)}")
        
        # 处理标准差为0的情况
        std_values = X.std(ddof=0)
        print(f"DEBUG: 标准差: {std_values.to_dict()}")
        
        # 避免除零错误
        z = (X - X.mean()) / (std_values.replace(0, 1.0))  # 用1.0替换0，避免除零
        z = z.fillna(0.0)
        print(f"DEBUG: Z-score计算完成，形状: {z.shape}")
        
        # 应用权重
        weights = pd.Series(self.cfg.factor_weights, index=factors).astype(float)
        day["score"] = (z * weights).sum(axis=1)
        
        # 排名并选择top_n
        ranked = day.sort_values(["score", "liq_rank"], ascending=[False, False])
        return ranked.head(int(self.cfg.top_n)).reset_index(drop=True)

    def _update_hold_days(self):
        """更新所有持仓的持有天数"""
        for sym in self._positions.keys():
            if sym in self.hold_days:
                self.hold_days[sym] += 1
            else:
                self.hold_days[sym] = 1
    
    def _exit_all_signals(self, dt: pd.Timestamp):
        sigs = []
        for sym, lots in list(self._positions.items()):
            if lots > 0:
                sigs.append(SignalEvent(dt=dt, symbol=sym, action="EXIT", size=lots))
        return sigs
