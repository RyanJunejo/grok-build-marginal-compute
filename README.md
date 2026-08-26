# Marginal Compute in a Production Agent Harness: Forked-Trajectory Measurements in Grok Build

Modern agent harnesses expose increasingly large amounts of inference-time compute, but current
allocation is primarily model-controlled or statically configured. This project measures, inside
[Grok Build](https://github.com/xai-org/grok-build) (xAI's open-source coding-agent harness),
what buying more compute at a *specific point in an agent trajectory* actually purchases — using
a **paired counterfactual branch** design made possible by the harness's native session forking.

It produced two results:

## Result 1 — The harness's compute dial is silently disconnected

The A/A control run for the original design (reasoning effort medium vs xhigh) caught that
Grok Build 1.0.5's documented `--effort` flag is inert for **every** xAI-served model: the
server-delivered catalog marks all models `supports_reasoning_effort: false`, the CLI drops the
value with a tracing-only warning invisible in headless mode, `--effort banana` is accepted, and
realized reasoning tokens do not respond to the flag (`none` ≈ `xhigh`). Full receipts — catalog
dump, source-level root cause in `xai-grok-shell`, probe tables, and the resulting 8-task /
17-checkpoint A/A dataset (identical 75%/75% resolution matrices; median reasoning ratio 0.96) —
in **[docs/dial-finding.md](docs/dial-finding.md)**. The A/A forks double as the experiment's
measured noise floor.

This is Prime Agent's thesis (arXiv 2608.23552) one level down: the capability surface
advertises a knob; the wire discards it.

## Result 2 — The marginal value of model escalation, by trajectory state

With effort dead, the real escalation lever is the model. Design:

1. Run each SWE-bench Verified task on `grok-build-0.1` (single agent, no subagents, 40 turns)
   in 8-turn segments, `docker commit`-ing the full container (repo + env) at every boundary.
2. Select checkpoints by a pre-registered mechanical rule over the session event stream —
   **signal** states (latest verification execution failed, or repeat-edits without a passing
   verification) vs **quiet** states; seeded random choice among eligible candidates.
3. At each checkpoint, fork the session (`-r <sid> --fork-session`) from the identical snapshot
   into paired branches: **continue** on `grok-build-0.1` vs **escalate** to `grok-4.6` — same
   transcript, same filesystem, same remaining budget, same prompt; 2 repetitions per arm,
   execution order interleaved.
4. Grade every branch with the SWE-bench harness (hidden tests injected at grade time in fresh
   containers). Estimand: **Δ(state) = P(solve | state, escalate) − P(solve | state, continue)**,
   with bootstrap CIs over checkpoints, split by state group, alongside the token/cost premium.

**Results:** [pending — populated from `plots/marginal.py` when the branch batch completes]
— see `plots/marginal.png`, `plots/trajectory_*.png`, and `plots/static.png` (task-level
baselines: all-continue vs all-escalate vs per-task oracle).

## Pinned configuration

| Component | Value |
|---|---|
| Grok Build | 1.0.5 (`bin/SHA256SUMS`) |
| Models | `grok-build-0.1` (base) vs `grok-4.6` (escalation target); effort constant (and inert, see Result 1) |
| Tasks | SWE-bench Verified subset, difficulty-stratified, frozen pre-run (`tasks/manifest.json`) |
| Grading | `swebench==5.0.2`, fresh run-id per batch |
| Host | Apple Silicon; amd64 eval images under Rosetta (wall time emulated; tokens/$ unaffected) |

## Reproduce

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python swebench==5.0.2 datasets matplotlib
echo 'XAI_API_KEY=...' > .env
.venv/bin/python runner/select_subset.py                                  # manifest + prompts
.venv/bin/python runner/sweep.py --model grok-build-0.1 --efforts medium  # segmented sources + snapshots
.venv/bin/python runner/grade.py --layer L1
.venv/bin/python runner/forker.py --arms "continue=grok-build-0.1,escalate=grok-4.6" --reps 2
.venv/bin/python runner/grade.py --phase fork_branch
.venv/bin/python plots/static.py && .venv/bin/python plots/marginal.py
```

Raw records ship in-repo: `results/runs.jsonl` (every headless invocation: usage, cost, stop
reason, patch, verdict), `results/forks.jsonl` (checkpoints: state features, snapshot image,
branch run ids). Session event streams under `results/sessions/` locally.

## Validity notes

- **One manipulated variable.** Branch arms differ only in model id; prompts, budgets, snapshots,
  transcripts identical; subagents disabled everywhere; runner asserts it.
- **Paired design, honest language.** This is a paired counterfactual branch experiment with
  repetitions — not a causal-inference claim, not a benchmark claim. n is reported everywhere;
  the A/A dataset provides the empirical noise floor.
- **Grader integrity.** Hidden FAIL_TO_PASS tests exist only at grade time; the eval script
  re-checkouts touched test files; the runner flags any test-path edits in diffs.
- **Failure hygiene.** API/transport failures are `status=error` — excluded, never scored.
  Completions are validated (sessionId present); results cache dodged via fresh grade run-ids.
- **Observed-vs-documented CLI behavior** is recorded where it diverges (max-turns stop reports
  `cancelled`; the effort warning never reaches headless stderr).

## Future work

The fork dataset is the natural training set for a learned value-of-computation policy
(P(solve|state, action) per unit cost); a threshold escalation rule falls out of the marginal
plot first. Verifying wire-level behavior of reasoning parameters against the xAI API directly
(outside the CLI) is the open item from Result 1.
