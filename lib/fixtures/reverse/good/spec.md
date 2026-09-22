# Feature Specification: tally — named counters in one JSON file (as-is)

**Feature Branch**: `001-as-is-src-mini`

**Created**: 2026-09-22

**Status**: As-is (reverse-engineered from the source; describes current behavior only)

**Input**: Reverse-engineered by `specstride reverse` from the source tree `src-mini`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Count something from the shell (Priority: P1)

A person keeps a running count of named things (cups of coffee, pull requests
reviewed) by typing one command per occurrence.

**Why this priority**: adding to a counter is the only way data enters the tool;
every other command reads what `add` wrote.

**Independent Test**: run `tally add coffee` twice against an empty counter file and
read back `coffee: 2`.

**Acceptance Scenarios**:

1. **Given** no counter named `coffee`, **When** the user runs `tally add coffee`, **Then** the counter is created at 1 and `coffee: 1` is printed.
2. **Given** `coffee` is 1, **When** the user runs `tally add coffee 2`, **Then** `coffee: 3` is printed and saved.
3. **Given** any counter, **When** the user runs `tally add coffee 0`, **Then** `tally: amount must be at least 1` is printed on stderr and the exit status is 2.

---

### User Story 2 - Review every counter and the total (Priority: P2)

The person prints all counters at once, in a stable order, with their sum.

**Why this priority**: it is the only read path; without it the counts are only
visible by opening the JSON file.

**Independent Test**: with `a` = 1 and `b` = 2 saved, `tally show` prints
`a: 1`, `b: 2`, `total: 3` on three lines.

**Acceptance Scenarios**:

1. **Given** counters `b` = 2 and `a` = 1, **When** the user runs `tally show`, **Then** the lines are sorted by name and end with `total: 3`.
2. **Given** no counter file, **When** the user runs `tally show`, **Then** only `total: 0` is printed.

---

### User Story 3 - Remove a counter (Priority: P3)

The person deletes a counter they no longer track.

**Why this priority**: housekeeping; the tool is usable without it.

**Independent Test**: with `coffee` saved, `tally reset coffee` leaves a file without
`coffee`.

**Acceptance Scenarios**:

1. **Given** `coffee` exists, **When** the user runs `tally reset coffee`, **Then** it is removed from the file and nothing is printed.
2. **Given** no counter named `tea`, **When** the user runs `tally reset tea`, **Then** `tally: no counter named tea` is printed on stderr and the exit status is 1.

---

### Edge Cases

- A counter file that holds JSON other than an object makes every command fail with
  `counter file must hold a JSON object`.
- A missing counter file reads as no counters; it is created by the first write.
- Concurrent invocations are not coordinated: the last writer's rename wins.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST add a positive integer amount (default 1) to a named counter, creating it at 0 first when absent, and print the new value.
<!-- evidence: tally/cli.py:11-13, tally/cli.py:23-29 | kind=observed | confidence=0.95 | contradicts=none | validation=verified -->
- **FR-002**: The system MUST refuse an amount below 1 with exit status 2 and change nothing.
<!-- evidence: tally/cli.py:24-26 | kind=observed | confidence=0.95 | contradicts=none | validation=verified -->
- **FR-003**: The system MUST print every counter as `name: value`, sorted by name, followed by `total: <sum>`.
<!-- evidence: tally/report.py:9-13, tests/test_report.py:4-5 | kind=observed | confidence=0.95 | contradicts=none | validation=verified -->
- **FR-004**: The system MUST remove a named counter on reset, and exit 1 with a message when no such counter exists.
<!-- evidence: tally/cli.py:32-37 | kind=observed | confidence=0.95 | contradicts=none | validation=verified -->
- **FR-005**: The system MUST keep the counters in the file named by `TALLY_FILE`, else `~/.tally.json`.
<!-- evidence: tally/store.py:5-10, README.md:14 | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->
- **FR-006**: The system MUST replace the counter file atomically, writing a temporary file and renaming it over the original.
<!-- evidence: tally/store.py:25-32 | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->

### Key Entities *(include if feature involves data)*

- **Counter**: a name and a non-negative integer value; names are unique within the file.
- **Counter file**: one JSON object mapping every counter name to its value.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A count added with one command is visible in the next `show`, with no other step.
<!-- evidence: tally/cli.py:27-31 | kind=observed | confidence=0.85 | contradicts=none | validation=verified -->
- **SC-002**: `show` output is identical for identical counters, whatever order they were added in.
<!-- evidence: tally/report.py:11, tests/test_report.py:4-5 | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->

## Assumptions

- The tool is used by one person at a time; nothing in the source locks the file.
- The README's `pip install .` is the intended installation; no packaging beyond `pyproject.toml` was found.
<!-- evidence: README.md:6, pyproject.toml:11-12 | kind=declared | confidence=0.40 | contradicts=none | validation=unverified -->
