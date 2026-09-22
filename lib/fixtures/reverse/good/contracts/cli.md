# Contract: the `tally` command line

Entry point: console script `tally` → `tally.cli:main` (`pyproject.toml` line 12).

| Command | Arguments | Output | Exit |
|---|---|---|---|
| `tally add NAME [AMOUNT]` | AMOUNT integer, default 1, at least 1 | `NAME: VALUE` | 0; 2 when AMOUNT < 1 |
| `tally show` | none | one `name: value` line per counter, sorted, then `total: N` | 0 |
| `tally reset NAME` | none | nothing | 0; 1 when NAME is unknown |

Errors are printed on stderr prefixed `tally: `. argparse usage errors exit 2.
