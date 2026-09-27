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
    build_registry,
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
