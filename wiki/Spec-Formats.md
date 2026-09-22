# Spec Formats

Specstride parses the spec through a **single pluggable layer** — [`lib/specstride_spec.py`](../lib/specstride_spec.py),
the one source of truth both the bash side and the critic call. **A Spec Kit `tasks.md` is the
input to use.** OpenSpec changes are also supported, and the older hand-written `SPECS.md`
format still works but will be deprecated soon. The format is **auto-detected**, or forced with
`--spec-format` / `SPECSTRIDE_SPEC_FORMAT`.

## `speckit-tasks` (recommended)

A [GitHub Spec Kit](https://github.com/github/spec-kit) `tasks.md`. Each `## Phase N:` heading
becomes a Specstride phase, and every `- [ ]` task line under it becomes a required deliverable the
critic gates on (the task's cited file paths are exactly what the grounding pass verifies):

```markdown
## Phase 2: User Story 1 - <title> (Priority: P1)
### Implementation for User Story 1
- [ ] T003 [US1] Implement greet(name) in src/greet.py
- [ ] T004 [US1] Add a __main__ block to src/greet.py
```

Specstride also accepts implementations that group executable tasks under priority headings such
as `## P0 — Safety`, `## P1 — Contracts`. Each task-bearing priority section becomes an ordered
phase with a unique gate id. Trailing shared sections such as `## Dependency order` and
`## Definition of done` are included in every normalized phase's context.

When the `tasks.md` lives inside a Spec Kit project (a `.specify/` directory above it), the
feature's **full design-doc set** is injected into both the proposer prompt and the critic as
**read-only context** — they explain the *why/how* and are the documents a grounding claim is
verified against, but only the tasks are gated. In **descending gating value** (the order the
context budget truncates from the tail):

`constitution.md` → `spec.md` → `plan.md` → every `contracts/*.md` → `data-model.md` →
`research.md` → `quickstart.md` → every `checklists/*.md`.

The total injected context respects `SPECSTRIDE_CONTEXT_BUDGET` (default ~24000 chars), allocated
in that priority order with per-doc floors — so a large `plan.md` cannot starve `contracts/` —
and truncation is line-clean and code-fence-safe.

Runnable example: [`examples/speckit-tasks.example.md`](../examples/speckit-tasks.example.md).

```bash
mkdir -p /tmp/specstride-speckit && cp examples/speckit-tasks.example.md /tmp/specstride-speckit/tasks.md
specstride run -w /tmp/specstride-speckit -s /tmp/specstride-speckit/tasks.md
```

## `openspec-change`

An active [OpenSpec](https://github.com/Fission-AI/OpenSpec) change at
`openspec/changes/<change>/tasks.md`. Each numbered level-2 task group becomes a phase and its
dotted checkbox items become required deliverables:

```markdown
## 1. Domain contract
- [ ] 1.1 Add the export requirement.
- [ ] 1.2 Add empty and populated-log scenarios.

## 2. Implementation
- [ ] 2.1 Implement the exporter in `src/audit/export.py`.
```

The change name becomes the feature-scoped Specstride state slug. Specstride injects the change's
`proposal.md`, every delta `specs/**/spec.md`, `design.md`, and matching current
`openspec/specs/**/spec.md` documents into both proposer and critic as read-only context. The
task list remains the gate; Specstride does not sync or archive the OpenSpec change.

Canonical OpenSpec paths are detected before the generic `tasks.md` filename rule. The numbered
task shape is also content-detected when the file has another name. Example:
[`examples/openspec-tasks.example.md`](../examples/openspec-tasks.example.md).

## `native` (legacy, to be deprecated soon)

A hand-written `SPECS.md` where each phase is a level-2 heading whose text starts with
`Phase <N>`, containing an `### Acceptance criteria` block. It is still the fallback when nothing
else is detected, so existing `SPECS.md` projects keep running; new work should use a Spec Kit
feature:

```markdown
## Phase 0 — <title>
<description of the work>

### Acceptance criteria
- [ ] criterion one
- [ ] criterion two
```

## Spec resolution (zero-flag start)

Inside a Spec Kit or OpenSpec project you rarely need `-s`. When it is omitted, Specstride resolves
the spec in this order (never silently picking between candidates):

1. `<workdir>/SPECS.md`, if a legacy one exists — checked first so existing projects are
   unaffected; remove it once the work has moved to a Spec Kit feature.
2. `<workdir>/.specify/feature.json` → its `feature_directory` → `<dir>/tasks.md`.
3. discover `<workdir>/specs/*/tasks.md` and `<workdir>/openspec/changes/*/tasks.md` — exactly
   one match is used; two or more with no `--feature` exits `E_SPEC` (3), listing every
   candidate with the `-s` and `--feature` forms to disambiguate.
4. none of the above → an error naming every location tried.

```bash
specstride run -w ./            # resolves specs/001-.../tasks.md, no -s
```

## `tasks.md` is the source of truth

Write the work as a Spec Kit feature and let Spec Kit own its `tasks.md`; it is generated from the
feature's `spec.md` and `plan.md`. Never keep a hand-written `SPECS.md` beside it for the same
work: `SPECS.md` is checked first and would become a second, un-reconciled source of truth.
`SPECS.md` remains readable for existing projects but will be deprecated soon; move non-feature
work (migrations, refactors, ops roadmaps) into a Spec Kit feature too.

Gate approvals live in `.specstride/features/<slug>/gates/`, which is what "is phase N done"
means. When the critic approves a phase, Specstride also ticks that phase's task checkboxes in
`tasks.md` (`SPECSTRIDE_TICK_TASKS=false` turns that off); checkbox state is outside the plan
hash and never re-plans anything.

Next: [On-Disk Contract](On-Disk-Contract) · [Architecture](Architecture)
