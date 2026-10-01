"""A reverse-guard failure on paths the attempt never touched is `infra` (#109)."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import guard_infra  # noqa: E402
from test_orchestrator_verification import ORCHESTRATOR, TWO_PHASE_SPEC, _FAKE_PRIME  # noqa: E402

GUARD_OUT = ("guard: changed: .claude/skills/s/SKILL.md\n"
             "guard: git status: ?? notes/scratch.txt\n"
             "guard: FAILED — 2 change(s) outside specs/001 and the state dir\n")


def _evidence(*commands):
    return {"commands": list(commands)}


def _guard(stdout=GUARD_OUT, passed=False, declared="guard-3"):
    return {"commandId": "verify-abc", "declaredId": declared, "passed": passed, "stdout": stdout}


def _events(*records, phase=3, attempt=1):
    start = {"event": "proposer_start", "phase": str(phase), "attempt": str(attempt)}
    return [json.dumps(r) for r in (start, {"event": "agent_init", "model": "m"}) + records]


def test_untouched_paths_are_infra():
    verdict, paths = guard_infra.classify(_evidence(_guard()), _events(
        {"event": "agent_tool", "tool": "Write", "target": "specs/001/spec.md",
         "targets": ["specs/001/spec.md"]}), 3, 1)
    assert verdict == "infra"
    assert paths == [".claude/skills/s/SKILL.md", "notes/scratch.txt"]


@pytest.mark.parametrize("record", [
    {"event": "agent_tool", "tool": "Write", "target": ".claude/skills/s/SKILL.md"},
    {"event": "agent_tool", "tool": "Bash", "target": "rm -rf ./notes", "targets": ["./notes"]},
    {"event": "agent_tool", "tool": "Bash", "target": "printf 7 > .claude/skills/s/SKILL.md"},
], ids=["write", "parent-dir", "bash-substring"])
def test_a_path_the_attempt_touched_is_the_agents(record):
    verdict, reason = guard_infra.classify(_evidence(_guard()), _events(record), 3, 1)
    assert verdict == "agent" and "touched" in reason


@pytest.mark.parametrize("evidence,reason", [
    (_evidence(_guard(), {"commandId": "x", "declaredId": "lint-3", "passed": False}), "non-guard"),
    (_evidence(_guard(stdout="guard: git HEAD moved: a -> b\n")), "without a path"),
    (_evidence(_guard(stdout="guard: fingerprint differs from the baseline\n")), "without a path"),
    (_evidence(_guard(stdout="guard: added: testautomation/x (verification output landed in"
                             " the source; pass --test-plan/--generate-tests under the state dir)\n")),
     "testautomation"),
    (_evidence(_guard(passed=True)), "no failing"),
    ({"nope": 1}, "no command evidence"),
], ids=["lint-failed-too", "head-moved", "bare-fingerprint", "testautomation", "passed", "garbage"])
def test_anything_undecidable_stays_a_reject(evidence, reason):
    verdict, why = guard_infra.classify(evidence, _events(), 3, 1)
    assert verdict == "agent" and reason in why


def test_an_unrecorded_stream_is_not_evidence_of_innocence():
    lines = [json.dumps({"event": "proposer_start", "phase": "3", "attempt": "1"})]
    assert guard_infra.classify(_evidence(_guard()), lines, 3, 1)[0] == "agent"


def test_only_the_newest_start_of_that_attempt_counts():
    older = [json.dumps({"event": "proposer_start", "phase": "3", "attempt": "1"}),
             json.dumps({"event": "agent_init"}),
             json.dumps({"event": "agent_tool", "target": "notes/scratch.txt"})]
    verdict, _ = guard_infra.classify(_evidence(_guard()), older + _events(), 3, 1)
    assert verdict == "infra"


def test_cli_exit_codes_and_garbage(tmp_path):
    ev, events = tmp_path / "ev.json", tmp_path / "events.jsonl"
    ev.write_text(json.dumps(_evidence(_guard())))
    events.write_text("not json\n" + "\n".join(_events()) + "\n")
    argv = ["classify", "--evidence", str(ev), "--events", str(events), "--phase", "3", "--attempt", "1"]
    assert guard_infra.main(argv) == 0
    ev.write_text("{broken")
    assert guard_infra.main(argv) == 1


# ── end to end: the orchestrator stops instead of rejecting ──────────────────

# The fake proposer stands in for the stream tap: it records that the attempt's
# stream was seen (agent_init) and, with $FAKE_TOUCH, a tool call naming a path.
_FAKE_PRIME_TAPPED = _FAKE_PRIME.replace(
    "  rel=\"$(printf",
    "  printf '{\"event\":\"agent_init\"}\\n' >> \"$SPECSTRIDE_EVENTS\"\n"
    "  [[ -n \"${FAKE_TOUCH:-}\" ]] && printf '{\"event\":\"agent_tool\",\"tool\":\"Bash\","
    "\"target\":\"rm -rf %s\"}\\n' \"$FAKE_TOUCH\" >> \"$SPECSTRIDE_EVENTS\"\n"
    "  rel=\"$(printf",
)


def _run(tmp_path, touch=""):
    workdir = tmp_path / "work"
    workdir.mkdir()
    spec = tmp_path / "spec.md"
    spec.write_text(TWO_PHASE_SPEC)
    fake = tmp_path / "fake-prime"
    fake.write_text(_FAKE_PRIME_TAPPED)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    commands = tmp_path / "verification-commands.json"
    commands.write_text(json.dumps({"schema_version": "1.0.0", "commands": [
        {"id": "guard-1", "phase": 1, "executable": "/usr/bin/env", "cwd": str(workdir),
         "args": ["sh", "-c", "printf 'guard: changed: .claude/autoharness/requests\\n"
                  "guard: FAILED — 1 change(s) outside out and the state dir\\n'; exit 3"],
         "timeoutSec": 60},
        {"id": "p2-noop", "phase": 2, "executable": "/usr/bin/env", "args": ["true"],
         "cwd": str(workdir), "timeoutSec": 60},
    ]}))
    env = dict(os.environ)
    env.update({"SPECSTRIDE_PRIME_AGENT_BIN": str(fake), "WORKDIR_ABS": str(workdir),
                "SPECSTRIDE_GIT_COMMITS": "off", "SPECSTRIDE_AGENT_STREAM": "false",
                "FAKE_VERDICT": "APPROVED", "FAKE_TOUCH": touch})
    result = subprocess.run(
        ["/usr/bin/bash", ORCHESTRATOR, "--workdir", str(workdir), "--specs", str(spec),
         "--proposer", "prime", "--critic", "prime", "--verification", "required",
         "--verification-commands", str(commands), "--max-iter", "1", "--max-rejects", "2",
         "--feature", "obs-lifecycle", "--no-live"],
        cwd=str(workdir), env=env, text=True, capture_output=True, timeout=300, check=False)
    runs = workdir / ".specstride" / "features" / "obs-lifecycle" / "runs"
    files = sorted(runs.rglob("events.jsonl"))
    events = [json.loads(l) for l in files[-1].read_text().splitlines() if l] if files else []
    return result, events


def test_orchestrator_stops_on_an_infra_guard_failure(tmp_path):
    result, events = _run(tmp_path)
    assert result.returncode == 4, result.stdout + result.stderr
    infra = [e for e in events if e["event"] == "verification_infra"]
    assert infra and infra[0]["paths"] == ".claude/autoharness/requests"
    stops = [e for e in events if e["event"] == "run_stop"]
    assert stops and stops[-1]["reason"] == "verification_infra"
    assert not [e for e in events if e["event"] in ("reject", "verdict")]


def test_orchestrator_still_rejects_when_the_attempt_touched_the_path(tmp_path):
    result, events = _run(tmp_path, touch=".claude/autoharness")
    assert result.returncode == 2, result.stdout + result.stderr
    assert not [e for e in events if e["event"] == "verification_infra"]
