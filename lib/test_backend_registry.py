#!/usr/bin/env python3
"""Registry guard: lib/backends.py is the single source of backend names.

T003/T006/T008 — unit tests over the registry API, CLI contract tests over
`python3 lib/backends.py`, and G7: the proposer's `case` dispatch and the
critic's `critic_call()` dispatch must equal the registry in both directions.
"""
import ast
import fnmatch
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backends  # noqa: E402

ROOT = os.path.dirname(HERE)
PROPOSER_SH = os.path.join(ROOT, "proposer.sh")
ORCHESTRATOR_SH = os.path.join(ROOT, "orchestrator.sh")
CRITIC_PY = os.path.join(ROOT, "lib", "critic.py")


# ── T003: registry unit tests ────────────────────────────────────────────────

def test_roles():
    assert backends.ROLES == ("proposer", "critic")


@pytest.mark.parametrize("role", ["proposer", "critic"])
def test_names(role):
    assert backends.names(role) == ("dsh", "claude", "codex", "bebop", "prime")


def test_display_strings():
    assert backends.display("proposer") == \
        "dsh[:provider/model] | claude | codex | bebop[:name] | prime[:variant]"
    assert backends.display("critic") == \
        "dsh[:provider/model] | claude | codex | bebop | prime[:variant]"


def test_spelling_forms():
    assert backends.spelling("bebop", "proposer") == "bebop[:name]"
    assert backends.spelling("bebop", "critic") == "bebop"
    assert backends.spelling("dsh", "critic") == "dsh[:provider/model]"


def test_spelling_keyerror_when_name_does_not_serve_role():
    # "gemini" is in no registry entry, so spelling() must raise KeyError.
    with pytest.raises(KeyError):
        backends.spelling("gemini", "proposer")


def test_valueerror_unknown_role():
    for fn in (backends.names, backends.display):
        with pytest.raises(ValueError):
            fn("nope")
    with pytest.raises(ValueError):
        backends.spelling("dsh", "nope")


def test_registry_invariants():
    seen = set()
    for b in backends.REGISTRY:
        assert re.fullmatch(r"[a-z][a-z0-9-]*", b.name), b.name
        assert b.name not in seen
        seen.add(b.name)
        assert len(b.roles) >= 1
        for role, q in b.roles:
            assert role in backends.ROLES
            assert q.form in ("none", "optional", "required")
            assert (q.label is None) == (q.form == "none")
            if q.label is not None:
                assert q.label != "" and not re.search(r"[\]>.|\s]", q.label)


def test_spelling_forms_on_synthetic_entries():
    required = backends.Backend("gemini", (("critic", backends.Qualifier("required", "variant")),))
    cases = [
        (backends.Backend("x1", (("critic", backends.Qualifier("none")),)), "x1"),
        (backends.Backend("x2", (("critic", backends.Qualifier("optional", "key")),)), "x2[:key]"),
        (required, "gemini:variant"),
    ]
    for entry, expect in cases:
        b = entry
        (role, q), = b.roles
        got = b.name if q.form == "none" else (
            "%s[:%s]" % (b.name, q.label) if q.form == "optional"
            else "%s:%s" % (b.name, q.label))
        assert got == expect


# ── T006: CLI contract tests ────────────────────────────────────────────────

CLI = os.path.join(HERE, "backends.py")


def _run_cli(args, cwd):
    return subprocess.run([sys.executable, CLI] + args, cwd=cwd,
                          capture_output=True, text=True)


def test_cli_names_critic(tmp_path):
    r = _run_cli(["names", "--role", "critic"], cwd=str(tmp_path))
    assert r.returncode == 0 and r.stdout == "dsh\nclaude\ncodex\nbebop\nprime\n"


def test_cli_display_strings(tmp_path):
    for role, expect in [
        ("proposer", "dsh[:provider/model] | claude | codex | bebop[:name] | prime[:variant]\n"),
        ("critic", "dsh[:provider/model] | claude | codex | bebop | prime[:variant]\n"),
    ]:
        r1 = _run_cli(["display", "--role", role], cwd=str(tmp_path))
        r2 = _run_cli(["display", "--role", role], cwd=str(tmp_path))
        assert r1.returncode == 0
        assert r1.stdout == expect, role
        assert r1.stdout == r2.stdout  # byte-identical across runs


def test_cli_usage_errors(tmp_path):
    for args in (["display", "--role", "nope"], ["names"], []):
        r = _run_cli(args, cwd=str(tmp_path))
        assert r.returncode == 2, args
        assert r.stdout == "", args


# ── T008: G7 dispatch parity (FR-016, research R7) ──────────────────────────

def extract_proposer_arms(text):
    """Collect {name: form} from run_agent()'s `case "$BACKEND" in ... esac`."""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("run_agent()"))
    cstart = next(i for i, l in enumerate(lines[start:], start)
                  if l.strip() == 'case "$BACKEND" in')
    depth = 0
    cend = None
    for i in range(cstart, len(lines)):
        if re.search(r"\bcase\b", lines[i]):
            depth += 1
        elif re.search(r"\besac\b", lines[i]):
            depth -= 1
            if depth == 0:
                cend = i
                break
    body = lines[cstart + 1:cend]
    forms = {}
    arm_re = re.compile(r"^\s*([A-Za-z0-9_][A-Za-z0-9_|:]*\*?|\*)\)\s*(?:#.*)?$")
    for line in body:
        m = arm_re.match(line)
        if not m:
            continue
        for token in m.group(1).split("|"):
            token = token.strip()
            if token == "*":
                continue  # catch-all
            forms.setdefault(token[:-2] if token.endswith(":*") else token, set())
            forms[token[:-2] if token.endswith(":*") else token].add(
                "qualified" if token.endswith(":*") else "bare")
    return {n: ("optional" if f == {"bare", "qualified"} else
                "required" if f == {"qualified"} else "none")
            for n, f in forms.items()}


def extract_critic_forms(source):
    """Collect {name: form} from critic_call()'s provider ==/startswith checks."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "critic_call")
    forms = {}
    for node in ast.walk(fn):
        if (isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name) and node.left.id == "provider"
                and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)
                and isinstance(node.comparators[0], ast.Constant)):
            forms.setdefault(node.comparators[0].value, set()).add("bare")
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "provider" and node.func.attr == "startswith"
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value.endswith(":")):
            forms.setdefault(node.args[0].value[:-1], set()).add("qualified")
    return {n: ("optional" if f == {"bare", "qualified"} else
                "required" if f == {"qualified"} else "none")
            for n, f in forms.items()}


def test_g7_dispatch_parity():
    registry = {role: {b.name: dict(b.roles)[role].form
                       for b in backends.REGISTRY if role in dict(b.roles)}
                for role in backends.ROLES}
    dispatch = {"proposer": extract_proposer_arms(open(PROPOSER_SH).read()),
                "critic": extract_critic_forms(open(CRITIC_PY).read())}
    for role in backends.ROLES:
        for name in sorted(set(dispatch[role]) | set(registry[role])):
            got = dispatch[role].get(name, "<absent>")
            want = registry[role].get(name, "<absent>")
            assert got == want, "%s dispatch: %s accepts %s, registry says %s" % (
                role, name, got, want)


def test_extract_proposer_arms_synthetic():
    script = 'run_agent() {\n  case "$BACKEND" in\n    dsh|dsh:*)\n      ;;\n' \
             '    gemini)\n      ;;\n    *)\n      ;;\n  esac\n}\n'
    got = extract_proposer_arms(script)
    assert got == {"dsh": "optional", "gemini": "none"}
    # registry entry with no arm must show up as absent in the parity test


def test_extract_critic_forms_synthetic():
    src = "def critic_call(provider, prompt, timeout):\n" \
          "    if provider == 'x' or provider.startswith('x:'):\n" \
          "        return 1\n" \
          "    if provider == 'y':\n" \
          "        return 2\n" \
          "    if provider.startswith('z:'):\n" \
          "        return 3\n" \
          "    raise RuntimeError('unknown')\n"
    assert extract_critic_forms(src) == {"x": "optional", "y": "none", "z": "required"}


# ── T009: G4 rendered-site tests ─────────────────────────────────────────────
# Five rendered sites (guard-policy.md "Declared sites"), each with a plain-
# English reason. They fail until the entry points render from lib/backends.py.

RENDERED_SITES = [
    ("bash orchestrator.sh --help", ("proposer", "critic"),
     "the orchestrator help shows both roles' lists to the operator"),
    ("bash proposer.sh --help", ("proposer",),
     "the proposer help shows the backends --backend accepts"),
    ("proposer.sh --workdir WD --evidence done.txt --prompt-file p "
     "--backend <unknown> (stderr)", ("proposer",),
     "the unknown-backend error lists the valid proposer backends; the pass's "
     "exit status is unchanged"),
    ("python3 lib/critic.py --help", ("critic",),
     "the critic help shows the providers --provider accepts"),
    ('critic_call("<unknown>", ...) exception text', ("critic",),
     "the unknown-provider error lists the valid critic providers; the call is "
     "still recorded as a fail-safe MALFORMED reject"),
]


def test_rendered_sites_are_declared_with_reasons():
    assert len(RENDERED_SITES) == 5
    for argv, roles, reason in RENDERED_SITES:
        assert isinstance(reason, str) and len(reason) > 20
        assert not re.search(r"(FR-|R\d|Clarification|G\d|US\d|SC-)", reason), reason
        assert roles and set(roles) <= set(backends.ROLES), argv


def _assert_has_list(argv, out, role):
    want = backends.display(role)
    assert want in out, "%s: output lacks %s list \"%s\"" % (argv, role, want)


def test_g4_orchestrator_help():
    r = subprocess.run(["bash", ORCHESTRATOR_SH, "--help"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    _assert_has_list("bash orchestrator.sh --help", r.stdout, "proposer")
    _assert_has_list("bash orchestrator.sh --help", r.stdout, "critic")


def test_g4_proposer_help():
    r = subprocess.run(["bash", PROPOSER_SH, "--help"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    _assert_has_list("bash proposer.sh --help", r.stdout, "proposer")


def test_g4_critic_help():
    r = subprocess.run([sys.executable, CRITIC_PY, "--help"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    folded = re.sub(r"\s+", " ", r.stdout)
    _assert_has_list("python3 lib/critic.py --help", folded, "critic")


def test_g4_critic_unknown_provider_exception():
    import critic  # noqa: E402
    argv = 'critic_call("no-such-backend", ...) exception text'
    with pytest.raises(RuntimeError) as ei:
        critic.critic_call("no-such-backend", "p", 1)
    _assert_has_list(argv, str(ei.value), "critic")


# ── T010: proposer unknown-backend behavioural test ─────────────────────────
# T002 recorded the unmodified script's baseline: exit status 4, ~0.6 s.
# Exit 4 (not 127) is correct and expected: run_agent() returns 127 per pass,
# the loop records the pass as launch_failed and, with --max-iter 1 exhausted,
# proposer.sh exits with its budget/max-iter status. 127 never reaches the shell.

UNKNOWN_BACKEND_EXIT = 4  # pinned from T002: launch_failed pass + max-iter 1


def test_g4_proposer_unknown_backend(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("do the work\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPECSTRIDE_")}
    argv = ["bash", PROPOSER_SH,
            "--workdir", str(wd), "--evidence", "done.txt",
            "--prompt-file", str(prompt),
            "--backend", "no-such-backend", "-n", "1", "-s", "0"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == UNKNOWN_BACKEND_EXIT, (r.returncode, r.stderr)
    _assert_has_list(" ".join(argv), r.stderr, "proposer")
    assert "unknown backend 'no-such-backend' (" in r.stderr
    # treated exactly as before: the pass is a launch failure, not a success —
    # the loop iterates once, finds no evidence and exits with the pinned status.
    events = wd / ".specstride" / "events.jsonl"
    assert events.exists(), "no events.jsonl written under the workdir"
    blob = events.read_text()
    assert '"event":"iter_done","iter":"1","evidence":"missing"' in blob


# ── T011: fake-backend temp-tree test (US1-2, SC-002) ────────────────────────

def _copy_tree(tmp_path):
    root = tmp_path / "tree"
    (root / "lib").mkdir(parents=True)
    for name in ("orchestrator.sh", "proposer.sh", "specstride-lib.sh"):
        shutil.copy2(os.path.join(ROOT, name), root / name)
    for name in os.listdir(os.path.join(ROOT, "lib")):
        src = os.path.join(ROOT, "lib", name)
        if os.path.isfile(src):
            shutil.copy2(src, root / "lib" / name)
    return root


FAKE_ENTRY = (
    '    Backend("fakebe", (\n'
    '        ("proposer", Qualifier("none")),\n'
    '        ("critic", Qualifier("none")),\n'
    '    )),\n'
)


def test_fake_backend_renders_everywhere(tmp_path):
    root = _copy_tree(tmp_path)
    bpath = root / "lib" / "backends.py"
    text = bpath.read_text(encoding="utf-8")
    marker = "REGISTRY = ("
    assert marker in text
    text = text.replace(marker, marker + "\n" + FAKE_ENTRY, 1)
    bpath.write_text(text, encoding="utf-8")

    orch = subprocess.run(["bash", str(root / "orchestrator.sh"), "--help"],
                          capture_output=True, text=True, timeout=60)
    prop = subprocess.run(["bash", str(root / "proposer.sh"), "--help"],
                          capture_output=True, text=True, timeout=60)
    crit = subprocess.run([sys.executable, str(root / "lib" / "critic.py"), "--help"],
                          capture_output=True, text=True, timeout=60)
    criterr = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r)\n"
         "import critic\n"
         "try:\n"
         "    critic.critic_call('no-such-backend', 'p', 1)\n"
         "except RuntimeError as e:\n"
         "    print(e)\n" % str(root / "lib")],
        capture_output=True, text=True, timeout=60)

    for label, out in [("orchestrator --help", orch.stdout),
                       ("proposer --help", prop.stdout),
                       ("critic --help", re.sub(r"\s+", " ", crit.stdout)),
                       ("critic_call error", criterr.stdout)]:
        assert "fakebe" in out, label

    # proposer unknown-backend error from the copy
    wd = tmp_path / "wd"
    wd.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("work\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPECSTRIDE_")}
    unk = subprocess.run(
        ["bash", str(root / "proposer.sh"), "--workdir", str(wd),
         "--evidence", "done.txt", "--prompt-file", str(prompt),
         "--backend", "no-such-backend", "-n", "1", "-s", "0"],
        capture_output=True, text=True, timeout=60, env=env)
    assert "fakebe" in unk.stderr, "proposer unknown-backend error"

    # the entry-point files are byte-identical: only lib/backends.py changed
    for name in ("orchestrator.sh", "proposer.sh", "lib/critic.py"):
        orig = open(os.path.join(ROOT, name), "rb").read()
        copy = open(os.path.join(root, name), "rb").read()
        assert orig == copy, name


# ── T020: guard grammar and classifier ───────────────────────────────────────
# TOKEN per guard-policy.md "Grammar": a bare name, optionally qualified with
# a bracket label (`bebop[:name]`) or an angle/colon label (`prime:<variant>`).

TOKEN = r"[a-z][a-z0-9-]*(?:\[:[^\]|\s]+\]|:<[^>|\s]+>)?"
LIST_RE = re.compile(TOKEN + r"(?:\s*\|\s*(?:#\s*)?" + TOKEN + r")+")

REGISTRY_NAMES = set(backends.names("proposer")) | set(backends.names("critic"))


def _bare(token):
    return re.match(r"[a-z][a-z0-9-]*", token).group(0)


def find_lists(text):
    """Every |-joined token list in text as (1-based start line, raw text)."""
    return [(text.count("\n", 0, m.start()) + 1, m.group(0))
            for m in LIST_RE.finditer(text)]


def is_backend_list(raw):
    """A backend list has >= 2 DISTINCT registry names (dsh|dsh:* is not one)."""
    names = {_bare(t.strip()) for t in raw.split("|")}
    return len(names & REGISTRY_NAMES) >= 2


def normalise_list(raw):
    """guard-policy.md 'Normalisation before comparison' — and nothing else."""
    lines = raw.split("\n")
    parts = [lines[0].strip()]
    for ln in lines[1:]:
        parts.append(re.sub(r"^#?\s*", "", ln).strip())
    joined = " ".join(p for p in parts if p)
    joined = joined.replace("`", "")
    joined = re.sub(r"\s*\|\s*", " | ", joined)
    return joined.rstrip().rstrip(".,:").rstrip()


def _spelling_map(s):
    return {_bare(t): t for t in re.split(r"\s*\|\s*", s.strip()) if t}


def list_diff(found, expected):
    """(extra names, missing names, differently spelled names) by bare name."""
    f, e = _spelling_map(found), _spelling_map(expected)
    extra = sorted(set(f) - set(e))
    missing = sorted(set(e) - set(f))
    spelling = sorted(n for n in set(f) & set(e) if f[n] != e[n])
    return extra, missing, spelling


@pytest.mark.parametrize("raw,wanted", [
    ("dsh | gemini | claude", True),                  # stray name gemini
    ("dsh[:provider/model] | claude | codex | bebop", True),   # missing prime
    ("codex | bebop:<name> | prime[:variant]", True),  # bebop spelled with ':'
    ("dsh[:provider/model] | claude | codex | bebop | prime[:variant]",
     True),                                           # merged two-role list
    ("dsh|dsh:*", False),                             # case arm: one name
    ("bebop|bebop:*", False),                         # case arm: one name
    ("dsh[:provider/model] | claude | codex |\nbebop | prime[:variant]",
     True),                                           # docstring wraps a line
    ("`dsh | claude | codex`", True),                 # Markdown backticks
    ("sleep | convert | identify", False),           # not backend names
])
def test_list_classifier(raw, wanted):
    hits = find_lists("x = " + raw)
    assert hits, raw
    assert is_backend_list(hits[0][1]) is wanted, hits[0][1]


def test_classifier_line_numbers():
    text = "first\nsecond: `dsh | claude`\nthird"
    (line, raw), = find_lists(text)
    assert (line, raw) == (2, "dsh | claude")


def test_normalise_and_diff():
    assert normalise_list("`dsh[:provider/model] | claude |\n#   codex | bebop`\n") \
        == "dsh[:provider/model] | claude | codex | bebop"
    assert list_diff("dsh | gemini | bebop:<name> | claude",
                     "dsh | claude | codex | bebop[:name] | prime[:variant]") \
        == (["gemini"], ["codex", "prime"], ["bebop"])


# ── T021: prose sites G1–G3 ──────────────────────────────────────────────────
# Each prose site shows one labelled list per role; the anchor is the label
# text. Reasons are plain-English sentences, never spec labels.

PROSE_SITES = [
    (".env.example",
     {"proposer": r"Proposer backends:", "critic": r"Critic backends:"},
     "the committed environment template documents, next to the role variables, "
     "which backends each role accepts"),
    ("README.md",
     {"proposer": r"Proposer backends:", "critic": r"Critic backends:"},
     "the README's configuration section tells a new operator which backends "
     "each role accepts"),
    ("wiki/Configuration.md",
     {"proposer": r"Proposer backends:", "critic": r"Critic backends:"},
     "the wiki configuration page tells the operator which backends each role "
     "accepts"),
    ("lib/critic.py",
     {"critic": r"Critic backends:"},
     "the critic module's hand-written docstring states which providers the "
     "critic accepts"),
]


def check_prose_site(path, anchors, text):
    """G1/G2/G3 over one file's text; returns failure-message strings."""
    claimed_spans = []
    problems = []
    for role, anchor in anchors.items():
        m = re.search(anchor, text)
        if not m:
            problems.append('%s: no "%s" (role %s)' % (path, anchor, role))
            continue
        lm = LIST_RE.search(text, m.end())
        anchor_line = text.count("\n", 0, m.start()) + 1
        found = normalise_list(lm.group(0)) if lm else ""
        if lm:
            claimed_spans.append(lm.span())
        want = backends.display(role)
        if found != want:
            extra, missing, spelling = list_diff(found, want)
            problems.append(
                '%s:%d: %s list "%s" != registry "%s" '
                "(extra: %s, missing: %s, spelling: %s)"
                % (path, anchor_line, role, found, want,
                   ", ".join(extra) or "-", ", ".join(missing) or "-",
                   ", ".join(spelling) or "-"))
    for m in LIST_RE.finditer(text):
        raw = m.group(0)
        if not is_backend_list(raw):
            continue
        if any(s <= m.start() and m.end() <= e for s, e in claimed_spans):
            continue
        line = text.count("\n", 0, m.start()) + 1
        problems.append("%s:%d: backend list not tied to a role" % (path, line))
    return problems


def check_undeclared_text(path, text):
    """G5 over one file's text; returns failure-message strings."""
    return ['%s:%d: undeclared backend list "%s"'
            % (path, line, re.sub(r"\s+", " ", normalise_list(raw)))
            for line, raw in find_lists(text) if is_backend_list(raw)]


def test_g123_prose_sites_match_registry():
    problems = []
    for path, anchors, _ in PROSE_SITES:
        with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
            problems += check_prose_site(path, anchors, fh.read())
    assert not problems, "\n".join(problems)


# ── T022: undeclared-list scan G5 ────────────────────────────────────────────

HISTORICAL = [
    ("lib/fixtures/*",
     "recorded copies of other projects' files kept only as historical "
     "reference; their backend mentions are quotes, not Specstride facts"),
]

SCAN_EXEMPT = [
    ("lib/test_backend_registry.py",
     "this guard itself quotes drift examples in its inline classifier tests"),
    ("lib/backends.py",
     "the registry module is the single source the lists are derived from, so "
     "its own docstring may not be scanned against itself"),
]


def _glob_hit(pattern, path):
    return fnmatch.fnmatch(path, pattern)


def tracked_files():
    r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                       capture_output=True, text=True, check=True, timeout=60)
    return [p.strip() for p in r.stdout.splitlines() if p.strip()]


def scan_undeclared():
    problems = []
    prose_paths = {path for path, _, _ in PROSE_SITES}
    exempt = [pat for pat, _ in HISTORICAL + SCAN_EXEMPT]
    for rel in tracked_files():
        if rel in prose_paths or any(_glob_hit(p, rel) for p in exempt):
            continue
        full = os.path.join(ROOT, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        if "\0" in text:
            continue
        problems += check_undeclared_text(rel, text)
    return problems


def test_g5_no_undeclared_lists():
    problems = scan_undeclared()
    assert not problems, "\n".join(problems)


# ── T023: stale-policy test G6 ───────────────────────────────────────────────

def test_g6_policy_entries_are_live():
    tracked = tracked_files()
    for path, anchors, _ in PROSE_SITES:
        assert any(t == path for t in tracked), "stale policy entry: %s" % path
        with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        for role, anchor in anchors.items():
            assert any(re.search(anchor, ln) for ln in lines), \
                "stale policy entry: %s anchor %s (role %s)" % (path, anchor, role)
    for pattern, _ in HISTORICAL + SCAN_EXEMPT:
        assert any(_glob_hit(pattern, t) for t in tracked), \
            "stale policy entry: %s" % pattern
    for argv, _, _ in RENDERED_SITES:
        for tok in argv.replace("(", " ").replace(")", " ").split():
            if tok.endswith(".sh") or tok.endswith(".py"):
                assert os.path.exists(os.path.join(ROOT, tok)), \
                    "stale policy entry: %s" % argv


# ── T024: drift fixtures (SC-003) — temp copies, repo untouched ──────────────

README_TEMPLATE = """# Config

Pick a backend per role:

Proposer backends: `dsh[:provider/model] | claude | codex | bebop[:name] | prime[:variant]`

Critic backends: `dsh[:provider/model] | claude | codex | bebop | prime[:variant]`
"""


def _run_g2(tmp_path, body):
    p = tmp_path / "site.md"
    p.write_text(body, encoding="utf-8")
    problems = check_prose_site(str(p), PROSE_SITES[1][1], body)
    assert problems, "expected a drift failure"
    for msg in problems:
        assert msg.startswith(str(p)), msg
    return problems


def test_drift_extra_name_gemini(tmp_path):
    body = README_TEMPLATE.replace(
        "bebop[:name] | prime[:variant]`",
        "bebop[:name] | prime[:variant] | gemini`", 1)
    problems = _run_g2(tmp_path, body)
    assert any("extra: gemini" in m and "proposer list" in m for m in problems)


def test_drift_missing_prime(tmp_path):
    body = README_TEMPLATE.replace(
        "bebop | prime[:variant]`", "bebop`", 1)
    problems = _run_g2(tmp_path, body)
    assert any("critic list" in m and "missing: prime" in m for m in problems)


def test_drift_bebop_spelling(tmp_path):
    body = README_TEMPLATE.replace("bebop[:name]", "bebop:<name>", 1)
    problems = _run_g2(tmp_path, body)
    assert any("proposer list" in m and "spelling: bebop" in m for m in problems)


def test_drift_undeclared_list(tmp_path):
    p = tmp_path / "Telemetry.md"
    body = "# Telemetry\n\nclaude | codex\n"
    p.write_text(body, encoding="utf-8")
    problems = check_undeclared_text(str(p), body)
    assert problems, "expected an undeclared-list failure"
    assert problems[0].startswith(str(p) + ":")
    assert "undeclared backend list" in problems[0]


# ── T026/T027/T028: US3 — the shim and the registry-unavailable fallback ─────
# The note is pinned here (T028) with the exact text of the fallback contract's
# header. The test never reads the contract file (untracked, absent in CI);
# the text was compared with the contract by hand once, locally.

FALLBACK_NOTE = \
    '(backend list unavailable: could not run lib/backends.py; see README "Configuration")'

SPECSTRIDE_LIB_SH = os.path.join(ROOT, "specstride-lib.sh")


def _no_registry_failure(argv, detail):
    return "%s without registry: %s" % (argv, detail)


def test_fallback_note_is_pinned_and_in_the_shim():
    assert FALLBACK_NOTE in open(SPECSTRIDE_LIB_SH, encoding="utf-8").read()
    # the note itself must not be a backend list the guard would flag
    assert not any(is_backend_list(raw) for _, raw in find_lists(FALLBACK_NOTE))


@pytest.mark.parametrize("role", ["proposer", "critic"])
def test_shim_prints_display_from_any_cwd(tmp_path, role):
    argv = ["/bin/bash", "-c",
            ". %s; specstride_backend_display %s" % (SPECSTRIDE_LIB_SH, role)]
    r = subprocess.run(argv, cwd=str(tmp_path), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert r.stdout == backends.display(role) + "\n", role


def _path_without_python(tmp_path):
    """A single dir symlinking every PATH executable (first match per name)
    except any interpreter whose name starts with 'python'."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d or not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name in seen or name.startswith("python"):
                continue
            full = os.path.join(d, name)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                os.symlink(full, bindir / name)
                seen.add(name)
    return bindir


def _assert_fallback(argv, out, stream):
    assert FALLBACK_NOTE in out, _no_registry_failure(argv, "note missing on " + stream)
    lists = [raw for _, raw in find_lists(out) if is_backend_list(raw)]
    assert not lists, _no_registry_failure(
        argv, "backend list still shown: %r" % (lists,))
    for role in backends.ROLES:
        assert backends.display(role) not in out, \
            _no_registry_failure(argv, "%s list rendered" % role)


def test_g8_fallback_when_backends_py_missing(tmp_path):
    root = _copy_tree(tmp_path)
    (root / "lib" / "backends.py").unlink()
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPECSTRIDE_")}
    orch = str(root / "orchestrator.sh")
    prop = str(root / "proposer.sh")

    help_orch = subprocess.run(["bash", orch, "--help"], capture_output=True,
                               text=True, timeout=60, env=env)
    assert help_orch.returncode == 0, _no_registry_failure(
        "bash orchestrator.sh --help", "exit %d" % help_orch.returncode)
    _assert_fallback("bash orchestrator.sh --help", help_orch.stdout, "stdout")

    help_prop = subprocess.run(["bash", prop, "--help"], capture_output=True,
                               text=True, timeout=60, env=env)
    assert help_prop.returncode == 0, _no_registry_failure(
        "bash proposer.sh --help", "exit %d" % help_prop.returncode)
    _assert_fallback("bash proposer.sh --help", help_prop.stdout, "stdout")

    bad_orch = subprocess.run(["bash", orch, "--no-such-flag"],
                              capture_output=True, text=True, timeout=60, env=env)
    assert bad_orch.returncode == 3, _no_registry_failure(
        "bash orchestrator.sh --no-such-flag",
        "exit %d (E_SPEC is 3)" % bad_orch.returncode)
    _assert_fallback("bash orchestrator.sh --no-such-flag",
                     bad_orch.stderr, "stderr")

    # the unmodified proposer exits with the status its own argument parser
    # assigns to an unknown argument (the `*) unknown arg` arm, exit 1)
    m = re.search(r"unknown arg.*?exit (\d+)",
                  open(PROPOSER_SH, encoding="utf-8").read())
    assert m, "could not read proposer.sh's unknown-arg exit status"
    want_prop_exit = int(m.group(1))
    assert want_prop_exit == 1, want_prop_exit
    bad_prop = subprocess.run(["bash", prop, "--no-such-flag"],
                              capture_output=True, text=True, timeout=60, env=env)
    assert bad_prop.returncode == want_prop_exit, _no_registry_failure(
        "bash proposer.sh --no-such-flag", "exit %d" % bad_prop.returncode)
    _assert_fallback("bash proposer.sh --no-such-flag",
                     bad_prop.stderr, "stderr")

    # the proposer unknown-backend error from the copy: T010's harness
    wd = tmp_path / "wd"
    wd.mkdir()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("work\n", encoding="utf-8")
    unk_argv = ["bash", prop, "--workdir", str(wd), "--evidence", "done.txt",
                "--prompt-file", str(prompt),
                "--backend", "no-such-backend", "-n", "1", "-s", "0"]
    unk = subprocess.run(unk_argv, capture_output=True, text=True,
                         timeout=60, env=env)
    assert unk.returncode == UNKNOWN_BACKEND_EXIT, _no_registry_failure(
        " ".join(unk_argv), "exit %d (pinned %d)" % (unk.returncode,
                                                     UNKNOWN_BACKEND_EXIT))
    assert "unknown backend 'no-such-backend' (" in unk.stderr
    _assert_fallback(" ".join(unk_argv), unk.stderr, "stderr")

    # no Python traceback anywhere
    for label, r in [("orchestrator --help", help_orch),
                     ("proposer --help", help_prop),
                     ("orchestrator unknown flag", bad_orch),
                     ("proposer unknown flag", bad_prop),
                     ("proposer unknown backend", unk)]:
        assert "Traceback" not in r.stderr, \
            _no_registry_failure(label, "Python traceback on stderr")


def test_g8_fallback_when_python3_not_on_path(tmp_path):
    bindir = _path_without_python(tmp_path)
    assert shutil.which("python3", path=str(bindir)) is None
    env = dict(os.environ)
    env["PATH"] = str(bindir)
    r = subprocess.run(["bash", ORCHESTRATOR_SH, "--help"], env=env,
                       capture_output=True, text=True, timeout=60)
    argv = "bash orchestrator.sh --help (PATH without python3)"
    assert r.returncode == 0, _no_registry_failure(argv, "exit %d" % r.returncode)
    _assert_fallback(argv, r.stdout, "stdout")
