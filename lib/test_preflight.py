"""Tests for lib/preflight.py — the non-fatal #109 workdir warnings."""

import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preflight  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preflight.py")


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _settings(base, data):
    _write(base / ".claude" / "settings.json", json.dumps(data))


def _proc(root, pid, ppid, argv, cwd):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "status").write_text("Name:\tx\nPPid:\t%d\n" % ppid)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    if cwd is not None:
        os.symlink(str(cwd), str(d / "cwd"))


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _git_repo(tmp_path / "work")
    proc = tmp_path / "proc"
    proc.mkdir()
    return home, work, proc


# ── plugin_unignored_writes ──────────────────────────────────────────────────

def test_autoharness_state_warns_without_enabled_plugin(env):
    home, work, _ = env
    _write(work / ".claude" / "autoharness" / "requests", "7")
    detail = preflight.check_plugin_writes(str(work), str(home), inherit_plugins=False)
    assert ".claude/autoharness/requests" in detail
    assert ".gitignore" in detail and "another session" in detail


def test_enabled_plugin_and_unignored_skill_warns(env):
    home, work, _ = env
    _settings(home, {"enabledPlugins": {"autoharness@autoharness": True, "x@y": False}})
    _write(work / ".claude" / "skills" / "s" / "SKILL.md", "x")
    detail = preflight.check_plugin_writes(str(work), str(home), inherit_plugins=True)
    assert "autoharness@autoharness" in detail and "x@y" not in detail
    assert "another session" not in detail


def test_no_plugin_and_no_state_dir_is_quiet(env):
    home, work, _ = env
    _write(work / ".claude" / "skills" / "s" / "SKILL.md", "x")
    assert preflight.check_plugin_writes(str(work), str(home), False) is None


def test_ignored_paths_are_quiet(env):
    home, work, _ = env
    _write(work / ".gitignore", ".claude/\n")
    _write(work / ".claude" / "autoharness" / "requests", "7")
    assert preflight.check_plugin_writes(str(work), str(home), False) is None


def test_missing_claude_dir_and_non_git_workdir(env, tmp_path):
    home, work, _ = env
    assert preflight.check_plugin_writes(str(work), str(home), False) is None
    plain = tmp_path / "plain"
    _write(plain / ".claude" / "autoharness" / "requests", "7")
    assert preflight.check_plugin_writes(str(plain), str(home), False) is None


def test_path_list_is_capped(env):
    home, work, _ = env
    for i in range(8):
        _write(work / ".claude" / "autoharness" / ("f%d" % i), "x")
    detail = preflight.check_plugin_writes(str(work), str(home), False)
    assert "8 untracked" in detail and "(+3 more)" in detail


# ── concurrent_claude_session ────────────────────────────────────────────────

def test_other_claude_in_workdir_warns(env):
    home, work, proc = env
    _settings(home, {"hooks": {"Stop": [{"hooks": []}]}})
    _proc(proc, 100, 1, ["bash"], "/")
    _proc(proc, 200, 100, ["/usr/bin/orchestrator"], work)  # self
    _proc(proc, 300, 1, ["/usr/local/bin/claude"], work)  # the other session
    detail = preflight.check_concurrent_session(str(work), str(home), 200, proc_root=str(proc))
    assert "pid 300" in detail and "reverse guard" in detail


def test_ancestor_and_descendant_claude_are_skipped(env):
    home, work, proc = env
    _settings(home, {"hooks": {"Stop": []}, "enabledPlugins": {"p@q": True}})
    _proc(proc, 50, 1, ["claude"], work)  # our parent session
    _proc(proc, 60, 50, ["bash", "orchestrator.sh"], work)  # self
    _proc(proc, 70, 60, ["node", "/opt/node_modules/@anthropic-ai/claude-code/cli.js"], work)
    detail = preflight.check_concurrent_session(str(work), str(home), 60, proc_root=str(proc))
    assert detail is None  # also no jsonl fallback: our ancestry owns this cwd


def test_node_claude_detected(env):
    home, work, proc = env
    _settings(work, {"enabledPlugins": {"p@q": True}})
    _proc(proc, 10, 1, ["sh"], "/")
    _proc(proc, 20, 1, ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], work)
    detail = preflight.check_concurrent_session(str(work), str(home), 10, proc_root=str(proc))
    assert "pid 20" in detail


def test_no_hooks_or_plugins_is_quiet(env):
    home, work, proc = env
    _proc(proc, 10, 1, ["sh"], "/")
    _proc(proc, 20, 1, ["claude"], work)
    assert preflight.check_concurrent_session(str(work), str(home), 10, proc_root=str(proc)) is None


def test_recent_session_log_is_a_may_be_hint(env):
    home, work, proc = env
    _settings(home, {"hooks": {"Stop": []}})
    _proc(proc, 10, 1, ["sh"], "/")
    folder = home / ".claude" / "projects" / preflight.project_slug(str(work))
    _write(folder / "abc.jsonl", "{}")
    detail = preflight.check_concurrent_session(str(work), str(home), 10, proc_root=str(proc))
    assert detail.startswith("a Claude Code session may be open") and "abc.jsonl" in detail
    old = time.time() - 3600
    os.utime(str(folder / "abc.jsonl"), (old, old))
    assert preflight.check_concurrent_session(str(work), str(home), 10, proc_root=str(proc)) is None


def test_project_slug():
    assert preflight.project_slug("/root/specstride") == "-root-specstride"
    assert preflight.project_slug("/root/.claude/x") == "-root--claude-x"


# ── run / CLI ────────────────────────────────────────────────────────────────

def test_probe_error_yields_no_finding(env, monkeypatch):
    home, work, proc = env

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(preflight, "check_plugin_writes", boom)
    assert preflight.run(str(work), str(home), self_pid=1, proc_root=str(proc)) == []


def test_cli_output_and_exit_zero(env):
    home, work, proc = env
    _write(work / ".claude" / "autoharness" / "requests", "7")
    out = subprocess.run(
        [sys.executable, SCRIPT, "--workdir", str(work), "--home", str(home),
         "--proc-root", str(proc), "--self-pid", "1"],
        stdout=subprocess.PIPE, text=True,
    )
    assert out.returncode == 0
    lines = out.stdout.splitlines()
    assert len(lines) == 1
    check, detail = lines[0].split("\t")
    assert check == "plugin_unignored_writes" and "autoharness" in detail


def test_cli_quiet_on_clean_workdir(env):
    home, work, proc = env
    out = subprocess.run(
        [sys.executable, SCRIPT, "--workdir", str(work), "--home", str(home),
         "--proc-root", str(proc)],
        stdout=subprocess.PIPE, text=True,
    )
    assert out.returncode == 0 and out.stdout == ""
