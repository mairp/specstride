import json
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import specstride_spec  # noqa: E402


ORCHESTRATOR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orchestrator.sh")

SPEC = """# Verification integration

## Phase 1 — Deliver
### Acceptance criteria
- [ ] Create an independently readable durable result
"""

# ── T068 (US6) — orchestrator lifecycle regression harness ───────────────────
# These tests drive the REAL orchestrator.sh end-to-end (proposer.sh + critic.py)
# hermetically: a single fake Prime Agent, injected via SPECSTRIDE_PRIME_AGENT_BIN,
# serves BOTH roles. proposer.sh and critic.py both shell out to that executable,
# so no live LLM, network, or credential is touched — the injection point that
# makes provider-neutral (Prime, not Claude/Bebop) coverage possible. The critic
# role is distinguished by its `--no-tools` isolation flag; the proposer writes
# the phase's GATE<N>-EVIDENCE.md whose workdir-relative path the standing prompt
# already spells out. Assertions read the run's authoritative events.jsonl and the
# orchestrator's documented exit codes, so they pin observable lifecycle behavior
# rather than internal wiring.
#
# Contracts pinned here (US6 "Preserve Existing Backend Behavior"):
#   * Lifecycle ordering — run_start precedes every phase; each phase emits
#     phase_start → proposer_start → phase_done; run_end is terminal exactly once.
#   * Phase advancement — a pre-approved GATE<N> is honored on resume (the run
#     starts at the first unapproved phase, never restarts from phase 1).
#   * Stop/resume — stop.flag halts cleanly (exit 6, reason stop_flag), consumes
#     the flag, approves nothing, and a rerun resumes to completion.
#   * Critic rejection — a REJECTED verdict archives the attempt and, at
#     MAX_REJECTS, halts (exit 2, reason max_rejects) without an APPROVED marker.
#   * Provider-neutral terminal synthesis — the same run_end/all_approved terminal
#     is produced for a Prime proposer+critic as for any other backend.

TWO_PHASE_SPEC = """# Observability lifecycle regression

## Phase 1 — Lay the foundation
### Acceptance criteria
- [ ] Create a durable phase-one result artifact

## Phase 2 — Build on the foundation
### Acceptance criteria
- [ ] Create a durable phase-two result artifact
"""

# A fake Prime Agent that plays both roles from its stdin prompt. As the critic
# (isolated with --no-tools) it echoes the per-call nonce back with the requested
# verdict; as the proposer it writes the exact evidence file the prompt names.
# $FAKE_VERDICT selects APPROVED (default) or REJECTED for the critic turn.
_FAKE_PRIME = r"""#!/bin/bash
prompt="$(cat)"
if [[ " $* " == *" --no-tools "* ]]; then
  verdict="${FAKE_VERDICT:-APPROVED}"
  nonce="$(printf '%s\n' "$prompt" \
    | grep -oE "VERDICT [0-9a-f]{16}: $verdict" | head -1 | awk '{print $2}' | tr -d ':')"
  printf 'Criterion review complete.\nVERDICT %s: %s\n' "$nonce" "$verdict"
else
  # $FAKE_PROPOSER_SLEEP (default 0 — the original behaviour) stalls the pass so a
  # pass-ceiling test can drive it into the watchdog instead of writing evidence.
  sleep "${FAKE_PROPOSER_SLEEP:-0}"
  # $YIELD_POLL_WITNESS records the poll interval the proposer handed down to us.
  [[ -n "${YIELD_POLL_WITNESS:-}" ]] && printf '%s\n' "${SPECSTRIDE_YIELD_POLL:-unset}" >> "$YIELD_POLL_WITNESS"
  rel="$(printf '%s\n' "$prompt" \
    | grep -oE '\.specstride/features/[^ ]*/gates/GATE[0-9]+-EVIDENCE\.md' | head -1)"
  mkdir -p "$(dirname "$WORKDIR_ABS/$rel")"
  printf '# Evidence\nPhase work complete.\n' > "$WORKDIR_ABS/$rel"
fi
"""


def _fake_prime(tmp_path):
    fake = tmp_path / "fake-prime-agent"
    fake.write_text(_FAKE_PRIME)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


def _run_orchestrator(tmp_path, *, verdict="APPROVED", extra_env=None,
                      max_iter="1", max_rejects="3", proposer_timeout=None,
                      phase_timeouts=(), timeout=120, orchestrator=None):
    """Drive orchestrator.sh with the fake Prime backend; return the result plus
    the parsed events from the run's authoritative events.jsonl."""
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    spec = tmp_path / "spec.md"
    spec.write_text(TWO_PHASE_SPEC)
    fake = _fake_prime(tmp_path)

    env = dict(os.environ)
    env.update({
        "SPECSTRIDE_PRIME_AGENT_BIN": str(fake),
        "WORKDIR_ABS": str(workdir),
        "SPECSTRIDE_AGENT_STREAM": "false",   # explicit raw-text Prime path (no live tap)
        "SPECSTRIDE_GIT_COMMITS": "off",      # never touch the outer repo
        "FAKE_VERDICT": verdict,
    })
    env.update(extra_env or {})

    argv = [
        "/usr/bin/bash", str(orchestrator or ORCHESTRATOR),
        "--workdir", str(workdir),
        "--specs", str(spec),
        "--proposer", "prime",
        "--critic", "prime",
        "--verification", "off",
        "--max-iter", max_iter,
        "--max-rejects", max_rejects,
        "--feature", "obs-lifecycle",
        "--no-live",
    ]
    if proposer_timeout is not None:
        argv += ["--proposer-timeout", str(proposer_timeout)]
    for spec_entry in phase_timeouts:
        argv += ["--proposer-timeout-phase", spec_entry]

    result = subprocess.run(
        argv,
        cwd=str(workdir), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False,
    )
    return result, workdir, _read_events(workdir)


def _read_events(workdir):
    runs = workdir / ".specstride" / "features" / "obs-lifecycle" / "runs"
    events = sorted(runs.rglob("events.jsonl"))
    if not events:
        return []
    return [json.loads(line) for line in events[-1].read_text().splitlines() if line]


def _names(events):
    return [e["event"] for e in events]


def test_lifecycle_ordering_and_provider_neutral_terminal(tmp_path):
    """Happy-path two-phase run: lifecycle events are correctly ordered and the
    terminal synthesis is provider-neutral (a Prime backend yields the same
    run_end/all_approved terminal any backend would)."""
    result, _workdir, events = _run_orchestrator(tmp_path, verdict="APPROVED")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    names = _names(events)
    # run_start opens the run before any phase; run_end closes it exactly once.
    assert names[0] == "run_start"
    assert names.count("run_end") == 1
    assert names[-1] == "run_end"
    assert names.index("run_start") < names.index("phase_start")

    # Both phases ran, in order, and each phase's lifecycle events are ordered
    # phase_start → proposer_start → phase_done.
    phase_starts = [e for e in events if e["event"] == "phase_start"]
    assert [e["phase"] for e in phase_starts] == ["1", "2"]
    for n in ("1", "2"):
        seq = [i for i, e in enumerate(events)
               if e.get("phase") == n and e["event"] in
               ("phase_start", "proposer_start", "phase_done")]
        got = [events[i]["event"] for i in seq]
        assert got == ["phase_start", "proposer_start", "phase_done"], (n, got)

    # Provider-neutral terminal: the closing event is the generic all_approved
    # outcome, and the backend label proves it came from the Prime path.
    run_end = events[-1]
    assert run_end["outcome"] == "all_approved"
    assert run_end["phases"] == "2"
    assert run_end["backend"] == "prop:prime/crit:prime"

    # Both gate markers exist; no leftover feedback from an approved run.
    gates = tmp_path / "work" / ".specstride" / "features" / "obs-lifecycle" / "gates"
    assert (gates / "GATE1-APPROVED").is_file()
    assert (gates / "GATE2-APPROVED").is_file()


def test_phase_advancement_resumes_from_preapproved_gate(tmp_path):
    """A pre-existing GATE1-APPROVED marker is honored: the run resumes at the
    first unapproved phase (2) instead of restarting from phase 1."""
    workdir = tmp_path / "work"
    gates = workdir / ".specstride" / "features" / "obs-lifecycle" / "gates"
    gates.mkdir(parents=True)
    (gates / "GATE1-APPROVED").write_text("")

    result, _workdir, events = _run_orchestrator(tmp_path, verdict="APPROVED")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    phases = [e["phase"] for e in events if e["event"] == "phase_start"]
    assert phases == ["2"], phases
    assert _names(events)[-1] == "run_end"
    assert events[-1]["outcome"] == "all_approved"


def test_stop_flag_halts_cleanly_and_rerun_resumes(tmp_path):
    """stop.flag makes the orchestrator halt cleanly (exit 6, reason stop_flag),
    consuming the flag and approving nothing; a rerun then resumes to completion."""
    workdir = tmp_path / "work"
    (workdir / ".specstride").mkdir(parents=True)
    stop_flag = workdir / ".specstride" / "stop.flag"
    stop_flag.write_text("")

    result, _workdir, events = _run_orchestrator(tmp_path, verdict="APPROVED")
    assert result.returncode == 6, result.stdout + "\n" + result.stderr
    assert not stop_flag.exists(), "clean stop must consume the flag so a rerun resumes"

    stops = [e for e in events if e["event"] == "run_stop"]
    assert stops and stops[-1]["reason"] == "stop_flag"
    gates = workdir / ".specstride" / "features" / "obs-lifecycle" / "gates"
    assert not (gates / "GATE1-APPROVED").exists(), "a stopped run approves nothing"

    # Rerun (no stop.flag) drives the same feature to completion — proof the halt
    # was resumable, not a dead end.
    result2, _workdir2, events2 = _run_orchestrator(tmp_path, verdict="APPROVED")
    assert result2.returncode == 0, result2.stdout + "\n" + result2.stderr
    assert _names(events2)[-1] == "run_end"
    assert (gates / "GATE1-APPROVED").is_file()
    assert (gates / "GATE2-APPROVED").is_file()


def test_critic_rejection_halts_at_max_rejects_without_approval(tmp_path):
    """A critic that always REJECTS records each rejection, archives the attempt,
    and halts at MAX_REJECTS (exit 2, reason max_rejects) with no APPROVED marker."""
    result, workdir, events = _run_orchestrator(
        tmp_path, verdict="REJECTED", max_iter="1", max_rejects="2")
    assert result.returncode == 2, result.stdout + "\n" + result.stderr

    names = _names(events)
    assert "reject" in names
    assert "attempt_archived" in names
    # Never advanced past phase 1, and no phase was ever marked done.
    assert {e["phase"] for e in events if e["event"] == "phase_start"} == {"1"}
    assert "phase_done" not in names

    stops = [e for e in events if e["event"] == "run_stop"]
    assert stops and stops[-1]["reason"] == "max_rejects"
    assert stops[-1]["phase"] == "1"

    gates = workdir / ".specstride" / "features" / "obs-lifecycle" / "gates"
    assert not (gates / "GATE1-APPROVED").exists()


def _feature_paths(workdir):
    feature = workdir / ".specstride" / "features" / "obs-lifecycle"
    return feature, feature / "gates", feature / "attempts" / "phase1"


def test_resume_archives_rejected_live_evidence_before_proposer(tmp_path):
    """A halted rejection must not let stale live evidence bypass the proposer on resume."""
    workdir = tmp_path / "work"
    _feature, gates, attempts = _feature_paths(workdir)
    gates.mkdir(parents=True)
    stale = "# Stale rejected evidence\nThis must not reach the critic again.\n"
    feedback = "# Phase 1 feedback\nT999 remains unmet.\n"
    (gates / "GATE1-EVIDENCE.md").write_text(stale)
    (gates / "GATE1-FEEDBACK.md").write_text(feedback)

    result, _workdir, events = _run_orchestrator(tmp_path, verdict="APPROVED")
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    # Resume archives the rejected document before proposer.sh checks for evidence.
    archived = list(attempts.glob("attemptresume-*/GATE1-EVIDENCE.md"))
    assert len(archived) == 1
    assert archived[0].read_text() == stale
    assert (gates / "GATE1-EVIDENCE.md").read_text() != stale
    assert (gates / "GATE1-APPROVED").is_file()
    archive_events = [e for e in events if e["event"] == "attempt_archived"]
    assert archive_events and archive_events[0]["attempt"].startswith("resume-")


def test_resume_ignores_oscillation_history_from_previous_runs(tmp_path):
    """Old flip-flops must not make a new run halt on its first rejection."""
    workdir = tmp_path / "work"
    _feature, gates, attempts = _feature_paths(workdir)
    gates.mkdir(parents=True)
    attempts.mkdir(parents=True)

    # Three historical present→absent→present cycles would trip the old detector.
    for number in range(1, 8):
        directory = attempts / f"attempt{number}"
        directory.mkdir()
        text = "T999 remains unmet.\n" if number % 2 else "A different gap remains.\n"
        (directory / "GATE1-FEEDBACK.md").write_text(text)
    old = 1_600_000_000
    for path in attempts.rglob("GATE1-FEEDBACK.md"):
        os.utime(path, (old, old))

    result, _workdir, events = _run_orchestrator(
        tmp_path, verdict="REJECTED", max_iter="1", max_rejects="1")
    assert result.returncode == 2, result.stdout + "\n" + result.stderr

    stops = [e for e in events if e["event"] == "run_stop"]
    assert stops and stops[-1]["reason"] == "max_rejects"
    assert not any(e["event"] == "gate_oscillation" for e in events)


def test_required_verification_runs_release_gate_when_phases_are_already_approved(
    tmp_path,
):
    workdir = str(tmp_path)
    specs = str(tmp_path / "SPECS.md")
    test_plan = str(tmp_path / "testautomation" / "TEST_PLAN.md")
    generated = str(tmp_path / "testautomation" / "generated")
    (tmp_path / "SPECS.md").write_text(SPEC)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "npm@10.0.0",
                "scripts": {"test": "verification-fixture"},
            }
        )
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text("#!/bin/sh\nexit 0\n")
    npm.chmod(npm.stat().st_mode | stat.S_IXUSR)

    gates = tmp_path / ".specstride" / "features" / "default" / "gates"
    gates.mkdir(parents=True)
    (gates / "GATE1-APPROVED").write_text("")

    env = dict(os.environ)
    env["PATH"] = "%s:/usr/bin:/bin" % fake_bin
    result = subprocess.run(
        [
            "/usr/bin/bash",
            ORCHESTRATOR,
            "--workdir",
            workdir,
            "--specs",
            specs,
            "--verification",
            "required",
            "--test-plan",
            test_plan,
            "--generate-tests",
            generated,
            "--no-live",
        ],
        cwd=workdir,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert os.path.isfile(test_plan)
    assert os.path.isfile(
        os.path.join(generated, "verification.generated.json")
    )
    runs = sorted(
        (tmp_path / ".specstride" / "features" / "default" / "runs").iterdir()
    )
    assert len(runs) == 1
    canonical = runs[0] / "verification" / "verification-plan.json"
    release = runs[0] / "verification" / "release.json"
    assert canonical.is_file()
    assert release.is_file()
    plan = json.loads(canonical.read_text())
    evidence = json.loads(release.read_text())
    assert set(plan["source"]) == {
        "bundleId",
        "contentHash",
        "projection",
        "specPath",
    }
    assert evidence["passed"] is True


def test_default_verification_executes_and_isolates_artifacts_by_feature(tmp_path):
    """Verification is required by default and its projections never collide across features."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    specs = tmp_path / "SPECS.md"
    specs.write_text(SPEC)
    (workdir / "package.json").write_text(
        json.dumps({"packageManager": "npm@10.0.0", "scripts": {"test": "fixture"}})
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text("#!/bin/sh\nexit 0\n")
    npm.chmod(npm.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ)
    env["PATH"] = "%s:/usr/bin:/bin" % fake_bin
    env.pop("SPECSTRIDE_VERIFICATION", None)
    env.pop("SPECSTRIDE_TEST_PLAN", None)
    env.pop("SPECSTRIDE_GENERATE_TESTS", None)

    for raw_feature, slug in (("001/alpha", "001-alpha"), ("002-beta", "002-beta")):
        gates = workdir / ".specstride" / "features" / slug / "gates"
        gates.mkdir(parents=True)
        (gates / "GATE1-APPROVED").write_text("")
        result = subprocess.run(
            [
                "/usr/bin/bash", ORCHESTRATOR,
                "--workdir", str(workdir),
                "--specs", str(specs),
                "--feature", raw_feature,
                "--no-live",
            ],
            cwd=str(workdir), env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False,
        )
        assert result.returncode == 0, result.stdout + "\n" + result.stderr

        artifact_dir = workdir / "testautomation" / slug
        assert (artifact_dir / "TEST_PLAN.md").is_file()
        assert (artifact_dir / "generated" / "verification.generated.json").is_file()

        runs = list((workdir / ".specstride" / "features" / slug / "runs").iterdir())
        assert len(runs) == 1
        assert (runs[0] / "verification" / "verification-plan.json").is_file()
        assert (runs[0] / "verification" / "release.json").is_file()

        config = (workdir / ".specstride" / "features" / slug / "last-run.conf").read_text()
        assert "VERIFICATION=required" in config
        assert "TEST_PLAN=%s" % (artifact_dir / "TEST_PLAN.md") in config
        assert "GENERATE_TESTS=%s" % (artifact_dir / "generated") in config

    assert (workdir / "testautomation" / "001-alpha" / "TEST_PLAN.md").is_file()
    assert (workdir / "testautomation" / "002-beta" / "TEST_PLAN.md").is_file()
    root_config = (workdir / ".specstride" / "last-run.conf").read_text()
    assert "FEATURE=002-beta" in root_config


# ── accelerator (acts on the diagnostician's hint) ────────────────────────────
# The first rejection of a phase yields a NEW unmet-criteria signature, so the
# diagnostician writes GATE<N>-HINT.md; the NEXT attempt must then be an accelerator
# pass (proposer.sh, role=accelerator, narrowed prompt) — and only one: a second
# rejection on the SAME signature (the fake critic always says REJECTED, and the
# text signature ignores the per-call VERDICT nonce) falls back to the wide
# proposer pass, which reads the acceleration note. Attempts are shared, so the
# run still halts at MAX_REJECTS.
def _role_trail(events):
    keep = ("proposer_start", "accelerator_start", "acceleration_note",
            "diagnostician_trigger", "reject")
    return [(e["event"], str(e.get("attempt", ""))) for e in events if e["event"] in keep]


def test_accelerator_takes_the_retry_after_a_new_hint_then_yields_to_proposer(tmp_path):
    result, workdir, events = _run_orchestrator(
        tmp_path, verdict="REJECTED", max_iter="1", max_rejects="3")
    assert result.returncode == 2, result.stdout + "\n" + result.stderr

    assert _role_trail(events) == [
        ("proposer_start", "1"), ("reject", "1"), ("diagnostician_trigger", "1"),
        ("accelerator_start", "2"), ("acceleration_note", "2"), ("reject", "2"),
        ("proposer_start", "3"), ("reject", "3"),
    ]
    rem = [e for e in events if e["event"] == "accelerator_start"][0]
    assert rem["phase"] == "1" and rem["backend"] == "prime"

    feature = workdir / ".specstride" / "features" / "obs-lifecycle"
    rem_prompt = (feature / "accelerator-prompt.phase1.txt").read_text()
    assert "You are the ACCELERATOR" in rem_prompt
    assert "your PRIMARY instruction" in rem_prompt
    assert "## Evidence contract" in rem_prompt          # shared with the proposer
    assert "GATE1-EVIDENCE.md" in rem_prompt
    # the wide pass that followed was told what the accelerator already did
    prop_prompt = (feature / "proposer-prompt.phase1.txt").read_text()
    assert "An accelerator pass already acted on that hint" in prop_prompt
    # the note is archived with the rejected accelerator attempt and stays live
    assert (feature / "attempts" / "phase1" / "attempt2" / "GATE1-ACCELERATION.md").is_file()
    note = (feature / "gates" / "GATE1-ACCELERATION.md").read_text()
    assert note.startswith("# Phase 1 — accelerator pass (attempt 2)")
    # one acceleration per signature: the marker records the signature it acted on
    marker = (feature / "gates" / ".accelerated-phase1").read_text()
    assert marker == (feature / "gates" / ".diagnosed-phase1").read_text()


def test_accelerator_can_be_disabled(tmp_path):
    result, _workdir, events = _run_orchestrator(
        tmp_path, verdict="REJECTED", max_iter="1", max_rejects="2",
        extra_env={"SPECSTRIDE_ACCELERATOR": "false"})
    assert result.returncode == 2, result.stdout + "\n" + result.stderr
    names = _names(events)
    assert "accelerator_start" not in names
    assert names.count("proposer_start") == 2


# ── unmet-criteria signature: suffixed task IDs (T049a) ──────────────────────
# Real Spec Kit plans number inserted work with a letter suffix (T044a, T049a,
# T077a). A digits-only token pattern dropped those: a phase rejected only on
# suffixed IDs looked signature-less and fell to the prose hash, and the
# accelerator's narrowed slice silently omitted exactly the criteria the critic
# had named. These drive the real bash functions out of orchestrator.sh.
def _bash_fn(script, tmp_path):
    """Run `script` with orchestrator.sh's pure signature helpers in scope."""
    harness = (
        'eval "$(sed -n \'/^unmet_signature()/,/^}/p; /^unmet_ids_re()/,/^}/p\' %s)"\n%s'
        % (ORCHESTRATOR, script))
    out = subprocess.run(["/usr/bin/bash", "-c", harness], text=True, cwd=str(tmp_path),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert out.returncode == 0, out.stdout + "\n" + out.stderr
    return out.stdout.strip()


def test_unmet_signature_keeps_letter_suffixed_task_ids(tmp_path):
    fb = tmp_path / "GATE8-FEEDBACK.md"
    fb.write_text(
        "# Phase 8 — critic feedback (REJECTED)\n\n"
        "- T049a [US3] gateway claim profile not evidenced\n"
        "- T082 Scenario 4 (lab run) not executed\n"
        "- T082 named twice must dedup\n\n"
        "VERDICT 96f3dc4ecd3e9b00: REJECTED\n")
    sig = _bash_fn('unmet_signature "%s"' % fb, tmp_path)
    assert sig == "T049a,T082,"
    ids = _bash_fn('unmet_ids_re "%s"' % sig, tmp_path)
    assert ids == "T049a|T082"


def test_unmet_signature_falls_back_to_prose_hash_without_the_verdict_nonce(tmp_path):
    """No T-token → hash the prose, but never the per-call VERDICT nonce line:
    hashing it would make every re-rejection look like new information and
    re-fire the diagnostician on an unchanged verdict."""
    body = "The acceptance criterion about durable artifacts is not substantiated.\n"
    first = tmp_path / "a.md"
    first.write_text(body + "VERDICT 1111111111111111: REJECTED\n")
    second = tmp_path / "b.md"
    second.write_text(body + "VERDICT 2222222222222222: REJECTED\n")
    sig_a = _bash_fn('unmet_signature "%s"' % first, tmp_path)
    sig_b = _bash_fn('unmet_signature "%s"' % second, tmp_path)
    assert sig_a.startswith("text:") and sig_a == sig_b

    third = tmp_path / "c.md"
    third.write_text("A different gap entirely.\nVERDICT 1111111111111111: REJECTED\n")
    assert _bash_fn('unmet_signature "%s"' % third, tmp_path) != sig_a


def test_unmet_ids_re_is_empty_for_a_prose_signature(tmp_path):
    """A prose signature has no IDs to narrow to, so the accelerator prompt must
    fall back to the whole phase rather than filtering on a literal "text:...".."""
    assert _bash_fn('unmet_ids_re "text:deadbeef"', tmp_path) == ""


def test_critic_outage_halts_before_burning_the_reject_budget(tmp_path):
    """A critic that never returns a usable verdict must stop the run, not spend
    the whole MAX_REJECTS budget on proposer passes it will never read.

    MALFORMED is how critic.py fails safe when the critic times out, is
    unreachable, or answers without a verdict line — the feedback it writes is
    contentless ("(no critic output)"), so every further attempt is a full agent
    pass run blind. No other breaker catches it: check_oscillation keys on
    criterion IDs, and a contentless feedback has none. The run must halt at
    SPECSTRIDE_CRITIC_MALFORMED_LIMIT with reason critic_unavailable (exit 1), which
    names the real cause, rather than at max_rejects (exit 2), which would blame
    the code.
    """
    result, workdir, events = _run_orchestrator(
        tmp_path, verdict="MALFORMED", max_iter="1", max_rejects="10",
        extra_env={"SPECSTRIDE_CRITIC_MALFORMED_LIMIT": "2"})

    assert result.returncode == 1, result.stdout + "\n" + result.stderr

    # The verdicts really were MALFORMED (not ordinary rejections) …
    verdicts = [e for e in events if e["event"] == "verdict"]
    assert verdicts and all(v["result"] == "MALFORMED" for v in verdicts), verdicts

    # … the breaker counted them consecutively and tripped on the 2nd …
    streaks = [e for e in events if e["event"] == "critic_malformed"]
    assert [e["streak"] for e in streaks] == ["1", "2"], streaks

    # … and it stopped there instead of running the reject budget to 10.
    stops = [e for e in events if e["event"] == "run_stop"]
    assert stops and stops[-1]["reason"] == "critic_unavailable", stops
    assert stops[-1]["streak"] == "2"
    assert len([e for e in events if e["event"] == "reject"]) == 2

    gates = workdir / ".specstride" / "features" / "obs-lifecycle" / "gates"
    assert not (gates / "GATE1-APPROVED").exists(), "an unanswered critic approves nothing"


def test_a_genuine_rejection_resets_the_critic_outage_streak(tmp_path):
    """The breaker must count only CONSECUTIVE malformed verdicts. A real
    REJECTED verdict in between is information — it proves the critic is up — so
    it resets the streak and the run stays on the normal max_rejects path."""
    result, _workdir, events = _run_orchestrator(
        tmp_path, verdict="REJECTED", max_iter="1", max_rejects="2",
        extra_env={"SPECSTRIDE_CRITIC_MALFORMED_LIMIT": "1"})

    # Limit of 1 would trip on the very first malformed verdict; none occur, so
    # the run halts the ordinary way instead.
    assert result.returncode == 2, result.stdout + "\n" + result.stderr
    assert not [e for e in events if e["event"] == "critic_malformed"]
    stops = [e for e in events if e["event"] == "run_stop"]
    assert stops and stops[-1]["reason"] == "max_rejects", stops


# ── --verification-commands, end to end through orchestrator.sh ──────────────


def test_orchestrator_refuses_a_missing_verification_commands_document(tmp_path):
    """Fail at launch, not four hours later at a phase gate."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    spec = tmp_path / "spec.md"
    spec.write_text(TWO_PHASE_SPEC)
    fake = _fake_prime(tmp_path)
    env = dict(os.environ)
    env.update({
        "SPECSTRIDE_PRIME_AGENT_BIN": str(fake),
        "WORKDIR_ABS": str(workdir),
        "SPECSTRIDE_GIT_COMMITS": "off",
        "SPECSTRIDE_AGENT_STREAM": "false",
    })
    result = subprocess.run(
        [
            "/usr/bin/bash", ORCHESTRATOR,
            "--workdir", str(workdir), "--specs", str(spec),
            "--proposer", "prime", "--critic", "prime",
            "--verification", "required",
            "--verification-commands", str(tmp_path / "nope.json"),
            "--feature", "obs-lifecycle", "--no-live",
        ],
        cwd=str(workdir), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "--verification-commands not found or empty" in result.stderr


# The same fake Prime, plus one line: every proposer pass records that it ran, in
# the same file the pre-staged command writes to. That file IS the ordering proof.
_FAKE_PRIME_ORDERED = _FAKE_PRIME.replace(
    "  rel=\"$(printf",
    "  printf 'proposer\\n' >> \"$ORDER_WITNESS\"\n  rel=\"$(printf",
)


def test_orchestrator_executes_declared_commands_and_records_the_revision(tmp_path):
    """A declared command runs at its phase gate, with its env, and the evidence
    document names the revision the gate ran against.

    Extended for step 4 (pre-staged long measurements): a command declared
    `"stage": "prestage"` runs ONCE, BEFORE the proposer pass, its passing result
    is reused by that phase's gate with provenance, and `"cumulative": false`
    keeps it out of the later phase's gate entirely.
    """
    workdir = tmp_path / "work"
    workdir.mkdir()
    spec = tmp_path / "spec.md"
    spec.write_text(TWO_PHASE_SPEC)
    fake = tmp_path / "fake-prime-ordered"
    fake.write_text(_FAKE_PRIME_ORDERED)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    witness = workdir / "declared-ran.txt"
    # Outside the workdir on purpose: a pre-stage that writes INTO the tree makes
    # it dirty, and a dirty tree refuses reuse — which is the point of the rule.
    ordering = tmp_path / "ordering.txt"
    commands = tmp_path / "verification-commands.json"
    # Both phases need a command: the spec has two, and a phase with neither a
    # discovered nor a declared command is refused at preflight by design.
    commands.write_text(json.dumps({
        "schema_version": "1.0.0",
        "commands": [
            {
                "id": "p1-witness",
                "phase": 1,
                "executable": "/usr/bin/env",
                "args": ["sh", "-c",
                         'test "$SPECSTRIDE_TEST_MARK" = declared && echo ran > "$1"',
                         "sh", str(witness)],
                "cwd": str(workdir),
                "timeoutSec": 60,
                "env": {"SPECSTRIDE_TEST_MARK": "declared"},
            },
            {
                "id": "p1-prestage",
                "phase": 1,
                "executable": "/usr/bin/env",
                "args": ["sh", "-c", 'printf "prestage\n" >> "$1"', "sh",
                         str(ordering)],
                "cwd": str(workdir),
                "timeoutSec": 60,
                "stage": "prestage",
                "reportPath": "declared-ran.txt",
                "cumulative": False,
            },
            {
                "id": "p2-noop",
                "phase": 2,
                "executable": "/usr/bin/env",
                "args": ["true"],
                "cwd": str(workdir),
                "timeoutSec": 60,
            },
        ],
    }))
    # The run writes every artifact under .specstride/; ignoring it keeps the tree
    # clean, which is what lets the gate reuse the pre-stage at all.
    (workdir / ".gitignore").write_text(
        ".specstride/\ntestautomation/\ndeclared-ran.txt\n"
    )
    subprocess.run(["git", "init", "-q", str(workdir)], check=True)
    subprocess.run(["git", "-C", str(workdir), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(workdir), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "initial", "--allow-empty"],
        check=True,
    )

    env = dict(os.environ)
    env.update({
        "SPECSTRIDE_PRIME_AGENT_BIN": str(fake),
        "WORKDIR_ABS": str(workdir),
        "ORDER_WITNESS": str(ordering),
        "SPECSTRIDE_GIT_COMMITS": "off",
        "SPECSTRIDE_AGENT_STREAM": "false",
        "FAKE_VERDICT": "APPROVED",
    })
    result = subprocess.run(
        [
            "/usr/bin/bash", ORCHESTRATOR,
            "--workdir", str(workdir), "--specs", str(spec),
            "--proposer", "prime", "--critic", "prime",
            "--verification", "required",
            "--verification-commands", str(commands),
            "--max-iter", "1", "--feature", "obs-lifecycle", "--no-live",
        ],
        cwd=str(workdir), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=False,
    )

    assert witness.is_file(), (
        "declared command never ran\n" + result.stdout + result.stderr
    )
    runs = workdir / ".specstride" / "features" / "obs-lifecycle" / "runs"
    evidence_files = sorted(runs.rglob("verification/phase-1-attempt-*.json"))
    assert evidence_files, result.stdout + result.stderr
    evidence = json.loads(evidence_files[0].read_text())
    declared = [c for c in evidence["commands"] if c["source"] == "declared"]
    assert [c["declaredId"] for c in declared] == ["p1-witness", "p1-prestage"]
    assert declared[0]["env"] == {"SPECSTRIDE_TEST_MARK": "declared"}
    assert evidence["sourceRevision"]["available"] is True
    assert len(evidence["sourceRevision"]["revision"]) == 40

    # The saved config carries the document so `specstride resume` cannot silently
    # narrow later gates back to the discovered heuristic.
    conf = (workdir / ".specstride" / "features" / "obs-lifecycle" / "last-run.conf").read_text()
    assert "VERIFICATION_COMMANDS=" in conf
    assert str(commands) in conf
    # ── step 4: the pre-stage ran ONCE, and it ran BEFORE the proposer ────────
    order = ordering.read_text().split()
    assert order, "nothing recorded its order\n" + result.stdout + result.stderr
    # Pre-stage, then the pass; the phase-1 gate does NOT run it a third time.
    # The release gate does run it — `cumulative: false` gates a command at its
    # own phase and at release, which is exactly what the second entry is.
    assert order[:3] == ["prestage", "proposer", "proposer"], order
    assert order.count("prestage") == 2, order

    prestage_files = sorted(runs.rglob("verification/prestage-phase-1-attempt-*.json"))
    assert prestage_files, result.stdout + result.stderr
    prestaged = json.loads(prestage_files[0].read_text())
    assert prestaged["phase"] == 1 and prestaged["attempt"] == 1
    assert prestaged["passed"] is True
    assert [c["declaredId"] for c in prestaged["commands"]] == ["p1-prestage"]
    assert prestaged["commands"][0]["reportPath"] == "declared-ran.txt"

    # The phase-1 gate adopted that result instead of re-running it, and says so.
    reused = [c for c in evidence["commands"] if c.get("declaredId") == "p1-prestage"]
    assert len(reused) == 1, evidence["commands"]
    assert reused[0]["reused"] is True
    assert reused[0]["reusedFrom"]["evidencePath"] == str(prestage_files[0])
    assert reused[0]["reusedFrom"]["revision"] == evidence["sourceRevision"]["revision"]

    # `cumulative: false`: phase 2's gate never sees it again.
    phase2 = json.loads(
        sorted(runs.rglob("verification/phase-2-attempt-*.json"))[0].read_text()
    )
    assert "p1-prestage" not in [c.get("declaredId") for c in phase2["commands"]]
    assert "p1-witness" in [c.get("declaredId") for c in phase2["commands"]]

    events = _read_events(workdir)
    prestage_events = [e for e in events if e["event"] == "prestage_done"]
    assert len(prestage_events) == 1, [e["event"] for e in events]
    assert prestage_events[0]["phase"] == "1" and prestage_events[0]["attempt"] == "1"


# ── per-phase proposer cap (design §4.1) ─────────────────────────────────────
# One global --proposer-timeout is set for the worst phase in the run, so every
# other phase carries a ceiling that means nothing — and a 90-minute ceiling on a
# pass that is stuck is 90 minutes of nothing. These pin the three properties the
# resolution has to have: an override actually reaches the pass, the resolved
# value and its SOURCE are recorded, and a run that declares no override behaves
# exactly as it did before any of this existed.


def _caps(events):
    return [e for e in events if e["event"] == "proposer_cap"]


def test_an_absent_override_is_the_global_timeout_unchanged(tmp_path):
    """The back-compat case, and the common one: nothing declared, so every
    phase resolves to the global value and the happy path is untouched."""
    result, _workdir, events = _run_orchestrator(tmp_path, proposer_timeout=900)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr

    caps = _caps(events)
    assert [c["phase"] for c in caps] == ["1", "2"]
    assert all(c["seconds"] == "900" and c["source"] == "global" for c in caps)
    # And the lifecycle is byte-identical to the plain happy-path run.
    names = _names(events)
    assert names[0] == "run_start" and names.count("run_end") == 1
    assert names.count("phase_done") == 2


def test_a_per_phase_override_is_resolved_and_sourced(tmp_path):
    """Phase 1 overridden, phase 2 not: two different ceilings in one run, each
    naming where it came from."""
    _result, _workdir, events = _run_orchestrator(
        tmp_path, proposer_timeout=900, phase_timeouts=("1=1234",))

    caps = {c["phase"]: c for c in _caps(events)}
    assert caps["1"]["seconds"] == "1234" and caps["1"]["source"] == "override"
    assert caps["2"]["seconds"] == "900" and caps["2"]["source"] == "global"


def test_the_env_spelling_of_the_override_is_the_same_route(tmp_path):
    """SPECSTRIDE_PROPOSER_TIMEOUT_PHASE_<N> is what a wrapper or a resume sets; it
    resolves identically to the flag."""
    _result, _workdir, events = _run_orchestrator(
        tmp_path, proposer_timeout=900,
        extra_env={"SPECSTRIDE_PROPOSER_TIMEOUT_PHASE_2": "1500"})

    caps = {c["phase"]: c for c in _caps(events)}
    assert caps["1"]["seconds"] == "900" and caps["1"]["source"] == "global"
    assert caps["2"]["seconds"] == "1500" and caps["2"]["source"] == "override"


def test_a_malformed_override_is_refused_at_launch(tmp_path):
    """Fail at launch, not six hours in with an unparseable ceiling."""
    result, _workdir, _events = _run_orchestrator(
        tmp_path, phase_timeouts=("1=soon",))
    assert result.returncode == 3, result.stdout + result.stderr
    assert "must be N=SECONDS" in result.stderr


def test_the_per_phase_timeout_reaches_the_proposer_subprocess(tmp_path):
    """The resolution is worthless unless the pass actually runs under it. A
    3-second phase-1 ceiling against a global 900 kills a stalling pass in
    seconds — which only happens if proposer.sh received 3, not 900."""
    result, _workdir, events = _run_orchestrator(
        tmp_path, proposer_timeout=900, phase_timeouts=("1=3",),
        extra_env={"FAKE_PROPOSER_SLEEP": "60",
                   "SPECSTRIDE_WATCHDOG_TICK": "1",
                   "SPECSTRIDE_PROPOSER_PROGRESS_TIMEOUT": "0",
                   "SPECSTRIDE_PROPOSER_REPEAT_LIMIT": "0"},
        timeout=180)

    kills = [e for e in events if e["event"] == "pass_killed"]
    assert kills, result.stdout + "\n" + result.stderr
    assert kills[0]["reason"] == "hard_cap"
    # Killed at the PHASE ceiling, not the global one.
    assert int(kills[0]["elapsed"]) < 60
    # max-iter reached without evidence → the documented budget exit.
    assert result.returncode == 4, result.stdout + result.stderr


def test_last_run_conf_round_trips_the_ceiling_and_its_overrides(tmp_path):
    """PROPOSER_TIMEOUT was never persisted, so `specstride resume` silently reverted
    every phase to the 1800s default. Both it and the per-phase map must survive."""
    _result, workdir, _events = _run_orchestrator(
        tmp_path, proposer_timeout=900, phase_timeouts=("2=1234", "1=600"))

    for conf_path in (
        workdir / ".specstride" / "features" / "obs-lifecycle" / "last-run.conf",
        workdir / ".specstride" / "last-run.conf",
    ):
        # The file is %q-escaped and sourced by `specstride resume`, so read it the
        # way resume does rather than pattern-matching one escaping of a space.
        sourced = subprocess.run(
            ["/usr/bin/bash", "-c",
             '. "$1"; printf "%s\n%s\n" "$PROPOSER_TIMEOUT" "$PROPOSER_TIMEOUT_PHASES"',
             "bash", str(conf_path)],
            text=True, stdout=subprocess.PIPE, check=True).stdout.splitlines()
        # Sorted by phase, so the file is stable across runs and diffable.
        assert sourced == ["900", "1=600 2=1234"], (sourced, conf_path.read_text())


def test_the_declared_document_can_carry_the_phase_ceiling(tmp_path):
    """A phase's ceiling is a property of the work that phase does, and the
    verification-commands document is already where the operator declares what
    that work IS — so it can say so there, beside the command."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    spec = tmp_path / "spec.md"
    spec.write_text(TWO_PHASE_SPEC)
    fake = _fake_prime(tmp_path)
    commands = tmp_path / "verification-commands.json"
    commands.write_text(json.dumps({
        "schema_version": "1.0.0",
        "phaseTimeouts": {"2": 4321},
        "commands": [
            {"id": "p1-noop", "phase": 1, "executable": "/usr/bin/env",
             "args": ["true"], "cwd": str(workdir), "timeoutSec": 60},
            {"id": "p2-noop", "phase": 2, "executable": "/usr/bin/env",
             "args": ["true"], "cwd": str(workdir), "timeoutSec": 60},
        ],
    }))
    env = dict(os.environ)
    env.update({
        "SPECSTRIDE_PRIME_AGENT_BIN": str(fake),
        "WORKDIR_ABS": str(workdir),
        "SPECSTRIDE_GIT_COMMITS": "off",
        "SPECSTRIDE_AGENT_STREAM": "false",
        "FAKE_VERDICT": "APPROVED",
    })
    result = subprocess.run(
        [
            "/usr/bin/bash", ORCHESTRATOR,
            "--workdir", str(workdir), "--specs", str(spec),
            "--proposer", "prime", "--critic", "prime",
            "--verification", "required",
            "--verification-commands", str(commands),
            "--proposer-timeout", "900",
            "--max-iter", "1", "--feature", "obs-lifecycle", "--no-live",
        ],
        cwd=str(workdir), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=False,
    )
    events = _read_events(workdir)
    caps = {c["phase"]: c for c in _caps(events)}
    assert caps, result.stdout + result.stderr
    assert caps["1"]["seconds"] == "900" and caps["1"]["source"] == "global"
    assert caps["2"]["seconds"] == "4321" and caps["2"]["source"] == "declared"


def test_an_explicit_override_outranks_the_declared_document(tmp_path):
    """Route 1 beats route 3: the operator's flag is the more local statement."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    spec = tmp_path / "spec.md"
    spec.write_text(TWO_PHASE_SPEC)
    fake = _fake_prime(tmp_path)
    commands = tmp_path / "verification-commands.json"
    commands.write_text(json.dumps({
        "schema_version": "1.0.0",
        "phaseTimeouts": {"2": 4321},
        "commands": [
            {"id": "p1-noop", "phase": 1, "executable": "/usr/bin/env",
             "args": ["true"], "cwd": str(workdir), "timeoutSec": 60},
            {"id": "p2-noop", "phase": 2, "executable": "/usr/bin/env",
             "args": ["true"], "cwd": str(workdir), "timeoutSec": 60},
        ],
    }))
    env = dict(os.environ)
    env.update({
        "SPECSTRIDE_PRIME_AGENT_BIN": str(fake),
        "WORKDIR_ABS": str(workdir),
        "SPECSTRIDE_GIT_COMMITS": "off",
        "SPECSTRIDE_AGENT_STREAM": "false",
        "FAKE_VERDICT": "APPROVED",
    })
    result = subprocess.run(
        [
            "/usr/bin/bash", ORCHESTRATOR,
            "--workdir", str(workdir), "--specs", str(spec),
            "--proposer", "prime", "--critic", "prime",
            "--verification", "required",
            "--verification-commands", str(commands),
            "--proposer-timeout", "900", "--proposer-timeout-phase", "2=77",
            "--max-iter", "1", "--feature", "obs-lifecycle", "--no-live",
        ],
        cwd=str(workdir), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, check=False,
    )
    caps = {c["phase"]: c for c in _caps(_read_events(workdir))}
    assert caps, result.stdout + result.stderr
    assert caps["2"]["seconds"] == "77" and caps["2"]["source"] == "override"


# ── route 1.5: the learned ceiling (step 5 wired into step 2) ────────────────
def _shape(phase, spec=None):
    """The shape digest the orchestrator files phase N of TWO_PHASE_SPEC under."""
    return specstride_spec.phase_shape(spec or TWO_PHASE_SPEC, phase, "native")


def _seed_applied(tmp_path, phase, value, shape=None):
    learning = tmp_path / "work" / ".specstride" / "features" / "obs-lifecycle" / "learning"
    learning.mkdir(parents=True, exist_ok=True)
    (learning / "applied.json").write_text(json.dumps(
        {"schema": "specstride.learn.applied/2", "action": "apply", "run_id": "seed",
         "knob": "proposer_timeout", "phase": phase, "shape": shape or _shape(phase), "value": value,
         "previous": 900, "samples": 3, "source_runs": ["seed"], "applied_at": "2026-09-12T00:00:00Z"}) + "\n")


def test_an_applied_learning_decision_resolves_as_learned_only_when_applying(tmp_path):
    """With SPECSTRIDE_LEARNING=apply an applied decision for phase 1 is the ceiling
    and is SOURCED as learned; phase 2, with no decision, stays global."""
    _seed_applied(tmp_path, 1, 1234)
    _result, _workdir, events = _run_orchestrator(
        tmp_path, proposer_timeout=900, extra_env={"SPECSTRIDE_LEARNING": "apply"})
    caps = {c["phase"]: c for c in _caps(events)}
    assert caps["1"]["seconds"] == "1234" and caps["1"]["source"] == "learned"
    assert caps["2"]["seconds"] == "900" and caps["2"]["source"] == "global"


def test_learning_unset_never_reads_an_applied_decision(tmp_path):
    """The §5.5 invariant: with the layer off, an applied file on disk changes
    nothing — the resolution is byte-identical to the plain run."""
    _seed_applied(tmp_path, 1, 1234)
    _result, _workdir, events = _run_orchestrator(tmp_path, proposer_timeout=900)
    caps = {c["phase"]: c for c in _caps(events)}
    assert all(c["seconds"] == "900" and c["source"] == "global" for c in caps.values())


def test_phase_start_records_the_phase_shape(tmp_path):
    _result, _workdir, events = _run_orchestrator(tmp_path, proposer_timeout=900)
    starts = {e["phase"]: e for e in events if e["event"] == "phase_start"}
    assert starts["1"]["shape"] == _shape(1) and starts["2"]["shape"] == _shape(2)


def test_a_learned_value_for_an_edited_phase_is_not_applied(tmp_path):
    """A decision learned for an earlier version of phase 1 (other criteria) does
    not reach the edited phase. (A shape-less decision: test_learn.py.)"""
    _seed_applied(tmp_path, 1, 1234, shape=_shape(1, TWO_PHASE_SPEC.replace("phase-one", "phase-1")))
    _result, _workdir, events = _run_orchestrator(
        tmp_path, proposer_timeout=900, extra_env={"SPECSTRIDE_LEARNING": "apply"})
    caps = {c["phase"]: c for c in _caps(events)}
    assert caps["1"]["seconds"] == "900" and caps["1"]["source"] == "global"


def test_an_explicit_override_outranks_a_learned_value(tmp_path):
    """Resolution order: the operator's override is route 1 and wins over 1.5."""
    _seed_applied(tmp_path, 1, 1234)
    _result, _workdir, events = _run_orchestrator(
        tmp_path, proposer_timeout=900, phase_timeouts=("1=777",),
        extra_env={"SPECSTRIDE_LEARNING": "apply"})
    caps = {c["phase"]: c for c in _caps(events)}
    assert caps["1"]["seconds"] == "777" and caps["1"]["source"] == "override"


# ── the learned yield poll interval, resolved beside the ceiling ─────────────
def _seed_applied_yield_poll(tmp_path, phase, value):
    learning = tmp_path / "work" / ".specstride" / "features" / "obs-lifecycle" / "learning"
    learning.mkdir(parents=True, exist_ok=True)
    with (learning / "applied.json").open("a") as handle:
        handle.write(json.dumps({"knob": "yield_poll_interval", "phase": phase, "shape": _shape(phase),
                                 "value": value, "previous": 30, "samples": 3, "action": "apply",
                                 "run_id": "seed"}) + "\n")


def _witnessed_polls(tmp_path):
    witness = tmp_path / "yield-poll-witness"
    return witness.read_text().split() if witness.exists() else []


def test_an_applied_yield_poll_reaches_the_proposer_only_when_applying(tmp_path):
    """Under SPECSTRIDE_LEARNING=apply phase 1's applied poll interval is what the
    proposer runs with, sourced `learned`; phase 2, with no decision, gets 30."""
    _seed_applied_yield_poll(tmp_path, 1, 45)
    _result, _workdir, events = _run_orchestrator(
        tmp_path, extra_env={"SPECSTRIDE_LEARNING": "apply",
                             "YIELD_POLL_WITNESS": str(tmp_path / "yield-poll-witness")})
    caps = {c["phase"]: c for c in _caps(events)}
    assert caps["1"]["yield_poll"] == "45" and caps["1"]["yield_poll_source"] == "learned"
    assert caps["2"]["yield_poll"] == "30" and caps["2"]["yield_poll_source"] == "default"
    assert _witnessed_polls(tmp_path) == ["45", "30"]


def test_learning_unset_never_reads_an_applied_yield_poll(tmp_path):
    _seed_applied_yield_poll(tmp_path, 1, 45)
    _result, _workdir, events = _run_orchestrator(
        tmp_path, extra_env={"YIELD_POLL_WITNESS": str(tmp_path / "yield-poll-witness")})
    caps = _caps(events)
    assert caps and all(c["yield_poll"] == "30" and c["yield_poll_source"] == "default" for c in caps)
    assert _witnessed_polls(tmp_path) == ["30", "30"]


def test_an_operator_yield_poll_outranks_a_learned_one(tmp_path):
    """Set-ness, not value: an explicit SPECSTRIDE_YIELD_POLL wins for every phase,
    even one equal to the default, and is sourced `override`."""
    _seed_applied_yield_poll(tmp_path, 1, 45)
    _result, _workdir, events = _run_orchestrator(
        tmp_path, extra_env={"SPECSTRIDE_LEARNING": "apply", "SPECSTRIDE_YIELD_POLL": "30",
                             "YIELD_POLL_WITNESS": str(tmp_path / "yield-poll-witness")})
    caps = {c["phase"]: c for c in _caps(events)}
    assert caps["1"]["yield_poll"] == "30" and caps["1"]["yield_poll_source"] == "override"
    assert caps["2"]["yield_poll_source"] == "override"
    assert _witnessed_polls(tmp_path) == ["30", "30"]


# ── the phase_done observation hook (design §5.4) ────────────────────────────
# The loop's per-phase observations are written where the phase closes, not
# recomputed on demand from a run's events later. The hook is deliberately the
# weakest thing that can work: one call site, guarded by SPECSTRIDE_LEARNING, and
# best-effort in every direction — because an observation that can fail an
# APPROVED phase is worse than no observation at all.
#
# `learn.py observe` itself is landing on another branch, so these tests run the
# orchestrator from a script root whose lib/learn.py is a stand-in: one that has
# the subcommand, one that does not, one that fails. That is also how the
# absence is pinned — a Specstride whose learn.py predates `observe` must run
# exactly as it always did.
_LEARN_HEAD = '''#!/usr/bin/env python3
import argparse, json, sys

parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="cmd", required=True)
resolve = sub.add_parser("resolve")
for flag in ("--knob", "--phase", "--default", "--feature-dir", "--phase-shape"):
    resolve.add_argument(flag)
'''

_LEARN_OBSERVE = '''observe = sub.add_parser("observe")
observe.add_argument("--events", required=True)
observe.add_argument("--phase", required=True)
observe.add_argument("--out", required=True)
observe.add_argument("--phase-shape")
'''

_LEARN_EVALUATE = '''evaluate = sub.add_parser("evaluate")
for flag in ("--events", "--feature-dir", "--phase", "--phase-shape", "--events-file"):
    evaluate.add_argument(flag)
'''

_LEARN_TAIL = '''args = parser.parse_args()
if args.cmd == "resolve":
    print(args.default or "")
    sys.exit(0)
if args.cmd == "evaluate":
    sys.stderr.write("evaluate: stand-in for phase " + args.phase + "\\n")
    if "__EVALUATE__" == "fail":
        sys.exit(1)
    import os
    os.makedirs(os.path.join(args.feature_dir, "learning"), exist_ok=True)
    with open(os.path.join(args.feature_dir, "learning", "evaluated.jsonl"), "a") as handle:
        handle.write(json.dumps({"phase": args.phase, "events": args.events, "shape": args.phase_shape,
                                 "events_file": args.events_file}) + "\\n")
    sys.exit(0)
sys.stderr.write("observe: stand-in for phase " + args.phase + "\\n")
if "__OUTCOME__" == "fail":
    sys.exit(1)
with open(args.out, "w") as handle:
    json.dump({"phase": args.phase, "events": args.events, "shape": args.phase_shape}, handle)
'''


def _learn_stub(*, observe=True, fails=False, evaluate=False, evaluate_fails=False):
    tail = (_LEARN_TAIL.replace("__OUTCOME__", "fail" if fails else "ok")
            .replace("__EVALUATE__", "fail" if evaluate_fails else "ok"))
    return (_LEARN_HEAD + (_LEARN_OBSERVE if observe else "")
            + (_LEARN_EVALUATE if evaluate else "") + tail)


def _script_root(tmp_path, learn_source):
    """A hermetic script root whose lib/learn.py is `learn_source`.

    orchestrator.sh derives LIB_DIR from its own location, so the way to put a
    different learn.py in front of it — without editing the repo and without
    adding a production knob that exists only for a test — is to run it from a
    directory that symlinks every real script and every real lib module except
    that one file.
    """
    repo = Path(ORCHESTRATOR).parent
    root = tmp_path / "script-root"
    (root / "lib").mkdir(parents=True)
    for entry in repo.iterdir():
        if entry.name in (".git", "lib"):
            continue
        (root / entry.name).symlink_to(entry)
    for entry in (repo / "lib").iterdir():
        if entry.name == "learn.py":
            continue
        (root / "lib" / entry.name).symlink_to(entry)
    (root / "lib" / "learn.py").write_text(learn_source)
    return root / "orchestrator.sh"


def _observations(workdir):
    learning = workdir / ".specstride" / "features" / "obs-lifecycle" / "learning"
    return sorted(p.name for p in learning.glob("phase-*.json")) if learning.is_dir() else []


def test_learning_unset_observes_nothing_at_phase_done(tmp_path):
    """§5.5 invariant: with the layer off, the hook does not run at all — even
    where a learn.py that CAN observe is installed."""
    orchestrator = _script_root(tmp_path, _learn_stub())
    result, workdir, events = _run_orchestrator(tmp_path, orchestrator=orchestrator)

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert _observations(workdir) == []
    assert [e for e in events if e["event"] == "learning_observed"] == []


def test_phase_done_writes_one_observation_per_phase_and_announces_it(tmp_path):
    """With the layer on, each approved phase leaves one observation at the §5.4
    path and one `learning_observed` event naming it."""
    orchestrator = _script_root(tmp_path, _learn_stub())
    result, workdir, events = _run_orchestrator(
        tmp_path, orchestrator=orchestrator,
        extra_env={"SPECSTRIDE_LEARNING": "suggest"})

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert _observations(workdir) == ["phase-1.json", "phase-2.json"]

    observed = [e for e in events if e["event"] == "learning_observed"]
    assert [e["phase"] for e in observed] == ["1", "2"]
    for event in observed:
        path = Path(event["path"])
        assert path.is_file(), event
        # Written from THIS run's events, for THIS phase — not recomputed later
        # from whatever stream happened to be newest.
        written = json.loads(path.read_text())
        assert written["phase"] == event["phase"]
        # The hook observes the feature's whole runs directory (cross-run view), not one run.
        assert written["events"].rstrip("/").endswith("/runs"), written["events"]
        # ... restricted to the phase's current shape
        assert written["shape"] == _shape(int(event["phase"]))

    # An observation is a by-product of the closed phase: it follows phase_done.
    order = [e["event"] for e in events
             if e["event"] in ("phase_done", "learning_observed")]
    assert order == ["phase_done", "learning_observed"] * 2


def test_a_learn_py_without_observe_leaves_the_run_untouched(tmp_path):
    """`observe` lands on another branch. A Specstride whose learn.py predates it
    must run exactly as before — silently, with no observation and no failure."""
    orchestrator = _script_root(tmp_path, _learn_stub(observe=False))
    result, workdir, events = _run_orchestrator(
        tmp_path, orchestrator=orchestrator,
        extra_env={"SPECSTRIDE_LEARNING": "suggest"})

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert _observations(workdir) == []
    assert [e for e in events if e["event"] == "learning_observed"] == []
    assert _names(events)[-1] == "run_end"


def test_a_failing_observe_never_fails_the_phase(tmp_path):
    """The hook is best-effort in both directions: a learn.py that HAS observe
    and fails it must not cost an approved phase — the failure is logged and the
    run finishes."""
    orchestrator = _script_root(tmp_path, _learn_stub(fails=True))
    result, workdir, events = _run_orchestrator(
        tmp_path, orchestrator=orchestrator,
        extra_env={"SPECSTRIDE_LEARNING": "suggest"})

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert _observations(workdir) == []
    assert [e for e in events if e["event"] == "learning_observed"] == []
    gates = workdir / ".specstride" / "features" / "obs-lifecycle" / "gates"
    assert (gates / "GATE1-APPROVED").is_file() and (gates / "GATE2-APPROVED").is_file()
    run_log = sorted((workdir / ".specstride" / "features" / "obs-lifecycle" / "runs")
                     .rglob("run.log"))[-1].read_text()
    assert "observing phase 1 failed" in run_log


# ── the phase_done evaluation hook (item 4; 05-evaluate-design.md §2.8) ──────
def _evaluations(workdir):
    path = workdir / ".specstride" / "features" / "obs-lifecycle" / "learning" / "evaluated.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def test_learning_unset_evaluates_nothing_at_phase_done(tmp_path):
    orchestrator = _script_root(tmp_path, _learn_stub(evaluate=True))
    result, workdir, _events = _run_orchestrator(tmp_path, orchestrator=orchestrator)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert _evaluations(workdir) == []


def test_phase_done_evaluates_each_phase_over_the_runs_tree(tmp_path):
    orchestrator = _script_root(tmp_path, _learn_stub(evaluate=True))
    result, workdir, events = _run_orchestrator(
        tmp_path, orchestrator=orchestrator, extra_env={"SPECSTRIDE_LEARNING": "suggest"})
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    evaluated = _evaluations(workdir)
    assert [e["phase"] for e in evaluated] == ["1", "2"]
    for e in evaluated:
        assert e["events"].rstrip("/").endswith("/runs")          # every run, not the newest one
        assert e["shape"] == _shape(int(e["phase"]))
        assert e["events_file"].endswith("/events.jsonl") and "/runs/" in e["events_file"]
    assert _observations(workdir) == ["phase-1.json", "phase-2.json"]   # observe still runs beside it


def test_a_learn_py_without_evaluate_leaves_the_run_untouched(tmp_path):
    orchestrator = _script_root(tmp_path, _learn_stub(evaluate=False))
    result, workdir, events = _run_orchestrator(
        tmp_path, orchestrator=orchestrator, extra_env={"SPECSTRIDE_LEARNING": "suggest"})
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert _evaluations(workdir) == []
    assert _observations(workdir) == ["phase-1.json", "phase-2.json"]
    assert _names(events)[-1] == "run_end"


def test_a_failing_evaluate_never_fails_the_phase(tmp_path):
    orchestrator = _script_root(tmp_path, _learn_stub(evaluate=True, evaluate_fails=True))
    result, workdir, _events = _run_orchestrator(
        tmp_path, orchestrator=orchestrator, extra_env={"SPECSTRIDE_LEARNING": "suggest"})
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    gates = workdir / ".specstride" / "features" / "obs-lifecycle" / "gates"
    assert (gates / "GATE1-APPROVED").is_file() and (gates / "GATE2-APPROVED").is_file()
    run_log = sorted((workdir / ".specstride" / "features" / "obs-lifecycle" / "runs")
                     .rglob("run.log"))[-1].read_text()
    assert "evaluating phase 1 failed" in run_log


# ── the learning summary on every exit (item 4b) ─────────────────────────────
def _run_logs(workdir):
    runs = workdir / ".specstride" / "features" / "obs-lifecycle" / "runs"
    return "\n".join(p.read_text() for p in sorted(runs.rglob("run.log")))


def test_the_learning_summary_prints_on_run_end_and_on_run_stop(tmp_path):
    _seed_applied(tmp_path, 1, 1234)
    result, workdir, events = _run_orchestrator(
        tmp_path, proposer_timeout=900, extra_env={"SPECSTRIDE_LEARNING": "suggest"})
    assert _names(events)[-1] == "run_end", result.stdout + result.stderr
    # phase 1 closed, so its hook evaluated the seed: no baseline was recorded with it
    line = "learn: phase 1 proposer_timeout 900s→1234s [seed]: insufficient (no baseline recorded"
    assert line in _run_logs(workdir) and line in result.stdout

    stopped = tmp_path / "stopped"
    stopped.mkdir()
    _seed_applied(stopped, 1, 1234)
    result, workdir, events = _run_orchestrator(
        stopped, verdict="REJECTED", max_rejects="0", proposer_timeout=900,
        extra_env={"SPECSTRIDE_LEARNING": "suggest"})
    assert "run_stop" in _names(events), result.stdout + result.stderr
    # phase 1 never closed: nothing evaluated the seed, and the summary says so
    assert "learn: phase 1 proposer_timeout 900s→1234s [seed]: not evaluated yet" in _run_logs(workdir)


def test_learning_off_prints_no_learning_summary(tmp_path):
    _seed_applied(tmp_path, 1, 1234)
    result, workdir, _events = _run_orchestrator(tmp_path, proposer_timeout=900)
    assert "learn: phase 1" not in _run_logs(workdir) and "learn: phase 1" not in result.stdout
