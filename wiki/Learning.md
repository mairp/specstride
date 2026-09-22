# Learning

The learning loop is the outer of Specstride's [three loops](Architecture#three-loops). The inner
Ralph loop and the gated phase loop get a phase approved. The learning loop reads how those
passes went and, across runs, suggests per-phase settings sized to what each phase actually
measured. Without it, you get one global setting sized for the worst phase in the project.

The agents never improve; they stay stateless workers. What improves is how the loop drives
them. The critic is never tuned.

Full design: `roadmap/research/self-improvement-loops/02-wiggum-loop-design.md` §5. Reference:
[README → Learning](../README.md#learning-self-tuning-knobs).

## Turning it on

```bash
# 1. Let runs record per-phase observations (measurement only, changes nothing)
SPECSTRIDE_LEARNING=suggest specstride run ...

# 2. After a few runs, see what the loop would change and the evidence behind it
specstride learn --show

# 3. Apply one decision you agree with (append-only, with provenance)
specstride learn --apply --knob proposer_timeout --phase 8 --default 1800

# 4. Launch runs that read applied values
SPECSTRIDE_LEARNING=apply specstride run ...

# Undo one decision, or all of them
specstride learn --revert <run-id>
specstride learn --off
```

| `SPECSTRIDE_LEARNING` | Observations at `phase_done` | Applied values read by the run |
|---|---|---|
| unset / `off` | no | no — `applied.json` is never opened |
| `suggest` (or any other value) | yes | no |
| `apply` | yes | yes |

## The knobs

| Knob | Suggested from | Bounds | Read by a live run? |
|---|---|---|---|
| `proposer_timeout` | the phase's measured work time (wall time minus declared waiting) | `[900 s, 2 × default]`, at most ±50 % per step | **yes** — `resolve_proposer_timeout`, source `learned`; an operator override still wins |
| `yield_poll_interval` | measured yield job durations, targeting about a tenth of the job's length | `[10 s, 300 s]`, at most ±50 % per step from its own default (30 s) | **yes** — `resolve_yield_poll`, source `learned`; a set `SPECSTRIDE_YIELD_POLL` still wins |

Both values, with their sources, are on every `proposer_cap` event. `specstride learn` passes
the asked knob's own default when you give no `--default`: `SPECSTRIDE_PROPOSER_TIMEOUT`
(else 1800) or `SPECSTRIDE_YIELD_POLL` (else 30).

The design's third knob, `inject_yield_hint`, was removed rather than wired: the yield
contract is already appended to every proposer and accelerator prompt, so it had nothing to
switch, and the only change left to make was to a prompt the critic later judges.

Samples and decisions are keyed on the phase's **shape** — a digest of its number, title and
criteria text, recorded on `phase_start` (`lib/specstride_spec.py shape N`). Ticking a checkbox
leaves it unchanged; editing a criterion or the title changes it, and then the old phase's
samples stop counting and its decisions stop applying. A decision or run recorded before
shapes existed matches no shape and is never silently applied; a one-line notice says so.

Every knob needs at least 3 samples before a value is suggested. A pass killed for futility
(`repeat_stall`, `progress_stall`) never counts as a sample, because its duration says nothing
about how long the work takes.

## Evaluating a decision

`--apply` records a baseline beside the decision (per-pass cost and wall-clock of the samples
that produced it, the phase shape, the backend label, the guardrail counts). At every
`phase_done` `learn.py evaluate` labels each active decision for the phase:

| Label | When |
|---|---|
| `insufficient` | fewer than 6 billed, non-futility passes in either arm, or the shape or backend label changed since the baseline |
| `helped` | log cost or log wall-clock per pass fell by at least the MDE, and neither rose by it |
| `regressed` | either rose by at least the MDE |
| `neutral` | anything in between |

`MDE = 2.80 · s · sqrt(1/n_a + 1/n_b) · sqrt(1 + (m̄ − 1)·0.5)`, `s` floored at 0.50. It is always
printed beside the effect: at 6 passes per arm only a change of roughly ×2–3 in cost is
visible, so `neutral` does not mean "no effect". No significance test is run; none is
attainable at these run counts. Design: `roadmap/research/self-improvement-loops/05-evaluate-design.md`.

Guardrails veto a decision even when it helped, and a breach **reverts it automatically** — the
only automatic write the loop makes, because it only moves a knob back toward its default:
`grounding_gap` rate, MALFORMED rate, a new `verification_failed`, the diagnostician's GROUNDING
share, critic input size (read from the verdict transcripts), a human-arbitration **proxy** (the
stop reasons that hand a phase back), and first-attempt approval rate as an alarm in both
directions. Rates use exact binomial tails (p < 0.01, at least 10 trials); below that a guardrail
is `unknown`, never `ok`. The reverted value is quarantined for its phase, shape and backend:
`--apply` refuses it (exit 4) until 6 new samples exist, unless `--force`. `specstride learn --show`
and every run's exit print each active decision's latest evaluation.

## What can never be tuned

The allowlist is exactly the two knobs above, and `lib/test_learn.py` asserts it literally.
Out of scope, permanently:

- anything the critic reads: grounding caps, backend, timeout;
- `--max-rejects` and everything in `verification-commands.json`;
- every breaker (`SPECSTRIDE_PROPOSER_MAX_ERRORS`, `MAX_NOPROGRESS`, `MAX_CAPS`, `REPEAT_LIMIT`).

A breaker must never be able to relax itself, and a loop that could tune its own judge would
drift toward approving its own work. Adding a name to the allowlist means editing the test that
says why it must not be there. A second test locks every place `learn.py` is invoked: the run's resolve
and observe calls in `orchestrator.sh` and the `specstride learn` dispatcher, never the critic or
the verification plan.

## On disk

| File | Written by | What it holds |
|---|---|---|
| `.specstride/features/<slug>/learning/phase-<N>.json` | `learn.py observe`, at `phase_done` | the phase's cross-run metrics (an **observation**); idempotent, best-effort, never fails the phase |
| `.specstride/features/<slug>/learning/applied.json` | `specstride learn --apply/--revert/--off`, and `learn.py evaluate` (evaluate entries only) | an append-only log of **decisions** and their evaluations, each decision with its baseline, the phase shape, the run ids and sample count behind it and the previous value |

Observations and decisions are kept in separate files so a measurement can never be mistaken
for a decision. Events: `learning_observed` (an observation was written), `knob_adjusted`
(a decision was applied or reverted) `knob_evaluated` (a decision was labelled) and `knob_auto_reverted` (a guardrail reverted one). See [On-Disk Contract](On-Disk-Contract).
