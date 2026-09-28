"""A fixture for the seam tests, never registered in the shipped table.

Adapter for the made-up ``specstride-test-v1`` stream (Specstride feature
004-stream-adapter-seam). It models no real harness; it exists so the seam's
shared behaviours and the Story 2 evidence detection can be exercised end to
end without a real provider. Imports only ``stream_seam`` and
``observability_policy`` — never ``agent_stream`` (that would be a cycle).
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from stream_seam import AdapterOutcome, ToolEvents, one_line
from observability_policy import ObservabilityPolicy  # noqa: F401

# Same cap as the tap's TEXT_MAX for Claude text blocks.
_TEXT_MAX = 160


class StreamTestAdapter:
    """Consume ``specstride-test-v1`` records into seam outcomes."""

    TOOL_NAMES = {"write_file": "Write", "run_shell": "Bash", "read_file": "Read"}

    def __init__(self, policy, *, expected_evidence=None):
        self.policy = policy
        self.expected_evidence = expected_evidence
        self._tools = ToolEvents(policy, expected_evidence, self.TOOL_NAMES)

    def consume(self, record):
        outcome = AdapterOutcome()
        record_type = record.get("type")
        if record_type == "text":
            text = (record.get("text") or "").strip()
            if text:
                display = self.policy.sanitize_text(text, self.policy.text_max_bytes)
                cleaned = self.policy.sanitize_text(" ".join(text.split()), _TEXT_MAX)
                outcome.events.append(("agent_text", {
                    "text": cleaned.value,
                    **cleaned.metadata(),
                }))
                outcome.output.append(display.value)
        elif record_type == "tool":
            events, display, telemetry = self._tools.build(
                record.get("name", "?"), record.get("input"), record.get("id"),
            )
            outcome.events.extend(events)
            outcome.output.append(display)
            outcome.telemetry = telemetry
        elif record_type == "result":
            outcome.terminal = self._terminal(record)
        elif record_type == "boom":
            raise RuntimeError("boom")
        else:
            outcome.events.append(("agent_diagnostic", {
                "code": "unknown_record", "severity": "warning",
                "message": one_line("unknown record type: %s" % record_type, 200),
            }))
        return outcome

    def _terminal(self, record):
        terminal = {key: value for key, value in record.items() if key != "type"}
        terminal["cost_usd"] = None  # never a fabricated 0
        status = record.get("status")
        terminal["reason_code"] = (
            "success" if status == "success" else str(record.get("code") or status))
        return terminal
