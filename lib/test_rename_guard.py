#!/usr/bin/env python3
"""Rename guard: Specstride was formerly Wiggum, and the old name must not creep back.

Walks every tracked file (`git ls-files`) and fails when one outside the
historical allowlist mentions the old name (case-insensitive), except at the
explicitly listed compatibility sites. The policy lives here, not in anyone's
memory: to add a site, add it below with the reason.
"""
import fnmatch
import os
import re
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OLD = re.compile("wiggum", re.IGNORECASE)

# Historical records: never rewritten, so they keep the old name.
HISTORICAL = [
    "INCIDENT-*.md",                  # incident write-ups
    "crash_incident",
    "reversed/*",                     # reverse-engineered spec of the pre-rename tree
    "roadmap/*",                      # dated notes, research, prompts, the naming decision
    "specs/*",                        # the completed 001 feature's Spec Kit record + run scripts
    ".ralph/*",                       # live loop state (never edited)
    ".codex-phase13-spec.patch",      # a recorded patch
    "resume-001.sh",                  # one-off scripts for runs whose state dir predates
    "swap-002-proposer-to-gpt5.sh",   # the rename (they work via the compatibility layer)
    "merge-main-into-002.sh",
    "untrack-specs-wiggum.sh",
    "lib/fixtures/*",                 # recorded agent transcripts (test data)
]

# Compatibility sites: whole files that exist to carry the old name.
COMPAT_FILES = [
    "wiggum",                              # deprecated command shim
    "wiggum-lib.sh",                       # deprecated library shim
    "lib/test_rename_guard.py",            # this policy
    "lib/test_specstride_cli.py",          # shim parity test
    "lib/test_specstride_env_compat.py",   # legacy env-name tests
    "lib/test_specstride_state_dir.py",    # legacy state-dir tests
    "lib/test_specstride_spec.py",         # asserts the old module is gone
]

# Compatibility sites: single lines, per file (regexes matched against the line).
COMPAT_LINES = {
    "specstride-lib.sh": [
        r'^LEGACY_STATE_DIRNAME="\.wiggum"$',      # state-dir fallback
        r'^LEGACY_ENV_PREFIX="WIGGUM_"$',          # env mapping
        r"\(Specstride was formerly Wiggum\)",
    ],
    "lib/specstride_env.py": [
        r'^LEGACY_STATE_DIRNAME = "\.wiggum"$',    # state-dir fallback (python mirror)
        r'^LEGACY_ENV_PREFIX = "WIGGUM_"$',        # env mapping (python mirror)
        r"^Specstride was formerly Wiggum\.",
    ],
    ".gitignore": [
        r"^(\*\*/)?\.wiggum/$",                     # legacy state dirs stay ignored
        r"^\.wiggum-autonomy-smoke$",
        r"^# Legacy state dir \(Specstride was formerly Wiggum\)",
    ],
    "README.md": [
        r"\(formerly Wiggum\)\. From specs to tested code\.",
    ],
    "telemetry/docker-compose.yml": [
        r"^name: wiggum-telemetry$",               # compose project name keys the volumes
        r'"\$\{SPECSTRIDE_[A-Z_]+_PORT:-\$\{WIGGUM_[A-Z_]+_PORT:-\d+\}\}:\d+"',
    ],
}

# README section that documents the migration (lines until the next "## ").
README_MIGRATION_HEADING = "## Migrating from Wiggum"

# Allowed anywhere: the character the Ralph loop is named after, and the path of
# a historical design doc that living files cite.
GLOBAL_OK = [
    re.compile(r"Ralph Wiggum"),
    re.compile(r"02-wiggum-loop-design\.md"),
]


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                         check=True).stdout.decode()
    return [f for f in out.split("\0") if f]


def is_historical(path):
    return any(fnmatch.fnmatch(path, pat) for pat in HISTORICAL)


def readme_migration_lines(lines):
    allowed, inside = set(), False
    for i, line in enumerate(lines):
        if line.startswith("## "):
            inside = line.strip() == README_MIGRATION_HEADING
        if inside:
            allowed.add(i)
    return allowed


def offending_lines(path, text):
    lines = text.splitlines()
    per_file = [re.compile(p) for p in COMPAT_LINES.get(path, [])]
    section = readme_migration_lines(lines) if path == "README.md" else set()
    bad = []
    for i, line in enumerate(lines):
        if not OLD.search(line) or i in section:
            continue
        rest = line
        for rx in GLOBAL_OK:
            rest = rx.sub("", rest)
        if not OLD.search(rest):
            continue
        if any(rx.search(line) for rx in per_file):
            continue
        bad.append("%s:%d: %s" % (path, i + 1, line.strip()[:160]))
    return bad


def test_no_old_name_outside_the_allowlist():
    bad = []
    for path in tracked_files():
        if is_historical(path) or path in COMPAT_FILES:
            continue
        full = os.path.join(ROOT, path)
        if os.path.islink(full) or not os.path.isfile(full):
            continue
        with open(full, "rb") as fh:
            raw = fh.read()
        if b"\0" in raw:
            continue                               # binary
        bad += offending_lines(path, raw.decode("utf-8", errors="replace"))
    assert not bad, "old name reintroduced:\n" + "\n".join(bad)


def test_no_tracked_path_carries_the_old_name_outside_the_allowlist():
    bad = [p for p in tracked_files()
           if OLD.search(p) and not is_historical(p) and p not in COMPAT_FILES]
    assert not bad, bad


def test_every_allowlist_entry_still_matches_something():
    """A stale allowlist entry is policy drift: prune it when its site goes away."""
    files = tracked_files()
    for pat in HISTORICAL:
        assert any(fnmatch.fnmatch(f, pat) for f in files), pat
    for path in COMPAT_FILES:
        assert path in files, path
    for path, patterns in COMPAT_LINES.items():
        text = open(os.path.join(ROOT, path)).read().splitlines()
        for p in patterns:
            assert any(re.search(p, line) for line in text), (path, p)
    readme = open(os.path.join(ROOT, "README.md")).read()
    assert README_MIGRATION_HEADING + "\n" in readme


@pytest.mark.parametrize("line,ok", [
    ("export WIGGUM_HOME=/x", False),
    ("see .wiggum/features", False),
    ("the Ralph Wiggum portrait", True),
    ("design 02-wiggum-loop-design.md §3", True),
    ("Ralph Wiggum and wiggum run", False),
])
def test_guard_classifies_lines(line, ok):
    assert (offending_lines("some/living/file.md", line) == []) is ok
