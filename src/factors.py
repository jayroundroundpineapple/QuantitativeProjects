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

    # 各因子的百分位单独留一列：排名表要能解释「为什么是这个名字次」
    df["p_value"] = _rank_pct(df["ep"])
    df["p_quality"] = _rank_pct(df["roe"])
    df["p_growth"] = _rank_pct(df["growth"])
    df["p_mom"] = _rank_pct(df["mom"])

    # 因子覆盖不足 30% 时把它当「未接入」：权重让给其余因子，而不是按 0.5 硬填。
    # 硬填虽然不改排序（常数会被标准化消掉），但会让人误以为这个因子算过了。
    cols = {"value": "ep", "quality": "roe", "growth": "growth", "momentum": "mom"}
    n = max(len(df), 1)
    avail = {k: float(df[c].notna().sum() / n >= 0.3) for k, c in cols.items()}
    tot_w = sum(weights[k] for k in weights if avail[k]) or 1.0

    df["score"] = sum(
        weights[k] * df["p_" + ("mom" if k == "momentum" else k)] * avail[k]
        for k in weights
    ) / tot_w
    df.attrs["factor_available"] = avail
    z = df["score"] - 0.5
    df["score"] = z / (z[df["eligible"]].std() or 1.0)
    return df
