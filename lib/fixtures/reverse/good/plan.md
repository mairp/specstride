# Implementation Plan: tally — named counters in one JSON file (as-is)

**Branch**: `001-as-is-src-mini` | **Date**: 2026-09-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-as-is-src-mini/spec.md`

**Note**: Reverse-engineered: this plan records the implementation that exists.

## Summary

A single-package Python command-line tool. `tally/cli.py` parses three subcommands,
`tally/store.py` reads and atomically rewrites one JSON file, and `tally/report.py`
formats the counters and their total.

## Technical Context

**Language/Version**: Python >= 3.10 (`pyproject.toml` line 5)

**Primary Dependencies**: none at runtime; argparse and json from the standard library

**Storage**: one JSON object in `$TALLY_FILE`, else `~/.tally.json`

**Testing**: pytest, configured in `pyproject.toml` (`testpaths = ["tests"]`)

**Target Platform**: any platform with Python 3.10+ and a home directory

**Project Type**: cli

**Performance Goals**: none stated in the source

**Constraints**: no file locking; the last writer wins

**Scale/Scope**: 4 modules, 1 test module, 3 subcommands

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Standard library only — passes: no runtime dependency is declared.
- II. One command, one file — passes: one console script, one state file.
- III. Tested with pytest — passes for `tally/report.py`; `tally/cli.py` and
  `tally/store.py` have no tests (recorded, not remediated).

## Project Structure

### Documentation (this feature)

```text
specs/001-as-is-src-mini/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
└── tasks.md
```

### Source Code (repository root)

```text
tally/
├── __init__.py      # version
├── cli.py           # argparse entry point (console script `tally`)
├── report.py        # total and rendering
└── store.py         # counter file load/save

tests/
└── test_report.py

pyproject.toml
README.md
```

**Structure Decision**: single project; the package and its tests sit at the root.

## Complexity Tracking

None: the Constitution Check records no violation.
