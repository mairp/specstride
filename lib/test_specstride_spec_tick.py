#!/usr/bin/env python3
"""Tests for ticking an APPROVED phase's task checkboxes (specstride_spec.tick_phase).

The orchestrator ticks a phase's `- [ ]` task lines only after the critic approved
the phase, so the task list stops reading "0 done" on a fully approved feature.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import specstride_spec  # noqa: E402

SPECKIT = (
    "# Tasks\r\n\r\n"
    "- [ ] T000 preamble checkbox, outside any phase\r\n\r\n"
    "## Phase 1: Setup\r\n\r\n"
    "- [ ] T001 Create the tree\r\n"
    "### Tests\r\n"
    "- [ ] T002 [P] Write the test\r\n"
    "- [x] T003 already ticked\r\n"
    "- [ ]\r\n"
    "\r\n## Phase 2: Core\r\n\r\n"
    "- [ ] T004 [US1] Build it\r\n"
    "\r\n## Dependencies\r\n\r\n"
    "- [ ] not a task of phase 2\r\n"
)

OPENSPEC = "## 1. Setup\n\n- [ ] 1.1 one\n- [ ] 1.2 two\n\n## 2. Build\n\n- [ ] 2.1 three\n"


def test_ticks_only_the_named_phase_and_preserves_every_other_byte():
    out, n = specstride_spec.tick_phase(SPECKIT, 1, "speckit-tasks")
    assert n == 2
    assert "- [x] T001 Create the tree\r\n" in out
    assert "- [x] T002 [P] Write the test\r\n" in out
    assert "- [ ] T004 [US1] Build it\r\n" in out          # phase 2 untouched
    assert "- [ ] T000 preamble" in out                     # preamble untouched
    assert "- [ ] not a task of phase 2" in out             # trailing section untouched
    assert "- [ ]\r\n" in out                                # an empty checkbox is not a task
    assert out.replace("[x] T001", "[ ] T001").replace("[x] T002", "[ ] T002") == SPECKIT


def test_is_idempotent():
    once, n1 = specstride_spec.tick_phase(SPECKIT, 1, "speckit-tasks")
    twice, n2 = specstride_spec.tick_phase(once, 1, "speckit-tasks")
    assert (n1, n2) == (2, 0) and once == twice


def test_a_phase_ends_at_the_next_level_two_heading():
    out, n = specstride_spec.tick_phase(SPECKIT, 2, "speckit-tasks")
    assert n == 1 and "- [ ] not a task of phase 2" in out and "- [ ] T001" in out


def test_openspec_change_sections_are_ticked_by_number():
    out, n = specstride_spec.tick_phase(OPENSPEC, 1, "openspec-change")
    assert n == 2 and "- [x] 1.1 one" in out and "- [ ] 2.1 three" in out


def test_native_and_unknown_phase_are_no_ops():
    assert specstride_spec.tick_phase(SPECKIT, 1, "native") == (SPECKIT, 0)
    assert specstride_spec.tick_phase(SPECKIT, 9, "speckit-tasks") == (SPECKIT, 0)


def test_ticking_does_not_change_the_plan_hash(tmp_path):
    import verification_plan
    before = verification_plan.spec_projection_hash(SPECKIT, "speckit-tasks")
    after_text, _ = specstride_spec.tick_phase(SPECKIT, 1, "speckit-tasks")
    assert verification_plan.spec_projection_hash(after_text, "speckit-tasks") == before


def test_cli_ticks_the_file_atomically_and_prints_the_count(tmp_path):
    spec = tmp_path / "tasks.md"
    spec.write_bytes(SPECKIT.encode())
    os.chmod(spec, 0o640)
    run = subprocess.run(
        [sys.executable, os.path.join(HERE, "specstride_spec.py"), "tick", "1",
         "--specs", str(spec), "--format", "speckit-tasks"],
        capture_output=True, text=True, check=True)
    assert run.stdout.strip() == "2"
    assert b"- [x] T001 Create the tree\r\n" in spec.read_bytes()
    assert oct(spec.stat().st_mode & 0o777) == "0o640"
    assert [p.name for p in tmp_path.iterdir()] == ["tasks.md"]   # no temp file left
