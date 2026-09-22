# Contract: files and environment

| Surface | Contract |
|---|---|
| `TALLY_FILE` | when set and non-empty, the path of the counter file |
| `~/.tally.json` | the counter file when `TALLY_FILE` is unset |
| `<counter file>.tmp` | transient; exists only during a save |

The counter file is a JSON object of string → integer (see `data-model.md`).
