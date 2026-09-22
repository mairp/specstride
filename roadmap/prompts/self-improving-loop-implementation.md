# Prompt — make the learning loop self-improving (plan items 0–4 and 7, then 5–6)

You are an autonomous coding agent running unattended. Nobody will answer questions.
Make routine decisions yourself, record them in the final report, and stop only when a
step below is impossible (say why and what remains). Written 2026-09-22 against
Specstride `main` 3e5cf47 and mixture-of-loops (MoL) `main` 1788911, and revised the same
day after four independent audits of the first draft against both checkouts and 235
real event streams. Where this prompt and the plan disagree, this prompt wins; the
disagreements are listed under *Corrections to the plan* at the end.

Read these in full, in this order, before the first edit:

1. [../self-improving-loop-plan.md](../self-improving-loop-plan.md) — the plan of record.
   Its item numbers are the ones used below. Its *Evaluation discipline*, *Risks* and
   *What not to do* sections are requirements, not background.
2. [../self-improvement-loops-shipped.md](../self-improvement-loops-shipped.md) — what the
   learning layer already does. See *Corrections to the two documents* below.
3. `../research/self-improvement-loops/02-wiggum-loop-design.md` §5 and §6 — the design
   the shipped code follows; the five invariants in §5.5 bind every change you make.
4. `lib/learn.py` top to bottom (1,429 lines), then `lib/test_learn.py`. You are extending
   this module, and its docstrings carry the design decisions.

The docs use the old product name; the code is renamed. Read `WIGGUM_X` as
`SPECSTRIDE_X`, `wiggum` as `specstride`, `.wiggum/` as `.specstride/`.

---

## Item −1 — land the plan before anything else

`roadmap/self-improving-loop-plan.md`, this prompt, and the `roadmap/README.md` edit that
links to both are **untracked working-tree files** on branch `docs/self-improving-loop-plan`,
whose HEAD is identical to `main` 3e5cf47. Nothing is committed. Your first act, in
`/root/specstride`: `git switch docs/self-improving-loop-plan`, add those three files,
commit as `docs: plan and handoff prompt for the self-improving loop`, open the PR, and
let Mergify merge it. Every later branch starts from the `main` that contains it. Do not
use `git worktree`, `git stash` or `git clean` before this lands: the plan exists only in
this working tree.

## The one rule that outranks every other

**The critic stays out of reach.** Nothing you build may change what the critic reads,
how it is prompted, its backend, its timeout, `--max-rejects`, the verification
declarations, the release gates, or the breakers. The learning loop may make the
proposer cheaper. It may never make the gate easier. `test_learn.py` locks the knob
allowlist; item 7 extends that lock. The only permitted edit to `lib/critic.py` in this
whole prompt is item 3's one read-only event field. `lib/verification_plan.py` is not
touched at all. If a design choice below seems to need more, the design choice is wrong.

## Ground truth (verified 2026-09-22; re-verify before editing)

### Repositories and how a change lands

Specstride is `/root/specstride` (remote `mairp/specstride`), MoL is
`/root/mixture-of-loops` (remote `mairp/mixture-of-loops`). Both protect `main`; every
change lands through a pull request and Mergify squash-merges it. The two repos differ:

- **Specstride** requires checks `lint` and `test`. Mergify queues **any** non-draft PR;
  no label is needed. `required_conversation_resolution` is on, so an unresolved review
  thread stalls the merge silently. To hold a PR back, open it `--draft`: the
  `do-not-merge` label named in `.mergify.yml` **does not exist** in the repository.
- **MoL** requires the single fan-in check `ci`, and Mergify's only auto-merge condition
  is `label = automerge`. **That label does not exist yet either.** Before the first MoL
  PR run `gh label create automerge -R mairp/mixture-of-loops`, then after every
  `gh pr create` run `gh pr edit <n> --add-label automerge`, or the PR never merges.

`gh` is logged in as `mairp` with a fine-grained token that can open PRs. The rename
report (`roadmap/rename-specstride-report.md:26-38`) says PR creation was refused; that is
historical, PRs #8–#16 were created this way. Do **not** work around a PR failure by
pushing to `main`: it is rejected. `gh pr merge` in any form, including `--admin`, is
forbidden. Mergify is the only merger. Commit as the configured git user; do not change
author identity. End every commit message with the `Co-Authored-By:` trailer for the
model running you, and every PR body with
`🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

### Tests and lint, exactly as CI runs them

**Specstride.** CI runs Python 3.13 only, `pytest lib/ -q -n 16`, with a hard 30-minute
job cap. Locally `pytest-xdist` is **not** installed and `pip install` is forbidden, so
run serially: `python3 -m pytest lib/ -q -p no:cacheprovider` with a 40-minute timeout,
never in a shell that kills after 10. 35 files; five drive real subprocess timeouts and
take minutes each (`test_orchestrator_verification.py`, `test_proposer_yield.py`,
`test_proposer_watchdog.py`, `test_prime_pipeline.py`, `test_prime_backend.py`). Iterate
on `lib/test_learn.py` and whichever file you touch; run the full suite once per PR and
paste its summary line into the report.

Lint, exactly as CI does it: `bash -n <file>` and `shellcheck -S error <file>`. **Only
`-S error`.** Plain `shellcheck orchestrator.sh` reports 52 pre-existing notes and
warnings that CI does not gate on; fixing them is out of scope and violates the
no-unrelated-changes rule. CI selects scripts with `git ls-files | xargs file`, so a new
shell file needs a shebang or CI will not lint it.

**MoL.** CI runs a matrix of Python **3.10, 3.11, 3.12 and 3.13** with a 10-minute
per-job cap, fanning into one `ci` job. Local Python is 3.13.5, so **write MoL code to
3.10 syntax** or three CI legs fail on a change that is green locally. Run
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` from the repo root:
158 tests, about 70 seconds, one skip (measured 2026-09-22). **The env var is
mandatory**: without it `__pycache__` lands in `tests/fixtures/repos`, whose two trees
`test_fixtures.py` asserts byte-identical, and you break your own suite. The suite calls
no model and starts no real Specstride, but it renders real launchers and runs real
detached processes against stub `specstride` binaries
(`tests/test_mixture_of_loops.py:408-418`, `tests/e2e/exec-stub-bin/specstride`), so reap
what you spawn. CI adds `bash -n bin/onboard-skill` and an `ast.parse` sweep; there is no
linter, formatter or type check.

**MoL's rename guard is a CI gate on new text.** `tests/test_mixture_of_loops.py:540-596`
fails on any case-insensitive `wiggum` in any tracked file unless the exact line matches
a regex in its `alias_lines` map, and also fails on a stale allowlist entry. Only
`prompts/mixture-of-loops-skill-prompt.md` and `tests/test_mixture_of_loops.py` are
exempt. Every legacy spelling you add needs an allowlist line, and new tests that mention
the old name belong in `test_mixture_of_loops.py`.

**MoL's rendered bundle freezes the runtime.** `render_launcher.publish_bundle` copies
`runtime.py` and `contract_lib.py` into the content-addressed bundle (`render_launcher.py:80-84`)
and the launcher execs that copy. The bundle is addressed on the contract hash alone
(`:68-74`), so re-rendering an unchanged contract silently reuses the stale copy. To
verify a runtime change by hand, delete `.mixture-of-loops/generated/<id>/<hash>/` or
change the contract.

### No run is live right now

Check again before every edit to `orchestrator.sh`, `proposer.sh`, `specstride`,
`specstride-lib.sh` or `lib/critic.py`:

```bash
pgrep -af 'orchestrator\.sh|proposer\.sh|runtime\.py' | grep -v -e '/tmp/pytest-' -e "${TMPDIR:-/nonexistent}"
```

must print nothing. **The unfiltered pattern matches your own tests**:
`test_orchestrator_verification.py` runs `orchestrator.sh` end to end for about 16
minutes. A match rooted at a real project's `.specstride/` or `.wiggum/` tree is a live
operator run: wait for it to end, and note the wait in the report. Bash reads scripts
incrementally, so editing a running one corrupts the run, and a MoL contract may
hash-bind those files, so editing a deployed checkout mid-run refuses the next relaunch
with exit 23.

### Corrections to the two documents

The code moved after they were written.

- `learn.py` has `advise` **and** `apply` engines for all three allowlisted knobs
  (`APPLY_ENGINES` at `lib/learn.py:1049`; `apply_yield_poll_interval` at `:988`,
  `apply_inject_yield_hint` at `:1017`). The shipped doc's "still open: suggestion
  engines" is stale. What is missing is the *consumption* side, as the plan's table
  says: nothing reads the applied `yield_poll_interval`, and `proposer.sh:1031` still
  defaults `SPECSTRIDE_YIELD_POLL:=30`.
- `learn.py observe` has landed (`observation_for_phase` at `lib/learn.py:1178`,
  `_cmd_observe` at `:1325`, subparser at `:1413`), so the `phase_done` hook at
  `orchestrator.sh:1913-1923` is live under `SPECSTRIDE_LEARNING` ≠ `off`. It writes
  `{schema: "specstride.learn.observation/1", phase, generated_at, observation, inputs}`
  to `<feature-dir>/learning/phase-<N>.json`, where `observation` is exactly
  `summarize()["phases"][str(n)]` or `None`. Unset `SPECSTRIDE_LEARNING` is **not**
  `suggest`: the hook is skipped and `resolve_knob` returns the default
  (`learn.py:1128`), so design §5.5 invariant 5's "suggest is the default" is not what
  ships.
- The plan's `specstride:529` / `:537` are now `:535` (advise) and `:540` (apply).
  `add_default` at `specstride:526-530` injects `--default "${SPECSTRIDE_PROPOSER_TIMEOUT:-1800}"`
  for whichever `--knob` the operator passes, and `advise` hands that default to every
  engine at `lib/learn.py:950`. **The item-1 bug changes the applied value, not just the
  display.** Measured: with `job_duration_p50=3000` and 5 samples,
  `suggest_yield_poll_interval(stats, default=1800)` returns **300** while `default=30`
  returns **45**. At `learn.py:875-879` the ±50 % window becomes `[900, 2700]`, which does
  not intersect the `[10, 300]` hard bound, so the degenerate-window branch discards the
  step cap and the knob can jump 30 → 300 in one apply, violating §5.5 invariant 2. A
  second bug: `learn.py:951` renders `inject_yield_hint`'s `current` as `bool(1800)`,
  so a bare `--show --knob inject_yield_hint` reports the hint ON when it is off.
- The plan's two `append_budgeted_block` sites are swapped: `orchestrator.sh:1195` closes
  `build_accelerator_prompt` (1113-1196) and `:1522` closes `build_proposer_prompt`
  (1407-1523).
- **The plan's risk 3 is answered: the proposer can write `events.jsonl` and
  `learning/`.** Every backend runs with permissions disabled (`proposer.sh:450`
  `--dangerously-bypass-approvals-and-sandbox`; `:1427` `--dangerously-skip-permissions`)
  in the project directory, which contains `.specstride/features/<slug>/`
  (`orchestrator.sh:457`). The proposer also writes the *same* `events.jsonl` the
  orchestrator does by design: `orchestrator.sh:605` sets
  `SPECSTRIDE_EVENTS="$RUN_DIR/events.jsonl"`, exported at `:676`, and `proposer.sh`
  reuses it across 40 emit sites. Appends during a pass are normal; only mutation of
  pre-existing lines is tampering. Item 4's *tamper rule* is built on that.
- `.env` still leaks: `orchestrator.sh:179-186` re-asserts only variables the caller
  *exported*, so a `.env` in a deployed checkout can set `SPECSTRIDE_LEARNING=apply`
  unnoticed. Item 0 closes this by always passing an explicit value.
- `diagnostician_done` (`lib/critic.py:1745-1746`) carries `phase`, `attempt`, `bytes`
  plus `ts`/`time`/`event`; critic events carry **no `run_id`** (attribution rule at
  `learn.py:19-22`).

### MoL facts the plan relies on

All re-verified against `main` 1788911. `resolve_env` starts from `os.environ` and
returns early when a stage declares no `env` (`skills/mixture-of-loops/scripts/runtime.py:109-112`);
it is called for a stage action (`:325`) and for a `command_success` check (`:192`). No
file in MoL mentions `SPECSTRIDE_LEARNING`, `SPECSTRIDE_YIELD_POLL`, `learn.py`,
`applied.json` or `phaseTimeouts`. `supervise.py` has `mode`, `resolve`, `gate`, `launch`,
`watch`, `observe`, `auto` and `stop` and no `retro` (`supervise.py:150-223`). The
supervisor reads one field from Specstride's events, whichever the stage's
`recovery.reason` declares, conventionally `run_stop.reason` (`supervisor_lib.py:863-877`);
a stage without `recovery.reason` gives it nothing. `classify_relaunch`
(`supervisor_lib.py:897-988`) checks many things, but the contract digest is its **only**
test of whether anything material changed (`:961-969`), which is why a `learn --apply`
between relaunches passes unnoticed. `semantic_contract_digest` (`runtime.py:501-516`)
already covers all of `configuration`, so a new `configuration.learning` block is
digest-bound with no digest change. Specstride maps the whole `WIGGUM_*` prefix onto
`SPECSTRIDE_*` (`specstride-lib.sh:34`, mirrored in `lib/specstride_env.py`), so
`WIGGUM_LEARNING=apply` in the operator's shell really does turn the loop on.

### The real event corpus

**235 `events.jsonl` under `/root/*/.wiggum/features/*/runs/*/`** (28 features, 248
distinct phase keys). Only one `.specstride/` state tree exists on this host
(`/root/agentic-netops-srl`, 3 event files), and **no `applied.json` exists anywhere**,
so there is no experimental arm to measure. Sanity-check every metric against the
legacy corpus, quote the numbers and paths, and label them observational. Stream, or
skip, `/root/muse/.wiggum/features/001-deploy-glimmer-routing/runs/20260810-224004-4054437/events.jsonl`
(35 MB). Those directories are live operator state: **read only, never write, never
`git add`.**

Measured over that corpus, and load-bearing for item 4: a phase is *approved* in exactly
one run in 239 of 241 cases; runs per phase have median 1 (mean 1.68), with ≥6 runs in
only 8 of 248 keys; `work_sec_samples` has median 1, reaching ≥3 in 20 % of phases and
≥6 in 7 %. Within-phase sd of `log(pass cost_usd)` is 0.506 (median) / 0.654 (pooled).
Nine distinct model labels appear. `run_end` fires in 27 of 209 runs; `run_stop` in 125.
Corpus base rates: `grounding_gap` on 23.0 % of attempts, MALFORMED 8.9 % of verdicts,
first-attempt approval 44.3 %. 69 of 947 `agent_result` events are `missing_terminal`
with no cost, duration or token fields.

## Scope

**Deliverable:** plan items **0, 1, 2, 3, 4 and 7**, each landed as its own PR with its
own tests (item 4 as three stacked PRs; see below). That is the smallest set the plan
says honestly earns "self-improving".

**Then, only when all of those are merged green:** items **5 and 6**. If you run out of
budget or hit a blocker after item 4, stop there and report; a merged 0–4+7 is a
complete, useful result.

**Out of scope, do not start:** items 8–12. Also out of scope: ABAB arm alternation
(deferred inside item 4, with the reason), any rewrite of the bash orchestrator in
another language, any change to the critic's prompts, budgets or grounding search, and
any pre-existing shellcheck note.

## How a PR lands, and what to do while it does

Each item ends with a green local run, a PR whose description names the plan item and
what it changes, and a green CI run. Both repos squash-merge, so the **revert unit is the
merge SHA, not the commit**; report both.

After `gh pr create` (and, in MoL, the `automerge` label), poll
`gh pr view <n> --json state,mergedAt,mergeStateStatus` every 60 seconds for up to 45
minutes.

- **Merged:** `git switch main && git pull`, start the next item from there.
- **Checks red:** `gh run view <id> --log-failed`, fix on the same branch, push. After
  **three** consecutive red pushes on one item, convert the PR to draft
  (`gh pr ready --undo`), record the failure, and move to the next item that does not
  depend on it.
- **Still open after 45 minutes:** do not idle. Start the next item on a branch based on
  the unmerged one (`gh pr create --base <previous-branch>`), say so in the PR body, and
  re-check the parent before the final report.

**The one-PR-at-a-time rule is per repository.** Item 0 is in MoL and items 1–4 and 7 are
in Specstride; they are independent and you should have both in flight.

**Item 4 is three stacked PRs**, each green on its own, each based on the previous:
`4a` baseline recording, the `evaluate` subcommand, per-episode primaries and the four
labels; `4b` guardrails, auto-revert, quarantine and the run-end summary; `4c` the arm
field and the tamper rule. Do not attempt item 4 as one PR.

## Order of work

Items 0 and 1 first, in parallel. Then 2, 3, 7 in Specstride (small, in any order). Then
4a, 4b, 4c. Then 5, then 6. One repo per commit; commit messages start with the item
(`item 4b: …`).

### Item 0 — MoL: a stage declares its learning mode; nothing is inherited

Repo: `/root/mixture-of-loops`. Land this first; it closes an open provenance hole.

- In `runtime.resolve_env`, strip `SPECSTRIDE_LEARNING` and `WIGGUM_LEARNING` from the
  inherited environment **above the `if specification is None: return environment` early
  return** (`runtime.py:109-112`), so a stage with no `env` block is cleaned too and so
  `command_success` checks are covered along with stage actions. Not in the launcher
  template (a seven-line exec stub, `render_launcher.py:94-107`) and not in
  `display_action`, whose stage-kind branch runs only when color is off
  (`runtime.py:309-316`). Note in a code comment that `check_condition`'s `env_set` still
  reads `os.environ` directly (`:170-172`), so a contract can observe an inherited value
  but never pass it to a child.
- A `specstride` stage may set `SPECSTRIDE_LEARNING` to `off`, `suggest` or `apply` in
  its `env`; any other value is rejected in `contract_lib._validate_action`'s env loop
  (`contract_lib.py:106-118`), which also covers `resume` and `command_success` checks.
  `_normalize_action` has already rewritten `WIGGUM_LEARNING` to `SPECSTRIDE_LEARNING`
  before validation (`:300-307`), so validate the current spelling only. Reject
  `from_env` for this variable in the same place and say why in `references/contract.md`:
  a `from_env` reference re-opens exactly the hole this item closes.
- **Unset means `off`, and the runtime passes `off` explicitly** to every `specstride`
  stage whose `env` does not declare it. An explicit exported value is what Specstride's
  caller-env precedence (`orchestrator.sh:179-186`) protects against a `.env` in the
  deployed checkout; an absent variable is not.
- Validation rejects any contract source whose path is under a `learning/` directory of
  a `.specstride/` or `.wiggum/` state tree, with a message that says why
  (`learning/phase-<N>.json` is rewritten every approved phase; hashing it would refuse
  every relaunch with exit 23). Put it in the per-source loop in
  `contract_lib.validate_contract` (`:396-420`) beside the `escapes authorized_roots`
  check, so it raises `ContractError` (exit 20) at derivation and render time. Not in
  `check_source_hashes` (`:155-186`): that pass is skipped whenever `check_sources=False`,
  which is how `supervise.py` reads bundles. Match both state-dir spellings literally;
  source paths do not go through `runtime.path_for`'s swap.
- Document it in `references/derivation.md` and `references/contract.md`, and add the
  rename-guard allowlist lines. Tests go in `tests/test_mixture_of_loops.py`, which
  already has the harness: `FAKE_SPECSTRIDE` (`:408-418`) writes every
  `SPECSTRIDE_*`/`WIGGUM_*` variable it received into `invocation.json`, `_alias_fixture`
  (`:421-454`) builds the contract and a `PATH` that finds it, `_run_env` (`:456-458`)
  runs it under a chosen environment. Cover: an inherited `SPECSTRIDE_LEARNING=apply`
  and `WIGGUM_LEARNING=apply` never reach the child; a stage with no `env` gets
  `SPECSTRIDE_LEARNING=off`; a declared `suggest` survives; the three accepted values
  validate and a fourth is rejected with exit 20; `from_env` is rejected; a `learning/`
  source is rejected (the last three following
  `test_validation_rejects_shell_strings_literal_secrets_and_path_escape`, `:380-405`).

### Item 1 — Specstride: wire the unread knob, remove the dead one

- **`yield_poll_interval`.** In `orchestrator.sh`, resolve it once per phase beside the
  `resolve_proposer_timeout` call (`:1606`; the function is at `:1555`), through
  `learn.py resolve --knob yield_poll_interval --default "${SPECSTRIDE_YIELD_POLL:-30}"`,
  only under `SPECSTRIDE_LEARNING=apply`. An operator's explicit `SPECSTRIDE_YIELD_POLL`
  still wins: test **set-ness** (`[[ -n "${SPECSTRIDE_YIELD_POLL+x}" ]]`), not the value,
  because the orchestrator never assigns it (only `proposer.sh:1031` does `:=`). The
  launch at `:1682` is a bare `bash "$SCRIPT_DIR/proposer.sh" …` with no `env` prefix, so
  `export` the resolved value. Emit it with its source on `proposer_cap` or a sibling
  event. `proposer_cap` today carries `phase, attempt, role, seconds, source`
  (`orchestrator.sh:1658-1659`) plus `ts, time, event, run_id, task, backend, feature,
  trace_id` from `specstride_emit` (`specstride-lib.sh:103-127`); **every value is a JSON
  string**, and the event fires once per *attempt* while the value is resolved once per
  *phase*. `learn.py summarize` has no `proposer_cap` branch (`learn.py:432-521`): add
  one, deduping on `(run_id, phase)`. Mirror the route-1.5 tests at
  `lib/test_orchestrator_verification.py:989-1016`. Adding a flag to the resolve call
  requires updating the stand-in `learn.py`'s `resolve` subparser at `:1036-1038`.
- **`inject_yield_hint`: remove it.** The yield contract is already appended
  unconditionally as the **only** budgeted block, and last, in both prompts
  (`append_budgeted_block` at `orchestrator.sh:1350-1363` has exactly those two call
  sites). There is no ordering to change; the plan's "give it priority" option reduces to
  deleting the budget check at `:1357`, which removes the guard its own comment justifies
  (`:1341-1348`) and cannot be tested as a priority change. Narrowing the allowlist can
  never weaken the gate, whereas that edit touches the prompt the critic later judges.
  Remove it from `ADJUSTABLE_KNOBS` (`learn.py:722-726`), `SUGGESTION_ENGINES` (`:926-930`),
  `APPLY_ENGINES` (`:1049-1059`), `suggest_inject_yield_hint` (`:890`),
  `apply_inject_yield_hint` (`:1017`), `_unit_suffix` (`:1147`), the locked test
  (`test_learn.py:425-442`) and its dedicated tests, and the CLI's `--knob` choices. Say
  why in the commit message and in the allowlist comment.
- **`specstride learn`**: `add_default` must inject the default that matches the knob
  (`SPECSTRIDE_PROPOSER_TIMEOUT` else 1800 for `proposer_timeout`; `SPECSTRIDE_YIELD_POLL`
  else 30 for `yield_poll_interval`). Add a test that a bare
  `--show --knob yield_poll_interval` steps relative to 30, and a unit test pinning the
  measured case above (`default=30` → 45, never 300).

### Item 2 — Specstride: key learned state on the phase's shape

- Define a stable hash of the phase's *shape*: its number, its title, and its criteria
  **text**, excluding checkbox state and whitespace. For `speckit-tasks` and
  `openspec-change` the criteria *are* the task checkbox lines
  (`specstride_spec.py:180-183`, `:311-316`); what must not enter the hash is the
  `[ ]`/`[x]` marker. `Phase.criteria` (`:65-72`) already holds only the text after the
  box (`_CHECKBOX` group(1), `:87`, `:124-126`), so `(n, title, criteria)` is
  tick-invariant. `Phase.section` is **not**, and the `slice` subcommand prints the raw
  section (`_raw_slice`, `:817-822`), so hashing its output fails your own ticking test.
  Add a `shape` subcommand to the CLI's choices (`:825-924`) that prints the digest. Only
  `lib/specstride_spec.py` parses specs; do not re-parse `tasks.md` in `learn.py`.
- Plumbing: neither `resolve` (`learn.py:1404-1411`) nor `observe` (`:1413-1422`) knows
  the spec. The orchestrator has `$SPECS` and `$SPEC_FORMAT`; compute the shape there and
  pass `--phase-shape` to `resolve`, `observe`, and (later) `evaluate`; the `learn`
  dispatcher in `specstride` passes it to `advise` and `apply`. Update the stand-in
  `learn.py` subparsers at `lib/test_orchestrator_verification.py:1031-1045`.
- `effective_value` (`learn.py:804-814`) keys strictly on `(knob, int(phase))` with a
  last-one-wins replay that ignores `action` and `schema`. Grow its key by shape:
  `resolve` returns the default when the stored shape differs, `advise` excludes samples
  from a different shape, and an old entry with no shape is treated as non-matching after
  a one-time notice, never silently applied. Bump `LEARN_APPLIED_SCHEMA` and keep reading
  the old one. The schema-less seed entry at `test_orchestrator_verification.py:981-986`
  must be updated to carry a shape.
- Tests: edit a phase's criteria in a fixture and prove its old decision stops applying
  and its old samples stop counting; prove that ticking a checkbox does not change the
  key, for a speckit fixture (ticking is a no-op for `native`, `:773-774`).

### Item 3 — Specstride: record the diagnostician's case

Two changes, not one. In `lib/critic.py:1745-1746`, parse the first line of the reply
(`CASE: GROUNDING` / `CASE: REAL-GAP`; instruction at `:1660`) and add a `case` field
valued `grounding`, `real_gap` or `unknown`. **`emit` drops `None`-valued fields**, so
emit the literal string `"unknown"`. Nothing else in `critic.py` changes. Then in
`learn.py`, `summarize` has **no** `diagnostician_done` branch (`:432-521`): add one and a
per-phase counter in `_phases` (`:672-697`), attributing through the `_locate` rules since
the event carries no `run_id`. Bump the summary schema constant (`SCHEMA` at
`learn.py:146`, `specstride.learn.summary/1`) if the output shape changes: the plan's
*Interface* names it as MoL's read contract. Test the parse on both cases, a
malformed reply, and an empty reply.

### Item 7 — Specstride: strengthen the allowlist lock

Extend `lib/test_learn.py:425-442`. Do **not** assert on the bare string `learn.py` (it
appears in comment lines in `proposer.sh:1631`, `:1870`, `lib/error_breaker.py:34-35`,
`lib/finalize_invocation.py:22`, two test files, `orchestrator.sh:1546,1568,1906,1910`
and `specstride:511,516`) and not on `resolve_knob`/`effective_value` (they appear
nowhere outside `learn.py` and its test; the shell calls the CLI subcommand). Lock the
set of **invocations**: scan every tracked shell and Python file except `lib/learn.py`
and `lib/test_learn.py` for lines matching `python3\s+\S*learn\.py`, and assert the
`(file, subcommand)` set equals exactly the expected set. Today: `orchestrator.sh:
resolve` (`:1572`), `observe --help` (`:1914`), `observe` (`:1917`); `specstride: advise`
(`:535`), `apply` (`:540`), `revert` (`:544`), `off` (`:547`). Items 1, 2 and 4 add the
yield resolver, the shape-carrying calls and `evaluate` with its `--help` probe; grow the
expected set in the same PR that adds each. Separately assert that `lib/critic.py` and
`lib/verification_plan.py` match zero such lines. Name the test so that widening the set
is visibly a design decision, e.g.
`test_learn_py_is_invoked_only_from_the_proposer_launch_path_never_the_gate`.

### Item 4 — Specstride: `learn.py evaluate`, the step that closes the loop

Write a short design note **before** coding, as
`roadmap/research/self-improvement-loops/05-evaluate-design.md`, covering every decision
below and every one you add. Do not append to `02-wiggum-loop-design.md`: all of
`roadmap/research/` is a dated historical record that the repository's documentation
policy does not rewrite.

**Read the corpus numbers above before designing.** Exact McNemar and exact Wilcoxon
both have minimum two-sided `p = 2/2ⁿ`, so at 3 pairs the smallest attainable p is 0.25
and 6 discordant pairs are needed to reach 0.05 at best; only 3.2 % of phase keys ever
saw 6 runs. The minimum detectable cost effect at 3 passes per arm is ≈218 %; detecting
30 % needs 58–97 passes per arm. **Formal significance testing is not available at this
N. Do not implement McNemar or Wilcoxon.** McNemar on approved/not would also score the
metric the plan's *What not to do* forbids.

**4a — baseline, evaluate, labels.**

- `apply` records a **baseline** with the decision: the per-pass cost and wall-clock
  series and the guardrail counts (below) over the samples that produced the suggestion,
  the phase shape from item 2, and the backend label in effect. Nothing records a model
  today: the apply entry (`learn.py:974-978`) has no model field and its `run_id` is a
  synthetic `learn-<hex>` (`:760-761`); the Specstride run ids are in `source_runs`. The
  best available provenance is `run_start.backend` (→ `summarize`'s `runs[<id>]["backend"]`,
  `learn.py:435`, `:584`), a label like `prime:sol`. Record it, and say in the design
  note that baseline-reset granularity is backend label, not model version.
- **Primary metrics, per phase episode**: one `phase_start`→`phase_done` window inside
  one run, never per run. `cost_per_approved_phase` is a per-run figure (`learn.py:599`),
  undefined in 62 % of real runs. The per-phase map (`:672-697`) has `cost_usd` but no
  wall-clock: add both per-episode primaries to `_phases`.
- **Sample unit: the billed, non-futility pass**, clustered by phase episode.
  Futility-killed passes (`KILL_CLASS == "futility"`) are excluded from both arms
  identically. **Floor: 6 per arm**; below it the label is `insufficient` and nothing
  else happens.
- **Decision rule**, stdlib `math`/`statistics`, no p-values:
  `r = mean(log cost | applied) − mean(log cost | baseline)`; `s` = pooled within-phase
  sd of `log cost`, floored at 0.50; `DEFF = 1 + (m̄ − 1)·0.5` for `m̄` passes per
  episode; `MDE = 2.80·s·sqrt(1/n_a + 1/n_b)·sqrt(DEFF)`. `|r| < MDE` → `neutral`;
  `r ≤ −MDE` → `helped`; `r ≥ +MDE` → `regressed`. Same for wall-clock; `regressed` on
  either primary is `regressed`. Record `r`, `MDE`, `n_a`, `n_b`, `s`, `DEFF` in the
  entry and print the effect **with its MDE beside it**, so `neutral` is never read as
  "no effect". The comparison is valid only while backend label and phase shape match the
  baseline; a change in either resets the baseline and discards the applied-arm samples.
- `evaluate` runs at `phase_done` beside `observe` (`orchestrator.sh:1913-1923`), with the
  same discipline: only under `SPECSTRIDE_LEARNING` ≠ `off`, guarded by an
  `evaluate --help` probe, a failure logs and never costs an approved phase. It reads the
  whole `$FEATURE_DIR/runs` tree like `observe` does, **not** the single newest file the
  `specstride` dispatcher's `feature_events` returns (`specstride:164-170`); give the
  dispatcher a runs-tree path for `--show` too. It appends an `evaluate` entry to
  `applied.json` (never mutating the apply entry) and emits `knob_evaluated`.
- **Counterfactuals from logs.** A *shorter* timeout may be evaluated from recorded pass
  durations for **wall-clock only**, never for cost (the 69 `missing_terminal` results
  have no cost, so a truncated pass would score as free). A *longer* timeout may not be
  evaluated from logs at all. Encode both.

**4b — guardrails, auto-revert, quarantine, summary.**

- Guardrails veto even when the primary improved. Their sources are not uniform:
  - `grounding_gap` rate per attempt — available (`critic.py:2676` → `learn.py:379`,
    `:502-505`; no rate computed yet).
  - MALFORMED verdict rate — available (`verdict.result`; per-attempt fields at
    `learn.py:506-511`; no rate computed yet).
  - Declared verification still passing — available (`verification_passed`/`_failed` →
    `learn.py:491-495`). Any `verification_failed` in the applied arm where the baseline
    had none is an immediate breach at any n.
  - The diagnostician `case` split from item 3.
  - **Critic input size: no event carries it.** `critic_start` has only `phase, attempt,
    provider` (`critic.py:2663`). Do **not** add an event. Read it, read-only, from the
    verdict transcript `<feature-dir>/verdicts/phase<N>.attempt<A>.<ts>.txt`, which
    `_write_transcript` (`critic.py:2746-2754`) writes with a `═══ PROMPT ═══` section;
    give `summarize` a `--verdicts-dir` beside its existing `--verification-dir`.
  - **Human-arbitration rate is not measurable.** No event exists; the only trace is a
    log line at `orchestrator.sh:2042`. Use `run_stop reason ∈ {max_rejects,
    gate_oscillation, critic_config, proposer_no_progress}` plus the `gate_oscillation`
    event as the proxy, and name it as a proxy in the design note.
  - **False-MISSING rate is out of scope for item 4.** No event carries it, recomputing
    it later by path existence is biased downward as the tree grows, and parsing verdict
    prose into a label makes the critic's output an optimization input (plan risk 2/4).
    It stays where design §5.3 puts it, an offline release check on `critic.py`. Say so.
- **First-attempt approval rate is an alarm in both directions, never a reward and never
  a label term.** Thresholds are exact binomial tails, not percentage points (at 10
  verdicts the binomial SE is 16 pp): breach when a one-sided exact binomial p < 0.01
  against the baseline rate with ≥10 verdicts in the applied arm; below 10 the guardrail
  reports `unknown`, never `ok`. `math.comb` only.
- On a breach, revert through the existing `revert_run` path and emit
  `knob_auto_reverted` naming the guardrail. That is the only automatic write to a
  decision the loop may make. `revert_run` refuses anything but the currently active
  decision (`learn.py:1076-1082`): re-read `applied.json` first, and if a manual
  `apply`/`--off` overtook the evaluated decision, **skip**, emit `knob_evaluated
  action=skipped_superseded`, and never retry against the new top of stack. After
  `--off` an auto-revert is a silent no-op.
- **Quarantine.** `advise` reads `work_sec_p90`, which a cost regression does not move,
  so it would re-propose the reverted value forever. The auto-revert entry carries
  `auto: true`, `guardrail`, `evaluated_from`, `quarantined_value`, and the shape and
  backend it was quarantined under. `advise` still prints the suggestion, annotated.
  `apply` refuses a value within ±10 % of a quarantined one for the same `(knob, phase,
  shape, backend)` until 6 new non-futility samples have accrued since the revert; it
  exits non-zero naming the reverted run id; `--force` overrides and is recorded. A shape
  or backend change clears the quarantine.
- `specstride learn --show` prints the evaluation state of every active decision. The
  orchestrator prints a one-line summary on **both** `run_end` and `run_stop` (`run_end`
  fires in 13 % of real runs; there are two `run_end` sites, `orchestrator.sh:922` and
  `:2069`, and about ten `run_stop` exit paths, so hang it on the exit trap).

**4c — arm field and tamper rule.**

- **Record the arm; do not alternate.** `resolve` stamps `arm: baseline|applied` on
  `proposer_cap` (`baseline` before any decision exists for that `(knob, phase)`,
  `applied` after). Two things must change for that to be possible:
  `resolve_proposer_timeout` discards a learned value equal to the fallback
  (`orchestrator.sh:1573`: `&& "$learned" != "$fallback"`), so an applied decision that
  equals the default is invisible; and `resolve` must return the arm beside the value
  (print `<value>\t<arm>`) for the shell to carry it. Do **not** implement ABAB, and say
  why in the design note: only 8 of 248 real phase keys ever saw the ≥6 runs it needs; a
  resumed run gets a new run id and run dir, so alternation splits one phase episode
  across both arms; and a pinned `configuration.learning.effective` value (item 6) that
  silently reverts on alternate runs breaks MoL's promise that the contract says what
  runs. State plainly that before/after is confounded with time, spec and backend drift,
  that this is why the MDE floor is conservative, and that it is why only *reverting*, a
  move toward the default, is automatic.
- **Tamper rule: prefix immutability, per proposer invocation, only under
  `SPECSTRIDE_LEARNING` ≠ `off`.** Bracket the launch at `orchestrator.sh:1682` in the
  orchestrator, the parent process, not in `proposer.sh`, whose child is the untrusted
  party. Before: `L = size(events.jsonl)` and `sha256` of the first `L` bytes. After: a
  smaller file or a different prefix hash marks the attempt `events_tampered`, excludes
  the run from every evaluation, and emits the event. Bytes past `L` are legitimate
  appends (the stream tap at `proposer.sh:1497`, `iter_start`/`iter_done`, the finalizer
  at `:1875`, and `specstride_emit` all append concurrently), so any whole-file or
  line-count digest would flag every normal pass. Apply the same rule to
  `learning/applied.json` and `learning/phase-<N>.json`. A pass may yield and resume
  under a different orchestrator process (shipped step 3): re-baseline at every
  invocation, and a run with no pre-digest is excluded, not trusted. With learning off
  the hot loop is byte-identical to today.
- In the design note, say what the rule does **not** cover: fabricated *appended* events
  (a forged `agent_result` with a low `cost_usd` is byte-indistinguishable from a tap
  append, and no in-band HMAC helps against a same-uid writer that can read the
  orchestrator's environment); anything written outside the bracket, including detached
  long jobs that outlive a pass (`long_job_start`, `sweep_stray_progress`);
  `verdicts/*.txt`, `verification/*.json` and `GATE<n>-EVIDENCE.md`; and spec edits,
  which item 2's shape detects but does not prevent.

**Tests, across 4a–4c.** Fixtures with synthetic event streams for: helped, neutral
below MDE, regressed on primary, guardrail breach with primary improved (must
auto-revert), below the 6-per-arm floor (`insufficient`, no revert), futility exclusion
symmetric, backend change resets the baseline, shape change resets the baseline,
superseded decision skips the revert instead of raising, quarantine blocks a re-apply
and `--force` overrides, tampered stream excluded, **and an append-only stream not
excluded**. A unit test that the MDE formula returns ≥1.5 at n=3 per arm with s=0.506,
so nobody later mistakes the floor for a detectable effect. The `phase_done` hook tests
at `lib/test_orchestrator_verification.py:1093-1163` get an `evaluate` sibling of each
`observe` case, including "installed learn.py lacks the subcommand". **You must extend
the stand-in generator to do it**: `_LEARN_HEAD` (`:1031-1039`) registers only a
`resolve` subparser and `_LEARN_TAIL` (`:1047-1056`) routes **every** non-`resolve`
command into the observe branch, so an unmodified stub mis-handles `evaluate` rather
than failing cleanly. Add an `_LEARN_EVALUATE` fragment and a tail branch; keep
`_script_root` (`:1064-1085`) unchanged. Item 7's lock grows by the `evaluate` call site
and its probe in 4a.

### Item 5 — MoL: `supervise.py retro`

A read-only retrospective for a supervised pipeline. Register it like every other
targeted subcommand: `with_target(retro)` (`supervise.py:155-159`), `_locate(args)` for the
launch record and `_bundle_for(record)` for the contract (`:44-60`),
`_emitter(record["run_dir"])` for output (`:39-41`).

- **Reaching `learn.py`.** `specstride learn` dispatches only `--show|--apply|--revert|--off`
  (`specstride:510-552`); `summarize` and `evaluate` exist only as
  `python3 <checkout>/lib/learn.py <cmd>`, and MoL knows no Specstride checkout (a stage's
  `argv[0]` is just `specstride`, which is not even on `PATH` on this host). **Land a
  small Specstride PR first** that adds `specstride learn --summarize` and `--evaluate`
  passthroughs (both read-only; `evaluate` gains `--dry-run`, which prints its verdict
  and writes no decision), then call `argv[0] learn --summarize` with no path guessing.
  Probe with `--help` before calling; on any failure, including an older Specstride
  without those flags, report `unavailable` and exit 0.
- **Finding the feature dir.** No contract field names Specstride's state dir. Derive it,
  in order, from the stage's `recovery.reason.jsonl` parent (`references/contract.md:70`,
  the field `latest_stop_reason` reads at `supervisor_lib.py:863-877`), then
  `evidence[0]`, then a `postconditions[].path`, matching
  `(\.specstride|\.wiggum)/features/<slug>`. Resolve with `runtime.path_for`'s state-dir
  rule (`runtime.py:141-149`), not bare `resolve_path`. A stage with no `recovery.reason`
  is normal, so the absence of all three is `unavailable`, exit 0.
- **Where it writes.** `runs/<id>` is keyed on the **pipeline id** (`supervisor_lib.py:328`,
  `runtime.py:547`) and reused across every relaunch and re-derivation, so a single
  `retrospective.json` would be overwritten each time. Write
  `runs/<id>/retrospectives/<contract-digest>.json` through `runtime.atomic_json`, with
  the digest recorded inside as well, and document it in `references/contract.md` as a
  fourth harness-owned file beside `harness-run.json`, `harness-launch.log` and
  `harness-report.log`.
- It may print a suggested `specstride learn --revert <run-id>`; it never runs `apply` or
  `revert`, and it writes nothing outside `run_dir`. Tests follow
  `tests/test_supervision.py:89-121`'s `Fixture` with a hand-built events tree and a stub
  `specstride`: normal path, stub lacking `--evaluate`, stage without `recovery.reason`,
  and an assertion that nothing outside `run_dir` was touched. CI has no Specstride
  checkout, so every MoL test uses the stub.

### Item 6 — both: bind learned decisions into the contract

Follow the plan's *Interface* section with these corrections.

- **The block needs a fifth field**: `mode`, `decisions_through`, `decisions_sha256`,
  `effective`, plus `source_path`, the `applied.json` path relative to `repository.root`.
  Validation cannot re-hash a prefix it cannot locate.
- **`configuration` is open, not fixed.** The schema declares only `auto` and sets no
  `additionalProperties: false` there (`assets/launch-contract.schema.json:56-69`);
  `contract_lib` validates only that it is an object plus `auto` (`:560-563`,
  `_validate_auto` `:241-265`); `runtime.py` reads `configuration` nowhere. Add
  `_validate_learning` beside `_validate_auto`. The JSON Schema file is documentation
  kept in step by hand; nothing validates against it.
- **`bootstrap_contract.py` cannot fill it as it stands.** It is Tier-1 only: it reads
  Spec Kit artifacts, emits a draft with zero stages and one open blocker (`:314-324`),
  and never touches `.specstride/`. Give it `--applied-file` (or a slug-derived default
  from `feature.name`, `:250-266`) and emit the block in the `configuration` dict at
  `:306-312`.
- **Put the re-hash under the `check_sources` guard** (`contract_lib.py:591-594`), not in
  the unconditional error pass: `supervise.py` reads bundles with `check_sources=False`
  where the file may be absent. Never register `applied.json` in `sources[]`; item 0
  forbids it.
- **Who declares `SPECSTRIDE_LEARNING`: derivation-time expansion.** The deriving agent
  writes `SPECSTRIDE_LEARNING` and `SPECSTRIDE_LEARNING_THROUGH=<run_id>` literally into
  each `specstride` stage's `env`, and `configuration.learning` is the provenance record
  validation cross-checks against those literals. That keeps item 0's "the stage's `env`
  says what runs" intact and needs no runtime change. Do not have the runtime inject from
  `configuration.learning`: that requires lifting `display_action`'s stage-kind branch out
  of its `if not color:` guard (`runtime.py:305-317`), threading the contract into
  `run_stage`, and a precedence rule against the stage's own `env`.
- **One gate change.** When the bound prefix no longer hashes, `read_bundle` raises before
  `classify_relaunch` runs and `_bundle_for` turns that into `bundle = None`
  (`supervise.py:50-52`), which classifies as `unknown-stage`. Surface a named
  `learning-decisions-changed` refusal instead.
- **Specstride half:** `resolve` ignores decisions newer than `SPECSTRIDE_LEARNING_THROUGH`.
  An item-4 auto-revert after derivation still applies, since it only moves toward the
  default; document that a bound `effective` value is therefore an upper bound on what
  runs, not a promise, and that the arm field records what actually ran.
- Ordering: the Specstride half lands first so the flag's contract is fixed before MoL
  pins it with a stub. Schema, both reference docs, tests on both sides, rename-guard
  allowlist lines for any legacy spelling added.

## Working rules

- One repo per commit. `/root/specstride` and `/root/mixture-of-loops` have distinct CI.
- Small, atomic commits, each leaving the tree green, each message starting with the
  item. Never bundle an unrelated fix into an item's commit.
- If a defect outside scope **blocks an item**, fix it in its own commit and PR and say
  which item it unblocked. Every other defect is a line in the report, not a PR: each
  extra PR costs a full CI cycle, and pre-existing shellcheck notes, unused variables and
  style findings are explicitly not yours.
- Do not edit the plan to match the code as you go. Collect what you find wrong and add
  one dated *Corrections* section to `roadmap/self-improving-loop-plan.md` at the end of
  the run, in one docs-only PR, starting from the list below.
- Bash stays bash; Python stays stdlib-only, 3.10-compatible in MoL. `learn.py` is the
  single owner of learning logic; shell calls it and reads one value or one exit code.
- Report measured numbers, not estimates, from the corpus named above.

## Corrections to the plan

Carry these into the plan's *Corrections* section at the end of the run, with your own:

- "Expect to detect only changes of roughly 25–30 % in cost per approved phase" is wrong
  by more than an order of magnitude: at 3 passes per arm the MDE is ≈218 %, and 30 %
  needs 58–97 passes per arm (within-phase sd of log cost 0.506–0.654).
- McNemar on approved/not scores approval rate, which *What not to do* forbids.
- ABAB alternation is unreachable at this N and contradicts item 6; version 1 records
  the arm only.
- Critic input size and human-arbitration rate have no event; false-MISSING has no event
  and is dropped from item 4.
- `inject_yield_hint`'s "give it priority" option is not implementable; the knob is
  removed.
- Item 7 as specified cannot be written (see the item); the lock is on invocation lines.
- The `append_budgeted_block` sites at plan line 31 are swapped.
- Risk 3 is answered: the proposer can write both files.

## Hard rules

- Never `git push --force`, never push to `main`, never merge locally, never `gh pr merge`
  in any form. Mergify merges.
- Never write under `.specstride/`, `.wiggum/`, `.ralph/` or any `runs/` directory of any
  repository. They are live state.
- Never touch `lib/critic.py` except for item 3's one field; never touch
  `lib/verification_plan.py`.
- No pip, no new dependencies.
- Do not start a paid model backend and do not start a `specstride run`.

## Final report

Write it to `roadmap/self-improving-loop-report.md` in Specstride and **commit it through
its own PR**, as `roadmap/rename-specstride-report.md` was. Print it as well. It must
contain:

1. Per item: status (merged / PR open / draft / not started / blocked and why), every
   decision you made that this prompt left open, and test files added or changed.
2. **An evidence table, one row per PR:** item | PR URL | CI run URL and conclusion
   (`gh run view <id> --json conclusion,url`) | `shellcheck -S error` result for every
   shell file touched | the verbatim pytest or unittest summary line, with the
   pre-change count beside it | merge SHA | `gh api repos/mairp/<repo>/commits/main --jq .sha`
   after the merge. A row with no CI run URL does not count as green.
3. The rollback target per item (the merge SHA to `git revert`) and any PR left open,
   draft or stacked, with what it is waiting on.
4. Item 4's design note location, the MDE it computes for a typical phase from the
   corpus (observational, with N), and the guardrail thresholds with justification.
5. What the tamper rule does not cover.
6. The corrections PR URL, and anything you found wrong beyond the list above, with
   `file:line` evidence.
7. Anything left for items 5, 6 and beyond, and the first concrete step for each.

Print the report path, every PR URL and every merge SHA as the last lines of output,
then print the single word `DONE` on its own line.
