"""量化实验台 · 入口

用法：
    python3 run.py --demo                      # 合成数据自检（验证流水线正确）
    python3 run.py --csv data --start 2021-01-01 # 用本地真实数据
    python3 run.py --demo --top 30 --out out/report.html
"""
from __future__ import annotations

import argparse
from pathlib import Path

from src import data as d
from src.engine import Config, run
from src.report import build


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="用合成数据自检")
    ap.add_argument("--csv", default=None, help="本地数据目录（含 prices.csv / fundamentals.csv）")
    ap.add_argument("--top", type=int, default=20, help="持仓只数")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default="out/report.html")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    if args.demo:
        print("· 使用合成数据（内置真实有效的价值因子，用于自检）")
        panel = d.synthetic(seed=args.seed)
        note = ("本报告基于<b>合成数据</b>，只用于验证代码链路是否把已知信号抓出来，"
                "不代表任何真实收益。真实回测请用 --csv 接入数据。")
    elif args.csv:
        print(f"· 读取本地数据：{args.csv}")
        panel = d.load_csv(args.csv)
        note = ("数据来自本地 CSV。请自行确认：是否含退市股（存活偏差）、"
                "是否前复权、是否剔除 ST 与新股。")
    else:
        ap.error("请指定 --demo 或 --csv")

    cfg = Config(top_n=args.top, start=args.start, end=args.end)
    res = run(panel, cfg)

    m = res.metrics
    print("\n" + "=" * 46)
    print(f"  区间      {m['start']} → {m['end']}  ({m['years']} 年)")
    print(f"  年化收益  {m['cagr']*100:7.2f}%      基准 {m.get('bench_cagr', float('nan'))*100:7.2f}%")
    print(f"  年化超额  {m.get('excess_cagr', float('nan'))*100:7.2f}%")
    print(f"  年化波动  {m['ann_vol']*100:7.2f}%")
    print(f"  夏普      {m['sharpe']:7.2f}")
    print(f"  最大回撤  {m['max_dd']*100:7.2f}%      基准 {m.get('bench_max_dd', float('nan'))*100:7.2f}%")
    print(f"  卡玛      {m['calmar']:7.2f}")
    print(f"  月度胜率  {m['monthly_win']*100:7.1f}%")
    print(f"  年换手    {m['turnover_annual']:7.2f} 倍")
    print(f"  IC 均值   {m.get('ic_mean', float('nan')):7.4f}    ICIR {m.get('icir', float('nan')):.2f}")
    print("=" * 46)
    print("\n分年度：")
    print(res.yearly.to_string(float_format=lambda x: f"{x*100:+6.1f}%"))

    last = res.holdings[res.holdings["date"] == res.holdings["date"].max()]
    tot = last["value"].sum() or 1
    print(f"\n期末持仓 {len(last)} 只，前 10：")
    for r in last.sort_values("value", ascending=False).head(10).itertuples():
        print(f"  {r.code}  {int(r.shares):>8,} 股  {r.value:>12,.0f}  {r.value/tot*100:5.1f}%")

    p = build(res, args.out, note)
    print(f"\n报告已生成：{Path(p).resolve()}")


if __name__ == "__main__":
    main()
