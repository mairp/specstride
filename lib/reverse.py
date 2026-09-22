#!/usr/bin/env python3
"""reverse.py — `specstride --reverse`: a codebase reverse-engineered into Spec Kit.

Three subcommands, all stdlib, none of which calls an LLM:

    python3 lib/reverse.py plan  <SRC> [--out DIR] [--name SLUG] [--tasks done|open]
    python3 lib/reverse.py lint  <FEATURE_DIR> [--src SRC] [--baseline INV] [--upto N]
                                 [--claims FILE] [--json]
    python3 lib/reverse.py guard <SRC> --baseline <inventory.json> --out <DIR>

``plan`` writes the deterministic inventory (lib/reverse_inventory.py), a native
driver spec whose phases write the artifacts one gate at a time, and the
``--verification-commands`` document whose per-phase commands are this file's
``lint`` and ``guard``. It prints every path as JSON; the `specstride reverse`
verb launches orchestrator.sh with them.

``lint`` is the deterministic Spec Kit linter each gate runs. Required headings
come from the Spec Kit templates at runtime (``<SRC>/.specify/templates/`` first,
else the copy vendored in lib/speckit_templates/), never from a list in this file. ``guard`` re-walks the
source and fails when anything outside the output dir and the state dir changed,
git history included.

Exit codes: 0 ok, 3 findings or invalid input, 2 usage (argparse).
"""
import argparse
import io
import json
import os
import re
import shlex
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import reverse_inventory  # noqa: E402
import specstride_env  # noqa: E402
import specstride_spec  # noqa: E402

E_FINDINGS = 3
TASK_MODES = ("done", "open")
SPECSTRIDE_TEMPLATES = os.path.join(HERE, "speckit_templates")   # vendored, see its README

# ── output layout and artifact ownership ─────────────────────────────────────
# The nine paths of the layout; `contracts/*.md` is the only pattern.
LAYOUT = ("spec.md", "plan.md", "research.md", "data-model.md", "quickstart.md",
          "tasks.md", "checklists/requirements.md", "memory/constitution.md")
CONTRACTS_DIR = "contracts"
# `--upto N` owns the artifacts phases <= N write. Level 0 owns nothing: it is the
# level of an oversize run's per-unit investigation phases, which write only their
# scratch claims file (checked with --claims).
OWNERSHIP = {
    1: ("spec.md", "checklists/requirements.md", "memory/constitution.md"),
    2: ("plan.md", "research.md"),
    3: ("data-model.md", CONTRACTS_DIR, "quickstart.md"),
    4: ("tasks.md",),
    5: (),
}
LEVELS = (0, 1, 2, 3, 4, 5)
TEMPLATE_FOR = {
    "spec.md": "spec-template.md",
    "plan.md": "plan-template.md",
    "tasks.md": "tasks-template.md",
    "checklists/requirements.md": "checklist-template.md",
    "memory/constitution.md": "constitution-template.md",
}

# ── the evidence comment ─────────────────────────────────────────────────────
# Lisa's five-line per-claim metadata block, on one line, invisible when rendered:
#   <!-- evidence: src/app.py:40-88 | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->
EVIDENCE_RE = re.compile(r'<!--\s*evidence:\s*(.*?)\s*-->')
CITATION_RE = re.compile(r'^(?P<path>[^\s,|]+?):(?P<start>\d+)(?:-(?P<end>\d+))?$')
KINDS = ("observed", "inferred", "declared")
VALIDATIONS = ("verified", "unverified", "disproved")
CONFIDENCE_RE = re.compile(r'^(0\.\d\d|1\.00)$')
TASKS_MODE_RE = re.compile(r'<!--\s*specstride-reverse:\s*tasks=(\w+)\s*-->')

# ── spec.md / tasks.md grammar ───────────────────────────────────────────────
FR_ANY = re.compile(r'\bFR-\d+\b')
FR_LINE = re.compile(r'^- \*\*FR-(\d{3})\*\*: \S')
SC_LINE = re.compile(r'^- \*\*SC-(\d{3})\*\*: \S')
FR_MENTION = re.compile(r'\*\*FR-(\d+)\*\*')
SC_MENTION = re.compile(r'\*\*SC-(\d+)\*\*')
STORY_ANY = re.compile(r'^###\s+User Story\b')
STORY_HEAD = re.compile(r'^### User Story (\d+) - (.+?) \(Priority: P(\d+)\)\s*(?:🎯.*)?$')
GIVEN_WHEN_THEN = re.compile(r'\*\*Given\*\*.*?\*\*When\*\*.*?\*\*Then\*\*', re.S)
TASK_LINE = re.compile(r'^- \[[ xX]\] T\d{3}( \[P\])?( \[US\d+\])? (?!\[(?:P|US\d+)\])\S')
TASK_PARTS = re.compile(r'^- \[(?P<box>[ xX])\] T(?P<id>\d{3})(?P<p> \[P\])?'
                        r'(?: \[US(?P<us>\d+)\])? (?P<desc>.*)$')
CHECKBOX_LINE = re.compile(r'^\s*-\s*\[[ xX]?\]')
PHASE_HEAD = re.compile(r'^## Phase (\d+):?\s*(.*)$')
STORY_IN_TITLE = re.compile(r'\bUser Story (\d+)\b')
DECISION_LINE = re.compile(r'^\s*(?:[-*]\s+)?\*\*Decision\*\*')
ENTITY_HEAD = re.compile(r'^### (?!#)\s*(.+?)\s*$')
PATH_TOKEN = re.compile(r'`([^`\s]+)`|(?<![\w/`])((?:[\w.-]+/)*[\w-]+\.[A-Za-z][\w]{0,7})(?![\w/`])'
                        r'|(?<![\w`])((?:[\w.-]+/)+[\w.-]*)(?![\w`])')

# ── placeholders the templates carry ─────────────────────────────────────────
PLACEHOLDERS = (
    (re.compile(r'\[FEATURE NAME\]'), "[FEATURE NAME]"),
    (re.compile(r'\[###-feature-name\]'), "[###-feature-name]"),
    (re.compile(r'\$ARGUMENTS'), "$ARGUMENTS"),
    (re.compile(r'ACTION REQUIRED'), "ACTION REQUIRED"),
    (re.compile(r'\[DATE\]'), "[DATE]"),
    (re.compile(r'__SPECKIT_COMMAND_[A-Z_]*__'), "__SPECKIT_COMMAND_*__"),
    (re.compile(r'\[e\.g\.,[^\]]*\]'), "[e.g., …]"),
)
UPPER_PLACEHOLDER = re.compile(r'\[[A-Z][A-Z0-9_]{2,}\]')
NEEDS_CLARIFICATION = "[NEEDS CLARIFICATION"
MAX_CLARIFICATIONS = 3

class ReverseError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
#  shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _real(path):
    return os.path.realpath(os.path.abspath(path))


def _is_within(path, root):
    path, root = _real(path), _real(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def state_basename(src):
    return specstride_env.state_dirname(src, stream=io.StringIO())


def strip_front_matter(text):
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            nl = text.find("\n", end + 4)
            return text[nl + 1:] if nl != -1 else ""
    return text


def read(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def load_inventory(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ReverseError("cannot read inventory %s: %s" % (path, exc))
    if not isinstance(data, dict) or "fingerprint" not in data or "files" not in data:
        raise ReverseError("not an inventory: %s" % path)
    return data


# ─────────────────────────────────────────────────────────────────────────────
#  plan: output dir, inventory, driver spec, verification commands
# ─────────────────────────────────────────────────────────────────────────────

SLUG_MAX = 40

# ── the investigation policy (Lisa's, ported) ───────────────────────────────
POLICY = """\
- Mode: source-aware. Treat source, configuration, tests, schemas, documentation,
  dependency manifests and Git history as read-only evidence.
- No network access, no dependency install, no services, containers or daemons.
- Do not execute the product, its tests, builds, generators, migrations, linters or
  formatters. Read-only discovery (`ls`, `cat`, `grep`, `find`, `wc`) and
  `git log`/`git show`/`git blame` are allowed.
- The only permitted writes are the output directory `{out_rel}/` and the state
  directory `{state_rel}/`. Never touch `.specify/`, never write
  `.specify/feature.json`, never create a git branch or commit.
- Describe current behavior only. Never present desired or recommended behavior as
  current behavior: no To-Be design, no remediation or enhancement backlog.
- Evidence precedence when sources disagree: executable source, schemas and
  configuration > tests > templates and command definitions > user docs > comments
  and git history. A disagreement lowers confidence and is recorded in
  `contradicts=`; it is never silently resolved.
- Every claim that the linter checks (each `FR-###`, each `SC-###`, each research
  **Decision**, each data-model entity `###` heading) carries, on the line directly
  after it, one evidence comment:
  `<!-- evidence: path:START-END | kind=observed | confidence=0.90 | contradicts=none | validation=verified -->`
  `kind` is observed, inferred or declared (never `desired`); paths are
  `{src_name}`-relative; ranges must exist in the file. At most 3
  `[NEEDS CLARIFICATION` markers in spec.md; every other unknown is written as an
  explicit unknown under *Assumptions* with `validation=unverified`."""


def slugify(value):
    """Kebab-case in the feature-slug alphabet, at most SLUG_MAX characters."""
    slug = re.sub(r'[^a-z0-9]+', '-', (value or "").lower()).strip("-")[:SLUG_MAX].strip("-")
    return slug or "project"


def default_out(src, slug):
    numbers = reverse_inventory.existing_spec_numbers(src)
    return os.path.join(src, "specs", "%03d-as-is-%s" % ((max(numbers) + 1) if numbers else 1,
                                                          slug))


def validate_out(src, out, state_base):
    """Refuse an output dir the run must not write (Lisa's validation, extended)."""
    src_r, out_r = _real(src), _real(out)
    if out_r == src_r:
        raise ReverseError("--out cannot be the source root itself; choose a dedicated "
                           "output folder: %s" % out)
    if not _is_within(out_r, src_r):
        raise ReverseError("--out must be inside the source tree %s (got %s)" % (src, out))
    rel = os.path.relpath(out_r, src_r).replace(os.sep, "/")
    first = rel.split("/", 1)[0]
    if first == ".git":
        raise ReverseError("--out cannot be inside the project's .git directory: %s" % out)
    if first in (state_base,) + reverse_inventory.STATE_DIRS:
        raise ReverseError("--out cannot be inside the state directory %s/: %s"
                           % (first, out))
    if first == ".specify":
        raise ReverseError("--out cannot be inside .specify/ (the run never touches it): %s"
                           % out)
    if os.path.exists(out_r) and not os.path.isdir(out_r):
        raise ReverseError("--out exists and is not a directory: %s" % out)
    if os.path.isdir(out_r) and os.listdir(out_r):
        raise ReverseError("--out already exists and is not empty: %s (resume an existing "
                           "reverse run with `specstride resume --feature reverse-<slug>`)"
                           % out)
    return out_r, rel


def driver_phases(inventory, tasks_mode, constitution_needed):
    """The driver's phases as dicts: n, title, level, kind, unit (oversize only)."""
    phases = []
    if inventory["oversize"]:
        for unit in inventory["units"]:
            phases.append({"kind": "unit", "unit": unit["path"], "level": 0,
                           "title": "Investigate unit %s (1%s)"
                           % (unit["path"], _letter(len(phases)))})
        phases.append({"kind": "spec", "level": 1,
                       "title": "Synthesis — constitution and specification"
                       if constitution_needed else "Synthesis — specification"})
    else:
        phases.append({"kind": "spec", "level": 1,
                       "title": "Constitution and specification"
                       if constitution_needed else "Specification"})
    phases += [
        {"kind": "plan", "level": 2, "title": "Plan and research"},
        {"kind": "design", "level": 3, "title": "Design artifacts"},
        {"kind": "tasks", "level": 4, "title": "Tasks (%s)" % tasks_mode},
        {"kind": "analysis", "level": 5, "title": "Cross-artifact analysis and sign-off"},
    ]
    for n, phase in enumerate(phases, 1):
        phase["n"] = n
    return phases


def _letter(index):
    letters = "abcdefghijklmnopqrstuvwxyz"
    return letters[index] if index < len(letters) else "-%d" % (index + 1)


def unit_key(unit_path):
    return "root" if unit_path == "." else re.sub(r'[^A-Za-z0-9._-]+', '-', unit_path)


def render_driver(ctx, phases):
    src = ctx["src"]
    out_rel = ctx["out_rel"]
    state_rel = ctx["state_rel"]
    rs = ctx["run_state_rel"]
    policy = POLICY.format(out_rel=out_rel, state_rel=state_rel,
                           src_name=os.path.basename(src))
    templates = ctx["templates_note"]
    lines = [
        "# Reverse-engineer `%s` into Spec Kit (as-is)" % os.path.basename(src),
        "",
        "Generated by `specstride reverse`. A native Specstride driver: each phase below",
        "writes part of the Spec Kit feature directory `%s/` and is one gate." % out_rel,
        "The gate runs the deterministic linter and the source-untouched guard; the",
        "critic judges the evidence file.",
        "",
        "## Run boundaries",
        "",
        "- Target repository: `%s`" % src,
        "- Output (Spec Kit feature directory): `%s/`" % out_rel,
        "- Run state: `%s/`" % rs,
        "- Tasks mode: `%s`" % ctx["tasks_mode"],
        policy,
        "",
    ]
    for phase in phases:
        n = phase["n"]
        level = phase["level"]
        lint = ctx["lint_cmd"](phase)
        guard = ctx["guard_cmd"]
        lines += [
            "## Phase %d: %s" % (n, phase["title"]),
            "",
            "Ground truth for **what exists**: `%s/INVENTORY.md` (full record: "
            "`%s/inventory.json`). Treat it as the file list, languages, manifests,"
            " entry points and workspaces of the target; do not re-derive them."
            % (rs, rs),
            "",
            "### Policy",
            "",
            policy,
            "",
            "### Work",
            "",
        ]
        lines += _phase_work(phase, ctx, templates)
        lines += [
            "",
            "### Gate",
            "",
            "The gate runs exactly these (run them yourself first, and fix every error"
            " before writing evidence):",
            "",
            "```bash",
            lint,
            guard,
            "```",
            "",
            "Write `GATE%d-EVIDENCE.md` short (the critic's grounding budget is 32 KB):"
            " paste the linter's `--json` output, and cite each artifact you wrote by its"
            " workdir-relative path (e.g. `%s/spec.md`)." % (n, out_rel),
            "",
            "### Acceptance criteria",
            "",
        ]
        lines += ["- [ ] %s" % c for c in _phase_criteria(phase, ctx, lint, guard)]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _phase_work(phase, ctx, templates):
    out = ctx["out_rel"]
    rs = ctx["run_state_rel"]
    kind = phase["kind"]
    if kind == "unit":
        return [
            "Investigate only unit `%s` of the inventory (an oversize repository is split"
            " by top-level unit so each phase fits one context)." % phase["unit"],
            "Append every material claim about it, each followed by its evidence comment,"
            " to the scratch file `%s/claims-%s.md`. Claims are one per line, as"
            " `- <claim>`. Do not write anything under `%s/` in this phase."
            % (rs, unit_key(phase["unit"]), out),
        ]
    if kind == "spec":
        work = []
        if ctx["oversize"]:
            work.append("Synthesize from the scratch claims files `%s/claims-*.md` (and "
                        "the source where a claim needs checking)." % rs)
        if ctx["constitution_needed"]:
            work.append("The target has no `.specify/memory/constitution.md`: write "
                        "`%s/memory/constitution.md` from `%s/constitution-template.md`,"
                        " with principles *recovered from evidence* (lint config, CI "
                        "gates, test conventions, AGENTS.md/CONTRIBUTING)."
                        % (out, templates))
        else:
            work.append("Read the existing `.specify/memory/constitution.md`; do not copy"
                        " it into the output.")
        work += [
            "Write `%s/spec.md` from `%s/spec-template.md`: prioritised user stories"
            " (`### User Story <n> - <title> (Priority: P<n>)`, numbered from 1), each"
            " with **Why this priority**, **Independent Test** and at least one"
            " `**Given** … **When** … **Then** …` scenario; edge cases; `- **FR-###**:`"
            " functional requirements and `- **SC-###**:` success criteria, each followed"
            " by its evidence comment; key entities; assumptions." % (out, templates),
            "Write `%s/checklists/requirements.md` with the specification quality"
            " checklist items of Spec Kit's `/speckit.specify` (Content Quality,"
            " Requirement Completeness, Feature Readiness, Notes), every item evaluated:"
            " `[x]`, or `[ ]` followed by an indented `Fails:` line carrying an evidence"
            " comment." % out,
            "Leave no template placeholder, sample text or template HTML comment behind.",
        ]
        return work
    if kind == "plan":
        return [
            "Write `%s/plan.md` from `%s/plan-template.md` with the *real* Technical"
            " Context, the Constitution Check against the constitution, and the actual"
            " project structure tree; delete the unused `[REMOVE IF UNUSED]` options and"
            " keep *Complexity Tracking* as a heading with \"None\" when nothing violates"
            " the constitution." % (out, ctx["templates_note"]),
            "Write `%s/research.md` as a list of decisions, each `- **Decision**: …`"
            " followed by its evidence comment, then `**Rationale**` and"
            " `**Alternatives**`. A rationale that cannot be recovered is written"
            " `unknown`, never invented." % out,
        ]
    if kind == "design":
        return [
            "Write `%s/data-model.md`: one `### <Entity>` heading per entity found in"
            " code, each followed by its evidence comment, then its fields,"
            " relationships and state transitions." % out,
            "Write `%s/contracts/*.md`, one file per external surface that exists (CLI,"
            " HTTP API, events, files on disk, config/env)." % out,
            "Write `%s/quickstart.md`. Commands come from manifests and docs, and every"
            " one is labelled *not executed by the reverse run*." % out,
        ]
    if kind == "tasks":
        box = "[x]" if ctx["tasks_mode"] == "done" else "[ ]"
        meaning = ("describes work the evidence shows already exists (a baseline that"
                   " `/speckit.converge` or a later run can extend)"
                   if ctx["tasks_mode"] == "done" else
                   "is a rebuild step for the same system")
        return [
            "Write `%s/tasks.md` from `%s/tasks-template.md` (keep its YAML front"
            " matter) and the rules of Spec Kit's `/speckit.tasks`:" % (out, ctx["templates_note"]),
            "- `## Phase 1: Setup …`, `## Phase 2: Foundational …`, one"
            " `## Phase <n>: User Story <k> - <title> (Priority: P<k>)` per user story in"
            " priority order, then a numbered final `## Phase <n>: Polish …`.",
            "- Every task is `- %s T### [P]? [USn]? description with exact file path`."
            " `[USn]` only in its own story's phase; `[P]` only on tasks touching"
            " different files with no unmet dependency." % box,
            "- Every task box is `%s`: every task %s." % (box, meaning),
            "- Mention each `FR-###` in at least one task description, e.g."
            " `(FR-003)`.",
            "- Fill in *Dependencies & Execution Order* and *Parallel Example*.",
            "- Keep the marker comment `<!-- specstride-reverse: tasks=%s -->` on its own"
            " line right after the front matter." % ctx["tasks_mode"],
        ]
    return [
        "Do a `/speckit.analyze`-style pass over every artifact: every FR is covered by"
        " at least one task, every `[USn]` exists in the spec, no terminology drift,"
        " duplicates or contradictions. Fix every finding in place.",
        "Write the analysis report to `%s/ANALYSIS.md` (not into the output dir), in"
        " the report shape of `/speckit.analyze` (findings table: ID, Category, Severity,"
        " Location(s), Summary, Recommendation; coverage summary; metrics)." % rs,
    ]


def _phase_criteria(phase, ctx, lint, guard):
    out = ctx["out_rel"]
    rs = ctx["run_state_rel"]
    common = ["`%s` exits 0." % lint, "`%s` exits 0." % guard]
    kind = phase["kind"]
    if kind == "unit":
        return ["`%s/claims-%s.md` exists and every claim in it carries an evidence"
                " comment that resolves into the source." % (rs, unit_key(phase["unit"]))] \
            + common
    if kind == "spec":
        crit = ["`%s/spec.md` and `%s/checklists/requirements.md` exist, and every"
                " requirement and success criterion is evidence-backed." % (out, out),
                "No desired behavior is represented as current behavior."]
        if ctx["constitution_needed"]:
            crit.append("`%s/memory/constitution.md` records principles recovered from"
                        " evidence only." % out)
        return crit + common
    if kind == "plan":
        return ["`%s/plan.md` holds the actual repository structure and no unused"
                " template options." % out,
                "`%s/research.md` separates evidenced decisions from unknown rationale."
                % out] + common
    if kind == "design":
        return ["`%s/data-model.md`, `%s/quickstart.md` and at least one"
                " `%s/contracts/*.md` exist, and cover only surfaces the evidence shows."
                % (out, out, out),
                "Every quickstart command is labelled not executed."] + common
    if kind == "tasks":
        return ["`%s/tasks.md` passes `python3 %s/lib/specstride_spec.py validate"
                " --specs %s/tasks.md --format speckit-tasks`."
                % (out, ROOT, ctx["out_abs"]),
                "Every task box is `%s`." % ("[x]" if ctx["tasks_mode"] == "done" else "[ ]")] \
            + common
    return ["`%s/ANALYSIS.md` exists and lists every finding with its resolution."
            % rs, "The linter reports zero errors at `--upto 5`."] + common


def plan(src, out=None, name=None, tasks_mode="done", environ=None):
    environ = dict(os.environ if environ is None else environ)
    if not os.path.isdir(src):
        raise ReverseError("source is not a directory: %s" % src)
    src = _real(src)
    if tasks_mode not in TASK_MODES:
        raise ReverseError("--tasks must be done or open (got %r)" % tasks_mode)
    slug = slugify(name if name else os.path.basename(src))
    state_base = state_basename(src)
    state_dir = os.path.join(src, state_base)
    feature = "reverse-%s" % slug
    feature_state = os.path.join(state_dir, "features", feature)
    if os.path.isdir(os.path.join(feature_state, "gates")) and \
            os.listdir(os.path.join(feature_state, "gates")):
        raise ReverseError("a reverse run for '%s' already has gate state in %s; resume it"
                           " with `specstride resume -w %s --feature %s`, or pass --name"
                           % (slug, feature_state, src, feature))
    out = os.path.abspath(out) if out else default_out(src, slug)
    out, out_rel = validate_out(src, out, state_base)
    run_state = os.path.join(state_dir, "reverse", slug)
    os.makedirs(run_state, exist_ok=True)

    inventory_path = os.path.join(run_state, "inventory.json")
    inventory_md = os.path.join(run_state, "INVENTORY.md")
    inventory = reverse_inventory.build(src, excludes=[out_rel], environ=environ)
    reverse_inventory.write_atomic(inventory_path, reverse_inventory.dumps(inventory))
    budget = reverse_inventory._env_int("SPECSTRIDE_CONTEXT_BUDGET",
                                        reverse_inventory.DEFAULT_MD_BUDGET, environ)
    reverse_inventory.write_atomic(inventory_md, reverse_inventory.render_md(
        inventory, inventory_path, budget))

    constitution_needed = not inventory["specify"]["constitution"]
    phases = driver_phases(inventory, tasks_mode, constitution_needed)
    reverse_py = os.path.join(HERE, "reverse.py")
    driver_path = os.path.join(run_state, "DRIVER.md")
    commands_path = os.path.join(run_state, "verification-commands.json")

    def lint_args(phase):
        args = [reverse_py, "lint", out, "--src", src, "--baseline", inventory_path,
                "--upto", str(phase["level"])]
        if phase["kind"] == "unit":
            args += ["--claims", os.path.join(run_state, "claims-%s.md"
                                              % unit_key(phase["unit"]))]
        return args

    guard_args = [reverse_py, "guard", src, "--baseline", inventory_path, "--out", out]
    ctx = {
        "src": src, "out_abs": out, "out_rel": out_rel, "state_rel": state_base,
        "run_state_rel": os.path.relpath(run_state, src).replace(os.sep, "/"),
        "tasks_mode": tasks_mode, "oversize": inventory["oversize"],
        "constitution_needed": constitution_needed,
        "templates_note": template_dir_note(src),
        "lint_cmd": lambda p: " ".join(shlex.quote(a) for a in
                                       ["python3"] + lint_args(p)),
        "guard_cmd": " ".join(shlex.quote(a) for a in ["python3"] + guard_args),
    }
    driver = render_driver(ctx, phases)
    ok, count, errors = specstride_spec.validate(driver, "native")
    if not ok:  # a generator bug, never an input problem
        raise ReverseError("generated driver is not a valid native spec: %s"
                           % "; ".join(errors))
    reverse_inventory.write_atomic(driver_path, driver)

    commands = []
    for phase in phases:
        commands.append({"id": "lint-%d" % phase["n"], "phase": phase["n"],
                         "executable": sys.executable, "cwd": src,
                         "args": lint_args(phase), "timeoutSec": 120})
        commands.append({"id": "guard-%d" % phase["n"], "phase": phase["n"],
                         "executable": sys.executable, "cwd": src,
                         "args": guard_args, "timeoutSec": 300})
    timeouts = {str(p["n"]): 3600 for p in phases if p["level"] <= 1}
    document = {"schema_version": "1.0.0", "discovery": "none", "commands": commands,
                "phaseTimeouts": timeouts}
    reverse_inventory.write_atomic(commands_path,
                                   json.dumps(document, indent=1, sort_keys=True) + "\n")
    return {
        "src": src,
        "slug": slug,
        "feature": feature,
        "out": out,
        "state_dir": state_dir,
        "run_state": run_state,
        "inventory": inventory_path,
        "inventory_md": inventory_md,
        "driver": driver_path,
        "verification_commands": commands_path,
        "test_plan": os.path.join(run_state, "TEST_PLAN.md"),
        "generate_tests": os.path.join(run_state, "generated"),
        "analysis": os.path.join(run_state, "ANALYSIS.md"),
        "tasks_mode": tasks_mode,
        "oversize": inventory["oversize"],
        "phases": [{"n": p["n"], "title": p["title"], "lint_level": p["level"]}
                   for p in phases],
        "summary": {
            "files": inventory["totals"]["files"],
            "skipped": inventory["totals"]["skipped"],
            "text_bytes": inventory["totals"]["text_bytes"],
            "walk": inventory["walk"],
            "git_head": inventory["git_head"],
            "languages": inventory["project"]["languages"],
            "frameworks": inventory["project"]["frameworks"],
            "units": len(inventory["units"]),
            "constitution": "existing" if not constitution_needed else "to be written",
        },
    }


def template_dir_note(src):
    local = os.path.join(src, ".specify", "templates")
    return local if os.path.isdir(local) else SPECSTRIDE_TEMPLATES


# ─────────────────────────────────────────────────────────────────────────────
#  lint
# ─────────────────────────────────────────────────────────────────────────────

class Findings:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, rule, file, line, message):
        self.errors.append({"rule": rule, "file": file, "line": line, "message": message})

    def warn(self, rule, file, line, message):
        self.warnings.append({"rule": rule, "file": file, "line": line, "message": message})

    def as_dict(self):
        key = lambda f: (f["file"] or "", f["line"] or 0, f["rule"], f["message"])  # noqa: E731
        return {"errors": sorted(self.errors, key=key),
                "warnings": sorted(self.warnings, key=key)}


def load_template(name, src):
    """A template's text: `<SRC>/.specify/templates/` first, else the vendored copy."""
    for base in ([os.path.join(src, ".specify", "templates")] if src else []) \
            + [SPECSTRIDE_TEMPLATES]:
        path = os.path.join(base, name)
        if os.path.isfile(path):
            return read(path), path
    return None, None


ANNOTATION_RE = re.compile(r'\*\([^)]*\)\*')
OPTIONAL_MARKS = re.compile(r'\*\(\s*optional\s*\)\*|\*\(\s*include if[^)]*\)\*|\bOPTIONAL\b'
                            r'|\[REMOVE IF UNUSED\]', re.I)
MANDATORY_MARK = re.compile(r'\*\(\s*mandatory\s*\)\*', re.I)
BRACKET_RE = re.compile(r'\[[^\]]+\]')
TEMPLATE_PHASE = re.compile(r'^Phase\s+(\d+|N)\s*:', re.I)


def _norm_heading(text):
    text = ANNOTATION_RE.sub("", text)
    text = re.sub(r'[^\w\s&:.,()\[\]/`\'"-]+', " ", text)   # emoji and ornaments
    return re.sub(r'\s+', " ", text).strip().lower()


def h2_headings(text):
    """(line_no, text) of every `## ` heading outside fenced code."""
    out = []
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.startswith("## "):
            out.append((number, line[3:].strip()))
    return out


def template_rules(template_text):
    """Classify the template's `##` headings: required, optional, family, expected.
    Returns a list of (kind, display, matcher) where matcher takes a normalized
    artifact heading."""
    rules = []
    seen_families = set()
    phase_family = False
    for _line, heading in h2_headings(strip_front_matter(template_text)):
        # The templates number their sample stories ("Parallel Example: User Story
        # 1"); that number is sample text, so it matches like a placeholder. The
        # heading carries no mark, so a miss stays a warning, as for any other.
        sample = re.sub(r'\bUser Story \d+\b', "User Story [N]", heading)
        if sample != heading and not BRACKET_RE.search(heading):
            norm = _norm_heading(sample)
            pattern = "^" + ".+".join(re.escape(p) for p in BRACKET_RE.split(norm)) + "$"
            rules.append(("expected", heading,
                          lambda h, rx=re.compile(pattern): bool(rx.match(h))))
            continue
        if MANDATORY_MARK.search(heading):
            norm = _norm_heading(heading)
            rules.append(("required", heading, lambda h, n=norm: h == n))
            continue
        if OPTIONAL_MARKS.search(heading):
            continue
        if TEMPLATE_PHASE.match(heading):
            phase_family = True
            continue
        if BRACKET_RE.search(heading):
            norm = _norm_heading(heading)
            pieces = BRACKET_RE.split(norm)
            pattern = "^" + ".+".join(re.escape(p) for p in pieces) + "$"
            if pattern in seen_families:
                continue
            seen_families.add(pattern)
            rules.append(("family", heading,
                          lambda h, rx=re.compile(pattern): bool(rx.match(h))))
            continue
        norm = _norm_heading(heading)
        rules.append(("expected", heading, lambda h, n=norm: h == n))
    if phase_family:
        rules.append(("phases", "Phase N:", lambda h: bool(re.match(r'^phase \d+\s*:', h))))
    return rules


def lint_template(findings, rel, text, template_text, template_path):
    headings = [(n, _norm_heading(h)) for n, h in h2_headings(strip_front_matter(text))]
    rules = template_rules(template_text)
    # A family (`## [Category 1]`) must be met by a heading of its own, not by one
    # an explicit template heading (`## Notes`) already accounts for.
    explicit = {n for kind, _d, matcher in rules if kind in ("required", "expected")
                for n, h in headings if matcher(h)}
    for kind, display, matcher in rules:
        pool = [(n, h) for n, h in headings if kind != "family" or n not in explicit]
        if any(matcher(h) for _n, h in pool):
            continue
        where = os.path.relpath(template_path, ROOT) if template_path.startswith(ROOT) \
            else template_path
        if kind == "required":
            findings.error("template.mandatory", rel, None,
                           "missing mandatory section '## %s' (from %s)" % (display, where))
        elif kind == "family":
            findings.error("template.family", rel, None,
                           "no section matches the template family '## %s' (from %s)"
                           % (display, where))
        elif kind == "phases":
            findings.error("template.family", rel, None,
                           "no '## Phase <n>:' section (the template's phase family, from %s)"
                           % where)
        else:
            findings.warn("template.expected", rel, None,
                          "expected section '## %s' is missing (from %s)" % (display, where))


def template_comments(template_text):
    return [re.sub(r'\s+', " ", c).strip()
            for c in re.findall(r'<!--(.*?)-->', template_text, re.S)
            if len(c.strip()) > 20]


def lint_placeholders(findings, rel, text, template_text):
    for number, line in enumerate(text.splitlines(), 1):
        for rx, label in PLACEHOLDERS:
            if rx.search(line):
                findings.error("placeholder", rel, number,
                               "template placeholder %s left in the artifact" % label)
    if template_text:
        tokens = set(UPPER_PLACEHOLDER.findall(template_text))
        for number, line in enumerate(text.splitlines(), 1):
            for token in UPPER_PLACEHOLDER.findall(line):
                if token in tokens and not re.match(r'^\[US\d+\]$', token):
                    findings.error("placeholder", rel, number,
                                   "template placeholder %s left in the artifact" % token)
        body = re.sub(r'\s+', " ", " ".join(re.findall(r'<!--(.*?)-->', text, re.S)))
        for comment in template_comments(template_text):
            if comment in body:
                findings.error("placeholder", rel, None,
                               "template HTML comment copied verbatim: <!-- %s… -->"
                               % comment[:60])


def lint_structure(findings, feature_dir, level, constitution_needed):
    present = set()
    for dirpath, dirnames, filenames in os.walk(feature_dir):
        dirnames.sort()
        for name in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, name), feature_dir).replace(os.sep, "/")
            present.add(rel)
    contracts = sorted(p for p in present if re.match(r'^contracts/[^/]+\.md$', p))
    allowed = set(LAYOUT) | set(contracts)
    if constitution_needed is False:
        allowed.discard("memory/constitution.md")
    for rel in sorted(present - allowed):
        findings.error("structure.extra", rel, None,
                       "not part of the Spec Kit feature layout%s"
                       % (" (the source already has .specify/memory/constitution.md)"
                          if rel == "memory/constitution.md" else ""))
    for owner in range(1, level + 1):
        for rel in OWNERSHIP[owner]:
            if rel == CONTRACTS_DIR:
                if not contracts:
                    findings.error("structure.contracts", "contracts/", None,
                                   "contracts/ holds no .md contract")
                continue
            if rel == "memory/constitution.md" and not constitution_needed:
                continue
            if rel not in present:
                findings.error("structure.required", rel, None,
                               "required at --upto %d but missing" % level)
    return present, contracts


# ── evidence ────────────────────────────────────────────────────────────────

class EvidenceContext:
    """Resolves citations against <SRC> with the inventory's rules."""

    def __init__(self, src, inventory, excluded_roots):
        self.src = _real(src)
        self.inventory = inventory
        self.excluded = [e for e in excluded_roots if e]
        if inventory:
            self.lines = {f["path"]: f["lines"] for f in inventory["files"]}
            self.skipped = {s["path"]: s["reason"] for s in inventory["skipped"]}
            self.workspaces = [w["path"] for w in inventory["project"]["workspaces"]]
        else:
            self.lines = None
            self.skipped = {}
            self.workspaces = self._scan_workspaces()

    def _scan_workspaces(self):
        found = []
        for dirpath, dirnames, filenames in os.walk(self.src):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in reverse_inventory.DENY_DIRS)
            if "package.json" in filenames and dirpath != self.src:
                found.append(os.path.relpath(dirpath, self.src).replace(os.sep, "/"))
        return found

    def _exists(self, rel):
        if self.lines is not None:
            return rel in self.lines or rel in self.skipped
        return os.path.isfile(os.path.join(self.src, rel))

    def line_count(self, rel):
        if self.lines is not None:
            return self.lines.get(rel)
        try:
            with open(os.path.join(self.src, rel), "rb") as handle:
                data = handle.read()
        except OSError:
            return None
        if b"\0" in data[:reverse_inventory.SNIFF_BYTES]:
            return None
        return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)

    def resolve(self, raw):
        """(rel, error). Normalises `./`, refuses absolute and `..` escapes, then tries
        the path as SRC-relative and, for a monorepo, workspace-relative."""
        if raw.startswith("/") or re.match(r'^[A-Za-z]:[\\/]', raw):
            return None, "absolute path (cite it SRC-relative)"
        norm = os.path.normpath(raw).replace(os.sep, "/")
        if norm == ".." or norm.startswith("../"):
            return None, "path escapes the source tree"
        for root in self.excluded:
            if norm == root or norm.startswith(root + "/"):
                return None, "cites a generated artifact under %s/, not the source" % root
        if self._exists(norm):
            return norm, None
        hits = [w + "/" + norm for w in self.workspaces if self._exists(w + "/" + norm)]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, "ambiguous across workspace packages: %s" % ", ".join(sorted(hits))
        return None, "no such file in the source"


def parse_evidence(body):
    """Parse the inside of an evidence comment. Returns (fields, problems)."""
    parts = [p.strip() for p in body.split("|")]
    problems = []
    if len(parts) != 5:
        return None, ["evidence comment needs 5 '|'-separated fields "
                      "(citations | kind= | confidence= | contradicts= | validation=)"]
    fields = {"citations": parts[0]}
    for part, key in zip(parts[1:], ("kind", "confidence", "contradicts", "validation")):
        if not part.startswith(key + "="):
            problems.append("field '%s' must be '%s=…'" % (part, key))
            continue
        fields[key] = part[len(key) + 1:].strip()
    return fields, problems


def check_evidence(findings, rel, number, body, ectx, claim_is_fr=False):
    fields, problems = parse_evidence(body)
    for problem in problems:
        findings.error("evidence.format", rel, number, problem)
    if fields is None:
        return None
    kind = fields.get("kind")
    if kind == "desired":
        findings.error("evidence.kind", rel, number,
                       "kind=desired is forbidden in as-is artifacts")
    elif kind is not None and kind not in KINDS:
        findings.error("evidence.kind", rel, number,
                       "kind must be observed, inferred or declared (got %r)" % kind)
    conf = fields.get("confidence")
    confidence = None
    if conf is not None:
        if not CONFIDENCE_RE.match(conf):
            findings.error("evidence.confidence", rel, number,
                           "confidence must be in [0,1] with two decimals (got %r)" % conf)
        else:
            confidence = float(conf)
    validation = fields.get("validation")
    if validation is not None and validation not in VALIDATIONS:
        findings.error("evidence.validation", rel, number,
                       "validation must be verified, unverified or disproved (got %r)"
                       % validation)
    if validation == "disproved" and claim_is_fr:
        findings.error("evidence.disproved", rel, number,
                       "a disproved requirement must be removed, not kept")
    _check_citations(findings, rel, number, fields["citations"], ectx, "evidence.citation")
    contradicts = fields.get("contradicts")
    if contradicts is not None and contradicts != "none":
        _check_citations(findings, rel, number, contradicts, ectx, "evidence.contradicts")
    return {"confidence": confidence, "validation": validation}


def _check_citations(findings, rel, number, text, ectx, rule):
    citations = [c.strip() for c in text.split(", ")] if text.strip() else []
    if not citations:
        findings.error(rule, rel, number, "no citation")
        return
    for citation in citations:
        m = CITATION_RE.match(citation)
        if not m:
            findings.error(rule, rel, number,
                           "citation %r is not path:START[-END]" % citation)
            continue
        path, error = ectx.resolve(m.group("path"))
        if error:
            findings.error(rule, rel, number, "%s: %s" % (citation, error))
            continue
        start = int(m.group("start"))
        end = int(m.group("end") or start)
        count = ectx.line_count(path)
        if count is None:
            reason = ectx.skipped.get(path, "not a text file")
            findings.error(rule, rel, number,
                           "%s: %s is not a text file the inventory counted (%s)"
                           % (citation, path, reason))
            continue
        if start < 1 or end < start or end > count:
            findings.error(rule, rel, number, "%s: line range outside %s (1-%d)"
                           % (citation, path, count))


def claim_blocks(lines, is_claim):
    """Yield (claim_line_no, next_line_no, next_line) for each claim. A claim's block
    runs over its wrapped continuation lines; the evidence comment is the line right
    after the block."""
    index = 0
    while index < len(lines):
        if is_claim(index, lines[index]):
            start = index
            index += 1
            while index < len(lines):
                nxt = lines[index]
                stripped = nxt.strip()
                if (not stripped or stripped.startswith(("<!--", "#", "- ", "* ", "|", "```"))
                        or re.match(r'^\d+\.\s', stripped) or is_claim(index, nxt)):
                    break
                index += 1
            nxt = lines[index] if index < len(lines) else ""
            yield start + 1, index + 1, nxt
            continue
        index += 1


def lint_claims(findings, rel, text, is_claim, ectx, what, fr=False, spec_sections=None):
    """Every claim needs an evidence comment on the line after it; then EVERY
    evidence comment in the file is checked, attached to a claim or not."""
    lines = text.splitlines()
    attached = {}
    for claim_no, next_no, nxt in claim_blocks(lines, is_claim):
        if not EVIDENCE_RE.search(nxt) or not nxt.strip().startswith("<!--"):
            findings.error("evidence.missing", rel, claim_no,
                           "%s has no evidence comment on the line after it" % what)
            continue
        attached[next_no] = claim_no
    if ectx is None:
        return
    for number, line in enumerate(lines, 1):
        for m in EVIDENCE_RE.finditer(line):
            claim_no = attached.get(number)
            claim = lines[claim_no - 1] if claim_no else ""
            result = check_evidence(findings, rel, number, m.group(1), ectx,
                                    claim_is_fr=fr and bool(FR_MENTION.search(claim)))
            if claim_no and result and spec_sections is not None \
                    and result["validation"] == "unverified" \
                    and result["confidence"] is not None and result["confidence"] < 0.5:
                section = spec_sections(claim_no)
                if section != "assumptions":
                    findings.warn("unknowns.placement", rel, claim_no,
                                  "unverified claim below 0.50 confidence belongs under "
                                  "Assumptions, not among the %s"
                                  % (section or "requirements"))


def section_lookup(text):
    """Map line number -> normalized name of the enclosing `##` section."""
    marks = [(n, _norm_heading(h)) for n, h in h2_headings(text)]

    def lookup(number):
        current = None
        for n, name in marks:
            if n <= number:
                current = name
        return current
    return lookup


# ── per-artifact rules ──────────────────────────────────────────────────────

def lint_spec(findings, text, ectx):
    rel = "spec.md"
    lines = text.splitlines()
    for rx_line, rx_mention, label in ((FR_LINE, FR_MENTION, "FR"), (SC_LINE, SC_MENTION, "SC")):
        seen = {}
        for number, line in enumerate(lines, 1):
            m = rx_mention.search(line)
            if not m or not line.lstrip().startswith("- **%s-" % label):
                continue
            if not rx_line.match(line) or len(m.group(1)) != 3:
                findings.error("spec.%s-format" % label.lower(), rel, number,
                               "%s must be written '- **%s-###**: …' with a 3-digit id"
                               % (label, label))
                continue
            if m.group(1) in seen:
                findings.error("spec.%s-unique" % label.lower(), rel, number,
                               "%s-%s duplicates line %d" % (label, m.group(1), seen[m.group(1)]))
            else:
                seen[m.group(1)] = number
    stories = []
    for number, line in enumerate(lines, 1):
        if STORY_ANY.match(line):
            m = STORY_HEAD.match(line)
            if not m:
                findings.error("spec.story-format", rel, number,
                               "user story heading must be '### User Story <n> - <title>"
                               " (Priority: P<n>)'")
                continue
            stories.append((number, int(m.group(1))))
    if not stories:
        findings.error("spec.story-format", rel, None, "no '### User Story <n> - …' found")
    numbers = [n for _l, n in stories]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        findings.error("spec.story-numbers", rel, stories[0][0],
                       "user story numbers must run 1..%d in order (got %s)"
                       % (len(numbers), ", ".join(map(str, numbers))))
    bounds = [l for l, _n in stories] + [len(lines) + 1]
    for (start, story), end in zip(stories, bounds[1:]):
        body = "\n".join(lines[start:end - 1])
        body = body.split("\n## ", 1)[0]
        for needle, label in (("**Why this priority**", "Why this priority"),
                              ("**Independent Test**", "Independent Test")):
            if needle not in body:
                findings.error("spec.story-parts", rel, start,
                               "User Story %d lacks **%s**" % (story, label))
        if not GIVEN_WHEN_THEN.search(body):
            findings.error("spec.story-parts", rel, start,
                           "User Story %d has no **Given** … **When** … **Then** scenario"
                           % story)
    count = text.count(NEEDS_CLARIFICATION)
    if count > MAX_CLARIFICATIONS:
        findings.error("spec.clarifications", rel, None,
                       "%d [NEEDS CLARIFICATION markers; Spec Kit allows at most %d"
                       % (count, MAX_CLARIFICATIONS))
    lint_claims(findings, rel, text,
                lambda _i, line: bool(re.match(r'^- \*\*(FR|SC)-\d+\*\*', line)),
                ectx, "requirement/success criterion", fr=True,
                spec_sections=section_lookup(text))
    return {int(n) for _l, n in stories}, set(
        m.group(1) for line in lines for m in [FR_LINE.match(line)] if m)


def lint_research(findings, text, ectx):
    rel = "research.md"
    if not any(DECISION_LINE.match(line) for line in text.splitlines()):
        findings.error("research.decisions", rel, None,
                       "no '**Decision**' entry (Decision / Rationale / Alternatives)")
    lint_claims(findings, rel, text, lambda _i, line: bool(DECISION_LINE.match(line)),
                ectx, "research decision")


def lint_data_model(findings, text, ectx):
    rel = "data-model.md"
    lines = text.splitlines()
    if not any(ENTITY_HEAD.match(line) for line in lines):
        findings.warn("data-model.entities", rel, None, "no '### <Entity>' heading")
    lint_claims(findings, rel, text, lambda _i, line: bool(ENTITY_HEAD.match(line)),
                ectx, "data-model entity")


def lint_checklist(findings, text, ectx):
    rel = "checklists/requirements.md"
    lines = text.splitlines()
    items = 0
    for index, line in enumerate(lines):
        m = re.match(r'^- \[([ xX])\] ', line)
        if not m:
            continue
        items += 1
        if m.group(1) in "xX":
            continue
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        if not re.match(r'^\s+Fails:', nxt):
            findings.error("checklist.items", rel, index + 1,
                           "unchecked item without an indented 'Fails:' note")
            continue
        if not EVIDENCE_RE.search(nxt):
            findings.error("checklist.items", rel, index + 2,
                           "'Fails:' note carries no evidence comment")
    if not items:
        findings.error("checklist.items", rel, None, "no checklist items")
    lint_claims(findings, rel, text, lambda _i, _line: False, ectx, "item")


def _task_paths(desc):
    paths = set()
    for m in PATH_TOKEN.finditer(desc):
        token = m.group(1) or m.group(2) or m.group(3)
        if token and ("/" in token or re.search(r'\.[A-Za-z]\w{0,7}$', token)) \
                and not re.match(r'^(FR|SC|US|T)-?\d+', token) and "://" not in token:
            paths.add(token.strip("./") if token.startswith("./") else token)
    return paths


def lint_tasks(findings, text, spec_stories):
    rel = "tasks.md"
    ok, _count, errors = specstride_spec.validate(text, "speckit-tasks")
    if not ok:
        for error in errors:
            findings.error("tasks.grammar", rel, None, error)
    body_offset = len(text.splitlines()) - len(strip_front_matter(text).splitlines())
    lines = text.splitlines()
    ids = []
    phases = []            # [start_line, n, title, [tasks]]
    fenced = False
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or number <= body_offset:
            continue
        head = PHASE_HEAD.match(line)
        if head:
            phases.append([number, int(head.group(1)), head.group(2).strip(), []])
            continue
        if line.startswith("## "):
            phases.append([number, None, line[3:].strip(), []])
            continue
        if not CHECKBOX_LINE.match(line):
            continue
        if not TASK_LINE.match(line):
            findings.error("tasks.line-format", rel, number,
                           "task must be '- [ ] T### [P]? [USn]? description'")
            continue
        m = TASK_PARTS.match(line)
        ids.append((number, int(m.group("id"))))
        if phases:
            phases[-1][3].append((number, m))
    last = 0
    seen = set()
    for number, tid in ids:
        if tid in seen:
            findings.error("tasks.ids", rel, number, "T%03d is not unique" % tid)
        elif tid <= last:
            findings.error("tasks.ids", rel, number,
                           "T%03d is not increasing (after T%03d)" % (tid, last))
        seen.add(tid)
        last = max(last, tid)
    task_phases = [p for p in phases if p[1] is not None and p[3]]
    if task_phases:
        first, second, final = task_phases[0], task_phases[1:2], task_phases[-1]
        if not first[2].lower().startswith("setup"):
            findings.error("tasks.phase-order", rel, first[0],
                           "the first phase must be Setup (got '%s')" % first[2])
        if not second or not second[0][2].lower().startswith("foundational"):
            findings.error("tasks.phase-order", rel, second[0][0] if second else first[0],
                           "the second phase must be Foundational")
        if len(task_phases) < 3 or not final[2].lower().startswith("polish"):
            findings.error("tasks.phase-order", rel, final[0],
                           "the last phase must be Polish (got '%s')" % final[2])
        middle = task_phases[2:-1]
        story_order = []
        for start, _n, title, _tasks in middle:
            m = STORY_IN_TITLE.search(title)
            if not m:
                findings.error("tasks.phase-order", rel, start,
                               "a phase between Foundational and Polish must name a user"
                               " story (got '%s')" % title)
            else:
                story_order.append(int(m.group(1)))
        if story_order != sorted(story_order):
            findings.error("tasks.phase-order", rel, middle[0][0] if middle else None,
                           "user story phases are not in priority order: %s"
                           % ", ".join("US%d" % s for s in story_order))
    mode = TASKS_MODE_RE.search(text)
    for start, _n, title, tasks in phases:
        m = STORY_IN_TITLE.search(title)
        own = int(m.group(1)) if m and _n is not None else None
        generic = _n is not None and title.lower().startswith(("setup", "foundational",
                                                                 "polish"))
        parallel = {}
        for number, task in tasks:
            us = task.group("us")
            if us is not None:
                if generic or own is None:
                    findings.error("tasks.story-labels", rel, number,
                                   "[US%s] outside a user-story phase" % us)
                elif int(us) != own:
                    findings.error("tasks.story-labels", rel, number,
                                   "[US%s] in the phase of User Story %d" % (us, own))
                if spec_stories is not None and int(us) not in spec_stories:
                    findings.error("tasks.story-exists", rel, number,
                                   "[US%s] names a story spec.md does not have" % us)
            if task.group("p"):
                for path in _task_paths(task.group("desc")):
                    if path in parallel:
                        findings.error("tasks.parallel-files", rel, number,
                                       "[P] task names %s, as does the [P] task on line %d"
                                       % (path, parallel[path]))
                    else:
                        parallel[path] = number
            if mode:
                box = task.group("box")
                if mode.group(1) == "done" and box not in "xX":
                    findings.error("tasks.mode", rel, number,
                                   "tasks=done but this task is not [x]")
                elif mode.group(1) == "open" and box != " ":
                    findings.error("tasks.mode", rel, number,
                                   "tasks=open but this task is ticked")
    if mode and mode.group(1) not in TASK_MODES:
        findings.error("tasks.mode", rel, None, "unknown tasks mode %r" % mode.group(1))
    return [m.group("desc") for _p in phases for _n, m in _p[3]]


def lint(feature_dir, src=None, baseline=None, upto=5, claims=None):
    findings = Findings()
    if not os.path.isdir(feature_dir):
        raise ReverseError("feature directory not found: %s" % feature_dir)
    if upto not in LEVELS:
        raise ReverseError("--upto must be one of %s" % ", ".join(map(str, LEVELS)))
    feature_dir = _real(feature_dir)
    inventory = load_inventory(baseline) if baseline else None
    ectx = None
    constitution_needed = None
    if src:
        if not os.path.isdir(src):
            raise ReverseError("--src is not a directory: %s" % src)
        src = _real(src)
        constitution_needed = not os.path.isfile(
            os.path.join(src, ".specify", "memory", "constitution.md"))
        excluded = []
        if _is_within(feature_dir, src):
            excluded.append(os.path.relpath(feature_dir, src).replace(os.sep, "/"))
        excluded.append(state_basename(src))
        ectx = EvidenceContext(src, inventory, excluded)
    else:
        findings.warn("evidence.skipped", None, None,
                      "no --src: evidence citations were not checked")
    present, contracts = lint_structure(findings, feature_dir, upto, constitution_needed)
    owned = set()
    for owner in range(1, upto + 1):
        owned.update(OWNERSHIP[owner])

    def text_of(rel):
        return read(os.path.join(feature_dir, rel)) if rel in present else None

    for rel, template in sorted(TEMPLATE_FOR.items()):
        if rel not in owned or rel not in present:
            continue
        template_text, template_path = load_template(template, src)
        text = text_of(rel)
        if template_text is not None:
            lint_template(findings, rel, text, template_text, template_path)
        lint_placeholders(findings, rel, text, template_text)
    for rel in ("research.md", "data-model.md", "quickstart.md") + tuple(contracts):
        owner_rel = CONTRACTS_DIR if rel.startswith("contracts/") else rel
        if owner_rel in owned and rel in present:
            lint_placeholders(findings, rel, text_of(rel), None)
    spec_stories, frs = None, set()
    if "spec.md" in owned and "spec.md" in present:
        spec_stories, frs = lint_spec(findings, text_of("spec.md"), ectx)
    if "checklists/requirements.md" in owned and "checklists/requirements.md" in present:
        lint_checklist(findings, text_of("checklists/requirements.md"), ectx)
    if "research.md" in owned and "research.md" in present:
        lint_research(findings, text_of("research.md"), ectx)
    if "data-model.md" in owned and "data-model.md" in present:
        lint_data_model(findings, text_of("data-model.md"), ectx)
    if "tasks.md" in owned and "tasks.md" in present:
        descriptions = lint_tasks(findings, text_of("tasks.md"), spec_stories)
        if upto >= 5:
            mentioned = set()
            for desc in descriptions:
                mentioned.update(re.findall(r'\bFR-(\d{3})\b', desc))
            for fr in sorted(frs - mentioned):
                findings.error("coverage.fr", "tasks.md", None,
                               "FR-%s is not covered by any task" % fr)
    if claims is not None:
        lint_claims_file(findings, claims, ectx)
    return findings


def lint_claims_file(findings, path, ectx):
    rel = os.path.basename(path)
    if not os.path.isfile(path):
        findings.error("claims.missing", rel, None, "claims file not written: %s" % path)
        return
    text = read(path)
    if not any(line.startswith("- ") for line in text.splitlines()):
        findings.error("claims.missing", rel, None, "claims file holds no '- ' claim")
    lint_claims(findings, rel, text, lambda _i, line: line.startswith("- "), ectx, "claim")


# ─────────────────────────────────────────────────────────────────────────────
#  guard
# ─────────────────────────────────────────────────────────────────────────────

def guard(src, baseline_path, out):
    """Return a list of problems (empty = the source is untouched)."""
    src = _real(src)
    baseline = load_inventory(baseline_path)
    state_base = state_basename(src)
    out_rel = os.path.relpath(_real(out), src).replace(os.sep, "/")
    excludes = sorted(set(baseline.get("excludes") or []) | {out_rel})
    files, skipped, _mode, _total = reverse_inventory.walk(src, excludes)
    now = {e["path"]: (e["bytes"], e["sha256"]) for e in files + skipped}
    before = {e["path"]: (e["bytes"], e["sha256"])
              for e in baseline["files"] + baseline["skipped"]}
    problems = []
    if reverse_inventory.fingerprint(files, skipped) != baseline["fingerprint"]:
        for path in sorted(set(before) | set(now)):
            if path not in now:
                problems.append("deleted: %s" % path)
            elif path not in before:
                problems.append("added: %s" % path)
            elif now[path] != before[path]:
                problems.append("changed: %s" % path)
        if not problems:
            problems.append("fingerprint differs from the baseline")
    if os.path.isdir(os.path.join(src, "testautomation")) and \
            not any(p.startswith("testautomation/") for p in before):
        problems.append("added: testautomation/ (verification output landed in the source;"
                        " pass --test-plan/--generate-tests under the state dir)")
    if baseline.get("walk") == "git":
        head = reverse_inventory.git_head(src)
        if head != baseline.get("git_head"):
            problems.append("git HEAD moved: %s -> %s" % (baseline.get("git_head"), head))
        status = reverse_inventory.git_status(src) or []
        allowed = (out_rel, state_base)
        known = set(baseline.get("git_status") or [])
        for entry in status:
            path = entry[3:].strip('"')
            if any(path == a or path.startswith(a + "/") or path == a + "/" for a in allowed):
                continue
            if entry in known:
                continue
            problems.append("git status: %s" % entry)
    return sorted(set(problems), key=problems.index)


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _print_findings(result, as_json):
    if as_json:
        print(json.dumps(result, indent=1, sort_keys=True))
        return
    for severity in ("errors", "warnings"):
        for f in result[severity]:
            where = f["file"] or "-"
            if f["line"]:
                where += ":%d" % f["line"]
            print("%s %s [%s] %s" % ("ERROR" if severity == "errors" else "warn ",
                                     where, f["rule"], f["message"]))
    print("%d error(s), %d warning(s)" % (len(result["errors"]), len(result["warnings"])))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="reverse.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="write the inventory, driver and verification commands")
    p.add_argument("src")
    p.add_argument("--out")
    p.add_argument("--name")
    p.add_argument("--tasks", default=None, choices=TASK_MODES)
    l = sub.add_parser("lint", help="the deterministic Spec Kit linter")  # noqa: E741
    l.add_argument("feature_dir")
    l.add_argument("--src")
    l.add_argument("--baseline")
    l.add_argument("--upto", type=int, default=5)
    l.add_argument("--claims")
    l.add_argument("--json", action="store_true")
    g = sub.add_parser("guard", help="fail when the source changed outside the output")
    g.add_argument("src")
    g.add_argument("--baseline", required=True)
    g.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "plan":
            tasks = args.tasks or specstride_env.get("SPECSTRIDE_REVERSE_TASKS", "done") \
                or "done"
            if tasks not in TASK_MODES:
                raise ReverseError("SPECSTRIDE_REVERSE_TASKS must be done or open (got %r)"
                                   % tasks)
            print(json.dumps(plan(args.src, args.out, args.name, tasks), indent=1,
                             sort_keys=True))
            return 0
        if args.cmd == "lint":
            findings = lint(args.feature_dir, args.src, args.baseline, args.upto, args.claims)
            result = findings.as_dict()
            _print_findings(result, args.json)
            return E_FINDINGS if result["errors"] else 0
        problems = guard(args.src, args.baseline, args.out)
        for problem in problems:
            print("guard: %s" % problem)
        if problems:
            print("guard: FAILED — %d change(s) outside %s and the state dir"
                  % (len(problems), args.out))
            return E_FINDINGS
        print("guard: ok — source untouched outside the output and state dirs")
        return 0
    except (ReverseError, reverse_inventory.InventoryError) as exc:
        sys.stderr.write("reverse: %s\n" % exc)
        return E_FINDINGS


if __name__ == "__main__":
    sys.exit(main())
