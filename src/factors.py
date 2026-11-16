"""因子层：截面打分，全部用排名百分位（rank pct），对极端值天然不敏感。"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_WEIGHTS = {"value": 0.30, "quality": 0.25, "growth": 0.25, "momentum": 0.20}


def _rank_pct(s: pd.Series) -> pd.Series:
    """转成 0~1 的百分位，NaN 给 0.5（中性），避免缺失值被当成最差而误杀。"""
    r = s.rank(pct=True)
    return r.fillna(0.5)


def cross_section(date: pd.Timestamp,
                  close: pd.DataFrame,
                  fundamentals: pd.DataFrame,
                  weights: dict | None = None,
                  min_price: float = 3.0,
                  min_history: int = 120) -> pd.DataFrame:
    """在 date 这一天，对全市场算四个因子的截面得分。

    返回 DataFrame(index=code)，列：ep, roe, growth, mom, score, eligible
    """
    weights = weights or DEFAULT_WEIGHTS
    i = close.index.get_loc(date)
    if i < min_history:
        return pd.DataFrame(columns=["ep", "roe", "growth", "mom", "score", "eligible"])

    px = close.iloc[i]
    px_60 = close.iloc[i - 60]
    prev = close.iloc[i - 1]

    fu = fundamentals[fundamentals["date"] <= date]
    if fu.empty:
        return pd.DataFrame(columns=["ep", "roe", "growth", "mom", "score", "eligible"])
    fu = fu.sort_values("date").groupby("code").tail(1).set_index("code")

    df = pd.DataFrame(index=px.index)
    df["price"] = px
    df["ep"] = np.where(fu["pe_ttm"].reindex(df.index) > 0,
                        1.0 / fu["pe_ttm"].reindex(df.index), np.nan)
    df["roe"] = fu["roe_ttm"].reindex(df.index)
    df["growth"] = fu["net_profit_yoy"].reindex(df.index)
    df["mom"] = (px / px_60 - 1).reindex(df.index)

    # 可交易性：有报价、未停牌、价格不过低、有 60 日历史
    df["eligible"] = (
        px.notna() & prev.notna() & (px >= min_price)
        & df["mom"].notna() & df["ep"].notna()
    )

    df["score"] = (
        weights["value"] * _rank_pct(df["ep"])
        + weights["quality"] * _rank_pct(df["roe"])
        + weights["growth"] * _rank_pct(df["growth"])
        + weights["momentum"] * _rank_pct(df["mom"])
    )
    z = df["score"] - 0.5
    df["score"] = z / (z[df["eligible"]].std() or 1.0)
    return df
