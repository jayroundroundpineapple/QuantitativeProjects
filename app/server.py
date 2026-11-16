"""本地量化工作台 —— 图形界面外壳（纯标准库，不需要 Flask/Django）。

启动方式（正常用户不需要看这些）：
    双击「量化工作台.app」            # macOS，无终端窗口
    python3 app/server.py             # 手动启动，自动开浏览器
    python3 app/server.py --port 8770 --no-open

设计原则：
  1) 浏览器只负责画界面，所有计算仍在本地进程里跑，数据不出本机。
  2) 一次回测 = 一个 job，后台线程执行，前端轮询进度，不会卡住页面。
  3) job 的产物（报告 / CSV / 参数 / 指标）全部落盘，可回看、可对比。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent.parent          # quant_lab/
sys.path.insert(0, str(BASE))

from src import realdata as rd                          # noqa: E402
from src.engine import Config, run                      # noqa: E402
from src.report import build                            # noqa: E402
from src.sector import GRID_MEMBERS, run_rotation      # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
OUT = BASE / "out"
JOBS = OUT / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)

VERSION = "1.0"
_JOBS: dict[str, "Job"] = {}
_LOCK = threading.Lock()


# ---------------------------------------------------------------- 工具函数
def _f(x, nd: int = 4):
    """numpy / NaN / inf → JSON 安全值。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, nd)


def _fmt_amt(x: float) -> str:
    if x >= 1e8:
        return f"{x / 1e8:.2f} 亿"
    if x >= 1e4:
        return f"{x / 1e4:.1f} 万"
    return f"{x:,.0f}"


def _s8(d: str) -> str:
    return d.replace("-", "").strip()


# ---------------------------------------------------------------- job 容器
class Job:
    def __init__(self, params: dict):
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.params = params
        self.status = "running"          # running | done | error | cancelled
        self.step = "排队中"
        self.progress = 0
        self.logs: list[str] = []
        self.result: dict | None = None
        self.error: str | None = None
        self.cancel = False
        self.t0 = time.time()
        self.elapsed = 0.0
        self.reports: dict[str, str] = {}
        self.dir = JOBS / self.id

    def log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        self.logs.append(line)
        print(line, flush=True)

    def quiet(self, pct: int, step: str) -> None:
        """只更新进度条，不写日志（抓取阶段每只股票都会调用）。"""
        self.progress = max(self.progress, min(int(pct), 99))
        self.step = step

    def bump(self, pct: int, step: str) -> None:
        self.progress = max(self.progress, pct)
        self.step = step
        print(f"    … {pct:3d}% {step}", flush=True)

    def snapshot(self) -> dict:
        return {
            "id": self.id, "status": self.status, "step": self.step,
            "progress": self.progress, "logs": self.logs[-200:],
            "error": self.error, "params": self.params,
            "elapsed": round(self.elapsed or (time.time() - self.t0), 1),
            "reports": self.reports,
        }

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        state = {
            **self.snapshot(),
            "result": self.result,
            "finished": datetime.now().isoformat(timespec="seconds"),
            "headline": self._headline(),
        }
        (self.dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, default=str), encoding="utf-8")

    def _headline(self) -> dict:
        if not self.result:
            return {}
        lines = self.result.get("lines", {})
        main = lines.get("rotation") or lines.get("factor") or {}
        m = main.get("metrics", {})
        return {
            "cagr": m.get("cagr"), "max_dd": m.get("max_dd"),
            "excess": m.get("excess_cagr"), "ic": m.get("ic_mean"),
            "n": self.result.get("pool", {}).get("count"),
        }


# ---------------------------------------------------------------- 数据准备
def _prepare_pool(p: dict, job: Job) -> list[str]:
    pool, n, seed = p["pool"], int(p["n"]), int(p["seed"])
    if pool == "custom":
        codes = [c.strip().zfill(6) for c in p["codes"].replace("，", ",").split(",") if c.strip()]
        if not codes:
            raise ValueError("自定义代码为空，请填写 6 位股票代码，用逗号分隔")
        return codes
    if pool == "grid":
        return list(GRID_MEMBERS)
    need = p.get("mode") in ("rotation", "both")
    base_n = max(n - len(GRID_MEMBERS), 5) if need else n
    codes = rd.universe(base_n, seed=seed)
    return sorted(set(codes) | set(GRID_MEMBERS)) if need else codes


def _delist_cached() -> "object":
    import pandas as pd
    fp = rd.CACHE / "delist_stats.json"
    if fp.exists() and time.time() - fp.stat().st_mtime < 86400 * 7:
        try:
            d = json.loads(fp.read_text())
            return pd.DataFrame(d)
        except Exception:
            pass
    df = rd.delist_stats()
    try:
        fp.write_text(json.dumps(df.astype(str).to_dict("records")))
    except Exception:
        pass
    return df


def _bias(codes: list[str], close, start: str, end: str) -> dict:
    import pandas as pd
    first = close.apply(lambda s: s.first_valid_index())
    late = [c for c in codes if first.get(c) is not None and first[c] > close.index[0]]
    alive, n_delist, note = 5579, 0, "未能取得退市名单，偏差按 0 计"
    try:
        dl = _delist_cached()
        if len(dl):
            d0 = pd.Timestamp(f"{start[:4]}-{start[4:6]}-{start[6:]}")
            d1 = pd.Timestamp(f"{end[:4]}-{end[4:6]}-{end[6:]}")
            dt = pd.to_datetime(dl["delist_date"], errors="coerce")
            n_delist = int(((dt >= d0) & (dt <= d1)).sum())
            note = "上交所/深交所「暂停、终止上市」口径，属上限估计"
    except Exception as e:                                    # 网络问题不该中断回测
        note = f"退市名单获取失败（{type(e).__name__}），偏差按 0 计"

    years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25, 1e-9)
    rate = n_delist / (n_delist + alive) if n_delist else 0.0
    return {
        "sample": len(codes), "listed_after_start": len(late),
        "delisted_in_period": n_delist, "universe_size": alive,
        "delist_rate_pct": _f(rate * 100, 2),
        "bias_pp_per_year": _f(rate * 0.85 / years * 100, 2),
        "years": _f(years, 2), "note": note,
    }


def _pack(res) -> dict:
    """把一个 Result 压成前端能直接画图的结构。"""
    eq = res.equity.dropna()
    base = float(eq.iloc[0])
    bench = res.benchmark.reindex(eq.index).ffill().dropna()
    bench = bench / float(bench.iloc[0]) if len(bench) else bench
    dd = eq / eq.cummax() - 1

    last = res.holdings[res.holdings["date"] == res.holdings["date"].max()] \
        if len(res.holdings) else res.holdings
    tot = float(last["value"].sum()) if len(last) else 1.0
    holdings = [{"code": r.code, "shares": int(r.shares),
                 "value": _f(r.value, 0),
                 "weight": _f(r.value / (tot or 1), 4)}
                for r in last.sort_values("value", ascending=False).itertuples()]

    sell = res.trades[res.trades["side"] == "sell"] if len(res.trades) else res.trades

    return {
        "metrics": {k: _f(v) for k, v in res.metrics.items()},
        "dates": [d.strftime("%Y-%m-%d") for d in eq.index],
        "equity": [_f(v / base, 4) for v in eq],
        "bench": [_f(v, 4) for v in bench.reindex(eq.index).ffill()],
        "dd": [_f(v, 4) for v in dd],
        "yearly": [{"year": int(y), "strategy": _f(r["策略"]), "bench": _f(r["基准"]),
                    "excess": _f(r["超额"])}
                   for y, r in res.yearly.iterrows()],
        "holdings": holdings,
        "trade_stats": {
            "count": int(len(res.trades)),
            "buy": int((res.trades["side"] == "buy").sum()) if len(res.trades) else 0,
            "sell": int(len(sell)),
            "amount": _fmt_amt(float(res.trades["amount"].sum()) if len(res.trades) else 0.0),
        },
    }


def _compare(lines: dict) -> dict:
    """把多条曲线对齐到同一日期轴，供叠加对比。"""
    import pandas as pd
    idx = None
    for v in lines.values():
        idx = pd.DatetimeIndex(v["dates"])
        break
    if idx is None:
        return {"dates": [], "series": {}}
    labels = [d.strftime("%Y-%m-%d") for d in idx]
    series = {}
    for key, v in lines.items():
        s = pd.Series(v["equity"], index=pd.DatetimeIndex(v["dates"]))
        series[key] = [_f(x, 4) for x in s.reindex(idx).ffill()]
    return {"dates": labels, "series": series}


def _caveats(result: dict) -> list[dict]:
    """把「这组数字哪里会骗你」直接写进结果里，而不是只写在文档里。"""
    out = []
    n = result["pool"]["count"]
    se = 1 / math.sqrt(max(n, 1))
    m = (result["lines"].get("rotation") or result["lines"].get("factor") or {}).get("metrics", {})
    ic = m.get("ic_mean")
    if ic is not None:
        if abs(ic) < se:
            out.append({"level": "warn", "title": "IC 落在噪声区间",
                        "text": f"IC = {ic:.3f}，而 {n} 只样本的 IC 标准误约 1/√{n} = {se:.2f}。"
                                f"两者不可区分，这条选股信号在统计上与随机排序无异。"})
        else:
            out.append({"level": "ok", "title": "IC 超出噪声区间",
                        "text": f"IC = {ic:.3f} > 标准误 {se:.2f}，方向可能真实，但仍需样本外验证。"})
    if n < 300:
        out.append({"level": "warn", "title": "样本太小，收益不可外推",
                    "text": f"当前只用 {n} 只股票。换一个随机种子重跑，年化收益会明显变化——"
                            f"这正是「抽样运气」而非「策略能力」的证据。"})
    if "rotation" in result["lines"]:
        out.append({"level": "warn", "title": "轮动只有 3 只标的，无法做显著性检验",
                    "text": "3 家 × 约 60 个月 × 1 个阈值，看不出是方法有效还是这个板块恰好走强。"
                            "换到半导体或有色可能完全不成立。"})
    b = result["bias"]
    if b["listed_after_start"]:
        out.append({"level": "warn", "title": "存在存活偏差，基准被高估",
                    "text": f"样本中 {b['listed_after_start']} 只是回测区间内才上市的，"
                            f"区间外退市的股票一只都取不到；按现有口径基准年化约虚高 "
                            f"{b['bias_pp_per_year']} 个百分点（下限）。"})
    out.append({"level": "info", "title": "回测 ≠ 未来",
                "text": "引擎已按 T+1 开盘成交、涨跌停拦截、双边成本计算，但仍无法模拟冲击成本、"
                        "分红除权差异与真实成交队列。实盘收益通常低于回测。"})
    return out


# ---------------------------------------------------------------- 主管线
def pipeline(p: dict, job: Job) -> dict:
    import pandas as pd

    start, end = _s8(p["start"]), _s8(p["end"])
    if start >= end:
        raise ValueError("开始日期必须早于结束日期")
    top = max(1, min(int(p["top"]), 100))
    mode = p["mode"]
    cost_on = bool(p.get("cost", True))

    job.bump(6, "准备股票池")
    codes = _prepare_pool(p, job)
    job.log(f"股票池 {len(codes)} 只：{'、'.join(codes[:8])}{' …' if len(codes) > 8 else ''}")

    job.bump(14, f"拉取行情（腾讯源，约 {max(len(codes), 1) * 0.45:.0f} 秒）")
    prices = rd.fetch_prices(
        codes, start, end, use_cache=bool(p.get("use_cache", True)),
        progress=lambda i, n: job.quiet(14 + 30 * i / max(n, 1), f"拉取行情 {i}/{n}"))
    if not prices["close"].shape[1]:
        raise RuntimeError("没有取到任何行情，请检查网络或股票代码")
    close = prices["close"]
    job.log(f"行情：{close.shape[1]} 只 × {len(close)} 个交易日，"
            f"{close.index[0]:%Y-%m-%d} → {close.index[-1]:%Y-%m-%d}")

    job.bump(46, "拉取估值（百度源）")
    pe = rd.fetch_pe(
        codes, use_cache=bool(p.get("use_cache", True)),
        progress=lambda i, n: job.quiet(46 + 10 * i / max(n, 1), f"拉取估值 {i}/{n}"))
    panel = rd.to_panel(prices, pe)
    job.log(f"估值：{len(pe):,} 条 PE-TTM 记录，覆盖 {pe['code'].nunique()} 只")

    cfg = Config(top_n=top, capital=float(p.get("capital", 1_000_000)),
                 commission=0.00025 if cost_on else 0.0,
                 stamp_tax=0.0005 if cost_on else 0.0,
                 slippage=0.001 if cost_on else 0.0)
    cfg.start, cfg.end = str(close.index[0].date()), str(close.index[-1].date())

    lines, reports, raw = {}, {}, {}
    sig = None

    if mode in ("factor", "both"):
        job.bump(58, f"多因子回测（持仓 {top} 只）")
        res = run(panel, cfg)
        raw["factor"] = res
        lines["factor"] = _pack(res)
        m = lines["factor"]["metrics"]
        job.log(f"多因子：年化 {m['cagr']*100:.2f}% ｜ 超额 {(m.get('excess_cagr') or 0)*100:.2f}% "
                f"｜ 回撤 {m['max_dd']*100:.1f}% ｜ IC {(m.get('ic_mean') or 0):.3f}")
        rp = build(res, job.dir / "report_factor.html",
                   "多因子选股：价值/质量/成长/动量等权排名，月度调仓，次日开盘成交")
        reports["factor"] = rp.name

    if mode in ("rotation", "both"):
        members = [c for c in GRID_MEMBERS if c in close.columns]
        if len(members) < 2:
            job.log("样本内电网设备不足 2 只，跳过轮动")
        else:
            job.bump(72, "电网设备轮动")
            r = run_rotation(panel, members, cfg, float(p.get("threshold", 0.0)))
            for k in ("rotation", "sector_only"):
                raw[k] = r[k]
                lines[k] = _pack(r[k])
                m = lines[k]["metrics"]
                job.log(f"{'轮动' if k=='rotation' else '纯持板块'}：年化 {m['cagr']*100:.2f}% "
                        f"｜ 回撤 {m['max_dd']*100:.1f}% ｜ 夏普 {m['sharpe']:.2f}")
            rp = build(r["rotation"], job.dir / "report_rotation.html",
                       "电网设备轮动：RS20/RS60 强度分 > 0 时持三只等权，否则回到全市场多因子")
            reports["rotation"] = rp.name

            st = r["strength"].dropna(subset=["score"])
            others = [c for c in close.columns if c not in members]
            if st.empty:
                sig = None
                job.log("强度分全部为空（池内无板块外标的），跳过信号展示")
            else:
                last = st.iloc[-1]
                note = None
                if not others:
                    note = "池内只有板块成员，没有可比市场，强度分恒为 0（轮动退化为死拿）"
                    job.log(note)
                job.log(f"当前强度分 {last['score']:+.2f} → "
                        f"{'持有电网设备' if last['score'] > 0 else '回到全市场'}")
                sig = {
                    "dates": [d.strftime("%Y-%m-%d") for d in st.index],
                    "score": [_f(v, 3) for v in st["score"]],
                    "sector": [_f(v, 4) for v in r["strength"]["sector"].reindex(st.index).ffill()],
                    "market": [_f(v, 4) for v in r["strength"]["market"].reindex(st.index).ffill()],
                    "last": {"date": str(last.name.date()), "score": _f(last["score"], 3),
                             "rs20": _f(last.get("rs20"), 4), "rs60": _f(last.get("rs60"), 4),
                             "signal": "持有电网设备" if last["score"] > 0 else "回到全市场"},
                    "members": members,
                    "note": note,
                }
    else:
        sig = None

    job.bump(86, "测算存活偏差")
    bias = _bias(codes, close, start, end)
    job.log(f"存活偏差：区间退市 {bias['delisted_in_period']} 只 / 现存 {bias['universe_size']} 只 "
            f"→ 基准年化约虚高 {bias['bias_pp_per_year']} 个百分点（{bias['note']}）")

    job.bump(95, "生成报告与导出文件")
    result = {
        "pool": {"count": len(codes), "codes": codes[:30],
                 "first": str(close.index[0].date()), "last": str(close.index[-1].date()),
                 "days": int(len(close))},
        "params": p,
        "bias": bias,
        "strength": sig if mode in ("rotation", "both") else None,
        "lines": lines,
        "compare": _compare(lines),
        "reports": reports,
    }
    result["caveats"] = _caveats(result)

    # CSV 导出：交易明细 / 期末持仓 / 净值（取主策略，不重跑回测）
    job.dir.mkdir(parents=True, exist_ok=True)
    try:
        main = raw.get("rotation") or raw.get("factor")
        _dump_csv(job, main)
    except Exception as e:
        job.log(f"CSV 导出跳过：{type(e).__name__}: {e}")

    job.reports = {k: f"/report?id={job.id}&line={k}" for k in reports}
    job.bump(100, "完成")
    return result


def _dump_csv(job: Job, res) -> None:
    """把回测的原始表落盘，前端可直接下载。"""
    if res is None:
        return
    res.trades.to_csv(job.dir / "trades.csv", index=False, encoding="utf-8-sig")
    res.holdings.to_csv(job.dir / "holdings.csv", index=False, encoding="utf-8-sig")
    eq = res.equity.dropna()
    (eq / eq.iloc[0]).rename("nav").to_csv(job.dir / "nav.csv", encoding="utf-8-sig")
    job.log("已导出 trades.csv / holdings.csv / nav.csv")


def _worker(job: Job) -> None:
    try:
        job.result = pipeline(job.params, job)
        job.status = "done"
    except Exception as e:
        job.status = "error"
        job.error = f"{type(e).__name__}: {e}"
        job.log(f"失败：{job.error}")
        traceback.print_exc()
    finally:
        job.elapsed = time.time() - job.t0
        try:
            job.save()
        except Exception:
            traceback.print_exc()


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"QuantLab/{VERSION}"

    def log_message(self, fmt, *args):          # 安静点，别把日志刷满屏幕
        pass

    # --- helpers
    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str) -> None:
        if not path.exists() or not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path

        if path == "/api/health":
            return self._json({"ok": True, "version": VERSION, "pid": os.getpid()})
        if path == "/api/meta":
            return self._json(self_meta())
        if path == "/api/job":
            job = _JOBS.get(q.get("id", ""))
            if not job:
                return self._json({"error": "job 不存在"}, 404)
            d = job.snapshot()
            if job.status == "done":
                d["result"] = job.result
            return self._json(d)
        if path == "/api/history":
            return self._json(history())
        if path == "/api/replay":
            st = _load_job(q.get("id", ""))
            if not st:
                return self._json({"error": "记录不存在"}, 404)
            return self._json(st)
        if path == "/report":
            jid, line = q.get("id", ""), q.get("line", "factor")
            fp = JOBS / jid / f"report_{line}.html"
            return self._file(fp, "text/html; charset=utf-8")
        if path.startswith("/api/export"):
            jid, kind = q.get("id", ""), q.get("kind", "trades")
            fp = JOBS / jid / f"{kind}.csv"
            if not fp.exists():
                return self._json({"error": "文件不存在"}, 404)
            data = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{jid}_{kind}.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)

        # 静态文件
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        fp = (STATIC / rel).resolve()
        if STATIC.resolve() not in fp.parents and fp != STATIC.resolve():
            return self._json({"error": "forbidden"}, 403)
        ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}.get(fp.suffix, "text/plain")
        return self._file(fp, ctype)

    # --- POST
    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "请求体不是合法 JSON"}, 400)

        if u.path == "/api/run":
            params = _normalize(body)
            job = Job(params)
            with _LOCK:
                _JOBS[job.id] = job
            threading.Thread(target=_worker, args=(job,), daemon=True).start()
            return self._json({"id": job.id})
        if u.path == "/api/cancel":
            job = _JOBS.get(body.get("id", ""))
            if job and job.status == "running":
                job.cancel = True
                job.log("收到取消请求（当前步骤结束后停止）")
            return self._json({"ok": True})
        if u.path == "/api/reveal":
            import subprocess
            jid = body.get("id") or ""
            target = (JOBS / jid) if jid else OUT
            if not target.exists():
                target = OUT
            try:
                subprocess.run(["open", str(target)], check=False, timeout=5)
                return self._json({"ok": True, "path": str(target)})
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        if u.path == "/api/shutdown":
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
            return self._json({"ok": True})
        return self._json({"error": "unknown endpoint"}, 404)


def _normalize(b: dict) -> dict:
    p = {
        "pool": b.get("pool", "sample"),
        "n": int(b.get("n", 100)),
        "codes": str(b.get("codes", "")),
        "start": b.get("start", "2021-01-01"),
        "end": b.get("end", datetime.now().strftime("%Y-%m-%d")),
        "top": int(b.get("top", 20)),
        "mode": b.get("mode", "both"),
        "seed": int(b.get("seed", 42)),
        "capital": float(b.get("capital", 1_000_000)),
        "threshold": float(b.get("threshold", 0.0)),
        "cost": bool(b.get("cost", True)),
        "use_cache": bool(b.get("use_cache", True)),
    }
    p["n"] = max(5, min(p["n"], 800))
    p["top"] = max(1, min(p["top"], 100))
    if p["mode"] not in ("factor", "rotation", "both"):
        p["mode"] = "both"
    if p["pool"] not in ("sample", "custom", "grid"):
        p["pool"] = "sample"
    return p


def self_meta() -> dict:
    cache = list(rd.CACHE.glob("prices_*.pkl")) if rd.CACHE.exists() else []
    return {
        "version": VERSION,
        "python": sys.version.split()[0],
        "base": str(BASE),
        "cache": {"files": len(cache),
                  "prices": len(list(rd.CACHE.glob("prices_*.pkl"))) if rd.CACHE.exists() else 0,
                  "pe": len(list(rd.CACHE.glob("pe_*.pkl"))) if rd.CACHE.exists() else 0},
        "grid": GRID_MEMBERS,
        "jobs": len(list(JOBS.glob("*/state.json"))),
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def history() -> list[dict]:
    rows = []
    for fp in sorted(JOBS.glob("*/state.json"), reverse=True)[:30]:
        try:
            d = json.loads(fp.read_text())
        except Exception:
            continue
        rows.append({
            "id": d.get("id"), "status": d.get("status"),
            "finished": d.get("finished"), "params": d.get("params"),
            "headline": d.get("headline"), "reports": d.get("reports"),
        })
    return rows


def _load_job(jid: str) -> dict | None:
    fp = JOBS / jid / "state.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text())
    except Exception:
        return None


def _free_port(preferred: int) -> int:
    for port in [preferred, preferred + 1, preferred + 2, preferred + 3, 0]:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    port = _free_port(args.port)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"量化工作台已启动：{url}   （关闭此进程即停止服务）", flush=True)
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
