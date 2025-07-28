"""
constants.py · v0.3
--------------------------------------------------------------------
全局常量 & 字段映射
✓ 双向列名映射（中文 ↔ 英文）
✓ 交易参数：默认值 + 交易所差异化费率
✓ 税费 / 强赎 / 回售 等制度阈值
✓ 可通过环境变量覆写关键参数
✓ 交易日历加载工具（本地 CSV 或 tushare 在线）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict

import pandas as pd

# ------------------------------------------------------------------
# 1. 字段映射  —— 按你的 CB_Data_Dictionary.txt 补充完整
# ------------------------------------------------------------------
FIELD_MAP_CN2EN: Dict[str, str] = {
    "symbol": "cb_code",
    "date": "trade_date",
    "close": "close",
    "volume": "volume",
    # 如有更多列继续补
    "transfer_price": "convert_price",
    "transfer_premium_ratio": "premium",
    "convert_stock_pricehq": "stk_close",
    # 静态表相关字段
    "SECUCODE": "cb_code",
    "CONVERT_STOCK_CODE": "stk_code",
    "TRANSFER_PRICE": "convert_price",
    "TRANSFER_PREMIUM_RATIO": "premium",
    # …（按需添加）
}

FIELD_MAP_EN2CN: Dict[str, str] = {v: k for k, v in FIELD_MAP_CN2EN.items()}


def rename_columns(df: pd.DataFrame, to_en: bool = True) -> pd.DataFrame:
    """
    DataFrame 列名批量中⇄英翻译
    Parameters
    ----------
    df    : 原始 DataFrame
    to_en : True=中文→英文；False=英文→中文
    """
    mapper = FIELD_MAP_CN2EN if to_en else FIELD_MAP_EN2CN
    return df.rename(columns=mapper)


# 可转债代码标准化工具
_CB_PAT = re.compile(r"^(?P<ex>[a-z]{2})?(?P<code>\d{6})$", re.I)

def normalise_cb_code(raw: str) -> str:
    """
    把各种可转债代码格式统一成 "110059.SH / 128044.SZ" 这样的 6 位数字 + 点 + 交易所后缀。
    目前支持：
      • "sz128044" / "SZ128044"
      • "110059.SH" / "110059" （默认按首位区分交易所）
    """
    raw = str(raw).strip().lower()
    
    # 如果已经包含交易所后缀，直接处理
    if '.' in raw:
        parts = raw.split('.')
        if len(parts) == 2:
            code, ex = parts[0], parts[1]
            if ex in ['sh', 'sz']:
                return f"{code}.{ex.upper()}"
    
    # 移除所有点号后处理
    raw_clean = raw.replace('.', '')
    m = _CB_PAT.match(raw_clean)
    if not m:
        raise ValueError(f"Un-recognised CB code: {raw}")

    code, ex = m["code"], m["ex"]
    if ex in {"sz", "szse"} or code.startswith("12"):
        return f"{code}.SZ"
    # 默认当作上交所
    return f"{code}.SH"


# ------------------------------------------------------------------
# 2. 交易参数  —— 默认 & 交易所差异
# ------------------------------------------------------------------
# 2.1 交易所费率 / 单位
EXCHANGE_RULES: Dict[str, dict] = {
    # LOT：最小张数；各费率均为"小数"表达（0.0001 = 0.01%）
    "SSE": {
        "LOT": 10,
        "COMMISSION_RATE": 0.0001,      # 成交金额 × 0.01%
        "COMMISSION_MAX": 0.0002,       # 上限 0.02%
        "HANDLING_FEE": 0.000001,       # 过户费
    },
    "SZSE": {
        "LOT": 10,
        "COMMISSION_RATE": 0.0001,
        "COMMISSION_MAX": 0.001,        # 上限 0.10%
        "HANDLING_FEE": 0.00004,
    },
}

# 2.2 税费（全国统一）
STAMP_DUTY_STOCK_SELL: float = 0.001       # 股票卖出印花税 0.1%

# 2.3 制度阈值
FORCE_REDEEM_TRIGGER: float = 1.30         # 强赎触发价 ≥130% 面值
FORCE_REDEEM_DAYS: int = 30                # 连续 n 个交易日
PUT_TRIGGER: float = 0.70                  # 回售触发价 ≤70% 面值
PUT_DAYS: int = 30

# ------------------------------------------------------------------
# 3. Config ：可运行默认参数（env 可覆盖）
# ------------------------------------------------------------------
@dataclass
class Config:
    """框架运行级别默认值"""
    LOT_SIZE: int = 10
    DEFAULT_SLIPPAGE_BPS: int = 2          # 买 +2bps / 卖 −2bps
    COMMISSION_RATE: float = 0.0001
    MIN_COMMISSION: float = 1.0
    TRADING_CALENDAR: List[str] = None     # 自动加载后赋值

    @classmethod
    def override_from_env(cls) -> None:
        """允许通过环境变量覆盖 LOT_SIZE / COMMISSION 等"""
        for attr in ("LOT_SIZE", "DEFAULT_SLIPPAGE_BPS",
                     "COMMISSION_RATE", "MIN_COMMISSION"):
            if attr in os.environ:
                val_type = type(getattr(cls, attr))
                setattr(cls, attr, val_type(os.environ[attr]))


Config.override_from_env()

# ------------------------------------------------------------------
# 4. 交易日历工具
# ------------------------------------------------------------------
def load_trading_calendar(
    csv_path: str | Path | None = None,
    force_reload: bool = False
) -> None:
    """
    加载交易日列表至 Config.TRADING_CALENDAR
    - 优先读取本地 CSV（单列日期，格式 YYYYMMDD 或 YYYY-MM-DD）
    - 若 csv_path 为空则尝试 tushare 在线接口
    """
    if Config.TRADING_CALENDAR and not force_reload:
        return

    dates: List[str] = []
    if csv_path and Path(csv_path).exists():
        df = pd.read_csv(csv_path, header=None)
        dates = df.iloc[:, 0].astype(str).str.replace("-", "").tolist()
    else:
        try:
            import tushare as ts
            pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))
            df = pro.trade_cal(exchange="", start_date="19900101")
            dates = df[df["is_open"] == 1]["cal_date"].astype(str).tolist()
        except Exception:
            pass  # 在线加载失败则留空，由调用方自行决定是否跳过节假日判断

    Config.TRADING_CALENDAR = dates


def is_trading_day(date_str: str) -> bool:
    """
    判断是否交易日
    请先确保调用过 load_trading_calendar()
    """
    if not Config.TRADING_CALENDAR:   # 若未加载，默认视为交易日
        return True
    return date_str.replace("-", "") in Config.TRADING_CALENDAR 