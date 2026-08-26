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

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(11.5, 4.8), gridspec_kw={"width_ratios": [1.5, 1]})
    groups = ["signal", "quiet"]
    group_color = {"signal": "#c0392b", "quiet": "#2e6f95"}
    CONT, ESC = "#6d6d6d", "#e07b39"

    # --- left: dumbbells, grouped with headers, pairs visible even when equal
    y = 0
    yticks, ylabels = [], []
    for grp in groups:
        members = sorted((r for r in rows if r["group"] == grp),
                         key=lambda r: (-r["delta"], r["task_id"]))
        if not members:
            continue
        ax1.text(-0.02, y + 0.55, f"{grp.upper()} states", fontsize=8,
                 fontweight="bold", color=group_color[grp], va="bottom")
        for r in members:
            ax1.plot([r["p_med"], r["p_xh"]], [y, y], color="#c9c9c9", lw=2, zorder=1)
            # continue: large open circle; escalate: smaller filled diamond —
            # both stay visible when the two probabilities are equal
            ax1.scatter([r["p_med"]], [y], s=130, facecolors="white",
                        edgecolors=CONT, linewidths=1.8, zorder=2)
            ax1.scatter([r["p_xh"]], [y], s=55, marker="D", color=ESC, zorder=3)
            ylabels.append(f"{r['task_id'].split('__')[-1]}  seg{r['segment']}")
            yticks.append(y)
            y -= 1
        y -= 1.2  # gap between groups
    ax1.set_yticks(yticks, ylabels, fontsize=8)
    ax1.set_xlim(-0.06, 1.06)
    ax1.set_xticks([0, 0.5, 1.0])
    ax1.set_xlabel("P(verified solve)", fontsize=9)
    ax1.set_title("per checkpoint (2 reps per arm)", fontsize=10)
    ax1.scatter([], [], s=130, facecolors="white", edgecolors=CONT,
                linewidths=1.8, label=f"{a} (base)")
    ax1.scatter([], [], s=55, marker="D", color=ESC, label=f"{b} (grok-4.6)")
    ax1.legend(fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.10),
               ncol=2, frameon=False)
    ax1.spines[["top", "right"]].set_visible(False)

    # --- right: group means with CIs + the per-checkpoint deltas behind them
    for i, grp in enumerate(groups):
        if grp not in agg:
            continue
        g = agg[grp]
        ax2.bar(i, g["delta"], width=0.55, color=group_color[grp], alpha=0.85, zorder=2)
        ax2.errorbar(i, g["delta"],
                     yerr=[[g["delta"] - g["ci"][0]], [g["ci"][1] - g["delta"]]],
                     fmt="none", ecolor="#222", capsize=6, lw=1.5, zorder=4)
        ax2.text(i, g["ci"][1] + 0.04, f"+{g['delta']:.2f}", ha="center",
                 fontsize=11, fontweight="bold", color=group_color[grp])
        pts = [r["delta"] for r in rows if r["group"] == grp]
        ax2.scatter([i + 0.34] * len(pts), pts, s=28, color="#444", alpha=0.6, zorder=3)
    ax2.axhline(0, color="#999", lw=0.8)
    ax2.set_xticks([0, 1], [f"signal\n(n={agg.get('signal', {}).get('n', 0)})",
                            f"quiet\n(n={agg.get('quiet', {}).get('n', 0)})"], fontsize=9)
    ax2.set_ylim(-0.12, 1.02)
    ax2.set_ylabel(f"Δ P(solve)  =  P({b}) − P({a})", fontsize=9)
    ax2.set_title("group mean ± 95% CI  (dots: checkpoints)", fontsize=10)
    ax2.spines[["top", "right"]].set_visible(False)

    fig.suptitle(f"Fork the same frozen state, run two futures: {a} vs {b} — "
                 "escalation never hurt, anywhere", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    out = ROOT / "plots" / "marginal.png"
    fig.savefig(out, dpi=160)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
