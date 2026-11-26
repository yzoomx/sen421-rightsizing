"""Analysis of the SEN421 right-sizing experiment.

Reads the raw k6 summaries and CPU counters copied from the load generator,
plus the official Hetzner price snapshot saved by scripts/preflight.ps1, and
produces:
  results/runs.csv      one row per configuration and repetition
  results/steps.csv     one row per load step of the stepped test
  results/summary.csv   mean and standard deviation per configuration, with cost
  report/figures/*.pdf  figures for the IEEE report (and .png for slides)
  report/tables/*.tex   LaTeX tables for the IEEE report

Usage (from the repository root):
  python analysis/analyze.py --raw results/raw --pricing results/pricing_2026-11-26.json --location nbg1

Cost model (stated in the report):
  * prices are in the account's billing currency (pricing.currency in the snapshot)
  * production monthly price of a configuration =
      single server:  server price + 1 primary IPv4
      N servers + LB: N x server price + load balancer price
        (behind a load balancer the servers need no public IPv4)
  * steady-state hourly cost = monthly price / 730 h
  * cost per 1,000 requests = hourly cost / (3600 s x throughput in req/s) x 1000
"""
import argparse
import csv
import glob
import json
import math
import os
import statistics as st

HOURS_PER_MONTH = 730
CUR = "EUR"   # replaced by the currency in the price snapshot (Hetzner can bill in EUR or USD)

# Reference categorical palette, slots 1-4 in fixed order (validated: CVD and
# normal-vision separation pass; contrast < 3:1 for 3 slots, so every bar and
# line carries a visible label).
COLORS = ["#257de0", "#f47230", "#00b894", "#f2a600"]
MARKERS = ["o", "s", "^", "D"]
TEXT = "#191919"
TEXT2 = "#666666"
GRID = "#e5e5e5"


# ---------------------------------------------------------------- helpers
def load_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def mean_sd(values):
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return float("nan"), float("nan")
    return st.mean(vals), (st.stdev(vals) if len(vals) > 1 else 0.0)


def metric(summary, name, field):
    try:
        return float(summary["metrics"][name]["values"][field])
    except (KeyError, TypeError, ValueError):
        return float("nan")


def cpu_pct(before, after):
    """Busy % and steal % of the whole VM between two /proc/stat snapshots."""
    keys = ["user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"]
    if not before or not after or any(k not in before or k not in after for k in keys):
        return float("nan"), float("nan")
    d = {k: after[k] - before[k] for k in keys}
    total = sum(d.values())
    if total <= 0:
        return float("nan"), float("nan")
    busy = total - d["idle"] - d["iowait"]
    return 100.0 * busy / total, 100.0 * d["steal"] / total


def read_targets(raw):
    configs = []
    with open(os.path.join(raw, "targets.txt"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            name, stype, count, url, backends = line.split("|")
            configs.append({"name": name, "type": stype, "count": int(count),
                            "backends": len(backends.split(","))})
    return configs


# ---------------------------------------------------------------- pricing
def price_for(entries, name, location):
    for e in entries or []:
        if e.get("name") == name:
            for p in e.get("prices", []):
                if p.get("location") == location:
                    return float(p["price_hourly"]["net"]), float(p["price_monthly"]["net"])
    raise KeyError(f"no price for {name} in {location}")


def ipv4_price(pricing, location):
    for e in pricing.get("primary_ips", []):
        if e.get("type") == "ipv4":
            prices = e.get("prices", [])
            chosen = next((p for p in prices if p.get("location") == location), prices[0] if prices else None)
            if chosen:
                return float(chosen["price_hourly"]["net"]), float(chosen["price_monthly"]["net"])
    return 0.0, 0.0


def config_prices(cfg, pricing, server_types, location, lb_type):
    s_h, s_m = price_for(server_types, cfg["type"], location)
    ip_h, ip_m = ipv4_price(pricing, location)
    if cfg["count"] > 1:
        lb_h, lb_m = price_for(pricing.get("load_balancer_types"), lb_type, location)
        prod_monthly = cfg["count"] * s_m + lb_m
        exp_hourly = cfg["count"] * (s_h + ip_h) + lb_h      # what the experiment paid per hour
    else:
        prod_monthly = s_m + ip_m
        exp_hourly = s_h + ip_h
    return {"server_hourly": s_h, "server_monthly": s_m, "ipv4_monthly": ip_m,
            "prod_monthly": prod_monthly, "exp_hourly": exp_hourly}


def cost_per_1k_micro(prod_monthly, rps):
    """Cost of 1,000 requests in millionths of the billing currency (1 micro = 0.000001)."""
    if not rps or math.isnan(rps) or rps <= 0:
        return float("nan")
    hourly = prod_monthly / HOURS_PER_MONTH
    return hourly / (3600.0 * rps) * 1000.0 * 1e6


# ---------------------------------------------------------------- parsing runs
def parse(raw, configs, slo_ms, max_fail, max_dropped):
    runs, steps = [], []
    for cfg in configs:
        d = os.path.join(raw, cfg["name"])
        for sat_path in sorted(glob.glob(os.path.join(d, "run*_sat.json"))):
            run = int(os.path.basename(sat_path)[3:].split("_")[0])
            sat = load_json(sat_path)
            if not sat:
                continue
            cpu = {"busy": [], "steal": []}
            for b in range(1, cfg["backends"] + 1):
                busy, steal = cpu_pct(load_json(os.path.join(d, f"cpu_run{run}_sat_before_backend{b}.json")),
                                      load_json(os.path.join(d, f"cpu_run{run}_sat_after_backend{b}.json")))
                cpu["busy"].append(busy)
                cpu["steal"].append(steal)
            lg_busy, _ = cpu_pct(load_json(os.path.join(d, f"cpu_run{run}_sat_before_loadgen.json")),
                                 load_json(os.path.join(d, f"cpu_run{run}_sat_after_loadgen.json")))
            row = {
                "config": cfg["name"], "server_type": cfg["type"], "count": cfg["count"], "run": run,
                "sat_rps": metric(sat, "http_reqs", "rate"),
                "sat_p50_ms": metric(sat, "http_req_duration", "med"),
                "sat_p95_ms": metric(sat, "http_req_duration", "p(95)"),
                "sat_p99_ms": metric(sat, "http_req_duration", "p(99)"),
                "sat_fail_pct": 100 * metric(sat, "http_req_failed", "rate"),
                "cards_p95_ms": metric(sat, "http_req_duration{name:cards}", "p(95)"),
                "review_p95_ms": metric(sat, "http_req_duration{name:review}", "p(95)"),
                "cpu_busy_pct": mean_sd(cpu["busy"])[0],
                "cpu_steal_pct": mean_sd(cpu["steal"])[0],
                "loadgen_busy_pct": lg_busy,
            }
            # Stepped test: highest offered rate at which this step and every lower step met the SLO.
            step = load_json(os.path.join(d, f"run{run}_step.json"))
            capacity = float("nan")
            if step and "step_config" in step:
                sc = step["step_config"]
                all_ok_so_far = True
                capacity = 0.0
                for i, rate in enumerate(sc["steps"], start=1):
                    tag = f"{{scenario:step_{i}}}"
                    count = metric(step, "http_reqs" + tag, "count")
                    dropped = metric(step, "dropped_iterations" + tag, "count")
                    dropped = 0.0 if math.isnan(dropped) else dropped
                    p95 = metric(step, "http_req_duration" + tag, "p(95)")
                    fail = metric(step, "http_req_failed" + tag, "rate")
                    fail = 0.0 if math.isnan(fail) else fail
                    offered = rate * sc["step_seconds"]
                    ok = (not math.isnan(p95) and p95 < slo_ms and fail < max_fail
                          and dropped / offered < max_dropped)
                    steps.append({
                        "config": cfg["name"], "run": run, "step": i, "offered_rps": rate,
                        "achieved_rps": (count / sc["step_seconds"]) if not math.isnan(count) else float("nan"),
                        "p95_ms": p95, "fail_pct": 100 * fail,
                        "dropped_pct": 100 * dropped / offered, "meets_slo": ok,
                    })
                    if ok and all_ok_so_far:
                        capacity = float(rate)
                    else:
                        all_ok_so_far = False
            row["slo_capacity_rps"] = capacity
            runs.append(row)
    return runs, steps


# ---------------------------------------------------------------- outputs
def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})


def summarize(configs, runs, prices):
    out = []
    for cfg in configs:
        rs = [r for r in runs if r["config"] == cfg["name"]]
        p = prices[cfg["name"]]
        row = {"config": cfg["name"], "server_type": cfg["type"], "count": cfg["count"], "n_runs": len(rs)}
        row["slo_n"] = sum(1 for r in rs if not math.isnan(r["slo_capacity_rps"]))
        for key in ["sat_rps", "sat_p50_ms", "sat_p95_ms", "sat_fail_pct", "cards_p95_ms", "review_p95_ms",
                    "cpu_busy_pct", "cpu_steal_pct", "loadgen_busy_pct", "slo_capacity_rps"]:
            m, s = mean_sd([r[key] for r in rs])
            row[key + "_mean"], row[key + "_sd"] = m, s
        row.update({"currency": CUR, "server_monthly": p["server_monthly"], "prod_monthly": p["prod_monthly"],
                    "exp_hourly": p["exp_hourly"]})
        # cost per 1,000 requests computed per run, then averaged
        for basis, key in [("sat", "sat_rps"), ("slo", "slo_capacity_rps")]:
            m, s = mean_sd([cost_per_1k_micro(p["prod_monthly"], r[key]) for r in rs])
            row[f"cost_1k_{basis}_micro_mean"], row[f"cost_1k_{basis}_micro_sd"] = m, s
        out.append(row)
    return out


def scaling_efficiency(summary):
    """Throughput of N small servers behind the LB / (N x one small server)."""
    singles = {r["server_type"]: r for r in summary if r["count"] == 1}
    res = {}
    for r in summary:
        if r["count"] > 1 and r["server_type"] in singles:
            base = singles[r["server_type"]]
            for key in ["sat_rps_mean", "slo_capacity_rps_mean"]:
                if base[key] and not math.isnan(base[key]):
                    res[(r["config"], key)] = r[key] / (r["count"] * base[key])
    return res


def label(cfg):
    stype = cfg.get("server_type") or cfg.get("type")
    return f"{cfg['count']}x {stype.upper()}" + (" + LB" if cfg["count"] > 1 else "")


def fmt(m, s, nd=0):
    if m is None or math.isnan(m):
        return "--"
    return f"{m:.{nd}f} $\\pm$ {s:.{nd}f}"


def dag(r):
    """Dagger mark when a stepped-test value is based on fewer runs than the others."""
    return "$^\\dagger$" if r["slo_n"] < r["n_runs"] else ""


def partial_note(summary, ncols):
    short = [r for r in summary if r["slo_n"] < r["n_runs"]]
    if not short:
        return []
    r = short[0]
    return [f"\\multicolumn{{{ncols}}}{{l}}{{$^\\dagger$Mean of {r['slo_n']} runs: one stepped test wrote no result file.}} \\\\"]


def write_tables(summary, tables_dir, slo_ms):
    os.makedirs(tables_dir, exist_ok=True)
    lines = [
        "\\begin{table}[htbp]",
        "\\caption{Performance of the Configurations (mean $\\pm$ standard deviation of 3 runs)}",
        "\\label{tab:perf}", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{3pt}",
        "\\begin{tabular}{|l|c|c|c|c|}", "\\hline",
        "\\textbf{Configuration} & \\textbf{Max req/s} & \\textbf{p95 at max (ms)} & "
        f"\\textbf{{req/s at SLO}}$^*$ & \\textbf{{CPU / steal (\\%)}} \\\\", "\\hline",
    ]
    for r in summary:
        lines.append(f"{label(r)} & {fmt(r['sat_rps_mean'], r['sat_rps_sd'])} & "
                     f"{fmt(r['sat_p95_ms_mean'], r['sat_p95_ms_sd'], 1)} & "
                     f"{fmt(r['slo_capacity_rps_mean'], r['slo_capacity_rps_sd'])}{dag(r)} & "
                     f"{r['cpu_busy_pct_mean']:.0f} / {r['cpu_steal_pct_mean']:.1f} \\\\")
        lines.append("\\hline")
    lines += ["\\multicolumn{5}{l}{$^*$Highest offered rate with p95 $<$ " + str(int(slo_ms)) +
              " ms, errors $<$ 1\\%, dropped $<$ 1\\%.} \\\\"] + partial_note(summary, 5) + \
             ["\\end{tabular}", "\\end{table}"]
    with open(os.path.join(tables_dir, "tab_perf.tex"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    lines = [
        "\\begin{table}[htbp]",
        f"\\caption{{Monthly Price and Cost per 1,000 Requests ({CUR}, excl. VAT)}}",
        "\\label{tab:cost}", "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{3pt}",
        "\\begin{tabular}{|l|c|c|c|}", "\\hline",
        f"\\textbf{{Configuration}} & \\textbf{{{CUR}/month}}$^*$ & \\multicolumn{{2}}{{c|}}{{\\textbf{{$\\mu${CUR} per 1,000 req.}}}} \\\\",
        "\\cline{3-4}", " & & \\textbf{at max} & \\textbf{at SLO} \\\\", "\\hline",
    ]
    for r in summary:
        lines.append(f"{label(r)} & {r['prod_monthly']:.2f} & "
                     f"{fmt(r['cost_1k_sat_micro_mean'], r['cost_1k_sat_micro_sd'], 2)} & "
                     f"{fmt(r['cost_1k_slo_micro_mean'], r['cost_1k_slo_micro_sd'], 2)}{dag(r)} \\\\")
        lines.append("\\hline")
    lines += ["\\multicolumn{4}{l}{$^*$Servers + one IPv4 (single server) or + load balancer.} \\\\",
              f"\\multicolumn{{4}}{{l}}{{1 $\\mu${CUR} = 0.000001 {CUR}.}} \\\\"] + partial_note(summary, 4) + \
             ["\\end{tabular}", "\\end{table}"]
    with open(os.path.join(tables_dir, "tab_cost.tex"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def style_axes(ax):
    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)
    for side in ["left", "bottom"]:
        ax.spines[side].set_color(TEXT2)
        ax.spines[side].set_linewidth(0.6)
    ax.tick_params(colors=TEXT2, labelsize=7, width=0.6)
    ax.yaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def bar_chart(summary, key, sd_key, ylabel, path, nd=0):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    names = [label(r).replace(" + LB", "\n+ LB") for r in summary]
    vals = [r[key] for r in summary]
    sds = [r[sd_key] for r in summary]
    x = range(len(summary))
    bars = ax.bar(x, vals, width=0.6, color=COLORS[:len(summary)], edgecolor="white", linewidth=2,
                  yerr=sds, capsize=3, error_kw={"elinewidth": 0.8, "ecolor": TEXT2, "capthick": 0.8})
    top = max(v + s for v, s in zip(vals, sds) if not math.isnan(v))
    for b, v, s in zip(bars, vals, sds):
        if not math.isnan(v):
            ax.text(b.get_x() + b.get_width() / 2, v + s + top * 0.02, f"{v:.{nd}f}",
                    ha="center", va="bottom", fontsize=7, color=TEXT)
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, fontsize=7, color=TEXT)
    ax.set_ylabel(ylabel, fontsize=7.5, color=TEXT)
    ax.set_ylim(0, top * 1.15)
    style_axes(ax)
    fig.tight_layout()
    fig.savefig(path + ".pdf")
    fig.savefig(path + ".png", dpi=200)
    plt.close(fig)


def latency_chart(configs, steps, slo_ms, path):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(3.4, 2.75))
    for ci, cfg in enumerate(configs):
        rows = [s for s in steps if s["config"] == cfg["name"]]
        if not rows:
            continue
        xs, ys = [], []
        for i in sorted({s["step"] for s in rows}):
            at = [s for s in rows if s["step"] == i]
            xs.append(st.mean(s["offered_rps"] for s in at))
            ys.append(st.mean(s["p95_ms"] for s in at if not math.isnan(s["p95_ms"])))
        ax.plot(xs, ys, color=COLORS[ci], linewidth=2, marker=MARKERS[ci], markersize=4,
                markeredgecolor="white", markeredgewidth=0.8, label=label(cfg))
    ax.axhline(slo_ms, color=TEXT2, linewidth=0.8, linestyle="--", label=f"SLO: p95 < {slo_ms:.0f} ms")
    ax.set_yscale("log")
    ax.set_xlabel("Offered load (requests per second)", fontsize=7.5, color=TEXT)
    ax.set_ylabel("p95 response time (ms, log scale)", fontsize=7.5, color=TEXT)
    ax.legend(fontsize=6, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.24), ncol=3,
              handlelength=2.2, columnspacing=1.0)
    style_axes(ax)
    fig.tight_layout()
    fig.savefig(path + ".pdf")
    fig.savefig(path + ".png", dpi=200)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="results/raw")
    ap.add_argument("--pricing", default=None, help="pricing_<date>.json from preflight.ps1 (default: newest)")
    ap.add_argument("--location", default="nbg1")
    ap.add_argument("--lb-type", default="lb11")
    ap.add_argument("--out", default="results")
    ap.add_argument("--report", default="report")
    ap.add_argument("--slo-ms", type=float, default=None, help="default: value used in the test (meta.json)")
    args = ap.parse_args()

    meta = load_json(os.path.join(args.raw, "meta.json")) or {}
    slo_ms = args.slo_ms or float(meta.get("slo_p95_ms", 100))
    pricing_path = args.pricing or sorted(glob.glob(os.path.join(args.out, "pricing_*.json")))[-1]
    snap = load_json(pricing_path)
    pricing = snap["pricing"]
    global CUR
    CUR = pricing.get("currency", "EUR")
    server_types = snap.get("server_types") or pricing.get("server_types")

    configs = read_targets(args.raw)
    prices = {c["name"]: config_prices(c, pricing, server_types, args.location, args.lb_type) for c in configs}
    runs, steps = parse(args.raw, configs, slo_ms, max_fail=0.01, max_dropped=0.01)
    summary = summarize(configs, runs, prices)

    os.makedirs(args.out, exist_ok=True)
    write_csv(os.path.join(args.out, "runs.csv"), runs)
    write_csv(os.path.join(args.out, "steps.csv"), steps)
    write_csv(os.path.join(args.out, "summary.csv"), summary)
    write_tables(summary, os.path.join(args.report, "tables"), slo_ms)

    figs = os.path.join(args.report, "figures")
    os.makedirs(figs, exist_ok=True)
    bar_chart(summary, "sat_rps_mean", "sat_rps_sd", "Maximum throughput (requests/s)",
              os.path.join(figs, "fig_throughput"))
    bar_chart(summary, "cost_1k_slo_micro_mean", "cost_1k_slo_micro_sd",
              f"Cost per 1,000 requests at SLO\n(millionths of a {CUR})", os.path.join(figs, "fig_cost"), nd=2)
    latency_chart(configs, steps, slo_ms, os.path.join(figs, "fig_latency"))

    print(f"Price source: {pricing_path} (accessed {snap.get('accessed_utc')}), location {args.location}")
    print(f"SLO: p95 < {slo_ms:.0f} ms | runs per configuration: {[r['n_runs'] for r in summary]}")
    print(f"{'config':<20}{CUR + '/mo':>8}{'max req/s':>16}{'SLO req/s':>16}{'CPU%':>6}{'steal%':>7}"
          f"{'LG%':>5}{'u' + CUR + '/1k@SLO':>14}")
    for r in summary:
        print(f"{r['config']:<20}{r['prod_monthly']:>8.2f}"
              f"{r['sat_rps_mean']:>9.0f} ±{r['sat_rps_sd']:<5.0f}"
              f"{r['slo_capacity_rps_mean']:>9.0f} ±{r['slo_capacity_rps_sd']:<5.0f}"
              f"{r['cpu_busy_pct_mean']:>6.0f}{r['cpu_steal_pct_mean']:>7.1f}{r['loadgen_busy_pct_mean']:>5.0f}"
              f"{r['cost_1k_slo_micro_mean']:>14.2f}")
    for (cfg, key), eff in scaling_efficiency(summary).items():
        print(f"Scaling efficiency of {cfg} ({key.replace('_mean', '')}): {eff * 100:.0f}%")
    for r in summary:
        if r["slo_n"] < r["n_runs"]:
            print(f"NOTE: {r['config']}: stepped-test result missing for {r['n_runs'] - r['slo_n']} run(s); "
                  f"SLO capacity is the mean of {r['slo_n']} runs.")
    busy_lg = [r["loadgen_busy_pct_mean"] for r in summary if not math.isnan(r["loadgen_busy_pct_mean"])]
    if busy_lg and max(busy_lg) > 85:
        print("WARNING: load generator CPU > 85% - it may have limited the results.")
    hours = None
    try:
        from datetime import datetime
        t0 = datetime.fromisoformat(meta["started_utc"].replace("Z", "+00:00"))
        fin = open(os.path.join(args.raw, "finished.txt")).read().split('"')[3]
        hours = (datetime.fromisoformat(fin.replace("Z", "+00:00")) - t0).total_seconds() / 3600
    except Exception:
        pass
    if hours:
        lg_h, _ = price_for(server_types, meta.get("loadgen_type", "cpx32"), args.location) \
            if meta.get("loadgen_type") else (0.0, 0.0)
        per_hour = sum(p["exp_hourly"] for p in prices.values())
        print(f"Benchmark duration {hours:.2f} h; targets cost about {CUR} {per_hour:.4f}/h "
              f"(excl. load generator). Check the Hetzner Usage page for the actual bill.")


if __name__ == "__main__":
    main()
