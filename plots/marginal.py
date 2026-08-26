"""Layer 3 analysis: paired counterfactual branches, medium vs xhigh.

Reads results/forks.jsonl (checkpoints + branch run ids) and results/runs.jsonl
(branch outcomes). Emits per-checkpoint paired outcomes, aggregate
Δsolve = P̂(solve|xhigh) − P̂(solve|medium) split by signal vs quiet state with
bootstrap CIs over checkpoints, the escalation token premium, and
plots/marginal.png.

Small-n paired design: n is printed on everything; no significance theater.
"""

import json
import random
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent


def read_jsonl(p):
    p = Path(p)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def out_tokens(r):
    u = r.get("usage", {})
    return (u.get("output_tokens") or 0) + (u.get("reasoning_tokens") or 0)


def total_tokens(r):
    return r.get("usage", {}).get("total_tokens") or 0


def boot_ci(vals, n=5000, seed=17):
    if not vals:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(vals, k=len(vals))) / len(vals) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main():
    forks = read_jsonl(ROOT / "results" / "forks.jsonl")
    runs = {r["run_id"]: r for r in read_jsonl(ROOT / "results" / "runs.jsonl")}

    # arm-pair priority: the model-escalation experiment, then the A/A effort study
    PAIRS = [("continue", "escalate"), ("medium", "xhigh")]

    import sys
    want = None
    if len(sys.argv) > 1:
        want = tuple(sys.argv[1].split(","))
    pair = None
    for cand in ([want] if want else PAIRS):
        if any(set(cand) <= set(ck["branch_run_ids"]) for ck in forks):
            pair = cand
            break
    if pair is None:
        print("no complete checkpoint pairs yet")
        return
    a, b = pair  # delta = P(b) - P(a)

    rows = []
    for ck in forks:
        if not set(pair) <= set(ck["branch_run_ids"]):
            continue
        per_eff = {}
        for eff in pair:
            ids = ck["branch_run_ids"][eff]
            branch_runs = [runs[i] for i in ids if i in runs
                           and runs[i]["status"] == "ok" and runs[i].get("resolved") is not None]
            if branch_runs:
                per_eff[eff] = {
                    "p": sum(bool(r["resolved"]) for r in branch_runs) / len(branch_runs),
                    "tok": sum(total_tokens(r) for r in branch_runs) / len(branch_runs),
                    "otok": sum(out_tokens(r) for r in branch_runs) / len(branch_runs),
                    "cost": sum(r["cost_usd"] for r in branch_runs) / len(branch_runs),
                    "n": len(branch_runs),
                }
        if a in per_eff and b in per_eff:
            rows.append({
                "checkpoint_id": ck["checkpoint_id"],
                "task_id": ck["task_id"],
                "group": ck["selected_as"],
                "segment": ck["segment"],
                "failure_streak": ck["state"].get("failure_streak"),
                "p_med": per_eff[a]["p"], "p_xh": per_eff[b]["p"],
                "delta": per_eff[b]["p"] - per_eff[a]["p"],
                "tok_med": per_eff[a]["tok"], "tok_xh": per_eff[b]["tok"],
                "otok_med": per_eff[a]["otok"], "otok_xh": per_eff[b]["otok"],
                "cost_med": per_eff[a]["cost"], "cost_xh": per_eff[b]["cost"],
                "n_med": per_eff[a]["n"], "n_xh": per_eff[b]["n"],
            })

    if not rows:
        print(f"no complete checkpoint pairs yet for {pair}")
        return
    print(f"experiment arms: {a} vs {b} (delta = P({b}) - P({a}))\n")

    print(f"{'checkpoint':58s} {'grp':6s} {'P(med)':>6s} {'P(xh)':>6s} {'Δ':>6s} {'Δtok':>10s}")
    for r in sorted(rows, key=lambda x: (x["group"], x["checkpoint_id"])):
        print(f"{r['checkpoint_id']:58s} {r['group']:6s} {r['p_med']:6.2f} {r['p_xh']:6.2f} "
              f"{r['delta']:+6.2f} {r['tok_xh'] - r['tok_med']:+10,.0f}")

    print()
    agg = {}
    for grp in ("signal", "quiet"):
        g = [r for r in rows if r["group"] == grp]
        if not g:
            continue
        deltas = [r["delta"] for r in g]
        lo, hi = boot_ci(deltas)
        mean_d = sum(deltas) / len(deltas)
        prem = sum(r["tok_xh"] - r["tok_med"] for r in g) / len(g)
        oprem = sum(r["otok_xh"] - r["otok_med"] for r in g) / len(g)
        agg[grp] = {"n": len(g), "delta": mean_d, "ci": (lo, hi), "prem": prem}
        line = (f"{grp:6s} n={len(g):2d}  Δsolve={mean_d:+.2f} [{lo:+.2f},{hi:+.2f}]  "
                f"token premium={prem:+,.0f} total ({oprem:+,.0f} out+reasoning)")
        if mean_d > 0:
            line += f"  -> {prem / mean_d:,.0f} total tok per marginal solve"
        print(line)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    groups = ["signal", "quiet"]
    colors = {"signal": "#c25e4c", "quiet": "#7aa6c2"}
    y = 0
    labels = []
    for grp in groups:
        for r in [x for x in rows if x["group"] == grp]:
            ax1.plot([r["p_med"], r["p_xh"]], [y, y], color=colors[grp], lw=1.5, zorder=1)
            ax1.scatter([r["p_med"]], [y], marker="o", color="#666", zorder=2,
                        label=a if y == 0 else None)
            ax1.scatter([r["p_xh"]], [y], marker="D", color=colors[grp], zorder=2,
                        label=b if y == 0 else None)
            labels.append(f"{r['task_id'].split('__')[-1]} s{r['segment']} ({grp[0]})")
            y += 1
    ax1.set_yticks(range(len(labels)), labels, fontsize=7)
    ax1.set_xlabel("P(verified solve)")
    ax1.set_xlim(-0.05, 1.05)
    ax1.set_title("paired branches per checkpoint")
    ax1.legend(fontsize=7, loc="lower right")

    xs, ds, errs, cols = [], [], [[], []], []
    for i, grp in enumerate(g for g in groups if g in agg):
        a = agg[grp]
        xs.append(f"{grp}\n(n={a['n']})")
        ds.append(a["delta"])
        errs[0].append(a["delta"] - a["ci"][0])
        errs[1].append(a["ci"][1] - a["delta"])
        cols.append(colors[grp])
    ax2.bar(xs, ds, yerr=errs, capsize=5, color=cols)
    ax2.axhline(0, color="#999", lw=0.8)
    ax2.set_ylabel(f"Δ P(solve)  =  {b} − {a}")
    ax2.set_title("marginal value of escalation, by state")
    fig.suptitle(f"Same trajectory prefix, same snapshot — only the {a}-vs-{b} arm differs",
                 fontsize=10)
    fig.tight_layout()
    out = ROOT / "plots" / "marginal.png"
    fig.savefig(out, dpi=160)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
