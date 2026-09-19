# Specstride Wiki

**A self-driving, spec-driven Ralph loop with an agent pairing gate and telemetry.**

You hand Specstride a spec — an ordered set of phases, each with acceptance criteria — and it
drives a coding agent phase by phase. *Nothing advances until a critic approves it.* The
human who used to eyeball each phase and click "approved" is replaced by an LLM-backed
critic. You stay out of the inner loop and only arbitrate the phases the machines genuinely
can't settle.

Specstride is one author's implementation and interpretation of the **"Ralph" technique** —
automating software development by running a coding agent in a repeating, self-checking
loop — coined by [Geoffrey Huntley](https://ghuntley.com/). It grew out of running that loop
by hand: driving the agent phase by phase, reading each phase's evidence, and approving the
gate before the next phase could start. Specstride takes over that seat, and it goes further
than a plain Ralph loop in two ways:

- **An automated critic gate.** An LLM critic checks each phase's evidence against the spec's
  acceptance criteria and the real code. Nothing advances until the critic approves it.
- **A diagnose-and-accelerate micro-loop for stuck phases.** A plain Ralph loop retries a
  rejected phase from scratch, so a stuck phase can burn pass after pass and learn nothing
  new. When a phase stalls on a new set of unmet criteria, the **diagnostician** reads the
  full rejection history and the untruncated files, then says whether the critic simply
  couldn't see the code or the gap is real, and what to change. The **accelerator** acts on
  that hint: a narrowed retry that fixes only the failing criteria and leaves approved work
  untouched. On one real 12-task phase, a full retry had spent 16 minutes re-deriving what
  turned out to be a two-file fix. See [Architecture](Architecture#the-roles).

You write the spec; the loop takes it to verified code.

> **Runtime is bash + python3 stdlib** — no pip, no dependency manager, clone-and-run.

## The roles

Literal role names are used everywhere — code, files, flags, env vars. The
**orchestrator** (`orchestrator.sh`) drives the **proposer** (`proposer.sh`,
`--proposer`, `SPECSTRIDE_PROPOSER`) and the **critic** (`lib/critic.py`, `--critic`,
`SPECSTRIDE_CRITIC`). Two more passes reuse those scripts when a phase gets stuck: the
**diagnostician** (`lib/critic.py --diagnose`) and the **accelerator**
(`proposer.sh --role accelerator`) — see [Architecture](Architecture).

## Wiki map

| Page | What it covers |
|---|---|
| [Architecture](Architecture) | The proposer → critic → gate loop, the sequence, why there's no file-watcher |
| [Getting Started](Getting-Started) | Clone, key, alias, first run; the bundled two-phase demo |
| [CLI Reference](CLI-Reference) | The single `specstride` front door: `run` + every inspection verb |
| [Spec Formats](Spec-Formats) | `native`, `speckit-tasks`, `openspec-change`; auto-detection and resolution |
| [On-Disk Contract](On-Disk-Contract) | `.specstride/` layout, feature-scoped state, the event stream |
| [Hardening](Hardening) | Nonce-bound verdicts, grounded critic, stale-evidence rule, exit codes |
| [Telemetry](Telemetry) | Optional Loki and OpenTelemetry backends (dual-ship) |
| [Configuration](Configuration) | `.env` precedence, backends, key knobs, branches |

## The relationship to Lisa

**Lisa** is the single-language (TypeScript) successor to this Bash/Python system. Specstride
remains the read-only *behavioral baseline* that Lisa's characterization suite pins parity
against. If you are choosing between them: Specstride is the clone-and-run, stdlib-only original;
Lisa is the platform build-out with a scheduler, durable approvals, plugins, and a typed
core. See the [Lisa wiki](https://github.com/mairp/lisa/wiki).

## Repository

- Front-door script: [`specstride`](../specstride) → routes to `orchestrator.sh` or the inspection CLI
- Orchestrator: [`orchestrator.sh`](../orchestrator.sh) · Proposer: [`proposer.sh`](../proposer.sh)
- Python components (critic, presenter, spec parser, shippers): [`lib/`](../lib)
- Full narrative reference: [`README.md`](../README.md) · License: Apache-2.0
