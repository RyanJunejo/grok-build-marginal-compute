# Finding: `--effort` is silently inert for every xAI-served model in Grok Build 1.0.5

While validating the experimental setup for a reasoning-effort study (an A/A sanity check any
paired experiment should run), we found that Grok Build's documented `--reasoning-effort` /
`--effort` flag has no effect for any xAI-catalog model, and fails silent in headless mode.

## What we can assert, with receipts

**1. The model catalog declares no effort support — for any model.**
`~/.grok/models_cache.json` (server-delivered catalog, CLI 1.0.5, API-key auth) lists every xAI
model — `grok-build-0.1`, `grok-4.5`, `grok-4.6`, `grok-4.3`, `grok-4.20-0309-reasoning`,
`grok-4.20-0309-non-reasoning`, `grok-4.20-multi-agent-0309`, and the imagine models — with:

```json
"supports_reasoning_effort": false, "reasoning_efforts": []
```

**2. The CLI therefore drops the flag before the request is built.**
`crates/codegen/xai-grok-shell/src/agent/mvp_agent/reasoning_effort.rs`,
`ModelsManager::apply_supported_effort()`:

```rust
if !self.model_supports_reasoning_effort(&sampling.model) {
    tracing::warn!(..., "reasoning_effort: model does not support effort; ignoring it");
    return;
}
```

The warning is emitted through `tracing`, not stderr — in headless mode nothing reaches the
caller. The user-guide (14-headless-mode.md) documents the flag as working "in TUI and headless"
with unsupported *levels* producing a visible warning; in practice, with these models, no
warning is observable and the flag is a no-op.

**3. Invalid effort tokens are accepted.**
`grok -p "hi" -m grok-build-0.1 --effort banana --output-format json` returns a normal
completion (observed 2026-08-26, CLI 1.0.5). The documented behavior is a hard failure on
unknown tokens.

**4. Realized reasoning does not respond to the flag.**
Same prompt (a small combinatorics puzzle), reasoning tokens from the headless `usage` block:

| model | effort | reasoning tokens |
|---|---|---|
| grok-build-0.1 | none | 466, 291 |
| grok-build-0.1 | medium | 446, 685 |
| grok-build-0.1 | xhigh | 441, 797 |
| grok-4.20-0309-reasoning | low | 468 |
| grok-4.20-0309-reasoning | xhigh | 250 |
| grok-4.6 | none | 504 |
| grok-4.6 | xhigh | 460 |

`--effort none` produces as much reasoning as `--effort xhigh`. Note the non-zero reasoning under
`none` — the flag isn't scaling anything down either.

**5. The agentic-scale consequence: an accidental A/A study.**
Before discovering this, we ran a full paired comparison on 8 SWE-bench Verified tasks
("medium" vs "xhigh", 40-turn budgets, single agent, identical prompts) plus 17 mid-trajectory
fork checkpoints with paired continuations. As expected for two samples of the *same*
configuration: identical per-task resolution matrices (75% vs 75%), median per-task
reasoning-token ratio 0.96 (range 0.52–1.47 — pure task-to-task variance), aggregate
Δsolve = 0.00 across all checkpoints. Two checkpoints showed arm differences (e.g. one branch
quit after 2 turns with an empty patch while its three siblings solved) — which, being
same-config runs, measure the **per-state sampling variance** of trajectory outcomes. We keep
this data as the experiment's noise-floor calibration.

## What we cannot assert

- Whether the xAI **API** would honor a reasoning-effort parameter if one were sent: the client
  never sends it for these models, and our attempt to force a menu via
  `[model."grok-build-0.1"] reasoning_efforts = [...]` in `config.toml` did not measurably change
  realized reasoning — but we could not confirm whether that override survives the remote
  catalog merge, so this remains open.
- Whether OAuth-authenticated or internal builds see a different catalog.

## Why it matters

Prime Agent (arXiv 2608.23552) argues that harness capabilities go underused because models
weren't trained to operate them. This is the same gap one level down: the harness *surface*
advertises a compute dial (documented, flag-validated in the parser, echoed in docs), while the
wire layer discards it — silently. Any experiment, product tier, or cost policy built on
`--effort` against these models is currently measuring sampling noise. It also means
effort-based cost dials (à la Amp's low/medium/high/ultra) cannot currently be implemented on
this stack via the advertised knob.

All raw probe outputs, run records, and the A/A dataset are in this repository
(`results/runs.jsonl`, `results/forks.jsonl`).
