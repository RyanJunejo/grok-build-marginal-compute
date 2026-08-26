"""Grade ungraded runs with the SWE-bench harness and merge verdicts back.

Selects runs.jsonl rows (status ok/error-with-patch, resolved is null), batches
them so each swebench invocation sees at most one patch per instance, runs
`swebench eval verified` with a FRESH run-id per batch (results cache on
(run_id, instance_id)), then writes `resolved` back into runs.jsonl atomically.

Empty-patch runs are marked resolved=False without grading.
"""

import argparse
import fcntl
import json
import os
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_JSONL = ROOT / "results" / "runs.jsonl"
GRADES_DIR = ROOT / "results" / "grades"
SWEBENCH = ROOT / ".venv" / "bin" / "swebench"


def read_runs():
    if not RUNS_JSONL.exists():
        return []
    return [json.loads(l) for l in RUNS_JSONL.read_text().splitlines() if l.strip()]


def write_runs(rows):
    tmp = RUNS_JSONL.with_suffix(".tmp")
    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        for r in rows:
            f.write(json.dumps(r) + "\n")
    os.replace(tmp, RUNS_JSONL)


def batch_unique_by_instance(rows):
    """Split rows into batches with unique instance_ids per batch."""
    batches = []
    for row in rows:
        for batch in batches:
            if row["task_id"] not in {r["task_id"] for r in batch}:
                batch.append(row)
                break
        else:
            batches.append([row])
    return batches


def grade_batch(batch, tag, timeout_s, workers):
    run_id = f"{tag}-{uuid.uuid4().hex[:6]}"
    model_name = "grokbuild"
    preds_path = GRADES_DIR / f"preds_{run_id}.jsonl"
    GRADES_DIR.mkdir(parents=True, exist_ok=True)
    with open(preds_path, "w") as f:
        for row in batch:
            patch = (ROOT / row["patch_path"]).read_text()
            f.write(json.dumps({
                "instance_id": row["task_id"],
                "model_name_or_path": model_name,
                "model_patch": patch,
            }) + "\n")

    cmd = [str(SWEBENCH), "eval", "verified", "-p", str(preds_path),
           "--run-id", run_id, "-j", str(workers), "-t", str(timeout_s)]
    print(f"  grading batch {run_id}: {len(batch)} instances")
    subprocess.run(cmd, cwd=GRADES_DIR, check=False,
                   capture_output=True, text=True, timeout=len(batch) * (timeout_s + 600))

    verdicts = {}
    for row in batch:
        report = (GRADES_DIR / "logs" / "run_evaluation" / run_id / model_name /
                  row["task_id"] / "report.json")
        if report.exists():
            data = json.loads(report.read_text())
            verdicts[row["run_id"]] = (bool(data.get(row["task_id"], {}).get("resolved")), run_id)
        else:
            verdicts[row["run_id"]] = (None, run_id)  # grading infra failure — leave ungraded
            print(f"  WARNING no report for {row['task_id']} in {run_id}")
    return verdicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="grade")
    ap.add_argument("--phase", default=None, help="filter: static|fork_source|fork_branch")
    ap.add_argument("--layer", default=None)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--regrade", action="store_true", help="include rows with resolved already set")
    args = ap.parse_args()

    rows = read_runs()
    selected, empty = [], []
    for r in rows:
        if args.phase and r["phase"] != args.phase:
            continue
        if args.layer and r.get("layer") != args.layer:
            continue
        if r.get("resolved") is not None and not args.regrade:
            continue
        if r["status"] != "ok":
            continue  # error rows are excluded from analysis, never graded
        (selected if r.get("patch_path") else empty).append(r)

    print(f"grading {len(selected)} runs with patches; {len(empty)} empty-patch runs -> resolved=False")

    verdicts = {}
    for batch in batch_unique_by_instance(selected):
        verdicts.update(grade_batch(batch, args.tag, args.timeout, args.workers))
    for r in empty:
        verdicts[r["run_id"]] = (False, "empty-patch")

    rows = read_runs()  # re-read: sweep may have appended while grading
    for r in rows:
        if r["run_id"] in verdicts:
            resolved, grade_run_id = verdicts[r["run_id"]]
            if resolved is not None:
                r["resolved"] = resolved
            r["grade_run_id"] = grade_run_id
    write_runs(rows)

    graded = [v for v, _ in verdicts.values() if v is not None]
    print(f"graded {len(graded)}: {sum(1 for v in graded if v)} resolved, "
          f"{sum(1 for v in graded if not v)} unresolved")


if __name__ == "__main__":
    main()
