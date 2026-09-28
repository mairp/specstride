# Stream tap goldens

These files pin the exact bytes the stream tap (`lib/agent_stream.py`) emits for
the two shipped stream formats: `claude` (Claude stream-json) and `prime-v3`
(Prime session schema v3). Each input stream is replayed through the tap in two
modes and its output committed:

- `<name>.legacy.events.golden` / `<name>.legacy.stdout.golden` — a bare,
  contextless tap run (no invocation context, no terminal sidecar).
- `<name>.correlated.events.golden` / `<name>.correlated.stdout.golden` /
  `<name>.correlated.sidecar.golden` — the same run with the full invocation
  context and the provider-terminal sidecar enabled.

## What is pinned

The goldens pin the tap's own output bytes: the `events.jsonl` records, the
human-readable stdout display lines, and (correlated mode) the
`provider-terminal.json` sidecar. Any refactor that changes what the tap writes
for these inputs will fail the golden test (`lib/test_stream_golden.py`).

## Provenance

The goldens were captured on the **unmodified tap**: the tree these files were
added to is the direct child of its parent commit, whose tap code is byte-equal
to public `main` commit `dc7ec21`. No adapter, dispatch, or event-shape code was
changed before capture. (No local-only commit hashes are cited here; `dc7ec21`
is a public `main` commit.)

## Regenerating

```sh
python3 lib/fixtures/stream-golden/capture.py
```

This replays every input under `claude/` (Claude inputs) and
`../../fixtures/prime-v3/` (Prime inputs, read in place, never copied) in both
modes and rewrites every `.golden` file.

## Masking rule

Event records carry wall-clock values under the `ts` and `time` keys. At capture
time those two values are replaced, byte-wise via regex, by fixed same-shape
tokens (`"ts":"0.000000"`, `"time":"1970-01-01T00:00:00+0000"`). Key order,
separators and the `sequence` field are untouched, so the goldens stay
byte-stable across runs while still pinning everything else.

## When to regenerate

Goldens are regenerated **only** when an intended change to the tap's output is
reviewed and merged on purpose — never to make a refactor pass. A refactor must
leave these bytes identical; regenerating goldens to make a refactor green is a
behaviour change and must go through review as one.
