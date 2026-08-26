# When should a coding agent escalate to a bigger model?

**Forked-trajectory measurements of marginal compute in [Grok Build](https://github.com/xai-org/grok-build), on SWE-bench Verified.**

Agent harnesses expose lots of test-time compute, but *when* to spend it is mostly guesswork.
This repo measures one allocation decision directly: fork a live coding-agent trajectory at a
matched checkpoint — identical filesystem, identical conversation, identical remaining budget —
and let one branch **continue** on `grok-build-0.1` while the other **escalates** to `grok-4.6`.
Grade both against SWE-bench's hidden tests. The difference is what escalation bought, *at that
state*.

- **Escalation raised P(solve) by ≈ +0.4 — and never lowered it at any checkpoint.**
  Δsolve = +0.38 [0.00, +0.75] at failure-signal states, +0.39 [+0.11, +0.67] at quiet states
  (13 checkpoints × 2 reps/arm, 50 graded branches).
- **It rescues trajectories the base model can never finish.** django-11400 is 0/6 for
  grok-build-0.1 from scratch; escalate branches solved it 2/2 even from a deep failure state.
- **At quiet states, escalating was net *cheaper*:** mean token premium **−86k** — grok-4.6
  finishes and stops while the base model burns its whole budget flailing.
- **Rescuing a failing trajectory has a price: ≈ 347k total tokens per marginal solve** — and a
  too-late boundary (with 8 turns of budget left, even grok-4.6 couldn't save it).
- **Bonus finding:** the harness's own compute dial (`--effort`) is **silently inert for every
  xAI-served model** — caught by an A/A control, receipts down to the source line in
  **[docs/dial-finding.md](docs/dial-finding.md)**.

![Marginal value of escalation by state](plots/marginal.png)
*Paired branches from identical frozen states: escalating to grok-4.6 raised P(solve) at both
signal and quiet checkpoints and never lowered it. Bootstrap CIs over checkpoints.*

## How the measurement works

Every trajectory becomes its own control: run the base model in 8-turn segments, freeze the
complete state at each boundary — the **filesystem** via `docker commit` and the **conversation**
via a checkpoint session forked at that moment — then branch the same frozen state down two
futures and grade both.

1. **Sources.** Each SWE-bench Verified task runs on `grok-build-0.1` (single agent, subagents
   disabled, 40-turn budget) in 8-turn segments, snapshotting at every boundary.
2. **Checkpoints.** A mechanical rule over the session event stream labels each boundary a
   **signal** state (latest verification execution — test run or repro script — failed, or
   repeat-edits without a passing verification) or a **quiet** state, then picks up to 2 signal
   + 1 quiet per source with seeded randomness, frozen before any outcome is seen.
3. **Branches.** From each checkpoint's frozen state: **continue** (`grok-build-0.1`) vs
   **escalate** (`grok-4.6`) — same transcript-up-to-k, same filesystem-at-k, same remaining
   budget, same prompt; 2 repetitions per arm, execution order interleaved.
4. **Grading.** Every branch is graded by the SWE-bench harness in fresh containers; hidden
   tests are injected only at grade time. Estimand:
   **Δ(state) = P(solve | state, escalate) − P(solve | state, continue)**.

> [!NOTE]
> This is a paired counterfactual branch design with honest small-n reporting — not a
> causal-inference or benchmark claim. Checkpoints nest within 8 source trajectories, so CIs
> over checkpoints understate cluster correlation; the signal-vs-quiet *difference* is not
> established (both ≈ +0.38); and "escalate mid-flight vs restart fresh on grok-4.6" is a
> separate comparison this design does not measure.

## Results

![One rescue, drawn](plots/trajectory_django__django-11400__seg4__e047d153.png)
*One rescue: django-11400, forked at a deep failure state (turn 32). Both continue branches
fail cheap; both escalate branches solve.*

Task-level anchors (4–6 repetitions per task on the base model — with **zero within-task
variance**: every task is 100% or 0% across all reps):

| arm | solve | $/task | total tok/task |
|---|---|---|---|
| all `grok-build-0.1` | 74% | $0.28 | 944k |
| all `grok-4.6` | **100%** | $0.51 | **652k** |
| per-task-best (oracle*) | 100% | **$0.36** | 921k |

*Oracle selection conditions on observed outcomes — motivation, not a deployable policy.
Note grok-4.6 uses **fewer** tokens than the base model (it finishes instead of hitting the
turn cap); its premium is price-per-token, not volume.*

![Task-level cost/solve](plots/static_models.png)
*The task-level frontier behind the fork experiment: the escalation decision is worth 30% of
spend even before state-level allocation.*

## The inert dial (the finding we didn't go looking for)

The original design compared reasoning efforts. Its A/A control caught that Grok Build 1.0.5's
documented `--effort` flag does nothing on any xAI-served model:

- the server-delivered catalog marks **every** model `supports_reasoning_effort: false`, and the
  CLI silently drops the flag (the warning goes to tracing, never headless stderr);
- `--effort banana` is accepted without complaint;
- realized reasoning does not respond: `none` ≈ `xhigh` on three models, and across the 8-task
  suite the median per-task reasoning ratio between requested efforts is **0.96** — while task
  identity moves reasoning **~12×**.

![The dial is inert](plots/dial.png)
*Left: same prompt, requested effort swept — flat. Right: task moves realized reasoning ~12×;
the dial ~1×.*

Full receipts — catalog dump, source-level root cause in `xai-grok-shell`, probe tables, and the
A/A dataset — in **[docs/dial-finding.md](docs/dial-finding.md)**.

## Design integrity: a flaw we caught and fixed

The first fork design restored the filesystem to segment *k* but forked the parent session
**after the run finished** — and Grok Build sessions are append-only, so those branches
inherited the parent's *future*: a transcript describing all 40 turns, including (on solved
tasks) the working solution. Branch solve rates were answer-key-inflated, and one "divergence"
was a branch that read the transcript, declared the work done, and submitted nothing — onto a
filesystem where the fix did not exist. We caught this by auditing transcript sizes (a
"segment-3" child carried more history than its parent's full run), quarantined every v1 branch
(`results/forks_v1_leaky.jsonl`, plus `design: v1_leaky` tags in `results/runs.jsonl`), and
rebuilt around checkpoint sessions frozen at segment time. All clean records carry
`"design": "v2_marker_fork"`. Task-level results and the dial finding involve no forking and
were never affected.

## Repo map

| path | what |
|---|---|
| `results/runs.jsonl` | every agent invocation: usage, cost, stop reason, patch, verdict |
| `results/forks.jsonl` | checkpoints: state features, snapshot image, branch run ids |
| `runner/` | container lifecycle, segmented sources, checkpoint forking, grading |
| `plots/` | all figures + the scripts that regenerate them from the raw records |
| `docs/dial-finding.md` | the inert-dial dossier |
| `tasks/manifest.json` | the frozen, difficulty-stratified SWE-bench Verified subset |

## Reproduce

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python swebench==5.0.2 datasets matplotlib
echo 'XAI_API_KEY=...' > .env
.venv/bin/python runner/select_subset.py                                  # manifest + prompts
.venv/bin/python runner/sweep.py --model grok-build-0.1 --efforts medium  # sources + snapshots ("L1" = task-level layer)
.venv/bin/python runner/grade.py --layer L1
.venv/bin/python runner/forker.py --arms "continue=grok-build-0.1,escalate=grok-4.6" --reps 2
.venv/bin/python runner/grade.py --phase fork_branch
.venv/bin/python plots/static_models.py && .venv/bin/python plots/marginal.py
```

> [!NOTE]
> SWE-bench eval images are amd64-only. On Apple Silicon everything runs under Rosetta
> emulation (wall-clock is inflated; tokens and dollars are unaffected). Total cost of the full
> experiment as shipped: ≈ $65 of API spend.

| pinned | value |
|---|---|
| Grok Build | 1.0.5 (`bin/SHA256SUMS`) |
| Models | `grok-build-0.1` (base) vs `grok-4.6` (escalation target); effort constant (and inert — see above) |
| Grading | `swebench==5.0.2`, fresh run-id per batch |

## Validity notes

- **One manipulated variable.** Branch arms differ only in model id; prompts, budgets,
  snapshots, transcripts identical; subagents disabled everywhere; every run row records its
  full flag set for post-hoc verification.
- **Grader integrity.** Hidden FAIL_TO_PASS tests exist only at grade time; the eval script
  re-checkouts touched test files; the runner flags any test-path edits in diffs.
- **Failure hygiene.** API/transport failures are `status=error` — excluded, never scored.
  Completions are validated (session id present); a mechanical classifier (regression-tested in
  `runner/test_features.py`) derives all state features; nothing is hand-annotated.
- **Observed-vs-documented CLI behavior** is recorded wherever it diverges (max-turns stops
  report `cancelled`; the effort warning never reaches headless stderr).

## Future work

The fork dataset is the natural training set for a learned value-of-computation policy —
P(solve | state, action) per unit cost; a threshold escalation rule falls out of the marginal
plot first. Verifying wire-level behavior of reasoning parameters against the xAI API directly
(outside the CLI) is the open item from the dial finding.

Motivated by [Prime Agent](https://arxiv.org/abs/2608.23552) (Karten et al., 2026): harnesses
are computation managers, and models struggle to operate the compute they expose. This repo
measures that gap twice — once in a knob that turned out not to be connected, once in an
allocation decision worth +0.4 solve probability.
