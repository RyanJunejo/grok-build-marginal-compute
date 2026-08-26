#!/bin/bash
# Runs ON the VM under nohup. Full v2 experiment: sources -> probe -> branches -> grade -> plots.
set -u
cd ~/fastmodel
V=.venv/bin/python
log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "phase 1: 8 sources (rep 3) with v2 marker checkpoints, native amd64"
$V runner/sweep.py --model grok-build-0.1 --efforts medium --rep 3 --parallel 4 \
  --instances first_wave --snapshot-efforts medium || true
$V runner/grade.py --tag v2src --layer L1 || true

log "phase 2a: single-source probe before committing the full batch"
PROBE=$($V - <<'PY'
import json
rows = [json.loads(l) for l in open("results/runs.jsonl") if l.strip()]
srcs = [r for r in rows if r["phase"] == "fork_source" and r["rep"] == 3
        and r["status"] == "ok" and any(s.get("ckpt_session") for s in r.get("snapshots", []))]
print(srcs[0]["run_id"] if srcs else "")
PY
)
if [ -z "$PROBE" ]; then log "FATAL: no rep-3 sources with checkpoints"; exit 1; fi
(cd runner && ../$V forker.py --source-run-ids "$PROBE" --reps 1 --parallel 2 \
  --arms "continue=grok-build-0.1,escalate=grok-4.6") || true
OK=$($V - <<'PY'
import json
rows = [json.loads(l) for l in open("results/runs.jsonl") if l.strip()]
probe = [r for r in rows if r["phase"] == "fork_branch" and r["rep"] == 1
         and r.get("branch") in ("continue", "escalate") and r.get("design") != "v1_leaky"]
recent = probe[-2:]
print("OK" if recent and all(r["status"] == "ok" and r.get("session_id") for r in recent) else "BAD")
PY
)
if [ "$OK" != "OK" ]; then log "FATAL: probe branches unhealthy; aborting before batch"; exit 1; fi
log "probe healthy"

log "phase 2b: full paired branch batch (reps=2)"
(cd runner && ../$V forker.py --source-rep 3 --reps 2 --parallel 4 \
  --arms "continue=grok-build-0.1,escalate=grok-4.6") || true

log "phase 3: grade branches + refresh analyses"
$V runner/grade.py --tag v2forks --phase fork_branch || true
$V plots/static_models.py > results/static_models_v2.txt 2>&1 || true
$V plots/marginal.py > results/marginal_v2.txt 2>&1 || true
log "PIPELINE COMPLETE"
