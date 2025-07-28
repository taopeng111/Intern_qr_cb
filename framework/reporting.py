"""
reporting.py · TearSheetReporter v0.1
----------------------------------------------------------------------
✓ 收集 (dt, nav) → DataFrame
✓ 调用 perf_metrics.calc_metrics() 生成绩效指标
✓ 输出：
   ├─ nav_history.csv          每日净值
   ├─ perf_metrics.csv         指标表
   ├─ cumulative_nav.png       累计收益曲线
   └─ drawdown.png             回撤曲线
★ 若安装 openpyxl，可同时写 Excel（自动多 Sheet）
----------------------------------------------------------------------"""

from __future__ import annotations

import logging
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

import pandas as pd
import matplotlib.pyplot as plt

import perf_metrics


class TearSheetReporter:
    """
    Parameters
    ----------
    out_dir   : str | Path    输出目录；默认 'output/YYYYMMDD_HHMMSS'
    dpi       : int           图像分辨率
    log_level : int
    """

    def __init__(
        self,
        out_dir: str | Path | None = None,
        dpi: int = 150,
        log_level: int = logging.INFO,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(log_level)
        if not self.logger.handlers:
            self.logger.addHandler(logging.StreamHandler())

        if out_dir is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path("output") / ts
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.dpi = dpi
        self._records: List[Tuple[pd.Timestamp, float]] = []

    # ------------------------------------------------------------------
    # Engine / Portfolio 调用
    # ------------------------------------------------------------------
    def collect_nav(self, dt: pd.Timestamp, nav: float) -> None:
        self._records.append((dt, nav))

    # ------------------------------------------------------------------
    # 入口：引擎结束后调用
    # ------------------------------------------------------------------
    def output(self) -> None:
        if not self._records:
            self.logger.warning("No NAV records collected — nothing to output.")
            return

        df = pd.DataFrame(self._records, columns=["datetime", "nav"]).set_index("datetime")
        df.sort_index(inplace=True)
        df.to_csv(self.out_dir / "nav_history.csv", float_format="%.6f")

        # -------- 1. 绩效指标 --------
        returns = df["nav"].pct_change().dropna()         # 将净值转成日收益率
        metrics = perf_metrics.calc_metrics(
            returns.to_frame("strategy")                  # 列名随意
        )
        # metrics 是 DataFrame，取第一行（策略）转为 Series
        metrics_series = metrics.iloc[0] if isinstance(metrics, pd.DataFrame) else metrics
        metrics_series.to_csv(self.out_dir / "perf_metrics.csv", float_format="%.6f")

        # 若 openpyxl 可用，写 Excel 多 Sheet
        try:
            import openpyxl  # noqa: F401
            with pd.ExcelWriter(self.out_dir / "report.xlsx") as writer:
                df.to_excel(writer, sheet_name="NAV")
                metrics_series.to_frame("value").to_excel(writer, sheet_name="metrics")
        except ModuleNotFoundError:
            pass  # 忽略 Excel 输出

        # -------- 2. 累计收益曲线 --------
        plt.figure(figsize=(8, 4))
        (df["nav"] / df["nav"].iloc[0]).plot()
        plt.title("Cumulative NAV")
        plt.xlabel("")
        plt.ylabel("NAV (normalized)")
        plt.tight_layout()
        plt.savefig(self.out_dir / "cumulative_nav.png", dpi=self.dpi)
        plt.close()

        # -------- 3. 回撤曲线 --------
        cum_max = (df["nav"] / df["nav"].iloc[0]).cummax()
        drawdown = (df["nav"] / df["nav"].iloc[0]) / cum_max - 1.0
        plt.figure(figsize=(8, 3))
        drawdown.plot(kind="area", color="red", alpha=0.5)
        plt.title("Drawdown")
        plt.ylabel("")
        plt.tight_layout()
        plt.savefig(self.out_dir / "drawdown.png", dpi=self.dpi)
        plt.close()

        self.logger.info("Report saved to: %s", self.out_dir.resolve()) 