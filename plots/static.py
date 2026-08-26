"""Layer 1 + Layer 2 analysis: medium vs xhigh, plus the per-task allocation gap.

Reads results/runs.jsonl (layer L1, graded). Emits:
  - console table: per-effort solve rate (bootstrap CI), mean tokens, $, wall
  - per-task resolution matrix
  - Layer 2: hypothetical per-task-best-effort selection (motivation only —
    selection conditions on outcomes; stated wherever shown)
  - plots/static.png

Tokens metric = usage.total_tokens (input+cache+output+reasoning, as billed);
output+reasoning also reported since that is the compute actually purchased.
"""

import json
import random
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max"]


def load_l1():
    rows = [json.loads(l) for l in (ROOT / "results" / "runs.jsonl").read_text().splitlines() if l.strip()]
    return [r for r in rows if r.get("layer") == "L1" and r["phase"] == "fork_source"
            and r["status"] == "ok" and r.get("resolved") is not None]


def arm_of(r):
    """Group arm. Effort proved inert (docs/dial-finding.md), so the real
    task-level arm is the model; the medium/xhigh split is kept only for the
    A/A appendix (see --by-effort)."""
    return r["model"]


def out_tokens(r):
    u = r.get("usage", {})
    return (u.get("output_tokens") or 0) + (u.get("reasoning_tokens") or 0)


def total_tokens(r):
    return r.get("usage", {}).get("total_tokens") or 0


def boot_ci(vals, n=5000, seed=13):
    if not vals:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(vals, k=len(vals))) / len(vals) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main():
    rows = load_l1()
    if not rows:
        print("no graded L1 rows yet")
        return

    by_effort = defaultdict(list)
    for r in rows:
        by_effort[r["effort"]].append(r)
    efforts = [e for e in EFFORT_ORDER if e in by_effort]

    print(f"{'effort':8s} {'n':>3s} {'solve':>6s} {'CI95':>14s} {'out+reason tok':>15s} "
          f"{'total tok':>12s} {'$':>7s} {'wall(min,emul)':>14s}")
    stats = {}
    for e in efforts:
        rs = by_effort[e]
        solves = [1.0 if r["resolved"] else 0.0 for r in rs]
        rate = sum(solves) / len(solves)
        lo, hi = boot_ci(solves)
        stats[e] = {
            "n": len(rs), "rate": rate, "ci": (lo, hi),
            "out_tok": sum(out_tokens(r) for r in rs) / len(rs),
            "tot_tok": sum(total_tokens(r) for r in rs) / len(rs),
            "cost": sum(r["cost_usd"] for r in rs) / len(rs),
            "wall": sum(r["wall_s"] for r in rs) / len(rs) / 60,
        }
        s = stats[e]
        print(f"{e:8s} {s['n']:3d} {s['rate']:6.0%} [{lo:5.0%},{hi:5.0%}] {s['out_tok']:15,.0f} "
              f"{s['tot_tok']:12,.0f} {s['cost']:7.2f} {s['wall']:14.1f}")

    # per-task matrix (last run per task/effort wins for display; reps shown as fractions)
    tasks = sorted({r["task_id"] for r in rows})
    print("\nper-task resolution (fraction of reps solved):")
    print(f"{'task':45s} " + " ".join(f"{e:>7s}" for e in efforts))
    per = defaultdict(lambda: defaultdict(list))
    for r in rows:
        per[r["task_id"]][r["effort"]].append(bool(r["resolved"]))
    for t in tasks:
        cells = []
        for e in efforts:
            v = per[t][e]
            cells.append(f"{sum(v)}/{len(v)}" if v else "-")
        print(f"{t:45s} " + " ".join(f"{c:>7s}" for c in cells))

    # Layer 2: per-task cheapest successful effort (motivation only; selection
    # conditions on observed outcomes — an oracle, not a deployable policy)
    if len(efforts) >= 2:
        chosen_tok, chosen_solved = [], 0
        priciest = efforts[-1]
        for t in tasks:
            pick = None
            for e in efforts:  # cheapest tier first
                v = per[t][e]
                if v and any(v):
                    solved_runs = [r for r in by_effort[e] if r["task_id"] == t and r["resolved"]]
                    pick = sum(total_tokens(r) for r in solved_runs) / len(solved_runs)
                    chosen_solved += 1
                    break
            if pick is None:  # unsolved everywhere: pay the priciest tier's cost
                fails = [r for r in by_effort[priciest] if r["task_id"] == t]
                pick = sum(total_tokens(r) for r in fails) / max(len(fails), 1)
            chosen_tok.append(pick)
        gap_tok = sum(chosen_tok) / len(chosen_tok)
        print(f"\nLayer 2 (oracle-selection caveat applies): per-task-best -> "
              f"{chosen_solved}/{len(tasks)} solved at {gap_tok:,.0f} mean total tok/task")
        for e in efforts:
            s = stats[e]
            print(f"  vs all-{e:7s}: {s['rate']:6.0%} at {s['tot_tok']:,.0f} tok/task "
                  f"({(1 - gap_tok / s['tot_tok']):+.0%} tokens if selected per task)")

    # plot
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.6))
    xs = range(len(efforts))
    rates = [stats[e]["rate"] for e in efforts]
    errs = [[stats[e]["rate"] - stats[e]["ci"][0] for e in efforts],
            [stats[e]["ci"][1] - stats[e]["rate"] for e in efforts]]
    ax1.bar(xs, rates, yerr=errs, capsize=4, color=["#7aa6c2", "#c25e4c"][:len(efforts)] or None)
    ax1.set_xticks(list(xs), efforts)
    ax1.set_ylim(0, 1)
    ax1.set_ylabel("verified solve rate")
    ax1.set_title(f"SWE-bench Verified subset (n={stats[efforts[0]]['n']} tasks/arm)")
    for e in efforts:
        s = stats[e]
        ax2.scatter(s["tot_tok"], s["rate"], s=80, label=e)
        ax2.annotate(e, (s["tot_tok"], s["rate"]), textcoords="offset points", xytext=(8, -4))
    ax2.set_xlabel("mean total tokens / task")
    ax2.set_ylabel("verified solve rate")
    ax2.set_ylim(0, 1)
    ax2.set_title("solve rate vs token spend")
    fig.suptitle("Grok Build, single agent, effort as the only variable", fontsize=10)
    fig.tight_layout()
    out = ROOT / "plots" / "static.png"
    fig.savefig(out, dpi=160)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
