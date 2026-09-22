---

description: "As-is task baseline for tally (reverse-engineered)"
---

<!-- specstride-reverse: tasks=done -->

# Tasks: tally — named counters in one JSON file (as-is)

**Input**: Design documents from `specs/001-as-is-src-mini/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: the source has one test module; the test task below records it.

**Organization**: Tasks are grouped by user story. Every task is ticked: it records
work the evidence shows already exists.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `tally/`, `tests/` at repository root

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Declare the package, the `tally` console script and the pytest configuration in `pyproject.toml`
- [x] T002 [P] Record the package version in `tally/__init__.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the counter file every story reads or writes

- [x] T003 Implement the counter-file location, load and atomic save in `tally/store.py` (FR-005, FR-006)

**Checkpoint**: Foundation ready

---

## Phase 3: User Story 1 - Count something from the shell (Priority: P1) 🎯 MVP

**Goal**: add to a named counter from the shell

**Independent Test**: `tally add coffee` twice reads back `coffee: 2`

- [x] T004 [US1] Implement the `add` subcommand and its amount check in `tally/cli.py` (FR-001, FR-002)

---

## Phase 4: User Story 2 - Review every counter and the total (Priority: P2)

**Goal**: print every counter and the total

**Independent Test**: `tally show` prints sorted lines and the total

- [x] T005 [P] [US2] Implement the total and the sorted rendering in `tally/report.py` (FR-003)
- [x] T006 [P] [US2] Test the rendering order and the empty total in `tests/test_report.py`
- [x] T007 [US2] Wire the `show` subcommand in `tally/cli.py`

---

## Phase 5: User Story 3 - Remove a counter (Priority: P3)

**Goal**: delete a counter

**Independent Test**: `tally reset coffee` removes it

- [x] T008 [US3] Implement the `reset` subcommand and its unknown-name error in `tally/cli.py` (FR-004)

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: documentation

- [x] T009 [P] Document installation, the commands and `TALLY_FILE` in `README.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup; blocks every story
- **User Stories (Phases 3–5)**: depend on Foundational; independent of each other
  except that T007 and T008 edit `tally/cli.py` after T004
- **Polish (Phase 6)**: depends on the stories it documents

### Parallel Opportunities

- T002 runs beside T001; T005 and T006 touch different files

## Parallel Example: User Story 2

```bash
Task: "Implement the total and the sorted rendering in tally/report.py"
Task: "Test the rendering order and the empty total in tests/test_report.py"
```

## Implementation Strategy

As-is baseline: every task is done. `/speckit.converge` or a later Specstride run
extends this list rather than rebuilding it.

## Notes

- Task order follows the dependency order recovered from imports: `tally/cli.py`
  imports `tally/store.py` and `tally/report.py`.
