#!/usr/bin/env python3
"""Unit tests for ralph_otel_spans.py — events -> OTLP trace spans (stdlib only).

Run:  python3 -m pytest lib/test_ralph_otel_spans.py
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ralph_otel_spans as spans_mod  # noqa: E402
import ralph_otel_ship as otelship    # noqa: E402
from _test_http import CaptureServer  # noqa: E402

RUN = "20260921-000000-1"


def _ev(t, event, **kw):
    return event, dict(kw, run_id=RUN, ts="%d.0" % t)


LIFECYCLE = [
    _ev(100, "run_start", feature="004-x", phases="2"),
    _ev(101, "phase_start", phase="1", title="Setup"),
    _ev(102, "proposer_start", phase="1", attempt="1"),
    _ev(103, "iter_start", iter="1"),
    _ev(104, "agent_tool", tool="Bash"),
    _ev(106, "agent_tool", tool="Edit"),
    _ev(107, "agent_text", text="done"),
    _ev(110, "iter_done", iter="1"),
    _ev(111, "verification_start", phase="1", attempt="1"),
    _ev(115, "verification_failed", phase="1", attempt="1", rc="1"),
    _ev(115, "critic_start", phase="1", attempt="1"),
    _ev(116, "verdict", phase="1", attempt="1", result="REJECTED"),
    _ev(116, "reject", phase="1", attempt="1"),
    _ev(117, "proposer_start", phase="1", attempt="2"),
    _ev(118, "iter_start", iter="1"),
    _ev(120, "run_stop", reason="proposer_max_iter", iter="1"),   # proposer-level: point span
    _ev(121, "phase_done", phase="1", attempt="2"),               # closes iter+attempt+phase
    _ev(130, "run_end", outcome="all_approved"),
]


def _replay(events, tmp):
    tracker = spans_mod.SpanTracker({"task": "t"}, state_dir=tmp)
    for event, fields in events:
        tracker.on_event(event, fields)
    return tracker.drain()["resourceSpans"][0]["scopeSpans"][0]["spans"]


def _by_name(spans):
    return {s["name"]: s for s in spans}


def test_hierarchy_and_timing():
    with tempfile.TemporaryDirectory() as tmp:
        spans = _replay(LIFECYCLE, tmp)
    names = _by_name(spans)
    assert len({s["traceId"] for s in spans}) == 1
    assert spans[0]["traceId"] == spans_mod.trace_id_for(RUN)
    run, phase = names["run 004-x"], names["phase 1: Setup"]
    a1, a2 = names["attempt p1#1"], names["attempt p1#2"]
    assert "parentSpanId" not in run
    assert phase["parentSpanId"] == run["spanId"]
    assert a1["parentSpanId"] == a2["parentSpanId"] == phase["spanId"]
    assert run["startTimeUnixNano"] == str(100 * 10**9)
    assert run["endTimeUnixNano"] == str(130 * 10**9)
    assert a1["endTimeUnixNano"] == str(116 * 10**9)
    assert a1["status"]["code"] == spans_mod.STATUS_ERROR          # rejected
    assert "status" not in a2                                     # closed by phase_done
    iters = [s for s in spans if s["name"] == "iter 1"]
    assert [i["parentSpanId"] for i in iters] == [a1["spanId"], a2["spanId"]]
    bash = names["tool Bash"]
    assert bash["parentSpanId"] == iters[0]["spanId"]
    assert (bash["startTimeUnixNano"], bash["endTimeUnixNano"]) == (str(104 * 10**9), str(106 * 10**9))
    ver = names["verification p1"]
    assert ver["parentSpanId"] == a1["spanId"] and ver["status"]["code"] == spans_mod.STATUS_ERROR
    critic = names["critic p1#1"]
    assert critic["parentSpanId"] == a1["spanId"] and critic["status"]["message"] == "REJECTED"
    stop = names["run_stop"]                                       # proposer stop is a point
    assert stop["parentSpanId"] == iters[1]["spanId"]
    assert len(spans) == len({s["spanId"] for s in spans})


def test_state_is_shared_across_processes():
    """Orchestrator, proposer and tap are separate processes: a fresh tracker must
    parent under scopes opened by another one."""
    with tempfile.TemporaryDirectory() as tmp:
        spans_mod.SpanTracker({}, state_dir=tmp).on_event(*_ev(1, "run_start", feature="f"))
        spans_mod.SpanTracker({}, state_dir=tmp).on_event(*_ev(2, "iter_start", iter="3"))
        tap = spans_mod.SpanTracker({}, state_dir=tmp)
        tap.on_event(*_ev(3, "agent_tool", tool="Read"))
        tool = tap.drain()["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        closer = spans_mod.SpanTracker({}, state_dir=tmp)
        closer.on_event(*_ev(4, "run_end"))
        closed = _by_name(closer.drain()["resourceSpans"][0]["scopeSpans"][0]["spans"])
    assert tool["parentSpanId"] == closed["iter 3"]["spanId"]
    assert closed["iter 3"]["parentSpanId"] == closed["run f"]["spanId"]


def test_trace_id_override_and_disable(monkeypatch):
    monkeypatch.setenv("SPECSTRIDE_TRACE_ID", "ab" * 16)
    assert spans_mod.trace_id_for(RUN) == "ab" * 16
    monkeypatch.setenv("SPECSTRIDE_TRACE_ID", "not-hex")
    assert spans_mod.trace_id_for(RUN) == spans_mod.trace_id_for(RUN, {})
    monkeypatch.setenv("SPECSTRIDE_OTEL_TRACES", "false")
    with tempfile.TemporaryDirectory() as tmp:
        t = spans_mod.SpanTracker({}, state_dir=tmp)
        assert t.on_event(*_ev(1, "run_start")) is None
        assert t.drain() is None


def test_no_run_id_no_spans():
    with tempfile.TemporaryDirectory() as tmp:
        t = spans_mod.SpanTracker({}, state_dir=tmp)
        assert t.on_event("agent_tool", {"tool": "Bash"}) is None
        assert t.drain() is None


def test_shipper_posts_traces_and_correlates_logs(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp, CaptureServer() as srv:
        monkeypatch.setenv("SPECSTRIDE_OTEL_STATE_DIR", tmp)
        o = otelship.Otel(srv.url, {"service.name": "ralph", "task": "t"})
        for event, fields in LIFECYCLE[:3]:
            o.add(event, "x", fields=fields)
        o.add(*LIFECYCLE[-1][:1], line="x", fields=LIFECYCLE[-1][1])
        rec = o.flush()
        posts = {r.path: r.json for r in srv.requests}
    assert rec["status"] == "accepted"
    trace = posts["/v1/traces"]["resourceSpans"][0]
    res = {a["key"]: a["value"]["stringValue"] for a in trace["resource"]["attributes"]}
    assert res == {"service.name": "specstride", "task": "t"}
    names = {s["name"] for s in trace["scopeSpans"][0]["spans"]}
    assert {"run 004-x", "phase 1: Setup", "attempt p1#1"} <= names
    records = posts["/v1/logs"]["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    assert all(r["traceId"] == spans_mod.trace_id_for(RUN) for r in records)


def test_traces_failure_does_not_fail_delivery(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SPECSTRIDE_OTEL_STATE_DIR", tmp)
        o = otelship.Otel("http://127.0.0.1:9", {"service.name": "ralph"})
        calls = []

        def fake_post(url, payload):
            calls.append(url)
            return (404, "http_404") if url.endswith("/v1/traces") else (200, None)
        monkeypatch.setattr(o, "_post", fake_post)
        o.add("run_start", "x", fields={"run_id": RUN, "ts": "1"})
        o.add("run_end", "x", fields={"run_id": RUN, "ts": "2"})
        rec = o.flush()
    assert rec["status"] == "accepted"
    assert any(c.endswith("/v1/traces") for c in calls)


def test_critic_events_ship_with_run_correlation(monkeypatch):
    import critic
    with tempfile.TemporaryDirectory() as tmp, CaptureServer() as srv:
        monkeypatch.setenv("SPECSTRIDE_OTEL_STATE_DIR", tmp)
        monkeypatch.setenv("SPECSTRIDE_OTEL_ENABLED", "true")
        monkeypatch.setenv("SPECSTRIDE_OTEL_URL", srv.url)
        monkeypatch.setenv("SPECSTRIDE_RUN_ID", RUN)
        monkeypatch.setenv("SPECSTRIDE_FEATURE", "004-x")
        monkeypatch.delenv("SPECSTRIDE_TELEMETRY", raising=False)
        events = os.path.join(tmp, "events.jsonl")
        critic.emit(events, "critic_start", phase=1, attempt=1)
        critic.emit(events, "verdict", phase=1, attempt=1, result="APPROVED")
        local = [json.loads(l) for l in open(events)]
        posts = [r for r in srv.requests if r.path == "/v1/traces"]
    assert "run_id" not in local[0]                  # local JSONL format unchanged
    spans = posts[-1].json["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans[0]["name"] == "critic p1#1" and "status" not in spans[0]
    assert spans[0]["traceId"] == spans_mod.trace_id_for(RUN)
