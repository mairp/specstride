"""Deterministic presenter coverage for normalized provider-neutral activity."""

import queue
import re
import threading
import time

import present


ANSI = re.compile(r"\x1b\[[0-9;]*m")
BASE = {"time": "2026-08-10T12:34:56+0000", "phase": 3}


def rendered(event, detail="tools"):
    line = present.narrate({**BASE, **event}, detail=detail)
    return ANSI.sub("", line or "")


def test_prime_init_is_provider_neutral_and_keeps_available_identity():
    line = rendered({
        "event": "agent_init", "backend": "prime:sol", "provider": "anthropic",
        "model": "claude-sonnet", "session_id": "session-7", "schema_version": 3,
    })
    assert "agent up" in line
    assert "anthropic / claude-sonnet" in line
    assert "session session-7" in line and "schema v3" in line
    assert "Prime" not in line


def test_text_is_full_detail_only_and_marks_partial_and_final_fragments():
    partial = {"event": "agent_text", "text": "working on it", "final_fragment": False}
    final = {"event": "agent_text", "text": "done", "final_fragment": True}
    assert rendered(partial, "tools") == ""
    assert "partial working on it" in rendered(partial, "full")
    assert "final done" in rendered(final, "full")


def test_tool_lifecycle_renders_progress_results_failures_and_duration():
    start = rendered({
        "event": "agent_tool", "tool": "IPython", "status": "start",
        "summary": "src/app.py", "tool_id": "tool-1",
    })
    progress = rendered({
        "event": "agent_tool", "tool": "IPython", "status": "progress",
        "summary": "running checks", "tool_id": "tool-1",
    })
    success = rendered({
        "event": "agent_tool", "tool": "IPython", "status": "end",
        "result_summary": "3 passed", "duration_ms": 1200, "is_error": False,
        "tool_id": "tool-1",
    })
    failure = rendered({
        "event": "agent_tool", "tool": "Bash", "status": "end",
        "result_summary": "exit 2", "duration_ms": 2000, "is_error": True,
        "tool_id": "tool-2",
    })
    assert "IPython start" in start and "src/app.py" in start
    assert "IPython progress" in progress and "running checks" in progress
    assert "IPython done" in success and "3 passed" in success and "1s" in success
    assert "Bash failed" in failure and "exit 2" in failure and "2s" in failure
    assert rendered({"event": "agent_tool", "tool": "Bash"}, "milestones") == ""


def test_exact_evidence_activity_and_diagnostic_are_visible_at_existing_levels():
    evidence = rendered({
        "event": "evidence_writing", "tool": "IPython",
        "target": "/work/.specstride/GATE3-EVIDENCE.md",
        "match": "exact-expected-target",
    }, "milestones")
    diagnostic = rendered({
        "event": "agent_diagnostic", "code": "provider_retry", "severity": "warning",
        "message": "retrying in 250ms",
    })
    assert "/work/.specstride/GATE3-EVIDENCE.md" in evidence
    assert "exact-expected-target" in evidence
    assert "provider_retry" in diagnostic and "retrying in 250ms" in diagnostic
    assert rendered({"event": "agent_diagnostic", "code": "x"}, "milestones") == ""


def test_terminal_result_renders_normalized_fields_and_legacy_aliases():
    line = rendered({
        "event": "agent_result", "status": "error", "is_error": True,
        "reason_code": "provider_auth", "reason": "credentials rejected",
        "cost": 0.125, "input_tokens": 1200, "output_tokens": 345,
        "duration_ms": 65000, "turns": 4, "source": "reconciled",
    })
    assert "pass error" in line and "credentials rejected" in line
    assert "$0.12" in line and "1.2k tok in" in line and "345 tok out" in line
    assert "1m05s" in line and "4 turns" in line and "reconciled" in line


def test_observability_mode_renders_structured_raw_text_and_degraded_labels():
    structured = rendered({
        "event": "agent_observability", "mode": "structured",
        "reason": "Prime JSON schema v3 selected", "provider_format": "prime-v3",
        "signals": ["init", "text", "tool", "evidence", "result"],
    })
    raw_text = rendered({
        "event": "agent_observability", "mode": "raw-text",
        "reason": "structured schema unavailable — parsing plain output",
        "provider_format": None, "signals": ["text", "result"],
    })
    degraded = rendered({
        "event": "agent_observability", "mode": "degraded",
        "reason": "unknown schema version 9 — degraded parsing",
        "provider_format": None, "signals": ["result"],
    })
    assert "observability structured" in structured
    assert "Prime JSON schema v3 selected" in structured
    assert "prime-v3" in structured
    assert "tool" in structured and "evidence" in structured
    assert "observability raw-text" in raw_text
    assert "structured schema unavailable" in raw_text
    assert "observability degraded" in degraded
    assert "unknown schema version 9" in degraded


def test_observability_mode_change_keeps_each_reason_visible():
    first = rendered({
        "event": "agent_observability", "mode": "structured",
        "reason": "Prime JSON schema v3 selected", "provider_format": "prime-v3",
    })
    fallback = rendered({
        "event": "agent_observability", "mode": "raw-text",
        "reason": "provider dropped to plain text mid-run",
    })
    assert "observability structured" in first
    assert "Prime JSON schema v3 selected" in first
    assert "observability raw-text" in fallback
    assert "provider dropped to plain text mid-run" in fallback


def test_bounded_malformed_diagnostic_shows_code_and_message():
    line = rendered({
        "event": "agent_diagnostic", "code": "malformed_event", "severity": "warning",
        "message": "dropped 1 unparsable line (bounded)",
    })
    assert "malformed_event" in line
    assert "dropped 1 unparsable line (bounded)" in line


def test_configured_sink_failure_is_labeled_with_sink_and_reason():
    failed = rendered({
        "event": "telemetry_delivery", "sink": "otel", "batch_id": "otel-0007",
        "event_count": 12, "status": "failed", "http_status": 503,
        "reason": "Receiver rejected batch",
    })
    accepted = rendered({
        "event": "telemetry_delivery", "sink": "loki", "batch_id": "loki-0001",
        "event_count": 4, "status": "accepted", "http_status": 200,
        "reason": "Receiver accepted request",
    }, "full")
    assert "sink otel" in failed and "failed" in failed
    assert "Receiver rejected batch" in failed
    assert "503" in failed
    assert "sink loki" in accepted and "accepted" in accepted


def test_terminal_conflict_reason_is_reconciled_and_explicit():
    line = rendered({
        "event": "agent_result", "status": "error", "is_error": True,
        "reason_code": "provider_terminal_conflict",
        "reason": "provider reported error while exit code was 0",
        "source": "reconciled",
    })
    assert "pass error" in line
    assert "provider reported error while exit code was 0" in line
    assert "reconciled" in line


def test_sc012_five_facts_are_explicit_labeled_fields():
    """SC-012: mode, current phase, latest tool activity, final pass outcome,
    and configured sink failure are all present as explicit labeled fields."""
    mode = rendered({
        "event": "agent_observability", "mode": "structured",
        "reason": "Prime JSON schema v3 selected", "provider_format": "prime-v3",
    })
    phase = rendered({"event": "phase_start", "phase": 3, "total": 8, "title": "wire"})
    tool = rendered({
        "event": "agent_tool", "tool": "IPython", "status": "progress",
        "summary": "running checks", "tool_id": "tool-1",
    })
    outcome = rendered({
        "event": "agent_result", "status": "error", "is_error": True,
        "reason": "credentials rejected", "source": "provider",
    })
    sink = rendered({
        "event": "telemetry_delivery", "sink": "otel", "status": "failed",
        "http_status": 503, "reason": "Receiver rejected batch",
    })
    assert "observability" in mode
    assert "phase 3" in phase
    assert "IPython" in tool
    assert "pass error" in outcome
    assert "sink otel" in sink and "failed" in sink


def _clean(line):
    return ANSI.sub("", line or "")


def _seven_phase_timeline(phase):
    """Timeline line for one phase of a controlled seven-phase (1..7) run."""
    return _clean(present.narrate({
        "time": "2026-08-10T12:34:56+0000", "event": "phase_start",
        "phase": phase, "total": 7, "title": "wire",
    }))


def _seven_phase_card_header(phase):
    """Card header line for one phase of a controlled seven-phase (1..7) run."""
    st = present.State()
    st.update({"time": "2026-08-10T12:34:56+0000", "event": "run_start",
               "phases": 7, "proposer": "prime", "critic": "prime"})
    st.update({"time": "2026-08-10T12:34:56+0000", "event": "phase_start",
               "phase": phase, "total": 7, "title": "wire"})
    return _clean(st._header_lines(".")[0])


def test_seven_phase_timeline_reports_numerator_of_seven_through_final_phase():
    """SC-013/FR-022: a seven-phase run reports 1/7 through 7/7 in the timeline,
    never 7/6 on the final phase."""
    for phase in range(1, 8):
        line = _seven_phase_timeline(phase)
        assert f"phase {phase}/7" in line, line
    final = _seven_phase_timeline(7)
    assert "phase 7/7" in final
    assert "7/6" not in final


def test_seven_phase_card_header_reports_numerator_of_seven_through_final_phase():
    """SC-013/FR-022: the card header denominator equals the executable phase
    count (7) for every phase 1..7, including 7/7 on the final phase."""
    for phase in range(1, 8):
        header = _seven_phase_card_header(phase)
        assert f"phase {phase}/7" in header, header
    final = _seven_phase_card_header(7)
    assert "phase 7/7" in final
    assert "7/6" not in final


def test_follow_renders_received_activity_within_two_seconds_without_terminal(tmp_path):
    """T018 latency proof: no run_end is needed to flush live activity."""
    path = tmp_path / "events.jsonl"
    path.touch()
    count = 20
    ready = threading.Event()
    observed = queue.Queue()

    def consume():
        stream = present.iter_events(path, follow=True)
        ready.set()
        for _ in range(count):
            event = next(stream)
            line = present.narrate(event, detail="tools")
            observed.put((event["injected_at"], time.monotonic(), line))

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    assert ready.wait(1)
    injected = time.monotonic()
    with path.open("a", encoding="utf-8") as handle:
        for index in range(count):
            handle.write(
                '{"event":"agent_tool","tool":"IPython","status":"progress",'
                f'"summary":"step {index}","injected_at":{injected}}}\n'
            )
        handle.flush()

    samples = [observed.get(timeout=2.5) for _ in range(count)]
    reader.join(timeout=1)
    timely = [line for sent, received, line in samples if line and received - sent <= 2.0]
    assert len(timely) / count >= 0.95
    assert all("progress" in ANSI.sub("", line) for line in timely)
    assert not reader.is_alive()


# ── the gate rail re-stamped in the live timeline ────────────────────────────
import json
import os
import pathlib

import banner
import theme

DEMO = pathlib.Path(__file__).resolve().parent.parent / "docs" / "media" / "demo-events.jsonl"
PLAIN = {"NO_COLOR": "1", "SPECSTRIDE_ASCII": "1"}
RUN_END = {"time": "2026-09-26T14:40:00+0000", "event": "run_end", "outcome": "all_approved"}


def _timeline(tmp_path, events, capsys, monkeypatch, cols=100, env=None, name="events.jsonl"):
    """Replay `events` through the non-following timeline; the output lines."""
    path = tmp_path / name
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    env = {**PLAIN, **(env or {})}
    for k in ("SPECSTRIDE_LIVE_RAIL", "SPECSTRIDE_BANNER"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    present.apply_theme(theme.Theme("none"))
    capsys.readouterr()
    present.run_timeline(str(path), False, "tools", False, cols=cols)
    return capsys.readouterr().out.splitlines()


def _demo():
    return [json.loads(l) for l in DEMO.read_text(encoding="utf-8").splitlines() if l.strip()]


def _is_rail(line):
    return line.startswith(" " * present.RAIL_INDENT)


DEMO_REPLAY = """\
14:10:02  x REJECTED phase 3 (attempt 1) -- C3.2 cites a test that never ran
          ====+=========+=========+---------+---------+----   2/5 approved · attempt 1/3
              1 ok      2 ok      3 rej     4 -       5 -
14:17:06  ◆ phase 3 done (attempt 2)
          ====+=========+=========+=========+---------+----   3/5 approved · 14m55s
              1 ok      2 ok      3 ok      4 -       5 -
14:40:00  ■ run complete -- all_approved
          ====+=========+=========+=========+=========+===-   5/5 approved · 37m49s
              1 ok      2 ok      3 ok      4 ok      5 ok
"""


def test_demo_replay_stamps_the_rail_after_rejection_phase_done_and_run_end(
        tmp_path, capsys, monkeypatch):
    lines = _timeline(tmp_path, _demo() + [RUN_END], capsys, monkeypatch)
    rails = [i for i, l in enumerate(lines) if _is_rail(l)]
    assert len(rails) == 6  # three stamps, two lines each
    got = []
    for i in rails[::2]:
        got += lines[i - 1:i + 2]
    want = DEMO_REPLAY.replace(" -- ", " — ").replace("x REJECTED", "✗ REJECTED")
    assert "\n".join(got) + "\n" == want
    assert "APPROVED phase 3" in lines[lines.index(got[3]) - 1]  # no rail after APPROVED


def test_malformed_prints_no_rail_and_the_gate_stays_current(tmp_path, capsys, monkeypatch):
    events = [
        {"event": "run_start", "phases": 3, "states": "A,C,P"},
        {"event": "phase_start", "phase": 2, "total": 3},
        {"event": "verdict", "phase": 2, "result": "MALFORMED", "attempt": 1},
    ]
    lines = _timeline(tmp_path, events, capsys, monkeypatch)
    assert not any(_is_rail(l) for l in lines)
    rt = present.RailTracker()
    for ev in events:
        rt.update(ev)
    assert rt.rail_states() == ["A", "C", "P"] and rt.stamp is None


def test_resume_seeds_from_run_start_states(tmp_path, capsys, monkeypatch):
    seeded = _timeline(tmp_path, _demo(), capsys, monkeypatch)
    first = [l for l in seeded if _is_rail(l)][1]
    assert first.split() == ["1", "ok", "2", "ok", "3", "rej", "4", "-", "5", "-"]


def test_old_event_files_without_states_still_render(tmp_path, capsys, monkeypatch):
    events = _demo()
    del events[0]["states"]
    lines = _timeline(tmp_path, events + [RUN_END], capsys, monkeypatch)
    labels = [l for l in lines if _is_rail(l)][1::2]
    assert labels[0].split() == ["1", "-", "2", "-", "3", "rej", "4", "-", "5", "-"]
    assert labels[-1].split().count("ok") == 5  # run_end: every gate approved


def test_no_rail_line_is_wider_than_the_terminal(tmp_path, capsys, monkeypatch):
    for ascii_ in ("1", ""):
        for cols in range(30, 121):
            lines = _timeline(tmp_path, _demo() + [RUN_END], capsys, monkeypatch, cols=cols,
                              env={"SPECSTRIDE_ASCII": ascii_})
            rails = [l for l in lines if _is_rail(l)]
            assert rails, cols
            for l in rails:
                assert theme.display_width(l) <= cols, (cols, l)


def test_width_tiers(tmp_path, capsys, monkeypatch):
    def rails(cols):
        lines = _timeline(tmp_path, _demo(), capsys, monkeypatch, cols=cols)
        return [l for l in lines if _is_rail(l)]
    assert len(rails(30)) == 2  # one line per stamp: banner.oneline_rail
    for cols in (40, 60, 80, 120):
        got = rails(cols)
        assert len(got) == 4, cols
        assert ("approved" in got[0]) == (cols >= 120), cols  # the tail only when it fits
    # colored, each width still fits
    for cols in (30, 40, 60, 80, 120):
        th = theme.Theme("truecolor", "dark")
        for ln in present.live_rail_lines(["A", "A", "R", "P", "P"], cols, th, False,
                                          tail="2/5 approved · attempt 1/3"):
            assert theme.display_width(ln) <= cols


def test_switches_suppress_the_rail(tmp_path, capsys, monkeypatch):
    for env in ({"SPECSTRIDE_LIVE_RAIL": "off"}, {"SPECSTRIDE_BANNER": "off"}):
        lines = _timeline(tmp_path, _demo() + [RUN_END], capsys, monkeypatch, env=env)
        assert lines and not any(_is_rail(l) for l in lines), env


def test_plain_mode_never_shows_the_rail(tmp_path):
    import subprocess
    import sys
    path = tmp_path / "events.jsonl"
    path.write_text(DEMO.read_text(encoding="utf-8"), encoding="utf-8")
    env = {**os.environ, **PLAIN}
    out = subprocess.run([sys.executable, present.__file__, "--events", str(path),
                          "--mode", "plain"], capture_output=True, text=True, env=env,
                         timeout=30).stdout
    assert out and "+=====" not in out and not any(_is_rail(l) for l in out.splitlines())


def test_halt_after_a_rejection_shows_the_gate_rejected(tmp_path, capsys, monkeypatch):
    base = [{"event": "run_start", "phases": 3, "states": "A,C,P"},
            {"event": "phase_start", "phase": 2, "total": 3}]
    rejected = base + [
        {"event": "verdict", "phase": 2, "result": "REJECTED", "attempt": 3},
        {"event": "run_stop", "reason": "max_rejects", "phase": 2},
    ]
    lines = _timeline(tmp_path, rejected, capsys, monkeypatch)
    labels = [l for l in lines if _is_rail(l)][1::2]
    assert len(labels) == 2 and labels[-1].split() == ["1", "ok", "2", "rej", "3", "-"]
    # halted with no rejection: the gate stays current
    lines = _timeline(tmp_path, base + [{"event": "run_stop", "reason": "stop_flag",
                                         "phase": 2}], capsys, monkeypatch)
    labels = [l for l in lines if _is_rail(l)][1::2]
    assert labels == [labels[0]] and labels[0].split() == ["1", "ok", "2", "now", "3", "-"]


def test_one_halt_reported_twice_stamps_once(tmp_path, capsys, monkeypatch):
    events = [{"event": "run_start", "phases": 2},
              {"event": "phase_start", "phase": 1, "total": 2},
              {"event": "run_stop", "reason": "proposer_cap_exhausted", "iter": 4},
              {"event": "run_stop", "reason": "proposer_cap_exhausted", "phase": 1}]
    lines = _timeline(tmp_path, events, capsys, monkeypatch)
    assert sum(_is_rail(l) for l in lines) == 2


def test_card_rail_is_unchanged_by_the_tracker_refactor():
    st = present.State()
    for ev in _demo():
        st.update(ev)
    present.apply_theme(theme.Theme("16", "dark"))
    try:
        got = st._rail_lines(100)
        want = ["  " + ln for ln in banner.render_rail(["A", "A", "A", "P", "P"], 97,
                                                       present.THEME, False)] + [""]
        assert got == want
        assert st.rail_states() == ["A", "A", "A", "P", "P"]
        assert st.phases_total == 5 and st.cur_phase == 3
    finally:
        present.apply_theme(theme.Theme("16", "dark"))


def test_rail_motion_walks_the_tip_and_lands_on_the_static_rail():
    th = theme.Theme("16", "dark")
    before, after = ["A", "A", "R", "P", "P"], ["A", "A", "A", "P", "P"]
    frames = present._rail_frames(after, before, 100, th, False, "3/5 approved")
    assert 2 <= len(frames) <= 5 and all(len(f) == 2 for f in frames)
    assert frames[-1] == present.live_rail_lines(after, 100, th, False, tail="3/5 approved")
    assert (len(frames) - 1) * banner.FRAME_SECONDS <= 0.3
    assert present._rail_frames(before, before, 100, th, False, "") == []  # a hold: no walk
    assert present._rail_frames(after, before, 30, th, False, "") == []   # one-line tier
