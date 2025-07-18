"""
fetch_cb_data.py — 全量 / 增量两用
---------------------------------
第一次运行：生成 data/cb_SH_full.parquet & data/cb_SZ_full.parquet
后续运行：   仅抓取各标的 max(date)+1 之后的新行并追加
"""

import os, time, akshare as ak, pandas as pd
from pathlib import Path

# ------------------------------------------------------------
# 0. 参数
# ------------------------------------------------------------
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
PARQ_SH  = DATA_DIR / "cb_SH_full.parquet"
PARQ_SZ  = DATA_DIR / "cb_SZ_full.parquet"
N_RETRY  = 3                      # 单只债券重试次数

# ------------------------------------------------------------
# 1. 获取应有代码清单
# ------------------------------------------------------------
spot_df  = ak.bond_zh_hs_cov_spot()
code_col = "symbol" if "symbol" in spot_df.columns else "bond_code"
codes    = spot_df[code_col].astype(str).tolist()

# ------------------------------------------------------------
# 2. 读取旧数据（若存在），准备"最新日期"字典
# ------------------------------------------------------------
def load_parq(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() and path.stat().st_size else pd.DataFrame()

old_sh, old_sz = load_parq(PARQ_SH), load_parq(PARQ_SZ)
old_all = pd.concat([old_sh, old_sz], ignore_index=True)      # 可能为空

last_date = (
    old_all.groupby("symbol")["date"].max()
           .to_dict()
) if not old_all.empty else {}

print(f"📜  旧数据：{old_all.shape}, 已含 {len(last_date)} 只可转债")

# ------------------------------------------------------------
# 3. 抓取函数：拿到全表后只保留"> last_date"的新行
# ------------------------------------------------------------
def fetch_one(code, retry=N_RETRY):
    for _ in range(retry):
        try:
            raw = ak.bond_zh_hs_cov_daily(symbol=code)
            break
        except Exception as e:
            print(f"{code} 访问失败：{e}; 重试…")
            time.sleep(0.5)
    else:
        return None                        # 多次失败

    if raw is None or raw.empty:
        return None

    # --- 统一日期列 ---
    for cand in ["date", "trade_date", "日期", "day", "datetime"]:
        if cand in raw.columns:
            raw = raw.rename(columns={cand: "date"})
            break
    else:
        print(f"{code} 无日期列，跳过")
        return None

    raw["date"]   = pd.to_datetime(raw["date"], errors="coerce")
    raw["symbol"] = code

    # --- 增量过滤 ---
    cutoff = last_date.get(code)
    if cutoff is not None:
        raw = raw[raw["date"] > cutoff]

    return raw if not raw.empty else None

# ------------------------------------------------------------
# 4. 执行抓取 & 汇总
# ------------------------------------------------------------
new_frames = []
for code in codes:
    df_new = fetch_one(code)
    if df_new is not None:
        new_frames.append(df_new)

print(f"🆕  本轮抓到 {len(new_frames)} 只，合计 {sum(len(df) for df in new_frames):,} 行增量")

if not new_frames and old_all.empty:
    raise SystemExit("没有任何数据，可能接口异常；稍后再试吧。")

# ------------------------------------------------------------
# 5. 合并旧表 + 新表 → 去重 → 拆分写入
# ------------------------------------------------------------
all_data = (
    pd.concat([old_all, *new_frames], ignore_index=True)
      .drop_duplicates(subset=["date", "symbol"])
      .assign(exchange=lambda d: d["symbol"].str[:2].str.lower())
      .sort_values(["date", "symbol"])
)

# —— 写入 —— #
all_data.query("exchange == 'sh'").to_parquet(
    PARQ_SH, compression="zstd", index=False
)
all_data.query("exchange == 'sz'").to_parquet(
    PARQ_SZ, compression="zstd", index=False
)

print(f"✅  更新完成！总行数：{len(all_data):,} — 上海 {len(all_data.query('exchange=="sh"')):,} / 深圳 {len(all_data.query('exchange=="sz"')):,}")

# 后续可能的改进：
# - 字段检查：如需确保 open/high/low/close/volume 等字段完整，可加断言或 try/except 检查
# - 增量更新：如需每日增量，可先读本地数据再 append 新数据
# - 异常值处理：后续分析/回测时可处理极端价格或缺失值
