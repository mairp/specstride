#!/usr/bin/env python3
"""Unit tests for prompts/content on trace spans and child-agent trace nesting.

Covers: prompt file -> attempt span input.value + specstride.prompt.* (sha matches
the file), byte caps, secret redaction, span-only content never reaching the event
stream/Loki, W3C traceparent of the open scope, and SPECSTRIDE_OTEL_TRACES=false.

Run:  python3 -m pytest lib/test_otel_prompts.py
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ralph_otel_spans as spans_mod  # noqa: E402
import ralph_otel_ship as otelship    # noqa: E402
import critic  # noqa: E402

LIB = os.path.dirname(os.path.abspath(__file__))
RUN = "20260928-000000-1"
FAKE_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"


def _ev(t, event, **kw):
    return event, dict(kw, run_id=RUN, ts="%d.0" % t)


def _attrs(span):
    return {a["key"]: list(a["value"].values())[0] for a in span["attributes"]}


def _spans(tracker):
    payload = tracker.drain()
    return payload["resourceSpans"][0]["scopeSpans"][0]["spans"] if payload else []


def _prompt_file(tmp, text):
    path = os.path.join(tmp, "proposer-prompt.phase1.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _attempt_span(tmp, prompt_text, **extra_env):
    state = os.path.join(tmp, "state")
    path = _prompt_file(tmp, prompt_text)
    tracker = spans_mod.SpanTracker({}, state_dir=state)
    for event, fields in [
        _ev(100, "run_start", feature="f"),
        _ev(101, "phase_start", phase="1"),
        _ev(102, "proposer_start", phase="1", attempt="1", prompt_path=path),
        _ev(110, "reject", phase="1", attempt="1"),
    ]:
        tracker.on_event(event, fields)
    spans = {s["name"]: s for s in _spans(tracker)}
    return spans["attempt p1#1"], path, state


def test_prompt_on_attempt_span_with_matching_sha():
    with tempfile.TemporaryDirectory() as tmp:
        text = "Implement phase 1.\n" + "x" * 5000
        span, path, state = _attempt_span(tmp, text)
        a = _attrs(span)
        assert a["input.value"] == text             # content keys are exempt from ATTR_MAX
        assert a["input.mime_type"] == "text/plain"
        with open(path, "rb") as fh:
            assert a["specstride.prompt.sha256"] == hashlib.sha256(fh.read()).hexdigest()
        assert a["specstride.prompt.bytes"] == str(len(text.encode()))
        assert a["specstride.prompt.path"] == os.path.abspath(path)
        assert "prompt_path" not in a                # raw locator fields are not duplicated
        # the parked side file is consumed when the scope closes
        assert not [f for f in os.listdir(state) if f.endswith(".content.json")]


def test_prompt_read_at_open_not_at_close():
    """The next attempt rewrites proposer-prompt.phase<N>.txt before the old scope
    closes (superseded), so the text must be the one present at open time."""
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "state")
        path = _prompt_file(tmp, "first prompt")
        tracker = spans_mod.SpanTracker({}, state_dir=state)
        tracker.on_event(*_ev(100, "run_start"))
        tracker.on_event(*_ev(101, "proposer_start", phase="1", attempt="1", prompt_path=path))
        _prompt_file(tmp, "second prompt")
        tracker.on_event(*_ev(102, "proposer_start", phase="1", attempt="2", prompt_path=path))
        tracker.on_event(*_ev(103, "reject", phase="1", attempt="2"))
        spans = {s["name"]: _attrs(s) for s in _spans(tracker)}
        assert spans["attempt p1#1"]["input.value"] == "first prompt"
        assert spans["attempt p1#2"]["input.value"] == "second prompt"


def test_prompt_truncated_at_cap_but_sha_is_of_whole_file(monkeypatch):
    monkeypatch.setenv("SPECSTRIDE_OTEL_PROMPT_MAX", "1024")
    with tempfile.TemporaryDirectory() as tmp:
        text = "é" * 4000                           # multi-byte: cut on a char boundary
        span, path, _ = _attempt_span(tmp, text)
        a = _attrs(span)
        assert len(a["input.value"].encode()) <= 1024
        assert a["input.value_truncated"] is True
        assert a["specstride.prompt.bytes"] == str(len(text.encode()))
        assert a["specstride.prompt.sha256"] == hashlib.sha256(text.encode()).hexdigest()


def test_secret_in_prompt_is_redacted():
    with tempfile.TemporaryDirectory() as tmp:
        span, _, _ = _attempt_span(tmp, "use key %s please" % FAKE_KEY)
        value = _attrs(span)["input.value"]
        assert FAKE_KEY not in value
        assert "[REDACTED]" in value


def test_ordinary_attrs_keep_attr_max():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = spans_mod.SpanTracker({}, state_dir=tmp)
        tracker.on_event(*_ev(100, "run_start"))
        tracker.on_event(*_ev(101, "note", detail="y" * 2000))
        note = [s for s in _spans(tracker) if s["name"] == "note"][0]
        assert len(_attrs(note)["detail"]) == spans_mod.ATTR_MAX


def test_span_content_reaches_span_not_log_record():
    """span_content (critic reply, full agent text) is on the span only: the OTLP
    log record (-> Loki) and the event fields never carry it."""
    with tempfile.TemporaryDirectory() as tmp:
        otel = otelship.Otel("http://127.0.0.1:9", {"service.name": "ralph"})
        otel._spans = spans_mod.SpanTracker({}, state_dir=tmp)
        content = spans_mod.content_attrs("output.value", "VERDICT: REJECTED because " + "z" * 3000)
        otel.add("run_start", "", fields={"run_id": RUN, "ts": "100.0"})
        otel.add("critic_start", "", fields={"run_id": RUN, "ts": "101.0", "phase": "1"})
        otel.add("verdict", "result=REJECTED",
                 fields={"run_id": RUN, "ts": "102.0", "result": "REJECTED"},
                 span_content=content)
        logs = [rec for _, _, rec, _ in otel._logs]
        assert all("output.value" not in rec for rec in logs)
        critic_span = [s for s in otel._spans.drain()["resourceSpans"][0]["scopeSpans"][0]["spans"]
                       if s["name"].startswith("critic")][0]
        assert _attrs(critic_span)["output.value"].startswith("VERDICT: REJECTED")


def test_agent_text_trace_cap_is_separate_from_event_cap(monkeypatch):
    import agent_stream
    from observability_policy import ObservabilityPolicy
    monkeypatch.setenv("SPECSTRIDE_OTEL_TEXT_MAX", "2048")
    adapter = agent_stream.ClaudeAdapter(ObservabilityPolicy())
    long_text = "word " * 2000
    outcome = adapter.consume({"type": "assistant", "message": {
        "content": [{"type": "text", "text": long_text}]}})
    event, fields = outcome.events[0]
    assert event == "agent_text"
    assert len(fields["text"].encode()) <= agent_stream.TEXT_MAX   # events/Loki: 160 B
    span_text = outcome.span_content[0]["output.value"]
    assert agent_stream.TEXT_MAX < len(span_text.encode()) <= 2048


def test_traceparent_format_and_scope():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = spans_mod.SpanTracker({}, state_dir=tmp)
        tracker.on_event(*_ev(100, "run_start"))
        _, attempt_sid = tracker.on_event(*_ev(101, "proposer_start", phase="1", attempt="1"))
        tid, iter_sid = tracker.on_event(*_ev(102, "iter_start", iter="1"))
        tp = spans_mod.traceparent(RUN, ["iter", "attempt"], state_dir=tmp)
        assert re.match(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-01$", tp)
        assert tp == "00-%s-%s-01" % (tid, iter_sid)
        assert spans_mod.traceparent(RUN, ["attempt"], state_dir=tmp) == "00-%s-%s-01" % (tid, attempt_sid)
        assert spans_mod.traceparent(RUN, ["critic"], state_dir=tmp) == ""
        assert spans_mod.traceparent("no-such-run", None, state_dir=tmp) == ""
        # the span the child nests under is the one the scope later closes with
        tracker.on_event(*_ev(110, "iter_done", iter="1"))
        closed = [s for s in _spans(tracker) if s["name"] == "iter 1"][0]
        assert closed["spanId"] == iter_sid and closed["traceId"] == tid


def test_traceparent_cli(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tracker = spans_mod.SpanTracker({}, state_dir=tmp)
        tracker.on_event(*_ev(100, "run_start"))
        tid, sid = tracker.on_event(*_ev(101, "critic_start", phase="1", attempt="1"))
        env = dict(os.environ, SPECSTRIDE_OTEL_STATE_DIR=tmp)
        out = subprocess.run([sys.executable, os.path.join(LIB, "ralph_otel_spans.py"),
                              "traceparent", "--run-id", RUN, "--scope", "critic"],
                             capture_output=True, text=True, env=env)
        assert out.returncode == 0
        assert out.stdout.strip() == "00-%s-%s-01" % (tid, sid)


def test_critic_child_env(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("SPECSTRIDE_OTEL_STATE_DIR", tmp)
        monkeypatch.setenv("SPECSTRIDE_OTEL_ENABLED", "true")
        monkeypatch.setenv("SPECSTRIDE_RUN_ID", RUN)
        tracker = spans_mod.SpanTracker({}, state_dir=tmp)
        tracker.on_event(*_ev(100, "run_start"))
        tid, sid = tracker.on_event(*_ev(101, "critic_start", phase="1", attempt="1"))
        env = critic._child_otel_env("dsh")
        assert env["TRACEPARENT"] == "00-%s-%s-01" % (tid, sid)
        assert "openinference.project.name=specstride" in env["OTEL_RESOURCE_ATTRIBUTES"]
        assert "service.name=dsh" in env["OTEL_RESOURCE_ATTRIBUTES"]
        monkeypatch.setenv("SPECSTRIDE_OTEL_ENABLED", "false")
        assert critic._child_otel_env("dsh") == {}


def test_traces_false_disables_everything(monkeypatch):
    monkeypatch.setenv("SPECSTRIDE_OTEL_TRACES", "false")
    with tempfile.TemporaryDirectory() as tmp:
        path = _prompt_file(tmp, "prompt")
        tracker = spans_mod.SpanTracker({}, state_dir=tmp)
        assert tracker.on_event(*_ev(100, "proposer_start", prompt_path=path)) is None
        assert tracker.drain() is None
        assert spans_mod.traceparent(RUN, None, state_dir=tmp) == ""
        monkeypatch.setenv("SPECSTRIDE_OTEL_STATE_DIR", tmp)
        monkeypatch.setenv("SPECSTRIDE_OTEL_ENABLED", "true")
        monkeypatch.setenv("SPECSTRIDE_RUN_ID", RUN)
        assert critic._child_otel_env("dsh") == {}
        assert critic._write_prompt_file(tmp, 1, "p") == {}
        assert not os.path.exists(os.path.join(tmp, "critic-prompt.phase1.txt"))


def test_critic_prompt_file_fields(monkeypatch):
    monkeypatch.setenv("SPECSTRIDE_OTEL_ENABLED", "true")
    with tempfile.TemporaryDirectory() as tmp:
        fields = critic._write_prompt_file(tmp, 3, "critic prompt ✓")
        raw = "critic prompt ✓".encode()
        assert fields["prompt_path"] == os.path.join(tmp, "critic-prompt.phase3.txt")
        assert fields["prompt_sha256"] == hashlib.sha256(raw).hexdigest()
        assert fields["prompt_bytes"] == len(raw)


def test_prompt_fields_shell_helper():
    with tempfile.TemporaryDirectory() as tmp:
        path = _prompt_file(tmp, "abc")
        out = subprocess.run(
            ["bash", "-c", 'source "$1/specstride-lib.sh" >/dev/null 2>&1; '
             'specstride_prompt_fields "$2"; printf "%s\\n" "${SPECSTRIDE_PROMPT_KV[@]}"',
             "_", os.path.dirname(LIB), path], capture_output=True, text=True)
        assert out.stdout.split("\n")[:6] == [
            "prompt_path", path, "prompt_sha256", hashlib.sha256(b"abc").hexdigest(),
            "prompt_bytes", "3"]
