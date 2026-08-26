"""Deliverable #1: one forked trajectory, rendered.

Draws the source run (cumulative output+reasoning tokens vs turns, with
verification pass/fail marks per segment) and, from a chosen checkpoint, the
paired continuation branches (medium vs xhigh) to their graded verdicts.

Usage: trajectory.py <checkpoint_id>   (default: first checkpoint whose
branches disagree on resolved; else the first checkpoint)

Intra-segment event placement is proportional by event order within the
segment — segment boundaries and totals are exact, intra-segment x is
approximate (stated on the figure).
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runner"))
import features  # noqa: E402


def read_jsonl(p):
    p = Path(p)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def otok(r):
    u = r.get("usage", {})
    return (u.get("output_tokens") or 0) + (u.get("reasoning_tokens") or 0)


def pick_checkpoint(forks, runs):
    """Prefer the checkpoint with the largest |Δ solve-rate| between its arms."""
    best, best_gap = None, -1.0
    for ck in forks:
        rates = []
        for eff, ids in ck["branch_run_ids"].items():
            vals = [runs[i].get("resolved") for i in ids
                    if i in runs and runs[i]["status"] == "ok"]
            vals = [v for v in vals if v is not None]
            if vals:
                rates.append(sum(vals) / len(vals))
        if len(rates) >= 2:
            gap = max(rates) - min(rates)
            if gap > best_gap:
                best, best_gap = ck, gap
    return best, best_gap > 0


def main():
    forks = read_jsonl(ROOT / "results" / "forks.jsonl")
    runs = {r["run_id"]: r for r in read_jsonl(ROOT / "results" / "runs.jsonl")}
    if not forks:
        sys.exit("no forks yet")

    if len(sys.argv) > 1:
        ck = next(c for c in forks if c["checkpoint_id"] == sys.argv[1])
        divergent = None
    else:
        ck, divergent = pick_checkpoint(forks, runs)

    src = runs[ck["source_run_id"]]
    segs = src["segments"]
    fork_seg = ck["segment"]

    # source cumulative token curve at segment boundaries
    xs = [0] + [s["segment"] * 8 for s in segs]
    ys = [0] + [s.get("output_tokens_cum") or 0 for s in segs]

    fig, ax = plt.subplots(figsize=(9.5, 5))
    ax.plot(xs, ys, color="#555", lw=2, label=f"source ({src['model']})", zorder=2)

    # verification marks, placed within their segment proportionally
    upath = features.localize(src["updates_path"])
    seg_bounds = [(s["segment"], s.get("updates_bytes")) for s in segs]
    prev_n = 0
    for seg_i, upto in seg_bounds:
        seg_evs = features.extract_events(upath, upto)[prev_n:]
        for j, ev in enumerate(seg_evs):
            if features._is_verification(ev):
                frac = (j + 1) / (len(seg_evs) + 1)
                x = (seg_i - 1) * 8 + frac * 8
                y = ys[seg_i - 1] + frac * (ys[seg_i] - ys[seg_i - 1])
                if ev["saw_fail"]:
                    ax.scatter([x], [y], marker="x", s=70, color="#c0392b", zorder=3)
                else:
                    ax.scatter([x], [y], marker="o", s=45, color="#27ae60", zorder=3)
        prev_n += len(seg_evs)

    fork_x = fork_seg * 8
    fork_y = ys[fork_seg]
    ax.axvline(fork_x, color="#888", ls=":", lw=1)
    trigger = ("failed verification" if ck["state"].get("last_test_failed")
               else "repeat-edits without passing verification"
               if ck["selected_as"] == "signal" else "no failure signal")
    ax.annotate(f"fork @ turn {fork_x}\n{ck['selected_as']} state: {trigger}",
                (fork_x, fork_y), textcoords="offset points", xytext=(-8, 40),
                fontsize=8, ha="right")

    palette = ["#7aa6c2", "#c25e4c", "#8e7cc3", "#6aa84f"]
    arm_labels = list(ck["branch_run_ids"].keys())
    colors = {lab: palette[i % len(palette)] for i, lab in enumerate(arm_labels)}
    for eff in arm_labels:
        for rid in ck["branch_run_ids"].get(eff, []):
            br = runs.get(rid)
            if not br:
                continue
            bx = [fork_x, fork_x + br["num_turns"]]
            by = [fork_y, fork_y + otok(br)]
            ax.plot(bx, by, color=colors[eff], lw=2, zorder=2)
            solved = br.get("resolved")
            mark, mcol = ("*", "#27ae60") if solved else ("s", "#c0392b")
            ax.scatter([bx[1]], [by[1]], marker=mark, s=170 if solved else 70,
                       color=mcol, zorder=4, edgecolors="black", linewidths=0.5)
            ax.annotate(
                f"{eff}: {'SOLVED' if solved else 'failed'}\n"
                f"+{otok(br):,} tok, ${br['cost_usd']:.2f}",
                (bx[1], by[1]), textcoords="offset points", xytext=(8, -6),
                fontsize=8, color=colors[eff], fontweight="bold")

    for s in segs[:-1]:
        ax.axvline(s["segment"] * 8, color="#eee", lw=0.7, zorder=0)

    ax.scatter([], [], marker="x", color="#c0392b", label="verification failed")
    ax.scatter([], [], marker="o", color="#27ae60", label="verification passed")
    ax.set_xlabel("agent turns")
    ax.set_ylabel("cumulative output+reasoning tokens")
    ax.set_title(
        f"{ck['task_id']} — same state, continue vs escalate "
        f"(paired counterfactual branches; intra-segment marks approximate)",
        fontsize=10)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    out = ROOT / "plots" / f"trajectory_{ck['checkpoint_id']}.png"
    fig.savefig(out, dpi=160)
    print(f"checkpoint: {ck['checkpoint_id']} (divergent={divergent})")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
