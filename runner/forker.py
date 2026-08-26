"""Layer 3: paired counterfactual branch experiment.

Takes medium fork_source runs (segmented, docker-commit snapshots), selects
checkpoints under a PRE-REGISTERED rule, and for each checkpoint launches
paired continuations from the identical snapshot + identical session prefix —
one branch per effort in {medium, xhigh} — differing ONLY in --effort.

Selection rule (frozen before looking at outcomes):
  eligible = snapshot exists AND remaining turn budget > 0 AND the source did
             not already stop with end_turn at that boundary
  per task: up to --max-signal checkpoints with signal=True and up to
            --max-quiet with signal=False, chosen RANDOMLY (seeded) among
            eligible candidates.
Branch execution order alternates by checkpoint index (interleaving).
"""

import argparse
import fcntl
import json
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import features
from run_task import run_one

ROOT = Path(__file__).resolve().parent.parent
RUNS_JSONL = ROOT / "results" / "runs.jsonl"
FORKS_JSONL = ROOT / "results" / "forks.jsonl"
TOTAL_TURNS = 40


def read_jsonl(path):
    if not Path(path).exists():
        return []
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def append_jsonl(path, row):
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(row) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)


def select_checkpoints(source_rows, max_signal, max_quiet, seed, segment_turns,
                       branch_turns=None):
    """branch_turns: disclosed variant — both branches get this equal budget
    instead of the source's remaining budget; checkpoint ids get an @tN suffix
    so conditions never mix in analysis."""
    existing = {r["checkpoint_id"] for r in read_jsonl(FORKS_JSONL)}
    checkpoints = []
    for row in source_rows:
        states = features.state_at_segments(row)
        snaps = {s["segment"]: s["image"] for s in row.get("snapshots", [])}
        eligible = []
        for st in states:
            k = st["segment"]
            remaining = TOTAL_TURNS - segment_turns * k
            if k not in snaps or remaining <= 0:
                continue
            if st.get("stop_reason") == "end_turn":
                continue
            eligible.append((st, snaps[k], branch_turns or remaining))
        rng = random.Random(f"{seed}:{row['task_id']}")
        sig = [e for e in eligible if e[0]["signal"]]
        quiet = [e for e in eligible if not e[0]["signal"]]
        chosen = (rng.sample(sig, min(max_signal, len(sig))) +
                  rng.sample(quiet, min(max_quiet, len(quiet))))
        for st, snap, remaining in chosen:
            ck_id = f"{row['task_id']}__seg{st['segment']}__{row['run_id'].split('__')[-1]}"
            if branch_turns:
                ck_id += f"@t{branch_turns}"
            if ck_id in existing:
                continue
            checkpoints.append({
                "checkpoint_id": ck_id,
                "task_id": row["task_id"],
                "source_run_id": row["run_id"],
                "session_id": row["session_id"],
                "host_grok_dir": row["host_grok_dir"],
                "segment": st["segment"],
                "snapshot_image": snap,
                "remaining_turns": remaining,
                "selected_as": "signal" if st["signal"] else "quiet",
                "state": st,
                "branch_run_ids": {"medium": [], "xhigh": []},
            })
    return checkpoints


def run_branches(checkpoints, arms, reps, parallel):
    """arms: list of (label, model, effort) — the branch conditions. Only the
    listed dimension may differ between arms; everything else is shared."""
    for ck in checkpoints:
        ck["branch_run_ids"] = {label: [] for label, _, _ in arms}
    jobs = []
    for idx, ck in enumerate(checkpoints):
        order = arms if idx % 2 == 0 else list(reversed(arms))
        for rep in range(1, reps + 1):
            for arm in order:
                jobs.append((ck, arm, rep))

    def one(ck, arm, rep):
        label, model, effort = arm
        return ck, label, run_one(
            ck["task_id"], effort, rep, "fork_branch", model,
            ck["remaining_turns"], 8,
            snapshot=ck["snapshot_image"],
            resume_session={"session_id": ck["session_id"],
                            "host_grok_dir": ck["host_grok_dir"]},
            branch=label, checkpoint_id=ck["checkpoint_id"],
            tag_extra=f"__seg{ck['segment']}", layer="L3", snapshots=False,
        )

    done = 0
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futs = [pool.submit(one, *j) for j in jobs]
        for fut in as_completed(futs):
            ck, label, row = fut.result()
            ck["branch_run_ids"][label].append(row["run_id"])
            done += 1
            print(f"[{done}/{len(jobs)}] {ck['checkpoint_id']} {label}({row['model']}): "
                  f"{row['status']} stop={row['stop_reason']} turns={row['num_turns']} "
                  f"${row['cost_usd']:.2f} patch={'yes' if row['patch_path'] else 'EMPTY'}")

    for ck in checkpoints:
        append_jsonl(FORKS_JSONL, ck)


def parse_arms(spec):
    """'continue=grok-build-0.1,escalate=grok-4.6' -> [(label, model, effort)]"""
    arms = []
    for part in spec.split(","):
        label, model = part.split("=")
        arms.append((label.strip(), model.strip(), "medium"))
    return arms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="continue=grok-build-0.1,escalate=grok-4.6",
                    help="label=model pairs; the single manipulated variable")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--max-signal", type=int, default=2)
    ap.add_argument("--max-quiet", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--segment-turns", type=int, default=8)
    ap.add_argument("--source-run-ids", default=None, help="comma-list; default = all medium fork_source rows")
    ap.add_argument("--source-rep", type=int, default=None, help="filter sources by rep")
    ap.add_argument("--branch-turns", type=int, default=None,
                    help="equal branch budget override (disclosed variant; ids suffixed @tN)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = read_jsonl(RUNS_JSONL)
    sources = [r for r in rows
               if r["phase"] == "fork_source" and r["status"] == "ok"
               and r.get("snapshots") and r.get("session_id")]
    if args.source_run_ids:
        wanted = set(args.source_run_ids.split(","))
        sources = [r for r in sources if r["run_id"] in wanted]
    if args.source_rep is not None:
        sources = [r for r in sources if r["rep"] == args.source_rep]

    checkpoints = select_checkpoints(sources, args.max_signal, args.max_quiet,
                                     args.seed, args.segment_turns,
                                     branch_turns=args.branch_turns)
    n_sig = sum(1 for c in checkpoints if c["selected_as"] == "signal")
    print(f"{len(sources)} source runs -> {len(checkpoints)} new checkpoints "
          f"({n_sig} signal, {len(checkpoints) - n_sig} quiet)")
    for c in checkpoints:
        print(f"  {c['checkpoint_id']:60s} {c['selected_as']:6s} "
              f"remaining={c['remaining_turns']} streak={c['state']['failure_streak']}")

    if args.dry_run or not checkpoints:
        return
    run_branches(checkpoints, parse_arms(args.arms), args.reps, args.parallel)


if __name__ == "__main__":
    main()
