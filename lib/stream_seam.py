"""The one seam every stream adapter sits behind.

New stream adapters import from here — the shared outcome record, the target
helpers and (in later tasks) the format records and registry — never from the
tap itself (``agent_stream``), which would create an import cycle. This module
imports only the standard library and ``observability_policy``.
"""

from collections import deque
from dataclasses import dataclass, field
import re
from pathlib import Path

from observability_policy import ObservabilityPolicy, snake_key


TARGET_MAX = 120
TARGET_KEYS = (
    "file_path", "path", "notebook_path", "command", "pattern", "url", "query",
    "skill", "description", "prompt", "subject",
)

# Bounds for the nested target search (T015): a dict nested deeper than
# NESTED_MAX_DEPTH is not expanded, and the search stops after NESTED_MAX_NODES
# dequeued values, whatever the shape of the input.
NESTED_MAX_DEPTH = 6
NESTED_MAX_NODES = 256

# The tool names whose writes are matched against the expected gate evidence
# path (same five names, same result as the previous inline set).
EVIDENCE_TOOLS = frozenset({
    "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash",
})

# Every canonical tool name a registered adapter may report (T010).
CANONICAL_TOOLS = frozenset({
    "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Glob",
    "Grep", "Task", "TodoWrite", "WebFetch", "WebSearch",
})

# The five fine-grained signals a structured adapter can surface, in order.
SIGNAL_ORDER = ("init", "text", "tool", "evidence", "result")

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")


def one_line(value, limit):
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _dict_target(d):
    """The exact-then-converted key rule for one dict: an exact TARGET_KEYS hit
    wins over a snake_key-converted one, both in TARGET_KEYS tuple order."""
    for k in TARGET_KEYS:
        if d.get(k):
            return d[k]
    converted = [(snake_key(key), value) for key, value in d.items() if value]
    for k in TARGET_KEYS:
        for key, value in converted:
            if key == k:
                return value
    return None


def _nested_target(tool_input):
    """Bounded BFS over nested values for a target key.

    Returns ``(target, visited)``: the first BFS match wins, ``visited`` counts
    every dequeued dict/list/scalar. Top-level values sit at depth 1; a container
    at depth ``NESTED_MAX_DEPTH`` is counted but not expanded, so no value can be
    visited at depth > ``NESTED_MAX_DEPTH``."""
    visited = 0
    queue = deque([(tool_input, 0)])
    while queue:
        node, depth = queue.popleft()
        visited += 1
        if visited > NESTED_MAX_NODES:
            return "", NESTED_MAX_NODES
        if isinstance(node, dict):
            if depth <= NESTED_MAX_DEPTH:
                found = _dict_target(node)
                if found is not None:
                    return one_line(found, TARGET_MAX), visited
            if depth < NESTED_MAX_DEPTH:
                queue.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, (list, tuple)) and depth < NESTED_MAX_DEPTH:
            queue.extend((child, depth + 1) for child in node)
    return "", visited


def tool_target(name, tool_input):
    """Compact one-line description of what a tool call touches.

    Order: the ``Bash`` short-cut, the exact top-level pass over ``TARGET_KEYS``,
    a ``snake_key``-converted top-level pass, then a bounded nested search."""
    if not isinstance(tool_input, dict):
        return ""
    if name == "Bash":
        value = tool_input.get("command", "") or tool_input.get("description", "")
        return one_line(value, TARGET_MAX)
    for key in TARGET_KEYS:
        value = tool_input.get(key)
        if value:
            return one_line(value, TARGET_MAX)
    for k in TARGET_KEYS:
        for key, value in tool_input.items():
            if snake_key(key) == k and value:
                return one_line(value, TARGET_MAX)
    target, _ = _nested_target(tool_input)
    return target


def looks_like_evidence(name, tool_input, target, expected_evidence=None):
    """Classify writes only when a lexical target equals the expected gate path."""
    if not expected_evidence or name not in EVIDENCE_TOOLS:
        return False
    expected = Path(expected_evidence).expanduser().resolve()
    policy = ObservabilityPolicy(target_max_bytes=4096)
    candidates = policy.extract_target_paths(tool_input)
    if target:
        candidates.append(target)
    for candidate in candidates:
        try:
            if Path(candidate).expanduser().resolve() == expected:
                return True
        except (OSError, ValueError):
            continue
    return False


class ToolEvents:
    """Build the ``tool_use`` events of any adapter, once (T016).

    Reproduces ``ClaudeAdapter._consume_block``'s ``tool_use`` branch field for
    field — ``agent_tool``, the display line and the ``("tool_use", fields)``
    telemetry — except that the canonical tool name (the name given to
    ``tool_target`` and ``looks_like_evidence``) is
    ``tool_names.get(harness_name, harness_name)``. The ``evidence_writing``
    event fires at most once per instance."""

    def __init__(self, policy, expected_evidence, tool_names):
        self.policy = policy
        self.expected_evidence = expected_evidence
        self.tool_names = dict(tool_names or {})
        self.evidence_announced = False

    def build(self, harness_name, tool_input, tool_id=None):
        name = self.tool_names.get(harness_name, harness_name)
        raw_target = tool_target(name, tool_input)
        target = self.policy.sanitize_text(raw_target, TARGET_MAX).value
        summary = self.policy.summarize_targets(tool_input or {})
        fields = {
            "tool": name,
            "target": target,
            "targets": summary["targets"],
            "redacted": summary["redacted"],
            "truncated": summary["truncated"],
            "original_bytes": summary["original_bytes"],
            "retained_bytes": summary["retained_bytes"],
        }
        if tool_id:
            fields["tool_id"] = tool_id
        events = [("agent_tool", fields)]
        display = "  → %s %s" % (name, target) if target else "  → %s" % name
        if not self.evidence_announced and looks_like_evidence(
            name, tool_input, target, self.expected_evidence,
        ):
            self.evidence_announced = True
            evidence = {"tool": name, "target": str(self.expected_evidence),
                        "match": "exact-expected-target"}
            if tool_id:
                evidence["tool_id"] = tool_id
            events.append(("evidence_writing", evidence))
        return events, display, ("tool_use", fields)


@dataclass
class AdapterOutcome:
    events: list = field(default_factory=list)
    output: list = field(default_factory=list)
    terminal: dict | None = None
    telemetry: tuple | None = None
    # events index -> span-only attributes (trace spans only, never events/Loki)
    span_content: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Capability:
    """What a stream format can capture, announced on the invocation-start event."""

    reason: str
    signals: str = "init,text,tool,evidence,result"


@dataclass(frozen=True)
class StreamFormat:
    """One row of the seam table: a named stream format and its adapter."""

    name: str
    adapter: type
    capability: Capability
    aliases: tuple = ()
    has_init_record: bool = True
    synth_init: bool = False
    synth_terminal: bool = False
    no_activity: bool = False
    track_terminal: bool = False   # wrap only to expose terminal_seen

    @property
    def opts(self):
        """True when the row opts into any shared behaviour beyond a bare adapter."""
        return bool(self.synth_init or self.synth_terminal
                    or self.no_activity or self.track_terminal)


class FormatRegistry:
    """Immutable lookup over the declared stream formats, by name or alias."""

    def __init__(self, rows):
        self._rows = tuple(rows)
        self._by_name = {}
        for row in self._rows:
            self._by_name[row.name] = row
            for alias in row.aliases:
                self._by_name[alias] = row

    def lookup(self, name_or_alias):
        """The row registered under this canonical name or alias, else None."""
        return self._by_name.get(name_or_alias)

    def names(self):
        """Every canonical name then its aliases, in table order."""
        result = []
        for row in self._rows:
            result.append(row.name)
            result.extend(row.aliases)
        return tuple(result)

    def canonical(self):
        """Only the canonical names, in table order."""
        return tuple(row.name for row in self._rows)

    def __iter__(self):
        return iter(self._rows)


def build_registry(rows):
    """Validate the format rows (pure, no I/O) and build the registry.

    Raises ``ValueError`` naming the offending format on: a repeated name or
    alias, a name/alias that is not a lowercase identifier, an empty capability
    reason, signals that are not an in-order comma-joined subset of
    ``SIGNAL_ORDER``, ``synth_init`` alongside ``has_init_record``, or an
    adapter ``TOOL_NAMES`` value outside ``CANONICAL_TOOLS``.
    """
    seen = {}
    built = []
    for row in rows:
        _validate_format(row)
        for label in (row.name,) + tuple(row.aliases):
            if label in seen:
                raise ValueError(
                    "duplicate stream format: %s" % label)
            seen[label] = row.name
        built.append(row)
    return FormatRegistry(built)


def _validate_format(row):
    if not _NAME_PATTERN.match(row.name):
        raise ValueError("invalid stream format name: %s" % row.name)
    for alias in row.aliases:
        if not _NAME_PATTERN.match(alias):
            raise ValueError("invalid stream format name: %s" % alias)
    if not row.capability.reason:
        raise ValueError("empty capability reason for stream format: %s" % row.name)
    _validate_signals(row)
    if row.synth_init and row.has_init_record:
        raise ValueError(
            "stream format %s declares synth_init with has_init_record" % row.name)
    for value in getattr(row.adapter, "TOOL_NAMES", {}).values():
        if value not in CANONICAL_TOOLS:
            raise ValueError(
                "stream format %s maps tool to non-canonical name: %s" % (row.name, value))


def _validate_signals(row):
    signals = row.capability.signals
    if not signals:
        raise ValueError("stream format %s declares no signals" % row.name)
    parts = signals.split(",")
    if any(not part for part in parts):
        raise ValueError("stream format %s has empty signal entries" % row.name)
    indexes = []
    for part in parts:
        if part not in SIGNAL_ORDER:
            raise ValueError(
                "stream format %s declares unknown signal: %s" % (row.name, part))
        index = SIGNAL_ORDER.index(part)
        if index in indexes or (indexes and index < indexes[-1]):
            raise ValueError(
                "stream format %s declares out-of-order or repeated signals: %s"
                % (row.name, signals))
        indexes.append(index)


class SharedBehaviour:
    """The opt-in shared behaviours one stream format may request (T020).

    Wraps an inner adapter for a ``StreamFormat`` row that declares at least one
    of ``synth_init`` / ``synth_terminal`` / ``no_activity`` /
    ``track_terminal``. Nothing is invented: a synthesised ``agent_init`` carries
    the invocation's header facts, a synthesised terminal is only ever the
    honest ``missing_terminal``, and ``no_activity`` is an additive flag on a
    success terminal. ``status``, ``reason_code`` and ``is_error`` are never
    touched. ``terminal_seen`` is exposed read-only for the later hang
    classifier, on every wrapper (including a row whose only opt is
    ``track_terminal``, which synthesises and stamps nothing).
    """

    def __init__(self, inner, decl, invocation_model=None):
        self.inner = inner
        self.decl = decl
        self.invocation_model = invocation_model
        self._activity = False
        self._terminal_seen = False
        self._init_emitted = False
        self._finished = False
        # Expose consume_raw only when the inner adapter has it, so the tap's
        # duck check (getattr(adapter, "consume_raw", None)) stays honest.
        inner_raw = getattr(inner, "consume_raw", None)
        if inner_raw is not None:
            self.consume_raw = inner_raw

    @property
    def terminal_seen(self):
        """Read-only: whether a terminal (delivered or from inner.finish) was seen."""
        return self._terminal_seen

    def _header_facts(self):
        return {"model": self.invocation_model or None, "tools": None}

    def _synth_init(self):
        self._init_emitted = True
        facts = self._header_facts()
        event = ("agent_init", facts)
        display = "  · init model=%s" % (facts["model"] or "?")
        return event, display

    def _first_terminal(self, outcome):
        self._terminal_seen = True
        terminal = outcome.terminal
        if self.decl.no_activity and terminal.get("status") == "success":
            terminal["no_activity"] = not self._activity

    def _later_terminal(self, outcome):
        outcome.terminal = None
        outcome.events.append(("agent_diagnostic", {
            "code": "duplicate_terminal", "severity": "warning",
            "message": one_line(
                "duplicate terminal dropped: the pass already had one", 200),
        }))

    def _handle_terminal(self, outcome):
        if outcome.terminal is None:
            return
        if self._terminal_seen:
            self._later_terminal(outcome)
        else:
            self._first_terminal(outcome)

    def consume(self, record):
        outcome = self.inner.consume(record)
        if self.decl.synth_init and not self._init_emitted:
            event, display = self._synth_init()
            outcome.events.insert(0, event)
            outcome.output.insert(0, display)
        if any(name in ("agent_tool", "evidence_writing")
               for name, _ in outcome.events):
            self._activity = True
        self._handle_terminal(outcome)
        return outcome

    def finish(self):
        """Idempotent: the inner finish runs once; a missing terminal is synthesised."""
        outcome = AdapterOutcome()
        if self._finished:
            return outcome
        self._finished = True
        inner_finish = getattr(self.inner, "finish", None)
        if inner_finish is not None:
            inner_outcome = inner_finish()
            outcome.events.extend(inner_outcome.events)
            outcome.output.extend(inner_outcome.output)
            outcome.telemetry = outcome.telemetry or inner_outcome.telemetry
            self._handle_terminal(inner_outcome)
            outcome.terminal = inner_outcome.terminal
        if self.decl.synth_terminal and not self._terminal_seen:
            terminal = {
                "status": "error",
                "reason_code": "missing_terminal",
                "reason": "Provider stream ended without a terminal result",
                "model": self.invocation_model or None,
            }
            self._terminal_seen = True
            outcome.terminal = terminal
            outcome.output.append(
                "  ✗ result: missing_terminal (stream ended before result)")
        return outcome
