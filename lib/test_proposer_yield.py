"""The yield protocol: a pass that ends cleanly while its job keeps running.

A pass boundary and a measurement boundary are independent, and until the yield
existed the loop had no way to say so. Measured on project B
phase 15 (2026-09-11): a 93-minute live suite inside a 90-minute pass killed
three passes, 4.5 hours, 79-85 % of it literal `sleep` — and the first kill took
the measurement with it, because the job lived in the pass's process tree.

These drive the real proposer.sh with a fake agent, in the shape of
test_proposer_watchdog.py, and pin the eight properties the protocol has to have:
no second model invocation during the wait; the event order; the resume prompt;
a yield is not an error; the deadline; stop.flag; the per-attempt budget; and the
refusal of an `adopt` on a pid that would die with the next pass anyway.
"""
import json
import os
import subprocess
from pathlib import Path

PROPOSER = Path(__file__).parents[1] / "proposer.sh"

CONTRACT = "specstride-pass-yield/v1"


def _agent(tmp_path, body):
    """A fake `dsh` binary. $PROMPT_LOG collects each pass's prompt (last argv);
    $INVOCATIONS counts model invocations, which is how "no second model session
    during the wait" is proved rather than asserted."""
    path = tmp_path / "fake-agent"
    path.write_text(
        "#!/bin/bash\n"
        'for a in "$@"; do prompt="$a"; done\n'
        'printf "%s\\n===PASS-END===\\n" "$prompt" >> "$PROMPT_LOG"\n'
        'echo x >> "$INVOCATIONS"\n'
        + body
    )
    path.chmod(0o755)
    return path


def _yield_json(**overrides):
    """The artifact a yielding pass writes, with the fields these tests vary."""
    document = {
        "contract": CONTRACT,
        "reason": "the live suite is in flight and the evidence needs its report",
        "job": {"mode": "launch", "argv": ["/bin/sh", "-c", "sleep 1; echo done"]},
        "resume_when": {"kind": "exit_code_file"},
        "deadline_sec": 60,
        "on_resume": "read the report and write T404 from it",
    }
    document.update(overrides)
    return document


def _writes_yield(tmp_path, document, *, times=1, then="exit 0\n"):
    """Agent body that writes the yield artifact ATOMICALLY, as the contract
    requires, on its first `times` passes — so the pass after the last resume
    behaves like an ordinary pass. `times=0` means every pass."""
    payload = json.dumps(document)
    counter = tmp_path / "yields-written"
    if times:
        guard = (f'n=$(cat "{counter}" 2>/dev/null || echo 0)\n'
                 f'if (( n < {times} )); then echo $((n+1)) > "{counter}"\n')
    else:
        guard = "if true; then\n"
    return (
        guard
        + '  mkdir -p "$(dirname "$YIELD_ARTIFACT")"\n'
        + f"  cat > \"$YIELD_ARTIFACT.tmp\" <<'JSON'\n{payload}\nJSON\n"
        + '  mv "$YIELD_ARTIFACT.tmp" "$YIELD_ARTIFACT"\n'
        + "fi\n"
        + then
    )


def _run(tmp_path, agent, *, max_iter=2, env_extra=None, timeout="120",
         run_timeout=180, phase="1", attempt="1"):
    evidence = tmp_path / ".specstride" / "features" / "f" / "gates" / "GATE1-EVIDENCE.md"
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("standing prompt")
    feature_dir = tmp_path / ".specstride" / "features" / "f"
    env = os.environ.copy()
    env.update({
        "SPECSTRIDE_DSH_BIN": str(agent),
        "SPECSTRIDE_AGENT_STREAM": "false",
        "SPECSTRIDE_EVENTS": str(tmp_path / ".specstride" / "events.jsonl"),
        "SPECSTRIDE_WATCHDOG_TICK": "1",
        "PROMPT_LOG": str(tmp_path / "prompts.log"),
        "INVOCATIONS": str(tmp_path / "invocations.log"),
        # The artifact path the fake agent writes to — the real one is handed to a
        # real agent by the orchestrator's yield contract block.
        "YIELD_ARTIFACT": str(
            feature_dir / "yield" / f"phase{phase}-attempt{attempt}-run-yield.json"),
        "SPECSTRIDE_RUN_ID": "run-yield",
        "SPECSTRIDE_PROPOSER_IDLE_TIMEOUT": "900",
        "SPECSTRIDE_PROPOSER_PROGRESS_TIMEOUT": "0",
        "SPECSTRIDE_PROPOSER_REPEAT_LIMIT": "0",
        "SPECSTRIDE_PROPOSER_MAX_ERRORS": "9",
        "SPECSTRIDE_PROPOSER_MAX_NOPROGRESS": "0",
        "SPECSTRIDE_YIELD_POLL": "1",
        "SPECSTRIDE_YIELD_WAIT_EVERY": "1",
    })
    env.update(env_extra or {})
    (tmp_path / ".specstride").mkdir(exist_ok=True)
    result = subprocess.run(
        ["bash", str(PROPOSER), "-w", str(tmp_path), "-e", str(evidence),
         "-f", str(prompt), "--backend", "dsh", "-n", str(max_iter), "-s", "0",
         "--feature", "f", "--phase", phase, "--attempt", attempt,
         "--timeout", timeout],
        text=True, capture_output=True, env=env, timeout=run_timeout,
    )
    events = []
    events_file = tmp_path / ".specstride" / "events.jsonl"
    if events_file.exists():
        for line in events_file.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    return result, events


def _names(events):
    return [e["event"] for e in events]


def _invocations(tmp_path):
    path = tmp_path / "invocations.log"
    return len(path.read_text().splitlines()) if path.exists() else 0


# ── (a) no second model invocation occurs during the wait ────────────────────


def test_no_model_session_is_open_while_the_yield_waits(tmp_path):
    """The whole economic argument. Waiting inside a pass rebuilds ~250k tokens
    of context per pass; waiting here costs one syscall per tick. The job sleeps
    well past several poll ticks, and the agent must be invoked exactly twice:
    the pass that yielded, and the pass that resumed."""
    document = _yield_json(job={"mode": "launch",
                                "argv": ["/bin/sh", "-c", "sleep 4; echo finished"]})
    agent = _agent(tmp_path, _writes_yield(tmp_path, document))
    result, events = _run(tmp_path, agent, max_iter=1)

    waits = [e for e in events if e["event"] == "yield_wait"]
    assert waits, result.stderr          # it really did poll, repeatedly
    assert _invocations(tmp_path) == 2, result.stderr
    # And the wait was genuinely the long part of that pass.
    resumes = [e for e in events if e["event"] == "yield_resume"]
    assert resumes and int(resumes[0]["waited_sec"]) >= 3


# ── (b) pass_yield → yield_wait → yield_resume appear in order ───────────────


def test_the_yield_events_appear_in_order(tmp_path):
    """waited_sec is the field that makes a budget model possible at all: for the
    first time "how long the model worked" and "how long the loop was blocked"
    are separate numbers."""
    document = _yield_json(job={"mode": "launch",
                                "argv": ["/bin/sh", "-c", "sleep 3; echo finished"]})
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1)

    names = _names(events)
    assert "pass_yield" in names, result.stderr
    order = [names.index(n) for n in ("pass_yield", "yield_wait", "yield_resume")]
    assert order == sorted(order), names
    # The job is taken BEFORE pass_yield is announced, because pass_yield reports
    # the job's log path and specstride — not the agent — decides that path.
    assert names.index("yield_job_start") < names.index("pass_yield")

    yielded = next(e for e in events if e["event"] == "pass_yield")
    assert yielded["predicate_kind"] == "exit_code_file"
    assert yielded["job_mode"] == "launch"
    assert yielded["deadline_sec"] == "60"
    assert yielded["yield_index"] == "1"
    # The launched job is in specstride's OWN session, which is the property that
    # makes a pass kill unable to reach it.
    started = next(e for e in events if e["event"] == "yield_job_start")
    assert started["sid"] and started["sid"] != str(os.getsid(0))


# ── (c) the resume prompt carries the exit code and a bounded log slice ──────


def test_the_resume_prompt_carries_the_exit_code_and_a_bounded_log_slice(tmp_path):
    """The wait becomes work: the next pass is handed the result rather than
    discovering it a second time."""
    document = _yield_json(
        job={"mode": "launch",
             "argv": ["/bin/sh", "-c",
                      "for i in $(seq 1 400); do echo line-$i; done; exit 3"]},
        on_resume="write T404 from runs/live.md, then the evidence")
    result, _events = _run(
        tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)), max_iter=1,
        env_extra={"SPECSTRIDE_YIELD_LOG_HEAD": "5", "SPECSTRIDE_YIELD_LOG_TAIL": "5"})

    passes = (tmp_path / "prompts.log").read_text().split("===PASS-END===")
    assert len(passes) >= 2, result.stderr
    resumed = passes[1]
    assert "The job you yielded on has ENDED" in resumed
    assert "Exit code: 3" in resumed
    # The agent's own sentence, verbatim.
    assert "write T404 from runs/live.md, then the evidence" in resumed
    # Head AND tail, with an explicit elision marker — a reader must be able to
    # tell an elision from an absence.
    assert "line-1\n" in resumed and "line-400" in resumed
    assert "lines elided from the middle" in resumed
    assert "line-200" not in resumed
    # The pass that yielded was NOT told any of this (it had not happened yet).
    assert "The job you yielded on has ENDED" not in passes[0]


# ── (d) a yield does not increment the error breaker ─────────────────────────


def test_a_yield_is_not_an_agent_error_and_not_a_stall(tmp_path):
    """A yielding pass writes only .specstride, so every futility detector reads it
    as blocked. It is not: it is DECLARED waiting, which is exactly the
    distinction none of the three detectors could make."""
    document = _yield_json(job={"mode": "launch", "argv": ["/bin/true"]})
    body = _writes_yield(tmp_path, document, times=3,
                         then='echo work >> "$PWD/work.txt"\nexit 0\n')
    result, events = _run(
        tmp_path, _agent(tmp_path, body), max_iter=3,
        env_extra={"SPECSTRIDE_PROPOSER_MAX_ERRORS": "2",
                   "SPECSTRIDE_PROPOSER_MAX_NOPROGRESS": "2",
                   "SPECSTRIDE_YIELD_MAX_PER_ATTEMPT": "9"})

    # Three yields with an error budget of 2 and a no-progress budget of 2: the
    # loop must reach max-iter, not either breaker.
    assert result.returncode == 4, result.stderr
    assert [e for e in events if e["event"] == "iter_error"] == []
    assert [e for e in events if e["event"] == "iter_no_progress"] == []
    assert len([e for e in events if e["event"] == "pass_yield"]) == 3
    # yield + resume is ONE logical pass, so the iteration is handed back: three
    # yields still leave all three iterations for real work.
    assert _invocations(tmp_path) == 6


# ── (e) deadline_sec expiry exits 9 ──────────────────────────────────────────


def test_a_yield_that_outruns_its_own_deadline_exits_nine(tmp_path):
    """deadline_sec is mandatory because the workdir flock is held for the whole
    wait. When it expires the job is LEFT ALONE — killing someone's 90-minute
    measurement to report a timeout would be the original bug again."""
    document = _yield_json(
        job={"mode": "launch", "argv": ["/bin/sh", "-c", "sleep 90"]},
        deadline_sec=3)
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          run_timeout=120)

    assert result.returncode == 9, result.stderr
    timeouts = [e for e in events if e["event"] == "yield_timeout"]
    assert timeouts and timeouts[0]["reason"] == "deadline"
    assert int(timeouts[0]["waited_sec"]) >= 3
    stops = [e for e in events if e["event"] == "run_stop"]
    assert stops and stops[-1]["reason"] == "proposer_yield_timeout"
    # The job outlived the halt.
    started = next(e for e in events if e["event"] == "yield_job_start")
    assert _alive(int(started["pid"])), "the job must be left alone, not killed"
    os.kill(int(started["pid"]), 9)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ── (f) stop.flag during a yield exits 6 with the job still alive ────────────


def test_stop_during_a_yield_exits_six_and_leaves_the_job_running(tmp_path):
    """`specstride stop` must not have to wait out a two-hour yield, and must not
    destroy the job to be prompt about it."""
    document = _yield_json(
        job={"mode": "launch", "argv": ["/bin/sh", "-c", "sleep 90"]},
        deadline_sec=120)
    stop_flag = tmp_path / ".specstride" / "stop.flag"
    # The flag lands while the wait is in progress, not at a pass boundary.
    agent = _agent(tmp_path, _writes_yield(
        tmp_path, document,
        then=f'( sleep 3; touch "{stop_flag}" ) >/dev/null 2>&1 &\nexit 0\n'))
    result, events = _run(tmp_path, agent, run_timeout=120)

    assert result.returncode == 6, result.stderr
    stops = [e for e in events if e["event"] == "run_stop"]
    assert stops and stops[-1]["reason"] == "stop_flag"
    assert stops[-1]["yield"] == "true"
    started = next(e for e in events if e["event"] == "yield_job_start")
    assert _alive(int(started["pid"])), "the job is specstride-owned; a stop leaves it alive"
    os.kill(int(started["pid"]), 9)


# ── (g) max_yields_per_attempt exhaustion exits 9 ────────────────────────────


def test_too_many_yields_in_one_attempt_exits_nine(tmp_path):
    """A yield is for waiting on ONE long job, not for making a phase out of
    waiting. The bound is what keeps "declare a yield" from becoming the new
    "sleep 15"."""
    document = _yield_json(job={"mode": "launch", "argv": ["/bin/true"]})
    result, events = _run(
        tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document, times=0)),
        max_iter=20, env_extra={"SPECSTRIDE_YIELD_MAX_PER_ATTEMPT": "2"},
        run_timeout=180)

    assert result.returncode == 9, result.stderr
    assert len([e for e in events if e["event"] == "pass_yield"]) == 2
    stops = [e for e in events if e["event"] == "run_stop"]
    assert stops and stops[-1]["reason"] == "proposer_yield_budget"
    assert stops[-1]["max"] == "2"


# ── (h) an adopt yield naming a pid in the pass's own session is refused ─────


def test_an_adopt_in_the_passes_own_session_is_refused(tmp_path):
    """`adopt` exists for a pass that already detached the job. A pid in the
    pass's OWN session was never detached: it dies with the next watchdog kill,
    so a yield on it would be a wait for something already doomed."""
    document = _yield_json(job={"mode": "adopt", "pid": 1},
                           resume_when={"kind": "pid"})
    payload = json.dumps(document)
    # Start a child in this pass's own session and name ITS pid, patched in at
    # write time so the refusal is about the session, not about a stale pid.
    body = (
        'sleep 30 &\n'
        'child=$!\n'
        'mkdir -p "$(dirname "$YIELD_ARTIFACT")"\n'
        f"cat > \"$YIELD_ARTIFACT.tmp.raw\" <<'JSON'\n{payload}\nJSON\n"
        'sed "s/\\"pid\\": 1/\\"pid\\": $child/" "$YIELD_ARTIFACT.tmp.raw" > "$YIELD_ARTIFACT.tmp"\n'
        'mv "$YIELD_ARTIFACT.tmp" "$YIELD_ARTIFACT"\n'
        'exit 0\n'
    )
    result, events = _run(tmp_path, _agent(tmp_path, body), max_iter=1)

    invalid = [e for e in events if e["event"] == "yield_invalid"]
    assert invalid, result.stderr
    assert "shares the pass session" in invalid[0]["reason"]
    assert [e for e in events if e["event"] == "pass_yield"] == []
    # Refused, not fatal: the pass is accounted exactly as one that wrote no
    # evidence, so the loop reaches max-iter.
    assert result.returncode == 4, result.stderr


# ── the schema, and the one predicate that ships disabled ────────────────────


def test_the_command_predicate_ships_disabled(tmp_path):
    """It is arbitrary execution with no pass running. The four file/pid
    predicates cover every real case, so it is refused unless an operator turned
    it on — and even then it takes fixed argv, never a shell string."""
    document = _yield_json(resume_when={"kind": "command", "argv": ["/bin/true"]})
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1)

    invalid = [e for e in events if e["event"] == "yield_invalid"]
    assert invalid, result.stderr
    assert "DISABLED" in invalid[0]["reason"]
    assert [e for e in events if e["event"] == "pass_yield"] == []


def test_a_yield_without_a_deadline_is_refused(tmp_path):
    """The run holds this workdir's lock for the whole wait, so an unbounded
    yield would hold it forever."""
    document = _yield_json()
    document.pop("deadline_sec")
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1)

    invalid = [e for e in events if e["event"] == "yield_invalid"]
    assert invalid, result.stderr
    assert "deadline_sec is REQUIRED" in invalid[0]["reason"]


def test_a_grep_predicate_resumes_on_the_pattern(tmp_path):
    """The proposer's own polling idiom, moved out of the pass: `= (N passed)`
    in a live log is what an agent was greping for with `until ... sleep 15`."""
    log = tmp_path / "live.log"
    document = _yield_json(
        job={"mode": "launch",
             "argv": ["/bin/sh", "-c",
                      f'echo running > {log}; sleep 2; echo "= 41 passed =" >> {log}']},
        resume_when={"kind": "grep", "path": str(log),
                     "pattern": "= (.*passed|.*failed)"})
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1)

    resumes = [e for e in events if e["event"] == "yield_resume"]
    assert resumes, result.stderr
    assert resumes[0]["predicate_kind"] == "grep"


def test_a_watchdog_killed_pass_does_not_get_to_yield(tmp_path):
    """The protocol asks a pass to end VOLUNTARILY. A pass the watchdog had to
    end did not, so its artifact is refused and the kill is accounted normally —
    otherwise "write a yield, then keep working" would buy an extra ceiling."""
    document = _yield_json(job={"mode": "launch", "argv": ["/bin/true"]})
    # The stall outlives the kill only long enough to prove the point: an
    # orphaned child keeps the pass's stderr pipe open until it exits.
    body = _writes_yield(tmp_path, document, then="sleep 25\n")
    result, events = _run(tmp_path, _agent(tmp_path, body), max_iter=1, timeout="3",
                          run_timeout=120)

    kills = [e for e in events if e["event"] == "pass_killed"]
    assert kills and kills[0]["reason"] == "hard_cap", result.stderr
    invalid = [e for e in events if e["event"] == "yield_invalid"]
    assert invalid and "killed by the watchdog" in invalid[0]["reason"]
    assert [e for e in events if e["event"] == "pass_yield"] == []


def test_evidence_still_wins_over_a_yield(tmp_path):
    """A pass that wrote both is simply done — the gate file ends the phase, as
    it does everywhere else in the loop."""
    evidence = tmp_path / ".specstride" / "features" / "f" / "gates" / "GATE1-EVIDENCE.md"
    document = _yield_json(job={"mode": "launch", "argv": ["/bin/sh", "-c", "sleep 60"]})
    body = _writes_yield(
        tmp_path, document,
        then=f'mkdir -p "$(dirname "{evidence}")"\nprintf "# done\\n" > "{evidence}"\nexit 0\n')
    result, events = _run(tmp_path, _agent(tmp_path, body), max_iter=1)

    assert result.returncode == 0, result.stderr
    assert [e for e in events if e["event"] == "pass_yield"] == []
    assert "evidence_written" in _names(events)


def test_the_orchestrator_tells_the_proposer_the_protocol_exists(tmp_path):
    """Without the contract block no agent will ever use this. The incident's
    agent proved it: it hand-rolled `setsid nohup` wrappers instead."""
    orchestrator = Path(__file__).parents[1] / "orchestrator.sh"
    source = orchestrator.read_text()
    # Drive the real function rather than asserting on the file's text.
    body = source.split("emit_yield_contract() {", 1)[1].split("\n}\n", 1)[0]
    script = (
        "set -uo pipefail\n"
        f"FEATURE_DIR={tmp_path}\nWORKDIR={tmp_path}\nSPECSTRIDE_RUN_ID=run-yield\n"
        "emit_yield_contract() {" + body + "\n}\n"
        "emit_yield_contract 15 2\n"
    )
    out = subprocess.run(["bash", "-c", script], text=True, capture_output=True).stdout

    assert CONTRACT in out
    assert "phase15-attempt2-run-yield.json" in out
    assert "deadline_sec" in out and "REQUIRED" in out
    # The point of the block, stated where the agent will read it.
    assert "Do NOT sleep, poll, tail" in out
    assert "exit_code_file" in out and "file_stable" in out


# ── the agent is never left without the protocol ────────────────────────────
def _budget_script(tmp_path, name, *, assembled=200_000, protected=None):
    """Drive the real append_budgeted_block against a prompt that is already over
    budget, as it was for every phase from 5 on in project A 004."""
    orchestrator = Path(__file__).parents[1] / "orchestrator.sh"
    source = orchestrator.read_text()
    body = source.split("append_budgeted_block() {", 1)[1].split("\n}\n", 1)[0]
    out = tmp_path / "prompt.md"
    out.write_text("x" * assembled)
    events = tmp_path / "events.log"
    env_line = (f"SPECSTRIDE_PROMPT_PROTECTED_BLOCKS='{protected}'\n"
                if protected is not None else "")
    script = (
        "set -uo pipefail\n"
        "SPECSTRIDE_PROMPT_MAX_BYTES=180000\n"
        + env_line
        + ': "${SPECSTRIDE_PROMPT_PROTECTED_BLOCKS=yield_contract}"\n'
        "log() { echo \"$*\" >&2; }\n"
        f"specstride_emit() {{ echo \"$1\" >> {events}; }}\n"
        "block() { echo 'THE BLOCK BODY'; }\n"
        "append_budgeted_block() {" + body + "\n}\n"
        f"append_budgeted_block {out} {name} block\n"
    )
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True)
    emitted = events.read_text().split() if events.exists() else []
    return out.read_text(), result.stderr, emitted


def test_the_yield_contract_is_kept_even_over_the_prompt_budget(tmp_path):
    """Appended last, it was the block dropped whenever the rest of the prompt was
    already over budget. That was every phase from 5 on in project A 004,
    and it cost phase 15 nineteen of its thirty iterations."""
    prompt, log, emitted = _budget_script(tmp_path, "yield_contract")
    assert prompt.endswith("THE BLOCK BODY\n")
    assert "kept over budget (protected)" in log
    assert emitted == ["prompt_block_protected"]


def test_an_unprotected_block_is_still_dropped_over_budget(tmp_path):
    prompt, log, emitted = _budget_script(tmp_path, "design_context")
    assert "THE BLOCK BODY" not in prompt
    assert "dropped" in log
    assert emitted == ["prompt_block_dropped"]


def test_the_protected_list_is_configurable(tmp_path):
    prompt, _, emitted = _budget_script(tmp_path, "yield_contract", protected="")
    assert "THE BLOCK BODY" not in prompt
    assert emitted == ["prompt_block_dropped"]


def test_short_passes_in_a_row_are_told_to_yield(tmp_path):
    """Passes that end within seconds with no evidence and no yield are an agent
    waiting on a job by ending its turn. No breaker sees them, so the next pass
    is told how to yield, including this attempt's artifact path."""
    result, events = _run(tmp_path, _agent(tmp_path, "exit 0\n"), max_iter=3)
    prompts = (tmp_path / "prompts.log").read_text().split("===PASS-END===")
    assert "short passes in a row" in result.stderr
    assert [e["consec"] for e in events if e["event"] == "iter_short_pass"] == ["2", "3"] \
        or [e["consec"] for e in events if e["event"] == "iter_short_pass"] == [2, 3]
    # Passes 1 and 2 carry no nudge; pass 3 does, with the real artifact path.
    assert "without evidence" not in prompts[0] and "without evidence" not in prompts[1]
    assert "You have ended 2 passes in a row" in prompts[2]
    assert "phase1-attempt1-run-yield.json" in prompts[2]
    assert "specstride-pass-yield/v1" in prompts[2]


def test_the_short_pass_nudge_can_be_switched_off(tmp_path):
    result, events = _run(tmp_path, _agent(tmp_path, "exit 0\n"), max_iter=3,
                          env_extra={"SPECSTRIDE_SHORT_PASS_NUDGE_AFTER": "0"})
    assert "iter_short_pass" not in _names(events)
    assert "You have ended" not in (tmp_path / "prompts.log").read_text()


def test_a_long_pass_resets_the_short_pass_count(tmp_path):
    result, events = _run(tmp_path, _agent(tmp_path, "exit 0\n"), max_iter=3,
                          env_extra={"SPECSTRIDE_SHORT_PASS_SEC": "0"})
    assert "iter_short_pass" not in _names(events)


# ── the resume prompt lists every failure of the whole job log ──────────────
def test_the_resume_prompt_lists_a_failure_the_log_slice_elides(tmp_path):
    """A failure printed in the middle of a long job log is elided from the
    head+tail slice. The resume prompt must still name it (failure ledger)."""
    job = ("for i in $(seq 1 300); do echo step $i ok; done; "
           "echo 'FAIL lab-acl not Ready=True within 600s'; "
           "for i in $(seq 1 300); do echo step $i ok; done")
    document = _yield_json(job={"mode": "launch", "argv": ["/bin/sh", "-c", job]})
    evidence = tmp_path / ".specstride" / "features" / "f" / "gates" / "GATE1-EVIDENCE.md"
    body = _writes_yield(tmp_path, document,
        then=f'mkdir -p "$(dirname "{evidence}")"\nprintf "# done\\n" > "{evidence}"\nexit 0\n')
    body = body.replace(f'printf "# done\\n" > "{evidence}"\n',
                        f'[[ -f "{tmp_path}/yields-written" ]] && [[ $(cat "{tmp_path}/yields-written") -ge 1 ]] '
                        f'&& grep -q "resume from its result" <<<"$prompt" && printf "# done\\n" > "{evidence}"\n')
    result, events = _run(tmp_path, _agent(tmp_path, body), max_iter=2)
    prompts = (tmp_path / "prompts.log").read_text().split("===PASS-END===")
    resume = next(p for p in prompts if "resume from its result" in p)
    assert "lines elided from the middle" in resume
    assert "Every failure the job reported" in resume
    assert "[NEW]" in resume and "FAIL lab-acl not Ready=True" in resume
    ledger = tmp_path / ".specstride" / "features" / "f" / "failure-ledger.jsonl"
    assert ledger.exists()


def test_a_grep_predicate_anchored_with_caret_matches_a_later_line(tmp_path):
    """`^MARKER` must match the marker at the start of ANY line, not only at the
    start of the file (project A 004 phase 16, 2026-09-29: a
    `^DECISION: APPROVED` wait never resumed because the line was not line 1)."""
    decision = tmp_path / "DECISION.md"
    decision.write_text("# heading\nsome text\n")
    document = _yield_json(
        job={"mode": "launch",
             "argv": ["/bin/sh", "-c", f'sleep 2; printf "DECISION: APPROVED\\n" >> {decision}']},
        resume_when={"kind": "grep", "path": str(decision), "pattern": "^DECISION: APPROVED"},
        deadline_sec=30)
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1)

    resumes = [e for e in events if e["event"] == "yield_resume"]
    assert resumes, result.stderr
    assert [e for e in events if e["event"] == "yield_timeout"] == []


# ── infrastructure awareness (2026-10-14) ─────────────────────────────────────
# A four-hour replay chain ran on a lab whose bgp unit systemd refused to start,
# and a file_exists wait sat out its whole deadline after its job had died. The
# loop could see neither. These pin the three things that make it see.


def _health_cmd(tmp_path, healthy_file):
    """A health check that is healthy while `healthy_file` exists."""
    path = tmp_path / "health.sh"
    path.write_text(f'#!/bin/bash\n[ -f "{healthy_file}" ] && exit 0\n'
                    'echo "bgp.service: start-limit-hit on leaf01"\nexit 3\n')
    path.chmod(0o755)
    return str(path)


def test_a_pass_on_unhealthy_infrastructure_is_told_so_first(tmp_path):
    agent = _agent(tmp_path, "exit 0\n")
    result, events = _run(tmp_path, agent, max_iter=1, env_extra={
        "SPECSTRIDE_HEALTH_CMD": _health_cmd(tmp_path, tmp_path / "never")})
    prompt = (tmp_path / "prompts.log").read_text()
    assert "INFRASTRUCTURE HEALTH CHECK FAILED" in prompt, result.stderr
    assert "start-limit-hit on leaf01" in prompt
    failed = [e for e in events if e["event"] == "health_check_failed"]
    assert failed and failed[0]["where"] == "pass_start"


def test_a_healthy_check_adds_nothing_to_the_prompt(tmp_path):
    healthy = tmp_path / "ok"
    healthy.write_text("")
    agent = _agent(tmp_path, "exit 0\n")
    _run(tmp_path, agent, max_iter=1, env_extra={
        "SPECSTRIDE_HEALTH_CMD": _health_cmd(tmp_path, healthy)})
    assert "HEALTH CHECK FAILED" not in (tmp_path / "prompts.log").read_text()


def test_a_wait_ends_early_when_its_job_dies_with_the_predicate_unmet(tmp_path):
    """file_exists on a file the job never writes: before, the wait sat out the
    whole deadline; now it ends as soon as the job is gone."""
    document = _yield_json(
        job={"mode": "launch", "argv": ["/bin/sh", "-c", "sleep 1; exit 5"]},
        resume_when={"kind": "file_exists", "path": str(tmp_path / "never-written")},
        deadline_sec=120)
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1, run_timeout=100)
    early = [e for e in events if e["event"] == "yield_ended_early"]
    assert early and early[0]["reason"] == "job_died", result.stderr
    assert int(early[0]["waited_sec"]) < 60
    prompts = (tmp_path / "prompts.log").read_text()
    assert "ended EARLY (job_died)" in prompts
    assert "do not assume it finished" in prompts


def test_a_wait_ends_early_when_the_infrastructure_stays_unhealthy(tmp_path):
    healthy = tmp_path / "ok"
    healthy.write_text("")
    document = _yield_json(
        job={"mode": "launch", "argv": ["/bin/sh", "-c", f"sleep 2; rm {healthy}; sleep 60"]},
        deadline_sec=120)
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1, run_timeout=100, env_extra={
                              "SPECSTRIDE_HEALTH_CMD": _health_cmd(tmp_path, healthy),
                              "SPECSTRIDE_HEALTH_EVERY": "1",
                              "SPECSTRIDE_HEALTH_FAILS": "2"})
    early = [e for e in events if e["event"] == "yield_ended_early"]
    assert early and early[0]["reason"] == "unhealthy", result.stderr
    prompts = (tmp_path / "prompts.log").read_text()
    assert "ended EARLY (unhealthy)" in prompts
    assert "STILL RUNNING" in prompts
    started = next(e for e in events if e["event"] == "yield_job_start")
    assert _alive(int(started["pid"])), "the job is the agent's to stop, not specstride's"
    os.kill(int(started["pid"]), 9)


def test_a_job_that_takes_the_infrastructure_down_on_purpose_can_opt_out(tmp_path):
    document = _yield_json(
        job={"mode": "launch", "argv": ["/bin/sh", "-c", "sleep 4"]},
        requires_healthy_infra=False)
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1, run_timeout=100, env_extra={
                              "SPECSTRIDE_HEALTH_CMD": _health_cmd(tmp_path, tmp_path / "never"),
                              "SPECSTRIDE_HEALTH_EVERY": "1"})
    assert [e for e in events if e["event"] == "yield_resume"], result.stderr
    assert not [e for e in events if e["event"] == "yield_ended_early"]


def test_requires_healthy_infra_must_be_a_boolean(tmp_path):
    document = _yield_json(requires_healthy_infra="no")
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1)
    invalid = [e for e in events if e["event"] == "yield_invalid"]
    assert invalid and "requires_healthy_infra" in invalid[0]["reason"]


def test_a_launch_yield_waiting_on_a_file_nobody_writes_ends_when_the_job_exits(tmp_path):
    """Measured live 2026-10-14: a launch-mode yield named an exit_code_file path
    that only the agent's own (ignored) launch recipe would have written. The job
    exited rc=1; specstride wrote its OWN rc file; the wait would have sat out
    7200 s and halted the run."""
    document = _yield_json(
        job={"mode": "launch", "argv": ["/bin/sh", "-c", "exit 1"]},
        resume_when={"kind": "exit_code_file", "path": str(tmp_path / "agent-recipe.exit")},
        deadline_sec=600)
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1, run_timeout=100)
    early = [e for e in events if e["event"] == "yield_ended_early"]
    assert early and early[0]["reason"] == "job_died", result.stderr
    assert int(early[0]["waited_sec"]) < 60
    assert result.returncode != 9


# ── --health-cmd auto: the proposer writes and owns the check ────────────────


def _auto_path(tmp_path):
    return tmp_path / ".specstride" / "features" / "f" / "health-check"


def _auto_env():
    return {"SPECSTRIDE_HEALTH_CMD": "auto"}


def test_auto_with_no_check_tells_the_pass_to_write_one(tmp_path):
    _run(tmp_path, _agent(tmp_path, "exit 0\n"), max_iter=1, env_extra=_auto_env())
    prompt = (tmp_path / "prompts.log").read_text()
    assert "NOT WRITTEN YET (missing)" in prompt
    assert str(_auto_path(tmp_path)) in prompt


def test_auto_refuses_a_healthy_infra_yield_until_the_check_exists(tmp_path):
    document = _yield_json(job={"mode": "launch", "argv": ["/bin/sh", "-c", "sleep 1"]})
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1, env_extra=_auto_env())
    invalid = [e for e in events if e["event"] == "yield_invalid"]
    assert invalid and "health check missing" in invalid[0]["reason"], result.stderr
    assert not [e for e in events if e["event"] == "yield_job_start"]


def test_auto_still_allows_a_yield_that_takes_the_infra_down_on_purpose(tmp_path):
    document = _yield_json(job={"mode": "launch", "argv": ["/bin/sh", "-c", "sleep 1"]},
                           requires_healthy_infra=False)
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1, env_extra=_auto_env())
    assert [e for e in events if e["event"] == "yield_resume"], result.stderr


def test_auto_refuses_a_hollow_check(tmp_path):
    path = _auto_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/bash\n# always fine\nset -e\nexit 0\n")
    path.chmod(0o755)
    _run(tmp_path, _agent(tmp_path, "exit 0\n"), max_iter=1, env_extra=_auto_env())
    assert "refused: it does nothing but succeed" in (tmp_path / "prompts.log").read_text()


def test_auto_runs_the_check_the_proposer_wrote(tmp_path):
    """Pass 1 writes a failing check; pass 2 starts with its output."""
    path = _auto_path(tmp_path)
    body = (f'if [ ! -e "{path}" ]; then\n'
            f'  mkdir -p "$(dirname "{path}")"\n'
            f"  printf '#!/bin/bash\\necho \"leaf01 bgp: inactive\"\\nexit 2\\n' > \"{path}\"\n"
            f'  chmod +x "{path}"\n'
            'fi\nexit 0\n')
    result, events = _run(tmp_path, _agent(tmp_path, body), max_iter=2, env_extra=_auto_env())
    passes = (tmp_path / "prompts.log").read_text().split("===PASS-END===")
    assert "INFRASTRUCTURE HEALTH CHECK FAILED" not in passes[0]
    assert "INFRASTRUCTURE HEALTH CHECK FAILED" in passes[1], result.stderr
    assert "leaf01 bgp: inactive" in passes[1]
    assert [e for e in events if e["event"] == "health_check_adopted"]


def test_auto_shows_a_changed_check_as_a_diff(tmp_path):
    path = _auto_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("#!/bin/bash\nping -c1 -W1 127.0.0.1 >/dev/null || exit 3\n")
    path.chmod(0o755)
    body = (f'if ! grep -q weakened "{path}"; then\n'
            f"  printf '#!/bin/bash\\n# weakened\\ntrue\\necho ok\\n' > \"{path}\"\nfi\nexit 0\n")
    result, events = _run(tmp_path, _agent(tmp_path, body), max_iter=2, env_extra=_auto_env())
    passes = (tmp_path / "prompts.log").read_text().split("===PASS-END===")
    assert "health check CHANGED" in passes[1], result.stderr
    assert "+# weakened" in passes[1] and "-ping -c1" in passes[1]
    assert [e for e in events if e["event"] == "health_check_changed"]


def test_a_yield_whose_completion_marker_already_exists_is_refused(tmp_path):
    """Measured live 2026-10-14: an adopted chain's exit_code_file named a .rc an
    earlier job had left behind; the wait ended after 0 s while the chain ran."""
    stale = tmp_path / "chain.rc"
    stale.write_text("0\n")
    document = _yield_json(job={"mode": "launch", "argv": ["/bin/sh", "-c", "sleep 1"]},
                           resume_when={"kind": "exit_code_file", "path": str(stale)})
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1)
    invalid = [e for e in events if e["event"] == "yield_invalid"]
    assert invalid and "already exists" in invalid[0]["reason"], result.stderr
    assert not [e for e in events if e["event"] == "yield_job_start"]


def test_a_fresh_marker_path_is_still_accepted(tmp_path):
    marker = tmp_path / "fresh.rc"
    document = _yield_json(job={"mode": "launch",
                                "argv": ["/bin/sh", "-c", f"sleep 1; echo 0 > {marker}"]},
                           resume_when={"kind": "exit_code_file", "path": str(marker)})
    result, events = _run(tmp_path, _agent(tmp_path, _writes_yield(tmp_path, document)),
                          max_iter=1)
    assert [e for e in events if e["event"] == "yield_resume"], result.stderr
