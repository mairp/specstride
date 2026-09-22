# Self-improving loop — implementation report (2026-09-22)

Run of `roadmap/prompts/self-improving-loop-implementation.md` against Specstride `main`
a3d674f and mixture-of-loops (MoL) `main` 1788911. Every scoped item — 0, 1, 2, 3, 4 (as
4a/4b/4c), 7, then 5 and 6 — is **merged**, each through its own PR, squash-merged by
Mergify after green CI. Nothing was pushed to `main`; no `gh pr merge`; no paid backend or
`specstride run` was started; nothing was written under any `.specstride/`, `.wiggum/`,
`.ralph/` or `runs/` tree (the corpus was read only).

Item −1 needed no action: the plan and prompt were already on `main` as a3d674f (#18).

## 1. Per item

**0 — MoL: a stage declares its learning mode (merged, #17).** `runtime.resolve_env` strips
`SPECSTRIDE_LEARNING` and its legacy spelling above the no-`env` early return (covering stage
actions and `command_success` checks) and, given `kind="specstride"`, sets
`SPECSTRIDE_LEARNING=off` explicitly. `_validate_action` accepts only `off`/`suggest`/`apply`
and rejects `from_env` for it; `validate_contract` rejects a source under `learning/` of either
state tree. Decisions: the stage kind reaches `resolve_env` as a new keyword (`run_action`
already had it), so no runtime-wide change; the legacy key standing beside the current one is
rejected (normalization leaves it in place and it would otherwise be unvalidated). Tests:
`tests/test_mixture_of_loops.py` (+6). Docs: README supervision section,
`references/contract.md`, `references/derivation.md`. No legacy-name literal was added, so no
rename-guard allowlist line was needed (the constant is built from `LEGACY_ENV_PREFIX`).

**1 — wire `yield_poll_interval`, remove `inject_yield_hint` (merged, #21).** Decisions: the
resolved poll interval rides as an env prefix on the proposer command, not an `export` — an
exported value would read as an operator override at the next phase's set-ness test; the
values are added to `proposer_cap` (`yield_poll`, `yield_poll_source`) rather than a sibling
event; the `summarize` branch records one cap per `(run, phase)` under `phases[*].caps`;
summary schema bumped per shape change (`/2` here, reaching `/7` by 4c). Tests:
`lib/test_learn.py`, `lib/test_orchestrator_verification.py` (a witness in the fake agent
proves the child receives the value). Docs: README *Three loops*, *Learning*, event table, key
knobs; wiki `Learning.md`, `Configuration.md`, `On-Disk-Contract.md`, `Architecture.md`. The
first push failed CI on Specstride's own rename guard (a literal legacy env prefix in a new
test); fixed on the branch.

**2 — key learned state on the phase's shape (merged, #23).** `specstride_spec.py shape N`:
first 16 hex of SHA-256 over `(n, title, criteria)` with whitespace collapsed. Decisions: the
shape is recorded on every `phase_start` whatever the learning mode (so samples can be
attributed later); the learned route is skipped when no shape is known; unknown-shape samples
count for no shape; `resolve`'s stderr (the one-line notice for a shape-less decision) now goes
to `run.log`; `learn.py` computes shapes for `--specs` by importing `specstride_spec` (no second
parser). Tests: `lib/test_specstride_spec_tick.py`, `lib/test_learn.py`,
`lib/test_orchestrator_verification.py` (seeds carry a shape; stand-ins accept
`--phase-shape`). Docs: README *Learning*, event table; wiki `Learning.md`,
`On-Disk-Contract.md`.

**3 — the diagnostician's case (merged, #25).** `critic.py` gains `diagnostician_case()` and one
field on `diagnostician_done`. Decision: the first *non-blank* line is parsed, tolerating
markdown emphasis. Tests: `lib/test_critic.py`, `lib/test_learn.py`. Docs: README event table,
wiki `On-Disk-Contract.md`.

**7 — the invocation lock (merged, #27).** A `(file, subcommand)` **multiset** (counts, not a
set — a second `resolve` call site fails too), over tracked `.sh`/`.py` files and shebang
scripts. Grew in 4a (`evaluate`, its probe), 4b (`evaluate --report` at exit and in `--show`)
and 5 (`summarize`, `evaluate`). Docs: README, wiki `Learning.md`.

**4a/4b/4c — `learn.py evaluate` (merged, #29, #31, #33).** Design note first:
`roadmap/research/self-improvement-loops/05-evaluate-design.md`. Decisions beyond the prompt:
evaluate entries are appended only when the result changed (label, arm sizes, guardrail
states), so re-approving a phase adds nothing; replay ignores evaluate entries; the rate
guardrails shrink the baseline rate by half a trial (`(k+0.5)/(n+1)`) so a 0/6 baseline cannot
turn one applied-arm event into p = 0; the critic-input-size test is an exact rank test (top-k
of the pooled prompts all applied, `C(n_a,k)/C(n_a+n_b,k)`); a verdict transcript is matched to
the attempt whose verdict is nearest in time (≤ 900 s), because attempt numbers reset per run;
the arbitration proxy counts an episode whose run stopped on that phase for one of the four
reasons; quarantine exits 4; the MDE unit test reads "≥ 1.5" as the relative effect
(`exp(MDE) − 1 = 2.18`) since the log-scale MDE at 3/arm is 1.157; the exit summary required an
`add_exit_hook` registry (see §6); the tamper bracket is stamped on `proposer_cap`
(`tamper_bracket=on`), which is how "a run with no pre-digest is excluded" is decided; the
tamper record is the `events_tampered` event itself (it sits in the prefix the next bracket
protects). Tests: `lib/test_learn.py`, `lib/test_orchestrator_verification.py` (the
`_LEARN_EVALUATE` fragment and tail branch; `_script_root` unchanged). Docs: README *Three
loops*, *Evaluation* subsection (labels, MDE, guardrails and proxies, auto-revert, quarantine,
arm, tamper rule), on-disk table, event table; wiki `Learning.md`, `On-Disk-Contract.md`,
`Architecture.md`.

**5 — `supervise.py retro` (merged: Specstride #35, MoL #18).** Specstride gains read-only
`specstride learn --summarize` and `--evaluate` (the latter always `--dry-run`). Retro probes
both with `--help`, stores the summary's totals and a per-phase subset (not the whole summary),
the evaluation lines, and a suggested `--revert` for each `regressed` line. Docs: Specstride
README CLI row and usage block, wiki `CLI-Reference.md`, `Learning.md`; MoL README,
`references/contract.md`. Tests: `lib/test_learn.py`; MoL `tests/test_supervision.py`.

**6 — bind decisions into the contract (merged: Specstride #37, MoL #19).** Decisions: reverts
after `SPECSTRIDE_LEARNING_THROUGH` all count (auto, `--revert`, `--off`: each only moves toward
the default); a `through` naming no entry binds no applies; the bootstrap binds through the
log's last line with `--learning-mode` defaulting to `off`; `decisions_sha256` hashes the raw
bytes through the end of that line. Tests: `lib/test_learn.py`; MoL `tests/test_supervision.py`.
Docs: Specstride README key knobs, wiki `Configuration.md`; MoL README, `references/contract.md`
(*Learned decisions*), `references/derivation.md`, the schema file.

## 2. Evidence

Pre/post counts for Specstride are `pytest --collect-only` on the base and on the PR head; the
local serial full-suite line is from `python3 -m pytest lib/ -q -p no:cacheprovider` in a
scratch worktree of the branch before its final rebase (content identical; the base moved only
by the item beneath it). `shellcheck -S error` and `bash -n` were clean on every shell file
touched.

| Item | PR | CI run | shellcheck -S error | Test summary (pre-change count) | Merge SHA | `main` after merge |
|---|---|---|---|---|---|---|
| 0 | [MoL#17](https://github.com/mairp/mixture-of-loops/pull/17) | [35685290290](https://github.com/mairp/mixture-of-loops/actions/runs/35685290290) success | none touched | `Ran 164 tests in 88.841s OK (skipped=1)` (158) | 2c48401a7e63f1f85ea616525addbf82af183cbd | 2c48401a7e63f1f85ea616525addbf82af183cbd |
| 1 | [#21](https://github.com/mairp/specstride/pull/21) | [35686670830](https://github.com/mairp/specstride/actions/runs/35686670830) success (first push [35685853580](https://github.com/mairp/specstride/actions/runs/35685853580) failure: rename guard) | orchestrator.sh, specstride: clean | `1 failed, 642 passed in 1932.28s` on the pre-fix commit (the same rename-guard line CI caught); 643 collected (647) | 19c1492587f747384024dac2512c84df4954bee6 | 19c1492587f747384024dac2512c84df4954bee6 |
| 2 | [#23](https://github.com/mairp/specstride/pull/23) | [35687588553](https://github.com/mairp/specstride/actions/runs/35687588553) success | orchestrator.sh, specstride, specstride-lib.sh: clean | `654 passed in 1985.98s` (643) | 6967005fc2eb1cd973a453e0d50c6687a9fa523b | 6967005fc2eb1cd973a453e0d50c6687a9fa523b |
| 3 | [#25](https://github.com/mairp/specstride/pull/25) | [35688743930](https://github.com/mairp/specstride/actions/runs/35688743930) success | none touched | `657 passed in 1953.26s` (654) | f47deb155a9a818beaef8a5a7c625ecf1b3712d7 | f47deb155a9a818beaef8a5a7c625ecf1b3712d7 |
| 7 | [#27](https://github.com/mairp/specstride/pull/27) | [35689551282](https://github.com/mairp/specstride/actions/runs/35689551282) success | none touched | `658 passed in 2016.83s` (657) | d1a7b301a323f837941b310ee30dba1b598208d6 | d1a7b301a323f837941b310ee30dba1b598208d6 |
| 4a | [#29](https://github.com/mairp/specstride/pull/29) | [35690391479](https://github.com/mairp/specstride/actions/runs/35690391479) success | orchestrator.sh, specstride: clean | `675 passed in 2128.37s` (658) | a14a571d2638f1a640084758c537bad17f4f2544 | a14a571d2638f1a640084758c537bad17f4f2544 |
| 4b | [#31](https://github.com/mairp/specstride/pull/31) | [35691269442](https://github.com/mairp/specstride/actions/runs/35691269442) success | orchestrator.sh, specstride: clean | `688 passed in 2212.08s` (675) | 2ec5dde436b3719b9e1e791441ff800e4be1e69c | 2ec5dde436b3719b9e1e791441ff800e4be1e69c |
| 4c | [#33](https://github.com/mairp/specstride/pull/33) | [35692237838](https://github.com/mairp/specstride/actions/runs/35692237838) success | orchestrator.sh: clean | `695 passed in 2365.63s` (688) | 53229030b11d89ef4d276cce172a0499cc225e9b | 53229030b11d89ef4d276cce172a0499cc225e9b |
| 5 (Specstride) | [#35](https://github.com/mairp/specstride/pull/35) | [35693231326](https://github.com/mairp/specstride/actions/runs/35693231326) success | specstride: clean | `696 passed in 2354.82s` (695) | c5faa5f5663d43daab6791a64a7cffdfb8cf9165 | c5faa5f5663d43daab6791a64a7cffdfb8cf9165 |
| 5 (MoL) | [MoL#18](https://github.com/mairp/mixture-of-loops/pull/18) | [35694286654](https://github.com/mairp/mixture-of-loops/actions/runs/35694286654) success | none touched | `Ran 168 tests in 82.101s OK (skipped=1)` (164) | ebaf6bec7e406a58da9973a75540ad9116bc1109 | ebaf6bec7e406a58da9973a75540ad9116bc1109 |
| 6 (Specstride) | [#37](https://github.com/mairp/specstride/pull/37) | [35694229252](https://github.com/mairp/specstride/actions/runs/35694229252) success | none touched | `698 passed in 2330.54s` (696) | 7b35f3ae766564af988a3e924bbe7b250944f9d5 | 7b35f3ae766564af988a3e924bbe7b250944f9d5 |
| 6 (MoL) | [MoL#19](https://github.com/mairp/mixture-of-loops/pull/19) | [35695372620](https://github.com/mairp/mixture-of-loops/actions/runs/35695372620) success | none touched | `Ran 173 tests in 86.916s OK (skipped=1)` (168) | 415f825ebcc985a08010ecdc321efc081e80be08 | 415f825ebcc985a08010ecdc321efc081e80be08 |
| corrections | [#39](https://github.com/mairp/specstride/pull/39) | docs only; see the PR | none touched | docs only | see the PR | — |

Item 1's local full suite was not re-run after the one-line fix; its CI run on the fixed head is
the green evidence. Specstride's collected count fell 647 → 643 in item 1 because ten
`inject_yield_hint` tests were removed and six added.

## 3. Rollback targets

`git revert <merge SHA>` per item, newest first when reverting several (later items build on
earlier ones): 6 MoL 415f825, 6 Specstride 7b35f3a, 5 MoL ebaf6be, 5 Specstride c5faa5f, 4c
5322903, 4b 2ec5dde, 4a a14a571, 7 d1a7b30, 3 f47deb1, 2 6967005, 1 19c1492, 0 (MoL) 2c48401.
Nothing is left open, draft or stacked except the corrections PR (#39) and this report's PR,
both docs-only and queued for Mergify.

## 4. Item 4: design note, MDE, thresholds

Design note: `roadmap/research/self-improvement-loops/05-evaluate-design.md`.

Corpus (observational; 232 streams matched, 231 read, the 35 MB muse stream skipped; no
`applied.json` exists on the host): 242 episodes with a billed non-futility pass; passes per
episode mean 2.96, median 1; within-phase sd of log pass cost 0.554 median, 0.735 pooled over
94 phase keys with ≥ 2 passes. For a typical phase (`s = 0.554`, `m̄ = 2.96`, `DEFF = 1.98`) at
the 6-per-arm floor, the MDE is ≈ 1.26 on log cost, a factor of ≈ 3.5; with the prompt's
`s = 0.506` it is 1.151 (×3.16). At 3 per arm with `m̄ = 1` it is 1.157 (+218 %).

Thresholds: rate guardrails at exact one-sided binomial p < 0.01 with ≥ 10 applied trials
(about 5–6 % family-wise false alarms across the six tests, acceptable because a false alarm
only reverts toward the default; 10 is the smallest n at which 0.01 is reachable against the
corpus's base rates, e.g. 23 % `grounding_gap` needs 8 of 10); a new `verification_failed` at
any n; critic input size by exact rank test p < 0.01 with ≥ 6 per arm (5 of the 6 largest must
be applied-arm prompts, p = 0.0076); first-attempt approval both tails p < 0.01, ≥ 10 verdicts;
quarantine ±10 % until 6 new samples.

## 5. What the tamper rule does not cover

Fabricated *appended* events (a forged low-cost `agent_result` is indistinguishable from a tap
append; an in-band HMAC does not help against a same-uid writer); anything written outside the
bracket, including detached long jobs that outlive a pass; `verdicts/*.txt`,
`verification/*.json` and `GATE<n>-EVIDENCE.md`; spec edits (the shape detects, does not
prevent); appended decisions in `applied.json` during a pass (an operator may legitimately
apply concurrently); and a tamper in a run's final pass that also removes the orchestrator's
ability to write afterwards.

## 6. Corrections, and what else was found

Corrections PR: https://github.com/mairp/specstride/pull/39 (the prompt's list plus the items
below).

- `orchestrator.sh` (pre-run `:496` and `:733`): the mkdir-lock release and the presenter each
  installed `trap … EXIT`, the second replacing the first, so a live run on a host without
  `flock` left `lock.d` behind. Fixed in 4b with `add_exit_hook`, which the learning summary
  needed anyway.
- Item 6: `supervise.py` reads bundles with `check_sources=False` (`supervise.py:51`, `:60`), so
  a re-hash under `check_sources` never runs in the supervisor and `_bundle_for` never returns
  `None` for it; the named refusal is an explicit check in `classify_relaunch`, and a distinct
  `LearningDecisionsChanged` at the gate.
- The prompt's MDE unit-test wording ("returns ≥ 1.5 at n = 3") holds for the relative effect,
  not the log-scale MDE (1.157).
- The corpus glob matches 232 streams today, not 235.
- `specstride learn --show/--apply` read only the newest run's stream (`specstride:164-170`),
  so the cross-run 3-sample floor was almost never reachable from the CLI; they now read the
  runs tree (4a).

## 7. What is left, and the first step for each

- **A live applied arm.** Nothing has yet run under `SPECSTRIDE_LEARNING=apply` with an applied
  decision; every evaluation path is proven only by tests. First step: on a feature with ≥ 3
  runs of one phase, `specstride learn --show`, `--apply` one `proposer_timeout`, run with
  `SPECSTRIDE_LEARNING=apply`, and read `specstride learn --evaluate`.
- **Item 8 (process lessons).** First step: extract repeat-stall targets and
  `prompt_block_dropped` events per phase into a store outside `gates/`, gated by `evaluate`.
- **Item 9 (cross-project priors).** First step: define `global.json` keyed on phase shape and
  backend label.
- **Item 10 (history-informed derivation).** First step: have `retro` emit `advisory` findings
  from `phases[*].work_sec_p90` versus the stage's declared timeouts.
- **Items 11–12.** Unchanged from the plan; both wait on a live applied arm.
