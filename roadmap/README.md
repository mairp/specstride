# Wiggum Roadmap

This directory contains implementation roadmaps and operational guides for planned Wiggum improvements.

## Documents

- [Prime Agent observability](prime-agent-observability.md) — verified findings, risks, and phased remediation plan for Prime proposer/critic observability.
- [Prime RLM child-event observability gap](prime-rlm-child-events.md) — findings and remediation plan for unrecognized `rlm_child_*` records and warning floods.
- [Running a Ralph loop with Prime Agent](running-prime-ralph-loop.md) — prerequisites, commands, selector choices, artifacts, and troubleshooting.
- [Post-run parallelism implementation prompt](prompts/post-run-parallelism-implementation.md) —
  handoff prompt for the agent that implements L1/L2 and L3 once the active run finishes. **Planned.**
- [Stage parallelism and the Rust/Go question](stage-parallelism-research.md) — why stage
  parallelism belongs to the mixture-of-loops launcher, not to a Wiggum rewrite. **Current.**
- [Deriving a run pipeline from a Spec Kit artifact set](speckit-pipeline-skill.md) — design thinking for a skill that reads a Spec Kit spec set and emits the launch contract a run needs (the generalisation of `run-005-all.sh`). **Planned.**
- [The critic's byte budget is per-block, never per-prompt](critic-prompt-budget-is-per-block-not-per-prompt.md) —
  open defect: `GROUNDING_TOTAL_CAP`, `DIAGNOSTICIAN_TOTAL_CAP` and `EVIDENCE_MAX_BYTES` are
  each capped alone and never summed against the backend's context window; the rejection
  history has no cap at all. **Done.**

- [A cleanly-blocked phase looks like work to every futility detector](futility-detectors-miss-a-cleanly-blocked-phase.md) —
  all three futility detectors ask "is this pass stuck?"; none asked whether the
  SEQUENCE was going anywhere, so a phase blocked on an operator decision burned all
  twenty passes. Fixed by a consecutive-no-progress breaker (exit 8). **Done.**

- [The repeat-stall watchdog punishes polling a backgrounded long job](repeat-stall-punishes-polling-a-backgrounded-long-job.md) —
  open: `REPEAT_IGNORE` exempts long jobs by command NAME, so foreground `make test` is
  safe and backgrounding the same suite then tailing its log is killed. Three kills,
  ~1h40m, in one run. **Planned.**
- [What Specstride and mixture-of-loops need to be a self-improving loop](self-improving-loop-plan.md) —
  the learning loop observes and applies but never checks whether a change helped, and MoL
  leaks `SPECSTRIDE_LEARNING` from the caller's shell into contract-driven runs. Ranked plan:
  close the provenance hole, wire the unread knobs, add `learn.py evaluate`. **Partial**: items
  0–7 are merged with tests; no live run has exercised an applied arm yet, and items 8–12 remain.
- [Self-improving loop implementation prompt](prompts/self-improving-loop-implementation.md) —
  handoff prompt for the unattended agent that lands plan items 0–4 and 7, then 5–6, one PR
  per item, with the plan's ground truth re-verified against the code. **Done** — every item
  it scoped merged; results in [self-improving-loop-report.md](self-improving-loop-report.md).
- [Reverse-engineer a codebase into Spec Kit artifacts](prompts/reverse-engineer-to-speckit.md) —
  handoff prompt for `specstride --reverse <folder>`: deterministic inventory, a generated driver
  spec, and a deterministic Spec Kit linter as each phase's verification command. **Planned.**
- [New harness backends: research prompt](prompts/new-harness-backends-research.md) —
  handoff prompt that sends Opus/Sonnet research agents over OpenCode, Cursor and Google
  Antigravity, then writes one spec-sized roadmap per harness. **Done** — the roadmaps below.
- [New harness backends: overview and shared prerequisites](harnesses/README.md) —
  comparison of the three harnesses against the backend contract, the shared `SH-1…SH-7`
  items (backend registry, config-home overlay, stream-adapter seam, auth preflight,
  version drift, hang classification, critic context lookup) and the recommended order.
  Gemini CLI was not researched: Antigravity can run headless, and Google has retired
  Gemini CLI for consumer accounts. **Planned.**
- [OpenCode as a backend](harnesses/opencode.md) — `opencode[:provider/model]`: proven live
  end to end on v1.18.32; critic through direct HTTP. Items `OC-1…OC-7`. **Planned.**
- [Cursor as a backend](harnesses/cursor.md) — `cursor[:slug]`: headless `agent -p` proven
  up to the auth check; blocked on a credentialed spike (CU-0); the critic can only use the CLI.
  Items `CU-0…CU-5`. **Planned.**
- [Google Antigravity as a backend](harnesses/antigravity.md) — `antigravity[:slug]` through
  the `agy` CLI, plus a `gemini` critic through the Gemini API. The headless path is proven up
  to the model call. Items `AG-0…AG-5`. **Planned.**

## Status legend

- **Current**: verified against the repository and recorded run artifacts.
- **Planned**: proposed work, not yet implemented.
- **Done**: implemented and validated with automated tests and a live run.
