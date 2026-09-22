# Getting Started

Specstride is a **utility you install once**; your project lives elsewhere. Each run points at
your project with `-w/--workdir` and (optionally) `-s/--specs`.

## Requirements

- `bash` and `python3` (stdlib only — no pip, no dependency manager)
- An LLM backend key. On `main` the proposer defaults to **`dsh`** (DeepSeek Harness's
  configured model), while the critic defaults to **`claude`** (Messages API). Configure
  `$DSH_HOME/settings.yaml` and the corresponding provider credentials.

## Quick start

```bash
cp .env.example .env          # then edit: set ANTHROPIC_API_KEY

# one-time: add the thin specstride() pointer to ~/.bashrc (see below), then:
source ~/.bashrc

mkdir -p /tmp/specstride-demo/specs/001-greeting
cp examples/speckit-tasks.example.md /tmp/specstride-demo/specs/001-greeting/tasks.md
specstride run -w /tmp/specstride-demo       # discovers specs/001-greeting/tasks.md
```

Not set up the alias yet? Call the script directly:
`"$SPECSTRIDE_HOME"/specstride run -w /tmp/specstride-demo`, or `./specstride run -w /tmp/specstride-demo` from
inside the clone.

The bundled `examples/speckit-tasks.example.md` is a small, verifiable Spec Kit task list, so
you can watch the whole loop end to end. In a real project, generate the feature with Spec Kit
(`/speckit.specify`, `/speckit.plan`, `/speckit.tasks`) and point Specstride at it.

## Install it permanently (one `specstride` command)

The `specstride` script is already the single front door for *everything* — it owns the routing
itself. So your shell rc only needs a **thin pointer**, no dispatch logic to keep in sync.

Add to `~/.bashrc` (or `~/.zshrc`):

```bash
# ── Specstride ─────────────────────────────────────────────────────────────
export SPECSTRIDE_HOME="/root/specstride"          # wherever you cloned it — set once
export SPECSTRIDE_LIVE_DETAIL=full             # richest live view

# `specstride` owns its own run-vs-inspect routing, so this is just a pointer.
specstride() { "$SPECSTRIDE_HOME/specstride" "$@"; }
# ───────────────────────────────────────────────────────────────────────
```

Reload once (`source ~/.bashrc`) and the one command drives everything, from any directory:

```bash
specstride run -w ~/projects/foo -s ~/projects/foo/ROADMAP.md   # START a loop
specstride -w ~/projects/foo -s ~/projects/foo/ROADMAP.md       # …same, leading flag
specstride status -w ~/projects/foo                             # inspect it
specstride watch  -w ~/projects/foo                             # live status card
specstride stop   -w ~/projects/foo                             # clean halt
```

> **Why a function, not a symlink/PATH shim?** The scripts locate their own `lib/` and
> `specstride-lib.sh` via `dirname "${BASH_SOURCE[0]}"`, which does **not** dereference symlinks.
> A `ln -s … /usr/local/bin/specstride` would resolve its home to `/usr/local/bin` and fail. The
> function calls the real absolute path under `$SPECSTRIDE_HOME`. (Prefer PATH?
> `export PATH="$SPECSTRIDE_HOME:$PATH"` also works.)

## Pointing at your project

- **`-w/--workdir DIR`** — where the proposer works. All generated state lives under
  `.specstride/features/<slug>/`, so the workdir root holds only your real artifacts. Default: `$PWD`.
- **`-s/--specs FILE`** — the spec, normally a Spec Kit feature's `specs/<feature>/tasks.md`.
  Inside a Spec Kit project `run` discovers it, so you can leave `-s` out. A relative path
  resolves against the directory you launched from, not the workdir.
- **`--feature SLUG`** — the feature namespace for durable state, for repos with more than one
  Spec Kit feature. Default: the feature dir's basename, or `default`.

```bash
specstride run -w ~/projects/foo -s ~/projects/foo/ROADMAP.md
```

## Live visibility (on by default)

A backgrounded loop is not a black box. Every meaningful step emits one structured event and
a presenter renders it in real time, in full color, with zero containers.

- **Inline timeline (`--live`, auto-on at a TTY):** a colored scrolling timeline right in
  your terminal while the noisy raw output goes to `run.log`. Each tool call gets its own
  color and glyph (Read `◎`, Write `✚`, Edit `✎`, Bash `❯`, …); end-of-pass lines show
  cost / tokens / duration / turns.
- **Live status card (`specstride watch`):** a compact header (phase progress + activity +
  heartbeat) over a scrolling recent-activity feed. Attach to a backgrounded run.

Verbosity is `SPECSTRIDE_LIVE_DETAIL` (`milestones | tools | full`; default `tools`). `full` adds
each assistant thinking/narration line on top of the tool calls:

```bash
SPECSTRIDE_LIVE_DETAIL=full specstride run -w ~/projects/foo --live
```

## Pre-loop test automation

Specstride derives and executes a Lisa-compatible `VerificationPlan v1` by default before the
first proposer pass:

- `--verification required` — the default; creates the plan, injects its obligations, runs
  fixed-argv tests before approval, and runs the cumulative release gate.
- `--verification plan` — creates and injects the plan without executing its gates.
- `--verification off` — explicitly disables verification.

Default projections and scaffolds are isolated under
`<workdir>/testautomation/<feature>/`. Operator overrides must be absolute, resolve inside
that workdir, and not target a final-path symlink. Planning can also run independently
via `lib/verification_plan.py create …` before any loop. See [Configuration](Configuration).

Next: [CLI Reference](CLI-Reference) · [Spec Formats](Spec-Formats)
