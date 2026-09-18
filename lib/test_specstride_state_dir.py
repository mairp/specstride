#!/usr/bin/env python3
"""The per-workdir state directory and its legacy fallback.

Specstride was formerly Wiggum. A fresh workdir gets `.specstride/`; a workdir
that has only the legacy `.wiggum/` keeps using it in place (never moved or
renamed: a live run commits into it) with a one-line notice; a workdir that has
both uses `.specstride/`.

The run-level cases drive the real orchestrator end to end with a fake Prime
Agent (the same hermetic injection point test_orchestrator_verification.py uses),
so no model, network or credential is touched.
"""
import json
import os
import stat
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ORCHESTRATOR = os.path.join(ROOT, "orchestrator.sh")
SPECSTRIDE = os.path.join(ROOT, "specstride")
sys.path.insert(0, HERE)
import specstride_env  # noqa: E402

NEW = ".specstride"
LEGACY = ".wiggum"
NOTICE = "using legacy state dir"

SPEC = """# State dir regression

## Phase 1 — Only phase
### Acceptance criteria
- [ ] Create a durable phase-one result artifact
"""

# Plays both roles; as the proposer it writes the evidence file at whatever
# state-dir path the orchestrator's prompt names.
FAKE_PRIME = r"""#!/bin/bash
prompt="$(cat)"
if [[ " $* " == *" --no-tools "* ]]; then
  nonce="$(printf '%s\n' "$prompt" \
    | grep -oE "VERDICT [0-9a-f]{16}: APPROVED" | head -1 | awk '{print $2}' | tr -d ':')"
  printf 'Criterion review complete.\nVERDICT %s: APPROVED\n' "$nonce"
else
  rel="$(printf '%s\n' "$prompt" \
    | grep -oE '\.(specstride|wiggum)/features/[^ ]*/gates/GATE[0-9]+-EVIDENCE\.md' | head -1)"
  mkdir -p "$(dirname "$WORKDIR_ABS/$rel")"
  printf '# Evidence\nPhase work complete.\n' > "$WORKDIR_ABS/$rel"
fi
"""


def clean_env():
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("SPECSTRIDE_", "WIGGUM_"))}


def run_orchestrator(tmp_path, workdir):
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC)
    fake = tmp_path / "fake-prime-agent"
    fake.write_text(FAKE_PRIME)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = clean_env()
    env.update({
        "SPECSTRIDE_PRIME_AGENT_BIN": str(fake),
        "WORKDIR_ABS": str(workdir),
        "SPECSTRIDE_AGENT_STREAM": "false",
        "SPECSTRIDE_GIT_COMMITS": "off",
    })
    argv = ["/usr/bin/bash", ORCHESTRATOR, "--workdir", str(workdir),
            "--specs", str(spec), "--proposer", "prime", "--critic", "prime",
            "--verification", "off", "--max-iter", "1", "--feature", "sd",
            "--no-live"]
    return subprocess.run(argv, cwd=str(workdir), env=env, text=True,
                          capture_output=True, timeout=180, check=False)


def approved(workdir, name):
    return (workdir / name / "features" / "sd" / "gates" / "GATE1-APPROVED").is_file()


# ── end to end through the orchestrator ──────────────────────────────────────
def test_fresh_workdir_creates_specstride(tmp_path):
    wd = tmp_path / "work"
    wd.mkdir()
    r = run_orchestrator(tmp_path, wd)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert approved(wd, NEW)
    assert not (wd / LEGACY).exists()
    assert NOTICE not in r.stderr


def test_legacy_only_workdir_is_used_in_place_and_not_moved(tmp_path):
    wd = tmp_path / "work"
    legacy = wd / LEGACY
    (legacy / "features").mkdir(parents=True)
    marker = legacy / "pre-rename-marker"
    marker.write_text("keep me")
    inode = os.stat(legacy).st_ino

    r = run_orchestrator(tmp_path, wd)

    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert approved(wd, LEGACY)
    assert not (wd / NEW).exists()                 # nothing created beside it
    assert os.stat(legacy).st_ino == inode         # same directory, not moved
    assert marker.read_text() == "keep me"
    notices = [l for l in r.stderr.splitlines() if NOTICE in l]
    assert len(notices) == 1                       # once for the whole process tree
    for root, _dirs, files in os.walk(legacy):     # …not repeated by child logs either
        for fn in files:
            with open(os.path.join(root, fn), errors="replace") as fh:
                assert NOTICE not in fh.read(), os.path.join(root, fn)


def test_workdir_with_both_uses_specstride(tmp_path):
    wd = tmp_path / "work"
    (wd / LEGACY).mkdir(parents=True)
    (wd / NEW).mkdir()
    r = run_orchestrator(tmp_path, wd)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert approved(wd, NEW)
    assert not approved(wd, LEGACY)
    assert os.listdir(wd / LEGACY) == []
    assert NOTICE not in r.stderr


# ── read-only surfaces: the CLI and the python helper ────────────────────────
def _state_tree(wd, name, run_id):
    feat = wd / name / "features" / "default"
    feat.mkdir(parents=True)
    (wd / name / "events.jsonl").write_text(
        json.dumps({"event": "run_start", "run_id": run_id, "phases": "1",
                    "proposer": "p", "critic": "c"}) + "\n")


@pytest.mark.parametrize("layout,expect", [
    ((LEGACY,), "legacy-run"),
    ((NEW,), "new-run"),
    ((LEGACY, NEW), "new-run"),
])
def test_cli_events_reads_the_resolved_state_dir(tmp_path, layout, expect):
    wd = tmp_path / "proj"
    for name in layout:
        _state_tree(wd, name, "legacy-run" if name == LEGACY else "new-run")
    before = sorted(os.listdir(wd))
    p = subprocess.run(["bash", SPECSTRIDE, "events", "-w", str(wd), "--json"],
                       capture_output=True, text=True, env=clean_env(), timeout=30)
    assert p.returncode == 0, p.stderr
    assert expect in p.stdout
    assert sorted(os.listdir(wd)) == before        # read-only, nothing moved
    assert (NOTICE in p.stderr) == (layout == (LEGACY,))


def test_python_state_dirname(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(specstride_env.STATE_NOTICE_FLAG, raising=False)
    fresh, legacy, both = (tmp_path / n for n in ("fresh", "legacy", "both"))
    fresh.mkdir()
    (legacy / LEGACY).mkdir(parents=True)
    (both / LEGACY).mkdir(parents=True)
    (both / NEW).mkdir()
    assert specstride_env.state_dirname(str(fresh)) == NEW
    assert specstride_env.state_dirname(str(both)) == NEW
    assert specstride_env.state_dirname(str(legacy)) == LEGACY
    assert specstride_env.state_dirname(str(legacy)) == LEGACY
    assert capsys.readouterr().err.count(NOTICE) == 1
    assert specstride_env.state_dir(str(legacy)) == str(legacy / LEGACY)
    assert sorted(os.listdir(legacy)) == [LEGACY]


def test_bash_state_dirname(tmp_path):
    (tmp_path / "legacy" / LEGACY).mkdir(parents=True)
    (tmp_path / "fresh").mkdir()
    script = ('. "%s/specstride-lib.sh"; '
              'specstride_resolve_state_dir "%s/fresh"; echo "$STATE_BASENAME"; '
              'specstride_resolve_state_dir "%s/legacy"; echo "$STATE_BASENAME"; '
              'specstride_resolve_state_dir "%s/legacy"; echo "$STATE_BASENAME"; '
              'bash -c \'. "%s/specstride-lib.sh"; specstride_resolve_state_dir "%s/legacy"\''
              ) % (ROOT, tmp_path, tmp_path, tmp_path, ROOT, tmp_path)
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env=clean_env(), timeout=20)
    assert p.stdout.split() == [NEW, LEGACY, LEGACY]
    # once per process tree: the exported flag keeps the child bash quiet too
    assert p.stderr.count(NOTICE) == 1
