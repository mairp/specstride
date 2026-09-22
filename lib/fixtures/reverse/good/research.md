# Research: tally as built

Each entry is a decision the code shows, with its rationale where the source states
one. A rationale that cannot be recovered is written `unknown`.

## 1. Storage

- **Decision**: all counters live in one JSON object on disk, loaded whole and rewritten whole on every change.
<!-- evidence: tally/store.py:13-32 | kind=observed | confidence=0.95 | contradicts=none | validation=verified -->
  **Rationale**: unknown — no comment or document states why a file was chosen over a database.
  **Alternatives**: unknown.

## 2. Atomic writes

- **Decision**: a save writes `<path>.tmp` and renames it over the counter file.
<!-- evidence: tally/store.py:25-32 | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->
  **Rationale**: the docstring says the write is atomic; a crash mid-write leaves the old file intact.
  **Alternatives**: writing in place (not used).

## 3. Command-line parsing

- **Decision**: argparse subcommands, one per operation (`add`, `show`, `reset`).
<!-- evidence: tally/cli.py:8-17 | kind=observed | confidence=0.95 | contradicts=none | validation=verified -->
  **Rationale**: unknown.
  **Alternatives**: unknown.
