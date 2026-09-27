#!/usr/bin/env python3
"""Replay golden stream inputs through the stream tap and pin its bytes (stdlib only).

Each golden input is fed to ``lib/agent_stream.py`` on stdin, twice: once in
``legacy`` mode (a bare contextless tap run) and once in ``correlated`` mode (the
full invocation context plus the terminal sidecar). The tap's events file, stdout
and sidecar bytes are captured, with the two wall-clock keys (``ts`` and ``time``)
masked to fixed tokens, and written as ``<name>.<mode>.{events,stdout,sidecar}.golden``
next to the input's category directory.

Regenerate with::

    python3 lib/fixtures/stream-golden/capture.py
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent.parent
AGENT_STREAM = REPO_ROOT / "lib" / "agent_stream.py"

CATEGORIES = {
    "claude": {"provider_format": "claude", "backend": "claude"},
    "prime-v3": {"provider_format": "prime-v3", "backend": "prime"},
}

# The tap stamps every event with wall-clock values under these two keys; goldens
# replace their values with fixed same-shape tokens so replays are byte-stable.
_MASKS = (
    (re.compile(r'"ts":"[^"]*"'), '"ts":"0.000000"'),
    (re.compile(r'"time":"[^"]*"'), '"time":"1970-01-01T00:00:00+0000"'),
)

_CORRELATED_ARGS = (
    "--feature", "default", "--role", "proposer", "--phase", "1",
    "--attempt", "1", "--iteration", "1", "--invocation-id", "inv-golden",
    "--expected-evidence", "/golden/GATE1-EVIDENCE.md",
    "--terminal-sidecar", "{tmp}/provider-terminal.json",
)


def _golden_env():
    """The tap's environment with every SPECSTRIDE_* variable removed."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("SPECSTRIDE_")}
    return env


def replay(input_path, provider_format, mode):
    """Run the tap once over ``input_path`` and return its masked bytes.

    Returns ``{"events": bytes, "stdout": bytes}`` in legacy mode and additionally
    ``"sidecar"`` in correlated mode. ``provider_format`` is the ``--provider-format``
    value (``claude`` or ``prime-v3``); ``mode`` is ``"legacy"`` or ``"correlated"``.
    """
    category = CATEGORIES[provider_format]
    backend = category["backend"]
    with tempfile.TemporaryDirectory(prefix="stream-golden-") as tmp:
        events_path = os.path.join(tmp, "events.jsonl")
        args = [sys.executable, str(AGENT_STREAM),
                "--events", events_path, "--run-id", "run-golden",
                "--task", "golden", "--backend", backend,
                "--provider-format", provider_format]
        if mode == "correlated":
            args += [part.format(tmp=tmp) for part in _CORRELATED_ARGS]
        stdin = Path(input_path).read_bytes()
        proc = subprocess.run(
            args, input=stdin, cwd=str(REPO_ROOT), env=_golden_env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        events = Path(events_path).read_bytes() if os.path.exists(events_path) else b""
        result = {
            "events": _mask(events),
            "stdout": proc.stdout,
        }
        if mode == "correlated":
            sidecar = os.path.join(tmp, "provider-terminal.json")
            result["sidecar"] = (
                Path(sidecar).read_bytes() if os.path.exists(sidecar) else b"")
        return result


def _mask(events):
    """Replace every ``ts``/``time`` value with its fixed token, byte-wise."""
    text = events.decode("utf-8")
    for pattern, token in _MASKS:
        text = pattern.sub(token, text)
    return text.encode("utf-8")


def golden_paths():
    """Yield (input_path, provider_format, mode, golden_prefix) for every golden.

    Claude inputs live in ``claude/``; Prime inputs are read in place from
    ``lib/fixtures/prime-v3/`` (never copied). Goldens for both categories are
    written into this directory's own ``claude/`` and ``prime-v3/`` folders.
    """
    inputs = {"claude": sorted((HERE / "claude").glob("*.jsonl")),
              "prime-v3": sorted((REPO_ROOT / "lib" / "fixtures" / "prime-v3").glob("*.jsonl"))}
    for mode in ("legacy", "correlated"):
        for category, spec in CATEGORIES.items():
            directory = HERE / category
            for input_path in inputs[category]:
                yield (
                    input_path, spec["provider_format"], mode,
                    directory / ("%s.%s" % (input_path.stem, mode)),
                )


def main():
    count = 0
    for input_path, provider_format, mode, prefix in golden_paths():
        captured = replay(input_path, provider_format, mode)
        directory = prefix.parent
        directory.mkdir(parents=True, exist_ok=True)
        base = prefix.name
        (directory / ("%s.events.golden" % base)).write_bytes(captured["events"])
        (directory / ("%s.stdout.golden" % base)).write_bytes(captured["stdout"])
        if mode == "correlated":
            (directory / ("%s.sidecar.golden" % base)).write_bytes(captured["sidecar"])
        count += 1
        print("captured %s [%s]" % (prefix, mode))
    print("wrote goldens for %d streams x 2 modes" % count)


if __name__ == "__main__":
    main()
