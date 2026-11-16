"""数据层：统一成宽表，任何数据源只要按这个契约喂进来就能回测。

契约（宽表，index = 交易日，columns = 股票代码）：
    close, open, limit_up, limit_down, suspended, volume

基本面（长表）：
    date, code, pe_ttm, pb, roe_ttm, net_profit_yoy
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

PRICE_FIELDS = ["open", "close", "limit_up", "limit_down", "suspended", "volume"]
FUND_FIELDS = ["pe_ttm", "pb", "roe_ttm", "net_profit_yoy"]


def synthetic(n_stocks: int = 120,
              n_days: int = 1050,
              seed: int = 7,
              start: str = "2021-01-04") -> dict:
    """合成数据。内置一个真实有效的价值因子（v 越大估值越低、未来漂移越高），
    用来验证整条流水线能否把已知的信号抓出来。如果回测跑不出超额，
    说明代码有 bug，而不是市场没机会。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n_days)
    codes = [f"{600000 + i:06d}" for i in range(n_stocks)]

    v = rng.normal(0, 1, n_stocks)            # 潜在价值暴露
    beta = rng.uniform(0.7, 1.35, n_stocks)   # 市场 beta
    mkt = rng.normal(0.00022, 0.0095, n_days)
    drift = 0.00010 + 0.00060 * v             # 价值带来的真实 alpha（±1σ 年化约 30%）
    idio = rng.normal(0, 0.016, (n_days, n_stocks))
    ret = drift[None, :] + beta[None, :] * mkt[:, None] + idio

    close = pd.DataFrame(12 * np.exp(np.cumsum(np.log1p(ret), axis=0)),
                         index=dates, columns=codes).round(2)
    gap = rng.normal(0, 0.004, (n_days, n_stocks))
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + gap)

    prev = close.shift(1)
    limit_up = (prev * 1.10).round(2)
    limit_down = (prev * 0.90).round(2)
    suspended = pd.DataFrame(rng.random((n_days, n_stocks)) < 0.0015,
                             index=dates, columns=codes)
    volume = pd.DataFrame(np.exp(rng.normal(15.5, 0.7, (n_days, n_stocks))).round(0),
                          index=dates, columns=codes)
    volume[suspended] = 0.0

    month_end = pd.Series(dates, index=dates).groupby(dates.to_period("M")).max()
    snaps = sorted(set(month_end.values))
    base_pe = 46 * np.exp(-0.62 * v)          # 低估值 <-> 高 v
    rows = []
    for d in snaps:
        px = close.loc[d].values
        pe = base_pe * (px / close.iloc[0].values) * np.exp(rng.normal(0, 0.06, n_stocks))
        roe = 8.5 + 3.2 * v + rng.normal(0, 1.6, n_stocks)
        g = 9.0 + 2.4 * v + rng.normal(0, 9.0, n_stocks)
        rows.append(pd.DataFrame({
            "date": d, "code": codes,
            "pe_ttm": np.clip(pe, 3, 400),
            "pb": np.clip(pe * roe / 100, 0.3, 30),
            "roe_ttm": roe,
            "net_profit_yoy": g,
        }))
    fundamentals = pd.concat(rows, ignore_index=True)

    return {"open": open_.round(2), "close": close, "limit_up": limit_up,
            "limit_down": limit_down, "suspended": suspended,
            "volume": volume, "fundamentals": fundamentals}


def load_csv(folder: str | Path) -> dict:
    """从本地 CSV 读取真实数据。需要两个文件：
        data/prices.csv  列: date,code,open,close,limit_up,limit_down,suspended,volume
        data/fundamentals.csv 列: date,code,pe_ttm,pb,roe_ttm,net_profit_yoy
    """
    folder = Path(folder)
    px = pd.read_csv(folder / "prices.csv", parse_dates=["date"])
    fu = pd.read_csv(folder / "fundamentals.csv", parse_dates=["date"])
    out = {}
    for f in PRICE_FIELDS:
        out[f] = px.pivot(index="date", columns="code", values=f).sort_index()
    out["suspended"] = out["suspended"].fillna(True).astype(bool)
    out["fundamentals"] = fu
    return out


def load_akshare(start: str = "20200101", end: str | None = None,
                 limit: int | None = None) -> dict:
    """从 akshare 拉 A 股真实数据（需 pip install akshare）。

    这一步是整条链路最容易出错的地方，务必注意四个坑：
      1) 复权：用前复权（qfq）算因子，用不复权算涨跌停，二者不能混。
      2) 存活偏差：akshare 的沪深京 A 股列表只含存续股票，已退市的拿不到，
         会让回测收益系统性偏高。补齐需要付费数据（Wind/聚源/米筐）。
      3) 停牌：停牌日成交量为 0，不能简单 ffill 价格当成可交易。
      4) ST / 新股：上市不满 60 日、ST、退市整理期都要剔除。
    """
    import akshare as ak  # 延迟导入，未安装不影响其他功能

    end = end or pd.Timestamp.today().strftime("%Y%m%d")
    spot = ak.stock_zh_a_spot_em()
    codes = spot["代码"].tolist()
    if limit:
        codes = codes[:limit]

    closes, opens, vols = {}, {}, {}
    for code in codes:
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date=start, end_date=end, adjust="qfq")
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df["日期"] = pd.to_datetime(df["日期"])
        df = df.set_index("日期")
        closes[code] = df["收盘"]
        opens[code] = df["开盘"]
        vols[code] = df["成交量"]

    close = pd.DataFrame(closes).sort_index()
    open_ = pd.DataFrame(opens).reindex_like(close)
    volume = pd.DataFrame(vols).reindex_like(close).fillna(0)
    prev = close.shift(1)
    out = {
        "close": close, "open": open_,
        "limit_up": (prev * 1.10).round(2),
        "limit_down": (prev * 0.90).round(2),
        "suspended": volume.eq(0),
        "volume": volume,
        "fundamentals": _ak_fundamentals(codes, start, end),
    }
    return out


def _ak_fundamentals(codes: list[str], start: str, end: str) -> pd.DataFrame:
    """基本面快照。akshare 的财务指标是报告期口径，需要自己按公告日对齐，
    否则会用上未来数据（look-ahead bias）——这是散户回测最常见的致命错误。"""
    import akshare as ak

    rows = []
    for code in codes:
        try:
            df = ak.stock_financial_analysis_indicator(symbol=code, start_year=start[:4])
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df["日期"] = pd.to_datetime(df["日期"])
        df = df.sort_values("日期")
        # 只用公告日之前已知的数据：这里用报告期 + 90 天作为保守近似
        df["available"] = df["日期"] + pd.Timedelta(days=90)
        for _, r in df.iterrows():
            rows.append({"date": r["available"], "code": code,
                         "pe_ttm": np.nan, "pb": np.nan,
                         "roe_ttm": _f(r.get("净资产收益率(%)")),
                         "net_profit_yoy": _f(r.get("净利润增长率(%)"))})
    return pd.DataFrame(rows)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan
