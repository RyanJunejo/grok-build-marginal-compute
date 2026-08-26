"""Result 1 figure: the --effort dial does not move realized reasoning.

Left: probe measurements (same prompt, requested effort swept; values recorded
in docs/dial-finding.md, 2026-08-26). Right: per-task reasoning on the 8-task
L1 suite under the two requested efforts — task identity moves reasoning ~12x,
the dial ~1x. Output: plots/dial.png
"""

import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent

# Probe data from docs/dial-finding.md (same combinatorics prompt per call)
PROBES = {
    "grok-build-0.1": {"none": [466, 291], "medium": [446, 685], "xhigh": [441, 797]},
    "grok-4.20-0309-reasoning": {"low": [468], "xhigh": [250]},
    "grok-4.6": {"none": [504], "xhigh": [460]},
}
LEVELS = ["none", "low", "medium", "xhigh"]


def main():
    rows = [json.loads(l) for l in open(ROOT / "results" / "runs.jsonl") if l.strip()]
    per_task = defaultdict(dict)
    for r in rows:
        if (r.get("layer") == "L1" and r["phase"] == "fork_source" and r["status"] == "ok"
                and r.get("model") == "grok-build-0.1" and r["rep"] == 1
                and r["effort"] in ("medium", "xhigh")):
            per_task[r["task_id"]][r["effort"]] = r["usage"].get("reasoning_tokens") or 0

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

    colors = {"grok-build-0.1": "#c25e4c", "grok-4.20-0309-reasoning": "#8e7cc3",
              "grok-4.6": "#7aa6c2"}
    jitter = {"grok-build-0.1": -0.13, "grok-4.20-0309-reasoning": 0.0, "grok-4.6": 0.13}
    for model, by_level in PROBES.items():
        xs, ys = [], []
        for i, lvl in enumerate(LEVELS):
            for v in by_level.get(lvl, []):
                xs.append(i + jitter[model])
                ys.append(v)
        ax1.plot(xs, ys, "o", color=colors[model], label=model, alpha=0.85, markersize=7)
    ax1.set_xticks(range(len(LEVELS)), LEVELS)
    ax1.set_xlabel("requested --effort")
    ax1.set_ylabel("realized reasoning tokens")
    ax1.set_title("probe: same prompt, effort swept — flat", fontsize=10)
    ax1.set_ylim(0, 900)
    ax1.legend(fontsize=7)

    tasks = sorted(t for t, d in per_task.items() if "medium" in d and "xhigh" in d)
    for i, t in enumerate(tasks):
        m, x = per_task[t]["medium"], per_task[t]["xhigh"]
        ax2.plot([i, i], [m, x], color="#bbb", lw=1, zorder=1)
        ax2.scatter([i], [m], color="#666", zorder=2, label="medium" if i == 0 else None)
        ax2.scatter([i], [x], color="#c25e4c", zorder=2, label="xhigh" if i == 0 else None)
    ax2.set_yscale("log")
    ax2.set_xticks(range(len(tasks)), [t.split("__")[-1] for t in tasks],
                   rotation=45, ha="right", fontsize=7)
    ax2.set_ylabel("reasoning tokens (log)")
    ax2.set_title("L1 tasks: task moves reasoning ~12x, the dial ~1x", fontsize=10)
    ax2.legend(fontsize=7)

    fig.suptitle("--effort is inert on xAI-served models (Grok Build 1.0.5)", fontsize=11)
    fig.tight_layout()
    out = ROOT / "plots" / "dial.png"
    fig.savefig(out, dpi=160)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
