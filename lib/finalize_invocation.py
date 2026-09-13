"""Reconcile one Prime invocation into its single durable result and breaker fold.

The controller (proposer.sh) observes the producer process (exit/signal/timeout/
launch, in ``producer.json``); the tap observes the provider terminal (in
``provider-terminal.json``). Neither alone is authoritative. This helper joins
them for exactly one invocation:

* reconcile both observations into one atomic ``result.json`` (never overwriting
  an existing artifact — one finalization per invocation);
* emit exactly one equivalent ``agent_result`` event into the run event log;
* fold the result into the persisted consecutive-error breaker state, consuming
  this exact invocation's result (never a historical tail-scan), counting each
  failure once, resetting on success;
* print ``halt``, ``cap_halt`` or ``continue`` plus the reason code, is_error,
  the error count, the kill class and the cap count, so the shell controller can
  stop before launching pass N+1 at whichever threshold it reached.

A watchdog kill is carried in by the controller (it owns the watchdog, and a
killed pass's provider stream is severed before it can say anything). The kill
reason and its class are recorded IN the durable ``result.json`` and in the
mirrored ``agent_result`` event, so a killed invocation keeps its single durable
result and ``learn.py summarize`` classifies it exactly as it classifies the
legacy path's. The class also decides which breaker counts it: ``budget``
(``hard_cap``) is a budget signal, never an agent error (design §4.2).

Usage:
  finalize_invocation.py <invocation_dir> <events_path> <breaker_state_path> \
      <limit> [kill_reason] [cap_limit]
"""

import json
from pathlib import Path
import sys

from invocation_result import (
    EventEnvelope,
    InvocationContext,
    atomic_write_json,
    reconcile_result,
)
from error_breaker import ConsecutiveErrorBreaker, classify_kill


def _load_json(path):
    return json.loads(Path(path).read_text())


def _context_from_metadata(metadata):
    return InvocationContext.create(
        run_id=metadata["run_id"],
        feature=metadata["feature"],
        role=metadata["role"],
        backend=metadata["backend"],
        phase=int(metadata["phase"]),
        attempt=int(metadata["attempt"]),
        iteration=int(metadata["iteration"]),
        invocation_id=metadata["invocation_id"],
        observability_mode=metadata.get("observability_mode", "structured"),
        provider_format=metadata.get("provider_format", "claude"),
        expected_evidence=metadata.get("expected_evidence"),
    )


def _emit_agent_result(events_path, context, result):
    if not events_path:
        return
    envelope = EventEnvelope(context)
    identity_keys = set(context.identity().keys())
    fields = {
        key: value for key, value in result.items()
        if key not in {"contract", *identity_keys}
    }
    record = envelope.normalize("agent_result", **fields)
    with open(events_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def finalize(invocation_dir, events_path, breaker_state_path, limit,
             kill_reason=None, cap_limit=0):
    invocation_dir = Path(invocation_dir)
    metadata = _load_json(invocation_dir / "metadata.json")
    context = _context_from_metadata(metadata)

    producer = {}
    producer_path = invocation_dir / "producer.json"
    if producer_path.exists():
        producer = _load_json(producer_path)

    provider_terminal = None
    malformed_stream = False
    sidecar_path = invocation_dir / "provider-terminal.json"
    if sidecar_path.exists():
        sidecar = _load_json(sidecar_path)
        provider_terminal = sidecar.get("provider_terminal")
        malformed_stream = bool(sidecar.get("malformed_stream"))

    # A watchdog kill is the controller's observation, not the provider's: the
    # kill severs the stream, so nothing in producer.json or the terminal sidecar
    # can name it. Recording it here is what keeps a killed pass's result durable
    # AND classifiable — losing it would leave the most expensive passes of a run
    # indistinguishable from a cheap crash.
    kill_class = classify_kill(kill_reason)
    kill_fields = {}
    if kill_class:
        kill_fields = {"kill_reason": kill_reason, "kill_class": kill_class}

    result_path = invocation_dir / "result.json"
    if result_path.exists():
        # Already finalized (e.g. a resume). Consume the durable artifact as-is;
        # never re-reconcile or double-emit.
        result = _load_json(result_path)
    else:
        result = reconcile_result(
            context,
            provider_terminal=provider_terminal,
            producer_exit_code=producer.get("producer_exit_code"),
            producer_signal=producer.get("producer_signal"),
            parser_exit_code=producer.get("parser_exit_code"),
            timed_out=bool(producer.get("timed_out")),
            launch_failed=bool(producer.get("launch_failed")),
            malformed_stream=malformed_stream,
            duration_ms=producer.get("duration_ms", 0),
            **kill_fields,
        )
        atomic_write_json(result_path, result, replace=False)
        _emit_agent_result(events_path, context, result)

    breaker = ConsecutiveErrorBreaker(
        run_id=context.run_id, feature=context.feature, role=context.role,
        phase=context.phase, attempt=context.attempt, limit=int(limit),
        cap_limit=int(cap_limit or 0),
    )
    state_path = Path(breaker_state_path)
    if state_path.exists():
        state = _load_json(state_path)
        breaker.count = int(state.get("count", 0))
        breaker.cap_count = int(state.get("cap_count", 0))
        breaker.last_invocation_id = state.get("last_invocation_id")
        breaker.last_reason_code = state.get("last_reason_code")
        breaker.last_kill_class = state.get("last_kill_class")
    breaker.record(result)
    atomic_write_json(state_path, {
        "count": breaker.count,
        "cap_count": breaker.cap_count,
        "last_invocation_id": breaker.last_invocation_id,
        "last_reason_code": breaker.last_reason_code,
        "last_kill_class": breaker.last_kill_class,
    })

    return result, breaker


def main(argv):
    invocation_dir, events_path, breaker_state_path, limit = argv[1:5]
    # Optional, and optional on purpose: a caller that knows nothing about kills
    # (or about the cap budget) gets exactly the previous behaviour.
    kill_reason = argv[5] if len(argv) > 5 else ""
    cap_limit = argv[6] if len(argv) > 6 else 0
    result, breaker = finalize(invocation_dir, events_path, breaker_state_path,
                               limit, kill_reason=kill_reason, cap_limit=cap_limit)
    if breaker.should_halt_cap():
        decision = "cap_halt"
    elif breaker.should_halt():
        decision = "halt"
    else:
        decision = "continue"
    print(decision)
    print(result.get("reason_code", ""))
    print("true" if result.get("is_error") else "false")
    print(str(breaker.count))
    print(result.get("kill_class") or "")
    print(str(breaker.cap_count))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
