#!/usr/bin/env python3
"""ralph_otel_spans.py — derive OTLP trace spans from the specstride event stream.

The event stream is flat (one JSON line per lifecycle/agent event) and every
shipper invocation is a short-lived process, so spans are reconstructed from
paired events and a tiny per-run state file:

  run_start ─┬─ phase_start ─┬─ proposer_start ─┬─ iter_start ─┬─ agent_tool …
             │               │  (attempt)       │              └─ iter_done|error|cap|no_progress
             │               │                  └─ reject | phase_done
             │               │                  ├─ verification_start … verification_passed|failed
             │               │                  ├─ critic_start … verdict
             │               └─ phase_done
             └─ run_end | run_stop (orchestrator-level, i.e. no `iter` field)

  * One trace per run: trace_id = SPECSTRIDE_TRACE_ID / event trace_id when it is
    32 hex chars, else sha256("specstride:" + run_id)[:32]. Deterministic, so the
    orchestrator, the proposer and the agent-stream tap all land in one trace.
  * Scope spans (run/phase/attempt/iter/verification/yield/long_job) are emitted
    when they CLOSE, with their real start time taken from the state file. Opening
    a scope that is already open closes it (and anything nested above it) first;
    closing a scope closes anything still nested above it.
  * Every other event becomes a point span under the innermost open scope, so a
    long phase shows its tool calls live even before the phase span itself lands.
    agent_tool spans last until the next event seen by the same process.

Same contract as the shippers: best-effort, stdlib only, never raises into the
caller. Disable with SPECSTRIDE_OTEL_TRACES=false.
"""
import os, re, json, time, hashlib, secrets

try:
    import fcntl
except ImportError:  # pragma: no cover — non-POSIX: run unlocked
    fcntl = None

SERVICE_NAME = "specstride"
SCOPE_NAME = "specstride.trace"
KIND_INTERNAL = 1
STATUS_ERROR = 2
ATTR_MAX = 512
STATE_TTL_SEC = 7 * 86400

OPENERS = {
    "run_start": "run",
    "phase_start": "phase",
    "proposer_start": "attempt",
    "iter_start": "iter",
    "verification_start": "verification",
    "critic_start": "critic",
    "yield_wait": "yield",
    "long_job_start": "long_job",
}
CLOSERS = {
    "run_end": "run",
    "run_stop": "run",
    "phase_done": "phase",
    "reject": "attempt",
    "iter_done": "iter",
    "iter_error": "iter",
    "iter_cap": "iter",
    "iter_no_progress": "iter",
    "verification_passed": "verification",
    "verification_failed": "verification",
    "verdict": "critic",
    "yield_resume": "yield",
    "yield_timeout": "yield",
    "long_job_ended": "long_job",
}
ERROR_EVENTS = {"iter_error", "verification_failed", "reject", "run_stop", "yield_timeout",
                "critic_malformed", "pass_killed", "yield_invalid", "plugin_install_failed"}
DROP_ATTRS = {"ts", "time", "event"}
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def enabled():
    return os.environ.get("SPECSTRIDE_OTEL_TRACES", "true").strip().lower() not in (
        "false", "0", "no", "off")


def _now_ns():
    return time.time_ns()


def _ts_ns(fields):
    v = fields.get("ts")
    try:
        return int(float(v) * 1e9) if v not in (None, "") else _now_ns()
    except (TypeError, ValueError):
        return _now_ns()


def trace_id_for(run_id, fields=None):
    for cand in (os.environ.get("SPECSTRIDE_TRACE_ID", ""), (fields or {}).get("trace_id", "")):
        cand = str(cand or "").strip().lower().replace("-", "")
        if _HEX32.match(cand) and cand != "0" * 32:
            return cand
    return hashlib.sha256(("specstride:%s" % run_id).encode("utf-8")).hexdigest()[:32]


def _span_id():
    return secrets.token_hex(8)


def _attr_value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if not isinstance(v, str):
        v = json.dumps(v, default=str)
    return {"stringValue": v[:ATTR_MAX]}


def _attrs(d):
    return [{"key": k, "value": _attr_value(v)} for k, v in d.items() if v is not None]


def _scope_name(kind, f):
    if kind == "run":
        return "run %s" % (f.get("feature") or f.get("task") or f.get("run_id") or "")
    if kind == "phase":
        title = f.get("title")
        return "phase %s: %s" % (f.get("phase", "?"), title) if title else "phase %s" % f.get("phase", "?")
    if kind == "attempt":
        return "attempt p%s#%s" % (f.get("phase", "?"), f.get("attempt", "?"))
    if kind == "iter":
        return "iter %s" % f.get("iter", "?")
    if kind == "verification":
        return "verification p%s" % f.get("phase", "?")
    if kind == "critic":
        return "critic p%s#%s" % (f.get("phase", "?"), f.get("attempt", "?"))
    return kind


def _point_name(event, f):
    if event in ("agent_tool", "tool_use") and f.get("tool"):
        return "tool %s" % f["tool"]
    return event


class SpanTracker:
    """Turns events into finished OTLP spans. One instance per shipper process."""

    def __init__(self, resource, state_dir=None):
        self.resource = {"service.name": SERVICE_NAME}
        for k, v in (resource or {}).items():
            if k != "service.name" and v:
                self.resource[k] = v
        self.state_dir = state_dir or os.environ.get("SPECSTRIDE_OTEL_STATE_DIR") or os.path.join(
            os.path.expanduser("~"), ".cache", "specstride", "otel")
        self._spans = []          # finished OTLP span dicts
        self._pending = None      # in-process open point span (agent_tool)

    # -- state ----------------------------------------------------------------
    def _path(self, run_id):
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(run_id))[:200] or "_"
        return os.path.join(self.state_dir, safe + ".json")

    def _with_state(self, run_id, fields, mutate):
        """Run mutate(state) -> bool(dirty) under an exclusive lock."""
        os.makedirs(self.state_dir, exist_ok=True)
        path = self._path(run_id)
        with open(path + ".lock", "a") as lk:
            if fcntl:
                fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                try:
                    with open(path) as fh:
                        state = json.load(fh)
                except (OSError, ValueError):
                    state = {}
                state.setdefault("trace_id", trace_id_for(run_id, fields))
                state.setdefault("stack", [])
                if mutate(state):
                    tmp = "%s.%d.tmp" % (path, os.getpid())
                    with open(tmp, "w") as fh:
                        json.dump(state, fh)
                    os.replace(tmp, path)
                return state
            finally:
                if fcntl:
                    fcntl.flock(lk, fcntl.LOCK_UN)

    def _sweep(self):
        cutoff = time.time() - STATE_TTL_SEC
        try:
            for name in os.listdir(self.state_dir):
                p = os.path.join(self.state_dir, name)
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
        except OSError:
            pass

    # -- span building ----------------------------------------------------------
    def _span(self, trace_id, span_id, parent, name, start, end, attrs, error=None):
        span = {
            "traceId": trace_id, "spanId": span_id, "name": name, "kind": KIND_INTERNAL,
            "startTimeUnixNano": str(start), "endTimeUnixNano": str(max(end, start)),
            "attributes": _attrs(attrs),
        }
        if parent:
            span["parentSpanId"] = parent
        if error:
            span["status"] = {"code": STATUS_ERROR, "message": str(error)[:ATTR_MAX]}
        return span

    def _close_from(self, state, idx, end, closer, closer_fields):
        """Close stack[idx:] innermost-first; the scope at idx gets the closer's verdict."""
        tid = state["trace_id"]
        while len(state["stack"]) > idx:
            e = state["stack"].pop()
            attrs = dict(e.get("attrs") or {})
            attrs["specstride.closed_by"] = closer
            error = None
            if len(state["stack"]) == idx:
                for k, v in closer_fields.items():
                    if k not in DROP_ATTRS and k not in attrs:
                        attrs[k] = v
                if closer in ERROR_EVENTS:
                    error = closer_fields.get("reason") or closer
                elif closer == "verdict" and str(closer_fields.get("result", "")).upper() != "APPROVED":
                    error = closer_fields.get("result") or "not approved"
            self._spans.append(self._span(tid, e["span_id"], e.get("parent"), e["name"],
                                          e["start_ns"], end, attrs, error))

    def _end_pending(self, end):
        if self._pending is not None:
            span, _ = self._pending
            span["endTimeUnixNano"] = str(max(end, int(span["startTimeUnixNano"])))
            self._spans.append(span)
            self._pending = None

    def on_event(self, event, fields):
        """Consume one event. Returns (trace_id, span_id) for log correlation, or None."""
        if not enabled() or not event:
            return None
        fields = dict(fields or {})
        run_id = fields.get("run_id") or os.environ.get("SPECSTRIDE_RUN_ID")
        if not run_id:
            return None
        ts = _ts_ns(fields)
        self._end_pending(ts)
        attrs = {k: v for k, v in fields.items() if k not in DROP_ATTRS and v is not None}

        kind_open = OPENERS.get(event)
        kind_close = CLOSERS.get(event)
        # A proposer-level run_stop carries `iter`; only the orchestrator stops the run.
        if event == "run_stop" and "iter" in fields:
            kind_close = None
        result = {}

        def mutate(state):
            stack = state["stack"]
            if kind_open:
                for i, e in enumerate(stack):
                    if e["type"] == kind_open:
                        self._close_from(state, i, ts, "superseded", {})
                        break
                sid = _span_id()
                stack.append({"type": kind_open, "span_id": sid,
                              "parent": stack[-1]["span_id"] if stack else None,
                              "name": _scope_name(kind_open, fields), "start_ns": ts,
                              "attrs": dict(attrs, **{"specstride.scope": kind_open})})
                result["span"] = sid
                return True
            if kind_close:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i]["type"] == kind_close:
                        result["span"] = stack[i]["span_id"]
                        self._close_from(state, i, ts, event, fields)
                        return True
            # Point span under the innermost open scope (or a closer with no opener).
            sid = _span_id()
            span = self._span(state["trace_id"], sid, stack[-1]["span_id"] if stack else None,
                              _point_name(event, fields), ts, ts,
                              dict(attrs, **{"specstride.event": event}),
                              (fields.get("reason") or event) if event in ERROR_EVENTS else None)
            if event in ("agent_tool", "tool_use"):
                self._pending = (span, sid)
            else:
                self._spans.append(span)
            result["span"] = sid
            return False

        if event == "run_start":
            self._sweep()
        state = self._with_state(run_id, fields, mutate)
        return state["trace_id"], result.get("span")

    # -- emit -----------------------------------------------------------------
    def has_spans(self):
        return bool(self._spans or self._pending)

    def drain(self):
        """Return an OTLP /v1/traces payload for everything finished, or None."""
        self._end_pending(_now_ns())
        if not self._spans:
            return None
        spans, self._spans = self._spans, []
        return {"resourceSpans": [{
            "resource": {"attributes": _attrs(self.resource)},
            "scopeSpans": [{"scope": {"name": SCOPE_NAME}, "spans": spans}],
        }]}
