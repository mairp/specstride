# 05 — `learn.py evaluate`: the step that closes the loop

Written 2026-09-22 against `main` a3d674f plus the item 1–3 and 7 branches of the
self-improving-loop plan (`roadmap/self-improving-loop-plan.md`, item 4), before any of
item 4's code. It follows `roadmap/prompts/self-improving-loop-implementation.md`, which
corrects the plan where they disagree. This note is a dated design record, like the other
files in this directory: it is not rewritten when the code moves on.

The learning loop so far observes (`observe`), suggests (`advise`) and, when an operator
asks, applies one bounded decision (`apply`). Nothing checks whether a decision helped.
`evaluate` is that check. It compares the runs under an applied decision with the samples
that produced it, labels the decision, and reverts it automatically when a guardrail says
something leaked toward the gate. Reverting is the only automatic write it may make,
because a revert only ever moves a knob back toward its default.

## 1. What the corpus allows

Measured over the legacy corpus on this host: 231 readable `events.jsonl` streams under
`/root/*/.wiggum/features/*/runs/*/` (the 35 MB muse stream skipped), summarized per
feature with `learn.summarize`. Observational, not experimental: no `applied.json` exists
anywhere on the host, so there has never been an applied arm to measure.

| Figure | Value |
|---|---|
| phase episodes (one run × one phase) with a billed non-futility pass | 242 |
| passes per episode | mean 2.96, median 1 |
| episodes per phase key | median 1, mean 1.29 |
| within-phase sd of log(pass `cost_usd`), phase keys with ≥ 2 passes (94 keys) | median 0.554, pooled 0.735 |

The prompt's own audit measured 0.506 (median) and 0.654 (pooled) with a slightly
different selection; both are in the same place. With the decision rule below
(`s` floored at 0.50, `DEFF = 1 + (m̄ − 1)·0.5`):

| Billed passes per arm | MDE (log cost), `s = 0.506`, `m̄ = 1` | as a cost ratio | with the corpus `m̄ = 2.96` |
|---|---|---|---|
| 3 | 1.157 | ×3.18 (≈ +218 %) | 1.628 (×5.09) |
| 6 | 0.818 | ×2.27 | 1.151 (×3.16) |
| 12 | 0.578 | ×1.78 | 0.814 (×2.26) |
| 30 | 0.366 | ×1.44 | 0.515 (×1.67) |

At the floor of 6 per arm a *typical* phase from this corpus can only be labelled
`helped` or `regressed` if its cost moved by a factor of about three. Detecting a 30 %
change needs 58–97 passes per arm. **Formal significance testing is not available at
this N.** Exact McNemar and exact Wilcoxon both have minimum two-sided `p = 2/2ⁿ`, so at
3 pairs the smallest attainable p is 0.25; only 8 of 248 phase keys ever saw the 6 runs a
paired test would need. Neither is implemented. McNemar on approved/not would also score
approval rate, which the plan's *What not to do* forbids.

## 2. Decisions

### 2.1 The baseline is recorded with the decision

`apply` writes a `baseline` object into its `applied.json` entry, computed from exactly the
samples that produced the suggestion:

- `cost` and `wall`: the per-pass `cost_usd` and `elapsed_sec` series of the phase's
  billed, non-futility passes, each tagged with its run (the episode it clusters in);
- `guardrails`: the counts each guardrail in §2.6 needs (attempts, verdicts, MALFORMED
  verdicts, attempts with a `grounding_gap`, `verification_failed` attempts, first-attempt
  verdicts and approvals, diagnostician cases, arbitration-proxy stops, critic prompt
  sizes);
- `shape`: the phase shape from item 2;
- `backend`: the `run_start.backend` label of the most recent source run.

**Baseline-reset granularity is the backend label, not the model version.** Nothing in
the event stream records a model version: the apply entry's own `run_id` is a synthetic
`learn-<hex>`, and the best provenance a run carries is `run_start.backend`, a label such
as `prime:sol`. Two runs under `prime:sol` against different underlying models look the
same to the evaluator. This is a known blind spot, and one more reason the MDE floor is
conservative.

Baseline samples are those of the source runs whose backend equals that label; source runs
under another backend are left out of the baseline, not mixed into it.

### 2.2 Unit, primaries, floor

- **Sample unit: the billed, non-futility pass**, clustered by phase episode (one
  `phase_start` → `phase_done` window inside one run, never a whole run). A pass killed for
  futility (`KILL_CLASS == "futility"`) is excluded from both arms by the same filter; an
  unbilled pass (no `cost_usd`: the 69 `missing_terminal` results) is not a cost sample.
- **Primaries**: log cost per pass and log wall-clock per pass. `summarize` grows a
  per-phase `episodes` list (per episode: run, cost, wall, passes, approved) and
  `pass_samples`. `cost_per_approved_phase` stays a per-run report figure and is not a
  primary: it is undefined in 62 % of real runs.
- **Floor: 6 billed non-futility passes per arm.** Below it the label is `insufficient`
  and nothing else happens.

### 2.3 The decision rule

stdlib `math`/`statistics`, no p-values:

```
r    = mean(log x | applied) − mean(log x | baseline)
s    = max(0.50, pooled within-arm sd of log x)
m̄    = mean billed passes per episode, both arms
DEFF = 1 + (m̄ − 1) · 0.5
MDE  = 2.80 · s · sqrt(1/n_a + 1/n_b) · sqrt(DEFF)
```

`|r| < MDE` → `neutral`; `r ≤ −MDE` → `helped`; `r ≥ +MDE` → `regressed`, for cost and for
wall-clock separately. `regressed` on either primary is `regressed`; otherwise `helped` on
either is `helped`; otherwise `neutral`. The 2.80 is `z(0.975) + z(0.80)` (5 % two-sided,
80 % power); the 0.5 intra-cluster correlation is a deliberately pessimistic guess, because
the corpus cannot estimate it (median one episode per key). The entry records `r`, `MDE`,
`n_a`, `n_b`, `s` and `DEFF` for both primaries, and every printed label carries its effect
**with its MDE beside it**, so `neutral` is never read as "no effect": it means "no effect
this large could be seen".

### 2.4 When the comparison is invalid

The comparison holds only while the backend label and the phase shape match the baseline.

- A **shape** change already hides the decision (item 2 keys decisions on shape), and
  `evaluate` for the new shape finds no decision to evaluate. Evaluating the old decision
  explicitly under a different current shape returns `insufficient` with
  `reset: "shape"`.
- A **backend** change discards every applied-arm sample from runs under another label and
  records `reset: "backend"`; with nothing left the label is `insufficient`.

### 2.5 Which runs are the applied arm

Before item 4c, a run is in the applied arm for a `(knob, phase)` when its `proposer_cap`
for that phase reports the knob's source as `learned` at the decision's value, it is not
one of the decision's source runs, and it ran under the baseline's backend. 4c replaces
the source test with the recorded `arm`, which also sees an applied value equal to the
default (§3).

### 2.6 Guardrails (4b)

Guardrails veto even when a primary improved, and a breach reverts the decision.

| Guardrail | Source | Breach |
|---|---|---|
| `grounding_gap` rate per attempt | `grounding_gap` (critic) | exact one-sided binomial upper tail p < 0.01 against the baseline rate, ≥ 10 applied attempts |
| MALFORMED verdict rate | `verdict.result` | same test, ≥ 10 applied verdicts |
| declared verification | `verification_failed` | **any** failure in the applied arm where the baseline had none, at any n |
| diagnostician GROUNDING share | `diagnostician_done.case` (item 3) | same binomial test on the share of consulted attempts declared `grounding`, ≥ 10 applied cases |
| critic input size | the `═══ PROMPT ═══` section of `verdicts/phase<N>.attempt<A>.<ts>.txt` | exact rank test: the top `k` critic prompts of both arms together are all applied, with `C(n_a,k)/C(n_a+n_b,k) < 0.01`; ≥ 6 per arm |
| arbitration proxy | `run_stop.reason ∈ {max_rejects, gate_oscillation, critic_config, proposer_no_progress}` or a `gate_oscillation` event, per episode | binomial test as above, ≥ 10 applied episodes |
| first-attempt approval rate | `verdict` on attempt 1 | **alarm in both directions**: two one-sided exact binomial tails, each p < 0.01, ≥ 10 applied first-attempt verdicts |

Below its minimum n a guardrail reports `unknown`, never `ok`. All tails are computed with
`math.comb`. A percentage-point threshold would be meaningless here: at 10 verdicts the
binomial standard error is up to 16 points.

Why these thresholds. p < 0.01 per guardrail keeps the family-wise false-alarm rate
around 5–6 % for six simultaneously tested guardrails, which is acceptable because a false
alarm only reverts toward the default. The ≥ 10 minimum is the smallest n at which a
binomial tail can reach 0.01 against a baseline rate in the corpus's range (a 23 %
`grounding_gap` base rate needs 8 of 10 to get under 0.01; a 44 % first-attempt approval
rate is reachable in both directions at 10). The rank test for critic input size is exact
under exchangeability, needs no distributional assumption about prompt sizes, and at 6 per
arm breaches when 5 of the 6 largest prompts are applied-arm prompts (p = 0.0076).

**Critic input size has no event.** `critic_start` carries only `phase, attempt,
provider`. Adding an event would be a second change to `critic.py`, which the plan forbids.
It is read, read-only, from the verdict transcripts `critic.py` already writes;
`summarize --verdicts-dir` reads them beside `--verification-dir`.

**Human-arbitration rate is not measurable.** No event exists; the only trace is a log line
in `orchestrator.sh`. The proxy is the set of stop reasons that hand a phase back to a
human, plus the `gate_oscillation` event. It is a proxy, and is named one in every output.

**False-MISSING rate is out of scope for item 4.** No event carries it; recomputing it later
by checking whether a cited path exists is biased downward as the tree grows; and parsing
the critic's prose into a label would make the critic's output an optimization input (plan
risks 2 and 4). It stays where design §5.3 puts it: an offline release check on
`critic.py`.

**First-attempt approval rate is an alarm, never a reward and never a label term.** It
enters no label; it only ever reverts.

### 2.7 Auto-revert and quarantine (4b)

On a breach `evaluate` re-reads `applied.json` and reverts through the existing
`revert_run` path, then emits `knob_auto_reverted` naming the guardrail. `revert_run`
refuses anything but the currently active decision, so if a manual `apply` or `--off`
overtook the evaluated decision, `evaluate` **skips**, records and emits `knob_evaluated`
with `action: skipped_superseded`, and never retries against the new top of the stack.
After `--off` an auto-revert is a silent no-op.

The auto-revert entry carries `auto: true`, `guardrail`, `evaluated_from` (the evaluate
entry's run id), `quarantined_value`, and the shape and backend it was quarantined under.
`advise` reads `work_sec_p90`, which a cost regression does not move, so without a
quarantine it would re-propose the reverted value forever. `advise` still prints the
suggestion, annotated `quarantined`. `apply` refuses a value within ±10 % of a quarantined
one for the same `(knob, phase, shape, backend)` until 6 new billed non-futility samples
have accrued since the revert, exits 4 naming the reverted run id, and records `force:
true` when `--force` overrides. A shape or backend change clears the quarantine, because
the key no longer matches.

### 2.8 When evaluate runs, and what it writes

At `phase_done`, beside `observe`, under the same discipline: only under
`SPECSTRIDE_LEARNING` ≠ `off`, guarded by an `evaluate --help` probe, and a failure logs and
never costs an approved phase. It reads the whole `$FEATURE_DIR/runs` tree, not the newest
run's file; `specstride learn --show` reads the same tree. It appends an `evaluate` entry to
`applied.json` (never mutating the apply entry) **only when the result differs from the
last evaluate entry for that decision** (label, arm sizes or guardrail states), so a
phase that is re-approved without new samples does not grow the log. It emits
`knob_evaluated` into the run's own `events.jsonl`. Replay (`effective_value`,
`revert_run`, `--off`) ignores `evaluate` entries: only `apply` and `revert` entries are
decisions.

`evaluate --report` is read-only and prints one line per active decision; the orchestrator's
exit trap prints it on every exit, so the summary appears on `run_end` and on each of the
`run_stop` paths (`run_end` fires in 13 % of real runs). `specstride learn --show` prints
the same state after its suggestions.

### 2.9 Counterfactuals from logs

A *shorter* `proposer_timeout` can be evaluated from recorded pass durations for
**wall-clock only**: `min(elapsed, value)` over the baseline passes is an upper bound on
the wall-clock the shorter cap would have allowed. It is **never** evaluated for cost: a
truncated pass's cost is unobserved, and the 69 `missing_terminal` results with no cost
would score as free. A *longer* timeout cannot be evaluated from logs at all: a killed pass
is censored, and nothing says how long it would have run. The evaluate entry records a
`counterfactual` object stating which case applies; it is informational and never a label
input.

## 3. The arm field, and why there is no alternation (4c)

`resolve` prints `<value>\t<arm>`: `baseline` while no decision exists for that
`(knob, phase, shape)`, `applied` once one does, whatever its value. The shell stamps the
arm on `proposer_cap` (`arm`, `yield_poll_arm`). `resolve_proposer_timeout` used to drop a
learned value equal to its fallback (`&& "$learned" != "$fallback"`), which made an applied
decision equal to the default invisible; with the arm carried explicitly the route reports
`learned` whenever the arm is `applied`.

**ABAB alternation is not implemented.** Only 8 of 248 real phase keys ever saw the ≥ 6 runs
it needs; a resumed run gets a new run id and run directory, so alternating by run would split
one phase episode across both arms; and a pinned `configuration.learning.effective` value
(item 6) that silently reverts on alternate runs would break MoL's promise that the contract
says what runs. Version 1 records the arm and compares before with after.

Before/after is confounded with time, with spec drift inside one shape (a changed
implementation elsewhere in the tree), and with backend drift inside one label. That is
why the MDE floor is conservative, why nothing but a revert is automatic, and why a
`helped` label is a statement about this history, not a causal claim.

## 4. The tamper rule (4c)

Every proposer backend runs with permissions disabled in the project directory, which
contains `.specstride/features/<slug>/`, so the proposer can write `events.jsonl` and
`learning/`. It also legitimately appends to the same `events.jsonl` the orchestrator
writes. The rule is **prefix immutability, per proposer invocation, only under
`SPECSTRIDE_LEARNING` ≠ `off`**:

- The orchestrator, the parent process, brackets each `proposer.sh` launch. Before: `L =
  size(events.jsonl)` and the SHA-256 of its first `L` bytes; the same for
  `learning/applied.json` and `learning/phase-<N>.json`.
- After: a file smaller than `L`, or whose first `L` bytes hash differently, marks the
  attempt tampered. The orchestrator emits `events_tampered` (`file`, `phase`, `attempt`)
  into the run's stream once the pass has exited, and every evaluation excludes that run
  from both arms. The event itself sits in the prefix the next bracket protects.
- Bytes past `L` are legitimate appends: the stream tap, `iter_start`/`iter_done`, the
  finalizer and `specstride_emit` all append during a pass. A whole-file or line-count
  digest would flag every normal pass.
- A pass may yield and resume under a different orchestrator process: the bracket is taken
  per invocation, so each resume re-baselines. A run with no pre-digest for an attempt is
  excluded, not trusted.
- With learning off the bracket is not taken at all; the hot loop is byte-identical.

What it does **not** cover:

- **Fabricated appended events.** A forged `agent_result` with a low `cost_usd`, appended
  during the pass, is byte-indistinguishable from a tap append. An in-band HMAC would not
  help against a same-uid writer that can read the orchestrator's environment.
- **Anything written outside the bracket**, including detached long jobs that outlive a
  pass (`long_job_start`, `sweep_stray_progress`).
- **`verdicts/*.txt`, `verification/*.json` and `GATE<n>-EVIDENCE.md`**, which the gate
  owns and item 4 only reads (the critic-input-size guardrail reads the transcripts).
- **Spec edits**, which item 2's shape detects but does not prevent.
- A tamper in the last pass of a run's last attempt that also removes the orchestrator's
  ability to write afterwards (for instance by deleting the run directory): there is no
  later writer, and nothing in the stream records it.
