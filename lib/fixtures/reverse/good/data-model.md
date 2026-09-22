# Data Model: tally

## Entities

### Counter
<!-- evidence: tally/store.py:22 | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->

| Field | Type | Rule |
|---|---|---|
| name | string | unique key in the counter file |
| value | integer | starts at 0, grows by `add`; `add` refuses amounts below 1 |

State transitions: absent → created by `add` → updated by `add` → removed by `reset`.

### Counter file
<!-- evidence: tally/store.py:5-10, tally/store.py:20-21 | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->

One JSON object mapping name → value, written with sorted keys and a two-space
indent. Location: `$TALLY_FILE`, else `~/.tally.json`. Any other top-level JSON value
is rejected with `ValueError`.
