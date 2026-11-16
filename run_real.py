"""真实数据实盘级回测：akshare 抓 100 只 A 股，跑多因子 + 电网轮动。

用法：
    python3 run_real.py --n 100 --mode both
    python3 run_real.py --n 100 --mode factor --start 20210101 --end 20261115
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src import realdata as rd
from src.engine import Config, run
from src.report import build
from src.sector import GRID_MEMBERS, run_rotation, strength

OUT = Path("out")


def bias_report(codes: list[str], close: pd.DataFrame, start: str, end: str) -> dict:
    """量化存活偏差：免费数据只能给出"下限估计"，但足以说明量级。"""
    first = close.apply(lambda s: s.first_valid_index())
    base = close.index[0]
    late = [c for c in codes if first.get(c) is not None and first[c] > base]
    alive = 5579
    try:
        dl = rd.delist_stats()
        d0, d1 = pd.Timestamp(f"{start[:4]}-{start[4:6]}-{start[6:]}"), pd.Timestamp(
            f"{end[:4]}-{end[4:6]}-{end[6:]}")
        n_delist = int(((dl["delist_date"] >= d0) & (dl["delist_date"] <= d1)).sum())
    except Exception:
        dl, n_delist = pd.DataFrame(), 0

    years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25, 1e-9)
    rate = n_delist / (n_delist + alive) if n_delist else 0.0
    assume = 0.85                       # 退市前平均相对跌幅（保守假设）
    drag = rate * assume / years

    return {
        "universe_size": alive,
        "sample": len(codes),
        "listed_after_start": len(late),
        "delisted_in_period": n_delist,
        "delist_rate_pct": round(rate * 100, 2),
        "bias_pp_per_year": round(drag * 100, 2),
        "years": round(years, 2),
        "delist_df": dl,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--start", default="20210101")
    ap.add_argument("--end", default="20261115")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--mode", choices=["factor", "rotation", "both"], default="both")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    print(f"① 抽样 {args.n} 只 A 股（seed={args.seed}）…")
    pool = rd.universe(max(args.n - len(GRID_MEMBERS), 20), seed=args.seed)
    codes = sorted(set(pool) | set(GRID_MEMBERS))       # 确保三只电网设备在样本内
    print(f"   样本：{codes[:6]} … 共 {len(codes)} 只（含电网设备三只）")

    print("② 拉行情（腾讯源，约 0.4s/只）…")
    prices = rd.fetch_prices(codes, args.start, args.end)
    close = prices["close"]
    print(f"   交易日 {len(close)} 个，个股 {close.shape[1]} 只，"
          f"覆盖 {close.index[0]:%Y-%m-%d} → {close.index[-1]:%Y-%m-%d}")

    print("③ 拉估值（百度源，PE-TTM 日频）…")
    pe = rd.fetch_pe(codes)
    print("④ 拉财报（东财业绩报表，ROE / 净利同比，首次约 1–2 分钟）…")
    try:
        fin = rd.fetch_fundamentals(codes, args.start, args.end)
    except Exception as e:
        fin = None
        print(f"   财报获取失败（{type(e).__name__}），质量/成长因子不参与打分")
    panel = rd.to_panel(prices, pe, fin)
    print(f"   估值记录 {len(pe):,} 条，覆盖 {pe['code'].nunique()} 只")
    if fin is not None and len(fin):
        meta = panel["fin_meta"]
        print(f"   财报 {fin['period'].nunique()} 期，最新 {meta['period']}"
              f"（{meta['avail']} 起可用），覆盖 {meta['codes']} 只")

    cfg = Config(top_n=args.top, capital=1_000_000.0)
    if not OUT.exists():
        OUT.mkdir(parents=True)
    summary = {}

    if args.mode in ("factor", "both"):
        print("⑤ 多因子回测…")
        res = run(panel, cfg)
        m = res.metrics
        summary["factor"] = m
        print(f"   年化 {m['cagr']*100:.2f}% ｜ 基准 {m.get('bench_cagr', 0)*100:.2f}% "
              f"｜ 超额 {m.get('excess_cagr', 0)*100:.2f}% ｜ 回撤 {m['max_dd']*100:.1f}% "
              f"｜ IC {m.get('ic_mean', float('nan')):.3f}")
        build(res, OUT / "report_real_factor.html",
              "真实 A 股数据（100 只抽样）｜⚠️ 含存活偏差，且 100 只样本的 IC 噪声极大")

    if args.mode in ("rotation", "both"):
        print("⑥ 电网设备行业轮动…")
        r = run_rotation(panel, GRID_MEMBERS, cfg)
        for k, res in (("rotation", r["rotation"]), ("sector_only", r["sector_only"])):
            summary[k] = res.metrics
            m = res.metrics
            print(f"   {k:12s} 年化 {m['cagr']*100:6.2f}% ｜ 回撤 {m['max_dd']*100:6.1f}% "
                  f"｜ 夏普 {m['sharpe']:5.2f}")
        build(r["rotation"], OUT / "report_real_rotation.html",
              "电网设备三只等权 vs 全市场多因子轮动｜RS20/RS60 强度分 > 0 时切板块")

        st = r["strength"].dropna(subset=["score"])
        summary["strength_last"] = {
            "date": str(st.index[-1].date()),
            "score": round(float(st["score"].iloc[-1]), 3),
            "rs20": round(float(st["rs20"].iloc[-1]), 4),
            "rs60": round(float(st["rs60"].iloc[-1]), 4),
            "signal": "持有电网设备" if st["score"].iloc[-1] > 0 else "回到全市场",
        }
        print(f"   当前强度分 {st['score'].iloc[-1]:+.2f} → {summary['strength_last']['signal']}")

    print("⑦ 存活偏差测算…")
    b = bias_report(codes, close, args.start, args.end)
    dl = b.pop("delist_df")
    best = dl.sort_values("delist_date").tail(8) if len(dl) else pd.DataFrame()
    summary["bias"] = b
    print(f"   样本中 {b['listed_after_start']} 只是区间内才上市的（2021 年的池子里本不该有它们）")
    print(f"   区间暂停/终止上市 {b['delisted_in_period']} 只 / 现存 {b['universe_size']} 只 "
          f"→ 退出率 {b['delist_rate_pct']:.2f}%（上交所口径含暂停上市，属上限估计）")
    print(f"   按退市前相对跌幅 85% 估算，等权基准年化虚高约 {b['bias_pp_per_year']:.2f} 个百分点")

    (OUT / "summary_real.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\n汇总已写入 {OUT/'summary_real.json'}")
    if len(best):
        print("最近退市样本：" + "、".join(best["code"].tolist()))


if __name__ == "__main__":
    main()
