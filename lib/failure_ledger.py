#!/usr/bin/env python3
"""Every failure a yielded job reported, and whether it is new or recurring.

The yield resume block shows a job's log as head + tail with the middle elided.
That is the right default for reading a log, and the wrong one for acting on it:
a 20-hour live chain prints its failures in the middle, and the agent that
resumes from it only sees what survived the elision. In project A 004
phase 15 (2026-09-25/27), one quickstart failure (`lab-acl` never created) was
reported by two consecutive walks. Neither resuming pass fixed it, and it was
only acted on once a human pointed at it.

A second thing the log cannot say on its own is whether a failure is a bug or a
flake. A step that fails in every job is a bug. A step that fails once in five
jobs is almost certainly noise. The loop had no memory across jobs, so every
failure looked equally urgent and the agent spent rounds chasing noise.

So, for each job the loop resumes from, this helper:
  * extracts EVERY failure line from the whole log (not just the shown slice),
    deduplicated by a normalised signature (timestamps, ids and numbers
    stripped) and capped so a runaway log cannot flood the prompt;
  * appends the job and its signatures to a per-feature ledger (JSON lines);
  * labels each failure NEW, or RECURRING with how many earlier jobs of this
    feature reported it, so persistent failures stand out from one-offs.

Usage:
  failure_ledger.py --log FILE --ledger FILE --job KEY [--max N] [--pattern RE]
Prints a Markdown section on stdout (empty when the log has no failures and
there is no history). Never fails the caller: any error prints nothing.
"""
import argparse
import json
import os
import re
import sys
import time

DEFAULT_PATTERN = (
    r"(?<![A-Za-z])(FAIL(?:ED|URE)?|ERROR)(?![A-Za-z])|Traceback \(most recent|"
    r"\bRC=[1-9][0-9]*\b|\bexit(?: code)? [1-9][0-9]*\b"
)

# What a signature ignores: the parts of a line that differ between two
# reports of the same failure.
_NOISE = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{8}T\d{6}Z\b"), "<ts>"),
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<id>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def signature(line):
    s = line.strip()
    for rx, repl in _NOISE:
        s = rx.sub(repl, s)
    return s[:300]


def scan(log_path, pattern):
    rx = re.compile(pattern)
    found = {}   # signature -> [example, count]
    matched = 0
    with open(log_path, errors="replace") as fh:
        for raw in fh:
            if not rx.search(raw):
                continue
            matched += 1
            sig = signature(raw)
            if sig in found:
                found[sig][1] += 1
            else:
                found[sig] = [raw.strip()[:240], 1]
    return found, matched


def load_ledger(path):
    jobs, seen = [], {}
    if not os.path.exists(path):
        return jobs, seen
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            key = rec.get("job")
            if rec.get("kind") == "job" and key not in jobs:
                jobs.append(key)
            elif rec.get("kind") == "failure":
                seen.setdefault(rec.get("sig"), set()).add(key)
    return jobs, seen


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--job", required=True)
    ap.add_argument("--max", type=int, default=150)
    ap.add_argument("--pattern", default=os.environ.get("SPECSTRIDE_FAILURE_PATTERN") or DEFAULT_PATTERN)
    a = ap.parse_args(argv)
    if not os.path.isfile(a.log):
        return 0
    found, matched = scan(a.log, a.pattern)
    jobs, seen = load_ledger(a.ledger)
    prior = [j for j in jobs if j != a.job]

    os.makedirs(os.path.dirname(os.path.abspath(a.ledger)), exist_ok=True)
    with open(a.ledger, "a") as fh:
        if a.job not in jobs:
            fh.write(json.dumps({"kind": "job", "job": a.job, "ts": int(time.time()),
                                 "log": a.log, "failures": len(found)}) + "\n")
            for sig in found:
                fh.write(json.dumps({"kind": "failure", "job": a.job, "sig": sig}) + "\n")

    if not found:
        if prior:
            print("\n### Every failure the job reported\nNone: no line of the log matched the failure pattern.")
        return 0

    rows = []
    for sig, (example, count) in found.items():
        earlier = len(seen.get(sig, set()) - {a.job})
        rows.append((earlier, sig, example, count))
    # Recurring first (most persistent on top): those are the bugs.
    rows.sort(key=lambda r: (-r[0], r[1]))
    print(f"\n### Every failure the job reported ({len(found)} distinct, {matched} matching lines in the whole log)")
    print("Read ALL of these, not just the log slice above. RECURRING failures, reported by earlier jobs of "
          "this feature too, are almost certainly real bugs. Fix those first. A NEW failure that is "
          "absent from every earlier job may be a flake: check it, but do not let it cost a whole re-run.")
    shown = 0
    for earlier, _sig, example, count in rows:
        if shown >= a.max:
            print(f"- ... {len(rows) - shown} more distinct failure(s) not shown; grep the log for the rest")
            break
        tag = f"RECURRING {earlier}/{len(prior)} earlier jobs" if earlier else "NEW"
        times = f" (x{count})" if count > 1 else ""
        print(f"- [{tag}]{times} {example}")
        shown += 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # never break the resume prompt over a ledger problem
        sys.exit(0)
