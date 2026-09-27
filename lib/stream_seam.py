"""The one seam every stream adapter sits behind.

New stream adapters import from here — the shared outcome record, the target
helpers and (in later tasks) the format records and registry — never from the
tap itself (``agent_stream``), which would create an import cycle. This module
imports only the standard library and ``observability_policy``.
"""

from dataclasses import dataclass, field
import re
from pathlib import Path

from observability_policy import ObservabilityPolicy


TARGET_MAX = 120
TARGET_KEYS = (
    "file_path", "path", "notebook_path", "command", "pattern", "url", "query",
    "skill", "description", "prompt", "subject",
)

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


def tool_target(name, tool_input):
    """Compact one-line description of what a tool call touches."""
    if not isinstance(tool_input, dict):
        return ""
    if name == "Bash":
        value = tool_input.get("command", "") or tool_input.get("description", "")
        return one_line(value, TARGET_MAX)
    for key in TARGET_KEYS:
        value = tool_input.get(key)
        if value:
            return one_line(value, TARGET_MAX)
    return ""


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


@dataclass
class AdapterOutcome:
    events: list = field(default_factory=list)
    output: list = field(default_factory=list)
    terminal: dict | None = None
    telemetry: tuple | None = None


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
