"""Re-run one lost fork branch and append its run id to the checkpoint row."""

import argparse
import json
from pathlib import Path

from forker import FORKS_JSONL, read_jsonl
from run_task import run_one

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--effort", required=True)
    ap.add_argument("--rep", type=int, required=True)
    ap.add_argument("--model", required=True)
    args = ap.parse_args()

    forks = read_jsonl(FORKS_JSONL)
    ck = next(c for c in forks if c["checkpoint_id"] == args.checkpoint)
    row = run_one(
        ck["task_id"], args.effort, args.rep, "fork_branch", args.model,
        ck["remaining_turns"], 8,
        snapshot=ck["snapshot_image"],
        resume_session={"session_id": ck["session_id"],
                        "host_grok_dir": ck["host_grok_dir"]},
        branch=args.effort, checkpoint_id=ck["checkpoint_id"],
        tag_extra=f"__seg{ck['segment']}", layer="L3", snapshots=False,
    )
    print(json.dumps({k: row[k] for k in ("run_id", "status", "stop_reason",
                                          "num_turns", "cost_usd", "patch_path")}, indent=2))
    if row["status"] == "ok":
        ck["branch_run_ids"][args.effort].append(row["run_id"])
        tmp = FORKS_JSONL.with_suffix(".tmp")
        with open(tmp, "w") as f:
            for c in forks:
                f.write(json.dumps(c) + "\n")
        tmp.replace(FORKS_JSONL)
        print("forks.jsonl updated")


if __name__ == "__main__":
    main()
