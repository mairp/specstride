#!/usr/bin/env python3
"""learn.py — Step 0 of the self-improvement design: measure, change nothing.

Reads one or more Specstride ``events.jsonl`` streams (optionally the matching
``run.log`` and ``verification/`` directory) and computes the metric set of
``roadmap/research/self-improvement-loops/02-wiggum-loop-design.md`` §5.3 and
``03-002-run-telemetry.md`` §6. Pure functions; nothing here reads or writes
Specstride state, and nothing in Specstride reads this output yet.

    python3 lib/learn.py summarize --events <events.jsonl|run-dir|runs-dir> [...]
                                   [--run-log <run.log>] [--verification-dir <dir>]
                                   [--out <summary.json>]

Keying
------
Every figure is keyed on ``(run_id, phase, attempt)``: ``attempt`` numbers reset
on every Specstride run restart, so ``(phase, attempt)`` alone silently merges
unrelated data (telemetry report, methodology note). Events the critic emits
(``critic_start``, ``verdict``, ``grounding_gap``) carry no ``run_id``; they are
attributed to the run of the stream they were read from. Events the proposer
emits (``iter_*``, ``agent_*``, ``pass_killed``) carry no ``phase``/``attempt``;
they are attributed to the running ``phase_start``/``proposer_start`` state.

Learned state is further keyed on the phase's *shape* (``phase_start.shape``, a
digest of number + title + criteria text from ``specstride_spec.phase_shape``):
``summarize(phase_shapes=…)`` counts only samples of the current shape, and a
decision in ``applied.json`` applies only to the shape it was learned under.

Wait vs. work
-------------
An ``agent_tool`` whose tool is ``Bash`` and whose target matches ``WAIT_RE``
(the telemetry report's classifier) is a wait/poll call. ``sleep N`` seconds are
summed as ``sleep_sec_declared`` — a lower bound on time the pass spent waiting,
since ``tail -f``/``until`` loops declare no duration.

Outcome taxonomy (per pass, recommendation 2 / design §4.2)
-----------------------------------------------------------
``budget_kill``      pass_killed with a budget reason (``hard_cap``) — the work
                     did not fit; the agent may have been productive.
``worker_error``     pass_killed with a futility reason (``repeat_stall``,
                     ``progress_stall``) or a hang (``idle_timeout``), or an
                     agent_result with ``is_error`` and no kill.
``clean_no_progress`` agent_result success, but no evidence written in the pass.
``productive``       agent_result success and evidence written.
``open``             the pass has no terminal event yet (stream still growing).

Step 5 — the learning loop
---------------------------
``advise``/``apply``/``revert``/``resolve``/``off`` turn the §5.3 metrics into a
*suggested*, then optionally *applied*, per-phase knob value. Both allowlisted
knobs have an engine: ``proposer_timeout`` (§4.1, from measured ``work_sec``) and
``yield_poll_interval`` (§2.1, from measured yield job durations). §5.5's third
knob, ``inject_yield_hint``, was removed: the yield contract is already appended
to every proposer and accelerator prompt, so the knob had nothing to switch.
Storage follows §5.4 exactly: an attempt/phase summary is an **observation**
(``observe``, below); a knob value this module has decided to use is a separate,
append-only **decision** log (``<feature-dir>/learning/applied.json``, JSON-lines,
one entry per apply/revert, each keyed by a ``run_id``) so the two can never be
conflated. The adjustable-knob allowlist (``ADJUSTABLE_KNOBS``, §5.5) is a locked
literal set: nothing the critic reads may ever appear in it. ``resolve`` is the
one integration point another component may call — see ``resolve_knob`` — and it
is a total no-op unless ``SPECSTRIDE_LEARNING=apply`` is set in its environment,
matching the "suggest is the default; unset/off changes nothing" rule of §5.5
invariant 5.

Observations (§5.4, ``observe``)
---------------------------------
``observe`` writes the §5.4 per-phase **observation** document — exactly the
phase's entry from ``summarize``'s ``phases`` map, wrapped with provenance — to
``<feature-dir>/learning/phase-<N>.json``. It is the measurement half of §5.4's
table; it never reads or writes ``applied.json`` and nothing here decides
anything from it automatically. It exists so an orchestrator hook can call one
line at ``phase_done`` and leave a phase's own history somewhere the *next* run
of that phase (or a human, or `specstride learn --show`) can read it without
re-deriving it from every run's raw ``events.jsonl`` each time:

    python3 lib/learn.py observe --events <events.jsonl|run-dir|runs-dir> \\
                                 --phase <N> [--out <feature-dir>/learning/phase-<N>.json]

The documented call for an orchestrator's ``phase_done`` hook (one line, run
after ``specstride_emit phase_done ...``, using the orchestrator's own ``$LIB_DIR``,
``$FEATURE_DIR`` and current-phase ``$n``):

    python3 "$LIB_DIR/learn.py" observe --events "$FEATURE_DIR/runs" --phase "$n" \\
      --out "$FEATURE_DIR/learning/phase-$n.json"

``--events "$FEATURE_DIR/runs"`` (a dir-of-run-dirs, per ``find_event_files``) is
deliberate, not ``$RUN_DIR/events.jsonl`` alone: a phase's observation should
reflect every run that has ever touched it, the same cross-run view
``summarize``'s ``phases`` map already gives ``attempts_to_approval`` and
``runs_seen``. Idempotent: the same input always overwrites ``--out`` with the
same ``"observation"`` content (only ``generated_at`` differs run to run) — safe
to call once per ``phase_done``, and safe to call again by hand.

Output schema (``specstride.learn.summary/2``)
------------------------------------------
``/2`` adds ``phases[*].caps`` (one entry per run from ``proposer_cap``) to ``/1``;
``/3`` adds ``attempts[*].shape`` and ``phases[*].shape``/``other_shape_attempts``;
``/4`` adds ``attempts[*].diagnostician_case`` and ``phases[*].diagnostician_cases``;
``/5`` adds ``phases[*].episodes`` and ``phases[*].pass_samples`` (item 4's primaries);
``/6`` adds ``attempts[*].critic_prompt_bytes`` (with ``--verdicts-dir``);
``/7`` adds ``runs[*].tampered`` and the caps' ``arm``/``yield_poll_arm``/``tamper_bracket``.
{
  "schema": "specstride.learn.summary/1",
  "inputs": [<event file paths>],
  "runs": { "<run_id>": {
      "feature", "backend", "started", "stopped", "stop_reason", "stop_phase",
      "resume_from", "wall_sec", "cost_usd", "passes", "billed_passes",
      "unbilled_passes", "kills_by_reason", "outcomes", "attempts",
      "approved_phases", "cost_per_approved_phase", "non_approved_cost_usd",
      "non_approved_cost_share", "tampered" } },
  "attempts": [ {   # ``billed`` is True/False on a terminal pass, None while open;
                    # ``work_sec_estimate`` = max(0, elapsed − sleep_sec_declared)
      "run", "phase", "attempt", "title", "shape", "verdict", "verdict_reason",
      "verification": "passed"|"failed"|null, "verification_rc",
      "started", "ended", "wall_sec", "passes", "proposer_elapsed_sec",
      "cost_usd", "billed_passes", "unbilled_passes",
      "cache_creation_tokens", "cache_read_tokens", "output_tokens",
      "tool_calls", "wait_calls", "work_calls", "wait_call_share",
      "sleep_sec_declared", "work_sec_estimate",
      "kills_by_reason", "outcomes", "evidence_written",
      "grounding_gap_paths", "critic_sec", "diagnostician_case",
      "critic_prompt_bytes",                          # with --verdicts-dir
      "gate_duration_ms", "gate_live_share",          # with --verification-dir
      "passes_detail": [ { "iter", "outcome", "kill_reason", "kill_class",
                           "elapsed_sec", "cost_usd", "billed", "tool_calls",
                           "wait_calls", "sleep_sec_declared", "dominant_repeat",
                           "dominant_repeat_n", "dominant_repeat_share" } ] } ],
  "phases": { "<phase>": {
      "title", "shape", "other_shape_attempts", "runs_seen", "attempts_total", "attempts_to_approval",
      "approved_in_run", "attempt_number_reset", "cost_usd",
      "work_sec_p50", "work_sec_p90", "work_sec_samples", "kills_by_reason",
      "job_duration_p50", "job_duration_samples",           # yield_poll_interval's evidence
      "wait_share_p50", "wait_share_samples", "hard_cap_kills_with_wait",
      "caps": [ { "run", "seconds", "source", "arm", "yield_poll", "yield_poll_source",
                  "yield_poll_arm", "tamper_bracket" } ],
      "diagnostician_cases": { "grounding"|"real_gap"|"unknown": n },
      "episodes": [ { "run", "cost_usd", "wall_sec", "passes", "approved" } ],
      "pass_samples": [ { "run", "cost_usd", "elapsed_sec" } ] } },
  "totals": { "runs", "attempts", "passes", "cost_usd", "unbilled_passes",
              "kills_by_reason", "outcomes", "approved_phases",
              "cost_per_approved_phase", "non_approved_cost_share",
              "wait_call_share", "sleep_sec_declared",
              "proposer_live_invocations" (with --run-log) }
}
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
import uuid
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import specstride_env  # noqa: E402  (legacy env names map onto SPECSTRIDE_*)
specstride_env.apply()

SCHEMA = "specstride.learn.summary/7"

# The wait/poll classifier of 03-002-run-telemetry.md §2, applied to Bash targets.
WAIT_RE = re.compile(
    r"(^|[;&|(]\s*|\s)(sleep\s+\d|tail\s+-[fc]|while\s+(pgrep|true)|until\s+grep|"
    r"pgrep\s+-|ps\s+aux.*grep|watch\s+-n)"
)
SLEEP_RE = re.compile(r"(?:^|[;&|(]\s*|\s)sleep\s+(\d+(?:\.\d+)?)")
REPEAT_RE = re.compile(r"^(?:re-ran|repeated)\s+(\d+)x:\s*(.*)$", re.S)

# design §4.2 — the class of each kill reason.
KILL_CLASS = {
    "hard_cap": "budget",
    "repeat_stall": "futility",
    "progress_stall": "futility",
    "idle_timeout": "hang",
}
BUDGET_REASONS = {r for r, c in KILL_CLASS.items() if c == "budget"}

OUTCOMES = ("productive", "clean_no_progress", "worker_error", "budget_kill", "open")


# ── loading ──────────────────────────────────────────────────────────────────
def find_event_files(paths: Iterable[str]) -> List[str]:
    """A path is an events.jsonl, a run dir holding one, or a dir of run dirs."""
    out: List[str] = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
        elif os.path.isdir(p):
            direct = os.path.join(p, "events.jsonl")
            if os.path.isfile(direct):
                out.append(direct)
            else:
                for name in sorted(os.listdir(p)):
                    cand = os.path.join(p, name, "events.jsonl")
                    if os.path.isfile(cand):
                        out.append(cand)
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def read_events(path: str) -> List[dict]:
    """Lenient JSONL: a half-written trailing line (stream in flight) is skipped."""
    events: List[dict] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and "event" in ev:
                ev["_src"] = path
                events.append(ev)
    return events


# ── normalisation ────────────────────────────────────────────────────────────
def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def classify_bash_target(target: str) -> Tuple[bool, float]:
    """(is_wait, declared_sleep_seconds) for one Bash tool target."""
    if not target:
        return False, 0.0
    is_wait = bool(WAIT_RE.search(target))
    secs = sum(float(m) for m in SLEEP_RE.findall(target))
    return is_wait or secs > 0, secs


def parse_repeat_detail(detail: str) -> Tuple[Optional[int], Optional[str]]:
    """``pass_killed.detail`` is ``re-ran 12x: tail -4`` / ``repeated 13x: …``."""
    if not detail:
        return None, None
    m = REPEAT_RE.match(detail.strip())
    if not m:
        return None, None
    return int(m.group(1)), m.group(2).strip()


def percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    vs = sorted(values)
    k = (len(vs) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(vs) - 1)
    return round(vs[lo] + (vs[hi] - vs[lo]) * (k - lo), 1)


# ── the streaming pass ───────────────────────────────────────────────────────
class _Pass:
    def __init__(self, it: int, start: Optional[float]):
        self.iter = it
        self.start = start
        self.end: Optional[float] = None
        self.tool_calls = 0
        self.wait_calls = 0
        self.sleep_sec = 0.0
        self.kill_reason: Optional[str] = None
        self.kill_elapsed: Optional[float] = None
        self.kill_detail: Optional[str] = None
        self.result: Optional[dict] = None
        self.evidence = False
        # set from a ``yield_resume`` event (design §2.4): how long the job this
        # pass yielded on actually ran. None for a pass that never yielded.
        self.job_duration: Optional[float] = None

    def outcome(self) -> str:
        if self.kill_reason is not None:
            return "budget_kill" if self.kill_reason in BUDGET_REASONS else "worker_error"
        if self.result is None:
            return "open"
        if _bool(self.result.get("is_error")):
            return "worker_error"
        return "productive" if self.evidence else "clean_no_progress"

    def elapsed(self) -> Optional[float]:
        if self.kill_elapsed is not None:
            return self.kill_elapsed
        if self.result is not None and self.result.get("duration_ms") is not None:
            return round(_float(self.result["duration_ms"]) / 1000.0, 1)
        if self.start is not None and self.end is not None:
            return round(self.end - self.start, 1)
        return None

    def to_dict(self) -> dict:
        cost = _float(self.result.get("cost_usd")) if self.result else None
        n, cmd = parse_repeat_detail(self.kill_detail or "")
        outcome = self.outcome()
        return {
            "iter": self.iter,
            "outcome": outcome,
            "kill_reason": self.kill_reason,
            "kill_class": KILL_CLASS.get(self.kill_reason, "unknown") if self.kill_reason else None,
            "elapsed_sec": self.elapsed(),
            "cost_usd": cost,
            # None while the pass is still open: neither billed nor unbilled yet
            "billed": (cost is not None) if outcome != "open" else None,
            "tool_calls": self.tool_calls,
            "wait_calls": self.wait_calls,
            "sleep_sec_declared": round(self.sleep_sec, 1),
            "dominant_repeat": cmd,
            "dominant_repeat_n": n,
            "dominant_repeat_share": (round(n / self.tool_calls, 3) if n and self.tool_calls else None),
            "job_duration_sec": (round(self.job_duration, 1) if self.job_duration is not None else None),
        }


class _Attempt:
    def __init__(self, run: str, phase: int, attempt: int, start: Optional[float], title: str,
                 shape: Optional[str] = None):
        self.run, self.phase, self.attempt = run, phase, attempt
        self.title = title
        self.shape = shape   # the phase's shape digest from phase_start (None: not recorded)
        self.start = start
        self.end: Optional[float] = None
        self.passes: "OrderedDict[int, _Pass]" = OrderedDict()
        self.verdict: Optional[str] = None
        self.verdict_reason: Optional[str] = None
        self.verification: Optional[str] = None
        self.verification_rc: Optional[int] = None
        self.evidence_written = 0
        self.grounding_paths: List[str] = []
        self.critic_start: Optional[float] = None
        self.critic_sec: Optional[float] = None
        self.gate_duration_ms: Optional[int] = None
        self.gate_live_share: Optional[float] = None
        self.diagnostician_case: Optional[str] = None
        self.critic_prompt_bytes: Optional[int] = None   # from --verdicts-dir

    def key(self) -> Tuple[str, int, int]:
        return (self.run, self.phase, self.attempt)

    def to_dict(self) -> dict:
        passes = [p.to_dict() for p in self.passes.values()]
        tool = sum(p["tool_calls"] for p in passes)
        wait = sum(p["wait_calls"] for p in passes)
        sleep = round(sum(p["sleep_sec_declared"] for p in passes), 1)
        elapsed = [p["elapsed_sec"] for p in passes if p["elapsed_sec"] is not None]
        cost = round(sum(p["cost_usd"] for p in passes if p["cost_usd"] is not None), 4)
        res = [p.result for p in self.passes.values() if p.result]
        kills = Counter(p["kill_reason"] for p in passes if p["kill_reason"])
        outcomes = Counter(p["outcome"] for p in passes)
        return {
            "run": self.run,
            "phase": self.phase,
            "attempt": self.attempt,
            "title": self.title,
            "shape": self.shape,
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "verification": self.verification,
            "verification_rc": self.verification_rc,
            "started": self.start,
            "ended": self.end,
            "wall_sec": (round(self.end - self.start, 1) if self.start is not None and self.end is not None else None),
            "passes": len(passes),
            "proposer_elapsed_sec": round(sum(elapsed), 1),
            "cost_usd": cost,
            "billed_passes": sum(1 for p in passes if p["billed"] is True),
            "unbilled_passes": sum(1 for p in passes if p["billed"] is False),
            "cache_creation_tokens": sum(_int(r.get("cache_creation_tokens")) or 0 for r in res),
            "cache_read_tokens": sum(_int(r.get("cache_read_tokens")) or 0 for r in res),
            "output_tokens": sum(_int(r.get("output_tokens")) or 0 for r in res),
            "tool_calls": tool,
            "wait_calls": wait,
            "work_calls": tool - wait,
            "wait_call_share": (round(wait / tool, 3) if tool else None),
            "sleep_sec_declared": sleep,
            # declared sleeps can exceed the pass (backgrounded/timed-out waits): floor at 0
            "work_sec_estimate": (round(max(0.0, sum(elapsed) - sleep), 1) if elapsed else None),
            "kills_by_reason": dict(kills),
            "outcomes": {o: outcomes.get(o, 0) for o in OUTCOMES if outcomes.get(o, 0)},
            "evidence_written": self.evidence_written,
            "grounding_gap_paths": len(self.grounding_paths),
            "critic_sec": self.critic_sec,
            "gate_duration_ms": self.gate_duration_ms,
            "gate_live_share": self.gate_live_share,
            "diagnostician_case": self.diagnostician_case,
            "critic_prompt_bytes": self.critic_prompt_bytes,
            "passes_detail": passes,
        }


class _Run:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.feature = self.backend = None
        self.resume_from: Optional[int] = None
        self.started: Optional[float] = None
        self.last_ts: Optional[float] = None
        self.stopped: Optional[float] = None
        self.stop_reason: Optional[str] = None
        self.stop_phase: Optional[int] = None
        self.cur_phase: Optional[int] = None
        self.cur_title: str = ""
        self.cur_shape: Optional[str] = None
        self.cur_attempt: Optional[int] = None
        self.cur_pass: Optional[_Pass] = None
        self.attempts: "OrderedDict[Tuple[str,int,int], _Attempt]" = OrderedDict()
        # phase → the first proposer_cap of this run for it. The event fires once
        # per attempt, the values are resolved once per phase: dedupe on (run, phase).
        self.caps: "OrderedDict[int, dict]" = OrderedDict()
        self.tampered = False

    def attempt(self) -> Optional[_Attempt]:
        if self.cur_phase is None or self.cur_attempt is None:
            return None
        key = (self.run_id, self.cur_phase, self.cur_attempt)
        if key not in self.attempts:
            self.attempts[key] = _Attempt(self.run_id, self.cur_phase, self.cur_attempt, self.last_ts,
                                          self.cur_title, self.cur_shape)
        return self.attempts[key]


def summarize(events: List[dict], verification_dir: Optional[str] = None,
              run_log_live_invocations: Optional[int] = None,
              phase_shapes: Optional[Dict[int, str]] = None,
              verdicts_dir: Optional[str] = None) -> dict:
    """The pure summariser: a list of parsed events in → the summary dict out.

    ``phase_shapes`` ({phase: shape digest}) restricts a phase's entry in
    ``phases`` to the attempts recorded under that shape (``phase_start.shape``);
    attempts of an edited phase, or of a run that recorded no shape, stop counting
    as its samples. ``attempts`` and ``runs`` are never filtered."""
    runs: "OrderedDict[str, _Run]" = OrderedDict()
    src_run: Dict[str, str] = {}   # stream file → run id, for run_id-less critic events

    for ev in events:
        name = ev.get("event")
        ts = _float(ev.get("ts"))
        src = ev.get("_src", "")
        rid = ev.get("run_id") or src_run.get(src)
        if rid is None:
            continue
        if ev.get("run_id"):
            src_run[src] = rid
        run = runs.setdefault(rid, _Run(rid))
        if ts is not None:
            run.last_ts = ts
        phase, attempt = _int(ev.get("phase")), _int(ev.get("attempt"))

        if name == "run_start":
            run.started = ts
            run.feature = ev.get("feature")
            run.backend = ev.get("backend")
            run.resume_from = _int(ev.get("resume"))
        elif name == "phase_start":
            run.cur_phase, run.cur_title, run.cur_attempt, run.cur_pass = phase, ev.get("title", ""), None, None
            run.cur_shape = ev.get("shape") or None
        elif name == "proposer_start":
            if phase is not None:
                run.cur_phase = phase
            run.cur_attempt = attempt
            run.cur_pass = None
            a = run.attempt()
            if a is not None:
                a.start = ts
        elif name == "iter_start":
            a = run.attempt()
            it = _int(ev.get("iter")) or (len(a.passes) + 1 if a else 1)
            if a is not None:
                run.cur_pass = a.passes.setdefault(it, _Pass(it, ts))
        elif name == "agent_tool":
            p = run.cur_pass
            if p is not None:
                p.tool_calls += 1
                if ev.get("tool") == "Bash":
                    is_wait, secs = classify_bash_target(ev.get("target") or "")
                    p.wait_calls += 1 if is_wait else 0
                    p.sleep_sec += secs
        elif name == "agent_result":
            p = run.cur_pass
            if p is not None:
                p.result = ev
                p.end = ts
        elif name == "yield_resume":
            p = run.cur_pass
            if p is not None:
                dur = _float(ev.get("job_duration_sec"))
                if dur is None:
                    dur = _float(ev.get("waited_sec"))
                p.job_duration = dur
        elif name == "pass_killed":
            p = run.cur_pass
            if p is not None:
                p.kill_reason = ev.get("reason") or "unknown"
                p.kill_elapsed = _float(ev.get("elapsed"))
                p.kill_detail = ev.get("detail")
                p.end = ts
        elif name == "iter_done":
            p = run.cur_pass
            if p is not None:
                p.end = p.end or ts
                if ev.get("evidence") not in (None, "missing"):
                    p.evidence = True
        elif name == "evidence_written":
            a = run.attempt()
            if a is not None:
                a.evidence_written += 1
                if run.cur_pass is not None:
                    run.cur_pass.evidence = True
        elif name in ("verification_passed", "verification_failed"):
            a = _locate(run, phase, attempt)
            if a is not None:
                a.verification = "passed" if name == "verification_passed" else "failed"
                a.verification_rc = _int(ev.get("rc")) if name == "verification_failed" else 0
                if name == "verification_failed":
                    a.end = ts
        elif name == "critic_start":
            a = _locate(run, phase, attempt)
            if a is not None:
                a.critic_start = ts
        elif name == "diagnostician_done":
            # critic-emitted, no run_id: attributed by the same _locate rules. An
            # event from before the case field existed counts as "unknown".
            a = _locate(run, phase, attempt)
            if a is not None:
                a.diagnostician_case = ev.get("case") or "unknown"
        elif name == "grounding_gap":
            a = _locate(run, phase, attempt)
            if a is not None:
                a.grounding_paths += [s for s in str(ev.get("paths", "")).split(",") if s]
        elif name == "verdict":
            a = _locate(run, phase, attempt)
            if a is not None:
                a.verdict = ev.get("result")
                a.verdict_reason = ev.get("reason")
                a.end = ts
                if a.critic_start is not None and ts is not None:
                    a.critic_sec = round(ts - a.critic_start, 1)
        elif name in ("phase_done", "attempt_archived"):
            a = _locate(run, phase, attempt)
            if a is not None and a.end is None:
                a.end = ts
        elif name == "proposer_cap":
            if phase is not None and phase not in run.caps:
                run.caps[phase] = {
                    "run": rid,
                    "seconds": _int(ev.get("seconds")),
                    "source": ev.get("source"),
                    "yield_poll": _int(ev.get("yield_poll")),
                    "yield_poll_source": ev.get("yield_poll_source"),
                    "arm": ev.get("arm") or None,
                    "yield_poll_arm": ev.get("yield_poll_arm") or None,
                    "tamper_bracket": ev.get("tamper_bracket") or None,
                }
        elif name == "events_tampered":
            # the orchestrator saw bytes that predated a pass rewritten during it
            # (05-evaluate-design.md §4): this run is excluded from every evaluation
            run.tampered = True
        elif name == "run_stop":
            run.stopped = ts
            run.stop_reason = ev.get("reason")
            run.stop_phase = phase

    if verification_dir:
        _attach_gate_figures(runs, verification_dir)
    if verdicts_dir:
        _attach_critic_prompt_sizes(runs, verdicts_dir)

    return _render(runs, run_log_live_invocations, phase_shapes)


def _locate(run: _Run, phase: Optional[int], attempt: Optional[int]) -> Optional[_Attempt]:
    if phase is not None and attempt is not None:
        key = (run.run_id, phase, attempt)
        if key not in run.attempts:
            current = phase == run.cur_phase
            run.attempts[key] = _Attempt(run.run_id, phase, attempt, run.last_ts,
                                         run.cur_title if current else "", run.cur_shape if current else None)
        return run.attempts[key]
    return run.attempt()


def gate_figures(path: str) -> Tuple[Optional[int], Optional[float]]:
    """(total durationMs, share of it spent on commands whose args mention 'live')."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None, None
    cmds = doc.get("commands") or []
    total = sum(_int(c.get("durationMs")) or 0 for c in cmds)
    live = sum(_int(c.get("durationMs")) or 0 for c in cmds
               if any("live" in str(a) for a in (c.get("args") or [])) or "live" in str(c.get("executable", "")))
    return total, (round(live / total, 3) if total else None)


def _attach_gate_figures(runs, verification_dir: str) -> None:
    pat = re.compile(r"^phase-(\d+)-attempt-(\d+)\.json$")
    for name in os.listdir(verification_dir):
        m = pat.match(name)
        if not m:
            continue
        ph, at = int(m.group(1)), int(m.group(2))
        total, share = gate_figures(os.path.join(verification_dir, name))
        for run in runs.values():
            a = run.attempts.get((run.run_id, ph, at))
            if a is not None:
                a.gate_duration_ms, a.gate_live_share = total, share


_TRANSCRIPT = re.compile(r"^phase(\d+)\.attempt(\d+)\.(\d{8}-\d{6})\.txt$")
_PROMPT_START = "═══════════ PROMPT ═══════════\n"
_REPLY_START = "\n\n═══════════ REPLY ═══════════\n"
TRANSCRIPT_MATCH_SEC = 900   # a transcript belongs to the attempt whose verdict is this close


def critic_prompt_bytes(path: str) -> Optional[int]:
    """Size of the PROMPT section of one verdict transcript (critic.py's
    _write_transcript format): the critic-input-size guardrail, read-only. No
    event carries it, and adding one would be a second change to critic.py."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    start = text.find(_PROMPT_START)
    if start < 0:
        return None
    start += len(_PROMPT_START)
    end = text.find(_REPLY_START, start)
    return len(text[start:end if end >= 0 else len(text)].encode("utf-8"))


def _attach_critic_prompt_sizes(runs, verdicts_dir: str) -> None:
    """Transcripts are named phase<N>.attempt<A>.<local ts>; attempt numbers reset
    per run, so each is matched to the (phase, attempt) whose verdict is nearest
    in time, within TRANSCRIPT_MATCH_SEC."""
    try:
        names = os.listdir(verdicts_dir)
    except OSError:
        return
    for name in names:
        m = _TRANSCRIPT.match(name)
        if not m:
            continue
        ph, at = int(m.group(1)), int(m.group(2))
        try:
            when = time.mktime(time.strptime(m.group(3), "%Y%m%d-%H%M%S"))
        except ValueError:
            continue
        best, gap = None, TRANSCRIPT_MATCH_SEC
        for run in runs.values():
            a = run.attempts.get((run.run_id, ph, at))
            if a is not None and a.end is not None and abs(a.end - when) <= gap:
                best, gap = a, abs(a.end - when)
        if best is not None:
            best.critic_prompt_bytes = critic_prompt_bytes(os.path.join(verdicts_dir, name))


def _render(runs, run_log_live_invocations: Optional[int],
            phase_shapes: Optional[Dict[int, str]] = None) -> dict:
    attempts_out: List[dict] = []
    runs_out: "OrderedDict[str, dict]" = OrderedDict()
    for run in runs.values():
        atts = [a.to_dict() for a in run.attempts.values()]
        attempts_out.extend(atts)
        cost = round(sum(a["cost_usd"] for a in atts), 4)
        approved = sorted({a["phase"] for a in atts if a["verdict"] == "APPROVED"})
        non_approved = round(sum(a["cost_usd"] for a in atts if a["verdict"] != "APPROVED"), 4)
        kills: Counter = Counter()
        outcomes: Counter = Counter()
        for a in atts:
            kills.update(a["kills_by_reason"])
            outcomes.update(a["outcomes"])
        passes = sum(a["passes"] for a in atts)
        end = run.stopped if run.stopped is not None else run.last_ts
        runs_out[run.run_id] = {
            "feature": run.feature,
            "backend": run.backend,
            "started": run.started,
            "stopped": run.stopped,
            "stop_reason": run.stop_reason,
            "stop_phase": run.stop_phase,
            "resume_from": run.resume_from,
            "wall_sec": (round(end - run.started, 1) if run.started is not None and end is not None else None),
            "cost_usd": cost,
            "passes": passes,
            "billed_passes": sum(a["billed_passes"] for a in atts),
            "unbilled_passes": sum(a["unbilled_passes"] for a in atts),
            "kills_by_reason": dict(kills),
            "outcomes": dict(outcomes),
            "attempts": len(atts),
            "approved_phases": approved,
            "cost_per_approved_phase": (round(cost / len(approved), 4) if approved else None),
            "non_approved_cost_usd": non_approved,
            "non_approved_cost_share": (round(non_approved / cost, 3) if cost else None),
            "tampered": run.tampered,
        }

    caps: Dict[int, List[dict]] = {}
    for run in runs.values():
        for ph, cap in run.caps.items():
            caps.setdefault(ph, []).append(cap)
    phases_out = _phases(attempts_out, caps, phase_shapes)

    cost = round(sum(a["cost_usd"] for a in attempts_out), 4)
    non_approved = round(sum(a["cost_usd"] for a in attempts_out if a["verdict"] != "APPROVED"), 4)
    approved = sorted({a["phase"] for a in attempts_out if a["verdict"] == "APPROVED"})
    tool = sum(a["tool_calls"] for a in attempts_out)
    wait = sum(a["wait_calls"] for a in attempts_out)
    kills: Counter = Counter()
    outcomes: Counter = Counter()
    for a in attempts_out:
        kills.update(a["kills_by_reason"])
        outcomes.update(a["outcomes"])
    totals = {
        "runs": len(runs_out),
        "attempts": len(attempts_out),
        "passes": sum(a["passes"] for a in attempts_out),
        "cost_usd": cost,
        "unbilled_passes": sum(a["unbilled_passes"] for a in attempts_out),
        "kills_by_reason": dict(kills),
        "outcomes": dict(outcomes),
        "approved_phases": approved,
        "cost_per_approved_phase": (round(cost / len(approved), 4) if approved else None),
        "non_approved_cost_share": (round(non_approved / cost, 3) if cost else None),
        "wait_call_share": (round(wait / tool, 3) if tool else None),
        "sleep_sec_declared": round(sum(a["sleep_sec_declared"] for a in attempts_out), 1),
    }
    if run_log_live_invocations is not None:
        totals["proposer_live_invocations"] = run_log_live_invocations

    return {
        "schema": SCHEMA,
        "runs": runs_out,
        "attempts": attempts_out,
        "phases": phases_out,
        "totals": totals,
    }


def _is_sample(p: dict) -> bool:
    """Item 4's sample unit: a billed pass not killed for futility. An unbilled pass
    (no cost: a kill severed the stream) is no cost sample, and a futility kill's
    duration means nothing; both arms use this one filter."""
    return p.get("billed") is True and p.get("kill_class") != "futility"


def _episodes(atts: List[dict]) -> List[dict]:
    """One entry per run that touched the phase: that run's phase_start→phase_done
    window, with its cost, wall-clock, billed passes and whether it approved."""
    by_run: "OrderedDict[str, List[dict]]" = OrderedDict()
    for a in atts:
        by_run.setdefault(a["run"], []).append(a)
    out = []
    for run, group in by_run.items():
        starts = [a["started"] for a in group if a["started"] is not None]
        ends = [a["ended"] for a in group if a["ended"] is not None]
        out.append({
            "run": run,
            "cost_usd": round(sum(a["cost_usd"] for a in group), 4),
            "wall_sec": (round(max(ends) - min(starts), 1) if starts and ends else None),
            "passes": sum(1 for a in group for p in a["passes_detail"] if _is_sample(p)),
            "approved": any(a["verdict"] == "APPROVED" for a in group),
        })
    return out


def _phases(attempts: List[dict], caps: Optional[Dict[int, List[dict]]] = None,
            phase_shapes: Optional[Dict[int, str]] = None) -> "OrderedDict[str, dict]":
    caps = caps or {}
    phase_shapes = phase_shapes or {}
    by_phase: Dict[int, List[dict]] = {}
    for a in attempts:
        by_phase.setdefault(a["phase"], []).append(a)
    out: "OrderedDict[str, dict]" = OrderedDict()
    for ph in sorted(by_phase):
        atts = by_phase[ph]   # stream order == chronological across runs when files are given in order
        shape = phase_shapes.get(ph)
        other_shape = 0
        if shape:
            kept = [a for a in atts if a.get("shape") == shape]
            other_shape = len(atts) - len(kept)
            if not kept:
                continue
            atts = kept
        approved_idx = next((i for i, a in enumerate(atts) if a["verdict"] == "APPROVED"), None)
        runs_seen = []
        for a in atts:
            if a["run"] not in runs_seen:
                runs_seen.append(a["run"])
        first_attempts = [a for a in atts if a["attempt"] == 1]
        work = [a["work_sec_estimate"] for a in atts
                if a["work_sec_estimate"] is not None
                and not any(k in KILL_CLASS and KILL_CLASS[k] == "futility" for k in a["kills_by_reason"])]
        kills: Counter = Counter()
        for a in atts:
            kills.update(a["kills_by_reason"])
        # Per-*pass* wait/work and yield-job evidence (design §2.1/§2.2), the same
        # futility exclusion as `work` above but at pass granularity — a phase's
        # *passes* are what §5.5's "high wait share or hard-cap kills" is about,
        # not its attempts (one attempt can hold several passes).
        pass_rows = [p for a in atts for p in a["passes_detail"]]
        non_futile_passes = [p for p in pass_rows if p["kill_class"] != "futility"]
        wait_shares = [p["wait_calls"] / p["tool_calls"] for p in non_futile_passes if p["tool_calls"]]
        hard_cap_with_wait = sum(1 for p in pass_rows
                                  if p["kill_reason"] in BUDGET_REASONS and p["wait_calls"] > 0)
        job_durations = [p["job_duration_sec"] for p in non_futile_passes
                         if p.get("job_duration_sec") is not None]
        out[str(ph)] = {
            "title": next((a["title"] for a in atts if a["title"]), ""),
            # the shape these figures were restricted to (None: unrestricted), and how
            # many attempts of the phase number were left out for another shape
            "shape": shape,
            "other_shape_attempts": other_shape,
            "runs_seen": runs_seen,
            "attempts_total": len(atts),
            "attempts_to_approval": (approved_idx + 1 if approved_idx is not None else None),
            "approved_in_run": (atts[approved_idx]["run"] if approved_idx is not None else None),
            "attempt_number_reset": len({a["run"] for a in first_attempts}) > 1,
            "cost_usd": round(sum(a["cost_usd"] for a in atts), 4),
            "work_sec_p50": percentile(work, 0.5),
            "work_sec_p90": percentile(work, 0.9),
            # design §5.5 invariant 4 (the evidence floor): the sample count *behind*
            # the percentiles above — futility-killed attempts already excluded from
            # `work`, so this is exactly the denominator `advise`/`apply` must check.
            "work_sec_samples": len(work),
            "kills_by_reason": dict(kills),
            # `yield_poll_interval`'s evidence (§2.1): the length of the jobs this
            # phase's passes actually yielded on, futility-killed passes excluded.
            "job_duration_p50": percentile(job_durations, 0.5),
            "job_duration_samples": len(job_durations),
            # §2.2's wait/work evidence: how much of a pass's tool calls were
            # spent waiting, plus whether a hard_cap (budget) kill ever landed on
            # a pass that was busy waiting rather than working. Measurement only.
            "wait_share_p50": percentile(wait_shares, 0.5),
            "wait_share_samples": len(wait_shares),
            "hard_cap_kills_with_wait": hard_cap_with_wait,
            # item 4's primaries, per phase episode (one run's window on this phase),
            # and its sample unit: every billed, non-futility pass, tagged with its run
            "episodes": _episodes(atts),
            "pass_samples": [{"run": a["run"], "cost_usd": p["cost_usd"], "elapsed_sec": p["elapsed_sec"]}
                             for a in atts for p in a["passes_detail"] if _is_sample(p)],
            # what each run actually ran this phase under (proposer_cap, per run)
            "caps": [c for c in caps.get(ph, []) if c["run"] in runs_seen],
            # the diagnostician's declared case per consulted attempt (read-only):
            # "the proposer cites badly" vs "the work is incomplete"
            "diagnostician_cases": dict(Counter(a["diagnostician_case"] for a in atts
                                                if a.get("diagnostician_case"))),
        }
    return out


# ── optional run.log cross-check ─────────────────────────────────────────────
def count_live_invocations(run_log_path: str, pattern: str) -> int:
    """Proposer-initiated tool lines (``  → Bash …``) matching ``pattern``."""
    rx = re.compile(pattern)
    n = 0
    with open(run_log_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.lstrip()
            if s.startswith("→ ") and rx.search(s):
                n += 1
    return n


# ── the learning loop (design §5 / §6 step 5) ───────────────────────────────
#
# ADJUSTABLE_KNOBS is the §5.5 allowlist — the *only* names `apply`/`resolve` may
# ever act on. It is a locked literal set on purpose (see the test that asserts
# it): a critic-facing knob (grounding caps, critic backend/timeout, --max-rejects,
# anything in verification-commands.json) or a breaker setting (MAX_ERRORS,
# MAX_NOPROGRESS, MAX_CAPS, REPEAT_LIMIT/REPEAT_IGNORE) must never be addable here
# without deliberately editing that test.
#
# §5.5 also listed `inject_yield_hint` ("prepend the yield contract to phase N's
# prompt"). It was removed rather than wired: orchestrator.sh already appends the
# yield contract, last and unconditionally, to every proposer and accelerator
# prompt, so there was nothing to switch on; the only change left to make was to
# the prompt-budget guard, and that is a prompt the critic later judges. Narrowing
# the allowlist can never weaken the gate.
ADJUSTABLE_KNOBS = frozenset({
    "proposer_timeout",      # §4.1 per-phase proposer cap — bound [900, 2×default], ±50%/step
    "yield_poll_interval",   # §2.1 wait_for_yield poll cadence — bound [10, 300] s
})

# Hard bounds per §5.5. `proposer_timeout`'s upper bound is relative to the
# caller-supplied default (2×), so it is computed at suggestion time, not fixed here.
KNOB_HARD_MIN = {"proposer_timeout": 900, "yield_poll_interval": 10}
KNOB_HARD_MAX_FIXED = {"yield_poll_interval": 300}   # proposer_timeout: 2 × default
STEP_CAP_FRACTION = 0.5   # "≤ ±50% per step" — every numeric adjustable knob

# `yield_poll_interval`'s target: a poll of T seconds wastes up to T seconds of a
# finished job's result sitting unnoticed (§2.1). Keeping T at roughly a tenth of
# the job's own measured length keeps that waste a small fraction of the job
# instead of a fixed cost that dominates a short one; the [10, 300] hard bound
# and the ±50%-per-step cap (shared with every numeric knob, above) still apply.
YIELD_POLL_WASTE_FRACTION = 0.1


# /2 entries carry "shape" (the phase-shape digest they were learned under); /1
# entries, and any entry without a shape, are still read but never match a shape.
LEARN_APPLIED_SCHEMA = "specstride.learn.applied/2"
LEARN_APPLIED_SCHEMAS_READ = ("specstride.learn.applied/1", LEARN_APPLIED_SCHEMA)
OBSERVATION_SCHEMA = "specstride.learn.observation/1"


def _now_ts() -> str:
    return str(time.time())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_run_id() -> str:
    return "learn-" + uuid.uuid4().hex[:12]


def _read_jsonl(path: Optional[str]) -> List[dict]:
    if not path or not os.path.isfile(path):
        return []
    out: List[dict] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _append_jsonl(path: Optional[str], obj: dict) -> None:
    if not path:
        return
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, sort_keys=False) + "\n")


def _feature_paths(feature_dir: Optional[str], applied_file: Optional[str],
                    events_file: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """§5.4: applied.json lives under <feature-dir>/learning/, separate from the
    feature's own events.jsonl that `knob_adjusted` is appended to. Explicit
    --applied-file/--events-file win over anything derived from --feature-dir."""
    if not applied_file and feature_dir:
        applied_file = os.path.join(feature_dir, "learning", "applied.json")
    if not events_file and feature_dir:
        events_file = os.path.join(feature_dir, "events.jsonl")
    return applied_file, events_file


def _entry_shape(entry: dict) -> Optional[str]:
    return entry.get("shape") or None


def _is_decision(entry: dict) -> bool:
    """`apply` and `revert` entries are decisions; an `evaluate` entry is a record
    about one and never moves a value (entries from before `action` existed count)."""
    return entry.get("action") in (None, "apply", "revert")


def bound_entries(entries: List[dict], through: Optional[str]) -> List[dict]:
    """The decision log as a contract bound it (item 6): with `through` — the run
    id a MoL contract pinned as SPECSTRIDE_LEARNING_THROUGH — every `apply` after
    that entry is ignored until a re-derivation binds it. Reverts after it still
    count (an auto-revert, a `--revert`, `--off`): they only move a knob toward its
    default, so a bound value is an upper bound on what runs, not a promise. A
    `through` that names no entry binds no applies at all."""
    if not through:
        return entries
    cut = next((i for i, e in enumerate(entries) if e.get("run_id") == through), None)
    if cut is None:
        return [e for e in entries if e.get("action") == "revert"]
    return entries[:cut + 1] + [e for e in entries[cut + 1:] if e.get("action") == "revert"]


def _decisions(applied_file: Optional[str], knob: str, phase: int, shape: Optional[str],
               through: Optional[str] = None) -> List[dict]:
    """The decision entries for one key, (knob, phase, shape), in order. An entry
    recorded without a shape matches only a shape-less lookup."""
    return [e for e in bound_entries(_read_jsonl(applied_file), through)
            if _is_decision(e) and e.get("knob") == knob and _int(e.get("phase")) == phase
            and _entry_shape(e) == (shape or None)]


def effective_value(applied_file: Optional[str], knob: str, phase: int, shape: Optional[str] = None,
                    through: Optional[str] = None):
    """Replay the append-only decision log to the current value of (knob, phase,
    shape), or None if nothing has ever been applied under that key. Both an
    `apply` and a `revert` entry record the resulting value under "value" — a
    `revert` restores the value that was current *before* the apply it targets —
    so a straight last-one-wins replay is correct for either kind of entry.

    The shape is part of the key: a decision learned for an earlier version of
    the phase (different title or criteria) never applies to the edited one, and
    one recorded before shapes existed never applies to any shape."""
    current = None
    for e in _decisions(applied_file, knob, phase, shape, through):
        current = e.get("value")
    return current


def unshaped_notice(applied_file: Optional[str], knob: str, phase: int, shape: Optional[str]) -> Optional[str]:
    """The notice printed when a shape is asked for and the log holds decisions for
    (knob, phase) recorded under no shape: they are never silently applied."""
    if not shape:
        return None
    n = sum(1 for e in _read_jsonl(applied_file)
            if _is_decision(e) and e.get("knob") == knob and _int(e.get("phase")) == phase
            and _entry_shape(e) is None)
    if not n:
        return None
    return (f"learn: notice — {n} decision(s) for {knob}[{phase}] predate phase-shape keys and are "
            f"not applied; re-derive with `specstride learn --apply` under the current spec")


def _round_step(value: float, step: int = 60) -> int:
    return int(round(value / step)) * step


def suggest_proposer_timeout(phase_stats: dict, default: int, current: Optional[int] = None) -> dict:
    """§4.1 + §5.3: the per-phase proposer cap, derived from this phase's measured
    ``work_sec`` (futility-killed passes already excluded upstream in `_phases`).
    Returns a value of None — never a number — below the 3-sample evidence floor
    (§5.5 invariant 4); the caller (`apply`) must refuse to act on that."""
    samples = _int(phase_stats.get("work_sec_samples")) or 0
    hard_lo, hard_hi = KNOB_HARD_MIN["proposer_timeout"], 2 * default
    if samples < 3:
        return {
            "knob": "proposer_timeout", "samples": samples, "value": None,
            "reason": f"fewer than 3 non-futility-killed samples (have {samples})",
            "bounds": [hard_lo, hard_hi], "step_cap": None,
        }
    raw = phase_stats.get("work_sec_p90")
    if raw is None:
        raw = phase_stats.get("work_sec_p50")
    target = float(raw or 0.0) * 1.25   # headroom over the observed p90 work time
    base = current if current is not None else default
    step_lo, step_hi = base * (1 - STEP_CAP_FRACTION), base * (1 + STEP_CAP_FRACTION)
    lo = max(hard_lo, step_lo)
    hi = min(hard_hi, step_hi)
    if lo > hi:   # a degenerate window (current sits outside the hard bounds already)
        lo, hi = hard_lo, hard_hi
    value = min(max(target, lo), hi)
    value = _round_step(value)
    value = min(max(value, hard_lo), hard_hi)   # rounding must never escape the hard bound
    return {
        "knob": "proposer_timeout", "samples": samples, "value": value, "reason": None,
        "bounds": [hard_lo, hard_hi], "step_cap": [round(step_lo), round(step_hi)],
        "work_sec_p50": phase_stats.get("work_sec_p50"), "work_sec_p90": phase_stats.get("work_sec_p90"),
    }


def suggest_yield_poll_interval(phase_stats: dict, default: int, current: Optional[int] = None) -> dict:
    """§2.1 + §5.5: the yield predicate's poll interval, derived from this phase's
    observed ``yield_resume`` job durations (futility-killed passes already
    excluded upstream in `_phases`, same as `suggest_proposer_timeout`). The
    target is `YIELD_POLL_WASTE_FRACTION` of the job's own measured length —
    enough that a missed tick wastes a small fraction of the job, not a fixed
    cost — clamped to the `[10, 300]` hard bound and a ±50%-per-step window
    around whatever value is currently in effect. Returns a value of None below
    the 3-sample evidence floor (§5.5 invariant 4)."""
    samples = _int(phase_stats.get("job_duration_samples")) or 0
    hard_lo = KNOB_HARD_MIN["yield_poll_interval"]
    hard_hi = KNOB_HARD_MAX_FIXED["yield_poll_interval"]
    if samples < 3:
        return {
            "knob": "yield_poll_interval", "samples": samples, "value": None,
            "reason": f"fewer than 3 non-futility-killed job-duration samples (have {samples})",
            "bounds": [hard_lo, hard_hi], "step_cap": None,
        }
    raw = phase_stats.get("job_duration_p50") or 0.0
    target = float(raw) * YIELD_POLL_WASTE_FRACTION
    base = current if current is not None else default
    step_lo, step_hi = base * (1 - STEP_CAP_FRACTION), base * (1 + STEP_CAP_FRACTION)
    lo = max(hard_lo, step_lo)
    hi = min(hard_hi, step_hi)
    if lo > hi:   # a degenerate window (current sits outside the hard bounds already)
        lo, hi = hard_lo, hard_hi
    value = min(max(target, lo), hi)
    value = int(round(value))
    value = min(max(value, hard_lo), hard_hi)   # rounding must never escape the hard bound
    return {
        "knob": "yield_poll_interval", "samples": samples, "value": value, "reason": None,
        "bounds": [hard_lo, hard_hi], "step_cap": [round(step_lo), round(step_hi)],
        "job_duration_p50": phase_stats.get("job_duration_p50"),
    }



# One entry per allowlisted knob; `advise` and `_cmd_apply` both dispatch
# through this rather than hand-testing `knob ==` chains, so adding another
# engine later means adding one entry here (plus, deliberately, editing the
# locked-allowlist test — see `ADJUSTABLE_KNOBS` above).
SUGGESTION_ENGINES = {
    "proposer_timeout": lambda stats, default, current: suggest_proposer_timeout(stats, default, current),
    "yield_poll_interval": lambda stats, default, current: suggest_yield_poll_interval(stats, default, current),
}


def advise(summary: dict, knob: str, phase: Optional[int], default: int,
           applied_file: Optional[str] = None, shapes: Optional[Dict[int, str]] = None) -> List[dict]:
    """One advice dict per phase (or just `phase` if given), dispatched to
    `knob`'s entry in `SUGGESTION_ENGINES`. Every allowlisted knob has an
    engine; a `knob` outside that map (never reachable through the CLI,
    whose `--knob` choices are the allowlist itself) raises ValueError."""
    if knob not in SUGGESTION_ENGINES:
        raise ValueError(f"learn: no suggestion engine yet for knob {knob!r}")
    phases = summary.get("phases", {})
    keys = [str(phase)] if phase is not None else sorted(phases, key=lambda k: _int(k) or 0)
    out = []
    for k in keys:
        stats = phases.get(k)
        if stats is None:
            continue
        ph = _int(k)
        shape = (shapes or {}).get(ph)
        current = effective_value(applied_file, knob, ph, shape) if applied_file else None
        adv = SUGGESTION_ENGINES[knob](stats, default, current)
        adv.update({"phase": ph, "shape": shape, "current": current if current is not None else default,
                    "runs_seen": stats.get("runs_seen", [])})
        out.append(adv)
    return out


def apply_proposer_timeout(summary: dict, phase: int, default: int, applied_file: str,
                            events_file: Optional[str] = None, run_id: Optional[str] = None,
                            shape: Optional[str] = None, force: bool = False) -> dict:
    """Compute the suggestion for `phase` and, if it clears the evidence floor,
    append one decision to `applied_file` and one `knob_adjusted` event to
    `events_file`. Raises ValueError (never writes) when the floor isn't met or
    the phase has no data — the caller reports that and exits non-zero."""
    stats = summary.get("phases", {}).get(str(phase))
    if stats is None:
        raise ValueError(f"no telemetry for phase {phase}")
    current = effective_value(applied_file, "proposer_timeout", phase, shape)
    adv = suggest_proposer_timeout(stats, default, current)
    if adv["value"] is None:
        raise ValueError(adv["reason"])
    previous = current if current is not None else default
    baseline = record_baseline(summary, phase, shape, stats.get("runs_seen", []))
    blocked = quarantine_block(applied_file, summary, "proposer_timeout", phase, shape, baseline["backend"], adv["value"])
    if blocked is not None and not force:
        raise QuarantinedError(
            f"{adv['value']} is within ±{int(QUARANTINE_BAND * 100)}% of {blocked['quarantined_value']}, "
            f"auto-reverted by {blocked['run_id']} (reverting {blocked['reverts_run_id']}, guardrail "
            f"{','.join(blocked.get('guardrail') or [])}); {blocked['fresh_samples']} of {QUARANTINE_SAMPLES} new "
            f"samples since — pass --force to apply anyway")
    run_id = run_id or _new_run_id()
    entry = {
        "schema": LEARN_APPLIED_SCHEMA, "action": "apply", "run_id": run_id,
        "knob": "proposer_timeout", "phase": phase, "shape": shape, "value": adv["value"], "previous": previous,
        "samples": adv["samples"], "source_runs": stats.get("runs_seen", []),
        "metric": "work_sec_p90", "applied_at": _now_iso(),
        "baseline": baseline,
    }
    if blocked is not None:
        entry["force"] = True
        entry["forced_over"] = blocked["run_id"]
    _append_jsonl(applied_file, entry)
    _append_jsonl(events_file, {
        "event": "knob_adjusted", "ts": _now_ts(), "knob": "proposer_timeout", "phase": phase,
        "from": previous, "to": adv["value"], "reason": "learned_from_work_sec_p90",
        "metric": "work_sec_p90", "samples": adv["samples"], "run_id": run_id,
    })
    return entry


def apply_yield_poll_interval(summary: dict, phase: int, default: int, applied_file: str,
                               events_file: Optional[str] = None, run_id: Optional[str] = None,
                               shape: Optional[str] = None, force: bool = False) -> dict:
    """`apply_proposer_timeout`'s counterpart for `yield_poll_interval`: same
    provenance shape, same append-only `applied_file`/`events_file`, same
    ValueError-and-write-nothing refusal below the evidence floor."""
    stats = summary.get("phases", {}).get(str(phase))
    if stats is None:
        raise ValueError(f"no telemetry for phase {phase}")
    current = effective_value(applied_file, "yield_poll_interval", phase, shape)
    adv = suggest_yield_poll_interval(stats, default, current)
    if adv["value"] is None:
        raise ValueError(adv["reason"])
    previous = current if current is not None else default
    baseline = record_baseline(summary, phase, shape, stats.get("runs_seen", []))
    blocked = quarantine_block(applied_file, summary, "yield_poll_interval", phase, shape, baseline["backend"], adv["value"])
    if blocked is not None and not force:
        raise QuarantinedError(
            f"{adv['value']} is within ±{int(QUARANTINE_BAND * 100)}% of {blocked['quarantined_value']}, "
            f"auto-reverted by {blocked['run_id']} (reverting {blocked['reverts_run_id']}, guardrail "
            f"{','.join(blocked.get('guardrail') or [])}); {blocked['fresh_samples']} of {QUARANTINE_SAMPLES} new "
            f"samples since — pass --force to apply anyway")
    run_id = run_id or _new_run_id()
    entry = {
        "schema": LEARN_APPLIED_SCHEMA, "action": "apply", "run_id": run_id,
        "knob": "yield_poll_interval", "phase": phase, "shape": shape, "value": adv["value"], "previous": previous,
        "samples": adv["samples"], "source_runs": stats.get("runs_seen", []),
        "metric": "job_duration_p50", "applied_at": _now_iso(),
        "baseline": baseline,
    }
    if blocked is not None:
        entry["force"] = True
        entry["forced_over"] = blocked["run_id"]
    _append_jsonl(applied_file, entry)
    _append_jsonl(events_file, {
        "event": "knob_adjusted", "ts": _now_ts(), "knob": "yield_poll_interval", "phase": phase,
        "from": previous, "to": adv["value"], "reason": "learned_from_job_duration_p50",
        "metric": "job_duration_p50", "samples": adv["samples"], "run_id": run_id,
    })
    return entry



# Dispatch table mirroring `SUGGESTION_ENGINES`.
APPLY_ENGINES = {
    "proposer_timeout": lambda summary, phase, default, applied_file, events_file, run_id, shape, force:
        apply_proposer_timeout(summary, phase, default, applied_file,
                                events_file=events_file, run_id=run_id, shape=shape, force=force),
    "yield_poll_interval": lambda summary, phase, default, applied_file, events_file, run_id, shape, force:
        apply_yield_poll_interval(summary, phase, default, applied_file,
                                   events_file=events_file, run_id=run_id, shape=shape, force=force),
}


def revert_run(run_id: str, applied_file: str, events_file: Optional[str] = None,
               extra: Optional[dict] = None, reason: str = "revert") -> dict:
    """§5.5 invariant 3: undo exactly one prior `apply`, restoring the value that
    was current before it. Raises ValueError if `run_id` names no apply entry, or
    if it is not the *currently active* decision for its (knob, phase) — reverting
    a superseded apply (one a later apply or revert has already overtaken) would
    silently clobber whatever replaced it, since the log is replayed last-one-wins;
    only the entry currently on top of that stack may be undone."""
    entries = _read_jsonl(applied_file)
    orig = None
    for e in entries:
        if e.get("action") == "apply" and e.get("run_id") == run_id:
            orig = e
    if orig is None:
        raise ValueError(f"no applied entry with run_id {run_id!r}")
    latest = None
    for e in entries:
        if (_is_decision(e) and e.get("knob") == orig["knob"] and _int(e.get("phase")) == orig["phase"]
                and _entry_shape(e) == _entry_shape(orig)):
            latest = e
    if not (latest is not None and latest.get("action") == "apply" and latest.get("run_id") == run_id):
        raise ValueError(f"run_id {run_id!r} is not the active decision for "
                          f"{orig['knob']}[{orig['phase']}] — nothing to revert")
    entry = {
        "schema": LEARN_APPLIED_SCHEMA, "action": "revert", "run_id": _new_run_id(),
        "reverts_run_id": run_id, "knob": orig["knob"], "phase": orig["phase"], "shape": _entry_shape(orig),
        "value": orig["previous"], "previous": orig["value"], "applied_at": _now_iso(),
    }
    entry.update(extra or {})
    _append_jsonl(applied_file, entry)
    _append_jsonl(events_file, {
        "event": "knob_adjusted", "ts": _now_ts(), "knob": orig["knob"], "phase": orig["phase"],
        "from": orig["value"], "to": orig["previous"], "reason": reason,
        "samples": None, "metric": None, "run_id": entry["run_id"], "reverts_run_id": run_id,
    })
    return entry


def revert_all(applied_file: str, events_file: Optional[str] = None) -> List[dict]:
    """`specstride learn --off`: revert every (knob, phase) currently at a non-default
    value, in one pass — the bulk form of `revert_run` for "turn learning off"."""
    latest: "OrderedDict[Tuple[str, int, Optional[str]], dict]" = OrderedDict()
    for e in _read_jsonl(applied_file):
        if not _is_decision(e):
            continue
        key = (e.get("knob"), _int(e.get("phase")), _entry_shape(e))
        latest[key] = e
    out = []
    for e in latest.values():
        if e.get("action") == "revert":
            continue   # already at baseline
        out.append(revert_run(e["run_id"], applied_file, events_file))
    return out


def resolve_knob(knob: str, phase: int, default: int, applied_file: Optional[str],
                  env: Optional[dict] = None, shape: Optional[str] = None) -> int:
    """The one integration point (§6 step 5): what another component (e.g. a future
    `resolve_proposer_timeout`) calls to get this run's value for `knob`/`phase`.

    Total no-op — the applied log is not even opened — unless the environment sets
    SPECSTRIDE_LEARNING=apply. Unset, "off", "suggest", or any other value all return
    `default` unchanged: this is what makes "SPECSTRIDE_LEARNING unset or off" and the
    default "suggest" mode both zero-behaviour-change (§5.5 invariant 5; the
    migration table in the design doc).

    `shape` is the phase's current shape digest: a decision recorded under
    another shape, or under none, resolves to `default`. SPECSTRIDE_LEARNING_THROUGH
    (set by a MoL contract, item 6) ignores applies newer than the run id it names.

    Every knob resolves to an `int`, so a shell caller never has to branch on type."""
    env = os.environ if env is None else env
    if env.get("SPECSTRIDE_LEARNING") != "apply":
        return int(default)
    if knob not in ADJUSTABLE_KNOBS:
        return int(default)
    value = effective_value(applied_file, knob, phase, shape, env.get("SPECSTRIDE_LEARNING_THROUGH") or None)
    if value is None:
        return int(default)
    if knob not in KNOB_HARD_MIN:
        # a numeric-hard-bound-less knob with no apply engine has no clamp to
        # invent — never guess one; this stays defensive dead code unless a
        # future knob is added to the allowlist without also updating this.
        return int(default)
    hard_lo = KNOB_HARD_MIN[knob]
    hard_hi = KNOB_HARD_MAX_FIXED.get(knob, 2 * default)   # proposer_timeout: 2×default
    return int(min(max(int(value), hard_lo), hard_hi))


# ── evaluate: did an applied decision help? (item 4; 05-evaluate-design.md) ──
#
# The corpus allows no significance test (design §1): a decision is labelled by
# comparing its effect with the minimum detectable effect at the arm sizes it has,
# and the MDE is always reported beside the effect, so "neutral" reads as "nothing
# this large could be seen", never as "no effect".
EVAL_FLOOR = 6            # billed non-futility passes per arm, below which: insufficient
EVAL_Z = 2.80             # z(0.975) + z(0.80): 5 % two-sided, 80 % power
EVAL_SD_FLOOR = 0.50      # floor on the pooled within-phase sd of log(x)
EVAL_ICC = 0.5            # assumed intra-episode correlation (the corpus cannot estimate it)
LABELS = ("helped", "neutral", "regressed", "insufficient")
# log() needs a positive value: a pass billed at $0 or timed at 0 s is floored here
_LOG_FLOOR = {"cost": 1e-4, "wall": 1.0}
# the proposer_cap fields that say which value a run's passes ran under, per knob
_CAP_FIELDS = {
    "proposer_timeout": ("seconds", "source", "arm"),
    "yield_poll_interval": ("yield_poll", "yield_poll_source", "yield_poll_arm"),
}


def mde(s: float, n_a: int, n_b: int, mbar: float = 1.0) -> float:
    """Minimum detectable effect on log(x): 2.80 · s · sqrt(1/n_a + 1/n_b) · sqrt(DEFF),
    s floored at 0.50, DEFF = 1 + (m̄ − 1)·0.5 for m̄ passes per episode."""
    deff = 1.0 + (max(mbar, 1.0) - 1.0) * EVAL_ICC
    return EVAL_Z * max(s, EVAL_SD_FLOOR) * math.sqrt(1.0 / n_a + 1.0 / n_b) * math.sqrt(deff)


def compare_arms(applied: List[Tuple[str, float]], baseline: List[Tuple[str, float]], kind: str) -> dict:
    """One primary: `applied`/`baseline` are (run, value) samples; the run is the
    episode a pass clusters in."""
    floor = _LOG_FLOOR[kind]
    la = [math.log(max(v, floor)) for _, v in applied if v is not None]
    lb = [math.log(max(v, floor)) for _, v in baseline if v is not None]
    n_a, n_b = len(la), len(lb)
    out = {"n_a": n_a, "n_b": n_b, "r": None, "mde": None, "s": None, "deff": None, "label": "insufficient"}
    if n_a < EVAL_FLOOR or n_b < EVAL_FLOOR:
        return out
    ma, mb = statistics.fmean(la), statistics.fmean(lb)
    ss = sum((x - ma) ** 2 for x in la) + sum((x - mb) ** 2 for x in lb)
    sd = math.sqrt(ss / (n_a + n_b - 2))
    episodes = len({r for r, v in applied if v is not None}) + len({r for r, v in baseline if v is not None})
    mbar = (n_a + n_b) / max(episodes, 1)
    r = ma - mb
    m = mde(sd, n_a, n_b, mbar)
    label = "helped" if r <= -m else "regressed" if r >= m else "neutral"
    out.update({"r": round(r, 4), "mde": round(m, 4), "s": round(max(sd, EVAL_SD_FLOOR), 4),
                "sd": round(sd, 4), "deff": round(1.0 + (mbar - 1.0) * EVAL_ICC, 4),
                "mbar": round(mbar, 3), "label": label})
    return out


def _combine(cost: dict, wall: dict) -> str:
    labels = (cost["label"], wall["label"])
    if "regressed" in labels:
        return "regressed"
    if "helped" in labels:
        return "helped"
    if "neutral" in labels:
        return "neutral"
    return "insufficient"


def _phase_attempts(summary: dict, phase: int, shape: Optional[str], runs) -> List[dict]:
    runs = set(runs)
    return [a for a in summary.get("attempts", [])
            if a["phase"] == phase and a["run"] in runs and (not shape or a.get("shape") == shape)]


def _samples(attempts: List[dict]) -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]]]:
    cost, wall = [], []
    for a in attempts:
        for p in a["passes_detail"]:
            if _is_sample(p):
                cost.append((a["run"], p["cost_usd"]))
                if p["elapsed_sec"] is not None:
                    wall.append((a["run"], p["elapsed_sec"]))
    return cost, wall


# the stop reasons that hand a phase back to a human: the arbitration proxy (no
# event records arbitration itself; 05-evaluate-design.md §2.6)
ARBITRATION_STOPS = ("max_rejects", "gate_oscillation", "critic_config", "proposer_no_progress")


def guardrail_counts(attempts: List[dict], runs_meta: dict, phase: int) -> dict:
    """The raw counts every guardrail needs, for one arm."""
    verdicts = [a for a in attempts if a.get("verdict")]
    first = [a for a in verdicts if a["attempt"] == 1]
    cases = [a["diagnostician_case"] for a in attempts if a.get("diagnostician_case")]
    episodes = sorted({a["run"] for a in attempts})
    return {
        "attempts": len(attempts),
        "verdicts": len(verdicts),
        "malformed": sum(1 for a in verdicts if a["verdict"] == "MALFORMED"),
        "grounding_gap": sum(1 for a in verdicts if a.get("grounding_gap_paths")),
        "verification_failed": sum(1 for a in attempts if a.get("verification") == "failed"),
        "first_verdicts": len(first),
        "first_approved": sum(1 for a in first if a["verdict"] == "APPROVED"),
        "diagnostician_cases": len(cases),
        "diagnostician_grounding": sum(1 for c in cases if c == "grounding"),
        "episodes": len(episodes),
        "arbitration": sum(1 for r in episodes
                           if (runs_meta.get(r) or {}).get("stop_reason") in ARBITRATION_STOPS
                           and (runs_meta.get(r) or {}).get("stop_phase") == phase),
        "critic_prompt_bytes": [a["critic_prompt_bytes"] for a in attempts
                                if a.get("critic_prompt_bytes") is not None],
    }


def record_baseline(summary: dict, phase: int, shape: Optional[str], source_runs: List[str]) -> dict:
    """What `apply` stores beside a decision: the samples that produced it, under
    the backend label of the most recent source run (runs under another label are
    left out, not mixed in). The label, not a model version, is the reset key:
    nothing in the event stream records a model version."""
    runs_meta = summary.get("runs", {})
    backend = next((runs_meta.get(r, {}).get("backend") for r in reversed(source_runs)
                    if r in runs_meta), None)
    base_runs = [r for r in source_runs if runs_meta.get(r, {}).get("backend") == backend
                 and not runs_meta.get(r, {}).get("tampered")]
    atts = _phase_attempts(summary, phase, shape, base_runs)
    cost, wall = _samples(atts)
    return {"backend": backend, "shape": shape, "runs": base_runs,
            "cost": [[r, v] for r, v in cost], "wall": [[r, v] for r, v in wall],
            "guardrails": guardrail_counts(atts, runs_meta, phase)}


def _applied_runs(summary: dict, decision: dict) -> List[str]:
    """The runs whose passes of this phase ran under the decision: by the recorded
    arm when proposer_cap carries one, else by source `learned` at the decision's
    value. The decision's own source runs are never in its applied arm."""
    value_f, source_f, arm_f = _CAP_FIELDS[decision["knob"]]
    caps = (summary.get("phases", {}).get(str(decision["phase"])) or {}).get("caps") or []
    source = set(decision.get("source_runs") or [])
    out = []
    for c in caps:
        if c["run"] in source:
            continue
        arm = c.get(arm_f)
        if arm is not None:
            # a run that recorded an arm but no tamper bracket is excluded, not trusted
            hit = (arm == "applied" and c.get(value_f) == _int(decision.get("value"))
                   and c.get("tamper_bracket") == "on")
        else:
            hit = c.get(source_f) == "learned" and c.get(value_f) == _int(decision.get("value"))
        if hit:
            out.append(c["run"])
    return out


def counterfactual(decision: dict) -> dict:
    """What the recorded passes can say without a run under the new value
    (05-evaluate-design.md §2.9). Informational only; never a label input."""
    base = decision.get("baseline") or {}
    value, previous = _int(decision.get("value")), _int(decision.get("previous"))
    if decision["knob"] != "proposer_timeout" or value is None or previous is None or value == previous:
        return {"kind": "none"}
    if value > previous:
        return {"kind": "censored",
                "note": "a longer cap cannot be evaluated from logs: a killed pass does not show how long it would have run"}
    wall = [(r, v) for r, v in base.get("wall") or []]
    capped = [(r, min(v, value)) for r, v in wall]
    return {"kind": "shorter_cap_wall_only",
            "note": "wall-clock only: a truncated pass's cost is unobserved",
            "wall": compare_arms(capped, wall, "wall")}


def evaluate_decision(summary: dict, decision: dict, current_shape: Optional[str],
                      excluded_runs=()) -> dict:
    """Label one applied decision against its recorded baseline."""
    base = decision.get("baseline")
    phase = _int(decision["phase"])
    result = {"knob": decision["knob"], "phase": phase, "shape": _entry_shape(decision),
              "evaluates_run_id": decision["run_id"], "value": decision.get("value"),
              "previous": decision.get("previous"), "reset": None, "applied_runs": [],
              "counterfactual": counterfactual(decision)}
    empty = compare_arms([], [], "cost")
    if not base:
        result.update(label="insufficient", reason="no baseline recorded (applied before evaluate existed)",
                      cost=empty, wall=dict(empty))
        return result
    backend = base.get("backend")
    result["backend"] = backend
    if (current_shape or None) != _entry_shape(decision):
        result.update(label="insufficient", reset="shape", cost=empty, wall=dict(empty),
                      reason="the phase's shape changed since the decision; its baseline no longer applies")
        return result
    runs_meta = summary.get("runs", {})
    phase_runs = (summary.get("phases", {}).get(str(phase)) or {}).get("runs_seen") or []
    latest = next((r for r in reversed(phase_runs) if r in runs_meta), None)
    if latest is not None and latest not in (base.get("runs") or []) \
            and runs_meta[latest].get("backend") != backend:
        result.update(label="insufficient", reset="backend", cost=empty, wall=dict(empty),
                      reason=f"backend changed from {backend!r} to {runs_meta[latest].get('backend')!r}; "
                             "applied-arm samples discarded")
        return result
    excluded = set(excluded_runs) | {r for r, m in runs_meta.items() if m.get("tampered")}
    applied = [r for r in _applied_runs(summary, decision)
               if r not in excluded and runs_meta.get(r, {}).get("backend") == backend]
    result["applied_runs"] = applied
    result["excluded_runs"] = sorted(excluded & set(_applied_runs(summary, decision)))
    atts = _phase_attempts(summary, phase, _entry_shape(decision), applied)
    cost_a, wall_a = _samples(atts)
    cost = compare_arms(cost_a, [tuple(x) for x in base.get("cost") or []], "cost")
    wall = compare_arms(wall_a, [tuple(x) for x in base.get("wall") or []], "wall")
    applied_counts = guardrail_counts(atts, runs_meta, phase)
    result.update(cost=cost, wall=wall, label=_combine(cost, wall), applied_guardrails=applied_counts,
                  guardrails=evaluate_guardrails(base.get("guardrails") or guardrail_counts([], {}, phase),
                                                 applied_counts))
    return result


# ── guardrails (item 4b; 05-evaluate-design.md §2.6) ──────────────────────────
# A guardrail vetoes a decision even when a primary improved, and a breach reverts
# it. Rate guardrails use an exact one-sided binomial tail against the baseline
# rate; below its minimum n a guardrail is "unknown", never "ok".
GUARD_P = 0.01
GUARD_MIN_N = 10          # rate guardrails: applied-arm trials
GUARD_MIN_SIZE_N = 6      # critic input size: samples per arm
QUARANTINE_BAND = 0.10    # ±10 % of a quarantined value
QUARANTINE_SAMPLES = 6    # new non-futility samples before a quarantined value may return


def binom_upper(k: int, n: int, p: float) -> float:
    """P(X ≥ k) for X ~ Binomial(n, p), exactly (math.comb)."""
    return min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1)))


def binom_lower(k: int, n: int, p: float) -> float:
    """P(X ≤ k) for X ~ Binomial(n, p), exactly."""
    return min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, k + 1)))


def _base_rate(k: int, n: int) -> float:
    # shrunk by half a trial, so a baseline of 0/6 is not a certainty that turns
    # the first applied-arm event into p = 0
    return (k + 0.5) / (n + 1.0)


def _rate_guard(k_a: int, n_a: int, k_b: int, n_b: int, both: bool = False) -> dict:
    out = {"k_applied": k_a, "n_applied": n_a, "k_baseline": k_b, "n_baseline": n_b, "p": None}
    if n_a < GUARD_MIN_N or n_b < 1:
        return dict(out, state="unknown")
    rate = _base_rate(k_b, n_b)
    p_up = binom_upper(k_a, n_a, rate)
    p = min(p_up, binom_lower(k_a, n_a, rate)) if both else p_up
    return dict(out, p=round(p, 6), state="breach" if p < GUARD_P else "ok")


def _size_guard(sizes_a: List[int], sizes_b: List[int]) -> dict:
    """Exact rank test: K = how many applied-arm prompts are larger than every
    baseline prompt; P(K ≥ k) = C(n_a,k)/C(n_a+n_b,k) under exchangeability (K ≥ k
    exactly when the k largest prompts of both arms pooled are all applied)."""
    n_a, n_b = len(sizes_a), len(sizes_b)
    out = {"n_applied": n_a, "n_baseline": n_b, "p": None}
    if n_a < GUARD_MIN_SIZE_N or n_b < GUARD_MIN_SIZE_N:
        return dict(out, state="unknown")
    top = max(sizes_b)
    k = sum(1 for v in sizes_a if v > top)
    p = math.comb(n_a, k) / math.comb(n_a + n_b, k) if k else 1.0
    return dict(out, k=k, p=round(p, 6), state="breach" if p < GUARD_P else "ok")


def evaluate_guardrails(base: dict, applied: dict) -> "OrderedDict[str, dict]":
    """One state per guardrail: ok | breach | unknown. `base`/`applied` are
    `guardrail_counts` for the two arms."""
    g: "OrderedDict[str, dict]" = OrderedDict()
    g["grounding_gap_rate"] = _rate_guard(applied["grounding_gap"], applied["verdicts"],
                                          base["grounding_gap"], base["verdicts"])
    g["malformed_rate"] = _rate_guard(applied["malformed"], applied["verdicts"],
                                      base["malformed"], base["verdicts"])
    if applied["attempts"] == 0:
        g["verification"] = {"state": "unknown", "applied_failed": 0,
                             "baseline_failed": base["verification_failed"]}
    else:
        # any failure where the baseline had none is a breach, at any n
        breach = applied["verification_failed"] > 0 and base["verification_failed"] == 0
        g["verification"] = {"state": "breach" if breach else "ok",
                             "applied_failed": applied["verification_failed"],
                             "baseline_failed": base["verification_failed"]}
    g["diagnostician_grounding_share"] = _rate_guard(applied["diagnostician_grounding"],
                                                     applied["diagnostician_cases"],
                                                     base["diagnostician_grounding"],
                                                     base["diagnostician_cases"])
    g["critic_input_size"] = _size_guard(applied.get("critic_prompt_bytes") or [],
                                         base.get("critic_prompt_bytes") or [])
    # a PROXY: no event records human arbitration, only the stops that hand a phase back
    g["arbitration_proxy"] = _rate_guard(applied["arbitration"], applied["episodes"],
                                         base["arbitration"], base["episodes"])
    # an alarm in BOTH directions, never a reward and never a label term
    g["first_attempt_approval"] = _rate_guard(applied["first_approved"], applied["first_verdicts"],
                                              base["first_approved"], base["first_verdicts"], both=True)
    return g


def breaches(result: dict) -> List[str]:
    return [name for name, g in (result.get("guardrails") or {}).items() if g.get("state") == "breach"]


def act_on_evaluation(result: dict, applied_file: str, events_file: Optional[str]) -> Optional[dict]:
    """Record the evaluation and, on a guardrail breach, revert the decision: the
    only automatic write to a decision the loop may make, since a revert only ever
    moves a knob back toward its default. The log is re-read first. If a manual
    apply overtook the evaluated decision the revert is skipped, recorded as
    skipped_superseded, and never retried against the new top of the stack; after
    `--off` or a manual revert there is nothing to do."""
    names = breaches(result)
    if not names:
        return record_evaluation(result, applied_file, events_file)
    decision_id = result["evaluates_run_id"]
    decision = next((d for d in active_decisions(applied_file, result["phase"])
                     if d["run_id"] == decision_id), None)
    if decision is None:
        latest = None
        for e in _decisions(applied_file, result["knob"], result["phase"], result.get("shape")):
            latest = e
        if latest is None or latest.get("action") == "revert":
            return None                            # --off or a manual revert: a silent no-op
        return record_evaluation(dict(result, action_taken="skipped_superseded"), applied_file, events_file)
    entry = record_evaluation(dict(result, action_taken="auto_reverted"), applied_file, events_file)
    revert = revert_run(decision_id, applied_file, events_file, extra={
        "auto": True, "guardrail": names, "evaluated_from": entry["run_id"] if entry else None,
        "quarantined_value": decision.get("value"), "backend": result.get("backend"),
        "reverted_ts": time.time(),
    }, reason="auto_revert")
    _append_jsonl(events_file, {
        "event": "knob_auto_reverted", "ts": _now_ts(), "knob": result["knob"], "phase": result["phase"],
        "guardrail": ",".join(names), "from": decision.get("value"), "to": decision.get("previous"),
        "run_id": revert["run_id"], "reverts_run_id": decision_id,
    })
    return revert


def quarantine_block(applied_file: Optional[str], summary: dict, knob: str, phase: int,
                     shape: Optional[str], backend: Optional[str], value) -> Optional[dict]:
    """The auto-revert that quarantines `value` for (knob, phase, shape, backend),
    or None. A value within ±10 % of a quarantined one stays refused until
    QUARANTINE_SAMPLES new non-futility samples of the phase accrued after the
    revert; a shape or backend change is a different key, so it clears."""
    v = _float(value)
    if v is None:
        return None
    runs_meta = summary.get("runs", {})
    for e in reversed(_read_jsonl(applied_file)):
        if not (e.get("action") == "revert" and e.get("auto") and e.get("knob") == knob
                and _int(e.get("phase")) == phase and _entry_shape(e) == (shape or None)
                and e.get("backend") == backend):
            continue
        q = _float(e.get("quarantined_value"))
        if q is None or abs(v - q) > QUARANTINE_BAND * abs(q):
            continue
        after = _float(e.get("reverted_ts")) or 0.0
        fresh = [r for r, meta in runs_meta.items()
                 if (meta.get("started") or 0) > after and meta.get("backend") == backend]
        cost, _ = _samples(_phase_attempts(summary, phase, shape, fresh))
        if len(cost) < QUARANTINE_SAMPLES:
            return dict(e, fresh_samples=len(cost))
    return None


class QuarantinedError(ValueError):
    pass


def active_decisions(applied_file: Optional[str], phase: Optional[int] = None) -> List[dict]:
    """The apply entries currently in effect, one per (knob, phase, shape)."""
    latest: "OrderedDict[Tuple[str, int, Optional[str]], dict]" = OrderedDict()
    for e in _read_jsonl(applied_file):
        if _is_decision(e):
            latest[(e.get("knob"), _int(e.get("phase")), _entry_shape(e))] = e
    return [e for e in latest.values()
            if e.get("action") == "apply" and (phase is None or _int(e.get("phase")) == phase)]


def _last_evaluation(applied_file: Optional[str], run_id: str) -> Optional[dict]:
    last = None
    for e in _read_jsonl(applied_file):
        if e.get("action") == "evaluate" and e.get("evaluates_run_id") == run_id:
            last = e
    return last


def _signature(result: dict) -> tuple:
    return (result.get("label"), result.get("reset"), result.get("action_taken"),
            result["cost"]["n_a"], result["cost"]["n_b"], result["wall"]["n_a"], result["wall"]["n_b"],
            json.dumps(result.get("guardrails"), sort_keys=True))


def record_evaluation(result: dict, applied_file: str, events_file: Optional[str]) -> Optional[dict]:
    """Append one `evaluate` entry (never touching the apply entry) and emit
    `knob_evaluated` — only when the result differs from the last evaluation of
    the same decision, so re-approving a phase without new samples adds nothing."""
    last = _last_evaluation(applied_file, result["evaluates_run_id"])
    if last is not None and _signature(last) == _signature(result):
        return None
    entry = dict(result, schema=LEARN_APPLIED_SCHEMA, action="evaluate", run_id=_new_run_id(),
                 evaluated_at=_now_iso())
    _append_jsonl(applied_file, entry)
    _append_jsonl(events_file, {
        "event": "knob_evaluated", "ts": _now_ts(), "knob": result["knob"], "phase": result["phase"],
        "label": result["label"], "action": result.get("action_taken") or "evaluated",
        "reset": result.get("reset"),
        "cost_r": result["cost"]["r"], "cost_mde": result["cost"]["mde"],
        "wall_r": result["wall"]["r"], "wall_mde": result["wall"]["mde"],
        "n_applied": result["cost"]["n_a"], "n_baseline": result["cost"]["n_b"],
        "run_id": entry["run_id"], "evaluates_run_id": result["evaluates_run_id"],
    })
    return entry


def _fmt_primary(name: str, c: dict) -> str:
    if c["r"] is None:
        return f"{name} n={c['n_a']}/{c['n_b']} (need {EVAL_FLOOR}/{EVAL_FLOOR})"
    return f"{name} r={c['r']:+.2f} (MDE ±{c['mde']:.2f}, n={c['n_a']}/{c['n_b']})"


def format_evaluation(result: dict) -> str:
    unit = _unit_suffix(result["knob"])
    head = (f"learn: phase {result['phase']} {result['knob']} {result.get('previous')}{unit}→"
            f"{result.get('value')}{unit} [{result['evaluates_run_id']}]: {result['label']}")
    if result.get("reset"):
        head += f" (baseline reset: {result['reset']})"
    elif result.get("reason"):
        head += f" ({result['reason']})"
    tail = f" — {_fmt_primary('cost', result['cost'])}; {_fmt_primary('wall', result['wall'])}"
    guards = result.get("guardrails") or {}
    if guards:
        bad = [n for n, g in guards.items() if g.get("state") == "breach"]
        unknown = sum(1 for g in guards.values() if g.get("state") == "unknown")
        tail += (f"; guardrails: BREACH {','.join(bad)}" if bad
                 else f"; guardrails ok ({unknown} unknown below their minimum n)")
    if result.get("action_taken"):
        tail += f" [{result['action_taken']}]"
    return head + tail


def _unit_suffix(knob: str) -> str:
    """"s" for the second-valued knobs — purely cosmetic, used only in the CLI's
    human-readable print lines."""
    return "s" if knob in ("proposer_timeout", "yield_poll_interval") else ""


# ── CLI ──────────────────────────────────────────────────────────────────────
def _cmd_summarize(args: argparse.Namespace) -> int:
    files = find_event_files(args.events)
    if not files:
        print("learn: no events.jsonl found under: " + ", ".join(args.events), file=sys.stderr)
        return 2
    events: List[dict] = []
    for f in files:
        events.extend(read_events(f))
    live = count_live_invocations(args.run_log, args.live_regex) if args.run_log else None
    summary = summarize(events, verification_dir=args.verification_dir, run_log_live_invocations=live,
                        verdicts_dir=args.verdicts_dir)
    summary["inputs"] = files
    text = json.dumps(summary, indent=2 if args.pretty else None, sort_keys=False)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        t = summary["totals"]
        print(f"learn: {t['runs']} run(s), {t['attempts']} attempt(s), {t['passes']} pass(es), "
              f"${t['cost_usd']:.2f}, kills={t['kills_by_reason']} → {args.out}")
    else:
        print(text)
    return 0


def observation_for_phase(events: List[dict], phase: int,
                           verification_dir: Optional[str] = None,
                           shape: Optional[str] = None) -> dict:
    """§5.4's per-phase **observation** document: exactly this phase's entry from
    `summarize`'s `phases` map (§5.3's metric set), wrapped with a schema tag and
    a generation timestamp. Pure other than that timestamp — the same `events`
    always yields the same `"observation"` content, which is what makes the
    `observe` CLI command idempotent (safe to call once per `phase_done`, and
    safe to call again by hand). `"observation"` is `None`, not an error, when
    `phase` has no telemetry yet in `events` — a hook firing on the very first
    pass of a brand-new phase is not a failure. With `shape`, only attempts
    recorded under that phase shape are observed."""
    summary = summarize(events, verification_dir=verification_dir,
                        phase_shapes={phase: shape} if shape else None)
    return {
        "schema": OBSERVATION_SCHEMA,
        "phase": phase,
        "shape": shape,
        "generated_at": _now_iso(),
        "observation": summary.get("phases", {}).get(str(phase)),
    }


def _phase_shapes(args: argparse.Namespace) -> Dict[int, str]:
    """{phase: shape} from --phase-shape (for --phase) and/or --specs (every phase,
    computed by specstride_spec, the one module that parses specs). Empty when
    neither is given: nothing is then restricted by shape."""
    shapes: Dict[int, str] = {}
    specs = getattr(args, "specs", None)
    if specs:
        import specstride_spec
        try:
            with open(specs, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            fmt = specstride_spec.detect_format(specs, text, getattr(args, "spec_format", None) or None)
            for ph in specstride_spec.get_phases(text, fmt):
                shapes[int(ph.n)] = specstride_spec.phase_shape_of(ph)
        except (OSError, ValueError) as e:
            print(f"learn: cannot read phase shapes from {specs}: {e}", file=sys.stderr)
    if getattr(args, "phase_shape", None) and getattr(args, "phase", None) is not None:
        shapes[int(args.phase)] = args.phase_shape
    return shapes


def _restrict(summary: dict, shapes: Dict[int, str]) -> dict:
    """Re-derive a loaded summary's `phases` restricted to `shapes`, from its own
    `attempts` (which carry each attempt's shape), keeping each phase's caps."""
    if not shapes:
        return summary
    caps: Dict[int, List[dict]] = {}
    for k, stats in (summary.get("phases") or {}).items():
        caps[_int(k)] = list(stats.get("caps") or [])
    out = dict(summary)
    out["phases"] = _phases(summary.get("attempts") or [], caps, shapes)
    return out


def _verdicts_dir(args: argparse.Namespace) -> Optional[str]:
    """--verdicts-dir, else <feature-dir>/verdicts when it exists (critic input size)."""
    explicit = getattr(args, "verdicts_dir", None)
    if explicit:
        return explicit
    feature_dir = getattr(args, "feature_dir", None)
    if feature_dir and os.path.isdir(os.path.join(feature_dir, "verdicts")):
        return os.path.join(feature_dir, "verdicts")
    return None


def _load_summary(args: argparse.Namespace) -> Optional[dict]:
    """advise/apply accept either --summary (a `summarize --out` document, so a
    caller can decouple measuring from advising) or --events (computed fresh);
    either way a phase's figures are restricted to its current shape when one is
    known (--phase-shape / --specs)."""
    shapes = _phase_shapes(args)
    if getattr(args, "summary", None):
        with open(args.summary, "r", encoding="utf-8") as fh:
            return _restrict(json.load(fh), shapes)
    if getattr(args, "events", None):
        files = find_event_files(args.events)
        if not files:
            print("learn: no events.jsonl found under: " + ", ".join(args.events), file=sys.stderr)
            return None
        events: List[dict] = []
        for f in files:
            events.extend(read_events(f))
        return summarize(events, phase_shapes=shapes or None, verdicts_dir=_verdicts_dir(args))
    print("learn: one of --events or --summary is required", file=sys.stderr)
    return None


def _cmd_advise(args: argparse.Namespace) -> int:
    summary = _load_summary(args)
    if summary is None:
        return 2
    applied_file, _ = _feature_paths(args.feature_dir, args.applied_file, None)
    shapes = _phase_shapes(args)
    try:
        rows = advise(summary, args.knob, args.phase, args.default, applied_file, shapes)
    except ValueError as e:
        print(f"learn: {e}", file=sys.stderr)
        return 2
    if not rows:
        print(f"learn: no telemetry for {'phase ' + str(args.phase) if args.phase is not None else 'any phase'}",
              file=sys.stderr)
        return 1
    for r in rows:
        unit = _unit_suffix(r["knob"])
        notice = unshaped_notice(applied_file, r["knob"], r["phase"], r.get("shape"))
        if notice:
            print(notice, file=sys.stderr)
        blocked = (quarantine_block(applied_file, summary, r["knob"], r["phase"], r.get("shape"),
                                    record_baseline(summary, r["phase"], r.get("shape"),
                                                    r.get("runs_seen") or [])["backend"], r["value"])
                   if applied_file and r["value"] is not None else None)
        if blocked is not None:
            print(f"learn: phase {r['phase']} {r['knob']}: suggestion {r['value']} is QUARANTINED — "
                  f"auto-reverted by {blocked['run_id']} ({','.join(blocked.get('guardrail') or [])}); "
                  f"{blocked['fresh_samples']}/{QUARANTINE_SAMPLES} new samples since; --apply refuses "
                  f"without --force")
        if r["value"] is None:
            print(f"learn: phase {r['phase']} {r['knob']}: {r['samples']} sample(s) — {r['reason']}; "
                  f"no suggestion (need {'>=3'})")
        elif r["knob"] == "proposer_timeout":
            print(f"learn: phase {r['phase']} {r['knob']}: {r['samples']} sample(s), "
                  f"work p50={r.get('work_sec_p50')}s p90={r.get('work_sec_p90')}s, "
                  f"current={r['current']}{unit} → suggest {r['value']}{unit} "
                  f"(bounds={r['bounds']}, step_cap={r['step_cap']}) "
                  f"[not applied — run `specstride learn --apply` to take effect]")
        else:   # yield_poll_interval
            print(f"learn: phase {r['phase']} {r['knob']}: {r['samples']} sample(s), "
                  f"job_duration p50={r.get('job_duration_p50')}s, "
                  f"current={r['current']}{unit} → suggest {r['value']}{unit} "
                  f"(bounds={r['bounds']}, step_cap={r['step_cap']}) "
                  f"[not applied — run `specstride learn --apply` to take effect]")
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(rows, indent=2) + "\n")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    if args.knob not in ADJUSTABLE_KNOBS:
        print(f"learn: '{args.knob}' is not in the adjustable-knob allowlist "
              f"({sorted(ADJUSTABLE_KNOBS)}) — refusing", file=sys.stderr)
        return 2
    if args.knob not in APPLY_ENGINES:
        print(f"learn: no apply engine yet for knob {args.knob!r}", file=sys.stderr)
        return 2
    summary = _load_summary(args)
    if summary is None:
        return 2
    applied_file, events_file = _feature_paths(args.feature_dir, args.applied_file, args.events_file)
    if not applied_file:
        print("learn: --applied-file or --feature-dir is required to apply", file=sys.stderr)
        return 2
    shape = _phase_shapes(args).get(args.phase)
    notice = unshaped_notice(applied_file, args.knob, args.phase, shape)
    if notice:
        print(notice, file=sys.stderr)
    try:
        entry = APPLY_ENGINES[args.knob](summary, args.phase, args.default, applied_file,
                                          events_file, args.run_id, shape, args.force)
    except QuarantinedError as e:
        print(f"learn: refusing to apply — quarantined: {e}", file=sys.stderr)
        return 4
    except ValueError as e:
        print(f"learn: refusing to apply — {e}", file=sys.stderr)
        return 3
    unit = _unit_suffix(entry["knob"])
    print(f"learn: applied {entry['knob']}[{entry['phase']}] {entry['previous']}{unit} -> {entry['value']}{unit} "
          f"(samples={entry['samples']}, run_id={entry['run_id']}) → {applied_file}")
    return 0


def _cmd_revert(args: argparse.Namespace) -> int:
    applied_file, events_file = _feature_paths(args.feature_dir, args.applied_file, args.events_file)
    if not applied_file:
        print("learn: --applied-file or --feature-dir is required to revert", file=sys.stderr)
        return 2
    try:
        entry = revert_run(args.run_id, applied_file, events_file=events_file)
    except ValueError as e:
        print(f"learn: {e}", file=sys.stderr)
        return 2
    unit = _unit_suffix(entry["knob"])
    print(f"learn: reverted {args.run_id} — {entry['knob']}[{entry['phase']}] back to {entry['value']}{unit}")
    return 0


def _cmd_off(args: argparse.Namespace) -> int:
    applied_file, events_file = _feature_paths(args.feature_dir, args.applied_file, args.events_file)
    if not applied_file:
        print("learn: --applied-file or --feature-dir is required", file=sys.stderr)
        return 2
    reverted = revert_all(applied_file, events_file=events_file)
    if reverted:
        for e in reverted:
            unit = _unit_suffix(e["knob"])
            print(f"learn: reverted {e['reverts_run_id']} — {e['knob']}[{e['phase']}] back to {e['value']}{unit}")
    else:
        print("learn: nothing was applied — already at defaults")
    print("learn: note — SPECSTRIDE_LEARNING=off (or unset) in the run's environment is what actually "
          "makes `resolve` ignore applied.json; this command only clears the recorded decisions.")
    return 0


def resolve_arm(knob: str, phase: int, applied_file: Optional[str], env: Optional[dict] = None,
                shape: Optional[str] = None) -> str:
    """`applied` while a decision is in effect for (knob, phase, shape) under
    SPECSTRIDE_LEARNING=apply — whatever its value, even one equal to the default —
    else `baseline`. The run records it so evaluate can tell the arms apart."""
    env = os.environ if env is None else env
    if env.get("SPECSTRIDE_LEARNING") != "apply" or knob not in ADJUSTABLE_KNOBS:
        return "baseline"
    latest = None
    for e in _decisions(applied_file, knob, phase, shape, env.get("SPECSTRIDE_LEARNING_THROUGH") or None):
        latest = e
    return "applied" if latest is not None and latest.get("action") in (None, "apply") else "baseline"


def _cmd_resolve(args: argparse.Namespace) -> int:
    applied_file, _ = _feature_paths(args.feature_dir, args.applied_file, None)
    if os.environ.get("SPECSTRIDE_LEARNING") == "apply":
        notice = unshaped_notice(applied_file, args.knob, args.phase, args.phase_shape)
        if notice:
            print(notice, file=sys.stderr)
    value = resolve_knob(args.knob, args.phase, args.default, applied_file, shape=args.phase_shape)
    print(f"{value}\t{resolve_arm(args.knob, args.phase, applied_file, shape=args.phase_shape)}")
    return 0


def _cmd_observe(args: argparse.Namespace) -> int:
    files = find_event_files(args.events)
    if not files:
        print("learn: no events.jsonl found under: " + ", ".join(args.events), file=sys.stderr)
        return 2
    events: List[dict] = []
    for f in files:
        events.extend(read_events(f))
    doc = observation_for_phase(events, args.phase, verification_dir=args.verification_dir,
                                shape=args.phase_shape)
    doc["inputs"] = files
    text = json.dumps(doc, indent=2 if args.pretty else None, sort_keys=False)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        obs = doc["observation"]
        if obs is None:
            print(f"learn: no telemetry for phase {args.phase} yet → {args.out}")
        else:
            print(f"learn: phase {args.phase} observation — {obs['attempts_total']} attempt(s), "
                  f"work p50={obs.get('work_sec_p50')}s p90={obs.get('work_sec_p90')}s → {args.out}")
    else:
        print(text)
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    applied_file, events_file = _feature_paths(args.feature_dir, args.applied_file, args.events_file)
    if not applied_file:
        print("learn: --applied-file or --feature-dir is required", file=sys.stderr)
        return 2
    decisions = active_decisions(applied_file, args.phase)
    if args.report:
        # read-only: the last recorded evaluation of every active decision
        if not decisions:
            print("learn: no active decisions")
        for d in decisions:
            last = _last_evaluation(applied_file, d["run_id"])
            if last is None:
                unit = _unit_suffix(d["knob"])
                print(f"learn: phase {d['phase']} {d['knob']} {d.get('previous')}{unit}→{d.get('value')}{unit} "
                      f"[{d['run_id']}]: not evaluated yet")
            else:
                print(format_evaluation(last))
        return 0
    if not args.events:
        print("learn: --events is required (or --report)", file=sys.stderr)
        return 2
    if not decisions:
        return 0
    files = find_event_files(args.events)
    events: List[dict] = []
    for f in files:
        events.extend(read_events(f))
    summary = summarize(events, verdicts_dir=_verdicts_dir(args))
    shapes = _phase_shapes(args)
    for d in decisions:
        ph = _int(d["phase"])
        current = shapes.get(ph, _entry_shape(d))
        result = evaluate_decision(summary, d, current)
        print(format_evaluation(result))
        if not args.dry_run:
            reverted = act_on_evaluation(result, applied_file, events_file)
            if reverted is not None and reverted.get("auto"):
                print(f"learn: auto-reverted {d['run_id']} — guardrail {','.join(reverted['guardrail'])}; "
                      f"{d['knob']}[{ph}] back to {reverted['value']}{_unit_suffix(d['knob'])}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="learn.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summarize", help="compute the §5.3 metric set from events.jsonl streams")
    s.add_argument("--events", action="append", required=True,
                   help="events.jsonl, a run dir, or a dir of run dirs (repeatable)")
    s.add_argument("--run-log", help="run.log for the proposer-live-invocation cross-check")
    s.add_argument("--live-regex", default=r"runners/live",
                   help="what a proposer 'live' invocation looks like in run.log (default: runners/live)")
    s.add_argument("--verification-dir", help="a run's verification/ dir, for gate durations")
    s.add_argument("--verdicts-dir", help="a feature's verdicts/ dir, for the critic prompt size per attempt")
    s.add_argument("--out", help="write JSON here (default: stdout)")
    s.add_argument("--pretty", action="store_true")
    s.set_defaults(func=_cmd_summarize)

    def _shape_args(p):
        p.add_argument("--phase-shape", help="the shape digest of --phase (`specstride_spec.py shape N`): "
                                              "samples and decisions of another shape are left out")
        p.add_argument("--specs", help="the spec, to key every phase on its current shape")
        p.add_argument("--spec-format", help="native|speckit-tasks|openspec-change (else auto-detect)")
        p.add_argument("--verdicts-dir", help="the feature's verdicts/ dir (default <feature-dir>/verdicts)")

    def _events_or_summary(p):
        p.add_argument("--events", action="append", help="events.jsonl, a run dir, or a dir of run dirs (repeatable)")
        p.add_argument("--summary", help="a `summarize --out` JSON document, instead of --events")

    a = sub.add_parser("advise", help="print suggested knob value(s) — writes nothing (§5.5 invariant 5)")
    _events_or_summary(a)
    a.add_argument("--knob", default="proposer_timeout", choices=sorted(ADJUSTABLE_KNOBS))
    a.add_argument("--phase", type=int, help="one phase only (default: every phase seen)")
    a.add_argument("--default", type=int, required=True, help="the global default this knob falls back to")
    a.add_argument("--feature-dir", help="<feature-dir>/learning/applied.json is read for 'current', if present")
    a.add_argument("--applied-file", help="override the applied.json path (instead of deriving from --feature-dir)")
    a.add_argument("--out", help="also write the advice rows as JSON here")
    _shape_args(a)
    a.set_defaults(func=_cmd_advise)

    ap_ = sub.add_parser("apply", help="apply the current suggestion for one phase — writes applied.json + a "
                                        "knob_adjusted event (§5.4/§5.5 invariant 3)")
    _events_or_summary(ap_)
    ap_.add_argument("--knob", default="proposer_timeout", choices=sorted(ADJUSTABLE_KNOBS))
    ap_.add_argument("--phase", type=int, required=True)
    ap_.add_argument("--default", type=int, required=True)
    ap_.add_argument("--feature-dir", help="derives --applied-file/--events-file under it (§5.4 layout)")
    ap_.add_argument("--applied-file", help="<feature-dir>/learning/applied.json by default")
    ap_.add_argument("--events-file", help="<feature-dir>/events.jsonl by default; the knob_adjusted event goes here")
    ap_.add_argument("--run-id", help="override the generated run id (mainly for tests)")
    ap_.add_argument("--force", action="store_true",
                     help="apply a value an auto-revert quarantined anyway (recorded as force: true)")
    _shape_args(ap_)
    ap_.set_defaults(func=_cmd_apply)

    r = sub.add_parser("revert", help="undo one prior apply by its run id — restores the prior value exactly")
    r.add_argument("run_id")
    r.add_argument("--feature-dir")
    r.add_argument("--applied-file")
    r.add_argument("--events-file")
    r.set_defaults(func=_cmd_revert)

    o = sub.add_parser("off", help="revert every currently-applied knob at once (`specstride learn --off`)")
    o.add_argument("--feature-dir")
    o.add_argument("--applied-file")
    o.add_argument("--events-file")
    o.set_defaults(func=_cmd_off)

    rs = sub.add_parser("resolve", help="the shell-callable integration point: print `<value>\\t<arm>` — the "
                                         "effective value of --knob/--phase (or --default if SPECSTRIDE_LEARNING "
                                         "!= apply) and `applied`|`baseline`")
    rs.add_argument("--knob", required=True, choices=sorted(ADJUSTABLE_KNOBS))
    rs.add_argument("--phase", type=int, required=True)
    rs.add_argument("--default", type=int, required=True)
    rs.add_argument("--feature-dir")
    rs.add_argument("--applied-file")
    rs.add_argument("--phase-shape", help="the phase's current shape digest (`specstride_spec.py shape N`); "
                                           "a decision recorded under another shape, or none, is not applied")
    rs.set_defaults(func=_cmd_resolve)

    ob = sub.add_parser("observe", help="write the §5.4 per-phase OBSERVATION document (not a decision — see "
                                         "`apply`); an orchestrator hook calls this once at phase_done")
    ob.add_argument("--events", action="append", required=True,
                     help="events.jsonl, a run dir, or a dir of run dirs (repeatable) — the orchestrator's "
                          "phase_done hook passes \"$FEATURE_DIR/runs\" for the phase's full cross-run history")
    ob.add_argument("--phase", type=int, required=True)
    ob.add_argument("--verification-dir", help="a run's verification/ dir, for gate durations")
    ob.add_argument("--out", help="write JSON here, e.g. <feature-dir>/learning/phase-<N>.json (default: stdout)")
    ob.add_argument("--pretty", action="store_true")
    ob.add_argument("--phase-shape", help="observe only attempts recorded under this phase shape")
    ob.set_defaults(func=_cmd_observe)

    ev = sub.add_parser("evaluate", help="label every active decision helped/neutral/regressed/insufficient "
                                          "against its recorded baseline; appends an `evaluate` entry and a "
                                          "knob_evaluated event when the result changed")
    ev.add_argument("--events", action="append",
                    help="events.jsonl, a run dir, or a dir of run dirs (repeatable) — the phase_done hook "
                         "passes \"$FEATURE_DIR/runs\", every run of the feature")
    ev.add_argument("--phase", type=int, help="only decisions for this phase (default: every active decision)")
    ev.add_argument("--feature-dir")
    ev.add_argument("--applied-file")
    ev.add_argument("--events-file", help="where knob_evaluated goes (default <feature-dir>/events.jsonl)")
    ev.add_argument("--dry-run", action="store_true", help="print the evaluation; write nothing")
    ev.add_argument("--report", action="store_true",
                    help="read-only: print the last recorded evaluation of every active decision")
    _shape_args(ev)
    ev.set_defaults(func=_cmd_evaluate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
