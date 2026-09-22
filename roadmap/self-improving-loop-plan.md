# What Specstride and mixture-of-loops need to be a self-improving loop

**Status: Partial** (items 0–7 shipped 2026-09-22; see *Corrections* at the end and `self-improving-loop-report.md`). Written 2026-09-22 against `main` 3e5cf47 of this repository and the
`mixture-of-loops` (MoL) skill at `/root/mixture-of-loops`. Three read-only research passes
fed it: an audit of this repository, an audit of the MoL skill, and a literature survey. The
`file:line` references were verified against those checkouts; items marked *(inferred)*
were read from the code but not exercised. Companion documents:
`research/self-improvement-loops/01-literature-and-patterns.md`, `02-wiggum-loop-design.md`,
and `self-improvement-loops-shipped.md`.

A self-improving loop, as used here, learns from its own runs, changes how it drives the
next ones, and checks that each change helped, rolling it back when it did not. The critic
stays out of reach: nothing the loop learns may change what the critic reads or what a
verdict means.

## Where it stands

Specstride can **observe** (a per-phase observation at `phase_done`) and **apply** a bounded,
reversible decision. Nothing checks whether a decision helped. MoL has no learning integration
at all: it derives, launches, supervises and relaunches, and reads only `run_stop.reason` from
Specstride's `events.jsonl` (`supervisor_lib.py:863`). Today the pair is a human-operated tuning
tool, not a self-improving loop.

| Stage | Status | Where |
|---|---|---|
| Observe | exists, under `SPECSTRIDE_LEARNING` ≠ `off` | `orchestrator.sh:1904-1924` → `learning/phase-<N>.json` |
| Suggest | manual only (`specstride learn --show`); never printed at run end, although design §5.5 invariant 5 asks for it | `specstride:529`, `learn.py:1216` |
| Apply | manual only, one knob and one phase per call | `learn.py:1260`, `specstride:537` |
| Consume `proposer_timeout` | exists, under `SPECSTRIDE_LEARNING=apply` | `orchestrator.sh:1570-1576`, resolved per phase at `:1606` |
| Consume `yield_poll_interval` | **missing** | `proposer.sh:1031` defaults `SPECSTRIDE_YIELD_POLL:=30` |
| Consume `inject_yield_hint` | **missing, and redundant** *(inferred)*: the yield contract is already appended to every proposer and accelerator prompt | `orchestrator.sh:1195`, `:1522` |
| Evaluate an applied decision | **missing**: the decision records its sample count and source runs, but no baseline | `learn.py:973-978` |
| Keep or roll back | manual only (`--revert`, `--off`) | `learn.py:1062-1110` |
| Cross-project priors (`global.json`) | **missing**: designed in 02 §5.4, never built | — |
| MoL reads learning output | **missing**: no reference to `learn`, `SPECSTRIDE_LEARNING`, `phaseTimeouts` or `applied.json` in the MoL repository | — |

## A provenance hole that is open today

MoL builds each stage's environment from the caller's whole environment
(`skills/mixture-of-loops/scripts/runtime.py:109`, `resolve_env` starts from `os.environ`).
A `SPECSTRIDE_LEARNING=apply` left in the operator's shell therefore changes a contract-driven
run's proposer timeouts through `resolve_proposer_timeout`, and neither the contract nor its
digest records it. That breaks MoL's promise that the contract says what runs. It is item 0
below and should land before anything else.

A second, related gap: `applied.json` is not a contract source, so a `specstride learn --apply`
between two relaunches changes behaviour while the digest stays the same, and the supervisor
relaunches across a real change without noticing (`supervisor_lib.py:961-967`). The obvious fix
is wrong: `learning/phase-<N>.json` is rewritten at every approved phase
(`orchestrator.sh:1913-1919`), so binding it as a hashed source would refuse the next relaunch
with exit 23. Item 6 binds a snapshot of the decisions instead.

## The plan

| # | Repo | What to build | Where | Size | Risk |
|---|---|---|---|---|---|
| 0 | MoL | A stage must declare `SPECSTRIDE_LEARNING` (`off`, `suggest` or `apply`); the runtime strips any inherited value. Validation rejects `learning/*` as a contract source. | `runtime.resolve_env`, `contract_lib.py`, `references/derivation.md` | S | low |
| 1 | Specstride | Wire `yield_poll_interval`: resolve it next to `proposer_timeout` and export `SPECSTRIDE_YIELD_POLL` before the proposer starts. Redefine `inject_yield_hint` (for example: give the yield block priority so the prompt budget never drops it) or remove it from the allowlist. Fix `specstride learn` always passing `SPECSTRIDE_PROPOSER_TIMEOUT` as `--default` *(inferred)*, which makes the ±50 % step for the poll interval relative to 1800 instead of 30. | `orchestrator.sh:~1606`, `:1682`; `specstride:525` | S | low |
| 2 | Specstride | Key learned state on a hash of the phase's spec slice, not on the phase number alone (`learn.py:964`). Today an edited `tasks.md` silently applies old samples to a different phase, which violates design invariant 4 ("same phase shape"). | `learn.py`, `specstride_spec.py` | S | low |
| 3 | Specstride | Record the diagnostician's `CASE: GROUNDING` / `CASE: REAL-GAP` on `diagnostician_done` (today it carries only `bytes`, `critic.py:1745`). Read-only metric: it separates "the proposer cites badly" from "the work is incomplete". Never use it to tune what the critic reads. | `critic.py:1640-1660`, `:1745` | S | low |
| 4 | Specstride | **`learn.py evaluate`, the step that closes the loop.** At `apply`, record a baseline for the phase. At `phase_done`, next to `observe`, compare runs under the new value against it once at least 3 exist, and label the decision `helped`, `neutral` or `regressed`. On a guardrail breach, revert automatically and emit `knob_auto_reverted`; otherwise emit `knob_evaluated`. Metrics and guardrails: see *Evaluation discipline*. | `learn.py`, the `phase_done` hook | L | medium |
| 5 | MoL | `supervise.py retro`: a read-only retrospective per run. It calls `learn.py summarize` and `evaluate` over the stage's runs and writes `runs/<id>/retrospective.json`, keyed to the contract digest. It may *suggest* `learn --revert`; it never runs `apply` or `revert`. | `supervisor_lib.py`, `supervise.py` | M | low |
| 6 | both | Bind learned decisions into the contract (see *Interface*): a `configuration.learning` block covered by the digest, and a new `SPECSTRIDE_LEARNING_THROUGH=<run_id>` so `learn.py resolve` ignores decisions newer than the contract. | MoL schema, `bootstrap_contract.py`, `contract_lib.py`; `learn.py resolve` | M | medium |
| 7 | Specstride | Strengthen the allowlist test. `test_learn.py:425-442` compares knob *names* only; add an assertion that `learn.py resolve` is called only from the proposer launch path, and never from `critic.py` or `verification_plan.py`. | `lib/test_learn.py` | S | low |

**Items 0–4 and 7 are the smallest set that honestly earns the label "self-improving".**
Items 5 and 6 make it hold across a MoL pipeline.

### Later, and only after item 4

| # | What | Why it waits |
|---|---|---|
| 8 | **A process-lessons store.** Today FEEDBACK, HINT and ACCELERATION files feed retries within a phase (`orchestrator.sh:1477-1518`), then are archived into `attempts/` (`:1879-1884`) and never read again. Extract lessons from repeated *proposer-side* failures only (repeat-stall targets, `progress_stall`, hand-rolled waits, `prompt_block_dropped`) into a budgeted prompt block, Reflexion-style, append-only and ACE-style with provenance per lesson. Store it outside `gates/`, which the critic's grounding search covers (`critic.py:514-527`). | Without an evaluator there is no way to tell a helpful lesson from one that teaches the critic's vocabulary. |
| 9 | **Cross-project priors** (`~/.specstride/learning/global.json`), used to shrink a phase's estimate toward the project-wide value until it has local samples. Never applied without local samples. | Needs item 2's phase-shape keys to mean anything across projects. |
| 10 | **History-informed derivation in MoL.** Retrospectives emit `advisory` findings: split a phase that keeps hitting its cap, mark a slow gate command `stage: prestage`, declare `phaseTimeouts`. Accepting one is a human edit to `tasks.md` or `verification-commands.json`, which re-hashes and forces a new contract, as intended. | Needs item 5. |
| 11 | **Offline prompt optimization** of proposer prompt blocks (DSPy/GEPA-style) against a frozen replay corpus of past phases, proposed only as a PR gated by `lib/` tests and a human merge. | Expensive at ~$17 a pass; only worth it once items 4 and 8 exist. |
| 12 | **Harness-quality log in MoL.** The `/tmp/mol-live` matrix runs show derivations differing by harness and model (codex changed `tasks`/`declared_commands`; failures included `declared-command-preserved` and `no-pipeline-started`). Record which harness/model pairs derive faithfully and refuse `auto` for known-bad ones *(inferred remedy)*. | Independent; low priority. |

## Interface between Specstride learning and MoL

- **MoL reads, read-only:** `<feature-dir>/learning/applied.json`
  (`specstride.learn.applied/1`), `learning/phase-<N>.json` (hints only), and
  `learn.py summarize` output (`specstride.learn.summary/1`).
- **MoL binds** an optional block covered by the contract digest:

  ```json
  "configuration": { "learning": {
    "mode": "apply",
    "decisions_through": "<run_id>",
    "decisions_sha256": "<hash of applied.json lines up to that run_id>",
    "effective": { "proposer_timeout": { "3": 2700 } }
  } }
  ```

  Only the prefix of the decision log that existed at derivation time is bound, so later
  appends never make the contract stale. Validation re-hashes that prefix
  (`contract_lib.py:155`).
- **Specstride honours it:** the runtime passes `SPECSTRIDE_LEARNING` and
  `SPECSTRIDE_LEARNING_THROUGH=<run_id>`; `resolve` ignores later decisions. A mid-run
  `learn --apply` then waits for a re-derivation, and every re-derivation is a new, visible
  digest.
- **Feedback flows out, never in.** Retrospectives live in MoL's `runs/<id>/`.
  `applied.json` stays Specstride-owned; MoL never writes it.

## Evaluation discipline

Runs are expensive and few, so the evaluator has to be honest about what it can detect.

- **Primary metrics:** cost and wall-clock per *approved phase*, over the whole run. Never
  judge by one pass: a shorter cap makes each pass look efficient while causing more of
  them (the Huxley-Gödel Machine point, <https://arxiv.org/abs/2510.21614>).
- **Guardrails that veto a change**, even when the primary metric improved:
  - the critic's input size and `grounding_gap` rate;
  - false-MISSING and MALFORMED verdict rates (02 §5.3 already treats false-MISSING as a
    release check on the critic);
  - the human-arbitration rate;
  - the declared verification commands still passing.
- **First-attempt approval rate is an alarm, not a reward.** The knobs are not supposed to
  change verdicts, so a shift in either direction means something leaked.
- **Only large effects count.** Compare runs of the same phase, clustered by feature;
  clustering can widen standard errors more than threefold
  (<https://arxiv.org/abs/2411.00640>). Use McNemar for approved/not and a paired Wilcoxon
  for cost. Compute the minimum detectable effect before the runs; at Specstride's N, expect
  to detect only changes of roughly 25–30 % in cost per approved phase.
- **Alternate, don't before-and-after.** Interleave old and new values per phase across
  runs (ABAB). Record the model and version in each decision's provenance, and reset the
  baseline whenever the model changes.
- **Counterfactuals from logs, where they exist.** A *shorter* timeout can be evaluated from
  recorded pass durations. A *longer* one cannot, because killed passes are censored; that
  needs real runs.
- **Treat both arms alike.** Excluding futility-killed passes is right, but the exclusion must
  apply identically before and after a change.

## Risks

1. **Knobs reach the critic indirectly.** A longer `proposer_timeout` can produce more
   evidence, which fills the critic's grounding snapshot budget and changes verdicts. "No
   critic setting is tunable" is necessary but not sufficient; the critic-input guardrails
   above cover it.
2. **Lessons that teach the critic's language.** Lessons derived from rejections drift toward
   performing evidence instead of doing the work. Keep lessons to process and time ("start
   the suite before citing its report"), filter out citation and phrasing advice with a
   test, and keep the static citation guidance near `orchestrator.sh:~1335` hand-written.
   Have a judge from a different model family audit a sample, since same-family judges
   favour their own outputs (<https://arxiv.org/abs/2404.13076>).
3. **Tampering with evidence or telemetry.** A published self-improving agent reached a
   "perfect" score by removing its own hallucination-detection markers
   (<https://arxiv.org/abs/2505.22954>); METR saw frontier models edit tests and scoring code
   (<https://metr.org/blog/2025-06-05-recent-reward-hacking/>). **Unverified here:** whether
   the proposer's sandbox can write `events.jsonl` or `learning/`. Check this before item 4
   trusts either file. Add canary criteria that are known to be false and must always be
   rejected, as a standing proof the critic still works.
4. **A monitor that gets optimized against stops working.** A separate transcript monitor
   (test edits, skip markers, fabricated evidence) is worth having beside the critic
   (<https://arxiv.org/abs/2503.11926>), but its verdicts must never feed the learning
   signal, or the proposer learns to hide the behaviour.

## What not to do

- Reward approval rate or attempts-to-approval.
- Let the learning loop or MoL drop or loosen verification declarations, release gates,
  breakers, `--max-rejects`, `configuration.auto`, or anything the critic reads.
- Apply a decision automatically. Reverting automatically is fine: it only moves back toward
  the default.
- Rewrite prompts wholesale (ACE's "context collapse").
- Run population search at ~$17 a pass.
- Compare across model versions, or learn from a single feature.
- Let the optimizer read the critic prompt or the monitor's verdicts.
- Let a lessons file grow without a retirement rule.

## Sources

The literature pass cites 2025–2026 arXiv work that was not independently re-checked for
this plan; read a source before relying on its numbers. Beyond the links inline above:
Reflexion (<https://arxiv.org/abs/2303.11366>), Agent Workflow Memory
(<https://arxiv.org/abs/2409.07429>), SICA (<https://arxiv.org/abs/2504.15228>), MIPROv2
(<https://arxiv.org/abs/2406.11695>), OPRO (<https://arxiv.org/abs/2309.03409>), TextGrad
(<https://arxiv.org/abs/2406.07496>), Gödel Agent (<https://arxiv.org/abs/2410.04444>),
reward-hacking generalization (<https://arxiv.org/abs/2511.18397>).

## Corrections (2026-09-22)

Added at the end of the implementation run (`self-improving-loop-report.md`). The plan
above is left as written; where it and the shipped code disagree, the code and this list
win.

- **The MDE estimate is wrong by more than an order of magnitude.** "Expect to detect only
  changes of roughly 25–30 % in cost per approved phase" does not hold: with the within-phase
  sd of log pass cost this corpus shows (0.506–0.654 in the prompt's audit; 0.554 median,
  0.735 pooled over 94 phase keys in this run's measurement), the MDE at 3 passes per arm is
  ≈ +218 %, and detecting 30 % needs 58–97 passes per arm. `evaluate` therefore labels by
  effect-versus-MDE and prints both; it runs no significance test.
- **McNemar on approved/not scores approval rate**, which *What not to do* forbids. Neither
  McNemar nor Wilcoxon is implemented (minimum attainable two-sided p at 3 pairs is 0.25).
- **"Compare cost per approved phase over the whole run"** is not a usable primary:
  `cost_per_approved_phase` is undefined in 62 % of real runs. The primaries are per-pass log
  cost and log wall-clock, clustered by phase episode.
- **ABAB alternation is unreachable at this N and contradicts item 6.** Only 8 of 248 real
  phase keys ever saw ≥ 6 runs; a resumed run splits one episode across arms; a pinned
  contract value that silently reverts on alternate runs breaks MoL's promise. Version 1
  records the arm and compares before/after, and only reverting is automatic.
- **"Record the model and version"**: nothing records a model version. Baseline-reset
  granularity is the `run_start.backend` label.
- **Two guardrails have no event and one is dropped.** Critic input size is read from the
  verdict transcripts; human-arbitration rate is replaced by a named proxy (the stop reasons
  that hand a phase back); false-MISSING rate has no event and is not an item-4 guardrail
  (it stays an offline release check on `critic.py`).
- **`inject_yield_hint`'s "give it priority" option is not implementable** as a priority
  change: the yield contract is already the only budgeted block and is appended last to both
  prompts. The knob was removed from the allowlist instead.
- **Item 7 as specified cannot be written**: `learn.py` appears in comment lines across the
  tree and `resolve_knob` is called nowhere outside `learn.py`. The lock is on invocation
  lines, as a `(file, subcommand)` multiset.
- **The `append_budgeted_block` sites in the *Where it stands* table are swapped**:
  `orchestrator.sh:1195` closes `build_accelerator_prompt`, `:1522` closes
  `build_proposer_prompt`.
- **Risk 3 is answered: the proposer can write both files.** Every backend runs with
  permissions disabled in the project directory, and the proposer appends to the same
  `events.jsonl` by design. Item 4c's tamper rule is prefix immutability per proposer
  invocation; what it does not cover is listed in `05-evaluate-design.md` §4.
- **Unset `SPECSTRIDE_LEARNING` is `off`, not `suggest`**, contrary to design §5.5
  invariant 5; MoL now passes `off` explicitly to every stage that declares nothing.
- **Item 1's default bug changed the applied value, not only the display**: with
  `--default 1800` the poll interval's ±50 % window missed its `[10, 300]` bound, the
  degenerate branch dropped the step cap, and one apply could move 30 → 300.
- **Item 6: re-hashing under `check_sources` does not reach the relaunch classifier.**
  `supervise.py` reads bundles with `check_sources=False`, so `read_bundle` never raises there
  and the bundle is not turned into `None`. The named `learning-decisions-changed` refusal is
  therefore an explicit check in `classify_relaunch` (and a distinct exception at the gate,
  which reads with source checks).
- **Item 5's output path**: `runs/<id>/retrospective.json` would be overwritten on every
  relaunch and re-derivation (`runs/<id>` is keyed on the pipeline); it is
  `runs/<id>/retrospectives/<contract-digest>.json`.

Found during the run, not in the prompt's list:

- `orchestrator.sh`'s two `trap … EXIT` sites overwrote each other (the mkdir-lock release
  at the old `:496` and the presenter's at `:733`), so a live run on a host without `flock`
  left `lock.d` behind. Fixed in item 4b by registering exit work through `add_exit_hook`,
  because the learning summary needed an exit hook of its own.
- The corpus glob `/root/*/.wiggum/features/*/runs/*/events.jsonl` matches 232 streams on
  this host today (231 readable once the 35 MB stream is skipped), not 235.
