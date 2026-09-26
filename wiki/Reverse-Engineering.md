# Reverse Engineering

`specstride reverse` reads an existing codebase and writes a
[Spec Kit](https://github.com/github/spec-kit) feature directory that describes the system
**as it is today**: the spec, plan, research, data model, contracts, quickstart and tasks that
Spec Kit's own `/speckit.specify → /speckit.plan → /speckit.tasks` would have produced for it.
It never changes the source.

```bash
specstride reverse ~/src/my-service --dry-run     # see what it would do; no LLM call
specstride reverse ~/src/my-service               # run it (same as: specstride --reverse …)
specstride reverse --lint ~/src/my-service/specs/004-as-is-my-service --src ~/src/my-service
```

The result is a baseline. With `--tasks done` (the default) every task is ticked and records
work that already exists, so `/speckit.converge` or a later Specstride run extends it instead
of rebuilding it. With `--tasks open` every task is unticked: a rebuild plan for the same
system.

## How it works

A reverse run is an ordinary Specstride run with three additions. None of them asks an LLM
whether the result is good enough.

1. **A deterministic inventory.** `lib/reverse_inventory.py` walks the source (with
   `git ls-files -co --exclude-standard` in a git work tree, else a filesystem walk that
   honours the root `.gitignore` and skips `node_modules`, `dist`, `.venv` and the like). It
   records every file's size, line count, language and role, the manifests (`package.json`,
   `pyproject.toml`, `go.mod`, `Cargo.toml`, Dockerfiles, compose files, Makefiles, CI
   workflows), entry points, pnpm/npm/yarn workspaces, and any existing `.specify/` state.
   Binaries and files over `SPECSTRIDE_REVERSE_MAX_FILE_BYTES` are skipped but still
   fingerprinted; symlinks are never followed out of the tree. The agent gets this as ground
   truth for *what exists*.
2. **A driver spec.** `lib/reverse.py plan` writes a native spec whose phases write the
   artifacts one gate at a time. Each phase carries the investigation policy (below), the
   inventory path, and the commands its gate will run.
3. **A deterministic linter as every gate.** Each phase's verification commands are
   `reverse.py lint` (Spec Kit compliance and evidence) and `reverse.py guard` (the source is
   untouched). The critic still judges the phase, but it cannot approve a phase the linter
   fails.

| Phase | Writes | Gate lints |
|---|---|---|
| 1. Constitution and specification | `spec.md`, `checklists/requirements.md`, `memory/constitution.md` (only when the source has none) | `--upto 1` |
| 2. Plan and research | `plan.md`, `research.md` | `--upto 2` |
| 3. Design artifacts | `data-model.md`, `contracts/*.md`, `quickstart.md` | `--upto 3` |
| 4. Tasks | `tasks.md` | `--upto 4` |
| 5. Cross-artifact analysis | fixes in place; the report goes to the state dir | `--upto 5` |

When the source's text exceeds `SPECSTRIDE_REVERSE_MAX_TOTAL_BYTES` (default 20 MiB), phase 1
is split into one investigation phase per top-level unit (workspace package, else top-level
directory), each appending claims to a scratch file, followed by a synthesis phase. The phase
numbers stay contiguous.

## Output layout

```text
<SRC>/specs/NNN-as-is-<slug>/          # default; --out DIR overrides
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/*.md
├── tasks.md
├── checklists/requirements.md
└── memory/constitution.md             # only when <SRC>/.specify/memory/constitution.md is absent
```

`NNN` is one more than the highest existing `specs/NNN-*`. `<slug>` is `--name`, else the source
directory's name, kebab-cased and cut to 40 characters. Nothing else is allowed in the directory
(the linter fails on a stray `README.md`).

Everything the run itself needs lives in `<SRC>/.specstride/reverse/<slug>/`: `inventory.json`,
`INVENTORY.md`, `DRIVER.md`, `verification-commands.json`, `TEST_PLAN.md`, `generated/`,
`ANALYSIS.md`, and `claims-<unit>.md` for a split run. The run's feature is `reverse-<slug>`.

An `--out` that is the source root, outside the source, inside `.git/`, `.specify/` or the state
dir, or an existing non-empty directory is refused (exit 3). So is a second run of a slug whose
gates already have state: resume it instead.

## The evidence comment

Every `FR-###` and `SC-###` in `spec.md`, every research **Decision**, and every data-model
entity (`### <Entity>`) carries one comment on the line directly after it. It is invisible when
rendered, so the artifacts stay template-clean:

```markdown
- **FR-003**: The system MUST print every counter as `name: value`, sorted by name.
<!-- evidence: tally/report.py:9-13, tests/test_report.py:4-5 | kind=observed | confidence=0.95 | contradicts=none | validation=verified -->
```

| Field | Rule |
|---|---|
| citations | one or more `path:START[-END]`, separated by `, `; source-relative (`./` is normalised); absolute paths and `..` escapes are errors; in a monorepo a package-relative path resolves when exactly one workspace package has it; the range must exist in the file |
| `kind` | `observed` (source, schema, config or tests show it), `declared` (only docs or comments say it), or `inferred` (a synthesis). `desired` is an **error**: an as-is artifact never describes wanted behavior |
| `confidence` | `0.00`–`1.00`, two decimals |
| `contradicts` | `none`, or citations in the same form, checked the same way |
| `validation` | `verified`, `unverified` or `disproved`; a disproved requirement is an error (remove it) |

A claim that is `unverified` below `0.50` confidence belongs under the spec's *Assumptions*, not
among the requirements (a warning). `spec.md` may carry at most three `[NEEDS CLARIFICATION`
markers, as Spec Kit allows; every other unknown is written as an explicit unknown.

## The policy

Every driver phase carries it, and the gates enforce the parts a program can check:

- Read-only, source-aware investigation: no network, no dependency install, no services.
- The product, its tests, builds, linters and formatters are never executed. Reading files and
  `git log`/`git show`/`git blame` are allowed.
- The only writes are the output directory and the state directory. `.specify/` and
  `.specify/feature.json` are never touched, and no branch or commit is made.
- Current behavior only: no To-Be design, no remediation backlog, never `kind=desired`.
- When sources disagree, executable source, schemas and config beat tests, which beat templates
  and command definitions, which beat user docs, which beat comments and git history. The
  disagreement lowers confidence and is recorded in `contradicts=`.
- **Discovery off.** The generated verification-commands document sets `"discovery": "none"`,
  so the gates never run the target's own `pytest`, `npm test`, `cargo test` or `go test`;
  they run only the declared lint and guard commands.
- **Git checkpoints off.** The verb exports `SPECSTRIDE_GIT_COMMITS=off`, so no phase commits
  into the target repository. It is saved in `last-run.conf`, so `resume` keeps it off.
- **Verification output stays in the state dir.** `TEST_PLAN.md` and the generated scaffolds go
  to `.specstride/reverse/<slug>/`, not `<SRC>/testautomation/`.

`reverse.py guard` re-walks the source after every phase and fails the gate when any file outside
the output and state directories was added, changed or deleted, when `git HEAD` moved, when
`git status` shows a new entry outside those two directories, or when a `testautomation/`
directory appeared. `--mode` accepts only `source-aware`; black-box and runtime-assisted reverse
engineering are refused, because they need a permission and side-effect policy nobody has
written.

## What the linter checks

`specstride reverse --lint DIR [--src SRC] [--upto N] [--json]` works on any Spec Kit feature
directory, including one written by hand. Errors exit 3; warnings only print. `--json` prints
`{"errors": [...], "warnings": [...]}`, each finding with `rule`, `file`, `line` and `message`.

- **Structure**: every file the `--upto` level owns exists; nothing outside the layout exists;
  `contracts/` holds at least one `.md`.
- **Templates**: required headings come from the Spec Kit templates **at run time**
  (`<SRC>/.specify/templates/` first, else the copy of Spec Kit's templates vendored in
  `lib/speckit_templates/`), never from a list in the code. A
  heading marked `*(mandatory)*` is required; one marked `*(optional)*`, `*(include if …)*`,
  `OPTIONAL` or `[REMOVE IF UNUSED]` is optional; one with a bracket placeholder
  (`## [Category 1]`) is a family that some heading must match; every other `##` heading is
  expected (a warning when missing).
- **Placeholders**: no `[FEATURE NAME]`, `[DATE]`, `$ARGUMENTS`, `ACTION REQUIRED`,
  `__SPECKIT_COMMAND_*__`, `[e.g., …]`, upper-case template tokens, or template HTML comment
  copied verbatim.
- **spec.md**: `- **FR-###**:` / `- **SC-###**:` ids, unique and three-digit; user stories
  `### User Story <n> - <title> (Priority: P<n>)`, numbered from 1, each with *Why this
  priority*, *Independent Test* and a Given/When/Then scenario.
- **tasks.md**: the Spec Kit tasks grammar (through `lib/specstride_spec.py`, not a copy);
  `- [ ] T### [P]? [USn]? description` in that order; unique, increasing ids; Setup, then
  Foundational, then one phase per story in priority order, then Polish; `[USn]` only in its
  own story's phase and only for stories the spec has; no two `[P]` tasks in a phase naming the
  same file; every box ticked for `tasks=done`, none for `tasks=open`.
- **Evidence**, **unknowns** and the **requirements checklist** (every item `[x]`, or `[ ]` with an
  indented `Fails:` note that carries an evidence comment).
- **Coverage** (`--upto 5`): every `FR-###` is named by at least one task.

Without `--src` the evidence citations are not resolved (a warning says so); everything else
still runs.

## Resuming and inspecting

A reverse run is an ordinary feature, `reverse-<slug>`:

```bash
specstride status -w ~/src/my-service --feature reverse-my-service
specstride watch  -w ~/src/my-service
specstride resume -w ~/src/my-service --feature reverse-my-service
```

`resume` replays the saved config: the driver, the verification-commands document, the state-dir
`TEST_PLAN.md` path, and `SPECSTRIDE_GIT_COMMITS=off`.

## Knobs

| Variable | Default | Meaning |
|---|---|---|
| `SPECSTRIDE_REVERSE_MAX_FILE_BYTES` | `1048576` | a larger file is skipped (still fingerprinted) |
| `SPECSTRIDE_REVERSE_MAX_TOTAL_BYTES` | `20971520` | above this much text, phase 1 is split by unit |
| `SPECSTRIDE_REVERSE_TASKS` | `done` | the default for `--tasks` |

They follow the normal precedence: built-in default < `.env` < the caller's environment <
flags.

## Limits

- The linter proves structure and that every citation exists; it cannot prove a claim is
  *true*. The critic reads the cited lines, and you should too.
- Requirement coverage is by id: a task covers `FR-004` when its description names it.
- A gitignored file in a git work tree is outside the inventory, so the guard does not see a
  change to it.
- The non-git walk honours only the root `.gitignore`, as a simple glob subset.
- Only pnpm/npm/yarn and Cargo workspaces are recognised as units.
- The run describes source it can read. Behavior that only exists at run time (configuration
  injected by a platform, data in a database) is at best `inferred`.
