"""Layer 1 sweep: {medium, xhigh} x first-wave tasks, identical segmented mode.

Both arms run phase=fork_source (8-turn segments with resume between them) so the
ONLY difference between arms is --effort. Snapshots (docker commit per segment)
are taken on the medium arm only — they are passive and feed Layer 3.
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from run_task import load_manifest, run_one


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--efforts", default="medium,xhigh")
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--instances", default="first_wave", help="first_wave | scale_pool | all | comma-list")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--segment-turns", type=int, default=8)
    ap.add_argument("--snapshot-efforts", default="medium")
    args = ap.parse_args()

    m = load_manifest()
    if args.instances == "first_wave":
        instances = m["first_wave"]
    elif args.instances == "scale_pool":
        instances = m["scale_pool"]
    elif args.instances == "all":
        instances = m["first_wave"] + m["scale_pool"]
    else:
        instances = args.instances.split(",")

    efforts = args.efforts.split(",")
    snapshot_efforts = set(args.snapshot_efforts.split(","))

    jobs = [(iid, eff) for eff in efforts for iid in instances]
    print(f"sweep: {len(jobs)} runs ({len(instances)} tasks x {efforts}), rep {args.rep}, "
          f"parallel {args.parallel}")

    results = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futs = {
            pool.submit(
                run_one, iid, eff, args.rep, "fork_source", args.model,
                args.max_turns, args.segment_turns,
                layer="L1", snapshots=(eff in snapshot_efforts),
            ): (iid, eff)
            for iid, eff in jobs
        }
        for fut in as_completed(futs):
            iid, eff = futs[fut]
            try:
                row = fut.result()
                results.append(row)
                print(f"[done {len(results)}/{len(jobs)}] {iid} {eff}: "
                      f"{row['status']} stop={row['stop_reason']} turns={row['num_turns']} "
                      f"${row['cost_usd']:.2f} {row['wall_s']}s "
                      f"patch={'yes' if row['patch_path'] else 'EMPTY'}")
            except Exception as e:  # noqa: BLE001
                print(f"[FAILED] {iid} {eff}: {e!r}")

    ok = [r for r in results if r["status"] == "ok"]
    print(f"\n{len(ok)}/{len(jobs)} ok; total cost ${sum(r['cost_usd'] for r in results):.2f}")
    print(json.dumps({f"{r['task_id']}|{r['effort']}": r["status"] for r in results}, indent=2))


if __name__ == "__main__":
    main()
