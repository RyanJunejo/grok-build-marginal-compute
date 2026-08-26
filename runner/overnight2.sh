#!/bin/zsh
# v2 clean rerun. Phase 2 (expensive branches) is gated on the audit:
# it waits until .audit_hold is removed.
cd /Users/ryanjunejo/fastmodel
V=.venv/bin/python
log() { echo "[$(date '+%H:%M:%S')] $*"; }

log "phase 1: rebuild 8 medium sources with v2 marker checkpoints (rep 3)"
$V runner/sweep.py --model grok-build-0.1 --efforts medium --rep 3 --parallel 3 \
  --instances first_wave --snapshot-efforts medium || true
$V runner/grade.py --tag v2src --layer L1 || true

log "phase 2 gate: waiting for audit clearance (.audit_hold removal)"
while [ -f .audit_hold ]; do sleep 60; done

log "phase 2: cross-model paired branches (continue vs escalate), reps=2"
(cd runner && ../$V forker.py --source-rep 3 --reps 2 --parallel 2 \
  --arms "continue=grok-build-0.1,escalate=grok-4.6") || true

log "phase 3: grade branches + refresh analyses"
$V runner/grade.py --tag v2forks --phase fork_branch || true
$V plots/static_models.py > results/static_models_v2.txt 2>&1 || true
$V plots/marginal.py > results/marginal_v2.txt 2>&1 || true
log "v2 pipeline complete"
