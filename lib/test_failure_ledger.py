"""The failure ledger: every failure in a yielded job's whole log, NEW vs RECURRING.

Pinned on the agentic-netops-srl 004 phase-15 incident (2026-09-25/27): a
failure printed in the middle of a 20-hour job log (`lab-acl` never created) was
reported by two consecutive jobs and fixed by neither resuming pass, because the
resume prompt only showed the log's head and tail.
"""
import json
import subprocess
import sys
from pathlib import Path

LEDGER = Path(__file__).parent / "failure_ledger.py"


def _run(tmp_path, log_text, job, *extra):
    log = tmp_path / f"{job}.log"
    log.write_text(log_text)
    out = subprocess.run(
        [sys.executable, str(LEDGER), "--log", str(log),
         "--ledger", str(tmp_path / "ledger.jsonl"), "--job", job, *extra],
        text=True, capture_output=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _padded(middle):
    # 500 lines of noise around the failure, so it sits where the head+tail
    # slice would elide it.
    noise = "".join(f"step {i}: ok\n" for i in range(250))
    return noise + middle + noise


def test_a_failure_in_the_middle_of_the_log_is_reported(tmp_path):
    out = _run(tmp_path, _padded(
        '2026-09-27T07:49:06Z [ERROR] [ServicesReady] FAIL lab-acl not Ready=True within 600s\n'),
        "job1")
    assert "Every failure the job reported (1 distinct" in out
    assert "[NEW]" in out and "lab-acl not Ready=True" in out


def test_the_same_failure_is_deduplicated_across_timestamps_and_ids(tmp_path):
    log = ("2026-09-27T07:49:06Z FAIL claim 8fc59e2a49ea481f missing\n"
           "2026-09-27T08:01:10Z FAIL claim 92bd5e32fd96dce2 missing\n")
    out = _run(tmp_path, log, "job1")
    assert "(1 distinct, 2 matching lines" in out
    assert "(x2)" in out


def test_a_failure_seen_in_earlier_jobs_is_recurring(tmp_path):
    line = '2026-09-26T10:00:00Z [ERROR] FAIL lab-acl not Ready=True within 600s\n'
    _run(tmp_path, _padded(line), "job1")
    _run(tmp_path, "all good\n", "job2")
    out = _run(tmp_path, _padded(line.replace("2026-09-26T10", "2026-09-27T11")), "job3")
    assert "[RECURRING 1/2 earlier jobs]" in out


def test_recurring_failures_are_listed_before_new_ones(tmp_path):
    old = "FAIL verify-topology-view (exit 2)\n"
    _run(tmp_path, old, "job1")
    out = _run(tmp_path, "FAIL brand-new-step (exit 1)\n" + old, "job2")
    lines = [l for l in out.splitlines() if l.startswith("- [")]
    assert lines[0].startswith("- [RECURRING") and "verify-topology-view" in lines[0]
    assert lines[1].startswith("- [NEW]") and "brand-new-step" in lines[1]


def test_a_rerun_of_the_same_job_key_does_not_count_itself(tmp_path):
    line = "FAIL once\n"
    _run(tmp_path, line, "job1")
    out = _run(tmp_path, line, "job1")
    assert "[NEW]" in out
    jobs = [json.loads(l) for l in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert sum(1 for r in jobs if r["kind"] == "job") == 1


def test_output_is_capped(tmp_path):
    log = "".join(f"FAIL distinct-step-{chr(97 + i % 26)}{chr(97 + i // 26)}\n" for i in range(30))
    out = _run(tmp_path, log, "job1", "--max", "5")
    assert sum(1 for l in out.splitlines() if l.startswith("- [")) == 5
    assert "25 more distinct failure(s) not shown" in out


def test_a_clean_log_with_no_history_prints_nothing(tmp_path):
    assert _run(tmp_path, "all good\n", "job1") == ""


def test_a_missing_log_prints_nothing_and_does_not_fail(tmp_path):
    out = subprocess.run(
        [sys.executable, str(LEDGER), "--log", str(tmp_path / "absent.log"),
         "--ledger", str(tmp_path / "l.jsonl"), "--job", "j"],
        text=True, capture_output=True)
    assert out.returncode == 0 and out.stdout == ""
