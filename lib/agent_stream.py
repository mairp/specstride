#!/usr/bin/env python3
"""Turn a coding agent's JSONL stream into safe Specstride events (stdlib only)."""

import argparse
import json
import os
import signal
import sys
import time

from invocation_result import EventEnvelope, InvocationContext, atomic_write_json
from observability_policy import ObservabilityPolicy
from prime_stream import PrimeAdapter
from stream_seam import (
    AdapterOutcome, TARGET_KEYS, TARGET_MAX, tool_target, looks_like_evidence,
    Capability, StreamFormat, SharedBehaviour, build_registry,
)
from telemetry_delivery import LocalFirstFanout
import specstride_env  # noqa: E402  (legacy env names map onto SPECSTRIDE_*)
specstride_env.apply()


TEXT_MAX = 160


class EventSink:
    """Append complete JSON events, using correlated envelopes when available."""

    def __init__(
        self, path, run_id="", task="", backend="", *, context=None, policy=None,
    ):
        self.path = str(path) if path else ""
        self.policy = policy or ObservabilityPolicy()
        self.envelope = EventEnvelope(context) if context else None
        self.base = {}
        if run_id:
            self.base["run_id"] = run_id
        if task:
            self.base["task"] = task
        if backend:
            self.base["backend"] = backend

    def emit(self, event, **fields):
        fields = self._sanitize_fields(fields)
        if self.envelope:
            record = self.envelope.normalize(event, **fields)
        else:
            record = {
                "ts": "%f" % time.time(),
                "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "event": event,
                **self.base,
                **fields,
            }
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            except OSError:
                pass
        # Return the identity-enriched fields (run_id/invocation_id/sequence and
        # any correlation the envelope added) so the fan-out ships the SAME
        # correlated view to every remote sink as it wrote locally. Envelope
        # bookkeeping (ts/time/event) stays out of the shipped field dict: the
        # shippers time-stamp their own records and carry `event` as a label.
        return {key: value for key, value in record.items()
                if key not in ("ts", "time", "event")}

    def _sanitize_fields(self, fields):
        result = {}
        metadata = None
        for key, value in fields.items():
            if value is None:
                continue
            if key in {"text", "message", "summary", "result_summary", "target"}:
                cleaned = self.policy.sanitize_text(value)
            else:
                cleaned = self.policy.sanitize(value)
            result[key] = cleaned.value
            if key in {"text", "message", "summary", "result_summary", "target"}:
                metadata = cleaned.metadata()
        if metadata:
            for key, value in metadata.items():
                result.setdefault(key, value)
        return result


class ClaudeAdapter:
    """Normalize the established Claude stream without finalizing invocation state."""

    def __init__(self, policy, *, expected_evidence=None):
        self.policy = policy
        self.expected_evidence = expected_evidence
        self.model_seen = None
        self.evidence_announced = False
        self._terminal_exposed = False
        self._finished = False

    def consume(self, record):
        outcome = AdapterOutcome()
        record_type = record.get("type")
        if record_type == "system" and record.get("subtype") == "init":
            self.model_seen = record.get("model") or self.model_seen
            tool_count = len(record.get("tools", []) or [])
            outcome.events.append(("agent_init", {
                "model": self.model_seen, "tools": tool_count,
            }))
            outcome.output.append("  · init model=%s tools=%d" % (
                self.model_seen or "?", tool_count,
            ))
        elif record_type == "assistant":
            message = record.get("message", {}) or {}
            self.model_seen = message.get("model") or self.model_seen
            for block in message.get("content", []) or []:
                self._consume_block(block, outcome)
        elif record_type == "result":
            outcome.terminal = self._terminal(record)
            self._terminal_exposed = True
            outcome.output.append(
                "  ✓ result: %s  cost=$%.4f  turns=%s  out_tok=%s  %sms" % (
                    record.get("subtype", "?"), record.get("total_cost_usd") or 0.0,
                    record.get("num_turns", "?"),
                    (record.get("usage", {}) or {}).get("output_tokens", "?"),
                    record.get("duration_ms", "?"),
                )
            )
            outcome.telemetry = ("api_request", outcome.terminal)
        return outcome

    def _consume_block(self, block, outcome):
        block_type = block.get("type")
        if block_type == "text":
            text = (block.get("text") or "").strip()
            if text:
                display = self.policy.sanitize_text(text, self.policy.text_max_bytes)
                cleaned = self.policy.sanitize_text(" ".join(text.split()), TEXT_MAX)
                outcome.events.append(("agent_text", {
                    "text": cleaned.value,
                    **cleaned.metadata(),
                }))
                outcome.output.append(display.value)
        elif block_type == "tool_use":
            name = block.get("name", "?")
            tool_input = block.get("input")
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
            if block.get("id"):
                fields["tool_id"] = block["id"]
            outcome.events.append(("agent_tool", fields))
            outcome.output.append("  → %s %s" % (name, target) if target else "  → %s" % name)
            if not self.evidence_announced and looks_like_evidence(
                name, tool_input, target, self.expected_evidence,
            ):
                self.evidence_announced = True
                evidence = {"tool": name, "target": str(self.expected_evidence),
                            "match": "exact-expected-target"}
                if block.get("id"):
                    evidence["tool_id"] = block["id"]
                outcome.events.append(("evidence_writing", evidence))
            outcome.telemetry = ("tool_use", fields)

    def _terminal(self, record):
        usage = record.get("usage", {}) or {}
        is_error = bool(record.get("is_error"))
        terminal = {
            "status": "error" if is_error else "success",
            "stop_reason": record.get("subtype"),
            "model": self.model_seen or record.get("model"),
            "duration_ms": record.get("duration_ms"),
            "num_turns": record.get("num_turns"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_read_tokens": usage.get("cache_read_input_tokens"),
            "cache_creation_tokens": usage.get("cache_creation_input_tokens"),
            "cost_usd": record.get("total_cost_usd"),
        }
        if is_error:
            terminal["reason_code"] = "provider_error"
            terminal["reason"] = record.get("result") or "Provider reported an error"
        return terminal

    def finish(self):
        """Synthesize one failing terminal when the stream ended before ``result``."""
        outcome = AdapterOutcome()
        if self._finished:
            return outcome
        self._finished = True
        if self._terminal_exposed:
            return outcome
        terminal = {
            "status": "error",
            "reason_code": "missing_terminal",
            "reason": "Provider stream ended without a terminal result",
            "model": self.model_seen,
        }
        self._terminal_exposed = True
        outcome.terminal = terminal
        outcome.output.append("  ✗ result: missing_terminal (stream ended before result)")
        return outcome


# ── stream formats (the seam) ─────────────────────────────────────────────
# Adding a format = its adapter module plus one import and one row here.
# Order is display order; duplicates fail at import.
STREAM_FORMATS = (
    StreamFormat("claude", ClaudeAdapter, aliases=("claude-stream-json",),
                 capability=Capability("Claude stream-json schema selected")),
    StreamFormat("prime-v3", PrimeAdapter,
                 capability=Capability("Prime JSON schema v3 selected")),
)
FORMATS = build_registry(STREAM_FORMATS)


def select_provider_adapter(provider_format, policy, *, registry=None,
                            invocation_model=None, **kwargs):
    """Build the adapter for a format, resolving only through the seam table."""
    row = (registry or FORMATS).lookup(provider_format)
    if row is None:
        raise ValueError("unsupported provider format: %s" % provider_format)
    if row.opts:
        return SharedBehaviour(row.adapter(policy, **kwargs), row, invocation_model)
    return row.adapter(policy, **kwargs)


# The five fine-grained signals a structured adapter can surface. Emitted verbatim
# on the invocation-start observability event so an operator sees WHAT capture is
# available, not just that "some" capture happened (SC-012).
_STRUCTURED_SIGNALS = "init,text,tool,evidence,result"


def observability_start(provider_format, *, registry=None):
    """Describe the capability an invocation begins with (T060).

    Returns (mode, reason, supported_signals) for the invocation-start
    ``agent_observability`` event. A recognized structured schema starts fully
    structured; any other format degrades to raw-text parsing up front so the
    absence of fine-grained signals is explicit rather than silent."""
    row = (registry or FORMATS).lookup(provider_format)
    if row is not None:
        return "structured", row.capability.reason, row.capability.signals
    return ("raw-text",
            "structured schema unavailable — parsing plain output", "text,result")


# Adapter diagnostics that mean the structured schema itself could not be parsed:
# the capability mode degrades from `structured` to `degraded`, and only the
# coarse terminal result remains trustworthy.
_DEGRADE_CODES = {"unsupported_schema", "absent_schema"}


def observability_degrade(code, message):
    """Map a fatal schema diagnostic to a capability transition (T060).

    Returns (mode, reason, supported_signals) or ``None`` when the diagnostic is
    not a schema-level degradation (e.g. a bounded malformed-line warning)."""
    if code not in _DEGRADE_CODES:
        return None
    reason = message or "structured schema rejected — degraded parsing"
    return "degraded", reason, "result"


def _invocation_context(args):
    values = (args.run_id, args.feature, args.role, args.backend, args.phase,
              args.attempt, args.iteration)
    if not all(value not in (None, "") for value in values):
        return None
    return InvocationContext.create(
        run_id=args.run_id, feature=args.feature, role=args.role, backend=args.backend,
        phase=int(args.phase), attempt=int(args.attempt), iteration=int(args.iteration),
        invocation_id=args.invocation_id or None, provider_format=args.provider_format,
        expected_evidence=args.expected_evidence or None,
    )


def _telemetry(args):
    loki = otel = logfmt = None
    if args.loki:
        try:
            import ralph_loki_ship as ship
            base = {"job": "ralph", **({"task": args.task} if args.task else {}),
                    **({"backend": args.backend} if args.backend else {})}
            loki, logfmt = ship.Loki(args.loki, base), ship.logfmt
        except Exception as error:  # noqa: BLE001
            sys.stderr.write("agent_stream: Loki disabled (%s)\n" % error)
    if args.otel:
        try:
            import ralph_otel_ship as ship
            resource = {"service.name": "ralph", **({"task": args.task} if args.task else {}),
                        **({"backend": args.backend} if args.backend else {})}
            otel = ship.Otel(args.otel, resource)
            logfmt = logfmt or ship.logfmt
        except Exception as error:  # noqa: BLE001
            sys.stderr.write("agent_stream: OTEL disabled (%s)\n" % error)
    return loki, otel, logfmt


def main():
    parser = argparse.ArgumentParser(description="specstride agent stream parser")
    parser.add_argument("--events", default=os.environ.get("SPECSTRIDE_EVENTS", ""))
    parser.add_argument("--run-id", default=os.environ.get("SPECSTRIDE_RUN_ID", ""))
    parser.add_argument("--task", default=os.environ.get("SPECSTRIDE_TASK", ""))
    parser.add_argument("--feature", default=os.environ.get("SPECSTRIDE_FEATURE", ""))
    parser.add_argument("--role", choices=("proposer", "accelerator", "critic"), default=None)
    parser.add_argument("--backend", default=os.environ.get("SPECSTRIDE_BACKEND_LABEL", ""))
    parser.add_argument("--phase", type=int)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--iteration", "--iter", dest="iteration", type=int)
    parser.add_argument("--invocation-id", default="")
    parser.add_argument("--expected-evidence", default="")
    parser.add_argument("--provider-format", default="claude")
    # The model the invocation specified (research R5). Used only by the
    # shared-behaviour wrapper (synthesised init / missing_terminal header
    # facts); bare adapters ignore it, so Claude and Prime output is unchanged.
    parser.add_argument("--invocation-model", default="")
    # When set (structured Prime path only), the tap records the provider-terminal
    # observation it — and only it — can see into this atomic sidecar. The producer
    # exit/signal/timeout is observed separately by the controller (producer.json);
    # the controller reconciles both into the single durable result.json. The tap
    # NEVER writes result.json itself: it lacks the producer status.
    parser.add_argument("--terminal-sidecar", default="")
    parser.add_argument("--loki", default="")
    parser.add_argument("--otel", default="")
    # Registry introspection for the bash shim specstride_stream_formats: print
    # the canonical format names, one per line, and exit before reading stdin.
    # Emits no events and constructs no telemetry shipper.
    parser.add_argument("--list-formats", action="store_true")
    args = parser.parse_args()

    if args.list_formats:
        for name in FORMATS.canonical():
            print(name)
        return

    policy = ObservabilityPolicy()
    context = _invocation_context(args)
    sink = EventSink(
        args.events, args.run_id, args.task, args.backend, context=context, policy=policy,
    )
    # Resolve the row through the seam table BEFORE building the adapter, so an
    # unknown format (or an adapter whose constructor raises) degrades into a
    # plain pass-through instead of the old "fatal (ignored)" that never read
    # stdin and left the producer with SIGPIPE (contracts/tap-cli.md).
    adapter = None
    constructor_error = None
    if FORMATS.lookup(args.provider_format) is not None:
        try:
            adapter = select_provider_adapter(
                args.provider_format, policy,
                expected_evidence=(
                    context.expected_evidence if context
                    else args.expected_evidence or None),
                invocation_model=args.invocation_model or None,
            )
        except Exception as error:  # noqa: BLE001 — degrade, never die (T023)
            constructor_error = error
    loki, otel, logfmt = _telemetry(args)
    # Fan every normalized event out local-first (authoritative JSONL), then to each
    # independently configured remote sink, persisting recursion-safe local
    # telemetry_delivery records on flush (T042). The local writer is the same
    # sanitizing EventSink.emit, so sinks only ever see sanitized fields.
    remote_sinks = {}
    if loki:
        remote_sinks["loki"] = loki
    if otel:
        remote_sinks["otel"] = otel
    fanout = (
        LocalFirstFanout(sink.emit, remote_sinks, sanitize=lambda f: policy.sanitize(f).value)
        if remote_sinks else None
    )
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    common = {"iter": args.iteration} if args.iteration is not None and not context else {}
    last_terminal = {"value": None}
    malformed = {"flag": False}

    def emit_event(event, **fields):
        # Fan-out writes locally (via sink.emit) AND ships to each configured sink;
        # with no sinks it degrades to a plain local write. Never double-writes.
        merged = {**fields, **common}
        if fanout:
            fanout.emit(event, merged)
        else:
            sink.emit(event, **merged)

    # Announce the capability this invocation begins with (T060). Gated on the
    # correlated (invocation-context) path: the legacy claude CLI stream must keep
    # emitting exactly [agent_init, agent_result] (US6 backend-parity guarantee).
    observed_mode = {"value": None}
    if context and constructor_error is None:
        start_mode, start_reason, start_signals = observability_start(args.provider_format)
        observed_mode["value"] = start_mode
        emit_event(
            "agent_observability", mode=start_mode, reason=start_reason,
            provider_format=args.provider_format, role=context.role,
            supported_signals=start_signals,
        )
    if constructor_error is not None:
        # The adapter could not start (T023): announce the degraded capability —
        # only the coarse terminal result is trustworthy — and parse plain output.
        observed_mode["value"] = "degraded"
        emit_event(
            "agent_observability", mode="degraded",
            reason=("stream adapter for '%s' failed to start — parsing plain output"
                    % args.provider_format),
            provider_format=args.provider_format,
            **({"role": context.role} if context else {}),
            supported_signals="result",
        )

    # Adapter-fault policy (T023): the first 3 adapter faults per pass get a
    # diagnostic; later ones are only counted. Every fault sets the malformed
    # flag on the terminal sidecar and the pass carries on with the next line.
    adapter_errors = {"count": 0}

    def handle_adapter_error(exc):
        adapter_errors["count"] += 1
        malformed["flag"] = True
        if adapter_errors["count"] <= 3:
            emit_event("agent_diagnostic", code="adapter_error", severity="error",
                       message=str(exc)[:200])

    try:
        for raw in sys.stdin:
            if stop["flag"]:
                break
            raw = raw.strip()
            if not raw:
                continue
            if adapter is None:
                # Unknown format or a failed adapter start: every line passes
                # through raw, exactly as before the tap existed.
                print(raw)
                continue
            missing = object()
            try:
                record = json.loads(raw)
            except ValueError:
                record = missing
            if record is missing:
                consume_raw = getattr(adapter, "consume_raw", None)
                if consume_raw is None:
                    print(raw)
                    continue
                try:
                    outcome = consume_raw(raw)
                except Exception as error:  # noqa: BLE001 — degrade (T023)
                    handle_adapter_error(error)
                    continue
            else:
                try:
                    outcome = adapter.consume(record)
                except Exception as error:  # noqa: BLE001 — degrade (T023)
                    handle_adapter_error(error)
                    continue
            for event, fields in outcome.events:
                emit_event(event, **fields)
                if event == "agent_diagnostic" and fields.get("code") == "malformed_json":
                    malformed["flag"] = True
                # A fatal schema diagnostic is a capability transition, not just a
                # log line: surface the structured→degraded change once (T060).
                if context and event == "agent_diagnostic":
                    transition = observability_degrade(
                        fields.get("code"), fields.get("message"))
                    if transition and transition[0] != observed_mode["value"]:
                        mode, reason, signals = transition
                        observed_mode["value"] = mode
                        emit_event(
                            "agent_observability", mode=mode, reason=reason,
                            provider_format=args.provider_format, role=context.role,
                            supported_signals=signals,
                        )
            if outcome.terminal:
                last_terminal["value"] = outcome.terminal
            if outcome.terminal and not context:
                terminal = outcome.terminal
                emit_event(
                    "agent_result",
                    model=terminal.get("model"),
                    is_error=terminal.get("status") != "success",
                    subtype=terminal.get("stop_reason"),
                    cost_usd=terminal.get("cost_usd"),
                    duration_ms=terminal.get("duration_ms"),
                    num_turns=terminal.get("num_turns"),
                    input_tokens=terminal.get("input_tokens"),
                    output_tokens=terminal.get("output_tokens"),
                    cache_read_tokens=terminal.get("cache_read_tokens"),
                    cache_creation_tokens=terminal.get("cache_creation_tokens"),
                )
            for line in outcome.output:
                print(line)
            # A terminal is the invocation-completion barrier: flush every sink and
            # persist recursion-safe local telemetry_delivery records (SC-006).
            if outcome.terminal and fanout:
                fanout.flush()
            sys.stdout.flush()
        finish = getattr(adapter, "finish", None)
        if finish:
            # finish() is called at most once per process (T023), and a fault in
            # it degrades into a diagnostic exactly like a consume fault.
            try:
                outcome = finish()
            except Exception as error:  # noqa: BLE001 — degrade (T023)
                handle_adapter_error(error)
                outcome = None
            if outcome is not None:
                for event, fields in outcome.events:
                    emit_event(event, **fields)
                if outcome.terminal:
                    last_terminal["value"] = outcome.terminal
                if outcome.terminal and not context:
                    terminal = outcome.terminal
                    emit_event(
                        "agent_result",
                        model=terminal.get("model"),
                        is_error=terminal.get("status") != "success",
                        subtype=terminal.get("stop_reason") or terminal.get("reason_code"),
                        reason_code=terminal.get("reason_code"),
                        reason=terminal.get("reason"),
                        cost_usd=terminal.get("cost_usd"),
                        duration_ms=terminal.get("duration_ms"),
                        num_turns=terminal.get("num_turns"),
                        input_tokens=terminal.get("input_tokens"),
                        output_tokens=terminal.get("output_tokens"),
                        cache_read_tokens=terminal.get("cache_read_tokens"),
                        cache_creation_tokens=terminal.get("cache_creation_tokens"),
                    )
                for line in outcome.output:
                    print(line)
                sys.stdout.flush()
    except BrokenPipeError:
        pass
    finally:
        # Final barrier: flush residual batches and persist their delivery records.
        if fanout:
            try:
                fanout.flush()
            except Exception:  # noqa: BLE001
                pass
        if args.terminal_sidecar:
            # Persist exactly what the tap observed of the provider terminal, plus
            # the malformed-stream signal. The controller reconciles this with the
            # producer status it observed; the tap never decides success on its own.
            try:
                atomic_write_json(args.terminal_sidecar, {
                    "contract": "specstride-provider-terminal/v1",
                    "provider_terminal": last_terminal["value"],
                    "malformed_stream": malformed["flag"],
                })
            except Exception as error:  # noqa: BLE001
                sys.stderr.write("agent_stream: terminal sidecar write failed (%s)\n" % error)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as error:  # noqa: BLE001
        sys.stderr.write("agent_stream: fatal (ignored): %s\n" % error)
