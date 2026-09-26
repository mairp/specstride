# CLI Reference

`specstride` is the **single front door**. Starting a run and inspecting it are the same command;
the script owns the routing.

- `specstride run …` (or `specstride start …`) **starts** the loop by `exec`ing `orchestrator.sh`.
- A **leading flag** — `specstride -w DIR -s SPEC …` — also starts.
- Any reserved **inspection verb** (`status`, `watch`, …) or `-h/--help` runs the inspection CLI.

Everything is read-only **except `stop` and `resume`** — the only two subcommands that mutate
a run (they write `stop.flag` / relaunch the orchestrator).

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
| `specstride stop [-w DIR] [--now]` | **(mutates)** request a clean stop — writes `stop.flag`; the run finishes its current pass and exits 6. `--now` also kill-trees the in-flight proposer pass so it stops within seconds |
| `specstride learn [-w DIR] [--feature S] [--show\|--apply\|--revert <run-id>\|--off\|--summarize\|--evaluate]` | the [learning loop](Learning) over this feature's telemetry. `--show` (default), `--summarize` (the metric JSON) and `--evaluate` (what evaluation would conclude now; always a dry run) are read-only; `--apply`, `--revert` and `--off` **(mutate)** `learning/applied.json` only |
| `specstride resume [-w DIR] [--feature S] [overrides…]` | **(mutates)** relaunch the orchestrator from the last run's saved config (`.specstride/last-run.conf`); refuses if a run is already active. Extra args override the saved flags (last-wins) |

## Starting a run

`specstride run` `exec`s [`orchestrator.sh`](../orchestrator.sh); pass `--help` after `run` for
the full launch-flag list. Common launch flags:

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
roles defined in [`lib/theme.py`](../lib/theme.py); the live presenter uses the same roles.

| Switch | Effect |
|---|---|
| `SPECSTRIDE_BANNER=off` | no splash at all (CI logs, screen readers); the log copy is skipped too |
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
