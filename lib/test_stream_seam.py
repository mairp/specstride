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
