# tally Constitution

Recovered from evidence in the source tree; nothing here is aspirational.

## Core Principles

### I. Standard library only

The package declares no runtime dependencies; only the test extra pulls in pytest
(`pyproject.toml` lines 6–9).

### II. One command, one file

Every behavior is reached through the `tally` console script, and all state is one
JSON file (`pyproject.toml` lines 11–12, `tally/store.py`).

### III. Tested with pytest

Tests live under `tests/` and run with pytest in quiet mode (`pyproject.toml` lines
14–16).

## Constraints

- Python 3.10 or newer (`pyproject.toml` line 5).

## Development Workflow

No CI configuration, lint configuration or contribution guide exists in the tree;
the only recoverable gate is the test suite.

## Governance

No amendment process is recorded in the source.

**Version**: 1.0.0 | **Ratified**: unknown | **Last Amended**: 2026-09-22
