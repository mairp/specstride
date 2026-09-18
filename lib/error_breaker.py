"""Exact, current-invocation consecutive-error breaker for the proposer loop.

The proposer must stop predictably on repeated failing passes. The legacy
breaker tail-scanned the whole run event log for the last ``agent_result``,
which could not distinguish the current invocation from a historical one and
could not isolate concurrent features. This module replaces that with exact
per-invocation result consumption (data-model ``ConsecutiveErrorState``):

* results are located by identity-derived path, never latest-by-mtime;
* each failing invocation increments the count exactly once (a duplicate
  finalization for the same invocation id never double-counts);
* a successful invocation resets the count to zero;
* a result whose scope (run/feature/role/phase/attempt) does not match the
  breaker is rejected, isolating concurrent features and scopes;
* the breaker halts exactly at the configured limit, before the next pass.

A watchdog kill is accounted by CLASS, exactly as the legacy tail-scan ladder in
``proposer.sh`` accounts it (design §4.2): a ``budget`` kill (``hard_cap``) says
only that the work did not fit the pass, so it feeds a SECOND bounded counter
(``cap_count`` / ``SPECSTRIDE_PROPOSER_MAX_CAPS``, exit 10) and leaves the error
count untouched in both directions — it is neither a failing pass nor a clean
one. A ``futility``/``hang`` kill is an agent error and counts here as before.
One invocation is therefore counted by exactly one of the two breakers, never
both and never neither.
"""

import json
from pathlib import Path


_SCOPE_FIELDS = ("run_id", "feature", "role", "phase", "attempt")

# The class of each watchdog kill reason. Mirrors ``proposer.sh``'s
# ``watchdog_kill_class`` and ``lib/learn.py``'s ``KILL_CLASS`` so the Prime path
# and the legacy path classify one kill identically (and ``learn.py summarize``
# reads the same outcome from either). An unknown reason is treated as futility:
# fail safe, counting it as an agent error exactly as every reason did before the
# split.
KILL_CLASS = {
    "hard_cap": "budget",
    "repeat_stall": "futility",
    "progress_stall": "futility",
    "idle_timeout": "hang",
}
BUDGET_KILL_CLASS = "budget"


def classify_kill(reason):
    """Class of one watchdog kill reason, or None when the pass was not killed."""
    if not reason:
        return None
    return KILL_CLASS.get(reason, "futility")


def resolve_result_path(root, result):
    """Identity-derived location of one invocation's ``result.json``.

    Feature is intentionally not part of the path — the run/role/phase/attempt/
    iteration/invocation tuple already uniquely identifies the artifact, and
    cross-feature isolation is enforced by scope validation in the breaker.
    """
    return (
        Path(root)
        / str(result["run_id"])
        / str(result["role"])
        / f"phase-{result['phase']}"
        / f"attempt-{result['attempt']}"
        / f"iter-{result['iteration']}"
        / str(result["invocation_id"])
        / "result.json"
    )


def load_invocation_result(root, result):
    """Read exactly the result for ``result``'s identity (never by mtime)."""
    path = resolve_result_path(root, result)
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text())


def select_result_event(records, invocation_id):
    """Return the ``agent_result`` event for exactly ``invocation_id``.

    Scans in order and returns the matching terminal event, or ``None`` when no
    result event for that invocation is present.
    """
    for record in records:
        if (
            record.get("event") == "agent_result"
            and record.get("invocation_id") == invocation_id
        ):
            return record
    return None


class ConsecutiveErrorBreaker:
    """Track consecutive failing invocations for one proposer scope."""

    def __init__(self, *, run_id, feature, role, phase, attempt, limit,
                 cap_limit=0):
        self.scope = {
            "run_id": run_id,
            "feature": feature,
            "role": role,
            "phase": phase,
            "attempt": attempt,
        }
        self.limit = limit
        self.count = 0
        # The second, independent budget counter: consecutive passes killed at
        # the absolute pass ceiling. Zero (the default) means "not configured",
        # which never halts — callers that do not care are unchanged.
        self.cap_limit = int(cap_limit or 0)
        self.cap_count = 0
        self.last_invocation_id = None
        self.last_reason_code = None
        self.last_kill_class = None

    def _check_scope(self, result):
        for field in _SCOPE_FIELDS:
            if result.get(field) != self.scope[field]:
                raise ValueError(
                    f"result {field}={result.get(field)!r} does not match "
                    f"breaker scope {self.scope[field]!r}"
                )

    def record(self, result):
        """Fold one terminal invocation result into the consecutive counts.

        Exactly one counter moves per invocation. A budget kill increments the
        cap count and leaves the error count alone — not incremented (the pass
        did not fail) and not reset (it was not a clean pass either), which is
        what the legacy ladder does. Anything else moves the error count and
        clears the cap streak, so "three caps in a row" means three in a row.
        """
        self._check_scope(result)
        invocation_id = result.get("invocation_id")
        if invocation_id is not None and invocation_id == self.last_invocation_id:
            # Duplicate finalization for the same invocation — never re-count.
            return
        self.last_invocation_id = invocation_id
        self.last_reason_code = result.get("reason_code")
        kill_class = result.get("kill_class") or classify_kill(result.get("kill_reason"))
        self.last_kill_class = kill_class
        if kill_class == BUDGET_KILL_CLASS:
            self.cap_count += 1
            return
        if result.get("is_error"):
            self.count += 1
        else:
            self.count = 0
        self.cap_count = 0

    def should_halt(self):
        """True once the consecutive-error count has reached the limit."""
        return self.count >= self.limit

    def should_halt_cap(self):
        """True once the consecutive CAP count has reached its own limit.

        A separate decision from ``should_halt`` on purpose: the remedy differs
        (make the work fit a pass vs. fix the failing pass), and so does the
        proposer's exit code — 10, not 7.
        """
        return self.cap_limit > 0 and self.cap_count >= self.cap_limit
