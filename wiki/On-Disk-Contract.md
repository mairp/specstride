# On-Disk Contract

The spec (`SPECS.md` or a Spec Kit `tasks.md`) is the one input **you** write; it can live
anywhere. Everything else Specstride generates lives under `.specstride/`, **namespaced per feature**,
so the workdir root stays clean — only your real project artifacts sit there.

## The gate files

| File | Written by | Meaning |
|---|---|---|
| `SPECS.md` / `tasks.md` | you | Ordered phases + acceptance criteria (the input) |
| `.specstride/features/<slug>/PROGRESS.md` | proposer | Durable state; read first each iteration |
| `.specstride/features/<slug>/gates/GATE<N>-EVIDENCE.md` | proposer | Evidence phase N's criteria are met. Written atomically |
| `.specstride/features/<slug>/gates/GATE<N>-APPROVED` | **critic** | Empty marker; unblocks phase N+1 |
| `.specstride/features/<slug>/gates/GATE<N>-FEEDBACK.md` | **critic** | Present after a REJECT; the gaps to fix |

The current phase is **derived** from the `GATE*` markers, never stored.

## Feature-scoped state

Durable state hangs off `.specstride/features/<slug>/` so multiple Spec Kit features can build into
**one** repo without their gates, evidence, and verdicts colliding. `<slug>` is the feature-dir
basename when the spec lives inside a `.specify` project (`001-reverse-engineering-analysis`),
and `default` otherwise — which is also the back-compat identity of every pre-v2 `.specstride/gates/`
on disk (transparently migrated once on the next run).

| Path | Scope | Holds |
|---|---|---|
| `.specstride/features/<slug>/gates/` (+ `gates/proofs/`) | per-feature | all the phase-control files above |
| `.specstride/features/<slug>/runs/<run-id>/{run.log,events.jsonl}` | per-feature | each run isolated |
| `.specstride/features/<slug>/{verdicts,attempts,debug}/` | per-feature | critic transcripts, archived rejected attempts (`attempts/phase<N>/attempt<M>/`), debug dumps |
| `.specstride/features/<slug>/debug/invocations/<run-id>/<role>/phase-<N>/attempt-<M>/iter-<I>/<invocation-id>/` | per-feature | one reconstructable proposer/critic invocation — see [Invocation artifacts](#invocation-artifacts) |
| `.specstride/features/<slug>/PROGRESS.md`, `last-run.conf` | per-feature | proposer notes; that feature's resume config |
| `.specstride/lock`, `.specstride/stop.flag` | **workdir** | one run per repo, ever — concurrency is per-workdir, **not** per-feature |
| `.specstride/run.log`, `.specstride/events.jsonl` | **workdir** | symlinks retargeted into the active feature's newest run |
| `.specstride/last-run.conf` | **workdir** | the active-feature pointer + last launch config |
| `.specstride/features/<slug>/proposer.pid` | per-feature | in-flight proposer pass, so `specstride stop --now` can kill the tree |

**One run per workdir.** The `lock` stays at the `.specstride/` root: a second `specstride run` in the
same workdir exits `E_LOCK` (5) **even for a different feature**, because two features mutating
one source tree concurrently is a corruption, not a feature. Sequence features with the
operator; `specstride status --all` makes the sequence visible.

## Invocation artifacts

Each proposer/critic pass reconstructs itself under one leaf directory:

```
.specstride/features/<slug>/debug/invocations/<run-id>/<role>/phase-<N>/attempt-<M>/iter-<I>/<invocation-id>/
```

The path derives **only** from sanitized identity components ([`lib/invocation_result.py`](../lib/invocation_result.py),
`safe_path_component`), so a hostile `run_id` or `role` can never escape the feature root.

| Artifact | Contract | Written | Meaning |
|---|---|---|---|
| `metadata.json` | `specstride-invocation/v1` | atomically, when the exclusive dir is created **before** launch | the invocation's identity + capability context |
| `result.json` | `specstride-invocation-result/v1` | atomically, exactly once at finalization | the required terminal audit record — `status` + `reason_code` (see below) |
| `prompt.txt` / `provider.jsonl` / `events.jsonl` / `response.txt` | — | **only when raw capture is explicitly enabled** | raw provider prompt/stream/response; prunable after retention expiry without disturbing the audit record |

Every field routes through [`lib/observability_policy.py`](../lib/observability_policy.py) first:
secret-looking keys are redacted to `[REDACTED]`, provider thinking/reasoning content is dropped
entirely, and oversized payloads are truncated with `truncated=true`. `metadata.json` and
`result.json` are therefore safe to retain even when the raw content is not.

**Atomicity.** `atomic_write_json` writes to a temp file in the same directory, `fsync`s, then
`os.replace`s into place — a reader never sees a half-written record, and a crash mid-write leaves
the prior file intact (or, for the first write, no file at all).

### Result reason codes

`result.json` carries a `status` and a canonical `reason_code`. Terminal precedence is applied in
`reconcile_result` while every raw observation is retained:

| `reason_code` | `status` | Meaning |
|---|---|---|
| `success` | `success` | Invocation completed successfully |
| `timeout` | `timeout` | Invocation timed out |
| `cancelled` | `cancelled` | Invocation was cancelled |
| `launch_failed` | `error` | Provider process could not be launched |
| `producer_nonzero` | `error` | Provider process exited nonzero |
| `producer_signaled` | `error` | Provider process terminated by signal |
| `parser_failed` | `error` | Provider stream parser failed |
| `provider_auth` | `error` | Provider authentication failed |
| `provider_error` | `error` | Provider reported an error |
| `malformed_stream` | `error` | Provider stream was malformed or truncated |
| `missing_terminal` | `error` | Provider stream ended without a terminal observation |
| `unsupported_schema` | `degraded` | Provider stream used an unsupported schema |
| `status_conflict` | `error` | Provider and process terminal observations conflict |

A pass the watchdog ended reconciles as `timeout` (the producer was terminated),
which says nothing about WHY. The controller's own observation therefore rides on
the same record: `kill_reason` (`hard_cap` | `idle_timeout` | `repeat_stall` |
`progress_stall`) and its stable `kill_class` (`budget` | `hang` | `futility`),
present only when the pass was killed. The class is what decides the accounting —
a `budget` kill is charged to `SPECSTRIDE_PROPOSER_MAX_CAPS` (exit 10), never to the
error breaker — and it is written here so a consumer never re-derives it from a
reason string, and so a killed invocation keeps one durable, classified result.

### Retention

Raw provider capture is **disabled by default**. When enabled, retention is governed by
`RedactionRetentionPolicy` (`specstride-retention/v1`), whose version travels with each retained record:

- **Raw content** (`prompt.txt` / `provider.jsonl` / `events.jsonl` / `response.txt`) expires after
  **7 days** and is pruned by the retention sweep.
- **Redacted metadata + terminal result** (`metadata.json` / `result.json`) are kept **30 days**.
- The policy enforces `metadata_retention_days >= raw_retention_days`, so the summary always
  outlives the raw content it describes.

## The event stream

Every meaningful step appends one JSON object (one per line) to `.specstride/events.jsonl`;
`specstride events` and the live views render it. Lifecycle events come from the
orchestrator/proposer; the `agent_*` and `evidence_writing` events come from the proposer's
stream-json tap ([`lib/agent_stream.py`](../lib/agent_stream.py), gated by `SPECSTRIDE_AGENT_STREAM`).

| Event | Emitted by | Meaning |
|---|---|---|
| `run_start` / `run_end` | orchestrator | a run begins / all phases approved (`outcome`) |
| `run_stop` | orchestrator | run halted early — `reason` (`stop_flag`, `wall_budget`, `max_rejects`, `proposer_max_iter`, `proposer_consecutive_errors`, `proposer_cap_exhausted`, `proposer_yield_budget`, `proposer_yield_timeout`, `proposer_no_progress`, `proposer_no_evidence`, `critic_config`) + `phase` |
| `phase_start` / `phase_done` | orchestrator | phase N entered / approved |
| `learning_observed` | orchestrator | a per-phase observation was written at `phase_done` — `phase`, `path` (`learning/phase-<N>.json`). Only under `SPECSTRIDE_LEARNING`; best-effort, and never fails the phase |
| `proposer_start` | orchestrator | a proposer pass for phase N begins |
| `proposer_cap` | orchestrator | the pass ceiling this attempt runs under — `seconds` + `source` (`override` \| `learned` \| `declared` \| `global`), and the yield poll interval its passes use — `yield_poll` + `yield_poll_source` (`override` \| `learned` \| `default`). Both are resolved once per phase; the event repeats them per attempt. An unsourced budget is what makes budget archaeology expensive six hours in |
| `iter_cap` | proposer | a pass was killed at the ceiling — `reason` (`hard_cap`), `elapsed`, `consec`/`max` against `SPECSTRIDE_PROPOSER_MAX_CAPS`. A budget signal, not an error |
| `pass_cost_unknown` | proposer | a killed pass reports NO usage or cost (the kill severs the provider stream); this says "unmeasured", never "cheap" |
| `pass_yield` | proposer | a pass ended cleanly while a job it depends on runs — `reason`, `predicate_kind`, `deadline_sec`, `job_mode`, `job_log`, `yield_index` |
| `yield_job_start` | proposer | the job specstride now owns — `pid`, `argv`, `log`, `sid` |
| `yield_wait` | proposer | sampled while waiting with no model session open — `elapsed`, `predicate_kind` |
| `yield_resume` | proposer | the predicate is satisfied — `waited_sec`, `job_rc`, `job_duration_sec` |
| `yield_timeout` / `yield_invalid` | proposer | the wait ran out, or the yield artifact was refused |
| `prompt_block_dropped` | orchestrator | a prompt block did not fit the assembled-prompt budget (`SPECSTRIDE_PROMPT_MAX_BYTES`) |
| `iter_start` / `iter_done` | proposer | one headless proposer iteration |
| `evidence_written` / `evidence_present` | proposer | `GATE<N>-EVIDENCE.md` was just written / already existed |
| `attempt_archived` | orchestrator | a rejected evidence file was archived before retry |
| `verdict` | critic | the critic's APPROVED/REJECTED decision |
| `reject` | orchestrator | phase N rejected (attempt M) with feedback |
| `git_checkpoint` / `gates_migrated` | orchestrator | per-phase commit / one-time relocation of pre-v2 state into `features/default/` |
| `agent_observability` | agent tap | the capability this invocation begins with — `mode` (`structured` \| `degraded` \| `raw-text`) + `supported_signals` + `reason` + `provider_format` + `role`. Re-emitted if a fatal schema diagnostic degrades `structured`→`degraded` mid-stream, so a loss of fine-grained capture is explicit, never silent |
| `agent_init` | agent tap | once per pass: model + tool count |
| `agent_tool` | agent tap | every proposer tool call: tool name + compact target |
| `agent_text` | agent tap | first line of each assistant message (thinking/narration) |
| `agent_diagnostic` | agent tap | a bounded parse warning (`code`, e.g. `malformed_json` / `unsupported_schema` / `absent_schema`) — capped, never a flood; schema-fatal codes drive the `structured`→`degraded` transition above |
| `agent_result` | agent tap | end of pass: cost, tokens, duration, turns, and a terminal `reason_code` (see [Result reason codes](#result-reason-codes)) that the failure breaker counts |
| `evidence_writing` | agent tap | first Write/Edit/Bash of the pass that touches a `GATE<N>-EVIDENCE.md` |
| `_reopen` | presenter | **synthetic**, not on disk: the `events.jsonl` symlink retargeted (a new run after stop+resume) |

Next: [Hardening](Hardening) · [Telemetry](Telemetry)
