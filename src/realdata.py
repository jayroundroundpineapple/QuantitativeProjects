"""真实数据层：akshare 多源抓取 + 本地缓存。

为什么不用一个接口就完事——实测（2026-11）：
  · ak.stock_zh_a_hist        已更名，直接报错
  · ak.stock_zh_a_hist_em     东方财富，请求稍密就被 IP 限流（5~40 分钟）
  · ak.stock_a_indicator_lg   已下线
  · ak.stock_zh_a_hist_tx     腾讯，0.4s/只，稳定 ← 本模块主用
  · ak.stock_zh_valuation_baidu 百度，含每日 PE/PB 历史 ← 估值因子用

这个文件的真正价值不是"能拉到数据"，而是**限流、改名、字段缺失都处理掉了**，
并且把中间结果落盘缓存，重跑不重复请求。
"""
from __future__ import annotations

import hashlib
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"


def _tag(codes: list[str]) -> str:
    return hashlib.md5("|".join(sorted(codes)).encode()).hexdigest()[:8]


def _prefix(code: str) -> str:
    if code.startswith("6"):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    return "bj"


def _limit_pct(code: str) -> float:
    """涨跌停幅度：主板 10%、创业板/科创板 20%、北交所 30%。"""
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "920")):
        return 0.30
    return 0.10


def universe(n: int = 100, seed: int = 42, exclude_st: bool = True) -> list[str]:
    """当前全部 A 股中抽样。

    ⚠️ 这就是存活偏差的来源：这个列表只含**今天还活着**的股票。
    2021 年以来退市的那些，在这里一只都找不到。
    """
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    memo = CACHE / f"universe_{n}_{seed}.txt"
    if memo.exists():
        return memo.read_text().split()

    try:
        df = ak.stock_info_a_code_name()
    except Exception:
        # 成分接口会连带请求北交所域名，网络受限时直接回退到已缓存的行情
        for f in sorted(CACHE.glob("prices_*.pkl")):
            cols = list(pd.read_pickle(f).get("close", pd.DataFrame()).columns)
            if len(cols) >= n:
                memo.write_text("\n".join(cols[:n]))
                return cols[:n]
        raise
    df["code"] = df["code"].astype(str).str.zfill(6)
    if exclude_st:
        df = df[~df["name"].str.contains("ST|退", na=False)]
    codes = sorted(df["code"].unique().tolist())
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(codes), size=min(n, len(codes)), replace=False)
    out = [codes[i] for i in sorted(pick)]
    memo.write_text("\n".join(out))
    return out


def fetch_prices(codes: list[str], start: str, end: str,
                 use_cache: bool = True) -> dict:
    import akshare as ak
    from tqdm import tqdm

    CACHE.mkdir(parents=True, exist_ok=True)
    tag = f"{start}_{end}_{len(codes)}_{_tag(codes)}"
    fp = CACHE / f"prices_{tag}.pkl"
    if use_cache and fp.exists():
        return pd.read_pickle(fp)

    close, open_, vol = {}, {}, {}
    for c in tqdm(codes, desc="行情", ncols=70):
        sym = _prefix(c) + c
        for attempt in range(3):
            try:
                df = ak.stock_zh_a_hist_tx(symbol=sym, start_date=start,
                                           end_date=end, adjust="qfq")
                if df is None or df.empty:
                    break
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                close[c] = df["close"].astype(float)
                open_[c] = df["open"].astype(float)
                vol[c] = df["volume"].astype(float)
                break
            except Exception:
                time.sleep(0.8 * (attempt + 1))

    close = pd.DataFrame(close).sort_index()
    open_ = pd.DataFrame(open_).reindex_like(close)
    volume = pd.DataFrame(vol).reindex_like(close).fillna(0.0)

    # 停牌：成交量为 0 或价格缺失
    suspended = volume.eq(0) | close.isna()

    prev = close.shift(1)
    pct = pd.Series({c: _limit_pct(c) for c in close.columns})
    limit_up = prev.mul(1 + pct, axis=1).round(2)
    limit_down = prev.mul(1 - pct, axis=1).round(2)

    panel = {"open": open_, "close": close, "limit_up": limit_up,
             "limit_down": limit_down, "suspended": suspended, "volume": volume}
    pd.to_pickle(panel, fp)
    return panel


def fetch_pe(codes: list[str], use_cache: bool = True) -> pd.DataFrame:
    """百度估值：每日 PE(TTM) 历史，用于估值因子。

    注意：这是**事后**的每日 PE（含当日收盘价），所以在 t 日调仓要用 t 日的值，
    不能用 t+1 的。本模块返回原始序列，由 factors 按日期截断。
    """
    import akshare as ak
    from tqdm import tqdm

    CACHE.mkdir(parents=True, exist_ok=True)
    fp = CACHE / f"pe_{len(codes)}_{_tag(codes)}.pkl"
    if use_cache and fp.exists():
        return pd.read_pickle(fp)

    rows = []
    for c in tqdm(codes, desc="估值", ncols=70):
        try:
            df = ak.stock_zh_valuation_baidu(symbol=c, indicator="市盈率(TTM)",
                                            period="近五年")
            if df is None or df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df["code"] = c
            rows.append(df[["date", "code", "value"]].rename(columns={"value": "pe_ttm"}))
        except Exception:
            continue
        time.sleep(0.05)

    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["date", "code", "pe_ttm"])
    pd.to_pickle(out, fp)
    return out


def to_panel(prices: dict, pe: pd.DataFrame) -> dict:
    """拼成 engine 需要的 panel。质量/成长因子留空 -> 打分时自动中性。
    要接真实 ROE / 净利增速，用 ak.stock_financial_analysis_indicator，
    并按「报告期 + 90 天」对齐公告日，否则会引入未来函数。"""
    pe = pe.copy()
    pe["roe_ttm"] = np.nan
    pe["net_profit_yoy"] = np.nan
    panel = dict(prices)
    panel["fundamentals"] = pe
    return panel


def delist_stats() -> pd.DataFrame:
    """上市/退市统计，用来量化存活偏差——这是免费数据唯一能给出来的硬证据。"""
    import akshare as ak

    out = []
    for name, fn in [("沪市", "stock_info_sh_delist"), ("深市", "stock_info_sz_delist")]:
        try:
            df = getattr(ak, fn)()
        except Exception:
            continue
        col = [c for c in df.columns if "日期" in c]
        if not col:
            continue
        d = pd.to_datetime(df[col[-1]], errors="coerce")
        out.append(pd.DataFrame({"market": name, "code": df.iloc[:, 0].astype(str),
                                 "delist_date": d}).dropna())
    if not out:
        return pd.DataFrame(columns=["market", "code", "delist_date"])
    return pd.concat(out, ignore_index=True)
