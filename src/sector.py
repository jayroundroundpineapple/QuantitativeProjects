"""行业轮动因子：把"电网设备三只"做成一个可回测的择时信号。

思路不是"猜电网好不好"，而是问一个能被数据回答的问题：
    这个板块相对全市场的强度，是在扩散还是在收敛？

信号构造（全部用调仓日之前的数据，无未来函数）：
    RS(t, w) = 板块等权指数近 w 日收益 - 全市场等权近 w 日收益
    score(t) = 0.5 * z(RS20) + 0.5 * z(RS60)
    score > 0 → 持有板块组合；否则 → 回到全市场多因子组合

同时给出三条对照线：纯持板块、纯市场等权、轮动。这样你才能看出
"轮动"到底是贡献了 alpha，还是只是少亏。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import Config, Result, run

GRID_MEMBERS = ["600312", "002028", "600089"]   # 平高电气 / 思源电气 / 特变电工


def sector_index(close: pd.DataFrame, members: list[str]) -> pd.Series:
    """板块等权指数（归一化到 1）。成员缺失的日期按可得成员等权。"""
    cols = [c for c in members if c in close.columns]
    if not cols:
        return pd.Series(dtype=float)
    ret = close[cols].pct_change()
    return (1 + ret.mean(axis=1).fillna(0)).cumprod()


def market_index(close: pd.DataFrame) -> pd.Series:
    ret = close.pct_change()
    return (1 + ret.mean(axis=1).fillna(0)).cumprod()


def strength(close: pd.DataFrame, members: list[str],
             windows: tuple[int, int] = (20, 60)) -> pd.DataFrame:
    """返回每日的 RS 与综合强度分。"""
    sec, mkt = sector_index(close, members), market_index(close)
    out = pd.DataFrame(index=close.index)
    for w in windows:
        out[f"rs{w}"] = (sec / sec.shift(w) - 1) - (mkt / mkt.shift(w) - 1)
    z = out.apply(lambda s: (s - s.expanding(120).mean()) / s.expanding(120).std())
    out["score"] = 0.5 * z[f"rs{windows[0]}"] + 0.5 * z[f"rs{windows[1]}"]
    out["sector"] = sec
    out["market"] = mkt
    return out


def rotation_selector(close: pd.DataFrame, members: list[str],
                      threshold: float = 0.0):
    """返回一个 selector 闭包，供 engine.run 在每个调仓日调用。"""
    st = strength(close, members)["score"]

    def _sel(date, close_, cs):
        if date not in st.index or pd.isna(st.loc[date]):
            return None
        if st.loc[date] <= threshold:
            return None                      # 强度不足 -> 交给多因子
        live = [c for c in members if c in close_.columns
                and not pd.isna(close_.at[date, c])]
        return live if len(live) >= 2 else None

    return _sel


def run_rotation(panel: dict, members: list[str] | None = None,
                 cfg: Config | None = None, threshold: float = 0.0) -> dict:
    """跑三条线并返回结果，便于直接对比。"""
    members = members or GRID_MEMBERS
    cfg = cfg or Config(top_n=20)
    close = panel["close"]

    rot = run(panel, cfg, selector=rotation_selector(close, members, threshold))

    only = {**panel, "fundamentals": panel["fundamentals"]}
    sub = {k: (v[[c for c in members if c in v.columns]]
               if isinstance(v, pd.DataFrame) and k != "fundamentals" else v)
           for k, v in only.items()}
    sector_only = run(sub, Config(top_n=len(members), weights=cfg.weights,
                                  start=cfg.start, end=cfg.end, capital=cfg.capital))

    return {"rotation": rot, "sector_only": sector_only,
            "strength": strength(close, members)}
