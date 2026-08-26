# Marginal Value of Reasoning Effort in a Production Agent Harness

Modern agent harnesses expose increasingly large amounts of inference-time compute, but current
allocation is primarily model-controlled or statically configured. This project measures the
marginal value of reasoning effort inside [Grok Build](https://github.com/xai-org/grok-build)
(xAI's open-source coding-agent harness) and tests whether spending more inference compute at a
specific point in an agent trajectory actually changes verified outcomes.

**The question**: at a particular state in an agent trajectory, when is buying more reasoning
actually worth it?

**The method**: a **paired counterfactual branch experiment**. Run a coding agent on SWE-bench
Verified tasks at medium effort in 8-turn segments, snapshotting the full container state
(`docker commit`) at every segment boundary. At selected checkpoints, fork the *session* (Grok
Build's native `--fork-session`) and relaunch from the *identical* snapshot twice — one branch
continues at `--effort medium`, one escalates to `--effort xhigh`. Everything else is held fixed:
same model, same prompt, same transcript prefix, same filesystem, same remaining turn budget,
subagents disabled everywhere. Grade both branches with the SWE-bench harness (hidden tests are
injected only at grade time). The estimand:

> Δ(state) = P(verified solve | state, xhigh) − P(verified solve | state, medium)

split by trajectory state: **signal** states (the most recent verification execution — a test run
or repro script — failed, or the agent is repeat-editing one file without passing verification)
vs **quiet** states. Checkpoints are selected by a pre-registered mechanical rule with seeded
random choice among eligible candidates; branch stochasticity is handled with repetitions, not
hidden. This is a small-n paired design and is reported as such — not a causal-inference claim,
not a benchmark claim.

Motivated by [Prime Agent](https://arxiv.org/abs/2608.23552) (Karten et al., 2026), which frames
long-horizon harnesses as computation managers and reports that models still struggle to allocate
the test-time compute harnesses expose.

## Pinned configuration

| Component | Version |
|---|---|
| Grok Build | 1.0.5 (`bin/SHA256SUMS`) |
| Model | `grok-build-0.1`, held constant in every run |
| Efforts | `medium` vs `xhigh` (both verified accepted; stderr checked for silent degradation) |
| Tasks | 20-instance SWE-bench Verified subset, difficulty-stratified (`tasks/manifest.json`, frozen before first run) |
| Grading | `swebench==5.0.2`, fresh run-id per batch |
| Host | Apple Silicon; amd64 images under Rosetta (wall-clock is emulated — tokens/$ unaffected) |

## Layers

1. **L1 static floor** — medium vs xhigh, identical segmented execution, solve rate vs tokens/$ (`plots/static.py`)
2. **L2 allocation gap** — per-task cheapest-successful-effort selection (motivation only; oracle selection bias stated)
3. **L3 centerpiece** — the paired branch experiment (`runner/forker.py`, `plots/marginal.py`)
4. **L4 stretch** — a threshold escalation rule read off the L3 plot, run as a third arm

## Reproduce

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python swebench==5.0.2 datasets matplotlib
echo 'XAI_API_KEY=...' > .env
.venv/bin/python runner/select_subset.py                      # manifest + prompts
.venv/bin/python runner/sweep.py --model grok-build-0.1       # L1 (both arms segmented; medium arm snapshots)
.venv/bin/python runner/grade.py --layer L1                   # SWE-bench verdicts -> runs.jsonl
.venv/bin/python plots/static.py                              # L1 table/plot + L2 gap
.venv/bin/python runner/forker.py --model grok-build-0.1      # L3 checkpoint selection + paired branches
.venv/bin/python runner/grade.py --phase fork_branch          # grade branches
.venv/bin/python plots/marginal.py                            # Δsolve by state
```

Raw records: `results/runs.jsonl` (every headless invocation: usage, cost, stop reason, patch,
verdict), `results/forks.jsonl` (checkpoints: state features, snapshot image, branch run ids).
Session event streams (`updates.jsonl`) are preserved per run under `results/sessions/`.

## Validity notes

- Only `--effort` differs between arms; both L1 arms run the same 8-turn segmented mode so
  segmentation is not a confound. Runner asserts subagent count is zero.
- Effort levels the model doesn't support are soft-ignored by the CLI with a stderr warning;
  the runner greps for it and marks such runs as errors rather than letting an arm silently degrade.
- `docker commit` snapshots capture the repo *and* the python environment, so branch runs cannot
  leak state into each other, and branches run in parallel from the same frozen image.
- API/transport failures are excluded (`status=error`), never scored as unsolved.
- Agents cannot see or modify the hidden FAIL_TO_PASS tests (injected at grade time in fresh
  containers; the eval script re-checkouts touched test files first). The runner additionally
  flags any test-file paths in submitted diffs.
- Observed 1.0.5 behavior recorded during smoke testing: a `--max-turns` stop reports
  `stopReason: "cancelled"`; forked children record `parent_session_id` in `summary.json`.
