"""`specstride --reverse` (lib/reverse.py): the linter, the guard and the plan.

The lint fixtures are generated: every failing case is `good/` with ONE mutation
(the MUTATIONS table), made in tmp_path, so the fixture count stays honest and a
change to `good/` that breaks a rule's baseline fails every case at once.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

import reverse
import reverse_inventory
import specstride_spec
import verification_plan

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "reverse")
GOOD = os.path.join(FIXTURES, "good")


# ── helpers ──────────────────────────────────────────────────────────────────

def copy_fixture(tmp_path, name, dest=None):
    target = tmp_path / (dest or name)
    shutil.copytree(os.path.join(FIXTURES, name), target, symlinks=True)
    return target


def src_mini(tmp_path):
    return copy_fixture(tmp_path, "src-mini")


def good(tmp_path):
    return copy_fixture(tmp_path, "good")


def edit(path, old, new, count=1):
    text = path.read_text()
    assert old in text, "%s not in %s" % (old, path)
    path.write_text(text.replace(old, new, count))


def git(src, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", "-C", str(src), *args], check=True,
                          capture_output=True, text=True, env=env)


def lint(feature, src=None, upto=5, **kw):
    return reverse.lint(str(feature), str(src) if src else None, upto=upto, **kw).as_dict()


def rules(findings, severity="errors"):
    return {f["rule"] for f in findings[severity]}


# ── the golden fixture ───────────────────────────────────────────────────────

def test_good_is_clean_and_its_citations_resolve_into_src_mini(tmp_path):
    """Pins good/ to src-mini/: editing one fixture without the other fails here."""
    findings = lint(GOOD, os.path.join(FIXTURES, "src-mini"))
    assert findings == {"errors": [], "warnings": []}


def test_good_tasks_pass_the_speckit_adapter():
    with open(os.path.join(GOOD, "tasks.md"), encoding="utf-8") as handle:
        ok, count, errors = specstride_spec.validate(handle.read(), "speckit-tasks")
    assert ok and count == 6, errors


def test_good_carries_nothing_outside_the_speckit_layout():
    present = sorted(os.path.relpath(os.path.join(d, f), GOOD)
                     for d, _dirs, files in os.walk(GOOD) for f in files)
    assert "README.md" not in present and not any(p.startswith("proofs") for p in present)


# ── one mutation per rule ────────────────────────────────────────────────────

def m_remove(rel):
    def mutate(feature, _src):
        os.remove(feature / rel)
    return mutate


def m_edit(rel, old, new, count=1):
    def mutate(feature, _src):
        edit(feature / rel, old, new, count)
    return mutate


def m_write(rel, text):
    def mutate(feature, _src):
        (feature / rel).parent.mkdir(parents=True, exist_ok=True)
        (feature / rel).write_text(text)
    return mutate


def m_contracts_gone(feature, _src):
    shutil.rmtree(feature / "contracts")
    (feature / "contracts").mkdir()


def m_template_comment(feature, _src):
    with open(os.path.join(reverse.SPECSTRIDE_TEMPLATES, "spec-template.md")) as handle:
        comment = re.search(r'<!--.*?-->', handle.read(), re.S).group(0)
    edit(feature / "spec.md", "### Edge Cases\n", "### Edge Cases\n\n%s\n" % comment)


EV = "| kind=observed | confidence=0.95 | contradicts=none | validation=verified -->"
FR2_EV = "<!-- evidence: tally/cli.py:24-26 " + EV

MUTATIONS = [
    # (id, expected error rule, mutation)   — warning cases are listed separately
    ("missing-plan", "structure.required", m_remove("plan.md")),
    ("extra-readme", "structure.extra", m_write("README.md", "# not Spec Kit\n")),
    ("empty-contracts", "structure.contracts", m_contracts_gone),
    ("mandatory-heading", "template.mandatory",
     m_edit("spec.md", "## Success Criteria *(mandatory)*", "## Outcomes")),
    ("family-heading", "template.family",
     m_edit("memory/constitution.md",
            "## Constraints\n\n- Python 3.10 or newer (`pyproject.toml` line 5).\n\n"
            "## Development Workflow\n", "")),
    ("date-placeholder", "placeholder", m_edit("plan.md", "**Date**: 2026-09-22", "**Date**: [DATE]")),
    ("upper-placeholder", "placeholder",
     m_edit("memory/constitution.md", "# tally Constitution", "# [PROJECT_NAME] Constitution")),
    ("example-placeholder", "placeholder",
     m_edit("research.md", "**Rationale**: unknown.", "**Rationale**: [e.g., speed]")),
    ("template-comment", "placeholder", m_template_comment),
    ("fr-format", "spec.fr-format", m_edit("spec.md", "- **FR-006**:", "- **FR-06**:")),
    ("fr-unique", "spec.fr-unique", m_edit("spec.md", "- **FR-006**:", "- **FR-005**:")),
    ("sc-unique", "spec.sc-unique", m_edit("spec.md", "- **SC-002**:", "- **SC-001**:")),
    ("story-format", "spec.story-format",
     m_edit("spec.md", "(Priority: P3)\n", "(P3)\n")),
    ("story-numbers", "spec.story-numbers",
     m_edit("spec.md", "### User Story 2 - ", "### User Story 7 - ")),
    ("story-parts", "spec.story-parts",
     m_edit("spec.md", "**Independent Test**: with `coffee` saved", "Test: with `coffee` saved")),
    ("clarifications", "spec.clarifications",
     m_edit("spec.md", "## Assumptions\n",
            "## Assumptions\n\n" + "- [NEEDS CLARIFICATION: x]\n" * 4)),
    ("tasks-grammar", "tasks.grammar",
     m_edit("tasks.md", "## Phase 6: Polish", "## Phase 7: Polish")),
    ("tasks-line-format", "tasks.line-format",
     m_edit("tasks.md", "- [x] T006 [P] [US2]", "- [x] T006 [US2] [P]")),
    ("tasks-ids", "tasks.ids", m_edit("tasks.md", "- [x] T006 [P]", "- [x] T005 [P]")),
    ("tasks-phase-order", "tasks.phase-order",
     m_edit("tasks.md", "## Phase 1: Setup (Shared Infrastructure)", "## Phase 1: Bootstrap")),
    ("tasks-story-labels", "tasks.story-labels",
     m_edit("tasks.md", "- [x] T002 [P] Record", "- [x] T002 [P] [US1] Record")),
    ("tasks-story-exists", "tasks.story-exists",
     lambda feature, _src: edit(
         feature / "tasks.md",
         "## Phase 5: User Story 3 - Remove a counter (Priority: P3)",
         "## Phase 5: User Story 4 - Remove a counter (Priority: P3)") or edit(
         feature / "tasks.md", "- [x] T008 [US3]", "- [x] T008 [US4]")),
    ("tasks-parallel-files", "tasks.parallel-files",
     m_edit("tasks.md", "the empty total in `tests/test_report.py`",
            "the empty total in `tally/report.py`")),
    ("tasks-mode", "tasks.mode", m_edit("tasks.md", "- [x] T009", "- [ ] T009")),
    ("coverage", "coverage.fr",
     m_edit("tasks.md", "unknown-name error in `tally/cli.py` (FR-004)",
            "unknown-name error in `tally/cli.py`")),
    ("research-decisions", "research.decisions",
     m_edit("research.md", "**Decision**", "**Choice**", 3)),
    ("evidence-missing", "evidence.missing", m_edit("spec.md", FR2_EV + "\n", "")),
    ("evidence-format", "evidence.format",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("| contradicts=none ", ""))),
    ("evidence-kind-desired", "evidence.kind",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("kind=observed", "kind=desired"))),
    ("evidence-confidence", "evidence.confidence",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("confidence=0.95", "confidence=0.9"))),
    ("evidence-validation", "evidence.validation",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("validation=verified", "validation=maybe"))),
    ("evidence-disproved-fr", "evidence.disproved",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("validation=verified", "validation=disproved"))),
    ("citation-escape", "evidence.citation",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("tally/cli.py:24-26", "../outside.py:1"))),
    ("citation-absolute", "evidence.citation",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("tally/cli.py:24-26", "/etc/hostname:1"))),
    ("citation-range", "evidence.citation",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("tally/cli.py:24-26", "tally/cli.py:40-43"))),
    ("citation-missing-file", "evidence.citation",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("tally/cli.py:24-26", "tally/nope.py:1"))),
    ("citation-binary", "evidence.citation",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("tally/cli.py:24-26", "assets/icon.bin:1"))),
    ("citation-shape", "evidence.citation",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("tally/cli.py:24-26", "tally/cli.py"))),
    ("contradicts", "evidence.contradicts",
     m_edit("spec.md", FR2_EV, FR2_EV.replace("contradicts=none", "contradicts=gone.py:1"))),
    ("checklist-fails-note", "checklist.items",
     m_edit("checklists/requirements.md",
            "  Fails: an as-is spec names", "  Because: an as-is spec names")),
]


@pytest.mark.parametrize("case,rule,mutate", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_each_rule_fails_on_its_one_mutation(tmp_path, case, rule, mutate):
    src, feature = src_mini(tmp_path), good(tmp_path)
    mutate(feature, src)
    findings = lint(feature, src)
    assert rule in rules(findings), findings
    # One violation, one rule: the mutation trips nothing else. (A story renumbered
    # or unparseable in spec.md also leaves tasks.md naming a story spec.md no longer
    # has, which is the point of the cross rule.)
    extra = {"story-numbers": {"tasks.story-exists"},
             "story-format": {"tasks.story-exists"}}.get(case, set())
    assert rules(findings) - {rule} <= extra, findings


def test_the_unknowns_rule_warns_on_a_weak_requirement(tmp_path):
    src, feature = src_mini(tmp_path), good(tmp_path)
    edit(feature / "spec.md", FR2_EV,
         FR2_EV.replace("confidence=0.95", "confidence=0.40")
         .replace("validation=verified", "validation=unverified"))
    findings = lint(feature, src)
    assert findings["errors"] == []
    assert rules(findings, "warnings") == {"unknowns.placement"}


def test_the_same_weak_claim_under_assumptions_is_fine():
    # good/spec.md already carries one (confidence=0.40, unverified) under Assumptions
    with open(os.path.join(GOOD, "spec.md")) as handle:
        assert "confidence=0.40 | contradicts=none | validation=unverified" in handle.read()


def test_an_expected_heading_only_warns(tmp_path):
    src, feature = src_mini(tmp_path), good(tmp_path)
    edit(feature / "plan.md", "## Summary\n", "## Overview\n")
    findings = lint(feature, src)
    assert findings["errors"] == []
    assert rules(findings, "warnings") == {"template.expected"}


def test_a_scenario_wrapped_over_lines_still_counts(tmp_path):
    src, feature = src_mini(tmp_path), good(tmp_path)
    edit(feature / "spec.md",
         "1. **Given** `coffee` exists, **When** the user runs `tally reset coffee`, **Then**",
         "1. **Given** `coffee` exists,\n   **When** the user runs `tally reset coffee`,\n"
         "   **Then**")
    assert lint(feature, src) == {"errors": [], "warnings": []}


def test_a_sample_numbered_template_heading_matches_any_story_and_only_warns(tmp_path):
    src, feature = src_mini(tmp_path), good(tmp_path)
    # the template says "Parallel Example: User Story 1"; good/ has User Story 2
    assert "## Parallel Example: User Story 2" in (feature / "tasks.md").read_text()
    edit(feature / "tasks.md", "## Parallel Example: User Story 2", "## Parallelism")
    findings = lint(feature, src)
    assert findings["errors"] == []
    assert [f["message"] for f in findings["warnings"]] == [
        "expected section '## Parallel Example: User Story 1' is missing "
        "(from lib/speckit_templates/tasks-template.md)"]


def test_a_constitution_is_extra_when_the_source_has_one(tmp_path):
    src, feature = src_mini(tmp_path), good(tmp_path)
    (src / ".specify" / "memory").mkdir(parents=True)
    (src / ".specify" / "memory" / "constitution.md").write_text("# c\n")
    assert rules(lint(feature, src)) == {"structure.extra"}
    os.remove(feature / "memory" / "constitution.md")
    assert lint(feature, src)["errors"] == []


def test_open_mode_requires_every_box_unticked(tmp_path):
    src, feature = src_mini(tmp_path), good(tmp_path)
    text = (feature / "tasks.md").read_text().replace("tasks=done", "tasks=open")
    (feature / "tasks.md").write_text(text)
    assert rules(lint(feature, src)) == {"tasks.mode"}
    (feature / "tasks.md").write_text(text.replace("- [x] T", "- [ ] T"))
    assert lint(feature, src)["errors"] == []


# ── --upto scoping and the no --src degradation ─────────────────────────────

def test_upto_checks_only_the_artifacts_earlier_phases_own(tmp_path):
    src, feature = src_mini(tmp_path), good(tmp_path)
    for rel in ("tasks.md", "data-model.md", "quickstart.md", "plan.md", "research.md"):
        os.remove(feature / rel)
    shutil.rmtree(feature / "contracts")
    assert lint(feature, src, upto=1)["errors"] == []
    assert rules(lint(feature, src, upto=2)) == {"structure.required"}
    # a defect in a later phase's artifact is not this gate's business
    (feature / "research.md").write_text("# no decisions\n")
    assert lint(feature, src, upto=1)["errors"] == []
    assert "research.decisions" in rules(lint(feature, src, upto=2))


def test_level_zero_checks_a_claims_file(tmp_path):
    src = src_mini(tmp_path)
    feature = tmp_path / "out"
    feature.mkdir()
    claims = tmp_path / "claims-tally.md"
    assert rules(lint(feature, src, upto=0, claims=str(claims))) == {"claims.missing"}
    claims.write_text("- `tally` reads TALLY_FILE\n")
    assert rules(lint(feature, src, upto=0, claims=str(claims))) == {"evidence.missing"}
    claims.write_text("- `tally` reads TALLY_FILE\n<!-- evidence: tally/store.py:8-10 "
                      + EV + "\n")
    assert lint(feature, src, upto=0, claims=str(claims))["errors"] == []


def test_without_src_the_evidence_rules_are_skipped_with_a_warning(tmp_path):
    feature = good(tmp_path)
    edit(feature / "spec.md", FR2_EV, FR2_EV.replace("tally/cli.py:24-26", "nope.py:1"))
    findings = lint(feature)
    assert findings["errors"] == []
    assert rules(findings, "warnings") == {"evidence.skipped"}
    # presence is still checked: it needs no source
    edit(feature / "spec.md", FR2_EV.replace("tally/cli.py:24-26", "nope.py:1") + "\n", "")
    assert rules(lint(feature)) == {"evidence.missing"}


def test_lint_runs_on_a_handwritten_directory_with_its_own_templates(tmp_path):
    """The template rule is template-DRIVEN: a synthetic template with invented
    headings changes what is required, with no code change."""
    src = src_mini(tmp_path)
    templates = src / ".specify" / "templates"
    templates.mkdir(parents=True)
    shutil.copy(os.path.join(FIXTURES, "templates-synthetic", "spec-template.md"), templates)
    feature = good(tmp_path)
    findings = lint(feature, src, upto=1)
    errors = [f for f in findings["errors"] if f["rule"].startswith("template.")]
    assert {f["rule"] for f in errors} == {"template.mandatory", "template.family"}
    assert any("Widgets" in f["message"] for f in errors)
    assert any("Bin [Name]" in f["message"] for f in errors)
    assert [f["message"] for f in findings["warnings"]
            if f["rule"] == "template.expected"] == [
        "expected section '## Gadgets' is missing (from %s)"
        % (templates / "spec-template.md")]
    # Spec Kit's own mandatory headings are NOT required by the synthetic template
    assert not any("User Scenarios" in f["message"] for f in findings["errors"])
    edit(feature / "spec.md", "## Assumptions\n",
         "## Widgets\n\nwidget text\n\n## Gadgets\n\ngadget text\n\n## Bin Alpha\n\n"
         "bin text\n\n## Assumptions\n")
    findings = lint(feature, src, upto=1)
    assert not [f for f in findings["errors"] + findings["warnings"]
                if f["rule"].startswith("template.")]


def test_workspace_relative_citations_resolve_in_a_monorepo(tmp_path):
    src = copy_fixture(tmp_path, "src-pnpm")
    ctx = reverse.EvidenceContext(str(src), None, [])
    assert ctx.resolve("./src/cli.ts") == ("packages/cli/src/cli.ts", None)
    assert ctx.resolve("src/index.ts") == ("packages/core/src/index.ts", None)
    assert ctx.resolve("package.json")[0] == "package.json"        # the root wins
    rel, error = ctx.resolve("src/missing.ts")
    assert rel is None and "no such file" in error
    (src / "packages" / "core" / "src" / "cli.ts").write_text("x\n")
    rel, error = reverse.EvidenceContext(str(src), None, []).resolve("src/cli.ts")
    assert rel is None and "ambiguous" in error


# ── guard ───────────────────────────────────────────────────────────────────

def baselined(tmp_path, with_git=True):
    """src-mini with an inventory baseline where `reverse.py plan` would put it."""
    src = src_mini(tmp_path)
    if with_git:
        git(src, "init", "-q")
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "init")
    out_rel = "specs/001-as-is-src-mini"
    run_state = src / ".specstride" / "reverse" / "src-mini"
    run_state.mkdir(parents=True)
    inventory = run_state / "inventory.json"
    inventory.write_text(reverse_inventory.dumps(
        reverse_inventory.build(str(src), excludes=[out_rel])))
    return src, {"src": str(src), "inventory": str(inventory), "out": str(src / out_rel)}


def guard(result):
    return reverse.guard(result["src"], result["inventory"], result["out"])


@pytest.mark.parametrize("with_git", [True, False], ids=["git", "walk"])
def test_guard_passes_on_writes_to_the_output_and_state_dirs(tmp_path, with_git):
    src, result = baselined(tmp_path, with_git)
    shutil.copytree(GOOD, result["out"], dirs_exist_ok=True)
    (src / ".specstride" / "features").mkdir(parents=True, exist_ok=True)
    (src / ".specstride" / "features" / "x.log").write_text("state\n")
    assert guard(result) == []


@pytest.mark.parametrize("with_git", [True, False], ids=["git", "walk"])
@pytest.mark.parametrize("change,expect", [
    (lambda s: (s / "tally" / "report.py").write_text("changed\n"), "changed: tally/report.py"),
    (lambda s: (s / "new.py").write_text("x\n"), "added: new.py"),
    (lambda s: os.remove(s / "tests" / "test_report.py"), "deleted: tests/test_report.py"),
    (lambda s: (s / "testautomation" / "f").mkdir(parents=True), "testautomation/"),
], ids=["modified", "added", "deleted", "testautomation"])
def test_guard_catches_changes_to_the_source(tmp_path, with_git, change, expect):
    src, result = baselined(tmp_path, with_git)
    change(src)
    problems = guard(result)
    assert any(expect in p for p in problems), problems


def test_guard_catches_a_moved_head(tmp_path):
    src, result = baselined(tmp_path)
    git(src, "commit", "-q", "--allow-empty", "-m", "a checkpoint the run must not make")
    assert any(p.startswith("git HEAD moved") for p in guard(result))


def test_guard_cli_exit_codes(tmp_path):
    src, result = baselined(tmp_path)
    argv = ["guard", result["src"], "--baseline", result["inventory"], "--out", result["out"]]
    assert reverse.main(argv) == 0
    (src / "README.md").write_text("edited\n")
    assert reverse.main(argv) == 3


# ── plan: output dir numbering and refusals ──────────────────────────────────

def planned(tmp_path, with_git=True, **kw):
    src = src_mini(tmp_path)
    if with_git:
        git(src, "init", "-q")
        git(src, "add", "-A")
        git(src, "commit", "-q", "-m", "init")
    result = reverse.plan(str(src), **kw)
    return src, result



def test_default_output_numbering(tmp_path):
    src = src_mini(tmp_path)
    assert reverse.default_out(str(src), "x") == str(src / "specs" / "001-as-is-x")
    for name in ("001-a", "004-b"):
        (src / "specs" / name).mkdir(parents=True)
    result = reverse.plan(str(src))
    assert result["out"] == str(src / "specs" / "005-as-is-src-mini")
    assert result["slug"] == "src-mini" and result["feature"] == "reverse-src-mini"


def test_name_is_kebab_cased_and_truncated_to_forty():
    assert reverse.slugify("My Great__Project!") == "my-great-project"
    assert len(reverse.slugify("a" * 60)) == 40
    assert reverse.slugify("***") == "project"


@pytest.mark.parametrize("out,message", [
    (".", "source root"),
    ("../elsewhere", "inside the source tree"),
    (".git/specs", ".git"),
    (".specstride/out", "state directory"),
    (".specify/out", ".specify"),
])
def test_refused_output_dirs(tmp_path, out, message):
    src = src_mini(tmp_path)
    (src / ".git").mkdir()
    with pytest.raises(reverse.ReverseError, match=re.escape(message)):
        reverse.plan(str(src), out=str(src / out))


def test_a_non_empty_output_dir_is_refused(tmp_path):
    src = src_mini(tmp_path)
    (src / "specs" / "001-x").mkdir(parents=True)
    (src / "specs" / "001-x" / "spec.md").write_text("x\n")
    with pytest.raises(reverse.ReverseError, match="not empty"):
        reverse.plan(str(src), out=str(src / "specs" / "001-x"))
    assert reverse.main(["plan", str(src), "--out", str(src / "specs" / "001-x")]) == 3


def test_a_feature_with_gate_state_is_refused_toward_resume(tmp_path):
    src = src_mini(tmp_path)
    gates = src / ".specstride" / "features" / "reverse-src-mini" / "gates"
    gates.mkdir(parents=True)
    (gates / "GATE1-APPROVED").write_text("")
    with pytest.raises(reverse.ReverseError, match="specstride resume"):
        reverse.plan(str(src))


def test_plan_never_touches_specify_or_writes_outside_state(tmp_path):
    src, result = planned(tmp_path)
    assert not (src / ".specify").exists()
    assert not os.path.exists(result["out"])
    status = git(src, "status", "--porcelain", "--untracked-files=all").stdout.split("\n")
    assert all(line[3:].startswith(".specstride/") for line in status if line)


# ── plan: the driver and its verification commands ─────────────────────────

def test_driver_is_a_valid_native_spec_with_contiguous_phases(tmp_path):
    _src, result = planned(tmp_path)
    with open(result["driver"]) as handle:
        text = handle.read()
    ok, count, errors = specstride_spec.validate(text, "native")
    assert ok and count == 5, errors
    assert [p["lint_level"] for p in result["phases"]] == [1, 2, 3, 4, 5]
    titles = [p.title for p in specstride_spec.get_phases(text, "native")]
    assert titles[0] == "Constitution and specification"
    for phase in specstride_spec.get_phases(text, "native"):
        assert result["inventory_md"].split(result["src"] + "/")[1] in phase.section
        assert "--upto" in phase.section and "reverse.py guard" in phase.section
        assert "desired" in phase.section                         # the policy travels


def test_oversize_splits_phase_one_by_unit_with_contiguous_numbers(tmp_path):
    src = src_mini(tmp_path)
    result = reverse.plan(str(src), environ={"SPECSTRIDE_REVERSE_MAX_TOTAL_BYTES": "100"})
    assert result["oversize"] is True
    with open(result["driver"]) as handle:
        text = handle.read()
    ok, count, errors = specstride_spec.validate(text, "native")
    assert ok, errors
    phases = specstride_spec.get_phases(text, "native")
    assert [p.n for p in phases] == list(range(1, count + 1))
    assert [p.title for p in phases[:3]] == [
        "Investigate unit . (1a)", "Investigate unit tally (1b)", "Investigate unit tests (1c)"]
    assert phases[3].title.startswith("Synthesis")
    assert [p["lint_level"] for p in result["phases"]] == [0, 0, 0, 1, 2, 3, 4, 5]
    with open(result["verification_commands"]) as handle:
        document = json.load(handle)
    assert document["phaseTimeouts"] == {"1": 3600, "2": 3600, "3": 3600, "4": 3600}
    lint1 = next(c for c in document["commands"] if c["id"] == "lint-1")
    assert lint1["args"][-2:] == ["--claims", os.path.join(result["run_state"],
                                                           "claims-root.md")]


def test_verification_commands_load_and_gate_only_declared_commands(tmp_path):
    src, result = planned(tmp_path)
    loaded = verification_plan.load_declared_commands(result["verification_commands"])
    assert loaded["discovery"] == "none"
    assert sorted(c["declaredId"] for c in loaded["commands"]) == sorted(
        "%s-%d" % (kind, n) for kind in ("lint", "guard") for n in range(1, 6))
    for command in loaded["commands"]:
        assert os.path.isabs(command["executable"]) and command["cwd"] == result["src"]
        assert all(os.path.isabs(a) or not a.startswith((".", "lib")) for a in command["args"])
    plan = verification_plan.create_plan(result["src"], result["driver"], fmt="native",
                                         required=True,
                                         commands_path=result["verification_commands"])
    # src-mini's pyproject mentions pytest, so discovery WOULD have added it
    assert "pytest" in plan["project"]["frameworks"]
    assert all(c.get("source") == "declared" for c in plan["commands"])
    assert plan["ambiguities"] == []


def test_the_gates_pass_once_the_artifacts_are_written(tmp_path):
    """End to end without an LLM: plan, drop good/ into the output dir, and every
    phase gate's lint + guard pass exactly as the orchestrator would run them."""
    src, result = planned(tmp_path)
    plan = verification_plan.create_plan(result["src"], result["driver"], fmt="native",
                                         required=True,
                                         commands_path=result["verification_commands"])
    first = verification_plan.run_gate(plan, 1)
    assert first["passed"] is False                         # nothing written yet
    shutil.copytree(GOOD, result["out"])
    evidence = verification_plan.run_gate(plan, 5)
    assert evidence["passed"] is True, json.dumps(evidence["commands"], indent=1)[:4000]
    assert len(evidence["commands"]) == 10                  # cumulative: 5 phases x 2
    assert not (src / "testautomation").exists()


def test_plan_cli_prints_the_paths_as_json(tmp_path, capsys):
    src = src_mini(tmp_path)
    assert reverse.main(["plan", str(src), "--name", "Mini Tally", "--tasks", "open"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["feature"] == "reverse-mini-tally"
    assert result["tasks_mode"] == "open"
    for key in ("inventory", "inventory_md", "driver", "verification_commands"):
        assert os.path.isfile(result[key])
    assert result["test_plan"].startswith(os.path.join(str(src), ".specstride", "reverse"))


def test_tasks_mode_default_comes_from_the_environment(tmp_path, monkeypatch, capsys):
    src = src_mini(tmp_path)
    monkeypatch.setenv("SPECSTRIDE_REVERSE_TASKS", "open")
    assert reverse.main(["plan", str(src)]) == 0
    assert json.loads(capsys.readouterr().out)["tasks_mode"] == "open"
    monkeypatch.setenv("SPECSTRIDE_REVERSE_TASKS", "sometimes")
    assert reverse.main(["plan", str(src), "--name", "other"]) == 3


def test_lint_cli_json_and_exit_codes(tmp_path, capsys):
    src, feature = src_mini(tmp_path), good(tmp_path)
    assert reverse.main(["lint", str(feature), "--src", str(src), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"errors": [], "warnings": []}
    edit(feature / "spec.md", FR2_EV, FR2_EV.replace("kind=observed", "kind=desired"))
    assert reverse.main(["lint", str(feature), "--src", str(src), "--json"]) == 3
    finding = json.loads(capsys.readouterr().out)["errors"][0]
    assert set(finding) == {"rule", "file", "line", "message"}
    assert finding["rule"] == "evidence.kind" and finding["file"] == "spec.md"
    assert reverse.main(["lint", str(tmp_path / "nowhere")]) == 3
