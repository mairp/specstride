#!/usr/bin/env python3
"""Non-fatal workdir preflight warnings (issue #109).

Two things outside Specstride can change a run's workdir behind the proposer's
back, and neither is visible from the run's own events:

- ``plugin_unignored_writes``: untracked, NOT ignored paths under
  ``<workdir>/.claude/`` while a Claude Code plugin is enabled (or the paths sit
  under a known plugin-state dir such as ``.claude/autoharness/``). A phase
  commit runs ``git add -A``, so they would land in it, and the reverse guard
  counts them as source changes.
- ``concurrent_claude_session``: another live Claude Code process whose cwd is
  the workdir, while user or project settings define hooks or enable plugins.
  The reverse guard can't tell that session's writes from the proposer's.

The orchestrator calls this once before ``run_start`` and turns each line into
a ``WARN:`` log line plus a ``preflight_warning`` event. Output is one line per
finding, ``check<TAB>detail``; the exit status is always 0. Every probe is
wrapped so an error yields no finding instead of breaking the run (Principle
V). Stdlib only (Principle VI).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

PLUGIN_STATE_DIRS = (".claude/autoharness/",)
MAX_PATHS = 5


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _settings_files(workdir, home):
    return [
        os.path.join(home, ".claude", "settings.json"),
        os.path.join(workdir, ".claude", "settings.json"),
        os.path.join(workdir, ".claude", "settings.local.json"),
    ]


def enabled_plugins(workdir, home):
    """Plugin ids set to true in user or project settings (later files win)."""
    state = {}
    for path in _settings_files(workdir, home):
        plugins = _load_json(path).get("enabledPlugins")
        if isinstance(plugins, dict):
            for name, on in plugins.items():
                state[name] = bool(on)
    return sorted(name for name, on in state.items() if on)


def hooks_defined(workdir, home):
    for path in _settings_files(workdir, home):
        hooks = _load_json(path).get("hooks")
        if hooks:
            return True
    return False


def unignored_claude_paths(workdir):
    """Untracked, not-ignored paths under .claude/ (git's ``??`` entries)."""
    if not os.path.isdir(os.path.join(workdir, ".claude")):
        return []
    try:
        out = subprocess.run(
            ["git", "-C", workdir, "status", "--porcelain", "--untracked-files=all", "--", ".claude"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    paths = []
    for line in out.stdout.splitlines():
        if line.startswith("?? "):
            path = line[3:].strip()
            if path.startswith('"') and path.endswith('"'):
                path = path[1:-1]
            paths.append(path)
    return paths


def check_plugin_writes(workdir, home, inherit_plugins):
    paths = unignored_claude_paths(workdir)
    if not paths:
        return None
    plugins = enabled_plugins(workdir, home)
    state = [p for p in paths if p.startswith(PLUGIN_STATE_DIRS)]
    if not plugins and not state:
        return None
    shown = ", ".join(paths[:MAX_PATHS]) + (" (+%d more)" % (len(paths) - MAX_PATHS)
                                             if len(paths) > MAX_PATHS else "")
    detail = "%d untracked, unignored path(s) under .claude/: %s" % (len(paths), shown)
    if plugins:
        detail += "; enabled plugin(s): %s" % ", ".join(plugins)
    detail += "; add them to .gitignore (phase commits run git add -A)"
    if not inherit_plugins:
        detail += ("; the claude proposer runs isolated from plugins, so the writer"
                   " is another session")
    return detail


# ── concurrent sessions ──────────────────────────────────────────────────────

def _read(path, binary=False):
    try:
        with open(path, "rb" if binary else "r") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _ppid(proc_root, pid):
    status = _read(os.path.join(proc_root, str(pid), "status"))
    if not status:
        return None
    match = re.search(r"^PPid:\s*(\d+)", status, re.M)
    return int(match.group(1)) if match else None


def _cmdline(proc_root, pid):
    raw = _read(os.path.join(proc_root, str(pid), "cmdline"), binary=True)
    if not raw:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _cwd(proc_root, pid):
    try:
        return os.path.realpath(os.readlink(os.path.join(proc_root, str(pid), "cwd")))
    except OSError:
        return None


def is_claude(argv):
    if not argv:
        return False
    if os.path.basename(argv[0]) == "claude":
        return True
    if os.path.basename(argv[0]).startswith("node"):
        return any(re.search(r"(^|/)(claude|@anthropic-ai/claude-code)(/|$)", arg)
                   or os.path.basename(arg) == "claude" for arg in argv[1:3])
    return False


def _pids(proc_root):
    try:
        return [int(name) for name in os.listdir(proc_root) if name.isdigit()]
    except OSError:
        return []


def _ancestors(proc_root, pid):
    seen = []
    while pid and pid > 1 and pid not in seen:
        seen.append(pid)
        pid = _ppid(proc_root, pid)
    return seen


def _descendants(proc_root, pid, pids):
    parents = {p: _ppid(proc_root, p) for p in pids}
    found = {pid}
    grew = True
    while grew:
        grew = False
        for child, parent in parents.items():
            if parent in found and child not in found:
                found.add(child)
                grew = True
    return found


def project_slug(workdir):
    return re.sub(r"[/.]", "-", os.path.realpath(workdir))


def check_concurrent_session(workdir, home, self_pid, proc_root="/proc", recent_min=10,
                             now=None):
    if not (hooks_defined(workdir, home) or enabled_plugins(workdir, home)):
        return None
    real = os.path.realpath(workdir)
    pids = _pids(proc_root)
    ancestors = set(_ancestors(proc_root, self_pid))
    family = ancestors | _descendants(proc_root, self_pid, pids)
    others = sorted(pid for pid in pids
                    if pid not in family and _cwd(proc_root, pid) == real
                    and is_claude(_cmdline(proc_root, pid)))
    tail = ("; its hooks can write under the workdir, and the reverse guard can't tell"
            " its writes from the proposer's")
    if others:
        return "another Claude Code session is open in %s (pid %s)%s" % (
            real, ", ".join(str(p) for p in others), tail)
    # Fallback hint: a recently written session log for this workdir. Skip it
    # when our own ancestry already has a claude in this cwd (that log is ours).
    if any(_cwd(proc_root, pid) == real and is_claude(_cmdline(proc_root, pid))
           for pid in ancestors):
        return None
    folder = os.path.join(home, ".claude", "projects", project_slug(real))
    now = time.time() if now is None else now
    try:
        recent = [name for name in os.listdir(folder) if name.endswith(".jsonl")
                  and now - os.path.getmtime(os.path.join(folder, name)) <= recent_min * 60]
    except OSError:
        return None
    if not recent:
        return None
    return ("a Claude Code session may be open in %s (session log written in the last %d min:"
            " %s)%s" % (real, recent_min, ", ".join(sorted(recent)[:3]), tail))


def run(workdir, home, inherit_plugins=False, self_pid=None, proc_root="/proc", recent_min=10):
    findings = []
    for check, probe in (
        ("plugin_unignored_writes", lambda: check_plugin_writes(workdir, home, inherit_plugins)),
        ("concurrent_claude_session", lambda: check_concurrent_session(
            workdir, home, self_pid if self_pid is not None else os.getppid(),
            proc_root=proc_root, recent_min=recent_min)),
    ):
        try:
            detail = probe()
        except Exception:  # noqa: BLE001 — a probe error is no finding (Principle V)
            detail = None
        if detail:
            findings.append((check, detail.replace("\t", " ").replace("\n", " ")))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--inherit-plugins", action="store_true")
    parser.add_argument("--home", default=os.path.expanduser("~"))
    parser.add_argument("--recent-min", type=int, default=10)
    parser.add_argument("--self-pid", type=int, default=None)
    parser.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    try:
        args = parser.parse_args(argv)
        for check, detail in run(args.workdir, args.home, args.inherit_plugins, args.self_pid,
                                 args.proc_root, args.recent_min):
            print("%s\t%s" % (check, detail))
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001
        sys.stderr.write("preflight: skipped (%s)\n" % error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
