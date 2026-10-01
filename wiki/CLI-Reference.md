# CLI Reference

`specstride` is the **single front door**. Starting a run and inspecting it are the same command;
the script owns the routing.

- `specstride run …` (or `specstride start …`) **starts** the loop by `exec`ing `orchestrator.sh`.
- A **leading flag** — `specstride -w DIR -s SPEC …` — also starts.
- Any reserved **inspection verb** (`status`, `watch`, …) or `-h/--help` runs the inspection CLI.

Everything is read-only **except `stop`, `resume` and `reverse`** — the subcommands that mutate
(they write `stop.flag` / relaunch the orchestrator / start a reverse-engineering run).

All inspection subcommands take `--feature SLUG` and default to the **last run's feature**
(from `.specstride/last-run.conf`); `status --all` spans every feature.

## Inspection & control verbs

| Command | Shows / does |
|---|---|
| `specstride status [-w DIR] [-s SPEC] [--feature S] [--all]` | one-screen state: run-state headline (RUNNING / STOPPED / HALTED / DONE) + current phase + a ✓/✗ table of which contract files exist. `--all` lists every feature with approved/total phase counts |
| `specstride phases [-w DIR] [-s SPEC] [--feature S]` | phases parsed from the spec + each one's state (also lints the spec) |
| `specstride tail [-w DIR] [--feature S]` | `tail -f` the orchestrator `run.log` (raw log) |
| `specstride events [-w DIR] [--feature S] [-f\|--follow] [--json]` | the raw event stream ("RPC view"): every milestone **and** every agent tool call / message as `HH:MM:SS event key=value…` lines. `--follow` streams; `--json` emits raw JSONL |
| `specstride verdicts [-w DIR] [--feature S] [N]` | the critic's full reply(ies): prompt + response + parse decision |
| `specstride feedback <N> [-w DIR] [--feature S]` | `GATE<N>-FEEDBACK.md` |
| `specstride watch [-w DIR]` | the live status card (heartbeat + run totals) |
| `specstride doctor [-w DIR] [--proposer B] [--critic B] [--proposer-inherit-plugins] [--strict]` | read-only: what a run in `DIR` would start with. It shows the `claude` child's harness flags (pinned or inherited), the critic's resolved model and prompt window, and the [preflight](Configuration#harness-isolation-claude) warnings (unignored plugin files under `.claude/`, another live Claude Code session with hooks in the workdir). Reads `.env` like a run. Exit 0; `--strict` exits 3 when anything warns |
| `specstride stop [-w DIR] [--now]` | **(mutates)** request a clean stop — writes `stop.flag`; the run finishes its current pass and exits 6. `--now` also kill-trees the in-flight proposer pass so it stops within seconds |
| `specstride learn [-w DIR] [--feature S] [--show\|--apply\|--revert <run-id>\|--off\|--summarize\|--evaluate]` | the [learning loop](Learning) over this feature's telemetry. `--show` (default), `--summarize` (the metric JSON) and `--evaluate` (what evaluation would conclude now; always a dry run) are read-only; `--apply`, `--revert` and `--off` **(mutate)** `learning/applied.json` only |
| `specstride reverse <SRC> [--out DIR] [--name SLUG] [--tasks done\|open] [--dry-run] [launch flags…]` | **(mutates** only the output dir and `SRC/.specstride/`**)** reverse-engineer `SRC`, read-only, into a Spec Kit feature directory describing it as-is (default `SRC/specs/NNN-as-is-<slug>/`). Also spelled `specstride --reverse <SRC> …`. `--dry-run` prints the inventory summary, output dir, phases and the exact orchestrator argv without an LLM call. See [Reverse Engineering](Reverse-Engineering) |
| `specstride reverse --lint <FEATURE_DIR> [--src SRC] [--upto N] [--json]` | the deterministic Spec Kit linter alone, on any feature directory (hand-written ones too); exit 3 on errors |
| `specstride resume [-w DIR] [--feature S] [overrides…]` | **(mutates)** relaunch the orchestrator from the last run's saved config (`.specstride/last-run.conf`); refuses if a run is already active. Extra args override the saved flags (last-wins) |

## Starting a run

`specstride run` `exec`s [`orchestrator.sh`](../orchestrator.sh); pass `--help` after `run` for
the full launch-flag list. To start from an existing codebase with no spec yet,
`specstride reverse <SRC>` writes one and launches the run itself: it sets `-w`, `-s`,
`--spec-format`, `--feature`, `--verification-commands`, `--test-plan` and `--generate-tests`,
and passes the other launch flags below through (see [Reverse Engineering](Reverse-Engineering)).
Common launch flags:

| Flag | Meaning |
|---|---|
| `-w/--workdir DIR` | where the proposer works (default `$PWD`) |
| `-s/--specs FILE` | the spec, normally `specs/<feature>/tasks.md`; `run` discovers it inside a Spec Kit project (a legacy `<workdir>/SPECS.md` is still checked first) |
| `--feature SLUG` | feature namespace under `.specstride/features/` |
| `--spec-format native\|speckit-tasks\|openspec-change` | force the spec format (else auto-detected) |
| `--start-phase N` | override the derived starting phase |
| `--verification off\|plan\|required` | pre-loop test automation; default `required` (see [Configuration](Configuration)) |
| `--test-plan /abs/path` · `--generate-tests /abs/dir` | override feature-scoped projection/scaffolds; paths must resolve inside workdir |
| `--live` / `--no-live` · `--no-color` | live presenter control |
| `--debug` · `--telemetry` · `--loki-url URL` · `--otel` · `--otel-url URL` | debug + [Telemetry](Telemetry) |
| `--proposer-inherit-plugins` | a `claude` proposer/accelerator pass loads your Claude Code plugins, hooks, MCP servers and CLAUDE.md instead of running pinned (see [Configuration](Configuration#harness-isolation-claude)); kept on `resume`; learning evaluation excludes the run. Also `SPECSTRIDE_PROPOSER_INHERIT_PLUGINS=1` |

## Feature-awareness

Spec Kit numbers every feature's `tasks.md` from 1 and builds them all into one repo. Specstride
keeps each feature's gates independent under `.specstride/features/<slug>/`, selected with
`--feature SLUG` (or `SPECSTRIDE_FEATURE`):

```bash
specstride run    -w ./ --feature 001-login     # run one feature to completion
specstride run    -w ./ --feature 002-billing   # then the next — independent gates
specstride status -w ./ --all                    # see both, side by side
```

`resume --feature X` replays that feature's saved config (preserving its `SPEC_FORMAT`).

## Appearance

`specstride run` opens with the Specstride mark and a gate rail showing each phase's state
(`✓` approved, `✗` rejected, `•` current, `·` pending). It animates for under 700 ms, once,
and only on an interactive terminal. `run.log` always gets a plain ASCII copy. Colors are six
roles defined in [`lib/theme.py`](../lib/theme.py); the live presenter uses the same roles, and
re-stamps the same rail under the timeline line that moved a gate.

| Switch | Effect |
|---|---|
| `SPECSTRIDE_BANNER=off` | no splash at all (CI logs, screen readers); the log copy is skipped too |
| `SPECSTRIDE_LIVE_RAIL=on\|off` | re-stamp the gate rail in the live timeline (and a plain copy in `run.log`, via `banner.py --rail-only`) each time a gate opens or holds: after an approved phase's `phase_done`, a `REJECTED` verdict, a halt, and run complete. Default `on`; `SPECSTRIDE_BANNER=off` turns it off too. Never shown in `specstride events` or with `--quiet` |
| `SPECSTRIDE_MOTION=0` | never animate; the final frame prints alone. Motion is also off when `CI` is set, `TERM=dumb`, or stdout isn't a TTY |
| `SPECSTRIDE_COLOR=16\|256\|truecolor\|none` | override the detected color depth |
| `SPECSTRIDE_ASCII=1` | ASCII glyphs only (also automatic under a non-UTF-8 locale) |
| `SPECSTRIDE_BANNER_BG=dark\|light` | skip background detection (the orchestrator otherwise detects it once per run; inside tmux or screen it doesn't query the terminal) |
| `NO_COLOR` | no color anywhere (splash and presenter) |
| `FORCE_COLOR` | color even when stdout isn't a TTY |
| `--no-color` | presenter flag; same as `NO_COLOR` for the live view |

Color depth is resolved in this order: an explicit `--color` flag, `NO_COLOR`, `FORCE_COLOR`,
not-a-TTY or `TERM=dumb`, `COLORTERM=truecolor`, a `256color` `TERM`, then 16 colors;
`SPECSTRIDE_COLOR` overrides the result. `python3 lib/banner.py --preview` prints every width
tier, background and depth for review.

Next: [On-Disk Contract](On-Disk-Contract) · [Hardening](Hardening)
