"""回测引擎：月度调仓、T+1 开盘成交、涨跌停/停牌拦截、双边成本、等权基准。

刻意不做的三件事（也是绝大多数散户回测翻车的地方）：
  1) 不用当日收盘价成交（那是未来函数），一律次日开盘。
  2) 不忽略涨跌停：涨停买不进、跌停卖不掉，都按"委托失败"处理。
  3) 不忽略成本：佣金 + 印花税（仅卖出）+ 滑点，逐笔计。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .factors import DEFAULT_WEIGHTS, cross_section


@dataclass
class Config:
    top_n: int = 20
    commission: float = 0.00025     # 佣金，双边
    stamp_tax: float = 0.0005       # 印花税，仅卖出
    slippage: float = 0.001         # 滑点，双边
    min_price: float = 3.0
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    start: str | None = None
    end: str | None = None
    lot: int = 100
    capital: float = 1_000_000.0   # 初始资金，A股一手 100 股，太小会买不进


@dataclass
class Result:
    equity: pd.Series
    benchmark: pd.Series
    trades: pd.DataFrame
    holdings: pd.DataFrame
    ic: pd.Series
    metrics: dict
    yearly: pd.DataFrame


def _month_ends(idx: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(idx, index=idx)
    return sorted(set(s.groupby(idx.to_period("M")).max().values))


def run(data: dict, cfg: Config | None = None, selector=None) -> Result:
    cfg = cfg or Config()
    close = data["close"].sort_index()
    open_ = data["open"].reindex_like(close)
    lu = data["limit_up"].reindex_like(close)
    ld = data["limit_down"].reindex_like(close)
    susp = data["suspended"].reindex_like(close).fillna(True)
    fu = data["fundamentals"]
    roe_p, gro_p = data.get("roe_panel"), data.get("growth_panel")

    if cfg.start:
        close = close.loc[cfg.start:]
    if cfg.end:
        close = close.loc[:cfg.end]
    open_ = open_.reindex(close.index)
    lu, ld = lu.reindex(close.index), ld.reindex(close.index)
    susp = susp.reindex(close.index).fillna(True)

    idx = close.index
    pos = {d: i for i, d in enumerate(idx)}
    rebs = [d for d in _month_ends(idx) if d in pos]

    cash, shares = float(cfg.capital), {}
    equity = pd.Series(index=idx, dtype=float)
    bench = pd.Series(index=idx, dtype=float)
    trades, ic_rows, hold_rows = [], [], []
    traded_notional = 0.0

    for k, rb in enumerate(rebs):
        ei = pos[rb] + 1
        if ei >= len(idx):
            break
        ex = idx[ei]
        nxt_i = pos[rebs[k + 1]] if k + 1 < len(rebs) else len(idx) - 1
        if nxt_i < ei:
            break

        cs = cross_section(rb, close, fu, cfg.weights, cfg.min_price,
                           roe=roe_p, growth=gro_p)
        if cs.empty or not cs["eligible"].any():
            continue
        elig = cs[cs["eligible"]].sort_values("score", ascending=False)
        target = None
        if selector is not None:                      # 行业轮动等外部择时介入
            target = selector(rb, close, cs)
            if target:
                target = [c for c in target if c in close.columns]
        if not target:
            target = elig.head(cfg.top_n).index.tolist()

        px_open = open_.loc[ex]
        eq = cash + sum(sh * px_open.get(c, np.nan) for c, sh in shares.items()
                        if not np.isnan(px_open.get(c, np.nan)))
        if eq <= 0 or not target:
            continue
        tw = eq / len(target)

        # 1) 卖出不在目标里的持仓
        for c in list(shares):
            if c in target:
                continue
            p = px_open.get(c, np.nan)
            if np.isnan(p) or susp.at[ex, c] or p <= ld.at[ex, c] * 1.0001:
                continue
            amt = shares[c] * p * (1 - cfg.slippage)
            fee = amt * (cfg.commission + cfg.stamp_tax)
            cash += amt - fee
            traded_notional += amt
            trades.append((ex, c, "sell", shares.pop(c), p, amt))
            _ = fee

        # 2) 向目标权重靠拢（先补低配，后削超配）
        for c in target:
            p = px_open.get(c, np.nan)
            if np.isnan(p) or susp.at[ex, c]:
                continue
            cur = shares.get(c, 0) * p
            delta = tw - cur
            if delta > 0:
                if p >= lu.at[ex, c] * 0.9999:      # 涨停买不进
                    continue
                budget = min(delta, cash / (1 + cfg.commission + cfg.slippage))
                qty = int(budget / (p * (1 + cfg.slippage)) // cfg.lot) * cfg.lot
                if qty <= 0:
                    continue
                amt = qty * p * (1 + cfg.slippage)
                fee = amt * cfg.commission
                if amt + fee > cash:
                    continue
                cash -= amt + fee
                shares[c] = shares.get(c, 0) + qty
                traded_notional += amt
                trades.append((ex, c, "buy", qty, p, amt))
            elif delta < 0:
                if p <= ld.at[ex, c] * 1.0001:      # 跌停卖不掉
                    continue
                qty = int(min(abs(delta), cur) / p // cfg.lot) * cfg.lot
                if qty <= 0 or qty > shares.get(c, 0):
                    continue
                amt = qty * p * (1 - cfg.slippage)
                fee = amt * (cfg.commission + cfg.stamp_tax)
                cash += amt - fee
                shares[c] -= qty
                if shares[c] == 0:
                    del shares[c]
                traded_notional += amt
                trades.append((ex, c, "sell", qty, p, amt))

        # 3) 记录持仓与 IC
        for c, sh in shares.items():
            hold_rows.append((ex, c, sh, sh * px_open.get(c, np.nan)))
        fwd = _forward_return(close, ex, idx[min(nxt_i + 1, len(idx) - 1)])
        common = [c for c in elig.index if c in fwd.index and not np.isnan(fwd[c])]
        if len(common) > 5:
            s = pd.Series(elig.loc[common, "score"]).rank()
            f = pd.Series(fwd[common]).rank()
            ic = s.corr(f)          # 秩相关 = Spearman，不引入 scipy
            ic_rows.append((rb, ic, len(common)))

        # 4) 逐日盯市 + 等权基准
        bcodes = [c for c in elig.index if not susp.at[ex, c]]
        b_eq = 1.0
        for j in range(ei, nxt_i + 1):
            d = idx[j]
            cl = close.loc[d]
            equity[d] = cash + sum(sh * cl.get(c, np.nan) for c, sh in shares.items()
                                   if not np.isnan(cl.get(c, np.nan)))
            if j > ei:
                prev = close.loc[idx[j - 1]]
                rr = [(cl[c] / prev[c] - 1) for c in bcodes
                      if c in cl.index and c in prev.index
                      and not np.isnan(cl[c]) and not np.isnan(prev[c]) and prev[c] > 0]
                if rr:
                    b_eq *= 1 + float(np.mean(rr))
            bench[d] = b_eq

    equity = equity.ffill()
    equity = equity / equity.dropna().iloc[0]      # 归一到 1.0
    bench = bench.dropna()
    trades_df = pd.DataFrame(trades, columns=["date", "code", "side", "shares", "price", "amount"])
    hold_df = pd.DataFrame(hold_rows, columns=["date", "code", "shares", "value"])
    ic_s = pd.Series({d: v for d, v, _ in ic_rows}).sort_index()
    hold_df_all = pd.DataFrame(hold_rows, columns=["date", "code", "shares", "value"])
    metrics = evaluate(equity, bench, traded_notional / cfg.capital, ic_s, cfg, hold_df_all)
    yearly = yearly_table(equity, bench)
    return Result(equity, bench, trades_df, hold_df, ic_s, metrics, yearly)


def loc_row(date, frame):
    return frame.loc[date]


def _forward_return(close: pd.DataFrame, d0, d1) -> pd.Series:
    a, b = close.loc[d0], close.loc[d1]
    return (b / a - 1).replace([np.inf, -np.inf], np.nan)


def _dd(series: pd.Series) -> float:
    return float((series / series.cummax() - 1).min())


def evaluate(equity: pd.Series, bench: pd.Series, traded: float,
             ic: pd.Series, cfg: Config, holdings: pd.DataFrame | None = None) -> dict:
    eq = equity.dropna()
    ret = eq.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    vol = ret.std() * np.sqrt(252)
    mdd = _dd(eq)
    monthly = eq.resample("ME").last().pct_change().dropna()

    out = {
        "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
        "years": round(years, 2),
        "total_return": round(eq.iloc[-1] / eq.iloc[0] - 1, 4),
        "cagr": round(cagr, 4), "ann_vol": round(vol, 4),
        "sharpe": round(cagr / vol, 3) if vol else np.nan,
        "max_dd": round(mdd, 4),
        "calmar": round(cagr / abs(mdd), 3) if mdd else np.nan,
        "monthly_win": round(float((monthly > 0).mean()), 4),
        "turnover_annual": round(traded / (2 * eq.mean()) / years, 2),
        "avg_holdings": int(holdings.groupby("date").size().mean()) if holdings is not None and len(holdings) else 0,
    }
    if len(bench) > 5:
        b = bench.reindex(eq.index).ffill().dropna()
        b_cagr = (b.iloc[-1] / b.iloc[0]) ** (1 / years) - 1
        out.update({"bench_cagr": round(b_cagr, 4),
                    "excess_cagr": round(cagr - b_cagr, 4),
                    "bench_max_dd": round(_dd(b), 4)})
    if len(ic) > 3:
        out.update({"ic_mean": round(float(ic.mean()), 4),
                    "ic_std": round(float(ic.std()), 4),
                    "icir": round(float(ic.mean() / ic.std()), 3) if ic.std() else np.nan,
                    "ic_win": round(float((ic > 0).mean()), 4)})
    return out


def yearly_table(equity: pd.Series, bench: pd.Series) -> pd.DataFrame:
    eq = equity.dropna().resample("YE").last()
    eq = pd.concat([pd.Series([equity.dropna().iloc[0]], index=[equity.dropna().index[0]]), eq])
    r = eq.pct_change().dropna()
    r.index = [d.year for d in r.index]
    b = bench.reindex(equity.dropna().index).ffill().dropna().resample("YE").last()
    b = pd.concat([pd.Series([bench.dropna().iloc[0]], index=[bench.dropna().index[0]]), b])
    rb = b.pct_change().dropna()
    rb.index = [d.year for d in rb.index]
    df = pd.DataFrame({"策略": r, "基准": rb.reindex(r.index)})
    df["超额"] = df["策略"] - df["基准"]
    return df.round(4)
