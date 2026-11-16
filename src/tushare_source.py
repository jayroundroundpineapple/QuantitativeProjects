"""tushare 数据适配器（可选，需要自己的 token）。

免费源（腾讯/百度/东财）已经够跑通整条链路，但有两件事它做不到：
  1) **拿不到已退市股票** —— 存活偏差的根，只能靠含退市的全样本解决
  2) **拿不到真实公告日** —— akshare 只能用法定披露截止日近似，tushare 有 ann_date 字段

所以这个模块存在的意义只有一个：把「带退市股的全样本 + 真实公告日」这条路留好接口。
没配 token 时整条链路照旧跑，本模块不会被 import。

用法：
    export TUSHARE_TOKEN=你的token
    python3 -m src.tushare_source --check                 # 自检：连通性 + 字段
    python3 -m src.tushare_source --delisted out/delisted.csv   # 导出区间内退市股
    python3 -m src.tushare_source --probe 600312          # 看单只股票抓取结果

在代码里用：
    from src import tushare_source as ts
    panel = ts.panel(codes, "20210101", "20261116")       # 结构同 realdata.fetch_prices
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd

API = "https://api.tushare.pro"


def _fmt(code: str) -> str:
    """6 位代码 → tushare 的 600312.SH 形式。"""
    c = str(code).zfill(6)
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    if c.startswith(("4", "8", "920")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def pro(token: str | None = None):
    """返回 tushare pro 接口对象；未装库或未配 token 时给明确提示而不是一堆 traceback。"""
    token = token or os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "缺少 TUSHARE_TOKEN。去 https://tushare.pro 注册后复制 token，"
            "然后 export TUSHARE_TOKEN=xxx 再重试。\n"
            "不配也能用——本工具默认走免费源（腾讯行情 + 百度估值 + 东财财报），"
            "只是拿不到退市股。")
    try:
        import tushare as ts
    except ImportError as e:
        raise RuntimeError("未安装 tushare：pip install tushare") from e
    ts.set_token(token)
    return ts.pro_api()


class _Limiter:
    """免费额度约 120 次/分钟，超了会被静默拒绝（返回空而不是报错）——必须自己控速。"""

    def __init__(self, per_min: int = 110):
        self.gap = 60.0 / max(per_min, 1)
        self.t = 0.0

    def wait(self) -> None:
        dt = time.time() - self.t
        if dt < self.gap:
            time.sleep(self.gap - dt)
        self.t = time.time()


def universe(include_delisted: bool = True, token: str | None = None) -> pd.DataFrame:
    """全部 A 股，可选含暂停上市(P)与已退市(D)。

    这是免费源给不了的东西：list_status='D' 的那批代码，
    才是回测里「本该亏光却消失」的样本。
    """
    api = pro(token)
    frames = []
    for st in (("L", "P", "D") if include_delisted else ("L",)):
        df = api.stock_basic(exchange="", list_status=st,
                             fields="ts_code,symbol,name,area,industry,list_date,delist_date")
        if df is None or df.empty:
            continue
        df["status"] = st
        frames.append(df)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not out.empty:
        out["code"] = out["symbol"].astype(str).str.zfill(6)
    return out


def fetch_prices(codes: list[str], start: str, end: str,
                 token: str | None = None, progress=None) -> dict:
    """日线（前复权）+ 涨跌停/停牌标记，返回结构和 realdata.fetch_prices 完全一致。

    复权做法：close_qfq = close * adj_factor / adj_factor(最新)。
    自己算而不用现成复权接口，是为了让「复权基准日」可控、口径可复现。
    """
    api = pro(token)
    lim = _Limiter()
    s, e = start.replace("-", ""), end.replace("-", "")
    close, open_, vol = {}, {}, {}
    for i, c in enumerate(codes):
        if progress:
            progress(i + 1, len(codes))
        ts_code = _fmt(c)
        for attempt in range(3):
            try:
                lim.wait()
                d = api.daily(ts_code=ts_code, start_date=s, end_date=e)
                lim.wait()
                f = api.adj_factor(ts_code=ts_code, start_date=s, end_date=e)
                if d is None or d.empty:
                    break
                d["trade_date"] = pd.to_datetime(d["trade_date"])
                d = d.set_index("trade_date").sort_index()
                ratio = 1.0
                if f is not None and not f.empty:
                    f["trade_date"] = pd.to_datetime(f["trade_date"])
                    af = f.set_index("trade_date")["adj_factor"].sort_index()
                    base = float(af.dropna().iloc[-1]) if af.notna().any() else 1.0
                    ratio = (af / base).reindex(d.index).ffill().fillna(1.0)
                close[c] = d["close"].astype(float) * ratio
                open_[c] = d["open"].astype(float) * ratio
                vol[c] = d["vol"].astype(float)
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))

    close = pd.DataFrame(close).sort_index()
    open_ = pd.DataFrame(open_).reindex_like(close)
    volume = pd.DataFrame(vol).reindex_like(close).fillna(0.0)
    prev = close.shift(1)
    from .realdata import _limit_pct                      # 复用同一套板块涨跌幅规则
    pct = pd.Series({c: _limit_pct(c) for c in close.columns})
    return {
        "open": open_, "close": close, "volume": volume,
        "limit_up": prev.mul(1 + pct, axis=1).round(2),
        "limit_down": prev.mul(1 - pct, axis=1).round(2),
        "suspended": volume.eq(0) | close.isna(),
    }


def fetch_fundamentals(codes: list[str], start: str, end: str,
                       token: str | None = None, progress=None) -> pd.DataFrame:
    """ROE 与净利同比，用 **ann_date（真实公告日）** 做可用日。

    这是比 akshare 方案更准的地方：不必再用「法定披露截止日」近似，
    但也更危险——ann_date 是首次公告日，若日后被更正，
    回测里用的其实是当时看不到的数字（轻微前视）。做严格 PIT 需要存每日快照。
    """
    api = pro(token)
    lim = _Limiter()
    rows = []
    for i, c in enumerate(codes):
        if progress:
            progress(i + 1, len(codes))
        try:
            lim.wait()
            df = api.fina_indicator(ts_code=_fmt(c), start_date=start.replace("-", ""),
                                    end_date=end.replace("-", ""))
        except Exception:
            continue
        if df is None or df.empty:
            continue
        df = df.copy()
        for col in ("roe", "netprofit_yoy"):
            if col not in df.columns:
                df[col] = np.nan
        df["code"] = c
        df["date"] = pd.to_datetime(df["ann_date"], errors="coerce")
        rows.append(df[["date", "code", "end_date", "roe", "netprofit_yoy"]])
    if not rows:
        return pd.DataFrame(columns=["date", "code", "period", "roe", "growth", "industry"])
    out = pd.concat(rows, ignore_index=True).dropna(subset=["date"])
    out = out.rename(columns={"netprofit_yoy": "growth", "end_date": "period"})
    return out.sort_values(["date", "code"])


def panel(codes: list[str], start: str, end: str, token: str | None = None,
          progress=None) -> dict:
    """一次拿到 engine 需要的完整 panel（行情 + 财报 + 空的 PE 占位）。"""
    from .realdata import to_panel
    px = fetch_prices(codes, start, end, token, progress)
    fin = fetch_fundamentals(codes, start, end, token)
    pe = pd.DataFrame({"date": pd.NaT, "code": codes, "pe_ttm": np.nan})
    return to_panel(px, pe, fin)


# ------------------------------------------------------------------ CLI
def _check() -> None:
    api = pro()
    basic = api.stock_basic(list_status="L", fields="ts_code,name,list_date")
    dead = api.stock_basic(list_status="D", fields="ts_code,name,delist_date")
    print(f"✅ 连通成功：在市 {len(basic)} 只、已退市 {len(dead)} 只")
    print(f"   退市样本示例：{', '.join(dead['ts_code'].head(5))}" if len(dead) else "   退市列表为空")
    d = api.daily(ts_code="600312.SH", start_date="20260101", end_date="20261116")
    print(f"✅ daily 返回 {len(d)} 行，字段：{', '.join(d.columns[:8])}")
    f = api.fina_indicator(ts_code="600312.SH", start_date="20240101", end_date="20261116")
    cols = set(f.columns)
    print(f"✅ fina_indicator 返回 {len(f)} 行；"
          f"ann_date {'有' if 'ann_date' in cols else '缺'}、"
          f"roe {'有' if 'roe' in cols else '缺'}、"
          f"netprofit_yoy {'有' if 'netprofit_yoy' in cols else '缺'}")


def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="tushare 适配器自检与导出")
    ap.add_argument("--check", action="store_true", help="连通性与字段自检")
    ap.add_argument("--delisted", metavar="CSV", help="导出全部已退市股票到 CSV")
    ap.add_argument("--probe", metavar="CODE", help="抓单只股票的行情和财报看看")
    a = ap.parse_args(argv)
    if a.check:
        _check()
    if a.delisted:
        u = universe(True)
        dead = u[u["status"] == "D"]
        dead.to_csv(a.delisted, index=False, encoding="utf-8-sig")
        print(f"已导出 {len(dead)} 只退市股 → {a.delisted}")
    if a.probe:
        px = fetch_prices([a.probe], "20250101", "20261116")
        print(px["close"].describe().round(2).to_string())
        fin = fetch_fundamentals([a.probe], "20240101", "20261116")
        print(fin.tail(8).to_string())
    if not (a.check or a.delisted or a.probe):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
