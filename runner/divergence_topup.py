"""Adaptive reps: add branch repetitions where the arms disagree.

Sequential-design step (disclosed in README): after the main fork batches, any
checkpoint whose medium and xhigh solve fractions differ gets topped up to
--target reps per arm, so apparent divergences rest on more than 2 samples.
Selection is mechanical (fractions differ AND both arms have >=1 graded rep).
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from forker import FORKS_JSONL, read_jsonl
from run_task import run_one

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--target", type=int, default=4, help="reps per arm on divergent checkpoints")
    args = ap.parse_args()

    runs = [json.loads(l) for l in open(ROOT / "results" / "runs.jsonl") if l.strip()]
    per = defaultdict(lambda: defaultdict(list))
    for r in runs:
        if r["phase"] == "fork_branch" and r["status"] == "ok" and r.get("resolved") is not None:
            per[r["checkpoint_id"]][r["branch"]].append(r["resolved"])

    forks = read_jsonl(FORKS_JSONL)
    by_id = {c["checkpoint_id"]: c for c in forks}
    jobs = []
    for ck_id, d in per.items():
        med, xh = d.get("medium", []), d.get("xhigh", [])
        if not med or not xh or ck_id not in by_id:
            continue
        if sum(med) / len(med) == sum(xh) / len(xh):
            continue
        for eff, have in (("medium", len(med)), ("xhigh", len(xh))):
            for rep in range(have + 1, args.target + 1):
                jobs.append((ck_id, eff, rep))

    print(f"divergent-checkpoint top-ups: {len(jobs)} branch runs")
    for ck_id, eff, rep in jobs:
        ck = by_id[ck_id]
        row = run_one(
            ck["task_id"], eff, rep, "fork_branch", args.model,
            ck["remaining_turns"], 8,
            snapshot=ck["snapshot_image"],
            resume_session={"session_id": ck["session_id"],
                            "host_grok_dir": ck["host_grok_dir"]},
            branch=eff, checkpoint_id=ck_id,
            tag_extra=f"__seg{ck['segment']}", layer="L3", snapshots=False,
        )
        print(f"  {ck_id} {eff} r{rep}: {row['status']} resolved-pending")
        if row["status"] == "ok":
            ck["branch_run_ids"][eff].append(row["run_id"])

    tmp = FORKS_JSONL.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for c in forks:
            f.write(json.dumps(c) + "\n")
    tmp.replace(FORKS_JSONL)


if __name__ == "__main__":
    main()
