# Self-improvement loops over agents — what shipped (2026-09-12)

Branch `self-improvement-loops`, built from `main` d8bccaf after feature 002 of
semantic-router-sovereign merged. Design of record:
`research/self-improvement-loops/02-wiggum-loop-design.md` (§6 steps 0–5);
measurements: `03-002-run-telemetry.md`; literature: `01-literature-and-patterns.md`.

The unit that improves is the loop, not the agent. Every change below is one of:
a fact Wiggum now records about its own passes, a way for the loop to spend less
model time on waiting, or a bounded knob the loop may move from what it recorded.
The critic's independence — its grounding, backend, timeout, `--max-rejects` and
the verification documents — is never adjustable (locked by a test in
`lib/test_learn.py`).

| Step | Commit | What changed | Exit / event | Off by default? |
|---|---|---|---|---|
| 0 | ba10051 | `lib/learn.py summarize` — the §5.3 metric set over `events.jsonl` (wait vs. work, cost per approved criterion, kills by reason, false-MISSING rate) | — | measurement only |
| 1 | eb5678a | a watchdog `hard_cap` kill counts toward `WIGGUM_PROPOSER_MAX_CAPS` (default 3), not `WIGGUM_PROPOSER_MAX_ERRORS` | exit **10** `proposer_cap_exhausted`; `iter_error` carries the reason | stricter than the `.env` workaround it replaces |
| 2 | 7a84387 | per-phase pass ceiling: `--proposer-timeout-phase N=SECONDS`, `WIGGUM_PROPOSER_TIMEOUT_PHASE_<N>`, or `"phaseTimeouts": {"N": S}` in the verification-commands document; resolved by `resolve_proposer_timeout`, sourced (`override` / `learned` / `declared` / `global`), persisted in `last-run.conf`, emitted as `proposer_cap` | — | yes: nothing declared = today's behaviour |
| 3 | b6538fc | yield/resume, protocol `wiggum-pass-yield/v1`: a pass ends cleanly declaring "waiting on job X, resume when <predicate>" (`pid`, `exit_code_file`, `file_exists`, `file_stable`, `grep`; `command` only under `WIGGUM_YIELD_ALLOW_COMMAND` with fixed argv); Wiggum owns the job (`wiggum_launch_owned_job`, run-scoped markers), polls for free, resumes the next pass with the job's result; `deadline_sec` mandatory, `max_yields_per_attempt`, stop.flag during a yield exits 6 with the job alive; a yield is not an error and not no-progress | exit **9** on yield budget/deadline; `yield_job_start` → `pass_yield` → `yield_wait` → `yield_resume` | yes: no yield artifact = no change |
| 4 | b42a82e 5a63e24 89facb4 8373be4 | gate-owned long measurements: declared commands may carry `stage: prestage|gate|both`, `reportPath`, `reusePolicy`, `detached`, `cumulative` (default true; `false` is opt-in and listed in the plan's `assumptions`); `verification_plan.py prestage` runs the phase's prestage commands ONCE before the proposer and hands the report into the proposer prompt; the gate reuses a passing prestage only at the same clean revision (`(planHash, commandId, revision, dirty)`), recording `reusedFrom`; a detached prestage has a polled deadline and is never signalled | `prestage_done`; exit 11 from `prestage` is informational | yes: an entry with no `stage` is byte-identical |
| 5 | 1934291 | learning loop: `learn.py advise` (3-sample floor, futility-killed samples excluded, bounds `[900, 2×default]`, ±50 % per step), `apply` (append-only `<feature-dir>/learning/applied.json` with provenance + `knob_adjusted`), `revert <run-id>`, `resolve` (the shell-callable read); `wiggum learn [--show|--apply|--revert <run-id>|--off]`; only `proposer_timeout` has an engine | `knob_adjusted` | yes: `WIGGUM_LEARNING` unset/`off` never opens the file |
| 2+5 | 1b02fb9 | `resolve_proposer_timeout` route 1.5: under `WIGGUM_LEARNING=apply` the applied value replaces the declared/global ceiling, sourced `learned`; an operator override still wins | — | yes |

Two defects in the shared launch path were found and fixed on the way (step 3):
`setsid` forks when the caller is a process-group leader, so `$!` named a
short-lived launcher rather than the job; and the async supervisor kept the
caller's stdout open for the job's lifetime, so a reader saw no EOF until the job
ended.

## Operating it

- Nothing changes for a project that declares nothing. Turn things on one at a
  time: a `phaseTimeouts` entry for the phase whose evidence is a long run; a
  `stage: prestage` on that run's declared command; then let the proposer yield
  instead of sleeping.
- Reuse needs a clean tree at both ends: `.wiggum/` and `testautomation/` must be
  git-ignored on the project, or every run is dirty from its own artifacts and the
  gate correctly re-runs everything.
- `wiggum learn --show` before `--apply`; `--revert <run-id>` undoes one decision,
  `--off` all of them. The allowlist test names why a critic-facing knob can never
  be added without editing a test.

## Deployment rule (why this branch is not on `main` yet)

A running MoL contract may hash-bind `orchestrator.sh`, `proposer.sh`, `wiggum`,
`lib/critic.py` and `lib/verification_plan.py` (003 does); editing the deployed
checkout mid-run refuses the next relaunch (exit 23) and editing a running bash
script is unsafe. Merge to `main` in `/root/wiggum` only while no run is live,
then regenerate the affected contracts.

## Closed since (branch `prime-cap-accounting`, from `main` 43ba4fb)

| Item | Commit | What changed |
|---|---|---|
| Cap accounting in the Prime-backed proposer path | bf41a9d | `proposer.sh` hands the watchdog kill to `finalize_invocation.py`, which records `kill_reason` + the stable `kill_class` on the ONE durable `result.json` (and its `agent_result`) and charges the pass to exactly one breaker: `budget` → the new cap counter in `error_breaker.py` (`WIGGUM_PROPOSER_MAX_CAPS`, exit **10**, `iter_cap` + `pass_cost_unknown`), anything else → the per-invocation error breaker, which a budget kill neither increments nor resets. Both backends now halt on the same facts with the same exit code, and `learn.py summarize` classifies a Prime kill exactly as a legacy one |
| The `phase_done` observation hook | d271bbb | one delimited call site in `orchestrator.sh` runs `learn.py observe` over this run's `events.jsonl` into `<feature-dir>/learning/phase-<N>.json` and emits `learning_observed`. Guarded by `WIGGUM_LEARNING` (unset/`off` = nothing happens), skipped silently when the installed `learn.py` has no `observe` subcommand, and best-effort otherwise: a failure is logged to the run log and never costs an approved phase |

`learn.py observe` itself is being added on another branch; until it lands the
hook is inert by the subcommand check, which is also pinned by a test.

## Closed since (the self-improving loop, 2026-09-22)

Plan: `self-improving-loop-plan.md`; run report: `self-improving-loop-report.md`; design of
`evaluate`: `research/self-improvement-loops/05-evaluate-design.md`. Commits are the squash
merges on `main` (Specstride) and `mixture-of-loops` `main` (MoL).

| Item | Commit | What changed |
|---|---|---|
| 0 (MoL) | 2c48401 | a `specstride` stage declares its learning mode; the runtime strips any inherited `SPECSTRIDE_LEARNING` and passes `off` explicitly; validation accepts only `off`/`suggest`/`apply`, rejects `from_env` for it and rejects `learning/` state as a contract source |
| 1 | 19c1492 | `yield_poll_interval` is read by the run (`resolve_yield_poll`, sourced `override`/`learned`/`default`, on `proposer_cap`); `inject_yield_hint` removed from the allowlist (the yield contract is already in every prompt); `specstride learn` passes the asked knob's own default |
| 2 | 6967005 | samples and decisions keyed on the phase's shape (`specstride_spec.py shape`), tick-invariant; `applied.json` schema `/2`; a shape-less decision is never silently applied |
| 3 | f47deb1 | `diagnostician_done` carries `case` (`grounding`/`real_gap`/`unknown`); `summarize` counts it per phase |
| 7 | d1a7b30 | a test locks every `learn.py` invocation site; none in `critic.py` or `verification_plan.py` |
| 4a | a14a571 | `learn.py evaluate`: baseline recorded at apply, per-pass primaries clustered by episode, effect-versus-MDE labels (`helped`/`neutral`/`regressed`/`insufficient`), `knob_evaluated`, the `phase_done` hook |
| 4b | 2ec5dde | guardrails (exact binomial and rank tests), automatic revert on a breach, quarantine with `--force`, the evaluation summary on every exit |
| 4c | 5322903 | the arm on every `proposer_cap`; the prefix-immutability tamper rule and `events_tampered` |
| 5 | c5faa5f, MoL ebaf6be | `specstride learn --summarize`/`--evaluate` (read-only); MoL `supervise.py retro` writes `runs/<id>/retrospectives/<digest>.json` and never applies or reverts |
| 6 | 7b35f3a, MoL 415f825 | `SPECSTRIDE_LEARNING_THROUGH` bounds `resolve` to a contract; MoL's `configuration.learning` binds a hashed prefix of the decision log, with a named `learning-decisions-changed` refusal |

## Still open

- No live run has exercised an applied arm: every evaluation path is proven by tests and
  synthetic streams only. The first real `helped`/`regressed` label is still to come.
- Plan items 8–12 (process lessons, cross-project priors, history-informed derivation,
  offline prompt optimisation, harness-quality log).
- The MoL contract generator's task-tick postcondition (`[x]` vs `[X]`, F15 in 002's
  troubleshooting log) lives in the projects' regenerate scripts, not here.
