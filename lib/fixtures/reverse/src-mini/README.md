# tally

Keep a running tally of named counters in one JSON file.

```bash
pip install .
tally add coffee        # coffee: 1
tally add coffee 2      # coffee: 3
tally show              # coffee: 3 / total: 3
tally reset coffee
```

Counters live in `~/.tally.json`; set `TALLY_FILE` to use another file.

Run the tests with `pytest`.
