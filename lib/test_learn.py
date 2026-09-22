#!/usr/bin/env python3
"""Tests for lib/learn.py summarize (design §6 step 0) — pure-function assertions
over the synthetic fixture lib/fixtures/learn/events.jsonl.

Run:  python3 -m pytest lib/test_learn.py -q
"""
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import learn  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "learn", "events.jsonl")


def _summary(**kw):
    return learn.summarize(learn.read_events(FIXTURE), **kw)


def _attempt(s, run, phase, attempt):
    for a in s["attempts"]:
        if (a["run"], a["phase"], a["attempt"]) == (run, phase, attempt):
            return a
    raise AssertionError(f"no attempt {(run, phase, attempt)}")


# ── loading ──────────────────────────────────────────────────────────────────
def test_reads_jsonl_and_skips_a_half_written_trailing_line():
    events = learn.read_events(FIXTURE)
    assert events[0]["event"] == "run_start"
    assert all("event" in e for e in events)
    # the fixture ends with a truncated line; it is skipped, not raised
    assert events[-1]["event"] == "phase_done"


def test_find_event_files_accepts_file_run_dir_and_dir_of_runs(tmp_path):
    runs = tmp_path / "runs"
    for rid in ("r1", "r2"):
        d = runs / rid
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text('{"event":"run_start","run_id":"%s","ts":"1"}\n' % rid)
    (runs / "notarun").mkdir()
    assert learn.find_event_files([str(runs / "r1" / "events.jsonl")]) == [str(runs / "r1" / "events.jsonl")]
    assert learn.find_event_files([str(runs / "r1")]) == [str(runs / "r1" / "events.jsonl")]
    assert learn.find_event_files([str(runs)]) == [str(runs / "r1" / "events.jsonl"), str(runs / "r2" / "events.jsonl")]
    # duplicates collapse
    assert len(learn.find_event_files([str(runs), str(runs / "r1")])) == 2


# ── classifiers ──────────────────────────────────────────────────────────────
def test_wait_classifier_matches_the_telemetry_report_idioms():
    for tgt in ("sleep 560; tail -4 /tmp/x.log", "tail -f run.log", "while pgrep -f x; do :; done",
                "until grep -q ok f; do sleep 2; done", "pgrep -af specstride", "ps aux | grep pytest",
                "watch -n 5 ls", "cd /x && sleep 30"):
        assert learn.classify_bash_target(tgt)[0], tgt
    for tgt in ("tail -4 /tmp/x.log", "pytest -q", "ls -la", "grep -n sleep lib/learn.py", "echo asleep"):
        assert not learn.classify_bash_target(tgt)[0], tgt


def test_declared_sleep_seconds_are_summed():
    assert learn.classify_bash_target("sleep 560; tail -4 x; sleep 40")[1] == 600.0
    assert learn.classify_bash_target("tail -f x")[1] == 0.0


def test_repeat_detail_parses_both_spellings():
    assert learn.parse_repeat_detail("re-ran 12x: tail -4") == (12, "tail -4")
    assert learn.parse_repeat_detail("repeated 13x: Bash cd /x && ls") == (13, "Bash cd /x && ls")
    assert learn.parse_repeat_detail("") == (None, None)
    assert learn.parse_repeat_detail("something else") == (None, None)


def test_percentile_is_linear_interpolation():
    assert learn.percentile([], 0.5) is None
    assert learn.percentile([10.0], 0.9) == 10.0
    assert learn.percentile([10.0, 20.0, 30.0, 40.0], 0.5) == 25.0
    assert learn.percentile([10.0, 20.0, 30.0, 40.0], 0.9) == 37.0


# ── keying: (run, phase, attempt) ────────────────────────────────────────────
def test_attempts_are_keyed_on_run_phase_attempt():
    s = _summary()
    keys = [(a["run"], a["phase"], a["attempt"]) for a in s["attempts"]]
    assert keys == [("run-A", 1, 1), ("run-A", 2, 1), ("run-A", 2, 2), ("run-B", 2, 1)]
    # the two "phase 2 attempt 1"s are different rows, not merged
    assert _attempt(s, "run-A", 2, 1)["verdict"] == "REJECTED"
    assert _attempt(s, "run-B", 2, 1)["verdict"] == "APPROVED"


def test_attempt_number_reset_is_flagged_per_phase():
    s = _summary()
    assert s["phases"]["2"]["attempt_number_reset"] is True
    assert s["phases"]["1"]["attempt_number_reset"] is False
    assert s["phases"]["2"]["runs_seen"] == ["run-A", "run-B"]


def test_critic_events_without_run_id_attach_to_the_streams_run():
    s = _summary()
    a = _attempt(s, "run-A", 1, 1)
    assert a["verdict"] == "APPROVED"
    assert a["critic_sec"] == 60.0
    assert a["grounding_gap_paths"] == 2


# ── the outcome taxonomy ─────────────────────────────────────────────────────
def test_three_way_outcome_taxonomy_per_pass():
    s = _summary()
    a = _attempt(s, "run-A", 2, 1)
    by_iter = {p["iter"]: p for p in a["passes_detail"]}
    assert by_iter[1]["outcome"] == "budget_kill" and by_iter[1]["kill_class"] == "budget"
    assert by_iter[2]["outcome"] == "worker_error" and by_iter[2]["kill_class"] == "futility"
    assert by_iter[3]["outcome"] == "clean_no_progress"
    assert by_iter[4]["outcome"] == "productive"
    assert a["outcomes"] == {"productive": 1, "clean_no_progress": 1, "worker_error": 1, "budget_kill": 1}
    assert a["kills_by_reason"] == {"hard_cap": 1, "repeat_stall": 1}


def test_a_killed_pass_is_unbilled_and_its_elapsed_comes_from_the_kill():
    s = _summary()
    a = _attempt(s, "run-A", 2, 1)
    p1 = a["passes_detail"][0]
    assert p1["billed"] is False and p1["cost_usd"] is None
    assert p1["elapsed_sec"] == 5400.0
    assert a["unbilled_passes"] == 2 and a["billed_passes"] == 2
    assert s["totals"]["unbilled_passes"] == 2


def test_dominant_repeat_share_from_kill_detail():
    s = _summary()
    p2 = _attempt(s, "run-A", 2, 1)["passes_detail"][1]
    assert p2["dominant_repeat"] == "tail -4 /tmp/x.log"
    assert p2["dominant_repeat_n"] == 4
    assert p2["dominant_repeat_share"] == 1.0   # 4 of 4 tool calls


def test_an_open_pass_is_neither_billed_nor_unbilled_and_work_floors_at_zero():
    events = learn.read_events(FIXTURE)
    # cut the stream right after run-B's first pass starts: that pass is open
    cut = next(i for i, e in enumerate(events) if e["event"] == "iter_start" and e.get("run_id") == "run-B")
    s = learn.summarize(events[:cut + 1] + [
        # a declared sleep longer than the pass, then a terminal result of 10 s
        {"event": "agent_tool", "run_id": "run-B", "tool": "Bash", "target": "sleep 999", "ts": "1", "_src": FIXTURE},
    ])
    a = _attempt(s, "run-B", 2, 1)
    p = a["passes_detail"][0]
    assert p["outcome"] == "open" and p["billed"] is None
    assert a["billed_passes"] == 0 and a["unbilled_passes"] == 0
    assert s["totals"]["outcomes"]["open"] == 1
    # now close it with a 10 s success: work estimate floors at 0, not −989
    s2 = learn.summarize(events[:cut + 1] + [
        {"event": "agent_tool", "run_id": "run-B", "tool": "Bash", "target": "sleep 999", "ts": "1", "_src": FIXTURE},
        {"event": "agent_result", "run_id": "run-B", "is_error": False, "subtype": "success",
         "cost_usd": 1.0, "duration_ms": 10000, "iter": 1, "ts": "2", "_src": FIXTURE},
    ])
    assert _attempt(s2, "run-B", 2, 1)["work_sec_estimate"] == 0.0


def test_verification_failed_attempt_has_no_verdict():
    s = _summary()
    a = _attempt(s, "run-A", 2, 2)
    assert a["verdict"] is None
    assert a["verification"] == "failed" and a["verification_rc"] == 3
    assert a["wall_sec"] is not None


# ── wait vs work ─────────────────────────────────────────────────────────────
def test_wait_share_and_sleep_seconds_per_attempt():
    s = _summary()
    a1 = _attempt(s, "run-A", 1, 1)
    assert (a1["tool_calls"], a1["wait_calls"], a1["work_calls"]) == (4, 1, 3)
    assert a1["wait_call_share"] == 0.25
    assert a1["sleep_sec_declared"] == 30.0
    assert a1["work_sec_estimate"] == 70.0           # 100 s pass − 30 s declared sleep
    a2 = _attempt(s, "run-A", 2, 1)
    assert (a2["tool_calls"], a2["wait_calls"]) == (12, 3)
    assert a2["sleep_sec_declared"] == 15.0


def test_phase_work_sec_percentiles_exclude_futility_killed_attempts():
    s = _summary()
    # phase 2: run-A/att1 carries a repeat_stall kill → excluded; att2 (80 s) and run-B (90 s) remain
    assert s["phases"]["2"]["work_sec_p50"] == 85.0
    assert s["phases"]["2"]["work_sec_p90"] == 89.0
    assert s["phases"]["1"]["work_sec_p50"] == 70.0


# ── cost ─────────────────────────────────────────────────────────────────────
def test_cost_and_token_sums_per_attempt_and_run():
    s = _summary()
    assert _attempt(s, "run-A", 1, 1)["cost_usd"] == 2.5
    assert _attempt(s, "run-A", 2, 1)["cost_usd"] == 5.0
    assert _attempt(s, "run-A", 1, 1)["cache_creation_tokens"] == 300
    assert s["runs"]["run-A"]["cost_usd"] == 10.5
    assert s["runs"]["run-B"]["cost_usd"] == 6.0
    assert s["totals"]["cost_usd"] == 16.5


def test_cost_per_approved_phase_and_non_approved_share():
    s = _summary()
    ra = s["runs"]["run-A"]
    assert ra["approved_phases"] == [1]
    assert ra["cost_per_approved_phase"] == 10.5
    assert ra["non_approved_cost_usd"] == 8.0            # 5.0 REJECTED + 3.0 verification-failed
    assert ra["non_approved_cost_share"] == round(8.0 / 10.5, 3)
    assert s["totals"]["approved_phases"] == [1, 2]
    assert s["totals"]["cost_per_approved_phase"] == 8.25
    assert s["totals"]["non_approved_cost_share"] == round(8.0 / 16.5, 3)


def test_attempts_to_approval_counts_across_runs():
    s = _summary()
    assert s["phases"]["1"]["attempts_to_approval"] == 1
    assert s["phases"]["2"]["attempts_to_approval"] == 3
    assert s["phases"]["2"]["approved_in_run"] == "run-B"


def test_run_stop_and_resume_are_recorded():
    s = _summary()
    assert s["runs"]["run-A"]["stop_reason"] == "stop_flag"
    assert s["runs"]["run-A"]["stop_phase"] == 2
    assert s["runs"]["run-A"]["resume_from"] == 1
    assert s["runs"]["run-B"]["resume_from"] == 2
    assert s["runs"]["run-B"]["stop_reason"] is None


# ── optional inputs ──────────────────────────────────────────────────────────
def test_verification_dir_attaches_gate_duration_and_live_share(tmp_path):
    vdir = tmp_path / "verification"
    vdir.mkdir()
    (vdir / "phase-1-attempt-1.json").write_text(json.dumps({"commands": [
        {"executable": "/usr/bin/python3", "args": ["-m", "pytest", "conformance/runners/live"], "durationMs": 3000},
        {"executable": "/usr/bin/make", "args": ["lint"], "durationMs": 1000},
    ]}))
    (vdir / "verification-plan.json").write_text("{}")
    s = _summary(verification_dir=str(vdir))
    a = _attempt(s, "run-A", 1, 1)
    assert a["gate_duration_ms"] == 4000
    assert a["gate_live_share"] == 0.75
    assert _attempt(s, "run-A", 2, 1)["gate_duration_ms"] is None


def test_run_log_live_invocations_count_only_tool_lines(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("\n".join([
        "----- proposer pass 1/20 -----",
        "  → Bash SOV_LIVE=1 pytest conformance/runners/live/test_a.py",
        "  → Bash pytest conformance/runners -q",
        "  → Bash timeout 900 ./.venv/bin/pytest conformance/runners/live -q",
        "verification: pytest conformance/runners/live (gate)",   # not a tool line
    ]) + "\n")
    assert learn.count_live_invocations(str(log), r"runners/live") == 2
    s = _summary(run_log_live_invocations=2)
    assert s["totals"]["proposer_live_invocations"] == 2
    assert "proposer_live_invocations" not in _summary()["totals"]


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_cli_writes_json_with_schema_and_inputs(tmp_path):
    out = tmp_path / "summary.json"
    r = subprocess.run([sys.executable, os.path.join(HERE, "learn.py"), "summarize",
                        "--events", FIXTURE, "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "2 run(s), 4 attempt(s), 7 pass(es)" in r.stdout
    doc = json.loads(out.read_text())
    assert doc["schema"] == learn.SCHEMA
    assert doc["inputs"] == [FIXTURE]
    assert set(doc) == {"schema", "inputs", "runs", "attempts", "phases", "totals"}


def test_cli_fails_cleanly_with_no_events(tmp_path):
    r = subprocess.run([sys.executable, os.path.join(HERE, "learn.py"), "summarize",
                        "--events", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 2
    assert "no events.jsonl" in r.stderr


def test_summarize_is_pure_and_repeatable():
    events = learn.read_events(FIXTURE)
    a = learn.summarize(events)
    b = learn.summarize(events)
    assert a == b
    assert json.dumps(a)   # serialisable


# ── step 5: the learning loop (design §5 / §6 step 5) ───────────────────────
#
# A small synthetic-events builder, independent of the step-0 FIXTURE above, so
# these tests can control exactly how many non-futility-killed samples a phase
# has without perturbing the step-0 assertions that already pin the fixture's
# numbers.
def _events_for_phase(phase, work_secs, futile_count=0, run_id="run-X", shape=None):
    events, ts = [], [1000.0]

    def emit(ev):
        ev = dict(ev)
        ev.setdefault("ts", str(ts[0]))
        ev["_src"] = "synthetic"
        events.append(ev)
        ts[0] += 1

    emit({"event": "run_start", "run_id": run_id, "feature": "f"})
    emit(dict({"event": "phase_start", "run_id": run_id, "phase": phase, "title": "T"},
              **({"shape": shape} if shape else {})))
    attempt = 0
    for work in work_secs:
        attempt += 1
        emit({"event": "proposer_start", "run_id": run_id, "phase": phase, "attempt": attempt})
        emit({"event": "iter_start", "run_id": run_id, "iter": 1})
        emit({"event": "agent_result", "run_id": run_id, "is_error": False, "subtype": "success",
              "cost_usd": 1.0, "duration_ms": int(work * 1000), "iter": 1})
        emit({"event": "evidence_written", "run_id": run_id, "file": "GATE-EVIDENCE.md", "iters": 1})
        emit({"event": "iter_done", "run_id": run_id, "iter": 1, "evidence": "present"})
        emit({"event": "verification_passed", "run_id": run_id, "phase": phase, "attempt": attempt, "evidence": "e"})
        emit({"event": "critic_start", "phase": phase, "attempt": attempt})
        emit({"event": "verdict", "phase": phase, "attempt": attempt, "result": "APPROVED"})
        emit({"event": "attempt_archived", "run_id": run_id, "phase": phase, "attempt": attempt})
    for _ in range(futile_count):
        attempt += 1
        emit({"event": "proposer_start", "run_id": run_id, "phase": phase, "attempt": attempt})
        emit({"event": "iter_start", "run_id": run_id, "iter": 1})
        emit({"event": "pass_killed", "run_id": run_id, "iter": 1, "reason": "repeat_stall", "elapsed": 9999})
        emit({"event": "agent_result", "run_id": run_id, "is_error": True, "subtype": "missing_terminal", "iter": 1})
        emit({"event": "attempt_archived", "run_id": run_id, "phase": phase, "attempt": attempt})
    emit({"event": "phase_done", "run_id": run_id, "phase": phase, "attempt": attempt})
    return events


def _phase_stats(work_secs, futile_count=0, phase=9):
    s = learn.summarize(_events_for_phase(phase, work_secs, futile_count))
    return s["phases"][str(phase)]


# A second synthetic-events builder for `yield_poll_interval`'s evidence: each
# entry is (job_duration_sec_or_None, kill_reason_or_None). ``None`` duration
# means the pass never yielded (a plain pass, like `_events_for_phase`); a
# duration paired with a kill_reason means the pass yielded, resumed, and was
# THEN futility-killed anyway — the case that actually exercises the exclusion
# filter in `_phases` (a pass with real job-duration data that must still drop
# out of the evidence floor because of its kill class).
def _events_for_phase_yield(phase, specs, run_id="run-Y"):
    events, ts = [], [1000.0]

    def emit(ev):
        ev = dict(ev)
        ev.setdefault("ts", str(ts[0]))
        ev["_src"] = "synthetic"
        events.append(ev)
        ts[0] += 1

    emit({"event": "run_start", "run_id": run_id, "feature": "f"})
    emit({"event": "phase_start", "run_id": run_id, "phase": phase, "title": "T"})
    attempt = 0
    for job_duration, kill_reason in specs:
        attempt += 1
        emit({"event": "proposer_start", "run_id": run_id, "phase": phase, "attempt": attempt})
        emit({"event": "iter_start", "run_id": run_id, "iter": 1})
        if job_duration is not None:
            emit({"event": "yield_job_start", "run_id": run_id, "iter": 1, "mode": "launch"})
            emit({"event": "yield_resume", "run_id": run_id, "iter": 1,
                  "waited_sec": job_duration, "job_duration_sec": job_duration})
        if kill_reason:
            emit({"event": "pass_killed", "run_id": run_id, "iter": 1, "reason": kill_reason, "elapsed": 999})
            emit({"event": "agent_result", "run_id": run_id, "is_error": True, "subtype": "killed", "iter": 1})
        else:
            emit({"event": "agent_result", "run_id": run_id, "is_error": False, "subtype": "success",
                  "cost_usd": 1.0, "duration_ms": 60000, "iter": 1})
            emit({"event": "evidence_written", "run_id": run_id, "file": "GATE-EVIDENCE.md", "iters": 1})
            emit({"event": "iter_done", "run_id": run_id, "iter": 1, "evidence": "present"})
        emit({"event": "attempt_archived", "run_id": run_id, "phase": phase, "attempt": attempt})
    emit({"event": "phase_done", "run_id": run_id, "phase": phase, "attempt": attempt})
    return events


def _phase_stats_yield(specs, phase=8):
    s = learn.summarize(_events_for_phase_yield(phase, specs))
    return s["phases"][str(phase)]


# A third synthetic-events builder for the §2.2 wait/work evidence: each
# entry is (wait_calls, tool_calls, kill_reason_or_None) for one pass. Bash
# targets are either a WAIT_RE-matching "sleep 5" or a plain "pytest -q", in the
# exact counts needed so the pass's wait_call_share is `wait_calls/tool_calls`.
def _events_for_phase_wait(phase, pass_specs, run_id="run-W"):
    events, ts = [], [1000.0]

    def emit(ev):
        ev = dict(ev)
        ev.setdefault("ts", str(ts[0]))
        ev["_src"] = "synthetic"
        events.append(ev)
        ts[0] += 1

    emit({"event": "run_start", "run_id": run_id, "feature": "f"})
    emit({"event": "phase_start", "run_id": run_id, "phase": phase, "title": "T"})
    attempt = 0
    for wait_calls, tool_calls, kill_reason in pass_specs:
        attempt += 1
        emit({"event": "proposer_start", "run_id": run_id, "phase": phase, "attempt": attempt})
        emit({"event": "iter_start", "run_id": run_id, "iter": 1})
        for _ in range(wait_calls):
            emit({"event": "agent_tool", "run_id": run_id, "tool": "Bash", "target": "sleep 5"})
        for _ in range(tool_calls - wait_calls):
            emit({"event": "agent_tool", "run_id": run_id, "tool": "Bash", "target": "pytest -q"})
        if kill_reason:
            emit({"event": "pass_killed", "run_id": run_id, "iter": 1, "reason": kill_reason, "elapsed": 999})
            emit({"event": "agent_result", "run_id": run_id, "is_error": True, "subtype": "killed", "iter": 1})
        else:
            emit({"event": "agent_result", "run_id": run_id, "is_error": False, "subtype": "success",
                  "cost_usd": 1.0, "duration_ms": 60000, "iter": 1})
            emit({"event": "evidence_written", "run_id": run_id, "file": "GATE-EVIDENCE.md", "iters": 1})
            emit({"event": "iter_done", "run_id": run_id, "iter": 1, "evidence": "present"})
        emit({"event": "attempt_archived", "run_id": run_id, "phase": phase, "attempt": attempt})
    emit({"event": "phase_done", "run_id": run_id, "phase": phase, "attempt": attempt})
    return events


def _phase_stats_wait(pass_specs, phase=7):
    s = learn.summarize(_events_for_phase_wait(phase, pass_specs))
    return s["phases"][str(phase)]


# -- the locked allowlist (§5.5) ----------------------------------------------
def test_knob_allowlist_is_locked_so_a_critic_facing_knob_can_never_be_added():
    # This is the test the design doc's §6 step 5 refers to: it exists so that
    # adding a critic-facing or breaker-relaxing name to ADJUSTABLE_KNOBS requires
    # deliberately editing a test whose name says exactly why that must not happen.
    assert learn.ADJUSTABLE_KNOBS == frozenset({
        "proposer_timeout", "yield_poll_interval",
    })
    never_allowed = {
        # critic independence (§5.5) — grounding caps, critic backend/timeout, --max-rejects
        "grounding_max_lines", "grounding_max_files", "critic_backend", "critic_timeout",
        "max_rejects", "critic_model",
        # the operator's contract — never tunable by the loop
        "verification_commands", "verification_documents", "phase_timeouts_declared",
        # breakers must not relax themselves (§5.5)
        "SPECSTRIDE_PROPOSER_MAX_ERRORS", "SPECSTRIDE_PROPOSER_MAX_NOPROGRESS", "SPECSTRIDE_PROPOSER_MAX_CAPS",
        "repeat_limit", "repeat_ignore",
        # removed, not wired: the yield contract is already in every prompt, and the
        # only change left was to a prompt the critic later judges
        "inject_yield_hint",
    }
    assert never_allowed.isdisjoint(learn.ADJUSTABLE_KNOBS)


# The allowlist above locks WHAT the loop may tune; this locks WHERE learn.py can
# act on a run. Every invocation of it from tracked shell or Python code is
# listed, with its subcommand and how many call sites use it. The run path may
# resolve two knobs for the proposer launch and observe/evaluate a closed phase; the CLI
# dispatcher may advise/apply/revert/off. Nothing in the gate (critic.py,
# verification_plan.py) may call it at all. Widening this set is a design
# decision about who may act on learned state, and must be made here, visibly.
_LEARN_INVOCATION = re.compile(r"python3\s+\S*learn\.py[\"']?\s+([a-z]+)(\s+--help)?")


def _learn_invocations(paths):
    found = Counter()
    for rel in paths:
        text = open(os.path.join(REPO, rel), encoding="utf-8", errors="replace").read()
        for line in text.splitlines():
            for m in _LEARN_INVOCATION.finditer(line):
                found[(rel, m.group(1) + (" --help" if m.group(2) else ""))] += 1
    return found


def _tracked_code():
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True,
                             check=True).stdout.split()
    out = []
    for rel in tracked:
        path = os.path.join(REPO, rel)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        if rel.endswith((".sh", ".py")):
            out.append(rel)
            continue
        with open(path, "rb") as fh:
            head = fh.readline()
        if head.startswith(b"#!") and (b"sh" in head or b"python" in head):
            out.append(rel)
    return out


def test_learn_py_is_invoked_only_from_the_proposer_launch_path_never_the_gate():
    code = [rel for rel in _tracked_code() if rel not in ("lib/learn.py", "lib/test_learn.py")]
    assert "orchestrator.sh" in code and "specstride" in code and "lib/critic.py" in code
    assert _learn_invocations(code) == Counter({
        # the run: two knobs resolved per phase before the proposer launches ...
        ("orchestrator.sh", "resolve"): 2,
        # ... and, once a phase has closed, one observation and one evaluation of it
        ("orchestrator.sh", "observe --help"): 1,
        ("orchestrator.sh", "observe"): 1,
        ("orchestrator.sh", "evaluate --help"): 1,
        ("orchestrator.sh", "evaluate"): 2,              # the phase_done hook, and --report at exit
        ("specstride", "evaluate"): 1,                   # --report after --show's suggestions
        # the operator's CLI
        ("specstride", "advise"): 1,
        ("specstride", "apply"): 1,
        ("specstride", "revert"): 1,
        ("specstride", "off"): 1,
    })
    for gate in ("lib/critic.py", "lib/verification_plan.py"):
        assert _learn_invocations([gate]) == Counter(), gate
        text = open(os.path.join(REPO, gate), encoding="utf-8").read()
        assert not re.search(r"^\s*(import learn\b|from learn import)", text, re.M), gate


# -- suggestion: bounds, the evidence floor, futility exclusion ---------------
def test_suggest_proposer_timeout_needs_a_three_sample_floor():
    stats = _phase_stats([80.0, 90.0], futile_count=1)   # only 2 usable samples
    adv = learn.suggest_proposer_timeout(stats, default=5400)
    assert adv["samples"] == 2
    assert adv["value"] is None
    assert "3" in adv["reason"]


def test_suggest_proposer_timeout_excludes_futility_killed_samples_from_the_floor():
    # 3 clean samples + 2 futility kills: the floor is met on 3, not 5 — proving
    # the futility-killed passes never count as evidence.
    stats = _phase_stats([80.0, 90.0, 85.0], futile_count=2)
    assert stats["work_sec_samples"] == 3
    adv = learn.suggest_proposer_timeout(stats, default=5400)
    assert adv["samples"] == 3
    assert adv["value"] is not None


def test_suggest_proposer_timeout_clamps_to_the_step_cap_when_work_is_far_below_default():
    # work is tiny relative to the 5400 s default: the raw target would collapse
    # to near zero, but the ±50%-per-step cap (not the 900 s hard floor) is what
    # actually binds here, since 0.5 × 5400 = 2700 > 900.
    stats = _phase_stats([5.0, 6.0, 5.5])
    adv = learn.suggest_proposer_timeout(stats, default=5400)
    assert adv["bounds"] == [900, 10800]
    assert adv["step_cap"] == [2700, 8100]
    assert adv["value"] == 2700


def test_suggest_proposer_timeout_clamps_to_the_hard_upper_bound():
    # `current`=2000 makes the step cap's ceiling (1.5×2000=3000) looser than the
    # hard bound (2×default=1800) — so it is the hard 2× bound that must bind,
    # not the step cap, even though huge measured work would blow through both.
    stats = _phase_stats([100000.0, 110000.0, 105000.0])
    adv = learn.suggest_proposer_timeout(stats, default=900, current=2000)
    assert adv["bounds"] == [900, 1800]
    assert adv["step_cap"] == [1000, 3000]
    assert adv["value"] == 1800


def test_suggest_proposer_timeout_clamps_to_the_hard_lower_bound_of_900():
    # `current`=1000 makes the step cap's floor (0.5×1000=500) looser than the
    # hard 900 s floor — so tiny measured work must clamp to 900, not 500.
    stats = _phase_stats([5.0, 6.0, 5.5])
    adv = learn.suggest_proposer_timeout(stats, default=5400, current=1000)
    assert adv["bounds"] == [900, 10800]
    assert adv["step_cap"] == [500, 1500]
    assert adv["value"] == 900


def test_suggest_proposer_timeout_respects_a_previously_applied_current_value():
    stats = _phase_stats([100000.0, 110000.0, 105000.0])
    adv = learn.suggest_proposer_timeout(stats, default=5400, current=1200)
    # step cap is now relative to `current` (1200), not `default` (5400)
    assert adv["step_cap"] == [600, 1800]
    assert adv["bounds"] == [900, 10800]
    assert adv["value"] == 1800   # min(hard_hi=10800, step_hi=1800) binds here


# -- suggestion: yield_poll_interval (bounds, floor, futility exclusion) -----
def test_suggest_yield_poll_interval_needs_a_three_sample_floor():
    stats = _phase_stats_yield([(100.0, None), (120.0, None)])
    adv = learn.suggest_yield_poll_interval(stats, default=30)
    assert adv["samples"] == 2
    assert adv["value"] is None
    assert "3" in adv["reason"]


def test_suggest_yield_poll_interval_excludes_futility_killed_samples_from_the_floor():
    # 3 clean yields + 2 that yielded then were futility-killed anyway: the floor
    # is met on 3, not 5 — a futility-killed pass's job duration never counts,
    # even though the job itself did report a duration.
    stats = _phase_stats_yield([(100.0, None), (120.0, None), (110.0, None),
                                 (150.0, "repeat_stall"), (140.0, "progress_stall")])
    assert stats["job_duration_samples"] == 3
    adv = learn.suggest_yield_poll_interval(stats, default=30)
    assert adv["samples"] == 3
    assert adv["value"] is not None


def test_suggest_yield_poll_interval_scales_with_job_duration_and_the_step_cap_binds():
    stats = _phase_stats_yield([(100.0, None), (120.0, None), (110.0, None)])
    adv = learn.suggest_yield_poll_interval(stats, default=30)
    assert adv["bounds"] == [10, 300]
    assert adv["job_duration_p50"] == 110.0
    # 10% of the 110 s median job is 11 s, but the ±50%-per-step window around the
    # 30 s default floors at 15 s — the step cap binds tighter than the raw target.
    assert adv["step_cap"] == [15, 45]
    assert adv["value"] == 15


def test_suggest_yield_poll_interval_clamps_to_the_hard_upper_bound_of_300():
    stats = _phase_stats_yield([(20000.0, None), (21000.0, None), (20500.0, None)])
    adv = learn.suggest_yield_poll_interval(stats, default=30, current=250)
    assert adv["bounds"] == [10, 300]
    assert adv["step_cap"] == [125, 375]
    assert adv["value"] == 300   # the hard 300 s bound binds tighter than the 375 s step cap


def test_suggest_yield_poll_interval_clamps_to_the_hard_lower_bound_of_10():
    stats = _phase_stats_yield([(5.0, None), (6.0, None), (5.5, None)])
    adv = learn.suggest_yield_poll_interval(stats, default=30, current=12)
    assert adv["bounds"] == [10, 300]
    assert adv["step_cap"] == [6, 18]
    assert adv["value"] == 10   # the hard 10 s floor binds tighter than the 6 s step cap


def test_advise_reports_samples_and_reason_for_every_phase():
    summary = learn.summarize(_events_for_phase(4, [80.0, 90.0], futile_count=1))
    rows = learn.advise(summary, "proposer_timeout", None, default=5400)
    assert len(rows) == 1
    assert rows[0]["phase"] == 4 and rows[0]["samples"] == 2 and rows[0]["value"] is None


def test_advise_rejects_a_knob_with_no_suggestion_engine():
    # Every ADJUSTABLE_KNOB has an engine (SUGGESTION_ENGINES covers the
    # allowlist exactly); the CLI's `--knob` choices are the allowlist itself, so
    # the only way `advise` ever sees an unrecognised name is a caller bypassing
    # the CLI — this proves that path still refuses cleanly rather than guessing.
    summary = learn.summarize(_events_for_phase(4, [80.0, 90.0, 85.0]))
    assert set(learn.SUGGESTION_ENGINES) == learn.ADJUSTABLE_KNOBS
    import pytest
    with pytest.raises(ValueError):
        learn.advise(summary, "not_a_real_knob", None, default=30)


def test_advise_supports_yield_poll_interval():
    summary = learn.summarize(_events_for_phase_yield(8, [(100.0, None), (120.0, None), (110.0, None)]))
    rows = learn.advise(summary, "yield_poll_interval", None, default=30)
    assert len(rows) == 1 and rows[0]["phase"] == 8 and rows[0]["value"] == 15


def test_yield_poll_interval_steps_relative_to_its_own_default_not_the_proposer_timeout():
    # Measured case: a 3000 s median job with 5 samples. Stepped from 30 s (the
    # poll's own default) the ±50 % window caps it at 45 s; stepped from 1800 s
    # (what `specstride learn` used to pass for every knob) the window [900, 2700]
    # misses the [10, 300] bound entirely, the degenerate branch drops the step cap,
    # and the knob would jump 30 → 300 in one apply.
    stats = _phase_stats_yield([(3000.0, None)] * 5)
    assert stats["job_duration_p50"] == 3000.0 and stats["job_duration_samples"] == 5
    assert learn.suggest_yield_poll_interval(stats, default=30)["value"] == 45
    assert learn.suggest_yield_poll_interval(stats, default=1800)["value"] == 300


# -- apply / revert / resolve (§5.4 storage, §5.5 invariant 3) ----------------
def _applied_paths(tmp_path):
    return str(tmp_path / "learning" / "applied.json"), str(tmp_path / "events.jsonl")


def test_apply_writes_applied_json_with_provenance_and_emits_knob_adjusted(tmp_path):
    summary = learn.summarize(_events_for_phase(3, [1200.0, 1300.0, 1250.0]))
    applied, events_file = _applied_paths(tmp_path)
    entry = learn.apply_proposer_timeout(summary, 3, 5400, applied, events_file=events_file,
                                          run_id="learn-fixed-1")
    # provenance: run ids, sample count, previous value, timestamp — all present
    assert entry["run_id"] == "learn-fixed-1"
    assert entry["knob"] == "proposer_timeout" and entry["phase"] == 3
    assert entry["previous"] == 5400
    assert entry["samples"] == 3
    assert entry["source_runs"] == ["run-X"]
    assert entry["applied_at"]
    # applied.json is separate from any observation file, and is JSON-lines
    lines = [json.loads(l) for l in open(applied) if l.strip()]
    assert len(lines) == 1 and lines[0]["action"] == "apply"
    # one knob_adjusted event line was emitted
    ev_lines = [json.loads(l) for l in open(events_file) if l.strip()]
    assert len(ev_lines) == 1
    assert ev_lines[0]["event"] == "knob_adjusted"
    assert ev_lines[0]["knob"] == "proposer_timeout"
    assert ev_lines[0]["from"] == 5400 and ev_lines[0]["to"] == entry["value"]
    assert ev_lines[0]["samples"] == 3


def test_apply_refuses_a_knob_outside_the_allowlist(tmp_path):
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "learn.py"), "apply",
         "--events", FIXTURE, "--knob", "critic_timeout", "--phase", "1",
         "--default", "5400", "--feature-dir", str(tmp_path)],
        capture_output=True, text=True)
    assert r.returncode != 0
    assert "not in the adjustable-knob allowlist" in r.stderr or "invalid choice" in r.stderr


def test_apply_refuses_below_the_evidence_floor_and_writes_nothing(tmp_path):
    summary = learn.summarize(_events_for_phase(3, [80.0, 90.0], futile_count=1))
    applied, events_file = _applied_paths(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        learn.apply_proposer_timeout(summary, 3, 5400, applied, events_file=events_file)
    assert not os.path.exists(applied)
    assert not os.path.exists(events_file)


def test_revert_restores_the_prior_value(tmp_path):
    summary = learn.summarize(_events_for_phase(3, [1200.0, 1300.0, 1250.0]))
    applied, events_file = _applied_paths(tmp_path)
    entry = learn.apply_proposer_timeout(summary, 3, 5400, applied, events_file=events_file,
                                          run_id="learn-fixed-2")
    assert entry["value"] != 5400
    assert learn.effective_value(applied, "proposer_timeout", 3) == entry["value"]
    rev = learn.revert_run("learn-fixed-2", applied, events_file=events_file)
    assert rev["value"] == 5400            # restored to the previous (default) value
    assert rev["reverts_run_id"] == "learn-fixed-2"
    assert learn.effective_value(applied, "proposer_timeout", 3) == 5400
    # a second revert of the same, already-reverted run id is refused, not guessed at
    import pytest
    with pytest.raises(ValueError):
        learn.revert_run("learn-fixed-2", applied, events_file=events_file)


def test_revert_refuses_a_superseded_apply_to_avoid_clobbering_a_later_one(tmp_path):
    summary = learn.summarize(_events_for_phase(3, [1200.0, 1300.0, 1250.0]))
    applied, events_file = _applied_paths(tmp_path)
    learn.apply_proposer_timeout(summary, 3, 5400, applied, events_file=events_file, run_id="learn-old")
    later = learn.apply_proposer_timeout(summary, 3, 5400, applied, events_file=events_file, run_id="learn-new")
    import pytest
    with pytest.raises(ValueError):
        learn.revert_run("learn-old", applied, events_file=events_file)
    # the later decision must still be the effective one
    assert learn.effective_value(applied, "proposer_timeout", 3) == later["value"]


def test_off_reverts_every_currently_applied_knob(tmp_path):
    applied, events_file = _applied_paths(tmp_path)
    s3 = learn.summarize(_events_for_phase(3, [1200.0, 1300.0, 1250.0], run_id="run-A"))
    s5 = learn.summarize(_events_for_phase(5, [2000.0, 2100.0, 2050.0], run_id="run-B"))
    learn.apply_proposer_timeout(s3, 3, 5400, applied, events_file=events_file, run_id="learn-off-1")
    learn.apply_proposer_timeout(s5, 5, 5400, applied, events_file=events_file, run_id="learn-off-2")
    reverted = learn.revert_all(applied, events_file=events_file)
    assert {e["reverts_run_id"] for e in reverted} == {"learn-off-1", "learn-off-2"}
    assert learn.effective_value(applied, "proposer_timeout", 3) == 5400
    assert learn.effective_value(applied, "proposer_timeout", 5) == 5400
    # idempotent: nothing left to revert
    assert learn.revert_all(applied, events_file=events_file) == []


# -- apply / revert: yield_poll_interval (same provenance/floor shape) -------
def test_apply_yield_poll_interval_writes_applied_json_with_provenance(tmp_path):
    summary = learn.summarize(_events_for_phase_yield(6, [(100.0, None), (120.0, None), (110.0, None)]))
    applied, events_file = _applied_paths(tmp_path)
    entry = learn.apply_yield_poll_interval(summary, 6, 30, applied, events_file=events_file,
                                             run_id="learn-yp-1")
    assert entry["knob"] == "yield_poll_interval" and entry["phase"] == 6
    assert entry["previous"] == 30
    assert entry["samples"] == 3
    assert entry["value"] == 15
    lines = [json.loads(l) for l in open(applied) if l.strip()]
    assert len(lines) == 1 and lines[0]["action"] == "apply"
    ev_lines = [json.loads(l) for l in open(events_file) if l.strip()]
    assert len(ev_lines) == 1
    assert ev_lines[0]["event"] == "knob_adjusted" and ev_lines[0]["knob"] == "yield_poll_interval"
    assert ev_lines[0]["from"] == 30 and ev_lines[0]["to"] == 15


def test_apply_yield_poll_interval_refuses_below_the_evidence_floor_and_writes_nothing(tmp_path):
    summary = learn.summarize(_events_for_phase_yield(6, [(100.0, None), (120.0, None)]))
    applied, events_file = _applied_paths(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        learn.apply_yield_poll_interval(summary, 6, 30, applied, events_file=events_file)
    assert not os.path.exists(applied)
    assert not os.path.exists(events_file)


def test_revert_restores_the_prior_value_for_yield_poll_interval(tmp_path):
    summary = learn.summarize(_events_for_phase_yield(6, [(100.0, None), (120.0, None), (110.0, None)]))
    applied, events_file = _applied_paths(tmp_path)
    entry = learn.apply_yield_poll_interval(summary, 6, 30, applied, events_file=events_file,
                                             run_id="learn-yp-2")
    assert entry["value"] != 30
    assert learn.effective_value(applied, "yield_poll_interval", 6) == entry["value"]
    rev = learn.revert_run("learn-yp-2", applied, events_file=events_file)
    assert rev["value"] == 30
    assert learn.effective_value(applied, "yield_poll_interval", 6) == 30


def test_off_reverts_every_currently_applied_knob_across_both_kinds(tmp_path):
    applied, events_file = _applied_paths(tmp_path)
    s_pt = learn.summarize(_events_for_phase(3, [1200.0, 1300.0, 1250.0], run_id="run-A"))
    s_yp = learn.summarize(_events_for_phase_yield(6, [(100.0, None), (120.0, None), (110.0, None)],
                                                    run_id="run-B"))
    learn.apply_proposer_timeout(s_pt, 3, 5400, applied, events_file=events_file, run_id="off-pt")
    learn.apply_yield_poll_interval(s_yp, 6, 30, applied, events_file=events_file, run_id="off-yp")
    reverted = learn.revert_all(applied, events_file=events_file)
    assert {e["reverts_run_id"] for e in reverted} == {"off-pt", "off-yp"}
    assert learn.effective_value(applied, "proposer_timeout", 3) == 5400
    assert learn.effective_value(applied, "yield_poll_interval", 6) == 30


# -- the shell-callable integration point: resolve ---------------------------
def test_resolve_returns_the_default_when_nothing_is_applied(tmp_path):
    applied, _ = _applied_paths(tmp_path)   # file does not even exist yet
    assert learn.resolve_knob("proposer_timeout", 7, 5400, applied,
                               env={"SPECSTRIDE_LEARNING": "apply"}) == 5400


def test_specstride_learning_off_or_unset_is_a_total_no_op_for_resolve(tmp_path):
    summary = learn.summarize(_events_for_phase(3, [1200.0, 1300.0, 1250.0]))
    applied, events_file = _applied_paths(tmp_path)
    entry = learn.apply_proposer_timeout(summary, 3, 5400, applied, events_file=events_file,
                                          run_id="learn-fixed-3")
    assert entry["value"] != 5400
    # unset, "off", and any other spelling all ignore the applied decision entirely
    assert learn.resolve_knob("proposer_timeout", 3, 5400, applied, env={}) == 5400
    assert learn.resolve_knob("proposer_timeout", 3, 5400, applied, env={"SPECSTRIDE_LEARNING": "off"}) == 5400
    assert learn.resolve_knob("proposer_timeout", 3, 5400, applied, env={"SPECSTRIDE_LEARNING": "suggest"}) == 5400
    # only the explicit "apply" value turns resolve on
    assert learn.resolve_knob("proposer_timeout", 3, 5400, applied, env={"SPECSTRIDE_LEARNING": "apply"}) == entry["value"]


def test_resolve_cli_is_the_documented_shell_callable_entry_point(tmp_path):
    # the exact call the per-phase-cap branch's `resolve_proposer_timeout` makes:
    #   python3 lib/learn.py resolve --knob proposer_timeout --phase N --default S
    # printing one integer on stdout.
    summary = learn.summarize(_events_for_phase(9, [1200.0, 1300.0, 1250.0]))
    applied, _ = _applied_paths(tmp_path)
    entry = learn.apply_proposer_timeout(summary, 9, 5400, applied, run_id="learn-cli-1")
    env = dict(os.environ, SPECSTRIDE_LEARNING="apply")
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "learn.py"), "resolve",
         "--knob", "proposer_timeout", "--phase", "9", "--default", "5400",
         "--applied-file", applied],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(entry["value"])
    # and with SPECSTRIDE_LEARNING unset, the same call is a total no-op
    env_off = {k: v for k, v in os.environ.items() if k != "SPECSTRIDE_LEARNING"}
    r2 = subprocess.run(
        [sys.executable, os.path.join(HERE, "learn.py"), "resolve",
         "--knob", "proposer_timeout", "--phase", "9", "--default", "5400",
         "--applied-file", applied],
        capture_output=True, text=True, env=env_off)
    assert r2.returncode == 0 and r2.stdout.strip() == "5400"


def test_resolve_yield_poll_interval_clamps_to_the_hard_bounds(tmp_path):
    applied, _ = _applied_paths(tmp_path)
    learn._append_jsonl(applied, {
        "schema": learn.LEARN_APPLIED_SCHEMA, "action": "apply", "run_id": "r1",
        "knob": "yield_poll_interval", "phase": 6, "value": 500, "previous": 30,
        "samples": 3, "source_runs": [], "metric": "job_duration_p50", "applied_at": "x",
    })
    # an out-of-band 500 (e.g. hand-edited) still clamps to the hard 300 s bound
    assert learn.resolve_knob("yield_poll_interval", 6, 30, applied, env={"SPECSTRIDE_LEARNING": "apply"}) == 300
    assert learn.resolve_knob("yield_poll_interval", 6, 30, applied, env={}) == 30


# -- CLI: apply now round-trips for both new knobs too -----------------------
def _write_events(tmp_path, events, name="events.jsonl"):
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    return str(path)


def test_cli_apply_supports_yield_poll_interval(tmp_path):
    events = _write_events(tmp_path, _events_for_phase_yield(6, [(100.0, None), (120.0, None), (110.0, None)]))
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "learn.py"), "apply",
         "--events", events, "--knob", "yield_poll_interval",
         "--phase", "6", "--default", "30", "--feature-dir", str(tmp_path)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "applied yield_poll_interval[6]" in r.stdout
    applied_file = str(tmp_path / "learning" / "applied.json")
    assert learn.effective_value(applied_file, "yield_poll_interval", 6) == 15


# -- the phase shape keys samples and decisions (§5.5 invariant 4) -----------
def _shaped_history():
    # three samples under shape "aaaa", then three under the edited phase "bbbb"
    return (_events_for_phase(3, [1200.0, 1300.0, 1250.0], run_id="run-old", shape="aaaa")
            + _events_for_phase(3, [300.0, 320.0], run_id="run-new", shape="bbbb"))


def test_samples_of_another_shape_stop_counting():
    events = _shaped_history()
    whole = learn.summarize(events)["phases"]["3"]
    assert whole["work_sec_samples"] == 5 and whole["shape"] is None
    new = learn.summarize(events, phase_shapes={3: "bbbb"})["phases"]["3"]
    assert new["work_sec_samples"] == 2 and new["shape"] == "bbbb"
    assert new["other_shape_attempts"] == 3 and new["runs_seen"] == ["run-new"]
    rows = learn.advise(learn.summarize(events, phase_shapes={3: "bbbb"}), "proposer_timeout", 3, 5400,
                        shapes={3: "bbbb"})
    assert rows[0]["value"] is None and rows[0]["samples"] == 2   # below the floor under the new shape
    # a run that recorded no shape is not evidence for any shape
    unshaped = _events_for_phase(3, [1200.0, 1300.0, 1250.0], run_id="run-legacy")
    assert "3" not in learn.summarize(unshaped, phase_shapes={3: "bbbb"})["phases"]


def test_a_decision_stops_applying_when_the_phase_is_edited(tmp_path):
    applied, events_file = _applied_paths(tmp_path)
    summary = learn.summarize(_shaped_history(), phase_shapes={3: "aaaa"})
    entry = learn.apply_proposer_timeout(summary, 3, 5400, applied, events_file=events_file,
                                          run_id="learn-shape-1", shape="aaaa")
    assert entry["shape"] == "aaaa" and entry["schema"] == learn.LEARN_APPLIED_SCHEMA
    env = {"SPECSTRIDE_LEARNING": "apply"}
    assert learn.resolve_knob("proposer_timeout", 3, 5400, applied, env=env, shape="aaaa") == entry["value"]
    assert learn.resolve_knob("proposer_timeout", 3, 5400, applied, env=env, shape="bbbb") == 5400
    # reverting is scoped to the same key, and leaves another shape's decision alone
    s_b = learn.summarize(_events_for_phase(3, [2000.0, 2100.0, 2050.0], run_id="run-b", shape="bbbb"),
                          phase_shapes={3: "bbbb"})
    other = learn.apply_proposer_timeout(s_b, 3, 5400, applied, run_id="learn-shape-2", shape="bbbb")
    learn.revert_run("learn-shape-1", applied)
    assert learn.effective_value(applied, "proposer_timeout", 3, "aaaa") == 5400
    assert learn.effective_value(applied, "proposer_timeout", 3, "bbbb") == other["value"]
    assert {e["reverts_run_id"] for e in learn.revert_all(applied)} == {"learn-shape-2"}


def test_a_decision_recorded_before_shapes_is_never_silently_applied(tmp_path):
    applied, _ = _applied_paths(tmp_path)
    learn._append_jsonl(applied, {"schema": "specstride.learn.applied/1", "action": "apply", "run_id": "old",
                                  "knob": "proposer_timeout", "phase": 3, "value": 2400, "previous": 1800})
    env = {"SPECSTRIDE_LEARNING": "apply"}
    assert learn.resolve_knob("proposer_timeout", 3, 1800, applied, env=env, shape="aaaa") == 1800
    assert "predate phase-shape keys" in learn.unshaped_notice(applied, "proposer_timeout", 3, "aaaa")
    assert learn.unshaped_notice(applied, "proposer_timeout", 4, "aaaa") is None
    r = subprocess.run([sys.executable, os.path.join(HERE, "learn.py"), "resolve", "--knob", "proposer_timeout",
                        "--phase", "3", "--default", "1800", "--applied-file", applied, "--phase-shape", "aaaa"],
                       capture_output=True, text=True, env=dict(os.environ, SPECSTRIDE_LEARNING="apply"))
    assert r.returncode == 0 and r.stdout.strip() == "1800"
    assert "predate phase-shape keys" in r.stderr


def test_cli_apply_with_specs_keys_the_decision_on_the_current_shape(tmp_path):
    sys.path.insert(0, HERE)
    import specstride_spec
    spec = tmp_path / "SPECS.md"
    spec.write_text("## Phase 3 — Build\n\n### Acceptance criteria\n- [ ] it builds\n")
    shape = specstride_spec.phase_shape(spec.read_text(), 3, "native")
    events = _write_events(tmp_path, _events_for_phase(3, [1200.0, 1300.0, 1250.0], shape=shape))
    r = subprocess.run([sys.executable, os.path.join(HERE, "learn.py"), "apply", "--events", events,
                        "--phase", "3", "--default", "5400", "--feature-dir", str(tmp_path),
                        "--specs", str(spec)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    applied = str(tmp_path / "learning" / "applied.json")
    assert learn.effective_value(applied, "proposer_timeout", 3, shape) is not None
    assert learn.effective_value(applied, "proposer_timeout", 3) is None


def test_observe_with_a_phase_shape_observes_only_that_shape():
    doc = learn.observation_for_phase(_shaped_history(), 3, shape="bbbb")
    assert doc["shape"] == "bbbb" and doc["observation"]["attempts_total"] == 2


# -- `specstride learn`: the dispatcher passes the asked knob's own default ---
def _cli_workdir(tmp_path, events):
    wd = tmp_path / "proj"
    run_dir = wd / ".specstride" / "features" / "tf" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return wd


def _specstride_learn(wd, *args, env=None):
    import specstride_env
    prefixes = (specstride_env.ENV_PREFIX, specstride_env.LEGACY_ENV_PREFIX)
    base = {k: v for k, v in os.environ.items() if not k.startswith(prefixes)}
    base.update(env or {})
    return subprocess.run(["bash", os.path.join(os.path.dirname(HERE), "specstride"), "learn",
                           "-w", str(wd), "--feature", "tf", *args],
                          capture_output=True, text=True, env=base, timeout=60)


def test_a_bare_show_for_yield_poll_interval_steps_relative_to_30_not_1800(tmp_path):
    events = [{k: v for k, v in e.items() if k != "_src"}
              for e in _events_for_phase_yield(8, [(3000.0, None)] * 5)]
    wd = _cli_workdir(tmp_path, events)
    r = _specstride_learn(wd, "--show", "--knob", "yield_poll_interval")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "current=30s → suggest 45s" in r.stdout, r.stdout
    assert "step_cap=[15, 45]" in r.stdout
    # an operator's SPECSTRIDE_YIELD_POLL is the default it steps from, as at run time
    r = _specstride_learn(wd, "--show", "--knob=yield_poll_interval", env={"SPECSTRIDE_YIELD_POLL": "20"})
    assert "current=20s → suggest 30s" in r.stdout, r.stdout
    # and proposer_timeout still defaults to 1800
    r = _specstride_learn(wd, "--show")
    assert "current=1800s" in r.stdout, r.stdout


# -- proposer_cap: what each run ran a phase under ---------------------------
def test_proposer_cap_is_recorded_once_per_run_and_phase():
    # proposer_cap fires once per ATTEMPT but is resolved once per PHASE, and
    # every value on it is a JSON string: dedupe on (run, phase) and parse ints.
    events = []
    for rid in ("run-1", "run-2"):
        events.append({"event": "run_start", "run_id": rid, "ts": "1", "_src": rid})
        events.append({"event": "phase_start", "run_id": rid, "phase": "4", "ts": "2", "_src": rid})
        for attempt, secs in (("1", "1800"), ("2", "9999")):
            events.append({"event": "proposer_start", "run_id": rid, "phase": "4", "attempt": attempt,
                           "ts": "3", "_src": rid})
            events.append({"event": "proposer_cap", "run_id": rid, "phase": "4", "attempt": attempt,
                           "role": "proposer", "seconds": secs, "source": "learned",
                           "yield_poll": "45", "yield_poll_source": "learned", "ts": "3", "_src": rid})
    caps = learn.summarize(events)["phases"]["4"]["caps"]
    assert caps == [
        {"run": "run-1", "seconds": 1800, "source": "learned", "yield_poll": 45, "yield_poll_source": "learned"},
        {"run": "run-2", "seconds": 1800, "source": "learned", "yield_poll": 45, "yield_poll_source": "learned"},
    ]
    assert learn.SCHEMA.startswith("specstride.learn.summary/")


# -- diagnostician_done: the declared case, per attempt and per phase --------
def test_diagnostician_case_is_counted_per_phase():
    events = _events_for_phase(5, [100.0, 110.0, 120.0])
    # the critic's events carry no run_id: they are attributed to the stream's run
    extra = [{"event": "diagnostician_done", "phase": "5", "attempt": "1", "case": "grounding"},
             {"event": "diagnostician_done", "phase": "5", "attempt": "2", "case": "real_gap"},
             {"event": "diagnostician_done", "phase": "5", "attempt": "3", "bytes": "10"}]   # pre-case
    for e in extra:
        e.update(ts="2000", _src="synthetic")
    s = learn.summarize(events + extra)
    assert s["phases"]["5"]["diagnostician_cases"] == {"grounding": 1, "real_gap": 1, "unknown": 1}
    assert _attempt(s, "run-X", 5, 2)["diagnostician_case"] == "real_gap"


# -- evaluate (item 4): baseline, primaries, the MDE rule, the four labels --
def _episode(run, phase, costs, *, backend="prime:sol", shape="S1", cap=None, futile=0,
             secs=600.0, t0=1000.0, extra=()):
    """One run touching one phase: one billed pass per entry of `costs` (each its
    own attempt, rejected until the last, which is approved), then `futile`
    futility-killed passes. `cap` = (seconds, source[, arm]) stamps proposer_cap."""
    events, ts = [], [t0]

    def emit(ev):
        ev = dict(ev)
        ev.setdefault("ts", str(ts[0]))
        ev["_src"] = run
        events.append(ev)
        ts[0] += 1

    emit({"event": "run_start", "run_id": run, "backend": backend})
    emit({"event": "phase_start", "run_id": run, "phase": phase, "title": "T", "shape": shape})
    attempt = 0
    for i, cost in enumerate(list(costs) + [None] * futile):
        attempt += 1
        emit({"event": "proposer_start", "run_id": run, "phase": phase, "attempt": attempt})
        if cap:
            c = {"event": "proposer_cap", "run_id": run, "phase": str(phase), "attempt": str(attempt),
                 "seconds": str(cap[0]), "source": cap[1]}
            if len(cap) > 2:
                c["arm"] = cap[2]
            emit(c)
        emit({"event": "iter_start", "run_id": run, "iter": 1})
        if cost is None:
            emit({"event": "pass_killed", "run_id": run, "iter": 1, "reason": "repeat_stall", "elapsed": 99})
            emit({"event": "agent_result", "run_id": run, "is_error": True, "subtype": "missing_terminal"})
        else:
            emit({"event": "agent_result", "run_id": run, "is_error": False, "cost_usd": cost,
                  "duration_ms": int(secs * 1000)})
            emit({"event": "evidence_written", "run_id": run})
            emit({"event": "iter_done", "run_id": run, "iter": 1, "evidence": "present"})
        last = i == len(costs) - 1 and not futile
        emit({"event": "critic_start", "phase": phase, "attempt": attempt})
        emit({"event": "verdict", "phase": phase, "attempt": attempt, "result": "APPROVED" if last else "REJECTED"})
        emit({"event": "attempt_archived", "run_id": run, "phase": phase, "attempt": attempt})
    for ev in extra:
        emit(ev)
    emit({"event": "phase_done", "run_id": run, "phase": phase, "attempt": attempt})
    return events


_BASE_COSTS = [1.0, 1.2, 0.9, 1.1, 1.0, 0.95]


def _baseline_events():
    return (_episode("base-1", 3, _BASE_COSTS[:3], secs=1200.0)
            + _episode("base-2", 3, _BASE_COSTS[3:], secs=1300.0, t0=2000.0))


def _applied_decision(tmp_path):
    applied, events_file = _applied_paths(tmp_path)
    summary = learn.summarize(_baseline_events(), phase_shapes={3: "S1"})
    entry = learn.apply_proposer_timeout(summary, 3, 1800, applied, events_file=events_file,
                                          run_id="learn-eval", shape="S1")
    return applied, events_file, entry


def _arm(entry, costs, run="app-1", t0=5000.0, **kw):
    kw.setdefault("secs", 1250.0)
    return _episode(run, 3, costs, cap=(entry["value"], "learned"), t0=t0, **kw)


def test_apply_records_a_baseline_with_backend_shape_and_guardrail_counts(tmp_path):
    _applied, _events, entry = _applied_decision(tmp_path)
    base = entry["baseline"]
    assert base["backend"] == "prime:sol" and base["shape"] == "S1"
    assert base["runs"] == ["base-1", "base-2"]
    assert [v for _, v in base["cost"]] == _BASE_COSTS and len(base["wall"]) == 6
    g = base["guardrails"]
    assert g["verdicts"] == 6 and g["first_verdicts"] == 2 and g["first_approved"] == 0
    assert g["episodes"] == 2 and g["malformed"] == 0


def test_mde_is_never_mistaken_for_a_detectable_effect_at_three_per_arm():
    # 3 passes per arm, the corpus's within-phase sd: nothing under ≈ +218 % is visible
    m = learn.mde(0.506, 3, 3)
    assert 1.1 < m < 1.2
    assert math.exp(m) - 1 >= 1.5
    assert learn.mde(0.1, 6, 6) == learn.mde(0.5, 6, 6)            # s is floored at 0.50
    assert learn.mde(0.5, 6, 6, mbar=3) > learn.mde(0.5, 6, 6)      # clustering widens it


def test_evaluate_labels_a_large_cost_drop_helped(tmp_path):
    _applied, _ev, entry = _applied_decision(tmp_path)
    arm = _arm(entry, [0.1, 0.12, 0.09]) + _arm(entry, [0.11, 0.1, 0.095], run="app-2", t0=6000.0)
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    assert result["label"] == "helped" and result["cost"]["label"] == "helped"
    assert result["wall"]["label"] == "neutral"
    assert result["cost"]["r"] < -result["cost"]["mde"]
    assert result["applied_runs"] == ["app-1", "app-2"]


def test_evaluate_labels_a_small_change_neutral_with_its_mde(tmp_path):
    _applied, _ev, entry = _applied_decision(tmp_path)
    arm = (_arm(entry, [c * 0.8 for c in _BASE_COSTS[:3]])
           + _arm(entry, [c * 0.8 for c in _BASE_COSTS[3:]], run="app-2", t0=6000.0))
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    assert result["label"] == "neutral"
    assert abs(result["cost"]["r"]) < result["cost"]["mde"]
    line = learn.format_evaluation(result)
    assert "neutral" in line and "MDE ±" in line          # the MDE is printed beside the effect


def test_evaluate_labels_a_cost_regression_regressed(tmp_path):
    _applied, _ev, entry = _applied_decision(tmp_path)
    arm = _arm(entry, [10.0, 12.0, 9.0]) + _arm(entry, [11.0, 10.0, 9.5], run="app-2", t0=6000.0)
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    assert result["label"] == "regressed"


def test_evaluate_is_insufficient_below_six_per_arm_and_does_nothing_else(tmp_path):
    _applied, _ev, entry = _applied_decision(tmp_path)
    arm = _arm(entry, [0.1, 0.1, 0.1]) + _arm(entry, [0.1, 0.1], run="app-2", t0=6000.0)
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    assert result["label"] == "insufficient"
    assert result["cost"]["n_a"] == 5 and result["cost"]["r"] is None


def test_futility_killed_passes_are_excluded_from_both_arms_alike(tmp_path):
    applied, _ev = _applied_paths(tmp_path)
    base = (_episode("base-1", 3, _BASE_COSTS[:3], secs=1200.0, futile=2)
            + _episode("base-2", 3, _BASE_COSTS[3:], secs=1300.0, t0=2000.0))
    entry = learn.apply_proposer_timeout(learn.summarize(base, phase_shapes={3: "S1"}), 3, 1800, applied,
                                          run_id="learn-fut", shape="S1")
    assert len(entry["baseline"]["cost"]) == 6
    arm = _arm(entry, [1.0] * 3, futile=3) + _arm(entry, [1.0] * 3, run="app-2", t0=6000.0)
    result = learn.evaluate_decision(learn.summarize(base + arm), entry, "S1")
    assert (result["cost"]["n_a"], result["cost"]["n_b"]) == (6, 6)


def test_a_backend_change_resets_the_baseline(tmp_path):
    _applied, _ev, entry = _applied_decision(tmp_path)
    arm = (_arm(entry, [0.1] * 3) + _arm(entry, [0.1] * 3, run="app-2", t0=6000.0)
           + _arm(entry, [0.1] * 3, run="app-3", t0=7000.0, backend="claude"))
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    assert result["label"] == "insufficient" and result["reset"] == "backend"
    assert result["cost"]["n_a"] == 0


def test_a_shape_change_resets_the_baseline(tmp_path):
    _applied, _ev, entry = _applied_decision(tmp_path)
    arm = _arm(entry, [0.1] * 3, shape="S2") + _arm(entry, [0.1] * 3, run="app-2", t0=6000.0, shape="S2")
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S2")
    assert result["label"] == "insufficient" and result["reset"] == "shape"
    # and under the old shape the edited phase's runs are not its samples either
    same = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    assert same["cost"]["n_a"] == 0


def test_a_decision_without_a_baseline_is_insufficient(tmp_path):
    decision = {"knob": "proposer_timeout", "phase": 3, "shape": "S1", "run_id": "old",
                "value": 2400, "previous": 1800, "action": "apply"}
    result = learn.evaluate_decision(learn.summarize(_baseline_events()), decision, "S1")
    assert result["label"] == "insufficient" and "no baseline" in result["reason"]


def test_counterfactuals_from_logs_are_wall_only_for_a_shorter_cap_and_absent_for_a_longer_one():
    base = {"wall": [["r1", 1000.0], ["r2", 2000.0]] * 3, "cost": []}
    shorter = learn.counterfactual({"knob": "proposer_timeout", "value": 900, "previous": 1800, "baseline": base})
    assert shorter["kind"] == "shorter_cap_wall_only" and "cost" not in shorter
    assert shorter["wall"]["r"] < 0
    longer = learn.counterfactual({"knob": "proposer_timeout", "value": 2700, "previous": 1800, "baseline": base})
    assert longer["kind"] == "censored" and "wall" not in longer


def test_evaluate_entries_never_move_a_value_and_are_written_only_on_change(tmp_path):
    applied, events_file, entry = _applied_decision(tmp_path)
    arm = _arm(entry, [0.1] * 3) + _arm(entry, [0.1] * 3, run="app-2", t0=6000.0)
    summary = learn.summarize(_baseline_events() + arm)
    result = learn.evaluate_decision(summary, entry, "S1")
    assert learn.record_evaluation(result, applied, events_file) is not None
    assert learn.record_evaluation(learn.evaluate_decision(summary, entry, "S1"), applied, events_file) is None
    assert learn.effective_value(applied, "proposer_timeout", 3, "S1") == entry["value"]
    assert [e["action"] for e in learn._read_jsonl(applied)] == ["apply", "evaluate"]
    assert learn._read_jsonl(applied)[0] == entry                    # the apply entry is untouched
    evs = [e for e in learn._read_jsonl(events_file) if e["event"] == "knob_evaluated"]
    assert len(evs) == 1 and evs[0]["label"] == "helped"
    learn.revert_run("learn-eval", applied)                         # an evaluate entry is not a decision
    assert learn.active_decisions(applied) == []


def test_cli_evaluate_over_a_runs_tree(tmp_path):
    applied, _ev, entry = _applied_decision(tmp_path)
    runs = tmp_path / "runs"
    arm = _arm(entry, [0.1] * 3) + _arm(entry, [0.1] * 3, run="app-2", t0=6000.0)
    for run, evs in (("r1", _baseline_events()), ("r2", arm)):
        (runs / run).mkdir(parents=True)
        (runs / run / "events.jsonl").write_text(
            "".join(json.dumps({k: v for k, v in e.items() if k != "_src"}) + "\n" for e in evs))
    cmd = [sys.executable, os.path.join(HERE, "learn.py"), "evaluate", "--events", str(runs),
           "--feature-dir", str(tmp_path), "--phase", "3", "--phase-shape", "S1"]
    dry = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True)
    assert dry.returncode == 0, dry.stderr
    assert "helped" in dry.stdout and len(learn._read_jsonl(applied)) == 1
    real = subprocess.run(cmd, capture_output=True, text=True)
    assert real.returncode == 0 and learn._read_jsonl(applied)[-1]["action"] == "evaluate"
    report = subprocess.run([sys.executable, os.path.join(HERE, "learn.py"), "evaluate", "--report",
                             "--feature-dir", str(tmp_path)], capture_output=True, text=True)
    assert "helped" in report.stdout and "MDE ±" in report.stdout


# -- guardrails, auto-revert, quarantine (item 4b) ----------------------------
def test_binomial_tails_are_exact():
    assert abs(learn.binom_upper(0, 5, 0.3) - 1.0) < 1e-12
    assert abs(learn.binom_upper(10, 10, 0.5) - 0.5 ** 10) < 1e-12
    assert abs(learn.binom_lower(0, 10, 0.5) - 0.5 ** 10) < 1e-12


def test_a_rate_guardrail_is_unknown_below_ten_never_ok():
    g = learn._rate_guard(9, 9, 0, 6)
    assert g["state"] == "unknown"
    assert learn._rate_guard(1, 10, 2, 10)["state"] == "ok"
    assert learn._rate_guard(8, 10, 0, 10)["state"] == "breach"


def test_first_attempt_approval_alarms_in_both_directions():
    assert learn._rate_guard(10, 10, 4, 10, both=True)["state"] == "breach"   # approval jumped
    assert learn._rate_guard(0, 10, 8, 10, both=True)["state"] == "breach"    # approval collapsed
    assert learn._rate_guard(4, 10, 4, 10, both=True)["state"] == "ok"


def test_critic_input_size_uses_an_exact_rank_test():
    base = [100, 110, 120, 130, 140, 150]
    assert learn._size_guard([200] * 5 + [90], base)["state"] == "breach"    # 5 of 6 above: p = 0.0076
    assert learn._size_guard([200] * 4 + [90, 90], base)["state"] == "ok"    # 4 of 6: p = 0.030
    assert learn._size_guard([200] * 5, base)["state"] == "unknown"


def test_critic_prompt_bytes_come_from_the_verdict_transcripts(tmp_path):
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    when = time.strftime("%Y%m%d-%H%M%S", time.localtime(2000.0))
    (verdicts / f"phase3.attempt1.{when}.txt").write_text(
        "# Specstride critic transcript\nphase: 3\n\n═══════════ PROMPT ═══════════\nabcdé\n\n"
        "═══════════ REPLY ═══════════\nVERDICT x: APPROVED\n")
    events = _episode("r1", 3, [1.0], t0=1990.0)
    s = learn.summarize(events, verdicts_dir=str(verdicts))
    assert _attempt(s, "r1", 3, 1)["critic_prompt_bytes"] == len("abcdé".encode())


def _verification_failed(phase, attempt):
    return {"event": "verification_failed", "phase": phase, "attempt": attempt, "rc": 1}


def test_a_guardrail_breach_reverts_even_when_the_primary_improved(tmp_path):
    applied, events_file, entry = _applied_decision(tmp_path)
    arm = (_arm(entry, [0.1] * 3, extra=[_verification_failed(3, 3)])
           + _arm(entry, [0.1] * 3, run="app-2", t0=6000.0))
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    assert result["label"] == "helped"
    assert learn.breaches(result) == ["verification"]
    revert = learn.act_on_evaluation(result, applied, events_file)
    assert revert["auto"] is True and revert["guardrail"] == ["verification"]
    assert revert["quarantined_value"] == entry["value"] and revert["backend"] == "prime:sol"
    assert learn.effective_value(applied, "proposer_timeout", 3, "S1") == entry["previous"]
    kinds = [e["action"] for e in learn._read_jsonl(applied)]
    assert kinds == ["apply", "evaluate", "revert"]
    assert learn._read_jsonl(applied)[1]["action_taken"] == "auto_reverted"
    assert revert["evaluated_from"] == learn._read_jsonl(applied)[1]["run_id"]
    names = [e["event"] for e in learn._read_jsonl(events_file)]
    assert "knob_auto_reverted" in names and "knob_evaluated" in names


def test_a_superseded_decision_skips_the_revert_instead_of_raising(tmp_path):
    applied, events_file, entry = _applied_decision(tmp_path)
    arm = (_arm(entry, [0.1] * 3, extra=[_verification_failed(3, 3)])
           + _arm(entry, [0.1] * 3, run="app-2", t0=6000.0))
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    # a manual apply lands between the read and the revert
    newer = learn.apply_proposer_timeout(learn.summarize(_baseline_events(), phase_shapes={3: "S1"}), 3, 1800,
                                          applied, run_id="learn-manual", shape="S1")
    out = learn.act_on_evaluation(result, applied, events_file)
    assert out["action"] == "evaluate" and out["action_taken"] == "skipped_superseded"
    assert learn.effective_value(applied, "proposer_timeout", 3, "S1") == newer["value"]
    assert [e for e in learn._read_jsonl(applied) if e["action"] == "revert"] == []


def test_after_off_an_auto_revert_is_a_silent_no_op(tmp_path):
    applied, events_file, entry = _applied_decision(tmp_path)
    arm = (_arm(entry, [0.1] * 3, extra=[_verification_failed(3, 3)])
           + _arm(entry, [0.1] * 3, run="app-2", t0=6000.0))
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    learn.revert_all(applied)
    before = learn._read_jsonl(applied)
    assert learn.act_on_evaluation(result, applied, events_file) is None
    assert learn._read_jsonl(applied) == before


def _quarantined(tmp_path):
    applied, events_file, entry = _applied_decision(tmp_path)
    arm = (_arm(entry, [0.1] * 3, extra=[_verification_failed(3, 3)])
           + _arm(entry, [0.1] * 3, run="app-2", t0=6000.0))
    result = learn.evaluate_decision(learn.summarize(_baseline_events() + arm), entry, "S1")
    learn.act_on_evaluation(result, applied, events_file)
    return applied, entry, arm


def test_quarantine_blocks_a_re_apply_and_force_overrides_it(tmp_path):
    applied, entry, arm = _quarantined(tmp_path)
    summary = learn.summarize(_baseline_events() + arm, phase_shapes={3: "S1"})
    import pytest
    with pytest.raises(learn.QuarantinedError) as err:
        learn.apply_proposer_timeout(summary, 3, 1800, applied, shape="S1")
    assert "learn-eval" in str(err.value)
    forced = learn.apply_proposer_timeout(summary, 3, 1800, applied, shape="S1", force=True)
    assert forced["force"] is True and forced["forced_over"]
    # a different shape is a different key: no quarantine there
    other = learn.summarize(_episode("b1", 3, _BASE_COSTS[:3], secs=1200.0, shape="S2")
                            + _episode("b2", 3, _BASE_COSTS[3:], secs=1300.0, shape="S2", t0=2000.0),
                            phase_shapes={3: "S2"})
    assert learn.quarantine_block(applied, other, "proposer_timeout", 3, "S2", "prime:sol", entry["value"]) is None


def test_quarantine_lifts_after_six_new_samples(tmp_path):
    applied, entry, arm = _quarantined(tmp_path)
    fresh = [_episode("new-1", 3, [1.0] * 3, t0=time.time() + 10),
             _episode("new-2", 3, [1.0] * 3, t0=time.time() + 100)]
    summary = learn.summarize(_baseline_events() + arm + fresh[0] + fresh[1], phase_shapes={3: "S1"})
    assert learn.quarantine_block(applied, summary, "proposer_timeout", 3, "S1", "prime:sol",
                                  entry["value"]) is None


def test_cli_apply_exits_four_on_quarantine(tmp_path):
    applied, entry, arm = _quarantined(tmp_path)
    events = _write_events(tmp_path, [{k: v for k, v in e.items() if k != "_src"}
                                      for e in _baseline_events() + arm])
    cmd = [sys.executable, os.path.join(HERE, "learn.py"), "apply", "--events", events, "--phase", "3",
           "--default", "1800", "--feature-dir", str(tmp_path), "--phase-shape", "S1"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 4 and "quarantined" in r.stderr and "learn-eval" in r.stderr
    r = subprocess.run(cmd + ["--force"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# -- observe: the §5.4 per-phase observation document ------------------------
def test_observation_for_phase_has_the_documented_shape():
    events = learn.read_events(FIXTURE)
    doc = learn.observation_for_phase(events, 1)
    assert doc["schema"] == learn.OBSERVATION_SCHEMA
    assert doc["phase"] == 1
    assert doc["generated_at"]
    assert doc["observation"] == learn.summarize(events)["phases"]["1"]


def test_observation_for_phase_is_none_not_an_error_with_no_telemetry():
    doc = learn.observation_for_phase([], 42)
    assert doc["schema"] == learn.OBSERVATION_SCHEMA
    assert doc["phase"] == 42
    assert doc["observation"] is None


def test_observation_for_phase_is_idempotent_on_the_same_input():
    events = learn.read_events(FIXTURE)
    a = learn.observation_for_phase(events, 2)
    b = learn.observation_for_phase(events, 2)
    assert a["observation"] == b["observation"]   # deterministic content
    assert a["schema"] == b["schema"] and a["phase"] == b["phase"]


def test_observation_for_phase_isolates_phases_from_each_other():
    events = learn.read_events(FIXTURE)
    obs1 = learn.observation_for_phase(events, 1)["observation"]
    obs2 = learn.observation_for_phase(events, 2)["observation"]
    assert obs1 != obs2
    assert obs1["title"] != obs2["title"] or obs1["attempts_total"] != obs2["attempts_total"]


def test_cli_observe_writes_the_phase_file_and_is_idempotent(tmp_path):
    events_path = _write_events(tmp_path, _events_for_phase_yield(6, [(100.0, None), (120.0, None)]))
    out = tmp_path / "learning" / "phase-6.json"
    r1 = subprocess.run(
        [sys.executable, os.path.join(HERE, "learn.py"), "observe",
         "--events", events_path, "--phase", "6", "--out", str(out)],
        capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr
    doc1 = json.loads(out.read_text())
    assert doc1["schema"] == learn.OBSERVATION_SCHEMA
    assert doc1["phase"] == 6
    assert doc1["observation"]["job_duration_samples"] == 2
    # calling it again with the same input overwrites with the same observation
    # content, rather than appending or erroring — the orchestrator's phase_done
    # hook can call this every time unconditionally.
    r2 = subprocess.run(
        [sys.executable, os.path.join(HERE, "learn.py"), "observe",
         "--events", events_path, "--phase", "6", "--out", str(out)],
        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    doc2 = json.loads(out.read_text())
    assert doc2["observation"] == doc1["observation"]


def test_cli_observe_never_reads_or_writes_applied_json(tmp_path):
    # `observe` is the measurement half of §5.4; it must have no knowledge of
    # applied.json at all — the two files can never be conflated (see the
    # `apply_*` provenance tests for the decision half of the same table).
    events_path = _write_events(tmp_path, _events_for_phase(3, [80.0, 90.0, 85.0]))
    out = tmp_path / "learning" / "phase-3.json"
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "learn.py"), "observe",
         "--events", events_path, "--phase", "3", "--out", str(out)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not os.path.exists(tmp_path / "learning" / "applied.json")


def test_cli_observe_reports_no_telemetry_cleanly_for_an_unseen_phase(tmp_path):
    events_path = _write_events(tmp_path, _events_for_phase(3, [80.0, 90.0, 85.0]))
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "learn.py"), "observe",
         "--events", events_path, "--phase", "999"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = json.loads(r.stdout)
    assert doc["observation"] is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
