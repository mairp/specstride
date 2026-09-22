# Prompt — `specstride --reverse <folder>`: reverse-engineer a codebase into Spec Kit artifacts

You are an autonomous coding agent running unattended. Nobody will answer questions.
Make routine decisions yourself, record them in the final report, and stop only when a
step below is impossible (say why and what remains). Written 2026-09-22 against
Specstride `main` bf1e182 and Lisa `main` 295f92e, and revised the same day after
verifying every file, line range, flag and format it names against both checkouts. The
revision found two defects in the first draft that would have broken the read-only
policy on the very first live run; they are fixed under *Two launch-time hazards*. Where
this prompt and any other document disagree, this prompt wins.

## What you are building

A new front-door mode:

```text
specstride --reverse <SRC> [--out DIR] [--name SLUG] [--tasks done|open]
                           [--proposer B] [--critic B] [--dry-run] [launch flags…]
specstride reverse   <SRC> …                  # same thing, verb spelling
specstride reverse --lint <FEATURE_DIR> [--src SRC] [--upto N] [--json]   # linter only, no LLM
```

It inspects the folder `<SRC>` read-only and produces a **complete, Spec Kit-compliant
feature directory** that describes the system *as it is today*:

```text
<SRC>/specs/NNN-as-is-<slug>/          # default; --out overrides
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/*.md
├── tasks.md
├── checklists/requirements.md
└── memory/constitution.md             # ONLY when <SRC>/.specify/memory/constitution.md is absent
```

"Compliant" has a precise meaning here: the directory passes the deterministic linter you
build in step 3, **and** `python3 lib/specstride_spec.py validate --specs <out>/tasks.md
--format speckit-tasks` exits 0, **and** the output reads as if Spec Kit's own
`/speckit.specify → /speckit.plan → /speckit.tasks` produced it for this system.

The design has three parts. The first two are the things Lisa's version is missing.

1. **A deterministic inventory** (Python, stdlib only, no LLM) that walks `<SRC>` and
   records its facts.
2. **A driver spec**, generated from the inventory, that runs the ordinary Specstride
   proposer/critic loop to write the artifacts one gated phase at a time.
3. **A deterministic Spec Kit linter** that serves as each phase's verification command.
   An LLM critic that "feels" compliance is not enough to approve a phase.

## Read these first, in this order

1. `specstride` lines 1–70: the help comment block (lines 2–43, which `usage()` at L53
   prints with `sed -n '2,43p'`) and the pre-dispatch `case` at L64–68, whose `-*)` arm
   forwards any leading flag to `orchestrator.sh` as a launch. Then the `resume)` arm
   (L460–510) and the `learn)` arm (L512 to the end of that arm). `learn` is the pattern
   for a thin bash verb that hands off to a `lib/*.py` CLI; `resume` is what you extend
   under *Two launch-time hazards*.
2. `orchestrator.sh`: config precedence and `.env` sourcing (L164–217), the flag parser
   (L229–245), `resolve_spec` and the format pin (L340–432), state-dir and feature-slug
   resolution and the `testautomation/` defaults (L433–475), `write_last_run_conf`
   (L624–665), the verification preflight (L760–795), the `phaseTimeouts` resolution
   comment (L1545–1560), and `maybe_git_checkpoint` (L933–947).
3. `lib/specstride_spec.py`. Read all of it. It is the single source of truth for spec
   grammar (memory: *Spec parsing single source of truth*). Note especially the regexes
   at L85–89 and L189–200, `_validate_native` (L135), `_validate_speckit` (L287),
   `detect_format` (L397), `find_specify_root` (L504), `feature_slug` (L524),
   `speckit_context` (L611), and the `main` CLI (L853). Exit codes: 0 ok, 3 invalid.
4. `lib/verification_plan.py`: `_declared_entry_command` and `load_declared_commands`
   (L235–402), `discover_project` (L405–545), and `create_plan` (L716–1020), especially
   the declared/discovered merge at L840–930. `lib/test_verification_plan.py` is where
   the one edit this prompt allows to that module gets its tests.
5. `lib/learn.py` lines 1–80, for the CLI conventions of a `lib/*.py` component.
6. `reversed/`, a full Spec Kit reverse of Specstride (then called Wiggum) produced by a
   hand-written six-phase Wiggum run. Use it as the **quality bar and the golden example**
   for your fixtures: its inline evidence style, `contracts/` split, and `research.md`
   decision/rationale/alternatives format. Its names are pre-rename (`wiggum_spec.py`,
   `WIGGUM_*`), so do not copy them. It also carries a `README.md` and a `proofs/`
   directory that are **not** part of the Spec Kit layout; your `good/` fixture must not.
7. Spec Kit templates: `.specify/templates/{spec,plan,tasks,checklist,constitution}-template.md`
   and the command prompts `/root/spec-kit/templates/commands/{specify,plan,tasks,analyze}.md`.
   The commands define the rules the templates only imply: ID formats (`commands/tasks.md`
   L149–180), `[P]` and `[USn]` semantics, the phase order Setup → Foundational → one
   phase per user story in priority order → Polish (L206–213), the max-3
   `[NEEDS CLARIFICATION]` rule (`commands/specify.md` L128, L299), the requirements
   checklist items (`commands/specify.md` L146–180), and the analysis categories and
   severities (`commands/analyze.md` L106–160).
8. The Lisa feature, **for what to keep and what to avoid**:
   - `/root/lisa/packages/dashboard/src/chat.ts` L96–143, 275–340, 444–506. This is the
     only working path: intent parsing (it refuses black-box and runtime-assisted modes),
     destination defaulting (`nextDefaultDestination`, `slugify`), request validation
     (`validateReverseEngineeringRequest`: not the project root, not inside `.git/` or
     the state dir), and `createReverseEngineeringSpec`, the generated four-phase driver
     spec. Port its policy header and phase text; they are good. Drop everything about
     the v2 bundle, `as-is/`, `evidence/index.json`, `unknowns.json` and
     `transform-report.json`.
   - `/root/lisa/examples/spec-kit-reverse-engineering-scope.md`. The best statement of
     the investigation policy (L36–47), the evidence-precedence order (L48–55), and the
     per-claim metadata block (L56–69). Adopt all three; the metadata block becomes the
     one-line evidence comment under *Evidence* in step 3.
   - `/root/lisa/packages/specs/src/reverse/{investigate,synthesis}.ts`. Borrow the ideas
     only: claim kinds `observed > inferred > declared`, **never `desired`**, and
     disagreement lowers confidence and is recorded, never silently resolved. Do not port
     the code.

## Verified ground truth (2026-09-22; re-verify before editing)

Facts the first draft assumed or left open. Each one shapes a step below.

- **Declared verification commands are JSON**, loaded by `load_declared_commands`:
  `{"schema_version"?: …, "commands": [ {id, phase, executable, args, cwd, timeoutSec,
  env?, stage?, reportPath?, reusePolicy?, detached?, cumulative?} … ],
  "phaseTimeouts"?: {"N": seconds}}`. `phase` is a positive int and must name a phase the
  spec defines (orphans abort the preflight). `cwd` must be absolute and exist at plan
  time. `executable` may be bare (resolved on `PATH` at plan time) or absolute. Unknown
  top-level keys are ignored, and the document is hash-bound into the plan. Per-phase
  commands are therefore fully expressible; the first draft's "if the verification layer
  cannot express per-phase commands" fallback is deleted.
- **Gates are cumulative by default:** phase N's gate re-runs every earlier phase's
  declared commands, then phase N's. Your lint and guard commands are idempotent, so this
  is harmless; do not set `cumulative: false`.
- **Discovery is unconditional.** `create_plan` always calls `discover_project(workdir)`
  and puts every discovered `test` command (a `python3 -m pytest <workdir>` when
  `pyproject.toml`/`pytest.ini`/`tox.ini` mentions pytest; `npm|pnpm|yarn test|build|lint`
  from `package.json` scripts; `cargo test`; `go test ./...`) in **every** phase gate,
  before the declared ones (L852–858). There is no switch. See hazard A.
- **Git checkpoints are on by default.** After every approved phase
  `maybe_git_checkpoint` runs `git add -A` and `git commit` in the workdir unless
  `SPECSTRIDE_GIT_COMMITS` is anything other than `auto`. It is env-only (no flag), and it
  is **not** among the 22 keys `write_last_run_conf` saves, so `specstride resume` loses
  it. See hazard B.
- **Verification writes into the workdir by default:** `TEST_PLAN` defaults to
  `<workdir>/testautomation/<feature>/TEST_PLAN.md` and `GENERATE_TESTS` to
  `<workdir>/testautomation/<feature>/generated` (orchestrator.sh L463–465). Both are
  saved to `last-run.conf`, so resume keeps whatever you pass. See hazard C.
- **Spec resolution:** an explicit `-s` wins outright; `resolve_spec` (the
  `specs/*/tasks.md` glob) only runs when `-s` is absent. `--spec-format native` pins the
  adapter for the whole run, and `render-context`/`speckit_context` return nothing for a
  native spec. So a native driver passed with `-s` is never confused with the half-written
  output `tasks.md`.
- **Native driver grammar** (`_parse_native`): a phase is `^## Phase <N>` with any title
  after the number; its acceptance criteria are the `- [ ]` lines under a
  `### Acceptance criteria` heading inside that phase; phases must be contiguous; a phase
  with no such heading is invalid.
- **Spec Kit tasks grammar** (`_SPECKIT_HEAD`): `^## Phase <N>:? <title>`; a phase must
  contain at least one checkbox; `_CHECKBOX` accepts `- [ ]`, `- [x]` and `- [X]`, so a
  fully ticked `--tasks done` document validates. The template's literal
  `## Phase N: Polish & Cross-Cutting Concerns` must be numbered in the output.
- **Template annotations are sparse.** Only `spec-template.md` marks headings
  `*(mandatory)*` (User Scenarios & Testing, Requirements, Success Criteria) and one
  `*(include if feature involves data)*`. `plan-template.md` has none; `tasks-template.md`
  marks the per-story *Tests* H3s `OPTIONAL`; `plan-template.md` marks structure options
  `[REMOVE IF UNUSED]`. `tasks-template.md` begins with YAML front matter. The linter's
  template rule in step 3 is written for this reality.
- **State dir:** `specstride_resolve_state_dir` picks a legacy `.wiggum/` when it exists
  and `.specstride/` does not. Every rule below that says `.specstride/` means "the
  resolved `STATE_BASENAME`".
- **Exit codes** (`orchestrator.sh` L33): `E_SPEC=3` is the invalid-input code across the
  codebase; `specstride_spec.py` uses 3 as well. The new CLIs use 0 ok, 3 findings or
  invalid input, and argparse's 2 for usage errors.
- **CI is parallel.** `.github/workflows` runs `python -m pytest lib/ -q -n 16` under
  pytest-xdist. Locally xdist is not installed and `pip install` is forbidden, so you run
  serially. Every new test must be safe under both (see step 6).
- **Origins:** `main` is still bf1e182. The `.env` in the checkout sets
  `SPECSTRIDE_PROPOSER=dsh` and `SPECSTRIDE_CRITIC=dsh`; those are the step 7 backends.

## Lisa's defects you must not reproduce

Each one below is a requirement, and each needs a test.

| Lisa defect | Your requirement |
|---|---|
| No file walker, ignore rules, language detection, or size budget. The agent explores ad hoc. | Step 1 inventory is deterministic, and the proposer gets it as ground truth. |
| No structural Spec Kit validation. The critic judges ID formats, `[P]`, and `[USn]` from prose. | Step 3 linter is the verification command for every phase. |
| No template is loaded. Compliance depends on the prompt text. | The linter derives required headings **from the template files** at runtime. It does not hard-code them. |
| `--spec-format reverse` is documented but throws `UnknownFormatError`. | Everything documented is tested through the real `specstride` script. |
| Evidence citations are checked only by the critic's grounding snapshot (32 KB budget; memory: *Critic grounding starvation*). | The linter checks that every cited `path:line` exists in `<SRC>` and that the line is in range, with no budget. |
| The default output sits inside the source, and nothing verifies the source stayed untouched. | A before/after source fingerprint guard. The run fails if anything outside the output dir and `.specstride/` changed, including git history. |
| `[NEEDS CLARIFICATION]` is not handled at all. | Follow the Spec Kit rule: at most 3 in `spec.md`. All other unknowns are written as explicit unknowns (see *Evidence*). |

## Two launch-time hazards (and one more) that the first draft missed

These are Specstride defaults, not Lisa defects. Each is a requirement with a test.

**A. Discovered commands would execute the target's own tests at every gate.** With the
workdir at `<SRC>`, `discover_project` registers the target's pytest, npm scripts, cargo or
go test command and the gate runs it, which breaks "never execute the product's tests" and
may not even be installable. The fix is the one edit to `lib/verification_plan.py` this
prompt permits, and it is additive: a top-level `"discovery": "none"` key in the declared
document (the same mechanism as `phaseTimeouts`). When present, `create_plan` treats
`discovery["commands"]` as empty (the discovery fingerprint, frameworks and assumptions
are still recorded, and an assumption line says discovery was disabled by the document).
Every phase must then have a declared command, which the existing "neither discovered nor
declared" preflight already enforces. Without the key, plans are byte-identical to today;
a test proves that, and one proves the key drops the discovered pytest command from every
phase and release suite. Nothing else in that module changes.

**B. Per-phase git checkpoints would commit into the target repo.** The reverse verb
exports `SPECSTRIDE_GIT_COMMITS=off` before it execs `orchestrator.sh` (caller env wins
over `.env`; memory: *.env clobbers caller env*). Because the knob is not persisted,
`specstride resume --feature reverse-<slug>` would silently re-enable it. Add
`GIT_COMMITS=%q` to `write_last_run_conf` and have the `resume` arm export
`SPECSTRIDE_GIT_COMMITS` from the sourced conf when the key is present. Older confs
without the key behave exactly as today. Test both through the real script.

**C. The verification layer's own outputs land in the workdir.** Pass
`--test-plan <SRC>/.specstride/reverse/<slug>/TEST_PLAN.md` and
`--generate-tests <SRC>/.specstride/reverse/<slug>/generated` (absolute paths, inside the
workdir as the orchestrator requires). Both round-trip through `last-run.conf`. The guard
in step 4 is what catches a regression here: a `testautomation/` directory appearing under
`<SRC>` fails the run.

## Step 0: land this prompt

This file is committed as 98f7e01 on branch `docs/reverse-prompt`, and PR #52 is open for
it with the `roadmap/README.md` entry already under Planned. Your first act, in
`/root/specstride`: if the working tree has uncommitted changes to this file, commit them
to that branch as `docs(roadmap): revise the reverse prompt after verification` and push.
Then wait for Mergify to merge #52 (`gh pr view 52 --json state,mergedAt`; poll, do not
merge it yourself). Every later branch starts from a `main` that contains it. Do not use
`git worktree`, `git stash` or `git clean` before it lands.

Repository rules (memory: *Protected main + Mergify*): `main` requires PR + CI `lint` and
`test`; Mergify squash-merges any non-draft green PR, and `required_conversation_resolution`
means an unresolved review thread stalls it silently; never push to `main`; never
`gh pr merge`; do not change the author identity. End each commit message with the
`Co-Authored-By:` trailer for the model running you, and each PR body with
`🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

Land the work below as **four PRs** in order, each green on its own:
(A) the two orchestration edits from hazards A and B, with tests;
(B) inventory + linter + guard + fixtures + tests;
(C) driver + CLI + tests;
(D) docs + report. Open a draft PR to hold work back; there is no `do-not-merge` label.

## Step 1: `lib/reverse_inventory.py` (stdlib only)

`python3 lib/reverse_inventory.py <SRC> --out <state>/reverse/<slug>/inventory.json [--md <state>/reverse/<slug>/INVENTORY.md]`

- **File walk:**
  - If `<SRC>` is a git work tree, use `git ls-files -co --exclude-standard`.
  - Otherwise use `os.walk`, honouring a root `.gitignore` (simple glob subset) and a
    built-in deny list: `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `dist`,
    `build`, `target`, `.specstride`, `.wiggum`, `.lisa`, `vendor`, `testautomation`.
  - Never follow symlinks out of `<SRC>`. Record an in-tree symlink as a file with
    `symlink: true` and its target; record an out-of-tree one under `skipped` with reason
    `symlink-escape`.
  - Skip binaries with a NUL-byte sniff of the first 8 KB. Skip files over
    `SPECSTRIDE_REVERSE_MAX_FILE_BYTES`, default 1 MiB, and list them as skipped with the
    reason.
- **Per file:** relative path, bytes, line count, language (by extension + shebang), and
  a role, one of `source|test|config|doc|schema|script|template|ci|other`.
- **Project facts:**
  - Manifests, with detected languages, frameworks, and entry points: `package.json`
    (bin, exports, scripts, workspaces), `pyproject.toml`/`setup.cfg`, `go.mod`,
    `Cargo.toml`, `pom.xml`/`build.gradle`, `Dockerfile`, `compose*.yml`, `Makefile`
    targets, and `.github/workflows/*`.
  - Detect pnpm/npm/yarn workspaces and treat each workspace package as a unit (memory:
    *Monorepo path resolution*). `_workspace_export_artifacts` in `verification_plan.py`
    (L557) shows the existing workspace walk; reuse the idea, not the function.
  - Existing `.specify/`: constitution present?, existing `specs/NNN-*` numbers, and
    `feature.json`.
- **Budget:** if the total text exceeds `SPECSTRIDE_REVERSE_MAX_TOTAL_BYTES` (default
  20 MiB), keep inventorying but set `oversize: true`. The driver then splits phase 1
  investigation by top-level unit (see step 2).
- **Deterministic output:** sorted keys and sorted paths, no timestamps in the body, and a
  `sha256` fingerprint over `(path, size, sha256)` of every walked file, plus
  `git_head` (the `HEAD` sha, or null). Step 4 uses both. `INVENTORY.md` is a human- and
  agent-readable rendering, capped at about 24 000 chars to match
  `SPECSTRIDE_CONTEXT_BUDGET` (default 24000), with a pointer to the full JSON. When the
  cap bites, drop per-file rows before project facts, and say what was dropped.

## Step 2: `lib/reverse.py`, the driver spec and orchestration glue

`python3 lib/reverse.py plan <SRC> [--out DIR] [--name SLUG] [--tasks done|open]` writes
the inventory, the driver spec and the verification-commands file, then prints their
paths as JSON. The bash verb then launches `orchestrator.sh` with them.

**Paths.** Everything the run needs lives under one directory,
`<SRC>/.specstride/reverse/<slug>/`: `inventory.json`, `INVENTORY.md`, `DRIVER.md`,
`verification-commands.json`, `TEST_PLAN.md`, `generated/`, `claims-<unit>.md` (oversize
only) and `ANALYSIS.md`.
- Run workdir: `<SRC>`. State lives in `<SRC>/.specstride/`, like any Specstride run on
  that repo. This is the only permitted write outside the output dir.
- Feature slug for the run: `reverse-<slug>`, passed as `--feature`. The sanitiser keeps
  `A-Za-z0-9._-`, so produce slugs from that alphabet.
- The driver is a **native** spec launched with `--spec-format native` and `-s <abs path>`,
  **not** a `tasks.md`. That way the half-written output directory is never auto-discovered
  as the run's spec, and `speckit_context` never injects it. Check both with a test: launch
  with `--dry-run`-style plumbing against a fixture that already has a `specs/*/tasks.md`,
  and assert the driver path is what reaches `orchestrator.sh`.

**Output directory:**
- Default: `<SRC>/specs/NNN-as-is-<slug>/`, where NNN is one more than the highest
  existing `specs/NNN-*` (001 if there are none).
- `<slug>` comes from `--name`, else the `<SRC>` basename, kebab-cased and truncated to 40
  characters (Lisa uses 48 and the prefix `reverse-engineer-`; this is a deliberate
  difference, record it).
- Refuse (exit 3) any `--out` that is `<SRC>` itself, is outside `<SRC>`, is inside `.git/`
  or the state dir, or already exists and is non-empty. The last case is allowed only on
  `resume`.
- Never write `.specify/feature.json`, never create a git branch, and never touch
  `<SRC>/.specify/`.

**Driver phases.** Each phase is `## Phase N: <title>` with a `### Acceptance criteria`
checklist, and each is one gate. Port Lisa's policy header verbatim in spirit:
- no network, no dependency install, no services
- no executing the product, tests, builds, or linters of `<SRC>`
- read-only discovery and `git log`/`git show`/`git blame` are allowed
- the only writes allowed are the output dir and `.specstride/`
- never present desired or recommended behavior as current behavior
- no To-Be design and no remediation backlog
- evidence precedence when sources disagree: executable source, schemas and config >
  tests > templates and command definitions > user docs > comments and git history

Every phase's text starts with the inventory path and tells the agent to treat it as
ground truth for *what exists*. Every phase's text also names the lint command the gate
will run and tells the agent to run it itself before writing evidence, and to cite the
output artifacts by workdir-relative path in `GATE<N>-EVIDENCE.md` so the critic's
grounding can resolve them. Keep evidence files short: the critic's grounding snapshot is
32 KB, and the linter's `--json` output is the strongest evidence a phase can offer.

1. **Constitution + specification.**
   - If there is no constitution, write `memory/constitution.md` from
     `constitution-template.md`, with principles *recovered from evidence* (lint config,
     CI gates, test conventions, AGENTS.md/CONTRIBUTING). Otherwise read the existing one
     and do not copy it.
   - Write `spec.md` from `spec-template.md`: prioritised user stories (P1…), each with
     *Why this priority*, *Independent Test*, and Given/When/Then scenarios; edge cases;
     `FR-###` and `SC-###`; key entities; assumptions.
   - Write `checklists/requirements.md` using the item list from `commands/specify.md`
     L146–180, with every item evaluated.
   - If the inventory is `oversize`, split this phase into 1a…1k, one per top-level
     unit/workspace package, each appending to a scratch `claims-<unit>.md`. Then add a
     final synthesis phase that writes `spec.md`. The **phase numbers stay contiguous
     integers** (1, 2, 3 … with the unit name in the title), because `_validate_native`
     requires it; "1a" is a label in the title, never the number.
2. **Plan + research.** Write `plan.md` from `plan-template.md` with the *real* Technical
   Context, the Constitution Check against the constitution from phase 1, and the actual
   project structure tree, with unused `[REMOVE IF UNUSED]` options deleted and
   *Complexity Tracking* kept as a heading with "None" when there are no violations.
   Write `research.md` as Decision / Rationale / Alternatives. A rationale that cannot be
   recovered is written `unknown`, never invented.
3. **Design artifacts.** Write `data-model.md` (entities, fields, relationships, state
   transitions found in code), `contracts/*.md` (one per external surface: CLI, HTTP API,
   events, files on disk, config/env), and `quickstart.md`. Commands in the quickstart come
   from manifests and docs and are labelled *not executed by the reverse run*.
4. **Tasks.** Write `tasks.md` from `tasks-template.md` (keep its front matter) and the
   rules in `commands/tasks.md`:
   - Phase 1 Setup, Phase 2 Foundational, one phase per user story in priority order,
     then a final numbered Polish phase.
   - Every line is `- [ ] T### [P]? [USn]? description with exact file path`.
   - `[USn]` appears only in user-story phases.
   - `[P]` appears only on tasks that touch different files and have no unmet dependency.
   - The *Dependencies & Execution Order* and *Parallel Example* sections are filled in.
   - With `--tasks done` (the default), every task is `[x]` and describes work that the
     evidence shows already exists. That makes the directory a baseline that
     `/speckit.converge` or a later Specstride run can extend.
   - With `--tasks open`, every task is `[ ]`, giving a rebuild plan for the same system.
5. **Cross-artifact analysis and sign-off.** Do a `commands/analyze.md`-style pass:
   - every FR is covered by at least one task, every `[USn]` exists in the spec, and there
     are no terminology drift, duplicates, or contradictions
   - fix every finding in place
   - write the analysis report to `<state>/reverse/<slug>/ANALYSIS.md` (not into the
     output dir), in the report shape from `commands/analyze.md` L162–190
   - the linter passes with zero errors at `--upto 5` and the source-untouched guard passes

**Verification.** Generate `verification-commands.json` in the format above with
`"discovery": "none"` (hazard A) and, for every driver phase N, two entries:

```json
{"id": "lint-N", "phase": N, "executable": "<sys.executable>", "cwd": "<SRC>",
 "args": ["<SPECSTRIDE>/lib/reverse.py", "lint", "<out>", "--src", "<SRC>", "--upto", "N"],
 "timeoutSec": 120}
{"id": "guard-N", "phase": N, "executable": "<sys.executable>", "cwd": "<SRC>",
 "args": ["<SPECSTRIDE>/lib/reverse.py", "guard", "<SRC>", "--baseline", "<inventory.json>", "--out", "<out>"],
 "timeoutSec": 300}
```

All paths absolute. `--upto N` checks only the artifacts that phases ≤ N own (the map is
in step 3), so phase 1 is not failed for a missing `tasks.md`. Add a `phaseTimeouts` map:
phase 1 (and each oversize split) gets 3600 s, the rest keep the global default. The
document must load through `verification_plan.load_declared_commands` and `create_plan`
must accept it against the generated driver; test both with the real functions.
**Do not weaken or edit the critic** (`lib/critic.py`) to make reverse runs pass. The
linter's job is to make the critic's job easier.

## Step 3: the linter, `reverse.py lint`

`python3 lib/reverse.py lint <FEATURE_DIR> [--src SRC] [--upto N] [--json]`. Errors exit 3,
warnings print only, and `--json` gives machine output (`{"errors": [...], "warnings":
[...]}`, each finding with `rule`, `file`, `line`, `message`). Without `--src`, the
evidence rules are skipped with a warning, so the linter still works on any hand-written
Spec Kit directory. Every rule gets a fixture test with one passing case and one failing
case.

**Artifact ownership for `--upto`:** 1 → `spec.md`, `checklists/requirements.md`, and
`memory/constitution.md` only when `<SRC>/.specify/memory/constitution.md` is absent;
2 → `plan.md`, `research.md`; 3 → `data-model.md`, `contracts/*.md`, `quickstart.md`;
4 → `tasks.md` and the tasks↔spec cross rules; 5 (the default) → everything plus the
FR-coverage rule. An oversize run maps its split phases onto level 1 in the generated
document; `--upto` is about artifacts, not driver phase numbers.

- **Structure:** every required file exists for the `--upto` level, and nothing else is
  in the output dir (allowed set: the nine paths in the layout, `contracts/*.md`, and
  nothing more, not even `README.md`). `contracts/` holds at least one `.md`.
- **Templates:** load the templates with this precedence: `<SRC>/.specify/templates/`,
  else Specstride's own `.specify/templates/`. Derive the required `##` headings from the
  template at runtime with this rule, and no hard-coded heading list anywhere:
  - a heading whose text carries `*(mandatory)*` is required (error when missing);
  - a heading carrying `*(optional)*`, `*(include if …)*`, `OPTIONAL` or
    `[REMOVE IF UNUSED]` is optional (no finding);
  - a heading with a bracket placeholder (`## [Category 1]`, `## Phase 3: User Story 1 -
    [Title] (Priority: P1)`) is a **family**: at least one heading matching it with the
    placeholder replaced by `.+` must exist (error), and `## Phase N:` families are
    counted, not matched literally;
  - every other `##` heading is expected (warning when missing).
  Strip YAML front matter before parsing a template or an artifact. Test the rule with a
  synthetic template so it is proven template-driven, not just template-shaped.
- **No placeholders left:** `[FEATURE NAME]`, `[###-feature-name]`, `$ARGUMENTS`,
  `ACTION REQUIRED`, `[DATE]`, `__SPECKIT_COMMAND_*__`, bracketed `[e.g., …]` example
  text, and template HTML comments copied verbatim (compare against the loaded
  template's comments, not a list).
- **spec.md:**
  - `FR-###` and `SC-###` are unique and 3-digit, in the template's `- **FR-001**:` form.
  - User stories are `### User Story <n> - <title> (Priority: P<n>)` with *Why this
    priority*, *Independent Test*, and at least one `**Given** … **When** … **Then**`.
    Story numbers are contiguous from 1.
  - `[NEEDS CLARIFICATION` appears at most 3 times.
- **tasks.md:**
  - `specstride_spec.validate(..., format="speckit-tasks")` passes. **Import it; do not
    re-implement the grammar.**
  - Every checkbox line matches `^- \[[ xX]\] T\d{3}( \[P\])?( \[US\d+\])? \S` in that
    token order.
  - `T###` are unique and increasing.
  - The first phase title starts `Setup`, the second `Foundational`, the last `Polish`;
    every phase between names a user story, and `[USn]` appears only in the phase whose
    title names that story; every `USn` exists in `spec.md`.
  - Setup, Foundational and Polish phases carry no `[USn]`.
  - Two `[P]` tasks in the same phase do not name the same file path.
  - With `--tasks done` every box is `[x]`; with `--tasks open` every box is `[ ]`. The
    mode is read from a `<!-- specstride-reverse: tasks=done -->` comment the driver
    writes into `tasks.md`, so the linter needs no flag for it.
- **Evidence:**
  - Every `FR-###` and `SC-###` line, every research decision, and every data-model
    entity carries a citation in the machine-checkable form below, on the line directly
    after the claim. It is invisible when rendered, so the artifacts stay template-clean.
    It is Lisa's five-line metadata block on one line:
    ```text
    <!-- evidence: src/app/server.py:40-88 | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->
    ```
  - The evidence field is one or more `path:START[-END]` citations separated by `, `.
  - `kind ∈ {observed, inferred, declared}`. `desired` is an **error**.
  - `confidence ∈ [0,1]`, two decimals. `validation ∈ {verified, unverified, disproved}`.
  - Each path resolves under `<SRC>`: normalise `./`, reject `..` escapes and absolute
    paths (memory: *Relative-path normalization*), and apply workspace-relative resolution
    for monorepos using the inventory's workspace list.
  - The line range is within the file's line count as recorded in `inventory.json` when
    `--baseline` is given, else as read from disk.
  - `contradicts` is `none` or citations in the same form, resolved the same way.
  - `validation=disproved` on an FR is an error, because the requirement must then be
    removed.
- **Unknowns:** a claim with `validation=unverified` and `confidence < 0.5` must sit under
  the spec's *Assumptions* section, not among the requirements (warning).
- **Checklist:** in `checklists/requirements.md`, every item is `[x]` or has a following
  indented `Fails:` note with a citation in the evidence form. Items may carry `CHK###`
  ids (the template) or not (`commands/specify.md`); accept both.

## Step 4: `reverse.py guard`, the source-untouched guard

`python3 lib/reverse.py guard <SRC> --baseline <inventory.json> --out <out>`. Re-walk
`<SRC>` using the inventory rules, excluding `<out>` and the resolved state dir. Recompute
the fingerprint and compare it with the baseline. On a mismatch, list the changed, added
and deleted paths and exit 3. When `<SRC>` is a git tree, also require `git rev-parse HEAD`
to equal the baseline's `git_head` (hazard B) and treat any `git status --porcelain` entry
outside the two allowed roots as a failure. A `testautomation/` directory under `<SRC>` is
the canonical regression this catches (hazard C); test it.

## Step 5: CLI wiring in `specstride`

- Add `--reverse)` to the pre-dispatch `case` **above** the `-*)` catch-all (L64–68).
  Without it, `specstride --reverse X` is forwarded to `orchestrator.sh` as a launch.
- Add a `reverse` verb. Both routes call one function that:
  1. runs `reverse.py plan`
  2. unless `--dry-run`, exports `SPECSTRIDE_GIT_COMMITS=off` and execs
     `orchestrator.sh -w <SRC> -s <DRIVER.md> --spec-format native --feature reverse-<slug>
     --verification-commands <file> --test-plan <…> --generate-tests <…> …`, passing
     through any launch flags given (`--proposer`, `--critic`, timeouts, telemetry)
- `--dry-run` prints the inventory summary, the output dir, the driver path, the phase
  list and the exact orchestrator argv, then exits 0 without any LLM call.
- `reverse --lint DIR [--src SRC] [--upto N] [--json]` runs only the linter. It is useful
  on any Spec Kit directory, including hand-written ones. Its exit code is the linter's.
- Update the help comment block and **the `sed -n '2,43p'` range in `usage()`**, which
  must grow with it; a test asserts `--help` prints every new verb line.
- `specstride status|watch|resume --feature reverse-<slug>` must work unchanged. Test
  `resume` and assert it carries `SPECSTRIDE_GIT_COMMITS=off` (hazard B).
- Black-box or runtime-assisted requests (`--mode` anything but `source-aware`, if you add
  the flag at all) are refused with Lisa's message, exit 3.
- New knobs go in `.env.example` and through the normal precedence (memory: *.env
  clobbers caller env*): `SPECSTRIDE_REVERSE_MAX_FILE_BYTES`,
  `SPECSTRIDE_REVERSE_MAX_TOTAL_BYTES`, `SPECSTRIDE_REVERSE_TASKS`.

## Step 6: tests (pytest, next to the code, stdlib only, no live LLM)

- `lib/fixtures/reverse/src-mini/`: a tiny, committed, real-looking project with a
  `pyproject.toml` that mentions pytest (so discovery finds a test command and hazard A
  is exercised), 3 modules, 1 test, a README, a `.gitignore`d file, a binary, and a
  symlink pointing outside the tree. Git does not store the ignored file, so the test
  creates it in a `tmp_path` copy.
- `lib/fixtures/reverse/src-pnpm/`: a two-package workspace with `./dist/index.js`-style
  exports.
- `lib/fixtures/reverse/good/`: a hand-written, lint-clean artifact set for `src-mini`.
  Base its style on `reversed/`. Its citations point into `src-mini`, so a test pins
  that they resolve; editing one fixture without the other fails loudly.
- `lib/fixtures/reverse/bad-*/`: one directory per linter rule, each with exactly one
  violation. Generate them from `good/` in a test with a single mutation each where
  practical, so the fixture count stays honest.
- `lib/fixtures/reverse/templates-synthetic/`: a template set with invented headings for
  the template-driven proof in step 3.
- Fixtures use current names only. `lib/test_rename_guard.py` allowlists `reversed/*`
  but not `lib/fixtures/reverse/**`; do not extend the allowlist.
- `lib/test_reverse_inventory.py`: determinism (two runs give byte-identical JSON),
  ignore rules, binary/oversize skipping, symlink escape, workspace detection, and
  fingerprint stability.
- `lib/test_reverse.py`:
  - every lint rule passes and fails as expected, including `--upto` scoping and the
    no-`--src` degradation
  - the guard catches a modified file, a new file, a deleted file, a `testautomation/`
    directory and a moved `HEAD`, and ignores `<out>` and the state dir
  - output-dir numbering and refusals
  - the generated driver passes `specstride_spec.validate(..., "native")` with
    contiguous phases, in both the normal and the oversize/split case
  - the verification-commands file loads through `load_declared_commands`, and
    `create_plan` on the driver yields gates whose only commands are the declared ones
- `lib/test_verification_plan.py` additions: `"discovery": "none"` drops discovered
  commands; without the key the plan is byte-identical to today's (hazard A).
- `lib/test_specstride_cli.py` additions, through the real script (it already drives
  `specstride` over a sanitised fixture workdir; follow its pattern):
  - `--reverse --dry-run` and `reverse --dry-run` produce identical output
  - `--reverse` is **not** forwarded as a launch flag
  - the dry-run argv contains `--spec-format native`, `-s <DRIVER.md>`, `--test-plan` and
    `--generate-tests` under the state dir, and the env carries `SPECSTRIDE_GIT_COMMITS=off`
  - `resume --feature reverse-<slug>` re-exports `SPECSTRIDE_GIT_COMMITS` from a conf
    that has it, and does nothing new for a conf that does not
  - `reverse --lint` exit codes
  - `--help` contains the new lines
- **Parallel-safe tests.** CI runs `python -m pytest lib/ -q -n 16`; locally you run
  `python3 -m pytest lib/ -q -p no:cacheprovider` with a long timeout. Every test writes
  only under `tmp_path`, copies fixtures before mutating them, never `os.chdir`s without
  `monkeypatch.chdir`, and never depends on test order. `pip install` is forbidden.
- Run `bash -n` and `shellcheck -S error` on every touched shell file, as CI does.

## Step 7: one live end-to-end run (bounded)

After PR C merges, run `specstride --reverse <small repo> --tasks done` once, on a real
repository with fewer than about 60 text files and no secrets. Pick one under `/root/`,
and **never** `/root/specstride` itself, because `reversed/` already covers that. Use the
default backends from `.env` (`dsh` for both roles today).
- Before launching, snapshot `git -C <repo> rev-parse HEAD` and `git status --porcelain`
  yourself; after the run, compare them by hand as well as through the guard.
- Let it run to `all_approved` or a stop.
- Then run `reverse --lint` and `guard` yourself.
- Record in the report: phases, rejections per phase, wall time, the cost if the event
  stream records it, and three sample FRs with their evidence. Include your honest
  judgement of whether they are true.
- If there is no working backend, or the run stops, do not retry more than once. Report
  what happened and leave the state directory for inspection. Known noise: proposer pass
  1 may fail with "Prompt is too long" when the target mentions Anthropic tokens (memory:
  *Proposer pass-1 skill overflow*). Pass 2 recovers, so it is not a reverse bug.
- The generated artifacts belong to that repo. Do not commit them anywhere. Remove the
  `.specstride/` state you created there only if the repo's owner would not want it;
  otherwise leave it and say where it is.

## Step 8: docs (PR D)

- A new page, `wiki/Reverse-Engineering.md`: what the mode does, the output layout, the
  evidence comment format, the policy (read-only, never `desired`, git checkpoints off,
  discovery off), `--tasks done|open`, linting a hand-written Spec Kit directory,
  resuming, and its limits.
- Link it from `wiki/Home.md` (wiki map) and `wiki/CLI-Reference.md` (verb table and
  *Starting a run*), and add a short section to `README.md` near the Spec Kit input
  section.
- Document `"discovery": "none"` beside `phaseTimeouts` wherever the verification-commands
  document is described (`orchestrator.sh --help`, `.env.example`, the wiki).
- Update `.env.example` and the `roadmap/README.md` entry (move it to Done, with the
  report link, in the style of the self-improving-loop entry).
- Use the current names only (`specstride`, `SPECSTRIDE_*`, `.specstride/`).
  `lib/test_rename_guard.py` fails on new legacy names outside its allowlist.

## Out of scope

- A To-Be/enhancement mode.
- Black-box (no-source) or runtime-assisted reverse engineering. Lisa refuses these too.
  Refuse them with a clear message if someone asks.
- Emitting Lisa's v2 bundle JSON.
- Editing `lib/critic.py`, or the learning loop's knob allowlist.
- Editing `lib/verification_plan.py` beyond hazard A's one additive key, or
  `orchestrator.sh`/`specstride`'s `resume` beyond hazard B's one persisted knob.
- Committing anything under `.specstride/`, `.ralph/`, or run-specific scripts (memory:
  *One-off run artifacts*).

## Hard rules

- Never `git push --force`, never push to `main`, never merge locally, never `gh pr merge`
  in any form. Mergify merges.
- Never write under `.specstride/`, `.wiggum/`, `.ralph/` or any `runs/` directory of
  `/root/specstride`. They are live state. The step 7 target's state dir is the one
  exception, and only during step 7.
- No pip, no new dependencies.
- Do not start a paid model backend and do not start a `specstride run` before step 7.

## Final report

Write `roadmap/reverse-engineer-to-speckit-report.md` and ship it in PR D. It covers:
1. What shipped, per PR: status (merged / open / draft / blocked and why).
2. **An evidence table, one row per PR:** PR URL | CI run URL and conclusion
   (`gh run view <id> --json conclusion,url`) | `shellcheck -S error` result for every
   shell file touched | the verbatim pytest summary line, with the pre-change count
   beside it | merge SHA | `gh api repos/mairp/specstride/commits/main --jq .sha` after
   the merge. A row with no CI run URL does not count as green.
3. Every decision this prompt left to you, and what you chose.
4. Deviations from this prompt, and why, with `file:line` evidence for anything you found
   wrong in it.
5. The step 7 results, including failures, the before/after HEAD and status comparison,
   and the three sample FRs with your judgement.
6. The linter rule list, with a note on which rules produced findings during the live run
   and which never fired.
7. The rollback target per PR (the merge SHA to `git revert`).
8. Remaining gaps, and the first concrete step for each.

Print the report path, every PR URL and every merge SHA as the last lines of output,
then print the single word `DONE` on its own line.
