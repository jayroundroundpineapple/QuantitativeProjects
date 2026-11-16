"""财务因子数据：ROE 与净利润同比，按「公告可用日」对齐，避免未来函数。

数据源：东方财富「业绩报表」 ak.stock_yjbb_em(date=报告期)
  · 一次请求覆盖全市场一个报告期（5000+ 行，3~6 秒），比逐只抓财务指标快两个数量级
  · 自带「净资产收益率」「净利润-同比增长」「所处行业」，一次拿齐质量+成长两个因子

为什么不用表里的「最新公告日期」当可用日：
  该字段是**最新一次**公告（含更正公告）的日期。实测 2023 年中报里有标的被标到 2026 年，
  直接拿它对齐会让历史因子永远缺席——过度保守等于没有因子。
  所以改用**法定披露截止日**作为可用日：
      Q1 → 4/30      H1 → 8/31      Q3 → 10/31      年报 → 次年 4/30
  这对所有股票一致，且一定不早于真实公告日：宁可晚用，不可早用。

ROE 口径：报表给的是**年内累计** ROE，Q1 的 7% 和年报的 7% 不是一回事。
  统一年化（×4/季度序号）只用于横截面排序，不用于绝对估值判断。
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from .realdata import CACHE

# 报告期 → 法定披露截止日滞后天数（都落在月末，年内顺序固定）
QLAG = {"0331": 30, "0630": 62, "0930": 31, "1231": 120}
MAX_STALE_DAYS = 400          # 财报超过这么久没更新（退市/长期停牌）就当缺失


def _periods(start: str, end: str) -> list[tuple[str, pd.Timestamp, int]]:
    """列出 [start, end] 期间「按披露截止日算已经可用」的报告期。"""
    d0, d1 = pd.Timestamp(start), pd.Timestamp(end)
    out = []
    for y in range(d0.year - 1, d1.year + 1):
        for q, mmdd in enumerate(("0331", "0630", "0930", "1231"), start=1):
            avail = pd.Timestamp(f"{y}-{mmdd[:2]}-{mmdd[2:]}") + pd.Timedelta(days=QLAG[mmdd])
            if avail <= d1 and pd.Timestamp(f"{y}-{mmdd[:2]}-01") <= d1:
                out.append((f"{y}{mmdd}", avail, q))
    return out


def _tag(codes: list[str]) -> str:
    return hashlib.md5("|".join(sorted(codes)).encode()).hexdigest()[:8]


def fetch_period(period: str, use_cache: bool = True) -> pd.DataFrame:
    """抓单个报告期的全市场业绩快报，只保留因子需要的列。"""
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    fp = CACHE / f"yjbb_{period}.pkl"
    if use_cache and fp.exists():
        return pd.read_pickle(fp)

    df = ak.stock_yjbb_em(date=period)
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "roe", "growth", "industry"])

    out = pd.DataFrame({
        "code": df["股票代码"].astype(str).str.zfill(6),
        "name": df.get("股票简称"),
        "roe": pd.to_numeric(df.get("净资产收益率"), errors="coerce"),
        "growth": pd.to_numeric(df.get("净利润-同比增长"), errors="coerce"),
        "industry": df.get("所处行业"),
    })
    out = out.drop_duplicates("code", keep="first")
    pd.to_pickle(out, fp)
    return out


def fetch_fundamentals(codes: list[str], start: str, end: str,
                       use_cache: bool = True, progress=None) -> pd.DataFrame:
    """返回长表：date(可用日) / code / period / roe(年化) / growth / industry。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    fp = CACHE / f"fund_{start}_{end}_{len(codes)}_{_tag(codes)}.pkl"
    if use_cache and fp.exists():
        return pd.read_pickle(fp)

    plist = _periods(start, end)
    want = set(codes)
    rows = []
    for i, (period, avail, q) in enumerate(plist):
        if progress:
            progress(i + 1, len(plist), period)
        try:
            df = fetch_period(period, use_cache=use_cache)
        except Exception:
            continue                      # 单个报告期失败不该毁掉整个回测
        if df.empty:
            continue
        d = df[df["code"].isin(want)].copy()
        if d.empty:
            continue
        d["date"] = avail
        d["period"] = period
        d["roe"] = d["roe"] * (4.0 / q)    # 年内累计 → 年化近似
        rows.append(d[["date", "code", "period", "roe", "growth", "industry", "name"]])

    cols = ["date", "code", "period", "roe", "growth", "industry", "name"]
    out = (pd.concat(rows, ignore_index=True).sort_values(["date", "code"])
           if rows else pd.DataFrame(columns=cols))
    pd.to_pickle(out, fp)
    return out


def align(fin: pd.DataFrame, index: pd.DatetimeIndex, codes: list[str],
          col: str, max_stale_days: int = MAX_STALE_DAYS) -> pd.DataFrame:
    """把稀疏的财报长表按可用日向前填充成「日期 × 代码」的日频面板。

    超过 max_stale_days 没更新的事实上已经不成立（退市、长期停牌、漏披露），置空。
    """
    cols = [c for c in codes if c in set(fin["code"])]
    out = pd.DataFrame(np.nan, index=index, columns=codes, dtype=float)
    if not cols or fin.empty:
        return out

    idx = index.values
    for code, g in fin[fin["code"].isin(cols)].groupby("code"):
        s = g.dropna(subset=[col]).sort_values("date")
        if s.empty:
            continue
        pos = np.searchsorted(s["date"].values, idx, side="right") - 1
        ok = pos >= 0
        if not ok.any():
            continue
        vals = np.where(ok, s[col].values[np.clip(pos, 0, None)], np.nan)
        age = (idx - s["date"].values[np.clip(pos, 0, None)]) / np.timedelta64(1, "D")
        vals[~ok | (age > max_stale_days)] = np.nan
        out[code] = vals
    return out


def latest_meta(fin: pd.DataFrame) -> dict:
    """给界面用：最新一期覆盖了多少只、哪一期。"""
    if fin.empty:
        return {"period": None, "codes": 0, "industry": {}, "name": {}}
    last = fin["date"].max()
    tail = fin[fin["date"] == last]
    ind = tail["industry"] if "industry" in tail else pd.Series(dtype=object)
    nm = tail["name"] if "name" in tail else pd.Series(dtype=object)
    return {
        "period": str(tail["period"].iloc[0]),
        "avail": str(last.date()),
        "codes": int(tail["code"].nunique()),
        "industry": dict(zip(tail["code"], ind.fillna("—"))),
        "name": dict(zip(tail["code"], nm.fillna("—"))),
    }
