#!/usr/bin/env python3
"""Tell a reverse-guard failure the attempt caused from one its environment caused.

The reverse source guard (`reverse.py guard`, the fixed-argv `guard-<n>`
verification command) fails when anything outside the output and state dirs
changed during an attempt. Usually the proposer did it. Sometimes something
else did — an operator's concurrent Claude Code session or a plugin rewriting
its own state under the workdir (issue #109) — and then rejecting the phase
only teaches the proposer to "repair" files it never wrote (it brute-forced
hash preimages and, on a diagnostician hint, ran `rm -rf` on another tool's
state).

`classify` answers `infra` only when ALL of these hold, and `agent` otherwise
(the conservative answer keeps today's reject path):

- every failing command of the verification evidence is a `guard-<n>` command;
- every problem it reports names a path (changed/added/deleted, or a
  `git status` entry) — a moved HEAD, a bare fingerprint mismatch or
  verification output under testautomation/ is never infra;
- the attempt's stream was recorded (an `agent_init` follows the attempt's
  `proposer_start`/`accelerator_start` in events.jsonl), so "untouched" means
  something; and
- no `agent_tool` event of that attempt names the path or one of its parent
  directories in its `target`/`targets` (a Bash command line counts by
  substring).

CLI: `guard_infra.py classify --evidence E --events EV --phase N --attempt A`
prints `infra<TAB>path,path` (exit 0) or `agent<TAB>reason` (exit 1). Any error
is `agent` — this can never turn a real failure into a pass, only stop a run.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

_PATH_PROBLEM = re.compile(r"^guard: (changed|added|deleted): (.+)$")
_STATUS_PROBLEM = re.compile(r"^guard: git status: (.{2}) (.+)$")
_IGNORED = ("guard: ok", "guard: FAILED")
_START_EVENTS = ("proposer_start", "accelerator_start")


def _norm(path: str) -> str:
    path = path.strip().strip('"')
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def guard_paths(evidence: dict):
    """(paths, reason): the paths the failing guard named, or None + why not."""
    commands = evidence.get("commands") if isinstance(evidence, dict) else None
    if not isinstance(commands, list):
        return None, "no command evidence"
    failed = [c for c in commands if isinstance(c, dict) and not c.get("passed")]
    if not failed:
        return None, "no failing command"
    paths = []
    for command in failed:
        name = str(command.get("declaredId") or command.get("commandId") or "")
        if not name.startswith("guard-"):
            return None, "a non-guard command failed (%s)" % name
        for line in str(command.get("stdout") or "").splitlines():
            line = line.rstrip()
            if not line or line.startswith(_IGNORED):
                continue
            match = _PATH_PROBLEM.match(line) or _STATUS_PROBLEM.match(line)
            if not match:
                return None, "guard problem without a path: %s" % line
            path = _norm(match.group(2))
            if " (verification output landed" in path or path.startswith("testautomation"):
                return None, "verification output in testautomation/"
            if " -> " in path:  # a rename names two paths
                paths.extend(_norm(p) for p in path.split(" -> "))
            else:
                paths.append(path)
    if not paths:
        return None, "the guard named no path"
    return sorted(set(paths)), ""


def attempt_tools(lines, phase: str, attempt: str):
    """(targets, recorded) for the newest start of phase/attempt in events.jsonl."""
    events = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            events.append(record)
    start = None
    for index, record in enumerate(events):
        if record.get("event") in _START_EVENTS and str(record.get("phase")) == phase \
                and str(record.get("attempt")) == attempt:
            start = index
    if start is None:
        return [], False
    targets, recorded = [], False
    for record in events[start + 1:]:
        event = record.get("event")
        if event in _START_EVENTS:
            break
        if event == "agent_init":
            recorded = True
        elif event == "agent_tool":
            if isinstance(record.get("target"), str):
                targets.append(record["target"])
            for item in record.get("targets") or []:
                if isinstance(item, str):
                    targets.append(item)
    return targets, recorded


def touched(path: str, targets) -> bool:
    parts = path.split("/")
    names = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    for target in targets:
        cleaned = _norm(target)
        for name in names:
            if cleaned == name or cleaned.startswith(name + "/") or name in target:
                return True
    return False


def classify(evidence: dict, event_lines, phase, attempt):
    """("infra", [paths]) or ("agent", reason)."""
    paths, reason = guard_paths(evidence)
    if paths is None:
        return "agent", reason
    targets, recorded = attempt_tools(event_lines, str(phase), str(attempt))
    if not recorded:
        return "agent", "the attempt's tool calls were not recorded"
    hit = [p for p in paths if touched(p, targets)]
    if hit:
        return "agent", "the attempt's tool calls touched %s" % ", ".join(hit)
    return "infra", paths


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    cl = sub.add_parser("classify")
    cl.add_argument("--evidence", required=True)
    cl.add_argument("--events", required=True)
    cl.add_argument("--phase", required=True)
    cl.add_argument("--attempt", required=True)
    ns = parser.parse_args(argv)
    try:
        with open(ns.evidence, "r", encoding="utf-8") as handle:
            evidence = json.load(handle)
        with open(ns.events, "r", encoding="utf-8", errors="replace") as handle:
            verdict, detail = classify(evidence, handle, ns.phase, ns.attempt)
    except Exception as error:  # noqa: BLE001 — undecidable means the normal reject path
        verdict, detail = "agent", "unclassifiable (%s)" % error
    if verdict == "infra":
        print("infra\t%s" % ",".join(detail))
        return 0
    print("agent\t%s" % detail)
    return 1


if __name__ == "__main__":
    sys.exit(main())
