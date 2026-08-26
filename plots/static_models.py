"""Task-level model comparison: all-build-0.1 vs all-4.6 vs per-task-best.

Effort proved inert (docs/dial-finding.md), so build-0.1 pools all its runs
(the medium/xhigh A/A split lives in static.py as the appendix). Oracle caveat
applies to per-task-best: selection conditions on observed outcomes.
"""

import json
import random
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
ORDER = ["grok-build-0.1", "grok-4.6"]


def boot_ci(vals, n=5000, seed=13):
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(vals, k=len(vals))) / len(vals) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main():
    rows = [json.loads(l) for l in open(ROOT / "results" / "runs.jsonl") if l.strip()]
    l1 = [r for r in rows if r.get("layer") == "L1" and r["phase"] == "fork_source"
          and r["status"] == "ok" and r.get("resolved") is not None]
    by = defaultdict(list)
    for r in l1:
        by[r["model"]].append(r)
    tasks = sorted({r["task_id"] for r in l1})

    stats = {}
    for m in ORDER:
        rs = by[m]
        solves = [1.0 if r["resolved"] else 0.0 for r in rs]
        stats[m] = {
            "n": len(rs),
            "rate": sum(solves) / len(solves),
            "ci": boot_ci(solves),
            "tok": sum(r["usage"].get("total_tokens", 0) for r in rs) / len(rs),
            "cost": sum(r["cost_usd"] for r in rs) / len(rs),
        }

    oracle_cost, oracle_tok, solved = [], [], 0
    for t in tasks:
        pick = None
        for m in ORDER:  # cheapest model first
            wins = [r for r in by[m] if r["task_id"] == t and r["resolved"]]
            if wins:
                pick = wins
                solved += 1
                break
        if pick is None:
            pick = [r for r in by[ORDER[-1]] if r["task_id"] == t]
        oracle_cost.append(sum(r["cost_usd"] for r in pick) / len(pick))
        oracle_tok.append(sum(r["usage"].get("total_tokens", 0) for r in pick) / len(pick))
    stats["per-task-best*"] = {
        "n": len(tasks), "rate": solved / len(tasks), "ci": (None, None),
        "tok": sum(oracle_tok) / len(oracle_tok), "cost": sum(oracle_cost) / len(oracle_cost),
    }

    print(f"{'arm':16s} {'n':>3s} {'solve':>6s} {'tok/task':>10s} {'$/task':>7s}")
    for m, s in stats.items():
        print(f"{m:16s} {s['n']:3d} {s['rate']:6.0%} {s['tok']:10,.0f} {s['cost']:7.2f}")
    print("* oracle selection conditions on outcomes; motivation only")

    print("\nper-task (fraction of runs solved):")
    for t in tasks:
        cells = []
        for m in ORDER:
            v = [r["resolved"] for r in by[m] if r["task_id"] == t]
            cells.append(f"{sum(v)}/{len(v)}" if v else "-")
        print(f"  {t:40s} " + "  ".join(f"{c:>6s}" for c in cells))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    labels = list(stats)
    colors = ["#7aa6c2", "#c25e4c", "#6aa84f"]
    rates = [stats[m]["rate"] for m in labels]
    errs = [[stats[m]["rate"] - (stats[m]["ci"][0] or stats[m]["rate"]) for m in labels],
            [(stats[m]["ci"][1] or stats[m]["rate"]) - stats[m]["rate"] for m in labels]]
    ax1.bar(range(len(labels)), rates, yerr=errs, capsize=4, color=colors)
    for i, m in enumerate(labels):
        s = stats[m]
        ax1.text(i, min(s["rate"] + 0.07, 1.08), f"{s['rate']:.0%}\n${s['cost']:.2f}/task",
                 ha="center", fontsize=9, fontweight="bold")
    ax1.set_xticks(range(len(labels)), labels, fontsize=8)
    ax1.set_ylim(0, 1.22)
    ax1.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax1.set_ylabel("verified solve rate")
    ax1.set_title(f"SWE-bench Verified subset ({len(tasks)} tasks)", fontsize=10)
    ax1.spines[["top", "right"]].set_visible(False)

    offsets = {"grok-build-0.1": (10, -3), "grok-4.6": (-16, 9), "per-task-best*": (-20, -16)}
    for i, m in enumerate(labels):
        s = stats[m]
        ax2.scatter(s["cost"], s["rate"], s=110, color=colors[i], zorder=3)
        ax2.annotate(m, (s["cost"], s["rate"]), textcoords="offset points",
                     xytext=offsets.get(m, (8, -4)), fontsize=9)
    ax2.annotate("", xy=(stats["per-task-best*"]["cost"], stats["per-task-best*"]["rate"]),
                 xytext=(stats["grok-4.6"]["cost"], stats["grok-4.6"]["rate"]),
                 arrowprops=dict(arrowstyle="->", color="#999", lw=1.2))
    ax2.text((stats["grok-4.6"]["cost"] + stats["per-task-best*"]["cost"]) / 2, 0.955,
             "−30% cost,\nsame solve rate", ha="center", fontsize=8, color="#555")
    ax2.set_xlabel("mean $ / task")
    ax2.set_ylabel("verified solve rate")
    ax2.set_ylim(0.65, 1.06)
    ax2.set_xlim(0.24, 0.56)
    ax2.set_title("solve rate vs dollar cost", fontsize=10)
    ax2.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Task-level model allocation (single agent, 40-turn cap, effort constant)",
                 fontsize=10)
    fig.tight_layout()
    out = ROOT / "plots" / "static_models.png"
    fig.savefig(out, dpi=160)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
