#!/bin/zsh
# Overnight pipeline (2026-08-25). Self-sequences after the live fork batch
# drains. Logs to results/overnight.log. Each phase tolerates failures.
cd /Users/ryanjunejo/fastmodel
V=.venv/bin/python
log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "waiting for live agent containers to drain..."
while docker ps --format '{{.Names}}' | grep -q '^ag_'; do sleep 60; done
log "drained. starting overnight pipeline"

# 0. catch-up grading of anything the interactive session left ungraded
$V runner/grade.py --tag on-catchup --phase fork_branch || true

# 1. top up first task's checkpoints to 2 reps (uniform reps across dataset)
for ck in django__django-11999__seg3__9e178a82 django__django-11999__seg4__9e178a82 django__django-11999__seg1__9e178a82; do
  for eff in medium xhigh; do
    (cd runner && ../$V rerun_branch.py --checkpoint $ck --effort $eff --rep 2 --model grok-build-0.1) || true
  done
done
$V runner/grade.py --tag on-topup --phase fork_branch || true

# 2. free snapshot disk before new pulls; KEEP snap_9e178a82 (live-demo forks)
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^snap_' | grep -v snap_9e178a82 | xargs -n1 docker rmi 2>/dev/null || true

# 3. L1 rep 2, first wave (16 runs; medium arm snapshots -> new fork sources)
$V runner/sweep.py --model grok-build-0.1 --rep 2 --parallel 3 --instances first_wave || true

# 4. scale wave: the 4 medium + 2 hard scale-pool tasks (easies excluded --
# both arms saturate on easy; chosen by difficulty label + disk budget before
# any scale-pool run)
SCALE="matplotlib__matplotlib-23299,scikit-learn__scikit-learn-14894,astropy__astropy-14365,mwaskom__seaborn-3069,django__django-15128,sympy__sympy-18199"
for img in $($V -c "
import json
m = json.load(open('tasks/manifest.json'))
for iid in '$SCALE'.split(','): print(m['instances'][iid]['image'])"); do
  docker pull --platform linux/amd64 "$img" >/dev/null 2>&1 || true &
done
wait
$V runner/sweep.py --model grok-build-0.1 --rep 1 --parallel 3 --instances "$SCALE" || true

# 5. grade all L1
$V runner/grade.py --tag on-l1 --layer L1 || true

# 6. fork every new medium source (dedupe skips existing checkpoint ids)
(cd runner && ../$V forker.py --model grok-build-0.1 --reps 2 --parallel 3) || true

# 7. grade branches
$V runner/grade.py --tag on-forks --phase fork_branch || true

# 7b. adaptive reps on divergent checkpoints (sequential design, disclosed)
(cd runner && ../$V divergence_topup.py --model grok-build-0.1 --target 4) || true
$V runner/grade.py --tag on-div --phase fork_branch || true

# 8. refresh analyses
$V plots/static.py > results/static_overnight.txt 2>&1 || true
$V plots/marginal.py > results/marginal_overnight.txt 2>&1 || true
log "overnight pipeline complete"
