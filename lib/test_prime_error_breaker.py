"""Contracts for the exact, current-invocation consecutive-error breaker.

The proposer must stop predictably on repeated failing passes. Today the breaker
tail-scans the whole run event log for the last ``agent_result`` (proposer.sh),
which cannot distinguish the current invocation from a historical one and cannot
isolate concurrent features. These are the US2 failing tests for the replacement
breaker core (data-model ConsecutiveErrorState, invocation-v1 "reads this exact
invocation result or selects the event by exact invocation id"):

* exact invocation lookup by identity-derived path, never latest-by-mtime;
* a single increment per failing invocation (duplicate result for the same id
  is a contract violation and never double-counts);
* reset to zero on a successful invocation;
* isolation from historical results left elsewhere in the debug tree;
* isolation from other features/scopes running concurrently;
* halting exactly at the limit, before the next (N+1) invocation is launched.

The implementation (``lib/error_breaker.py``, wired from ``proposer.sh`` in T032)
does not exist yet, so these tests fail until it does.
"""

import json

import pytest

from invocation_result import InvocationContext, reconcile_result
from error_breaker import (
    ConsecutiveErrorBreaker,
    classify_kill,
    load_invocation_result,
    resolve_result_path,
    select_result_event,
)
import finalize_invocation


def _context(invocation_id, iteration, *, feature="feature-a", run_id="run-1",
             phase=1, attempt=1):
    return InvocationContext.create(
        run_id=run_id, feature=feature, role="proposer", backend="prime",
        phase=phase, attempt=attempt, iteration=iteration,
        invocation_id=invocation_id,
    )


def _error(invocation_id, iteration, *, reason_code="provider_auth", **scope):
    return reconcile_result(
        _context(invocation_id, iteration, **scope),
        provider_terminal={"status": "error", "reason_code": reason_code,
                           "reason": "boom", "stop_reason": "error"},
        producer_exit_code=0, parser_exit_code=0,
    )


def _success(invocation_id, iteration, **scope):
    return reconcile_result(
        _context(invocation_id, iteration, **scope),
        provider_terminal={"status": "success", "stop_reason": "stop"},
        producer_exit_code=0, parser_exit_code=0,
    )


def _killed(invocation_id, iteration, reason="hard_cap", **scope):
    """A watchdog-killed pass exactly as the finalizer records one: the producer
    was terminated (timeout-shaped), and the controller's kill observation rides
    along on the durable result."""
    kill_class = classify_kill(reason)
    return reconcile_result(
        _context(invocation_id, iteration, **scope),
        timed_out=True, parser_exit_code=0,
        kill_reason=reason, kill_class=kill_class,
    )


def _write_result(root, result):
    path = resolve_result_path(root, result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result) + "\n")
    return path


def _breaker(limit=2, *, feature="feature-a", run_id="run-1", phase=1, attempt=1):
    return ConsecutiveErrorBreaker(
        run_id=run_id, feature=feature, role="proposer",
        phase=phase, attempt=attempt, limit=limit,
    )


# --- exact invocation lookup -------------------------------------------------

def test_resolve_result_path_is_identity_derived(tmp_path):
    result = _error("inv-a", 3)
    expected = (tmp_path / "run-1" / "proposer" / "phase-1" / "attempt-1"
                / "iter-3" / "inv-a" / "result.json")
    assert resolve_result_path(tmp_path, result) == expected


def test_load_invocation_result_reads_only_that_invocation(tmp_path):
    current = _success("inv-current", 2)
    other = _error("inv-other", 1)
    _write_result(tmp_path, current)
    _write_result(tmp_path, other)
    loaded = load_invocation_result(tmp_path, current)
    assert loaded["invocation_id"] == "inv-current"
    assert loaded["reason_code"] == "success"


def test_load_invocation_result_missing_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_invocation_result(tmp_path, _error("inv-missing", 1))


def test_select_result_event_matches_exact_invocation_id():
    records = [
        {"event": "agent_result", "invocation_id": "inv-old", "reason_code": "timeout"},
        {"event": "tool_use", "invocation_id": "inv-current"},
        {"event": "agent_result", "invocation_id": "inv-current", "reason_code": "provider_auth"},
    ]
    chosen = select_result_event(records, "inv-current")
    assert chosen["reason_code"] == "provider_auth"
    assert select_result_event(records, "inv-absent") is None


# --- single increment / reset ------------------------------------------------

def test_failure_increments_once_per_new_invocation():
    breaker = _breaker(limit=5)
    breaker.record(_error("inv-1", 1))
    breaker.record(_error("inv-2", 2))
    assert breaker.count == 2
    assert breaker.last_invocation_id == "inv-2"
    assert breaker.last_reason_code == "provider_auth"


def test_duplicate_result_for_same_invocation_does_not_double_count():
    breaker = _breaker(limit=5)
    result = _error("inv-1", 1)
    breaker.record(result)
    breaker.record(result)  # duplicate finalization for the same invocation
    assert breaker.count == 1


def test_success_resets_count():
    breaker = _breaker(limit=5)
    breaker.record(_error("inv-1", 1))
    breaker.record(_error("inv-2", 2))
    breaker.record(_success("inv-3", 3))
    assert breaker.count == 0
    assert breaker.last_reason_code == "success"


# --- historical-result isolation ---------------------------------------------

def test_historical_result_on_disk_does_not_leak_into_current_count(tmp_path):
    # A prior invocation left an error result behind; the current invocation
    # succeeded. Loading by the current identity (not the latest-by-mtime file)
    # yields success, so the breaker resets rather than counting the stale error.
    _write_result(tmp_path, _error("inv-old", 1))
    current = _success("inv-new", 2)
    _write_result(tmp_path, current)

    breaker = _breaker(limit=2)
    breaker.record(_error("inv-old", 1))       # earlier failing pass
    breaker.record(load_invocation_result(tmp_path, current))
    assert breaker.count == 0


# --- concurrent-feature isolation --------------------------------------------

def test_result_from_a_different_feature_is_rejected():
    breaker = _breaker(limit=2, feature="feature-a")
    with pytest.raises(ValueError):
        breaker.record(_error("inv-x", 1, feature="feature-b"))
    assert breaker.count == 0


@pytest.mark.parametrize("mismatch", [
    {"run_id": "run-2"},
    {"phase": 2},
    {"attempt": 2},
])
def test_result_from_a_different_scope_is_rejected(mismatch):
    breaker = _breaker(limit=2)
    with pytest.raises(ValueError):
        breaker.record(_error("inv-x", 1, **mismatch))
    assert breaker.count == 0


# --- stop before N+1 ---------------------------------------------------------

def test_halts_exactly_at_limit_before_next_invocation():
    breaker = _breaker(limit=2)
    assert breaker.should_halt() is False
    breaker.record(_error("inv-1", 1))
    assert breaker.should_halt() is False          # one failure: keep going
    breaker.record(_error("inv-2", 2))
    assert breaker.should_halt() is True           # limit reached: no pass 3


def test_duplicate_at_limit_minus_one_does_not_trip_breaker():
    breaker = _breaker(limit=2)
    result = _error("inv-1", 1)
    breaker.record(result)
    breaker.record(result)                          # duplicate, still count 1
    assert breaker.should_halt() is False


# --- kills are accounted by class, not by is_error ---------------------------
#
# A kill reaches the Prime path as a `timeout` reason code with is_error true —
# the same shape whatever ended the pass. Counting on that flag alone is what
# made three productive passes killed at a 90-minute ceiling look like three
# crashes (semantic-router-sovereign phase 15, 2026-09-11) and put
# SPECSTRIDE_PROPOSER_MAX_ERRORS=30 into a real .env. The class decides the counter:
# budget → the cap breaker (exit 10), everything else → the error breaker.

def test_kill_reasons_classify_exactly_as_the_legacy_ladder_does():
    assert classify_kill("hard_cap") == "budget"
    assert classify_kill("repeat_stall") == "futility"
    assert classify_kill("progress_stall") == "futility"
    assert classify_kill("idle_timeout") == "hang"
    # Unknown reasons fail SAFE: an agent error, as every reason was before.
    assert classify_kill("something_new") == "futility"
    # No kill at all is not a class.
    assert classify_kill(None) is None and classify_kill("") is None


def test_a_budget_kill_is_not_an_agent_error():
    breaker = _breaker(limit=2)
    breaker.record(_killed("inv-1", 1))
    breaker.record(_killed("inv-2", 2))
    breaker.record(_killed("inv-3", 3))
    assert breaker.count == 0, "a cap kill must never reach the error breaker"
    assert breaker.should_halt() is False
    assert breaker.cap_count == 3


def test_a_budget_kill_neither_increments_nor_resets_the_error_count():
    """It is neither a failing pass nor a clean one, so the error streak is left
    exactly as it was — the legacy ladder's behaviour, mirrored."""
    breaker = _breaker(limit=3)
    breaker.record(_error("inv-1", 1))
    breaker.record(_killed("inv-2", 2))
    assert breaker.count == 1
    breaker.record(_error("inv-3", 3))
    assert breaker.count == 2


def test_the_cap_breaker_halts_exactly_at_its_own_limit():
    breaker = _breaker(limit=2)
    breaker.cap_limit = 3
    for iteration in (1, 2):
        breaker.record(_killed("inv-%d" % iteration, iteration))
        assert breaker.should_halt_cap() is False
    breaker.record(_killed("inv-3", 3))
    assert breaker.should_halt_cap() is True
    assert breaker.should_halt() is False, "the two breakers are independent"


def test_an_unconfigured_cap_limit_never_halts():
    breaker = _breaker(limit=2)            # cap_limit defaults to 0
    for iteration in range(1, 6):
        breaker.record(_killed("inv-%d" % iteration, iteration))
    assert breaker.cap_count == 5 and breaker.should_halt_cap() is False


def test_a_duplicate_budget_kill_does_not_double_count():
    breaker = ConsecutiveErrorBreaker(
        run_id="run-1", feature="feature-a", role="proposer", phase=1,
        attempt=1, limit=2, cap_limit=2)
    result = _killed("inv-1", 1)
    breaker.record(result)
    breaker.record(result)
    assert breaker.cap_count == 1 and breaker.should_halt_cap() is False


@pytest.mark.parametrize("following", ["error", "success"])
def test_an_error_or_a_clean_pass_clears_the_cap_streak(following):
    """"Three caps in a row" has to mean in a row, or the breaker fires on an
    accumulation that nothing was actually wrong with."""
    breaker = ConsecutiveErrorBreaker(
        run_id="run-1", feature="feature-a", role="proposer", phase=1,
        attempt=1, limit=5, cap_limit=2)
    breaker.record(_killed("inv-1", 1))
    assert breaker.cap_count == 1
    breaker.record(_error("inv-2", 2) if following == "error" else _success("inv-2", 2))
    assert breaker.cap_count == 0
    breaker.record(_killed("inv-3", 3))
    assert breaker.cap_count == 1 and breaker.should_halt_cap() is False


@pytest.mark.parametrize("reason,expected", [
    ("idle_timeout", "hang"),
    ("repeat_stall", "futility"),
    ("progress_stall", "futility"),
])
def test_a_futility_or_hang_kill_is_still_an_agent_error(reason, expected):
    breaker = _breaker(limit=2)
    breaker.record(_killed("inv-1", 1, reason))
    assert breaker.last_kill_class == expected
    assert breaker.count == 1 and breaker.cap_count == 0


# --- the finalizer records the kill on the one durable result ----------------

def _invocation_dir(tmp_path, *, timed_out=True):
    """One invocation's artifacts exactly as proposer.sh leaves them after a
    watchdog kill: metadata written before launch, the producer's observed status
    (124 → timed_out), and no provider terminal at all — the kill severed it."""
    directory = tmp_path / "inv"
    directory.mkdir()
    (directory / "metadata.json").write_text(json.dumps({
        "run_id": "run-1", "feature": "feature-a", "role": "proposer",
        "backend": "prime", "phase": 1, "attempt": 1, "iteration": 1,
        "invocation_id": "inv-1", "observability_mode": "structured",
        "provider_format": "prime-v3",
    }))
    (directory / "producer.json").write_text(json.dumps({
        "producer_exit_code": None, "producer_signal": None, "parser_exit_code": 0,
        "timed_out": timed_out, "launch_failed": False, "duration_ms": 3000,
    }))
    return directory


def test_finalizing_a_killed_pass_keeps_its_result_and_records_the_class(tmp_path):
    """The durable artifact survives the kill and carries the classification, so
    `learn.py summarize` reads a Prime budget kill exactly as it reads a legacy
    one — without re-deriving anything from a reason string."""
    directory = _invocation_dir(tmp_path)
    events = tmp_path / "events.jsonl"
    result, breaker = finalize_invocation.finalize(
        directory, events, tmp_path / "breaker.json", 2,
        kill_reason="hard_cap", cap_limit=3)

    assert result["kill_reason"] == "hard_cap"
    assert result["kill_class"] == "budget"
    assert result["reason_code"] == "timeout" and result["is_error"] is True
    assert json.loads((directory / "result.json").read_text())["kill_class"] == "budget"
    # The mirrored event carries it too, so a consumer never needs the artifact.
    emitted = [json.loads(line) for line in events.read_text().splitlines()]
    assert [e["event"] for e in emitted] == ["agent_result"]
    assert emitted[0]["kill_class"] == "budget"
    # Charged to the cap breaker, and only to it.
    assert breaker.cap_count == 1 and breaker.count == 0


def test_the_persisted_state_carries_both_counters(tmp_path):
    directory = _invocation_dir(tmp_path)
    state = tmp_path / "breaker.json"
    finalize_invocation.finalize(directory, tmp_path / "events.jsonl", state, 2,
                                 kill_reason="hard_cap", cap_limit=3)
    persisted = json.loads(state.read_text())
    assert persisted["cap_count"] == 1
    assert persisted["count"] == 0
    assert persisted["last_kill_class"] == "budget"


def test_finalizing_without_a_kill_is_unchanged(tmp_path):
    """A caller that knows nothing about kills gets exactly the old behaviour."""
    directory = _invocation_dir(tmp_path, timed_out=False)
    (directory / "provider-terminal.json").write_text(json.dumps(
        {"provider_terminal": {"status": "success", "stop_reason": "end_turn"}}))
    (directory / "producer.json").write_text(json.dumps({
        "producer_exit_code": 0, "parser_exit_code": 0, "timed_out": False,
        "launch_failed": False, "duration_ms": 10,
    }))
    result, breaker = finalize_invocation.finalize(
        directory, tmp_path / "events.jsonl", tmp_path / "breaker.json", 2)
    assert "kill_reason" not in result and "kill_class" not in result
    assert result["is_error"] is False
    assert breaker.count == 0 and breaker.cap_count == 0
