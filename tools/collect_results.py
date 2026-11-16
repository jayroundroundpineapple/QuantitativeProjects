"""把 out/jobs/<id>/ 里的产物整理成 results/ 下的归档结构。

用法：
    python3 tools/collect_results.py <job_id> <目标目录名> [--html]

做的事：
  - 复制 nav.csv / trades.csv / holdings.csv / ranking.csv / stress.csv
  - 抽出一份精简的 metrics.json（参数 + 各条策略指标 + caveats），
    不复制 state.json（体积大，且混着全部日志）
  - 可选复制 report_*.html

只读 out/，不修改原始产物，重跑安全。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "out" / "jobs"
OUT = ROOT / "results"

KEEP = ["nav.csv", "trades.csv", "holdings.csv", "ranking.csv", "stress.csv"]


def collect(job_id: str, dest_name: str, with_html: bool = False) -> Path:
    src = JOBS / job_id
    state_fp = src / "state.json"
    if not state_fp.exists():
        raise SystemExit(f"找不到 {state_fp}")

    state = json.loads(state_fp.read_text())
    res = state.get("result") or {}
    dest = OUT / dest_name
    dest.mkdir(parents=True, exist_ok=True)

    copied = []
    for name in KEEP:
        fp = src / name
        if fp.exists():
            shutil.copy2(fp, dest / name)
            copied.append(name)
    if with_html:
        for fp in src.glob("report_*.html"):
            shutil.copy2(fp, dest / fp.name)
            copied.append(fp.name)

    slim = {
        "job_id": job_id,
        "kind": res.get("kind", "run"),
        "params": state.get("params"),
        "finished": state.get("finished"),
        "metrics": {k: v.get("metrics") for k, v in (res.get("lines") or {}).items()},
        "pool": res.get("pool"),
        "stats": res.get("stats"),
        "ranking_head": (res.get("ranking") or {}).get("rows", [])[:10],
        "ranking_meta": {k: (res.get("ranking") or {}).get(k)
                         for k in ("total", "eligible", "live", "label")},
        "sources": res.get("sources"),
        "bias": res.get("bias"),
        "caveats": res.get("caveats"),
        "files": copied,
    }
    (dest / "metrics.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=1, default=str))
    return dest


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    d = collect(sys.argv[1], sys.argv[2], with_html="--html" in sys.argv)
    print(f"→ {d.relative_to(ROOT)}")
    for fp in sorted(d.iterdir()):
        print(f"   {fp.name:26} {fp.stat().st_size/1024:7.1f} KB")
