"""Keep the test suite hermetic with respect to telemetry.

Hosts that run specstride for real often export SPECSTRIDE_OTEL_*/LOKI_* (or the
legacy-prefixed equivalents) from their shell profile. Tests spawn orchestrator/proposer
subprocesses with a copy of os.environ, so without this every fixture run would be
shipped to the host's LIVE Loki/OTLP stack and show up in Grafana as a real run.
Tests that exercise telemetry set their own capture-server URLs explicitly.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from specstride_env import ENV_PREFIX, LEGACY_ENV_PREFIX  # noqa: E402

_TELEMETRY_VARS = (
    "TELEMETRY", "TELEMETRY_ENABLED", "LOKI_URL", "LOKI_PORT",
    "OTEL_ENABLED", "OTEL_URL", "OTEL_SHIP", "TRACE_ID",
)

for _prefix in (ENV_PREFIX, LEGACY_ENV_PREFIX):
    for _name in _TELEMETRY_VARS:
        os.environ.pop(_prefix + _name, None)
os.environ.pop("RALPH_OTEL_URL", None)
# Explicit "off" (not just unset): the orchestrator sources the checkout's .env, which
# may turn telemetry on; a caller-exported value beats .env, a --telemetry/--otel flag
# still beats both.
os.environ["SPECSTRIDE_TELEMETRY_ENABLED"] = "false"
os.environ["SPECSTRIDE_OTEL_ENABLED"] = "false"
# Span state files from fixture runs never touch the real ~/.cache/specstride/otel.
os.environ.setdefault("SPECSTRIDE_OTEL_STATE_DIR", tempfile.mkdtemp(prefix="specstride-otel-test-"))

# lib/fixtures/ holds test DATA, including whole fixture projects with their own
# test_*.py files (lib/fixtures/reverse/src-mini); they are never part of this suite.
collect_ignore_glob = ["fixtures/*"]
