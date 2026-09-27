"""Registry and dispatch guards for the stream seam (FR-018, SC-002)."""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_stream
from agent_stream import FORMATS, ClaudeAdapter, PrimeAdapter, select_provider_adapter, observability_start
from observability_policy import ObservabilityPolicy
from stream_seam import (
    CANONICAL_TOOLS,
    Capability,
    StreamFormat,
    _nested_target,
    build_registry,
    tool_target,
)


def _row(name, **kwargs):
    kwargs.setdefault("capability", Capability("%s selected" % name))
    return StreamFormat(name, ClaudeAdapter, **kwargs)


def _dup_rows(first, second_name, alias=()):
    return (_row(first), _row(second_name, aliases=alias))


def test_duplicate_format_registration_fails():
    # name/name
    with pytest.raises(ValueError, match="duplicate stream format: dup"):
        build_registry(_dup_rows("dup", "dup"))
    # alias/alias
    with pytest.raises(ValueError, match="duplicate stream format: twin"):
        build_registry((
            _row("alpha", aliases=("twin",)),
            _row("beta", aliases=("twin",)),
        ))
    # alias/name
    with pytest.raises(ValueError, match="duplicate stream format: twin"):
        build_registry((
            _row("alpha", aliases=("twin",)),
            _row("twin"),
        ))


def test_shipped_table_builds():
    assert FORMATS.names() == ("claude", "claude-stream-json", "prime-v3")
    assert FORMATS.canonical() == ("claude", "prime-v3")


def test_rejects_invalid_name():
    with pytest.raises(ValueError, match="Bad_Name"):
        build_registry((_row("Bad_Name"),))


def test_rejects_invalid_alias():
    with pytest.raises(ValueError, match="9alias"):
        build_registry((_row("ok", aliases=("9alias",)),))


def test_rejects_empty_reason():
    with pytest.raises(ValueError, match="empty-reason"):
        build_registry((_row("empty-reason", capability=Capability("")),))


def test_rejects_out_of_order_signals():
    with pytest.raises(ValueError, match="bad-signals"):
        build_registry((_row(
            "bad-signals", capability=Capability("r", signals="text,init")),))


def test_rejects_unknown_signal():
    with pytest.raises(ValueError, match="unknown-signal"):
        build_registry((_row(
            "unknown-signal", capability=Capability("r", signals="init,bogus")),))


def test_rejects_empty_signals():
    with pytest.raises(ValueError, match="no-signals"):
        build_registry((_row(
            "no-signals", capability=Capability("r", signals="")),))


def test_rejects_synth_init_with_init_record():
    with pytest.raises(ValueError, match="synth"):
        build_registry((_row("synth", synth_init=True, has_init_record=True),))


def test_rejects_noncanonical_tool_names():
    class BadAdapter:
        TOOL_NAMES = {"write_file": "NotCanonical"}

    with pytest.raises(ValueError, match="NotCanonical"):
        build_registry((StreamFormat(
            "bad-tools", BadAdapter, capability=Capability("bad tools selected")),))


def test_tool_names_are_canonical():
    for row in FORMATS:
        for value in getattr(row.adapter, "TOOL_NAMES", {}).values():
            assert value in CANONICAL_TOOLS

    class BadAdapter:
        TOOL_NAMES = {"run": "Shell"}

    assert "Shell" not in CANONICAL_TOOLS


def test_select_returns_bare_adapters():
    policy = ObservabilityPolicy()
    assert isinstance(select_provider_adapter("claude", policy), ClaudeAdapter)
    assert isinstance(
        select_provider_adapter("claude-stream-json", policy), ClaudeAdapter)
    assert isinstance(select_provider_adapter("prime-v3", policy), PrimeAdapter)
    with pytest.raises(ValueError, match="unsupported provider format"):
        select_provider_adapter("future-format", policy)


def test_observability_start_tuples():
    assert observability_start("claude") == (
        "structured", "Claude stream-json schema selected",
        "init,text,tool,evidence,result")
    assert observability_start("prime-v3") == (
        "structured", "Prime JSON schema v3 selected",
        "init,text,tool,evidence,result")
    assert observability_start("future-format") == (
        "raw-text", "structured schema unavailable — parsing plain output",
        "text,result")


def _string_constants(node):
    strings = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            strings.add(child.value)
    return strings


def _function_strings(source, names):
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = _string_constants(node)
    return found


def test_no_format_names_in_dispatch():
    with open(agent_stream.__file__, encoding="utf-8") as handle:
        source = handle.read()
    functions = _function_strings(
        source, {"select_provider_adapter", "observability_start", "main"})
    for name, strings in functions.items():
        literals = strings & set(FORMATS.names())
        if name == "main":
            # The single --provider-format argparse default is allowed.
            assert literals == {"claude"}
        else:
            assert literals == set(), "%s contains format literals: %s" % (
                name, literals)


def test_no_format_names_in_dispatch_self_test():
    synthetic = '''
def select_provider_adapter(provider_format, policy):
    if provider_format == "claude":
        return object()
    return None
'''
    found = _function_strings(synthetic, {"select_provider_adapter"})
    assert found["select_provider_adapter"] & set(FORMATS.names()) == {"claude"}


# ── Story 2: evidence detection fires for a harness's own tool names/paths ──

import json
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures"))
from stream_test_adapter import StreamTestAdapter  # noqa: E402

_STREAM_FIXTURES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "stream-test-v1")


def test_target_camel_keys_found_by_tool_target_and_policy():
    assert tool_target("Write", {"filePath": "notes/x.txt"}) == "notes/x.txt"
    assert tool_target(
        "NotebookEdit", {"notebookPath": "n/e.ipynb"}) == "n/e.ipynb"
    policy = ObservabilityPolicy()
    assert policy.extract_target_paths({"filePath": "notes/x.txt"}) == [
        "notes/x.txt"]
    assert policy.extract_target_paths({"notebookPath": "n/e.ipynb"}) == [
        "n/e.ipynb"]


def test_target_exact_key_beats_converted():
    assert tool_target("Write", {"file_path": "a", "filePath": "b"}) == "a"


def test_target_nested_found():
    assert tool_target(
        "Write", {"call": {"args": {"path": "hello.txt"}}}) == "hello.txt"


def test_target_top_level_beats_nested():
    assert tool_target("Write", {
        "path": "top.txt",
        "call": {"args": {"path": "deep.txt"}},
    }) == "top.txt"


def test_nested_target_is_bounded():
    deep = {"path": "v"}
    for _ in range(5000):
        deep = {"a": deep}
    wide = {"k": {("key%05d" % i): "v" for i in range(100_000)}}
    start = time.monotonic()
    wide_target, wide_visited = _nested_target(wide)
    deep_target, deep_visited = _nested_target(deep)
    elapsed = time.monotonic() - start
    assert wide_target == "" and wide_visited == 256
    assert deep_target == "" and deep_visited <= 256

    at_six = {"path": "v"}
    for _ in range(6):
        at_six = {"a": at_six}
    assert _nested_target(at_six) == ("v", 7)
    at_seven = {"a": at_six}
    # The depth-7 dict is never dequeued: visited stops at the 7 counted nodes.
    assert _nested_target(at_seven) == ("", 7)
    assert elapsed < 1.0


def test_target_camel_secret_redacted_like_snake():
    policy = ObservabilityPolicy()
    secret = "Bearer abcdefgh1234567890"
    camel = policy.summarize_targets({"filePath": secret})
    snake = policy.summarize_targets({"file_path": secret})
    assert camel["targets"] == snake["targets"]
    assert camel["redacted"] is True and camel["redacted"] == snake["redacted"]
    assert camel["truncated"] == snake["truncated"]


def _run_test_stream(name, expected_evidence):
    with open(os.path.join(_STREAM_FIXTURES, name), encoding="utf-8") as handle:
        records = [
            json.loads(line)
            for line in handle.read().replace("{EVIDENCE}", expected_evidence).splitlines()
            if line.strip()
        ]
    policy = ObservabilityPolicy()
    row = StreamFormat(
        "specstride-test-v1", StreamTestAdapter,
        capability=Capability("specstride test stream selected"))
    adapter = select_provider_adapter(
        "specstride-test-v1", policy, registry=build_registry([row]),
        expected_evidence=expected_evidence)
    outcomes = [adapter.consume(record) for record in records]
    events = [event for outcome in outcomes for event in outcome.events]
    return outcomes, events


def test_target_camel_stream_emits_evidence_writing(tmp_path):
    outcomes, events = _run_test_stream(
        "camel.jsonl", str(tmp_path / "gate.md"))
    tools = [fields for name, fields in events if name == "agent_tool"]
    evidence = [fields for name, fields in events if name == "evidence_writing"]
    assert len(tools) == 1 and tools[0]["tool"] == "Write"
    assert len(evidence) == 1
    assert evidence[0]["tool"] == "Write"
    assert evidence[0]["match"] == "exact-expected-target"


def test_target_nested_stream_emits_evidence_writing(tmp_path):
    outcomes, events = _run_test_stream(
        "nested.jsonl", str(tmp_path / "gate.md"))
    tools = [fields for name, fields in events if name == "agent_tool"]
    evidence = [fields for name, fields in events if name == "evidence_writing"]
    assert len(tools) == 1 and tools[0]["tool"] == "Write"
    assert len(evidence) == 1 and evidence[0]["tool"] == "Write"


def test_target_unmapped_tool_stream_has_no_evidence(tmp_path):
    outcomes, events = _run_test_stream(
        "unmapped-tool.jsonl", str(tmp_path / "gate.md"))
    tools = [fields for name, fields in events if name == "agent_tool"]
    assert len(tools) == 1 and tools[0]["tool"] == "apply_patch"
    assert not [name for name, _ in events if name == "evidence_writing"]


def test_target_run_shell_lexical_match_only(tmp_path):
    outcomes, events = _run_test_stream(
        "run-shell.jsonl", str(tmp_path / "gate.md"))
    tools = [fields for name, fields in events if name == "agent_tool"]
    assert len(tools) == 1 and tools[0]["tool"] == "Bash"
    assert "gate.md" in tools[0]["target"]
    evidence = [fields for name, fields in events if name == "evidence_writing"]
    assert len(evidence) == 1  # lexical match only; nothing was executed


def test_target_multi_write_emits_evidence_once(tmp_path):
    outcomes, events = _run_test_stream(
        "multi-write.jsonl", str(tmp_path / "gate.md"))
    evidence = [fields for name, fields in events if name == "evidence_writing"]
    assert len(evidence) == 1


# ── Story 3: opt-in shared behaviours (synthesised init/terminal, no_activity) ──

from stream_seam import SharedBehaviour  # noqa: E402

_HEADER_ROW_KWARGS = dict(
    has_init_record=False, synth_init=True, synth_terminal=True, no_activity=True,
)


def _test_row(**kwargs):
    kwargs.setdefault("capability", Capability("specstride test stream selected"))
    return StreamFormat("specstride-test-v1", StreamTestAdapter, **kwargs)


def _shared_wrapper(registry_row=None, invocation_model=None, expected_evidence=None):
    row = registry_row if registry_row is not None else _test_row(**_HEADER_ROW_KWARGS)
    return select_provider_adapter(
        "specstride-test-v1", ObservabilityPolicy(), registry=build_registry([row]),
        invocation_model=invocation_model, expected_evidence=expected_evidence,
    )


def _feed(wrapper, name):
    """Feed one fixture stream through the wrapper, then finish() once."""
    with open(os.path.join(_STREAM_FIXTURES, name), encoding="utf-8") as handle:
        raw = handle.read().replace("{EVIDENCE}", "/tmp/e2e/placeholder-gate.md")
    outcomes = [wrapper.consume(json.loads(line))
                for line in raw.splitlines() if line.strip()]
    outcomes.append(wrapper.finish())
    events = [event for outcome in outcomes for event in outcome.events]
    terminals = [outcome.terminal for outcome in outcomes if outcome.terminal]
    output = [line for outcome in outcomes for line in outcome.output]
    return outcomes, events, terminals, output


def test_shared_init_emitted_once_and_first(tmp_path):
    outcomes, events, terminals, _ = _feed(
        _shared_wrapper(), "no-init-no-terminal.jsonl")
    names = [name for name, _ in events]
    assert names.count("agent_init") == 1
    assert names[0] == "agent_init"


def test_shared_init_model_from_invocation_or_none():
    # research R5 table cases as the proposer would pass them: "", "M", "Q".
    for passed, expected in (("", None), ("M", "M"), ("Q", "Q"), (None, None)):
        outcomes, events, terminals, _ = _feed(
            _shared_wrapper(invocation_model=passed), "idle-success.jsonl")
        inits = [fields for name, fields in events if name == "agent_init"]
        assert len(inits) == 1 and inits[0]["model"] == expected
        assert inits[0]["tools"] is None


def test_shared_missing_terminal_model_is_invocation_model():
    # The synthesised missing_terminal carries the header-fact model.
    _, _, terminals, _ = _feed(_shared_wrapper(invocation_model="Q"), "empty.jsonl")
    assert terminals[0]["reason_code"] == "missing_terminal"
    assert terminals[0]["model"] == "Q"
    _, _, terminals, _ = _feed(_shared_wrapper(), "empty.jsonl")
    assert terminals[0]["model"] is None


def test_shared_empty_stream_yields_only_missing_terminal():
    outcomes, events, terminals, output = _feed(_shared_wrapper(), "empty.jsonl")
    names = [name for name, _ in events]
    assert names == []
    assert len(terminals) == 1
    assert terminals[0]["reason_code"] == "missing_terminal"
    assert any("missing_terminal" in line for line in output)
    assert not [fields for _, fields in events if False] and events == []


def test_shared_no_init_no_terminal_fixture():
    outcomes, events, terminals, _ = _feed(
        _shared_wrapper(), "no-init-no-terminal.jsonl")
    names = [name for name, _ in events]
    assert names.count("agent_init") == 1
    assert len(terminals) == 1
    assert terminals[0]["reason_code"] == "missing_terminal"


def test_shared_delivered_terminal_not_resynthesised():
    outcomes, events, terminals, _ = _feed(
        _shared_wrapper(), "idle-success.jsonl")
    assert len(terminals) == 1
    assert terminals[0]["reason_code"] == "success"


def test_shared_duplicate_terminal_dropped_with_diagnostic():
    outcomes, events, terminals, _ = _feed(
        _shared_wrapper(), "duplicate-terminal.jsonl")
    assert len(terminals) == 1
    duplicates = [fields for name, fields in events
                  if name == "agent_diagnostic"
                  and fields.get("code") == "duplicate_terminal"]
    assert len(duplicates) == 1 and duplicates[0]["severity"] == "warning"


def test_shared_cost_usd_is_never_zero():
    outcomes, events, terminals, _ = _feed(
        _shared_wrapper(), "idle-success.jsonl")
    assert len(terminals) == 1
    assert terminals[0]["cost_usd"] is None


def test_shared_idle_success_stamps_no_activity():
    outcomes, events, terminals, _ = _feed(
        _shared_wrapper(), "idle-success.jsonl")
    assert terminals[0]["no_activity"] is True
    assert terminals[0]["status"] == "success"
    assert terminals[0]["reason_code"] == "success"


def test_shared_camel_tool_means_activity():
    outcomes, events, terminals, _ = _feed(
        _shared_wrapper(), "camel.jsonl")
    assert terminals[0]["no_activity"] is False


def test_shared_no_activity_opt_off_leaves_key_absent():
    row = _test_row(has_init_record=False, synth_init=True, synth_terminal=True,
                    no_activity=False)
    outcomes, events, terminals, _ = _feed(_shared_wrapper(row), "idle-success.jsonl")
    assert "no_activity" not in terminals[0]


def test_shared_error_terminal_leaves_no_activity_absent():
    with open(os.path.join(_STREAM_FIXTURES, "idle-success.jsonl"),
              encoding="utf-8") as handle:
        raw = handle.read().replace('"success"', '"error"').replace(
            "{EVIDENCE}", "/tmp/e2e/placeholder-gate.md")
    wrapper = _shared_wrapper()
    terminal = None
    for line in raw.splitlines():
        if line.strip():
            outcome = wrapper.consume(json.loads(line))
            terminal = terminal or outcome.terminal
    assert terminal is not None and terminal["status"] == "error"
    assert "no_activity" not in terminal
    # The error terminal was delivered, so finish() synthesises nothing extra.
    outcome = wrapper.finish()
    assert outcome.terminal is None
    assert wrapper.terminal_seen is True


def test_shared_finish_twice_same_single_terminal():
    # Empty stream: the first finish() synthesises the missing_terminal, the
    # second returns the same single terminal by adding nothing.
    wrapper = _shared_wrapper()
    first = wrapper.finish()
    second = wrapper.finish()
    assert first.terminal is not None
    assert first.terminal["reason_code"] == "missing_terminal"
    assert second.terminal is None
    assert second.events == [] and second.output == []


def test_shared_terminal_seen_reflects_terminal():
    wrapper = _shared_wrapper()
    assert wrapper.terminal_seen is False
    _feed(wrapper, "idle-success.jsonl")
    assert wrapper.terminal_seen is True
    empty_wrapper = _shared_wrapper()
    _feed(empty_wrapper, "empty.jsonl")
    assert empty_wrapper.terminal_seen is True  # the synthesised terminal


def test_shared_consume_raw_absent_for_test_adapter():
    wrapper = _shared_wrapper()
    assert isinstance(wrapper, SharedBehaviour)
    assert getattr(wrapper, "consume_raw", None) is None


def test_shared_track_terminal_only():
    row = _test_row(track_terminal=True)
    wrapper = _shared_wrapper(row)
    assert isinstance(wrapper, SharedBehaviour)
    # A delivered terminal sets terminal_seen and is stamped with nothing.
    outcomes, events, terminals, _ = _feed(wrapper, "idle-success.jsonl")
    assert wrapper.terminal_seen is True
    assert "no_activity" not in terminals[0]
    assert not [name for name, _ in events if name == "agent_init"]
    # EOF without a terminal: no synthesis at all.
    quiet = _shared_wrapper(row)
    quiet.consume({"type": "text", "text": "still working"})
    finish_outcome = quiet.finish()
    assert finish_outcome.terminal is None
    assert quiet.terminal_seen is False
    assert not [name for name, _ in finish_outcome.events if name == "agent_init"]


def test_shared_text_is_sanitised():
    secret_text = "token Bearer abcdefgh1234567890 in the log"
    shared = _shared_wrapper()
    shared_outcome = shared.consume({"type": "text", "text": secret_text})
    claude = select_provider_adapter("claude", ObservabilityPolicy())
    claude_outcome = claude.consume({"type": "assistant", "message": {
        "model": "m", "content": [{"type": "text", "text": secret_text}]}})
    shared_text = [fields for name, fields in shared_outcome.events
                   if name == "agent_text"]
    claude_text = [fields for name, fields in claude_outcome.events
                   if name == "agent_text"]
    assert len(shared_text) == 1 and len(claude_text) == 1
    assert shared_text[0] == claude_text[0]


# ── T025: tap hardening — every adapter fault degrades instead of dying ──────

import io  # noqa: E402

_CONTEXT_ARGS = ("--run-id", "run-fault", "--feature", "fault", "--role",
                 "proposer", "--backend", "faulty", "--phase", "1",
                 "--attempt", "1", "--iteration", "1",
                 "--expected-evidence", "/tmp/fault-gate.md")


def _registry_with_test_row():
    return build_registry(tuple(agent_stream.STREAM_FORMATS) + (_test_row(**_HEADER_ROW_KWARGS),))


def _run_main(monkeypatch, tmp_path, stdin_text, extra_argv=(), formats=None):
    """Call agent_stream.main() in-process with patched argv/stdin/stdout.

    Returning normally is the tap's exit 0 (the outer handler only swallows
    unexpected exceptions). Returns (events_path, sidecar_path, stdout)."""
    events = tmp_path / "events.jsonl"
    sidecar = tmp_path / "provider-terminal.json"
    argv = ["agent_stream.py", "--events", str(events),
            "--terminal-sidecar", str(sidecar), *extra_argv]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    if formats is not None:
        monkeypatch.setattr(agent_stream, "FORMATS", formats)
    agent_stream.main()
    return events, sidecar, out


def _events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _fixture(name):
    with open(os.path.join(_STREAM_FIXTURES, name), encoding="utf-8") as handle:
        return handle.read().replace("{EVIDENCE}", "/tmp/fault-gate.md")


def test_fault_raising_record_degrades_and_continues(monkeypatch, tmp_path):
    events, sidecar, _ = _run_main(
        monkeypatch, tmp_path, _fixture("raising-record.jsonl"),
        ("--provider-format", "specstride-test-v1", *_CONTEXT_ARGS),
        formats=_registry_with_test_row())
    found = _events(events)
    errors = [e for e in found
              if e["event"] == "agent_diagnostic" and e.get("code") == "adapter_error"]
    assert len(errors) == 1 and errors[0]["severity"] == "error"
    # The following tool and result were still processed.
    assert [e["event"] for e in found].count("agent_tool") == 1
    assert [e["event"] for e in found].count("evidence_writing") == 1
    # On the correlated (context) path the terminal goes to the sidecar; the
    # finalizer owns the one agent_result event.
    assert not [e for e in found if e["event"] == "agent_result"]
    side = json.loads(sidecar.read_text())
    assert side["malformed_stream"] is True
    assert side["provider_terminal"]["status"] == "success"


def test_fault_only_first_three_adapter_errors_emit(monkeypatch, tmp_path):
    stdin_text = "".join('{"type":"boom"}\n' for _ in range(5))
    events, _, _ = _run_main(
        monkeypatch, tmp_path, stdin_text,
        ("--provider-format", "specstride-test-v1", *_CONTEXT_ARGS),
        formats=_registry_with_test_row())
    errors = [e for e in _events(events)
              if e["event"] == "agent_diagnostic" and e.get("code") == "adapter_error"]
    assert len(errors) == 3
    assert all(e["severity"] == "error" for e in errors)


class _ExplodingInitAdapter:
    TOOL_NAMES = {}

    def __init__(self, policy, *, expected_evidence=None):
        raise RuntimeError("cannot start")


def test_fault_adapter_constructor_raises_degrades_to_raw(monkeypatch, tmp_path):
    row = StreamFormat("boom-init", _ExplodingInitAdapter,
                       capability=Capability("boom init selected"))
    lines = "plain one\nplain two\n"
    events, _, out = _run_main(
        monkeypatch, tmp_path, lines, ("--provider-format", "boom-init",),
        formats=build_registry((row,)))
    found = _events(events)
    degraded = [e for e in found if e["event"] == "agent_observability"]
    assert len(degraded) == 1 and degraded[0]["mode"] == "degraded"
    assert degraded[0]["reason"] == (
        "stream adapter for 'boom-init' failed to start — parsing plain output")
    assert degraded[0]["supported_signals"] == "result"
    # Raw pass-through of every line.
    assert out.getvalue() == lines


def test_fault_unknown_format_passes_through_raw(monkeypatch, tmp_path):
    lines = '{"type":"text","text":"hi"}\nnot json at all\n'
    events, _, out = _run_main(
        monkeypatch, tmp_path, lines, ("--provider-format", "nope",))
    assert out.getvalue() == lines
    assert _events(events) == []


def test_fault_unknown_format_context_emits_verbatim_raw_text_start(monkeypatch, tmp_path):
    lines = '{"unparsed": true}\n'
    events, _, out = _run_main(
        monkeypatch, tmp_path, lines, ("--provider-format", "nope", *_CONTEXT_ARGS))
    assert out.getvalue() == lines
    found = _events(events)
    starts = [e for e in found if e["event"] == "agent_observability"]
    assert len(starts) == 1
    assert starts[0]["mode"] == "raw-text"
    assert starts[0]["reason"] == (
        "structured schema unavailable — parsing plain output")
    assert starts[0]["supported_signals"] == "text,result"
    assert starts[0]["provider_format"] == "nope"


def test_fault_list_formats_prints_canonical_and_no_events(monkeypatch, tmp_path):
    events_path = tmp_path / "events.jsonl"
    monkeypatch.setattr(sys, "argv", ["agent_stream.py", "--list-formats",
                                      "--events", str(events_path)])
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    agent_stream.main()
    assert out.getvalue() == "claude\nprime-v3\n"
    assert not events_path.exists()


