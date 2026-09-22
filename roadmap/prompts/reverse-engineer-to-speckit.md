# Prompt — `specstride --reverse <folder>`: reverse-engineer a codebase into Spec Kit artifacts

You are an autonomous coding agent running unattended. Nobody will answer questions.
Make routine decisions yourself, record them in the final report, and stop only when a
step below is impossible (say why and what remains). Written 2026-09-22 against
Specstride `main` bf1e182 and Lisa `main` 295f92e. Where this prompt and any other
document disagree, this prompt wins.

## What you are building

A new front-door mode:

```text
specstride --reverse <SRC> [--out DIR] [--name SLUG] [--tasks done|open]
                           [--proposer B] [--critic B] [--dry-run] [launch flags…]
specstride reverse   <SRC> …                  # same thing, verb spelling
specstride reverse --lint <FEATURE_DIR> [--src SRC]   # deterministic linter only, no LLM
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

1. `specstride` lines 1–120 (the front door, help block, dispatch), then `orchestrator.sh`
   lines 160–420 (config precedence, flag parse, spec discovery) and 760–800
   (`--verification-commands` wiring).
2. `lib/specstride_spec.py`. Read all of it. It is the single source of truth for spec
   grammar (memory: *Spec parsing single source of truth*). Note especially
   `_validate_speckit` (L287), `detect_format` (L397), `find_specify_root` (L504),
   `feature_slug` (L524), `speckit_context` (L611), and the `main` CLI (L853).
3. `lib/verification_plan.py` and the `--verification-commands` format it accepts. You
   will generate a document in that format; you should not need to edit this module.
4. `lib/learn.py` lines 1–80 and the `learn` verb in `specstride` (L540–595). Use this as
   the pattern for a thin bash verb that hands off to a `lib/*.py` CLI.
5. `reversed/`, a full Spec Kit reverse of Specstride (then called Wiggum) produced by a
   hand-written six-phase Wiggum run. Use it as the **quality bar and the golden example**
   for your fixtures: its inline evidence style, `contracts/` split, and `research.md`
   decision/rationale/alternatives format. Its names are pre-rename (`wiggum_spec.py`,
   `WIGGUM_*`), so do not copy them.
6. Spec Kit templates: `.specify/templates/{spec,plan,tasks,checklist,constitution}-template.md`
   and the command prompts `/root/spec-kit/templates/commands/{specify,plan,tasks,analyze}.md`.
   The commands define the rules the templates only imply: ID formats, `[P]` and `[USn]`
   semantics, the phase order Setup → Foundational → one phase per user story in priority
   order → Polish, the max-3 `[NEEDS CLARIFICATION]` rule, and the requirements checklist.
7. The Lisa feature, **for what to keep and what to avoid**:
   - `/root/lisa/packages/dashboard/src/chat.ts` L96–143, 275–340, 444–506. This is the
     only working path: intent parsing, destination defaulting, and
     `createReverseEngineeringSpec`, the generated four-phase driver spec. Port its phase
     text and policy lines; they are good.
   - `/root/lisa/examples/spec-kit-reverse-engineering-scope.md`. The best statement of the
     investigation policy, the evidence-precedence order, and the per-claim metadata.
     Adopt all three.
   - `/root/lisa/packages/specs/src/reverse/{investigate,synthesis}.ts`. Borrow the ideas
     only: claim kinds `observed > inferred > declared`, **never `desired`**, and
     disagreement lowers confidence and is recorded, never silently resolved. Do not port
     the code.

## Lisa's defects you must not reproduce

Each one below is a requirement, and each needs a test.

| Lisa defect | Your requirement |
|---|---|
| No file walker, ignore rules, language detection, or size budget. The agent explores ad hoc. | Step 1 inventory is deterministic, and the proposer gets it as ground truth. |
| No structural Spec Kit validation. The critic judges ID formats, `[P]`, and `[USn]` from prose. | Step 3 linter is the verification command for every phase. |
| No template is loaded. Compliance depends on the prompt text. | The linter derives required headings **from the template files** at runtime. It does not hard-code them. |
| `--spec-format reverse` is documented but throws `UnknownFormatError`. | Everything documented is tested through the real `specstride` script. |
| Evidence citations are checked only by the critic's grounding snapshot (32 KB budget; memory: *Critic grounding starvation*). | The linter checks that every cited `path:line` exists in `<SRC>` and that the line is in range, with no budget. |
| The default output sits inside the source, and nothing verifies the source stayed untouched. | A before/after source fingerprint guard. The run fails if anything outside the output dir and `.specstride/` changed. |
| `[NEEDS CLARIFICATION]` is not handled at all. | Follow the Spec Kit rule: at most 3 in `spec.md`. All other unknowns are written as explicit unknowns (see *Evidence*). |

## Step 0: land this prompt

This file is an untracked working-tree file on `main`. On branch
`docs/reverse-prompt`, commit it (plus a one-line entry in `roadmap/README.md` under
Planned), open a PR, and let Mergify merge it. Every later branch starts from a `main`
that contains it. Repository rules (memory: *Protected main + Mergify*): `main` requires
PR + CI `lint` and `test`; Mergify squash-merges any non-draft green PR; never push to
`main`; never `gh pr merge`; do not change the author identity. End each commit message
with the `Co-Authored-By:` trailer for the model running you, and each PR body with
`🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

Land the work below as **three PRs** in order: (A) inventory + linter + tests, (B) driver
+ CLI + tests, (C) docs. Each one must be green on its own.

## Step 1: `lib/reverse_inventory.py` (stdlib only)

`python3 lib/reverse_inventory.py <SRC> --out <state>/reverse/inventory.json [--md <state>/reverse/INVENTORY.md]`

- **File walk:**
  - If `<SRC>` is a git work tree, use `git ls-files -co --exclude-standard`.
  - Otherwise use `os.walk`, honouring a root `.gitignore` (simple glob subset) and a
    built-in deny list: `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `dist`,
    `build`, `target`, `.specstride`, `.wiggum`, `.lisa`, `vendor`.
  - Never follow symlinks out of `<SRC>`.
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
    *Monorepo path resolution*).
  - Existing `.specify/`: constitution present?, existing `specs/NNN-*` numbers, and
    `feature.json`.
- **Budget:** if the total text exceeds `SPECSTRIDE_REVERSE_MAX_TOTAL_BYTES` (default
  20 MiB), keep inventorying but set `oversize: true`. The driver then splits phase 1
  investigation by top-level unit (see step 2).
- **Deterministic output:** sorted keys and sorted paths, no timestamps in the body, and a
  `sha256` fingerprint over `(path, size, sha256)` of every walked file. Step 4 uses this
  fingerprint. `INVENTORY.md` is a human- and agent-readable rendering, capped at about
  24 000 chars to match `CONTEXT_BUDGET_DEFAULT`, with a pointer to the full JSON.

## Step 2: `lib/reverse.py`, the driver spec and orchestration glue

`python3 lib/reverse.py plan <SRC> [--out DIR] [--name SLUG] [--tasks done|open]` writes
the inventory, the driver spec and the verification-commands file, then prints their
paths as JSON. The bash verb then launches `orchestrator.sh` with them.

**Paths:**
- Run workdir: `<SRC>`. State lives in `<SRC>/.specstride/`, like any Specstride run on
  that repo. This is the only permitted write outside the output dir.
- Driver spec: `<SRC>/.specstride/reverse/<slug>/DRIVER.md`.
- Feature slug for the run: `reverse-<slug>`, passed as `--feature`.
- The driver is a **native** spec launched with `--spec-format native` and `-s`, **not**
  a `tasks.md`. That way the half-written output directory is never auto-discovered as
  the run's spec, and `speckit_context` never injects it. Check both with a test.

**Output directory:**
- Default: `<SRC>/specs/NNN-as-is-<slug>/`, where NNN is one more than the highest
  existing `specs/NNN-*` (001 if there are none).
- `<slug>` comes from `--name`, else the `<SRC>` basename, kebab-cased and truncated to 40
  characters.
- Refuse (exit 3) any `--out` that is `<SRC>` itself, is inside `.git/` or `.specstride/`,
  or already exists and is non-empty. The last case is allowed only with `resume`.
- Never write `.specify/feature.json`, never create a git branch, and never touch
  `<SRC>/.specify/`.

**Driver phases.** Each phase has an `### Acceptance criteria` checklist, and each is one
gate. Port Lisa's policy header verbatim in spirit:
- no network, no dependency install, no services
- no executing the product, tests, builds, or linters of `<SRC>`
- read-only discovery and `git log`/`git show`/`git blame` are allowed
- the only writes allowed are the output dir and `.specstride/`
- never present desired or recommended behavior as current behavior
- no To-Be design and no remediation backlog

Every phase's text starts with the inventory path and tells the agent to treat it as
ground truth for *what exists*.

1. **Constitution + specification.**
   - If there is no constitution, write `memory/constitution.md` from
     `constitution-template.md`, with principles *recovered from evidence* (lint config,
     CI gates, test conventions, AGENTS.md/CONTRIBUTING). Otherwise read the existing one
     and do not copy it.
   - Write `spec.md` from `spec-template.md`: prioritised user stories (P1…), each with
     *Why this priority*, *Independent Test*, and Given/When/Then scenarios; edge cases;
     `FR-###` and `SC-###`; key entities; assumptions.
   - Write `checklists/requirements.md` using the structure from `commands/specify.md`,
     with every item evaluated.
   - If the inventory is `oversize`, split this phase into 1a…1k, one per top-level
     unit/workspace package, each appending to a scratch `<state>/reverse/<slug>/claims-<unit>.md`.
     Then add a final 1z that synthesises `spec.md`. Keep contiguous numbering, because
     `_validate_speckit` requires it.
2. **Plan + research.** Write `plan.md` from `plan-template.md` with the *real* Technical
   Context, the Constitution Check against the constitution from phase 1, and the actual
   project structure tree, with unused template options deleted. Write `research.md` as
   Decision / Rationale / Alternatives. A rationale that cannot be recovered is written
   `unknown`, never invented.
3. **Design artifacts.** Write `data-model.md` (entities, fields, relationships, state
   transitions found in code), `contracts/*.md` (one per external surface: CLI, HTTP API,
   events, files on disk, config/env), and `quickstart.md`. Commands in the quickstart come
   from manifests and docs and are labelled *not executed by the reverse run*.
4. **Tasks.** Write `tasks.md` from `tasks-template.md` and the rules in
   `commands/tasks.md`:
   - Phase 1 Setup, Phase 2 Foundational, one phase per user story in priority order,
     then a final Polish phase.
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
     output dir)
   - the linter passes with zero errors and the source-untouched guard passes

**Verification.** Generate `<state>/reverse/<slug>/verification-commands.<ext>` in the
exact format `lib/verification_plan.py` accepts. Phase N runs
`python3 <SPECSTRIDE>/lib/reverse.py lint <out> --src <SRC> --upto N` plus
`python3 <SPECSTRIDE>/lib/reverse.py guard <SRC> --baseline <inventory.json> --out <out>`.
`--upto N` checks only the artifacts that phases ≤ N own, so phase 1 is not failed for a
missing `tasks.md`. If the verification layer cannot express per-phase commands, record
that in the report and fall back to a lint command that is idempotent across phases.
**Do not weaken or edit the critic** (`lib/critic.py`) to make reverse runs pass. The
linter's job is to make the critic's job easier.

## Step 3: the linter, `reverse.py lint`

Errors exit 3, warnings print only, and `--json` gives machine output. Every rule gets a
fixture test with one passing case and one failing case.

- **Structure:** every required file exists for the `--upto` level, and nothing else is
  in the output dir. `contracts/` holds at least one `.md`.
- **Templates:** load the templates with this precedence: `<SRC>/.specify/templates/`,
  else Specstride's own `.specify/templates/`. For each artifact, every `##` heading that
  the template marks mandatory must be present. Parse the template's own
  "mandatory/optional" annotations rather than hard-coding them.
- **No placeholders left:** `[FEATURE NAME]`, `[###-feature-name]`, `$ARGUMENTS`,
  `ACTION REQUIRED`, `[DATE]`, bracketed `[e.g., …]` example text, and template HTML
  comments copied verbatim.
- **spec.md:**
  - `FR-###` and `SC-###` are unique and 3-digit.
  - User stories have `Priority: P<n>`, *Independent Test*, and at least one
    `**Given** … **When** … **Then**`.
  - `[NEEDS CLARIFICATION` appears at most 3 times.
- **tasks.md:**
  - `specstride_spec.validate(..., format="speckit-tasks")` passes. **Import it; do not
    re-implement the grammar.**
  - Every checkbox line matches `^- \[[ xX]\] T\d{3}( \[P\])?( \[US\d+\])? \S` in that
    token order.
  - `T###` are unique and increasing.
  - `[USn]` appears only in phases whose title names that story, and every `USn` exists
    in `spec.md`.
  - Setup, Foundational and Polish phases carry no `[USn]`.
  - Two `[P]` tasks in the same phase do not name the same file path.
- **Evidence:**
  - Every `FR-###` and `SC-###` line, every research decision, and every data-model
    entity carries a citation in the machine-checkable form below. It is invisible when
    rendered, so the artifacts stay template-clean:
    ```text
    <!-- evidence: src/app/server.py:40-88 | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->
    ```
  - `kind ∈ {observed, inferred, declared}`. `desired` is an **error**.
  - `confidence ∈ [0,1]`.
  - The path resolves under `<SRC>`: normalise `./`, reject `..` escapes (memory:
    *Relative-path normalization*), and apply workspace-relative resolution for monorepos.
  - The line range is within the file's line count.
  - `contradicts` paths resolve the same way.
  - `validation=disproved` on an FR is an error, because the requirement must then be
    removed.
- **Unknowns:** a claim with `validation=unverified` and `confidence < 0.5` must sit under
  the spec's *Assumptions* section, not among the requirements (warning).
- **Checklist:** in `checklists/requirements.md`, every item is `[x]` or has a following
  indented `Fails:` note with evidence.

## Step 4: `reverse.py guard`, the source-untouched guard

Re-walk `<SRC>` using the inventory rules, excluding `<out>` and `.specstride/`. Recompute
the fingerprint and compare it with the baseline. On a mismatch, list the changed paths
and exit 3. Also run `git status --porcelain` when `<SRC>` is a git tree, and treat any
entry outside the two allowed roots as a failure.

## Step 5: CLI wiring in `specstride`

- Add `--reverse)` to the pre-dispatch `case` **above** the `-*)` catch-all (L64–69).
  Without it, `specstride --reverse X` is forwarded to `orchestrator.sh` as a launch.
- Add a `reverse` verb. Both routes call one function that:
  1. runs `reverse.py plan`
  2. unless `--dry-run`, execs `orchestrator.sh -w <SRC> -s <DRIVER.md> --spec-format native --feature reverse-<slug> --verification-commands <file> …`, passing through any launch flags given (`--proposer`, `--critic`, timeouts, telemetry)
- `--dry-run` prints the inventory summary, the output dir, the driver path and the phase
  list, then exits 0 without any LLM call.
- `reverse --lint DIR [--src SRC]` runs only the linter. It is useful on any Spec Kit
  directory, including hand-written ones.
- Update the help comment block and **the `sed -n '2,43p'` range in `usage()`**, which
  must grow with it.
- `specstride status|watch|resume --feature reverse-<slug>` must work unchanged. Test
  `resume`.
- New knobs go in `.env.example` and through the normal precedence (memory: *.env
  clobbers caller env*): `SPECSTRIDE_REVERSE_MAX_FILE_BYTES`,
  `SPECSTRIDE_REVERSE_MAX_TOTAL_BYTES`, `SPECSTRIDE_REVERSE_TASKS`.

## Step 6: tests (pytest, next to the code, stdlib only, no live LLM)

- `lib/fixtures/reverse/src-mini/`: a tiny, committed, real-looking project with a
  `pyproject.toml`, 3 modules, 1 test, a README, a `.gitignore`d file, a binary, and a
  symlink pointing outside the tree.
- `lib/fixtures/reverse/src-pnpm/`: a two-package workspace with `./dist/index.js`-style
  exports.
- `lib/fixtures/reverse/good/`: a hand-written, lint-clean artifact set for `src-mini`.
  Base its style on `reversed/`.
- `lib/fixtures/reverse/bad-*/`: one directory per linter rule, each with exactly one
  violation.
- `lib/test_reverse_inventory.py`: determinism (two runs give byte-identical JSON),
  ignore rules, binary/oversize skipping, symlink escape, workspace detection, and
  fingerprint stability.
- `lib/test_reverse.py`:
  - every lint rule passes and fails as expected, including `--upto` scoping
  - the guard catches a modified file, a new file and a deleted file, and ignores
    `<out>` and `.specstride/`
  - output-dir numbering and refusals
  - the generated driver passes `specstride_spec validate --format native` with
    contiguous phases, in both the normal and the oversize/split case
  - the verification-commands file is accepted by `verification_plan`'s own loader
- `lib/test_specstride_cli.py` additions, through the real script:
  - `--reverse --dry-run` and `reverse --dry-run` produce identical output
  - `--reverse` is **not** forwarded as a launch flag
  - `reverse --lint` exit codes
  - `--help` contains the new lines
- Run the suite as CI does, serially: `python3 -m pytest lib/ -q -p no:cacheprovider`
  with a long timeout. `pytest-xdist` is not installed locally, and `pip install` is
  forbidden. Run `bash -n` and `shellcheck -S error` on every touched shell file.

## Step 7: one live end-to-end run (bounded)

After PR B merges, run `specstride --reverse <small repo> --tasks done` once, on a real
repository with fewer than about 60 text files and no secrets. Pick one under `/root/`,
and **never** `/root/specstride` itself, because `reversed/` already covers that. Use the
default backends from `.env`.
- Let it run to `all_approved` or a stop.
- Then run `reverse --lint` and `guard` yourself.
- Record in the report: phases, rejections per phase, wall time, the cost if the event
  stream records it, and three sample FRs with their evidence. Include your honest
  judgement of whether they are true.
- If there is no working backend, or the run stops, do not retry more than once. Report
  what happened and leave the state directory for inspection. Known noise: proposer pass
  1 may fail with "Prompt is too long" when the target mentions Anthropic tokens (memory:
  *Proposer pass-1 skill overflow*). Pass 2 recovers, so it is not a reverse bug.
- The generated artifacts belong to that repo. Do not commit them anywhere.

## Step 8: docs (PR C)

- A new page, `wiki/Reverse-Engineering.md`: what the mode does, the output layout, the
  evidence comment format, the policy (read-only, never `desired`), `--tasks done|open`,
  linting a hand-written Spec Kit directory, resuming, and its limits.
- Link it from `wiki/Home.md` (wiki map) and `wiki/CLI-Reference.md` (verb table and
  *Starting a run*), and add a short section to `README.md` near the Spec Kit input
  section.
- Update `.env.example` and the `roadmap/README.md` entry (move it to Shipped).
- Use the current names only (`specstride`, `SPECSTRIDE_*`, `.specstride/`).
  `lib/test_rename_guard.py` fails on new legacy names outside its allowlist.

## Out of scope

- A To-Be/enhancement mode.
- Black-box (no-source) or runtime-assisted reverse engineering. Lisa refuses these too.
  Refuse them with a clear message if someone asks.
- Emitting Lisa's v2 bundle JSON.
- Editing `lib/critic.py`, `lib/verification_plan.py`, or the learning loop's knob
  allowlist.
- Committing anything under `.specstride/`, `.ralph/`, or run-specific scripts (memory:
  *One-off run artifacts*).

## Final report

Write `roadmap/reverse-engineer-to-speckit-report.md` and ship it in PR C. It covers:
- what shipped (PR numbers)
- every decision this prompt left to you, and what you chose
- deviations from this prompt, and why
- the step 7 results, including failures
- the linter rule list, with a note on which rules produced findings during the live run
- remaining gaps
