#!/usr/bin/env python3
"""CLI parity for observability state (US5 / SC-012).

Drives the real `specstride` bash front door over a sanitized fixture workdir and
asserts that the operator-facing surfaces — `status`, `events`, `watch` — expose
the per-invocation capability mode and degradation reason, and that all five
SC-012 facts (active observability mode, current phase, latest tool activity,
final pass outcome, configured sink failure) are present as explicit labeled
fields in BOTH the displayed output and the retained records (events.jsonl).

Everything here is host-independent: no real credentials, no real provider
payloads, no live network dependency for the assertions (the loki "events
accepted" claim is proven from the JSONL, not a TCP probe).
"""
import json
import os
import re
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPECSTRIDE = os.path.join(ROOT, "specstride")

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")

# A sanitized, self-contained event stream covering the full US5 journey:
# structured capture → live tool activity → explicit degradation → sink
# failure/acceptance → terminal pass outcome. Times are fixed, no /root paths,
# no thinking content, no real provider payloads.
EVENTS = [
    {"time": "2026-08-10T12:00:00+0000", "event": "run_start", "run_id": "r1",
     "phases": "7", "proposer": "prime:sol", "critic": "prime"},
    {"time": "2026-08-10T12:00:01+0000", "event": "agent_observability", "run_id": "r1",
     "mode": "structured", "reason": "Prime JSON schema v3 selected",
     "provider_format": "prime-v3", "role": "proposer",
     "supported_signals": "tool,text,evidence,result"},
    {"time": "2026-08-10T12:00:02+0000", "event": "phase_start", "run_id": "r1",
     "phase": "3", "total": "7", "title": "wire the presenter"},
    {"time": "2026-08-10T12:00:03+0000", "event": "agent_tool", "run_id": "r1",
     "tool": "IPython", "status": "progress", "summary": "running checks",
     "tool_id": "t1"},
    {"time": "2026-08-10T12:00:04+0000", "event": "agent_observability", "run_id": "r1",
     "mode": "degraded", "reason": "unknown schema version 9 — degraded parsing",
     "provider_format": "prime-v9", "role": "proposer"},
    {"time": "2026-08-10T12:00:05+0000", "event": "telemetry_delivery", "run_id": "r1",
     "sink": "otel", "status": "failed", "http_status": "503",
     "reason": "Receiver rejected batch"},
    {"time": "2026-08-10T12:00:06+0000", "event": "telemetry_delivery", "run_id": "r1",
     "sink": "loki", "status": "accepted", "batch_id": "loki-0001"},
    {"time": "2026-08-10T12:00:07+0000", "event": "agent_result", "run_id": "r1",
     "status": "error", "is_error": True, "reason": "credentials rejected",
     "source": "provider"},
    {"time": "2026-08-10T12:00:08+0000", "event": "run_end", "run_id": "r1",
     "outcome": "failed"},
]


@pytest.fixture
def workdir(tmp_path):
    """A minimal .specstride/ state tree for feature 'tf' with the US5 event stream."""
    wd = tmp_path / "proj"
    feat = wd / ".specstride" / "features" / "tf"
    feat.mkdir(parents=True)
    (wd / ".specstride" / "last-run.conf").write_text("FEATURE=tf\n")
    # Configured sinks — otel points at the discard port so the live probe can
    # never spuriously report it reachable; loki's acceptance is proven by the
    # retained delivery record, not by a probe.
    feat.joinpath("last-run.conf").write_text(
        "TELEMETRY=true\nLOKI_URL=http://127.0.0.1:3100\n"
        "OTEL=true\nOTEL_URL=http://127.0.0.1:9\n")
    ev = wd / ".specstride" / "events.jsonl"
    ev.write_text("".join(json.dumps(e) + "\n" for e in EVENTS))
    return wd


def run(sub, wd, *args, timeout=20):
    """Run `specstride <sub> -w <wd> ...`, return combined ANSI-stripped output."""
    cmd = ["bash", SPECSTRIDE, sub, "-w", str(wd), *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return ANSI.sub("", p.stdout + p.stderr)


def watch(wd, seconds=2):
    """Run `specstride watch` for a bounded window (card mode never exits on its own)."""
    cmd = ["timeout", str(seconds), "bash", SPECSTRIDE, "watch", "-w", str(wd)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 10)
    return ANSI.sub("", p.stdout + p.stderr)


# ── status: capability mode + degradation reason (T062) ──────────────────────
def test_status_exposes_observability_mode_and_degradation_reason(workdir):
    out = run("status", workdir)
    # The active/last capability mode must be a labeled field, not source-only.
    assert "observability" in out
    assert "degraded" in out
    # The stable, human-readable degradation reason must be visible verbatim.
    assert "unknown schema version 9" in out


def test_status_shows_latest_tool_activity(workdir):
    # SC-012 fact: latest tool activity is an explicit field in the display, not
    # filtered away as agent_* noise.
    out = run("status", workdir)
    assert "IPython" in out


def test_status_distinguishes_configured_accepted_and_never_collapses(workdir):
    out = run("status", workdir)
    # FR-036 / SC-012: telemetry state is escalating and honest — never a
    # collapsed "telemetry: true".
    assert "telemetry: true" not in out
    # loki acceptance is proven from the retained delivery record.
    assert "loki" in out and "accepted" in out


# ── events: capability transitions rendered in plain mode (T061) ─────────────
def test_events_stream_renders_capability_labels(workdir):
    out = run("events", workdir)
    # Plain-mode capability rendering uses the presenter's labels, so a reader
    # sees "observability structured" / "observability degraded", not just a raw
    # mode= key dump.
    assert "observability structured" in out
    assert "observability degraded" in out
    assert "unknown schema version 9" in out


# ── watch: capability transitions surfaced in the live card (T061) ───────────
def test_watch_card_surfaces_capability_transitions(workdir):
    out = watch(workdir)
    assert "observability structured" in out
    assert "observability degraded" in out


# ── SC-012: five facts as explicit labeled fields ────────────────────────────
def test_sc012_five_facts_present_in_status_display(workdir):
    """All five SC-012 facts appear in the displayed status without needing to
    read source: mode, current phase, latest tool activity, final pass outcome,
    and configured sink failure."""
    out = run("status", workdir)
    assert "observability" in out                    # active observability mode
    assert "phase" in out and "3" in out             # current phase
    assert "IPython" in out                          # latest tool activity
    assert "failed" in out                           # final pass outcome
    assert "otel" in out                             # configured sink failure


def test_sc012_five_facts_present_in_retained_records(workdir):
    """The retained events.jsonl carries the same five facts as labeled fields,
    so a reconstruction has display/record parity."""
    lines = [json.loads(l) for l in
             (workdir / ".specstride" / "events.jsonl").read_text().splitlines() if l.strip()]
    kinds = {e["event"] for e in lines}
    assert "agent_observability" in kinds            # mode
    assert any(e["event"] == "agent_observability" and "mode" in e for e in lines)
    assert any(e["event"] == "phase_start" and "phase" in e for e in lines)   # phase
    assert any(e["event"] == "agent_tool" for e in lines)                     # tool
    assert any(e["event"] == "agent_result" for e in lines)                   # outcome
    assert any(e["event"] == "telemetry_delivery" and e.get("status") == "failed"
               for e in lines)                                                # sink failure


def test_resume_carries_the_phase_long_job(tmp_path):
    """A resume that drops --long-job-* starves the phase of its own evidence.

    The long job is part of a phase's contract, not a launch-time nicety: it is
    the thing the gate's evidence is derived from. It was persisted nowhere, so
    `specstride resume` silently relaunched without it.
    """
    wd = tmp_path / "proj"
    feat = wd / ".specstride" / "features" / "tf"
    feat.mkdir(parents=True)
    specs = wd / "spec.md"
    specs.write_text("## Phase 1\n")
    fake_orch = tmp_path / "fake-orchestrator.sh"
    fake_orch.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    fake_orch.chmod(0o755)
    conf = (
        f"WORKDIR={wd}\nSPECS={specs}\nFEATURE=tf\n"
        "PROPOSER_BACKEND=dsh\nCRITIC_BACKEND=dsh\nMAX_REJECTS=3\nMAX_ITER=5\n"
        "LONG_JOB_PHASE=8\nLONG_JOB_CMD=./tests/integration/cycles_runner.sh\n"
        f"ORCHESTRATOR={fake_orch}\n"
    )
    (wd / ".specstride" / "last-run.conf").write_text(conf)
    feat.joinpath("last-run.conf").write_text(conf)

    out = run("resume", wd)

    assert "--long-job-phase" in out and "\n8\n" in out
    assert "--long-job-cmd" in out
    assert "./tests/integration/cycles_runner.sh" in out


def test_resume_omits_long_job_when_the_run_had_none(tmp_path):
    """Older configs carry no long job; resume must not invent empty flags."""
    wd = tmp_path / "proj"
    feat = wd / ".specstride" / "features" / "tf"
    feat.mkdir(parents=True)
    specs = wd / "spec.md"
    specs.write_text("## Phase 1\n")
    fake_orch = tmp_path / "fake-orchestrator.sh"
    fake_orch.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    fake_orch.chmod(0o755)
    conf = (
        f"WORKDIR={wd}\nSPECS={specs}\nFEATURE=tf\n"
        "PROPOSER_BACKEND=dsh\nCRITIC_BACKEND=dsh\nMAX_REJECTS=3\nMAX_ITER=5\n"
        f"ORCHESTRATOR={fake_orch}\n"
    )
    (wd / ".specstride" / "last-run.conf").write_text(conf)
    feat.joinpath("last-run.conf").write_text(conf)

    out = run("resume", wd)

    assert "--long-job" not in out


# ── the pre-rename command name (Specstride was formerly Wiggum) ─────────────
LEGACY_CLI = os.path.join(ROOT, "wiggum")
SHIM_NOTICE = ("wiggum: this command was renamed to specstride; "
               "the alias will be removed in a future release")


def _clean_env():
    """The caller's env minus any Specstride/legacy knobs, so stderr is exact."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("SPECSTRIDE_", "WIGGUM_"))}


def test_legacy_command_name_is_a_forwarding_shim(workdir):
    env = _clean_env()
    new = subprocess.run(["bash", SPECSTRIDE, "status", "-w", str(workdir)],
                         capture_output=True, text=True, timeout=20, env=env)
    old = subprocess.run([LEGACY_CLI, "status", "-w", str(workdir)],
                         capture_output=True, text=True, timeout=20, env=env)
    assert new.returncode == old.returncode == 0
    assert "unknown schema version 9" in ANSI.sub("", new.stdout)   # a real status card
    assert old.stdout == new.stdout
    assert old.stderr == SHIM_NOTICE + "\n" + new.stderr


def test_legacy_command_name_forwards_arguments_and_exit_code(workdir, tmp_path):
    env = _clean_env()
    missing = tmp_path / "nowhere"
    missing.mkdir()
    new = subprocess.run(["bash", SPECSTRIDE, "status", "-w", str(missing)],
                         capture_output=True, text=True, timeout=20, env=env)
    old = subprocess.run([LEGACY_CLI, "status", "-w", str(missing)],
                         capture_output=True, text=True, timeout=20, env=env)
    assert new.returncode == old.returncode != 0
    assert old.stderr.splitlines()[0] == SHIM_NOTICE
    assert old.stderr.splitlines()[1:] == new.stderr.splitlines()


# ── git checkpoints survive resume (SPECSTRIDE_GIT_COMMITS is env-only) ──────
def _env_echo_orchestrator(tmp_path):
    fake = tmp_path / "fake-orchestrator.sh"
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n'
                    'echo "GIT_COMMITS_ENV=${SPECSTRIDE_GIT_COMMITS-unset}"\n')
    fake.chmod(0o755)
    return fake


def _resume_conf(tmp_path, extra=""):
    wd = tmp_path / "proj"
    feat = wd / ".specstride" / "features" / "tf"
    feat.mkdir(parents=True)
    specs = wd / "spec.md"
    specs.write_text("## Phase 1\n")
    conf = (
        f"WORKDIR={wd}\nSPECS={specs}\nFEATURE=tf\n"
        "PROPOSER_BACKEND=dsh\nCRITIC_BACKEND=dsh\nMAX_REJECTS=3\nMAX_ITER=5\n"
        f"ORCHESTRATOR={_env_echo_orchestrator(tmp_path)}\n{extra}"
    )
    (wd / ".specstride" / "last-run.conf").write_text(conf)
    feat.joinpath("last-run.conf").write_text(conf)
    return wd


def test_launch_persists_git_commits_in_last_run_conf(tmp_path):
    """The orchestrator writes last-run.conf before it validates the spec, so an
    invalid spec is enough to prove the key is saved without starting any agent."""
    wd = tmp_path / "proj"
    wd.mkdir()
    bad = wd / "bad.md"
    bad.write_text("# x\n\n## Phase 1: a\n\nno criteria\n")
    env = dict(_clean_env(), SPECSTRIDE_GIT_COMMITS="off")
    p = subprocess.run(
        ["bash", os.path.join(ROOT, "orchestrator.sh"), "-w", str(wd), "-s", str(bad),
         "--feature", "tf", "--no-live"],
        capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 3, p.stderr
    for conf in (wd / ".specstride" / "last-run.conf",
                 wd / ".specstride" / "features" / "tf" / "last-run.conf"):
        assert "GIT_COMMITS=off\n" in conf.read_text()


def test_resume_re_exports_saved_git_commits(tmp_path):
    wd = _resume_conf(tmp_path, "GIT_COMMITS=off\n")
    p = subprocess.run(["bash", SPECSTRIDE, "resume", "-w", str(wd), "--feature", "tf"],
                       capture_output=True, text=True, timeout=20,
                       env=dict(_clean_env(), SPECSTRIDE_GIT_COMMITS="auto"))
    assert "GIT_COMMITS_ENV=off" in p.stdout


def test_resume_without_saved_git_commits_changes_nothing(tmp_path):
    wd = _resume_conf(tmp_path)
    p = subprocess.run(["bash", SPECSTRIDE, "resume", "-w", str(wd), "--feature", "tf"],
                       capture_output=True, text=True, timeout=20, env=_clean_env())
    assert "GIT_COMMITS_ENV=unset" in p.stdout
    p = subprocess.run(["bash", SPECSTRIDE, "resume", "-w", str(wd), "--feature", "tf"],
                       capture_output=True, text=True, timeout=20,
                       env=dict(_clean_env(), SPECSTRIDE_GIT_COMMITS="auto"))
    assert "GIT_COMMITS_ENV=auto" in p.stdout

