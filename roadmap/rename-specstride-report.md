# Rename report: Wiggum is now Specstride

Date: 2026-09-18

Status: **Phases 0–4 done and green. Phase 5 stopped at PR creation**:
the GitHub token cannot create pull requests (the exact error is below). Both
branches are pushed. Nothing was merged, the repository was not renamed, and
neither `main` was changed on GitHub.

## Outcome at a glance

| Item | State |
|---|---|
| Wiggum rename (code, env, state dir, docs, shims, tests) | committed on `rename/specstride`, pushed |
| mixture-of-loops (stage-kind alias, docs, tests) | committed on `rename/specstride`, pushed |
| mixture-of-loops `main` docs commit (`8052c32`) | committed locally on `main`; on GitHub only through the pushed branch |
| Pull requests | **not created** (token refused) |
| Merge, `gh repo rename specstride`, description/topics, remote URL | **not done** (blocked on the PRs) |
| `/root/.bashrc` | migrated (Specstride block, `wiggum()` kept, `WIGGUM_HOME` kept) |

## The Phase 5 error

```
$ gh pr create --base main --head rename/specstride --title "Rename Wiggum to Specstride" --body-file -
pull request create failed: GraphQL: Resource not accessible by personal access token (createPullRequest)
```

`gh auth status` shows a fine-grained token (`github_pat_…`). The repository
API reports `admin: true, push: true` for this account, so pushing works. The
token itself lacks the **Pull requests: write** permission. A repository
rename also needs **Administration: write**, which this token probably lacks
as well (untested).

To finish, grant the token those permissions (or use `gh auth login` with a
classic token), then run the remaining Phase 5 steps:

```bash
# Wiggum
cd /root/wiggum
gh pr create --base main --head rename/specstride --title "Rename Wiggum to Specstride" --fill
gh pr merge --merge --delete-branch && git checkout main && git pull
gh repo rename specstride --yes
git remote set-url origin https://github.com/mairp/specstride.git && git fetch origin && git status -sb
gh repo edit mairp/specstride --description "From specs to tested code." \
  --add-topic ralph-loop --add-topic spec-driven-development \
  --add-topic autonomous-coding --add-topic formerly-wiggum

# mixture-of-loops (local main carries the docs commit 8052c32)
cd /root/mixture-of-loops
git push origin main
gh pr create --base main --head rename/specstride --title "Target Specstride (formerly Wiggum)" --fill
gh pr merge --merge --delete-branch && git checkout main && git pull
```

Until then the branches can be reviewed here:
- https://github.com/mairp/wiggum/compare/main...rename/specstride
- https://github.com/mairp/mixture-of-loops/compare/main...rename/specstride

## Commits

Wiggum (`/root/wiggum`, branch `rename/specstride`, pushed to `origin`):

| SHA | Subject |
|---|---|
| `226903d4936dad6a6189f3f9794f57db14d8047c` | rename: Wiggum is now Specstride (CLI, env, state dir, docs) |
| `c61b45dbde12444b5c1a811c629bdac5fc56a8f2` | compat: keep wiggum and wiggum-lib.sh as deprecated shims |
| `3cb6fb3dbe8176e71e81376d8fe8be092f9d379c` | tests: cover the Specstride rename and the Wiggum compatibility layer |
| (this file) | docs: record the Specstride rename |

mixture-of-loops (`/root/mixture-of-loops`):

| SHA | Branch | Subject |
|---|---|---|
| `8052c326865424fefe9c86945ac15e960c681bca` | `main` (local; also inside the pushed branch) | docs: add the how-it-works sequence diagram |
| `07fb48157dcbfac8c7f9a2dc1657fa134a982fe1` | `rename/specstride` (pushed) | rename: target Specstride (formerly Wiggum); accept the old stage kind |

PR URLs: none (creation refused). New repository URL: none yet (`mairp/wiggum` was not renamed).

## Test counts

| Repo | Command | Baseline (`main`) | Final |
|---|---|---|---|
| Wiggum/Specstride | `python3 -m pytest lib/ -q -p no:cacheprovider` | 596 passed, 0 failed, 0 skipped (31m35s, one process) | 633 passed, 0 failed, 0 skipped |
| mixture-of-loops | `python3 -m pytest tests/ -q` | 6 passed, 2 subtests | 10 passed, 2 subtests |

Wiggum ran after every phase: Phase 1 had 596 passed and Phase 2 had 633
passed. To stay well inside the 40-minute limit, the same `pytest` command
was split into six parallel processes: the five slow files each ran alone
and everything else ran together. The slowest,
`test_orchestrator_verification.py`, takes about 16 minutes. The per-file
counts sum to the totals above. The first rename commit (no shims) was also
checked on its own in a worktree: all 478 fast tests passed.

The 37 new Wiggum tests:
- `test_specstride_cli.py` (+2): the `wiggum` shim gives the same stdout and exit code as `specstride`, and stderr gains exactly the deprecation line.
- `test_specstride_env_compat.py` (18): 12 variables (`HOME`, `PROPOSER_TIMEOUT`, `SPEC_FORMAT`, `LIVE_DETAIL`, `CRITIC`, `MAX_REJECTS`, `MAX_ITER`, `LOKI_URL`, `OTEL_URL`, `LEARNING`, `AGENT_STREAM`, `FEATURE`). Bash and python each check: new name wins, old name alone is honored, neither gives the default, one notice per process. Also covers the orchestrator's `.env` sequence and each env-reading python module.
- `test_specstride_state_dir.py` (8): runs the orchestrator end to end with a fake Prime agent, for a fresh workdir, a legacy-only workdir and a workdir with both. The CLI read path and the bash and python helpers are also covered.
- `test_rename_guard.py` (8): the allowlist policy, including stale-entry detection.
- `test_specstride_spec.py` (+1): `specstride_spec` imports, `wiggum_spec` does not.

On the pre-rename tree, each new test fails: the `specstride` executable,
`specstride-lib.sh`, `specstride_env` and `specstride_spec` do not exist,
no run creates `.specstride/`, and the guard finds about 3,000 hits.

## Smoke test (Phase 3)

The Spec Kit example was copied to `$T/specs/001-greeting-cli/tasks.md` in
`T=/tmp/claude-0/smoke.hXGS6a`. The commands ran in a login shell (`bash -lc`)
with a clean environment, so the migrated `.bashrc` was in effect.

```
$ specstride phases -w $T -s specs/001-greeting-cli/tasks.md
/tmp/claude-0/smoke.hXGS6a/specs/001-greeting-cli/tasks.md — 3 phase(s):
  Phase 1   Setup (Shared Infrastructure)            [pending]
  Phase 2   User Story 1 - Greet a named user (Prior [pending]
  Phase 3   User Story 2 - Default greeting (Priorit [pending]
exit 0
```

`specstride status` needs a state dir, and so did the pre-rename tool: on the
bare workdir it prints `no .specstride/ state dir in …` and exits 1. Without
starting a run, the state that a run's launch writes was seeded:
`.specstride/features/001-greeting-cli/` plus `last-run.conf` (FEATURE, SPECS).

```
$ specstride status -w $T
specstride status — /tmp/claude-0/smoke.hXGS6a  (feature: 001-greeting-cli)
  state: no run recorded yet

  PHASE  TITLE                        EVIDENCE APPROVED FEEDBACK
  -----  ---------------------------- -------- -------- --------
  1      Setup (Shared Infrastructure ✗      ✗      ✗
  2      User Story 1 - Greet a named ✗      ✗      ✗
  3      User Story 2 - Default greet ✗      ✗      ✗

  current phase: 1
exit 0
```

Through the old name, calling the tracked `$SPECSTRIDE_HOME/wiggum` shim
(the interactive `wiggum()` shell function forwards silently):

```
== phases: specstride exit=0, wiggum-shim exit=0
   stdout identical
   specstride stderr:
   wiggum stderr:     wiggum: this command was renamed to specstride; the alias will be removed in a future release
== status: specstride exit=0, wiggum-shim exit=0
   stdout identical
   specstride stderr:
   wiggum stderr:     wiggum: this command was renamed to specstride; the alias will be removed in a future release
```

A legacy workdir (only `.wiggum/`), run with `WIGGUM_LIVE_DETAIL=full`:

```
specstride: deprecated WIGGUM_* variables read as SPECSTRIDE_*: WIGGUM_LIVE_DETAIL (rename them)
specstride: using legacy state dir /tmp/claude-0/smoke.hXGS6a-legacy/.wiggum (no .specstride/ there; it is not moved)
specstride status — /tmp/claude-0/smoke.hXGS6a-legacy  (feature: 001-greeting-cli)
```

Afterwards the workdir still held only `.wiggum/` and `specs/`. `.bashrc` check:
`bash -lc 'type specstride; specstride help | head -3'` reports a function and
prints the Specstride usage header.

## mixture-of-loops launcher against a real feature set (Phase 4)

The launch contract and its 13 sources for `002-extproc-data-path` were
copied out of `/root/semantic-router-sovereign-003` (read-only) into
`/tmp/claude-0/mol-smoke.oe3UqT`. `repository.root` and `authorized_roots`
were rebased onto that copy. The copied contract was correctly rejected as
stale (exit 23): five spec files changed after it was generated. It was
re-bound to the current sources with the bootstrapper's own hash functions,
then validated, rendered and dry-run:

```
$ validate_contract.py …/launch-contract.json
warning: stages[0] uses the deprecated 'wiggum' command or WIGGUM_* env names; read as 'specstride'
warning: stages[3].kind 'wiggum' is deprecated; read as 'specstride' (Specstride was formerly Wiggum)
warning: stages[3] uses the deprecated 'wiggum' command or WIGGUM_* env names; read as 'specstride'
warning: configuration.wiggum_live is deprecated; read as specstride_live
warning: 117 coverage timing value(s) use the deprecated 'wiggum-phase:' prefix; read as 'specstride-phase:'
valid validated launch contract: 002-extproc-data-path          (exit 0)

$ ./run-002-extproc-data-path.sh --dry-run --no-color            (exit 0, stderr empty)
[DRY-RUN] contract 002-extproc-data-path is source-current; no actions or writes will occur
[PLAN] preflight-topology: env[PATH,PYTHONPATH] "/root/wiggum/specstride" "phases" …
[CHECK] stack-up uses existing prerequisite; --implement would run setup
[CHECK] sidecars-up uses existing prerequisite; --implement would run setup
[PLAN] run-feature: env[…,SPECSTRIDE_AGENT_STREAM,…,SPECSTRIDE_LIVE_DETAIL,…] "/root/wiggum/specstride" "run" "-w" … "--live"
[SKIP] live-restore (enable with --smoke)
[SKIP] live-suite (enable with --smoke)
[DIGEST] state=dry-run exit=0 last-stage=none evidence=none
```

`bootstrap_contract.py` on the same copy emits `specstride_live` and
`specstride-phase:N`, and the draft validates.

## Decisions this prompt left open

- **The third untracked file.** `roadmap/prompts/rename-wiggum-to-specstride.md` (this task's prompt) was untracked at baseline in addition to the two expected files. It is not a modification, so it was treated as benign. It stayed untracked and was not committed.
- **Historical allowlist.** All of `roadmap/` (dated notes, research, prompts, the naming decision) is historical, plus `INCIDENT-*.md`, `crash_incident`, `reversed/`, `specs/` (the completed 001 feature's Spec Kit record and run scripts), `.ralph/` (tracked live loop state, never edited), `.codex-phase13-spec.patch`, the one-off run scripts `resume-001.sh`, `swap-002-proposer-to-gpt5.sh`, `merge-main-into-002.sh` and `untrack-specs-wiggum.sh` (they target live `.wiggum/` state and keep working through the compatibility layer), and `lib/fixtures/` (recorded agent transcripts). `test_rename_guard.py` encodes exactly this list.
- **Compatibility sites allowed by the guard.** The two shims. The mapping and fallback constants in `specstride-lib.sh` and `lib/specstride_env.py`. The legacy `.gitignore` lines. The README "formerly Wiggum" lead and "Migrating from Wiggum" section. The compat test files. Two phrases are allowed everywhere: "Ralph Wiggum" (the character the loop is named after; the startup banner keeps his portrait) and the cited file name `02-wiggum-loop-design.md`.
- **Telemetry compose project name kept (`name: wiggum-telemetry`).** Docker prefixes named volumes with it, so renaming it would orphan existing Loki, Grafana and Prometheus data. The port overrides use a nested default, `${SPECSTRIDE_X_PORT:-${WIGGUM_X_PORT:-N}}`.
- **Env mapping is generic, not a table.** Every `WIGGUM_<X>` shell variable maps to `SPECSTRIDE_<X>`, so all ~40 variables, and any added later, are covered. It runs when `specstride-lib.sh` is sourced (a documented exception to the lib's "no side effects at source time"). It runs again after the orchestrator sources `.env`, so a caller's export still beats `.env`. Python modules that read env call `specstride_env.apply()` at import.
- **State-dir notice once per process tree.** Callers resolve the state dir with `specstride_resolve_state_dir`, not in `$(…)`, so the exported `SPECSTRIDE_STATE_NOTICE_SHOWN` flag reaches child scripts and python. A test checks that the legacy run's own logs do not repeat the notice. Progress scans and the critic's config walk prune both directory names.
- **`wiggum-lib.sh` shim** also defines `wiggum_<name>` wrappers for every `specstride_<name>` function, so old callers of `wiggum_emit` and similar keep working.
- **Two commits for Phase 1.** A pure-rename commit, then a commit that re-adds `wiggum` and `wiggum-lib.sh` as shims, so git records `wiggum => specstride` as a rename and `--follow` works. The phase commit message is on the first.
- **`untrack-specs-wiggum.sh` was not renamed.** The naming table does not list it, and it is a one-off script for the ainetops repo's live `.wiggum/` tree.
- **`specstride-digest.sh`** now resolves the ainetops state dir through the fallback (today that is still `.wiggum/`). Its default output path moved to `/tmp/specstride-digest.md`.
- **Smaller string renames.** Banner title: "Specstride · The Autonomous Ralph Loop". OTEL instrumentation scope: `specstride.ralph`. Provider-terminal contract id: `specstride-provider-terminal/v1`. dsh plugin install lock file: `.specstride-plugin-install.lock`. Nothing in these repos queries any of them. `job=ralph` and `service.name=ralph` are unchanged.
- **`.bashrc` `wiggum()`** is `specstride "$@"` with no deprecation line, because it is the interactive shell's own alias. The tracked `wiggum` executable is the one that warns.
- **mixture-of-loops normalization** goes a little beyond the legacy stage kind. In any stage, a `wiggum` command (argv, `command_available`, `command_success`) is rewritten to `specstride`, keeping its directory, so `/root/wiggum/wiggum` becomes `/root/wiggum/specstride`, and `WIGGUM_*` action env keys become `SPECSTRIDE_*`. Without this, the real 002 contract's `command` preflight stage would have kept calling the shim. The renderer publishes the normalized contract. The runtime resolves `.specstride/` and `.wiggum/` paths with Specstride's fallback rule, so a legacy postcondition like `.wiggum/features/…/PROGRESS.md` holds on a fresh workdir. `validate_contract()` now returns the list of warnings; it used to return `None`.
- **This report was committed on `rename/specstride`, not `main`.** Phase 5 stopped before the merge, so pushing it to `main` would have described a rename that is not there. It rides along with the PR.
- **Commit trailers** use `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`, as the task asked. The work was actually done by Claude Opus 5.

## Follow-ups

- **Finish Phase 5** with the commands above, once the token can create PRs (and rename the repository).
- **Out-of-scope consumers** keep working through the compatibility layer but should be migrated:
  - `/root/workflow_orchestration` (31 files): uses `WIGGUM_DIR` and `WIGGUM_OTEL_*` names and the `wiggum` command. The env names are mapped automatically; rename them when convenient.
  - `/root/fleet-config` (5 files): points at `/root/wiggum/.env` and sets `WIGGUM_*` keys such as `WIGGUM_COMPASS_KEY` and `WIGGUM_VISION_KEY`. `.env` files go through the same mapping. The path needs the checkout symlink if the checkout moves.
  - `/root/semantic-router-sovereign-003/.mixture-of-loops/…`: the generated launch contracts use `kind: "wiggum"` and `/root/wiggum/wiggum`. Their existing launchers bundle the old runtime and still work through the shim. Re-rendering with the new skill normalizes them and prints warnings. The 002 contract is also stale against its current sources (seen during the Phase 4 smoke test).
  - Workdirs with an existing `.wiggum/` (for example `/root/wiggum/.wiggum` itself) keep using it in place. Once a workdir is quiet, `mv .wiggum .specstride` migrates it.
- **Move the checkout** from `/root/wiggum` to `/root/specstride`: update `SPECSTRIDE_HOME` and `WIGGUM_HOME` in `/root/.bashrc`, then `ln -s /root/specstride /root/wiggum` for older scripts. The hardcoded `/root/wiggum/…` paths in the historical run scripts and in generated contracts need that symlink.
- **Remove the shims** in a future release: `wiggum`, `wiggum-lib.sh`, the `WIGGUM_*` mapping, the `.wiggum/` fallback, the mixture-of-loops `wiggum` stage-kind alias and `WIGGUM_LIVE`. The rename guard's allowlist then shrinks to the historical records.
