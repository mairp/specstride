"""Shared end-to-end scaffolding for the SH-2 config-home overlay feature.

The proposer launches a real backend binary for every pass, so the only way to
prove anything about what the harness process actually SAW is to stand a fake
binary in its place and record the launch: argv, $0, stdin and the full
environment, plus which config/state/cache directories the binary touched.

This file provides, in one place:

- a fake backend binary ("recorder") that captures its launch and probes the
  config-home paths the overlay feature is supposed to redirect;
- the temp-tree seam: a copy of proposer.sh + specstride-lib.sh + lib/ that a
  test can mutate (e.g. give registry entries an overlay declaration) without
  touching the real tree;
- helpers to run one proposer pass against such a tree and to snapshot a
  directory byte-for-byte;
- a --record-baseline entry point that records the PRE-FEATURE launch of each
  backend into lib/fixtures/overlay_baseline/, and BaselineTest, which replays
  the same recording against the CURRENT tree and asserts byte-identical
  captures. Together they prove SH-2 changed nothing for a backend that
  declares no overlay — by fixture, never by a hand-copied backend list.

No overlay assertion lives here yet beyond the loader/scope unit tests; the
later feature phases add their own end-to-end cases reusing these helpers.
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import unittest.mock as mock
import contextlib
import inspect
from dataclasses import FrozenInstanceError
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import backend_overlay  # noqa: E402

# lib/critic.py is loaded by spec (not importable as a package member), the same
# way lib/test_prime_backend.py reaches it — the CLI critic helpers it owns are
# the subject of CriticOverlayTest (US4).
import importlib.util as _ilu  # noqa: E402
_SPEC = _ilu.spec_from_file_location(
    "critic", os.path.join(HERE, "critic.py"))
critic = _ilu.module_from_spec(_SPEC)
_SPEC.loader.exec_module(critic)

REPO = Path(__file__).parents[1]
PROPOSER = REPO / "proposer.sh"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "overlay_baseline"

# Env keys whose captured value can never be reproduced byte-for-byte (shell
# depth, cwd, wall-clock run ids, pids). Everything else is compared verbatim.
VOLATILE_ENV_KEYS = {"SHLVL", "PWD", "OLDPWD", "_", "PPID", "SECONDS", "EPOCHREALTIME"}


# ── fake binaries ────────────────────────────────────────────────────────────

RECORDER_BODY = r"""#!/bin/bash
# Test recorder: captures the launch, probes the config-home paths, writes
# the evidence file so the proposer pass completes.
set +u
cap="${SPECSTRIDE_TEST_CAPTURE:?SPECSTRIDE_TEST_CAPTURE not set}"
n="${SPECSTRIDE_TEST_N:-1}"
mkdir -p "$cap"
# env: NUL-separated, exactly as the process saw it
env -0 > "$cap/$n.env"
# argv, NUL-joined (argv[1:] — $0 goes separately)
printf '%s\0' "$@" > "$cap/$n.argv"
printf '%s' "$0" > "$cap/$n.arg0"
# stdin, only when it is a pipe/file (never block on a tty)
if [[ -t 0 ]]; then : > "$cap/$n.stdin"; else cat > "$cap/$n.stdin" 2>/dev/null || : > "$cap/$n.stdin"; fi
# probe every config-home path the overlay feature claims to redirect
mkdir -p "$HOME/.probe" 2>/dev/null
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/probe" 2>/dev/null
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/probe" 2>/dev/null
mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}/probe" 2>/dev/null
mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/probe" 2>/dev/null
mkdir -p "${TMPDIR:-/tmp}/probe.$$" 2>/dev/null
# complete the pass
mkdir -p .specstride/gates
echo ok > .specstride/gates/GATE1-EVIDENCE.md
exit 0
"""

BEBOP_SH_TEMPLATE = """\
# Test stand-in for bebop.sh: bebop() forwards to the recorder binary.
bebop() {{
  local bb="$1"; shift
  "{recorder}" "$bb" "$@"
}}
"""


def write_fake_bin(dir_path, name, body):
    """Write an executable bash script `name` (with `body`) into dir_path."""
    dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    fake = dir_path / name
    fake.write_text(body if body.endswith("\n") else body + "\n")
    fake.chmod(0o755)
    return fake


def fake_recorder_bin(dir_path, name):
    """Stand in for a backend's binary.

    Writes the recorder script as `<dir>/<name>`; for the bebop shell function
    (`name == "bebop.sh"`) it also writes the `bebop` executable the function
    forwards to, mirroring how proposer.sh sources $BEBOP_SH and calls bebop().
    """
    dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    if name == "bebop.sh":
        recorder = write_fake_bin(dir_path, "bebop", RECORDER_BODY)
        write_fake_bin(dir_path, "bebop.sh",
                       BEBOP_SH_TEMPLATE.format(recorder=recorder))
        return dir_path / "bebop.sh"
    return write_fake_bin(dir_path, name, RECORDER_BODY)


BASE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def backend_launcher_env(bin_dir, backend, tmp):
    """Env additions that make `backend` resolve to the fake binary.

    Mirrors how each run_agent arm finds its binary: claude/codex via PATH,
    prime/prime:<variant> and dsh via SPECSTRIDE_*_BIN, bebop via BEBOP_SH.
    """
    env = {"PATH": str(bin_dir) + os.pathsep + BASE_PATH}
    if backend == "claude":
        write_fake_bin(bin_dir, "claude", RECORDER_BODY)
    elif backend == "codex":
        write_fake_bin(bin_dir, "codex", RECORDER_BODY)
    elif backend == "bebop" or backend.startswith("bebop:"):
        bebop_sh = fake_recorder_bin(bin_dir, "bebop.sh")
        env["BEBOP_SH"] = str(bebop_sh)
    elif backend == "prime":
        env["SPECSTRIDE_PRIME_AGENT_BIN"] = str(fake_recorder_bin(bin_dir, "prime-agent"))
        env["SPECSTRIDE_PRIME_BIN"] = ""
        env["SPECSTRIDE_PRIME_FLEET_BIN"] = ""
    elif backend.startswith("prime:"):
        env["SPECSTRIDE_PRIME_FLEET_BIN"] = str(fake_recorder_bin(bin_dir, "prime"))
        env["SPECSTRIDE_PRIME_AGENT_BIN"] = ""
    elif backend == "dsh" or backend.startswith("dsh:"):
        env["SPECSTRIDE_DSH_BIN"] = str(fake_recorder_bin(bin_dir, "dsh"))
        env["SPECSTRIDE_DSH_MODEL"] = ""
        env["SPECSTRIDE_DSH_PROVIDER"] = ""
        # a real (fake) home for the dsh settings overlay to link through
        home = Path(tmp) / "dsh-home"
        home.mkdir(exist_ok=True)
        if not (home / "settings.yaml").exists():
            (home / "settings.yaml").write_text(
                "agent-default-model:\n  provider: someone-else\n  model: not-this-one\n")
        env["DSH_HOME"] = str(home)
    else:
        raise AssertionError("no fake-binary convention known for backend %r" % backend)
    return env


# ── the temp-tree seam ───────────────────────────────────────────────────────

def copy_tree(tmp):
    """Copy proposer.sh, specstride-lib.sh and lib/ into tmp (a fresh dir).

    Returns the tree root. Tests mutate ONLY the copy.
    """
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROPOSER, tmp / "proposer.sh")
    shutil.copy2(REPO / "specstride-lib.sh", tmp / "specstride-lib.sh")
    shutil.copytree(REPO / "lib", tmp / "lib",
                    ignore=shutil.ignore_patterns("__pycache__"),
                    ignore_dangling_symlinks=True)
    (tmp / "lib" / "fixtures").mkdir(exist_ok=True)
    return tmp


def set_declarations(tree, mapping):
    """Give the COPY's registry entries an `overlay` value.

    Appends one statement to the copied lib/backends.py rebuilding REGISTRY
    with dataclasses.replace(entry, overlay=...) for each key of `mapping`
    ({name: {"vars": ..., "seeds": ...}}). Fails loudly if a key names no
    registry entry. Never touches the real lib/backends.py. (The copied
    registry can only carry the value once the Backend record has the
    `overlay` field; before that, replace() raises — which is itself loud.)
    """
    tree = Path(tree)
    backends = tree / "lib" / "backends.py"
    source = backends.read_text()
    import re
    names = re.findall(r'Registry\("([a-z0-9]+)"|Backend\("([a-z0-9]+)"', source)
    known = {a or b for a, b in names}
    unknown = set(mapping) - known
    if unknown:
        raise AssertionError(
            "set_declarations: no registry entry for %s (known: %s)"
            % (sorted(unknown), sorted(known)))
    lines = ["\n# --- test overlay declarations (set_declarations) ---\n",
             "import dataclasses\n"]
    for key, value in mapping.items():
        lines.append(
            "REGISTRY = tuple(dataclasses.replace(b, overlay=%r) if b.name == %r else b "
            "for b in REGISTRY)\n" % (value, key))
    with backends.open("a") as handle:
        handle.write("".join(lines))
    return backends


# ── running one proposer pass ────────────────────────────────────────────────

def _fake_home_env(tmp):
    home = Path(tmp) / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home)}
    for var, sub in (("XDG_CONFIG_HOME", "xdg/config"),
                     ("XDG_DATA_HOME", "xdg/data"),
                     ("XDG_CACHE_HOME", "xdg/cache"),
                     ("XDG_STATE_HOME", "xdg/state")):
        path = Path(tmp) / sub
        path.mkdir(parents=True, exist_ok=True)
        env[var] = str(path)
    tmpdir = Path(tmp) / "tmp"
    tmpdir.mkdir(exist_ok=True)
    env["TMPDIR"] = str(tmpdir)
    return env


def run_proposer(tree, workdir, backend, env, n=1, timeout=1800, extra_args=None,
                 prompt_text=None):
    """Run one proposer pass against `tree` and return the CompletedProcess."""
    proc = run_proposer_popen(tree, workdir, backend, env, n=n,
                              extra_args=extra_args, prompt_text=prompt_text)
    stdout, stderr = proc.communicate(timeout=timeout)
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout,
                                       stderr)


def run_proposer_popen(tree, workdir, backend, env, n=1, extra_args=None,
                       prompt_text=None):
    """Start one proposer pass without waiting (signal / concurrency tests)."""
    tree = Path(tree)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    prompt = workdir / "prompt.txt"
    prompt.write_text(prompt_text if prompt_text is not None
                      else "standing prompt for the overlay test\n")
    evidence = workdir / ".specstride" / "gates" / "GATE1-EVIDENCE.md"
    full_env = {
        # minimal hermetic base: the proposer, the watchdog and python3 only —
        # never the caller's ambient session env (its session ids, sockets and
        # nonces would make the recorded baseline unreproducible)
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        # proposer.sh exports IS_SANDBOX=1 only when run as root; pinning it
        # keeps the recorded launch the same for root and non-root callers
        "IS_SANDBOX": "1",
    }
    full_env.update({
        "SPECSTRIDE_AGENT_STREAM": "false",
        "SPECSTRIDE_RUN_ID": "run-overlay-test",
        "SPECSTRIDE_EVENTS": str(workdir / "events.jsonl"),
    })
    full_env.update(env)
    return subprocess.Popen([
        "bash", str(tree / "proposer.sh"),
        "-w", str(workdir),
        "-e", str(evidence),
        "-f", str(prompt),
        "--backend", backend,
        "-n", str(n),
        "--feature", "overlay-test",
        "--role", "proposer",
        "--phase", "1",
        "--attempt", "1",
        "--invocation-id", "invocation-overlay-test",
    ] + list(extra_args or []), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=full_env)


def one_pass(tmp, backend, tree=None, n=1, timeout=1800):
    """Convenience: fake home + fake binary + one proposer pass.

    Returns (completed, capture_dir, workdir).
    """
    tmp = Path(tmp)
    tree = tree or copy_tree(tmp / "tree")
    workdir = tmp / "work"
    capture = tmp / "capture"
    env = _fake_home_env(tmp)
    env["SPECSTRIDE_TEST_CAPTURE"] = str(capture)
    env["SPECSTRIDE_TEST_N"] = "1"
    env.update(backend_launcher_env(tmp / "bin", backend, tmp))
    completed = run_proposer(tree, workdir, backend, env, n=n, timeout=timeout)
    return completed, capture, workdir


# ── snapshots and normalisation ──────────────────────────────────────────────

def snapshot_tree(path):
    """Sorted list of (relpath, octal mode, sha256) for every file under path."""
    entries = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = Path(root) / name
            rel = str(full.relative_to(path))
            data = full.read_bytes()
            entries.append((rel, oct(full.stat().st_mode & 0o777),
                            hashlib.sha256(data).hexdigest()))
    return sorted(entries)


def list_overlays(tmpdir):
    """Every leftover overlay root (specstride-overlay.*) directly under tmpdir."""
    return sorted(glob.glob(str(Path(tmpdir) / "specstride-overlay.*")))


def normalise_capture(capture_dir, tmp_root, n=1):
    """Read one invocation's raw capture into a normalised, comparable dict.

    Every temp path becomes <TMP>; run ids, pids and timestamps are masked;
    the env is sorted by name.
    """
    capture_dir = Path(capture_dir)
    tmp_root = str(tmp_root)

    def scrub(value):
        value = value.replace(tmp_root, "<TMP>")
        # mktemp suffixes are random per run: specstride-prompt.QFGFMQ etc.
        return re.sub(r"(specstride-[A-Za-z0-9._-]+)\.[A-Za-z0-9]{6}\b",
                      r"\1.XXXXXX", value)

    capture = {}
    raw_env = (capture_dir / ("%d.env" % n)).read_bytes()
    env = {}
    for record in raw_env.split(b"\0"):
        if not record:
            continue
        key, sep, value = record.decode("utf-8", "surrogateescape").partition("=")
        if not sep:
            continue
        if key in VOLATILE_ENV_KEYS:
            env[key] = "<MASKED>"
        else:
            env[key] = scrub(value)
    capture["env"] = dict(sorted(env.items()))
    argv_raw = (capture_dir / ("%d.argv" % n)).read_bytes()
    capture["argv"] = [scrub(a.decode("utf-8", "surrogateescape"))
                       for a in argv_raw.split(b"\0") if a != b""]
    capture["argv_nul_count"] = len(argv_raw.split(b"\0")) - 1
    capture["arg0"] = scrub((capture_dir / ("%d.arg0" % n))
                            .read_bytes().decode("utf-8", "surrogateescape"))
    stdin_raw = (capture_dir / ("%d.stdin" % n)).read_bytes()
    capture["stdin"] = scrub(stdin_raw.decode("utf-8", "surrogateescape"))
    return capture


# ── baseline recording (T004) and its test (T005) ────────────────────────────

def record_baseline(tree, outdir, backends, tmp_root=None):
    """Record the pre-feature launch of each backend into <outdir>/<file>.json.

    The backend name comes from the command line only; each JSON file carries
    its own `backend` field, so the fixture files ARE the backend set.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    results = {}
    for backend in backends:
        with tempfile.TemporaryDirectory(prefix="overlay-rec-", dir=tmp_root) as tmp:
            completed, capture, workdir = one_pass(tmp, backend, tree=tree)
            if completed.returncode != 0:
                raise AssertionError(
                    "recording %s failed rc=%s\n%s\n%s"
                    % (backend, completed.returncode, completed.stdout, completed.stderr))
            if not (workdir / ".specstride" / "gates" / "GATE1-EVIDENCE.md").exists():
                raise AssertionError("recording %s produced no evidence" % backend)
            fixture = {
                "backend": backend,
                "launch": normalise_capture(capture, tmp),
            }
            name = backend.replace(":", "_").replace("/", "_")
            (outdir / (name + ".json")).write_text(
                json.dumps(fixture, indent=2, sort_keys=True) + "\n")
            results[backend] = fixture
    return results


class BaselineTest(unittest.TestCase):
    """Every baseline fixture replays byte-identically against the current tree.

    This is the pre-feature pin: a backend that declares no
    overlay must be launched with exactly the same argv, $0, stdin and env as
    before SH-2. The backend set is the fixture directory itself — never a
    hand-copied list.
    """

    def test_baseline_launches_are_unchanged(self):
        fixtures = sorted(FIXTURE_DIR.glob("*.json"))
        self.assertTrue(fixtures, "no baseline fixtures in %s" % FIXTURE_DIR)
        for fixture_path in fixtures:
            with self.subTest(fixture=fixture_path.name):
                fixture = json.loads(fixture_path.read_text())
                backend = fixture["backend"]
                with tempfile.TemporaryDirectory(prefix="overlay-base-") as tmp:
                    tree = copy_tree(Path(tmp) / "tree")
                    completed, capture, workdir = one_pass(tmp, backend, tree=tree)
                    self.assertEqual(
                        completed.returncode, 0,
                        "replay of %s failed:\n%s\n%s"
                        % (backend, completed.stdout, completed.stderr))
                    observed = normalise_capture(capture, tmp)
                self.assertEqual(observed, fixture["launch"],
                                 "launch of %s changed vs the recorded baseline"
                                 % backend)


# ── declaration loader (the module's validation layer) ───────────────────────

VALID_ENTRY = {
    "vars": ("HOME", "XDG_CONFIG_HOME"),
    "seeds": ({"var": "HOME", "path": ".tool/settings.json",
               "content": '{"mode": "default"}\n'},),
}


class _DeclarationTableCase(unittest.TestCase):
    """Shared plumbing: patch the derived table and reset the loader cache."""

    def use_table(self, mapping):
        patcher = mock.patch.dict(backend_overlay.DECLARATIONS, mapping,
                                  clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        backend_overlay._DECLARATIONS_CACHE = None
        self.addCleanup(self._clear_cache)

    @staticmethod
    def _clear_cache():
        backend_overlay._DECLARATIONS_CACHE = None


class DeclarationLoaderTest(_DeclarationTableCase):
    """The declaration table loads, validates and caches as one unit.

    Every rule the module enforces is checked here against its exact error
    text, because that text is what an operator sees when a run is stopped
    before pass 1 — a paraphrase would hide which rule fired.
    """

    def test_valid_entry_loads_frozen(self):
        self.use_table({"acme": VALID_ENTRY})
        table = backend_overlay.load_declarations()
        decl = table["acme"]
        self.assertEqual(decl.key, "acme")
        self.assertEqual(decl.vars, ("HOME", "XDG_CONFIG_HOME"))
        self.assertEqual(decl.seeds, (backend_overlay.Seed(
            "HOME", ".tool/settings.json", '{"mode": "default"}\n'),))
        self.assertEqual(decl.unset, ("XDG_DATA_HOME", "XDG_CACHE_HOME",
                                      "XDG_STATE_HOME"))
        with self.assertRaises(FrozenInstanceError):
            decl.key = "other"

    def test_load_is_cached(self):
        self.use_table({"acme": VALID_ENTRY})
        first = backend_overlay.load_declarations()
        backend_overlay.DECLARATIONS.clear()  # a later change must not be re-read
        self.assertIs(backend_overlay.load_declarations(), first)

    def test_bad_key(self):
        for key in ("", "Acme", "1acme", "a-b", "a:b", "a_b"):
            with self.subTest(key=key):
                self.use_table({key: VALID_ENTRY})
                with self.assertRaisesRegex(
                        backend_overlay.DeclarationError,
                        "^overlay declaration '%s': key must be a registry "
                        "name without qualifier$" % re.escape(key)):
                    backend_overlay.load_declarations()

    def test_dsh_key_rejected(self):
        self.use_table({"dsh": VALID_ENTRY})
        with self.assertRaisesRegex(
                backend_overlay.DeclarationError,
                "^overlay declaration 'dsh': dsh keeps its DSH_HOME overlay "
                "and cannot declare one$"):
            backend_overlay.load_declarations()

    def test_unknown_field(self):
        self.use_table({"acme": dict(VALID_ENTRY, extra=1)})
        with self.assertRaisesRegex(
                backend_overlay.DeclarationError,
                "^overlay declaration 'acme': unknown field 'extra'$"):
            backend_overlay.load_declarations()

    def test_missing_vars(self):
        self.use_table({"acme": {"seeds": ()}})
        with self.assertRaisesRegex(
                backend_overlay.DeclarationError,
                "^overlay declaration 'acme': missing 'vars'$"):
            backend_overlay.load_declarations()

    def test_var_rules(self):
        cases = [("empty", (), "bad variable ''"),
                 ("not-a-tuple", "HOME", "bad variable ''"),
                 ("lowercase", ("home",), "bad variable 'home'"),
                 ("digit-led", ("1HOME",), "bad variable '1HOME'"),
                 ("hyphen", ("X-HOME",), "bad variable 'X-HOME'"),
                 ("duplicate", ("HOME", "HOME"), "duplicate variable 'HOME'")]
        for label, variables, message in cases:
            with self.subTest(case=label):
                self.use_table({"acme": {"vars": variables}})
                with self.assertRaisesRegex(backend_overlay.DeclarationError,
                                            re.escape(message) + r"$"):
                    backend_overlay.load_declarations()

    def test_reserved_vars(self):
        reserved = ("PATH", "PWD", "SHELL", "GIT_AUTHOR_NAME",
                    "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                    "GIT_COMMITTER_EMAIL", "SPECSTRIDE_FOO")
        for name in reserved:
            with self.subTest(var=name):
                self.use_table({"acme": {"vars": (name,)}})
                with self.assertRaisesRegex(
                        backend_overlay.DeclarationError,
                        "^overlay declaration 'acme': variable '%s' is "
                        "reserved$" % re.escape(name)):
                    backend_overlay.load_declarations()

    def test_seed_wrong_keys(self):
        for label, seeds in (("missing-content",
                              ({"var": "HOME", "path": "a"},)),
                             ("missing-path",
                              ({"var": "HOME", "content": "x"},)),
                             ("extra-key",
                              ({"var": "HOME", "path": "a", "content": "x",
                                "mode": "w"},))):
            with self.subTest(case=label):
                self.use_table({"acme": {"vars": ("HOME",), "seeds": seeds}})
                with self.assertRaisesRegex(
                        backend_overlay.DeclarationError,
                        "^overlay declaration 'acme': seed #0 must have var, "
                        r"path, content$"):
                    backend_overlay.load_declarations()

    def test_seed_undeclared_var(self):
        self.use_table({"acme": {"vars": ("HOME",),
                                 "seeds": ({"var": "XDG_DATA_HOME",
                                            "path": "a",
                                            "content": "x"},)}})
        with self.assertRaisesRegex(
                backend_overlay.DeclarationError,
                "^overlay declaration 'acme': seed 'XDG_DATA_HOME:a' names "
                "undeclared variable 'XDG_DATA_HOME'$"):
            backend_overlay.load_declarations()

    def test_seed_bad_paths(self):
        bad = ("/abs/a", "../a", "./a", "a//b", "a/\x00b", "")
        for path in bad:
            with self.subTest(path=path):
                self.use_table({"acme": {"vars": ("HOME",),
                                         "seeds": ({"var": "HOME",
                                                    "path": path,
                                                    "content": "x"},)}})
                with self.assertRaisesRegex(
                        backend_overlay.DeclarationError,
                        "^overlay declaration 'acme': seed 'HOME:%s' path "
                        "must be relative without '[.][.]'$"
                        % re.escape(path)):
                    backend_overlay.load_declarations()

    def test_duplicate_seed(self):
        self.use_table({"acme": {"vars": ("HOME",),
                                 "seeds": ({"var": "HOME", "path": "a",
                                            "content": "x"},
                                           {"var": "HOME", "path": "a",
                                            "content": "y"},)}})
        with self.assertRaisesRegex(
                backend_overlay.DeclarationError,
                "^overlay declaration 'acme': duplicate seed 'HOME:a'$"):
            backend_overlay.load_declarations()

    def test_seed_credential_content(self):
        for content in ("api_key=x", "token: x", "API-KEY=1", "password: y"):
            with self.subTest(content=content):
                self.use_table({"acme": {"vars": ("HOME",),
                                         "seeds": ({"var": "HOME",
                                                    "path": "a",
                                                    "content": content},)}})
                with self.assertRaisesRegex(
                        backend_overlay.DeclarationError,
                        "^overlay declaration 'acme': seed 'HOME:a' content "
                        "looks like a credential; seeds must not carry "
                        "secrets$"):
                    backend_overlay.load_declarations()

    def test_seed_innocuous_content_loads(self):
        for content in ('{"maxTokens": 4}', "tokenizer=x"):
            with self.subTest(content=content):
                self.use_table({"acme": {"vars": ("HOME",),
                                         "seeds": ({"var": "HOME",
                                                    "path": "a",
                                                    "content": content},)}})
                decl = backend_overlay.load_declarations()["acme"]
                self.assertEqual(decl.seeds[0].content, content)

    def test_one_bad_entry_fails_the_whole_table(self):
        self.use_table({"good": VALID_ENTRY,
                        "bad": {"vars": ("reserved-now",)}})
        with self.assertRaises(backend_overlay.DeclarationError):
            backend_overlay.load_declarations()
        # the failure is not cached as success: every later call fails too
        with self.assertRaises(backend_overlay.DeclarationError):
            backend_overlay.load_declarations()
        with self.assertRaises(backend_overlay.DeclarationError):
            backend_overlay.declaration_for("good")

    def test_declaration_for_uses_the_key_without_qualifier(self):
        self.use_table({"prime": VALID_ENTRY})
        decl = backend_overlay.declaration_for("prime:qwen")
        self.assertIsNotNone(decl)
        self.assertEqual(decl.key, "prime")
        self.assertIsNone(backend_overlay.declaration_for("unknown"))

    def test_declaration_for_empty_table(self):
        self.use_table({})
        self.assertIsNone(backend_overlay.declaration_for("prime:qwen"))

    def test_derived_unset(self):
        xdg = ("XDG_CONFIG_HOME", "XDG_DATA_HOME",
               "XDG_CACHE_HOME", "XDG_STATE_HOME")
        cases = [(("HOME",), xdg),
                 (("HOME", "XDG_CONFIG_HOME"),
                  ("XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")),
                 (("XDG_CONFIG_HOME",), ()),
                 (("TMPDIR",), ()),
                 (("HOME",) + xdg, ())]
        for variables, unset in cases:
            with self.subTest(vars=variables):
                decl = backend_overlay.Declaration(key="acme", vars=variables)
                self.assertEqual(decl.unset, unset)
                self.assertNotIn("GIT_CONFIG_GLOBAL", decl.unset)

    def test_no_git_identity_field(self):
        self.assertNotIn("git_identity",
                         backend_overlay.Declaration.__dataclass_fields__)


# ── the scope setting (pass / attempt / none) ────────────────────────────────

def _clean_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SPECSTRIDE_")}
    return env


class ScopeTest(_DeclarationTableCase):
    """The per-backend scope variable parses, validates and warns.

    The variable is validated for EVERY backend — a bogus value is an error
    even where it would have no effect — and a backend that declares no
    overlay resolves to `none` with exactly one warning.
    """

    def test_scope_var_spellings(self):
        self.assertEqual(backend_overlay.scope_var("opencode:p/m"),
                         "SPECSTRIDE_OPENCODE_OVERLAY")
        self.assertEqual(backend_overlay.scope_var("prime:qwen"),
                         "SPECSTRIDE_PRIME_OVERLAY")
        self.assertEqual(backend_overlay.scope_var("dsh:p/m"),
                         "SPECSTRIDE_DSH_OVERLAY")

    def test_scope_var_non_identifier_key(self):
        self.assertIsNone(backend_overlay.scope_var("1bad"))
        self.assertIsNone(backend_overlay.scope_var("a-b:x"))

    def test_no_declaration_is_none_for_any_valid_value(self):
        self.use_table({})
        for raw in (None, "", "pass", "attempt"):
            env = {} if raw is None else {"SPECSTRIDE_CLAUDE_OVERLAY": raw}
            with self.subTest(value=raw):
                self.assertEqual(backend_overlay.scope_for("claude", env),
                                 "none")

    def test_declared_backend_resolves_pass_and_attempt(self):
        self.use_table({"codex": VALID_ENTRY})
        cases = [{}, {"SPECSTRIDE_CODEX_OVERLAY": ""},
                 {"SPECSTRIDE_CODEX_OVERLAY": "pass"}]
        for env in cases:
            with self.subTest(env=env):
                self.assertEqual(backend_overlay.scope_for("codex", env),
                                 "pass")
        self.assertEqual(backend_overlay.scope_for(
            "codex", {"SPECSTRIDE_CODEX_OVERLAY": "attempt"}), "attempt")

    def test_invalid_value_raises_with_exact_message(self):
        message = ("SPECSTRIDE_CODEX_OVERLAY='bogus' is invalid; "
                   "accepted values: pass, attempt")
        for label, mapping in (("with-declaration", {"codex": VALID_ENTRY}),
                               ("without-declaration", {})):
            with self.subTest(case=label):
                self.use_table(mapping)
                with self.assertRaisesRegex(
                        backend_overlay.ScopeError, "^%s$" % re.escape(message)):
                    backend_overlay.scope_for(
                        "codex", {"SPECSTRIDE_CODEX_OVERLAY": "bogus"})

    def test_warning_only_for_explicit_value_without_declaration(self):
        warning = ("specstride: warning: SPECSTRIDE_DSH_OVERLAY is set but "
                   "backend 'dsh' has no overlay; ignoring it")
        cases = [({"SPECSTRIDE_DSH_OVERLAY": "attempt"}, warning),
                 ({"SPECSTRIDE_DSH_OVERLAY": "pass"}, warning),
                 ({}, None),
                 ({"SPECSTRIDE_DSH_OVERLAY": ""}, None),
                 ({"SPECSTRIDE_DSH_OVERLAY": "bogus"}, None)]
        for env, expected in cases:
            with self.subTest(env=env):
                self.assertEqual(backend_overlay.scope_warning("dsh:p/m", env),
                                 expected)

    def test_no_warning_with_declaration(self):
        self.use_table({"codex": VALID_ENTRY})
        for raw in ("pass", "attempt"):
            with self.subTest(value=raw):
                self.assertIsNone(backend_overlay.scope_warning(
                    "codex", {"SPECSTRIDE_CODEX_OVERLAY": raw}))

    # ── the CLI the proposer shell reaches ───────────────────────────────

    MODULE = str(Path(__file__).parent / "backend_overlay.py")

    def run_cli(self, *argv, tree=None, extra_env=None):
        env = _clean_env()
        env.update(extra_env or {})
        module = str((Path(tree) / "lib" / "backend_overlay.py") if tree
                     else self.MODULE)
        return subprocess.run(["python3", module, *argv],
                              capture_output=True, text=True, env=env,
                              timeout=120)

    def test_cli_startup_prints_none(self):
        done = self.run_cli("startup", "claude")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "none\n")
        self.assertEqual(done.stderr, "")

    def test_cli_invalid_scope_exits_3_with_message(self):
        done = self.run_cli("startup", "claude",
                            extra_env={"SPECSTRIDE_CLAUDE_OVERLAY": "bogus"})
        self.assertEqual(done.returncode, 3)
        self.assertIn("SPECSTRIDE_CLAUDE_OVERLAY='bogus' is invalid; "
                      "accepted values: pass, attempt", done.stderr)
        self.assertEqual(done.stdout, "")

    def test_cli_scope_quiet_suppresses_the_warning(self):
        loud = self.run_cli("scope", "claude",
                            extra_env={"SPECSTRIDE_CLAUDE_OVERLAY": "attempt"})
        self.assertEqual(loud.returncode, 0)
        self.assertIn("has no overlay; ignoring it", loud.stderr)
        quiet = self.run_cli("scope", "claude", "--quiet",
                             extra_env={"SPECSTRIDE_CLAUDE_OVERLAY": "attempt"})
        self.assertEqual(quiet.returncode, 0)
        self.assertEqual(quiet.stderr, "")
        self.assertEqual(quiet.stdout, "none\n")

    def test_cli_startup_without_the_registry(self):
        with tempfile.TemporaryDirectory(prefix="overlay-noreg-") as tmp:
            tree = copy_tree(Path(tmp) / "tree")
            (tree / "lib" / "backends.py").unlink()
            done = self.run_cli("startup", "claude", tree=tree)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout, "none\n")
            self.assertNotIn("Traceback", done.stderr)


# ── US1: create / apply / remove overlay trees (in-process unit tests) ───────

UNIT_DECL = {
    "vars": ("HOME", "XDG_CONFIG_HOME", "TMPDIR"),
    "seeds": ({"var": "HOME", "path": ".tool/settings.json",
               "content": '{"mode": "overlay-test"}\n'},),
}


class OverlayUnitTest(_DeclarationTableCase):
    """populate / harness_env / remove_overlay, in-process.

    A private tmp_root per test; the declaration table is patched, so no
    registry entry needs an overlay for these to run.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="overlay-unit-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "root"
        self.root.mkdir(mode=0o700)
        self.use_table({"codex": UNIT_DECL})

    def populate(self, root=None, backend="codex", scope="pass"):
        # the operator has exported all four identity variables, so the US2
        # identity step resolves nothing and the US1 record list stays exact
        env = dict(os.environ)
        env.update({"GIT_AUTHOR_NAME": "Op", "GIT_AUTHOR_EMAIL": "op@x",
                    "GIT_COMMITTER_NAME": "Op", "GIT_COMMITTER_EMAIL": "op@x"})
        backend_overlay.populate(root or self.root, backend, scope, os.getpid(),
                                 str(self.tmp), env)
        return root or self.root

    def records(self, root):
        return backend_overlay._read_env_file(root)

    def test_populate_layout_marker_first_env_file_no_gitconfig(self):
        import time as _time
        before = _time.time()
        self.populate()
        after = _time.time()
        marker_path = self.root / "specstride-overlay.json"
        marker = json.loads(marker_path.read_text())
        self.assertEqual(marker["contract"], "specstride-overlay/v1")
        self.assertEqual(marker["backend"], "codex")
        self.assertEqual(marker["scope"], "pass")
        self.assertEqual(marker["owner_pid"], os.getpid())
        self.assertIsInstance(marker["owner_start"], int)
        self.assertTrue(before <= marker["created_at"] <= after)
        self.assertEqual(marker["vars"], ["HOME", "XDG_CONFIG_HOME", "TMPDIR"])
        # marker written FIRST: it is at least as old as every var directory
        marker_mtime = marker_path.stat().st_mtime
        for var in ("HOME", "XDG_CONFIG_HOME", "TMPDIR"):
            var_dir = self.root / var
            self.assertTrue(var_dir.is_dir())
            self.assertEqual(stat.S_IMODE(var_dir.stat().st_mode), 0o700)
            self.assertLessEqual(marker_mtime, var_dir.stat().st_mtime)
        seed = self.root / "HOME" / ".tool" / "settings.json"
        self.assertEqual(stat.S_IMODE(seed.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "HOME" / ".tool").stat().st_mode),
                         0o700)
        self.assertEqual(seed.read_text(), '{"mode": "overlay-test"}\n')
        # env records: every unset BEFORE every set, sets per var + gitconfig;
        # no overlay gitconfig file is created (identity records: US2)
        records = self.records(self.root)
        self.assertEqual([r[1] for r in records if r[0] == "unset"],
                         ["XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"])
        kinds_in_order = [r[0] for r in records]
        self.assertEqual(kinds_in_order[:3], ["unset"] * 3)
        self.assertNotIn("unset", kinds_in_order[3:])
        sets = [(r[1], r[2]) for r in records if r[0] == "set"]
        self.assertEqual(sets, [
            ("HOME", str(self.root / "HOME")),
            ("XDG_CONFIG_HOME", str(self.root / "XDG_CONFIG_HOME")),
            ("TMPDIR", str(self.root / "TMPDIR")),
            ("GIT_CONFIG_GLOBAL", str(self.root / "specstride-overlay.gitconfig")),
        ])
        self.assertFalse((self.root / "specstride-overlay.gitconfig").exists())

    def test_seed_through_planted_symlink_cannot_escape(self):
        outside = self.tmp / "outside"
        (outside / "secret").mkdir(parents=True)
        (outside / "secret" / "x").write_text("do not touch\n")
        (self.root / "HOME").mkdir()
        (self.root / "HOME" / ".tool").mkdir()
        (self.root / "HOME" / ".tool" / "settings.json").symlink_to(
            outside / "secret" / "x")
        orig_mkdir = os.mkdir

        def tolerant_mkdir(path, mode=0o777):
            try:
                orig_mkdir(path, mode)
            except FileExistsError:
                if not os.path.isdir(path):
                    raise

        with mock.patch("os.mkdir", tolerant_mkdir):
            with self.assertRaisesRegex(backend_overlay.OverlayError,
                                        "^seed 'HOME:"):
                self.populate()

    def test_harness_env_unsets_sets_and_passes_the_rest_through(self):
        self.populate()
        base = {"PATH": "/real/bin", "ACME_API_KEY": "sk-live-1",
                "SPECSTRIDE_RUN_ID": "run-1", "HOME": "/real/home",
                "XDG_DATA_HOME": "/real/xdg", "TMPDIR": "/real/tmp",
                "XDG_CACHE_HOME": "/real/cache"}
        env = backend_overlay.harness_env(self.root, base)
        self.assertEqual(env["HOME"], str(self.root / "HOME"))
        self.assertEqual(env["TMPDIR"], str(self.root / "TMPDIR"))
        self.assertEqual(env["GIT_CONFIG_GLOBAL"],
                         str(self.root / "specstride-overlay.gitconfig"))
        for dropped in ("XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
            self.assertNotIn(dropped, env)
        self.assertEqual(env["PATH"], "/real/bin")
        self.assertEqual(env["ACME_API_KEY"], "sk-live-1")
        self.assertEqual(env["SPECSTRIDE_RUN_ID"], "run-1")

    def test_remove_overlay_read_only_tree(self):
        target = self.root / "HOME" / "ro"
        target.mkdir(parents=True)
        (target / "f").write_text("x")
        os.chmod(target, 0o500)
        os.chmod(target / "f", 0o400)
        self.assertTrue(backend_overlay.remove_overlay(self.root))
        self.assertFalse(self.root.exists())

    def test_remove_overlay_failure_prints_one_exact_line_and_returns_false(self):
        (self.root / "HOME").mkdir()
        with mock.patch.object(backend_overlay.shutil, "rmtree",
                               side_effect=OSError("boom: device busy")):
            sink = io.StringIO()
            self.assertFalse(
                backend_overlay.remove_overlay(self.root, warn=sink))
        self.assertEqual(
            sink.getvalue(),
            "specstride: warning: could not remove overlay %s: "
            "boom: device busy\n" % self.root)

    def test_remove_overlay_unlinks_symlink_and_spares_the_target(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "keep").write_text("kept\n")
        (self.root / "HOME").mkdir()
        (self.root / "HOME" / "x").symlink_to(outside)
        self.assertTrue(backend_overlay.remove_overlay(self.root))
        self.assertFalse(self.root.exists())
        self.assertTrue((outside / "keep").exists())
        self.assertEqual((outside / "keep").read_text(), "kept\n")


# ── US1: owner identity, reap and sweep ──────────────────────────────────────

def _write_overlay_dir(tmp, name, owner_pid, owner_start, scope="pass",
                       contract="specstride-overlay/v1", marker=True,
                       marker_body=None):
    path = Path(tmp) / name
    path.mkdir()
    if marker:
        body = marker_body if marker_body is not None else json.dumps({
            "contract": contract, "backend": "codex", "scope": scope,
            "owner_pid": owner_pid, "owner_start": owner_start,
            "created_at": 0, "vars": ["HOME"]})
        (path / "specstride-overlay.json").write_text(body)
    return path


def _dead_child_pid():
    """Spawn and reap a short-lived child; its PID no longer exists."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


class OwnerAndSweepTest(unittest.TestCase):
    """Owner start times, liveness, reap by owner and the stale sweep."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="overlay-sweep-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_proc_start_time_is_an_int_for_a_live_pid(self):
        self.assertIsInstance(backend_overlay._proc_start_time(os.getpid()), int)
        self.assertIsNone(backend_overlay._proc_start_time(999999999))

    def test_owner_is_live_cases(self):
        real_start = backend_overlay._proc_start_time(os.getpid())
        dead = _dead_child_pid()
        self.assertTrue(dead)
        cases = [
            ("current-real-start", os.getpid(), real_start, True),
            ("dead-pid", dead, 12345, False),
            ("pid-reuse-mismatch", os.getpid(), real_start + 1, False),
            ("live-null-start", os.getpid(), None, True),
        ]
        for label, pid, start, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(backend_overlay.owner_is_live(pid, start),
                                 expected)
        with mock.patch.object(backend_overlay, "_proc_start_time",
                               return_value=None):
            self.assertTrue(backend_overlay.owner_is_live(os.getpid(), 7))

    def test_reap_removes_only_that_owner_and_scope(self):
        mine_pass = _write_overlay_dir(self.tmp, "specstride-overlay.a.aaa",
                                       5000, 1, scope="pass")
        mine_attempt = _write_overlay_dir(self.tmp, "specstride-overlay.b.bbb",
                                          5000, 1, scope="attempt")
        other = _write_overlay_dir(self.tmp, "specstride-overlay.c.ccc",
                                   6000, 1, scope="pass")
        self.assertEqual(
            backend_overlay.reap(5000, scope="pass", tmp_root=str(self.tmp)), 1)
        self.assertFalse(mine_pass.exists())
        self.assertTrue(mine_attempt.exists())
        self.assertTrue(other.exists())
        self.assertEqual(
            backend_overlay.reap(5000, tmp_root=str(self.tmp)), 1)
        self.assertFalse(mine_attempt.exists())
        self.assertEqual(backend_overlay.reap(6000, tmp_root=str(self.tmp)), 1)
        self.assertFalse(other.exists())

    def test_sweep_removes_dead_owners_and_leaves_everything_else(self):
        dead = _dead_child_pid()
        dead_dir = _write_overlay_dir(self.tmp, "specstride-overlay.d.ddd",
                                      dead, 12345)
        reuse_dir = _write_overlay_dir(self.tmp, "specstride-overlay.e.eee",
                                       os.getpid(),
                                       backend_overlay._proc_start_time(os.getpid()) + 99)
        live_dir = _write_overlay_dir(self.tmp, "specstride-overlay.f.fff",
                                      os.getpid(),
                                      backend_overlay._proc_start_time(os.getpid()))
        no_marker = _write_overlay_dir(self.tmp, "specstride-overlay.g.ggg",
                                       dead, 1, marker=False)
        foreign_contract = _write_overlay_dir(self.tmp, "specstride-overlay.h.hhh",
                                              dead, 1,
                                              contract="someone-else/v9")
        unparseable = _write_overlay_dir(self.tmp, "specstride-overlay.i.iii",
                                         dead, 1, marker_body="{not json")
        dsh_home = _write_overlay_dir(self.tmp, "specstride-dsh-home.jjj",
                                      dead, 1)
        target = self.tmp / "symlink-target"
        target.mkdir()
        link = self.tmp / "specstride-overlay.k.kkk"
        link.symlink_to(target, target_is_directory=True)
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            removed = backend_overlay.sweep(tmp_root=str(self.tmp))
        self.assertEqual(removed, 2)
        self.assertFalse(dead_dir.exists())
        self.assertFalse(reuse_dir.exists())
        lines = sink.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(sorted(lines), sorted([
            "specstride: removed stale overlay %s (owner %d gone)"
            % (dead_dir, dead),
            "specstride: removed stale overlay %s (owner %d gone)"
            % (reuse_dir, os.getpid())]))
        for kept in (live_dir, no_marker, foreign_contract, unparseable,
                     dsh_home, link):
            self.assertTrue(kept.exists(), kept)


# ── US1: end-to-end proposer passes against a temp-tree copy ─────────────────

FULL_VARS = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
             "XDG_STATE_HOME", "TMPDIR")

PRE_HOMES_LOG = r'''printf '%s %s\n' "$HOME" "$([[ -e "$HOME/.probe" ]] && echo pre:probe || echo pre:clean)" >> "$cap/homes.log"
'''

PROBE_LISTING = r""": > "$cap/$n.probes"
for d in "$HOME" "${XDG_CONFIG_HOME:-}" "${XDG_DATA_HOME:-}" "${XDG_CACHE_HOME:-}" "${XDG_STATE_HOME:-}" "${TMPDIR:-}"; do
  [[ -n "$d" ]] && find "$d" >> "$cap/$n.probes" 2>/dev/null
done
"""

HARNESS_PID_RECORD = r'''printf '%s\n' "$$" > "$cap/harness.pid"
'''


class _ProposerOverlayCase(unittest.TestCase):
    """Shared end-to-end plumbing: a declared codex in a temp-tree copy."""

    maxDiff = None

    def make_case(self, tmp, decl, body_extra="", body_pre="", extra_env=None):
        tmp = Path(tmp)
        tree = copy_tree(tmp / "tree")
        if decl is not None:
            set_declarations(tree, {"codex": decl})
        workdir = tmp / "work"
        capture = tmp / "capture"
        env = _fake_home_env(tmp)
        env["SPECSTRIDE_TEST_CAPTURE"] = str(capture)
        env["SPECSTRIDE_TEST_N"] = "1"
        bin_dir = tmp / "bin"
        bin_dir.mkdir(exist_ok=True)
        body = RECORDER_BODY
        if body_pre:
            # recorded BEFORE the recorder creates its own probes, so a
            # "pre:probe" line means the probe already existed (carryover)
            body = body.replace("# probe every config-home path",
                                body_pre + "# probe every config-home path")
        write_fake_bin(bin_dir, "codex",
                       body.replace("exit 0", body_extra + "\nexit 0"))
        env["PATH"] = str(bin_dir) + os.pathsep + BASE_PATH
        env.update(extra_env or {})
        return tree, workdir, capture, env

    @staticmethod
    def read_env(capture, n=1):
        raw = (Path(capture) / ("%d.env" % n)).read_bytes()
        env = {}
        for record in raw.split(b"\0"):
            if record:
                key, _, value = record.partition(b"=")
                env[key.decode()] = value.decode("utf-8", "surrogateescape")
        return env

    @staticmethod
    def wait_until(predicate, timeout=30, interval=0.2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()


class ProposerPassScopeTest(_ProposerOverlayCase):
    """Pass-scope end to end: fresh overlay, probes inside, nothing left."""

    FULL_DECL = {"vars": FULL_VARS,
                 "seeds": ({"var": "HOME", "path": ".tool/settings.json",
                            "content": '{"mode": "pass-scope"}\n'},)}

    def test_exit0_pass_fresh_overlay_probes_inside_nothing_left(self):
        with tempfile.TemporaryDirectory(prefix="overlay-e2e-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=PROBE_LISTING, body_pre=PRE_HOMES_LOG)
            home = Path(tmp) / "home"
            before = {d: snapshot_tree(d) for d in
                      (home, Path(tmp) / "xdg", Path(tmp) / "tmp")}
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            harness_env_d = self.read_env(capture)
            home_seen = harness_env_d["HOME"]
            self.assertIn("specstride-overlay.codex.", home_seen)
            root = os.path.dirname(home_seen)
            self.assertEqual(os.path.dirname(root), str(Path(tmp) / "tmp"))
            for var in FULL_VARS:
                self.assertEqual(harness_env_d[var], os.path.join(root, var))
            probes = (Path(capture) / "1.probes").read_text()
            self.assertIn(os.path.join(root, "HOME", ".probe"), probes)
            self.assertIn(os.path.join(root, "TMPDIR"), probes)
            self.assertIn(os.path.join(root, "HOME", ".tool", "settings.json"),
                          probes)
            self.assertEqual(
                (Path(capture) / "homes.log").read_text().strip().split(" ")[-1],
                "pre:clean")
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [], "overlay root left behind")
            for d, snap in before.items():
                self.assertEqual(snapshot_tree(d), snap,
                                 "real directory changed: %s" % d)

    def test_tmpdir_probe_lands_inside_not_in_the_real_tmp(self):
        with tempfile.TemporaryDirectory(prefix="overlay-e2e-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=PROBE_LISTING, body_pre=PRE_HOMES_LOG)
            real_tmp = Path(tmp) / "tmp"
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            probes = (Path(capture) / "1.probes").read_text()
            self.assertRegex(
                probes, r"specstride-overlay\.codex\.[A-Za-z0-9]{6}/TMPDIR/probe")
            self.assertEqual([p for p in list_overlays(real_tmp)
                              if "probe" in p], [])
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    def test_exit1_pass_leaves_nothing_and_counts_like_any_failed_pass(self):
        fail_body = r'''rm -f .specstride/gates/GATE1-EVIDENCE.md
exit 1
'''
        outcomes = {}
        for label, decl in (("declared", self.FULL_DECL), ("undeclared", None)):
            with tempfile.TemporaryDirectory(prefix="overlay-e2e-") as tmp:
                tree, workdir, capture, env = self.make_case(
                    tmp, decl, body_extra=fail_body)
                done = run_proposer(tree, workdir, "codex", env)
                outcomes[label] = (done.returncode,
                                   self._normalise(done.stderr, tmp))
                self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])
        self.assertEqual(outcomes["declared"][0], outcomes["undeclared"][0])
        self.assertNotEqual(outcomes["declared"][0], 0)

    @staticmethod
    def _normalise(stderr, tmp):
        """Mask timestamps and the run's temp path for a like-for-like diff."""
        stderr = re.sub(r"\d{4}-\d\d-\d\dT[\d:+-]+", "<TS>", stderr)
        return stderr.replace(str(tmp), "<T>")

    def test_idle_watchdog_kill_removes_the_root(self):
        sleep_body = "sleep 120\n"
        with tempfile.TemporaryDirectory(prefix="overlay-e2e-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=sleep_body)
            env["SPECSTRIDE_WATCHDOG_TICK"] = "1"
            done = run_proposer(tree, workdir, "codex", env,
                                extra_args=["--idle-timeout", "2",
                                            "--timeout", "60"], timeout=300)
            self.assertIn("terminated by the watchdog", done.stderr)
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [],
                             "watchdog kill left the overlay behind")

    NO_EVIDENCE_FIRST_PASS = r'''# pass 1 writes no evidence (so the -n 2 loop really runs twice);
# pass 2 writes it, which is how a proposer pass completes
if [[ -f "$cap/.pass1done" ]]; then
  mkdir -p .specstride/gates
  echo ok > .specstride/gates/GATE1-EVIDENCE.md
else
  rm -f .specstride/gates/GATE1-EVIDENCE.md
  touch "$cap/.pass1done"
fi
'''

    def test_two_passes_get_two_roots_and_no_carryover(self):
        with tempfile.TemporaryDirectory(prefix="overlay-e2e-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL,
                body_extra=PROBE_LISTING + self.NO_EVIDENCE_FIRST_PASS,
                body_pre=PRE_HOMES_LOG)
            done = run_proposer(tree, workdir, "codex", env, n=2)
            self.assertEqual(done.returncode, 0, done.stderr)
            homes = [l.split(" ")[0]
                     for l in (Path(capture) / "homes.log").read_text().splitlines()]
            self.assertEqual(len(homes), 2)
            self.assertNotEqual(homes[0], homes[1])
            for seen in homes:
                self.assertIn("specstride-overlay.codex.", seen)
                self.assertTrue(seen.endswith("/HOME"))
            self.assertEqual(
                (Path(capture) / "homes.log").read_text().splitlines()[1].split(" ")[-1],
                "pre:clean",
                "pass 2 saw pass 1's $HOME/.probe")
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    def test_seed_content_reaches_the_harness_home(self):
        cat_seed = r'''cat "$HOME/.tool/settings.json" > "$cap/$n.seed" 2>/dev/null
'''
        with tempfile.TemporaryDirectory(prefix="overlay-e2e-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=cat_seed)
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual((Path(capture) / "1.seed").read_text(),
                             '{"mode": "pass-scope"}\n')

    def test_home_only_declaration_unsets_operator_xdg(self):
        decl = {"vars": ("HOME",)}
        real_xdg = None
        with tempfile.TemporaryDirectory(prefix="overlay-e2e-") as tmp:
            real_xdg = Path(tmp) / "xdg" / "config"
            real_xdg.mkdir(parents=True)
            tree, workdir, capture, env = self.make_case(
                tmp, decl, body_extra=PROBE_LISTING, body_pre=PRE_HOMES_LOG,
                extra_env={"XDG_CONFIG_HOME": str(real_xdg / "imported")})
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            seen = self.read_env(capture)
            self.assertNotIn("XDG_CONFIG_HOME", seen,
                             "operator XDG_CONFIG_HOME reached the harness")
            home_seen = seen["HOME"]
            self.assertIn("specstride-overlay.codex.", home_seen)
            probes = (Path(capture) / "1.probes").read_text()
            self.assertIn(os.path.join(home_seen, ".config", "probe"), probes)
            self.assertEqual(snapshot_tree(real_xdg), [],
                             "the real XDG directory was changed")
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])


class ProposerIsolationTest(_ProposerOverlayCase):
    """The overlay env reaches only the harness; every exit path cleans up."""

    FULL_DECL = ProposerPassScopeTest.FULL_DECL

    ANCESTOR_WALK = r'''# isolation: copy every ancestor's environ (parent chain up to the test proc)
target="${SPECSTRIDE_TEST_PID:?}"
p="$PPID"
: > "$cap/ancestors"
while [[ -n "$p" && "$p" != "$target" && "$p" != "1" ]]; do
  cat "/proc/$p/environ" > "$cap/anc.$p.env" 2>/dev/null
  echo "$p" >> "$cap/ancestors"
  p="$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null)"
done
# and the stream tap: whoever reads our stdout (pipe inode on its fd 0)
ino="$(stat -Lc %i /proc/self/fd/1 2>/dev/null)"
: > "$cap/tap"
if [[ -n "$ino" ]]; then
  for d in /proc/[0-9]*; do
    [[ "$(stat -Lc %i "$d/fd/0" 2>/dev/null)" == "$ino" ]] && echo "${d#/proc/}" >> "$cap/tap"
  done
  while read -r tp; do
    cat "/proc/$tp/environ" > "$cap/tap.$tp.env" 2>/dev/null
  done < "$cap/tap"
fi
'''

    def assert_no_overlay_in(self, env_bytes, label):
        self.assertNotIn(b"specstride-overlay", env_bytes, label)

    def test_no_specstride_process_sees_the_overlay_env(self):
        with tempfile.TemporaryDirectory(prefix="overlay-iso-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=self.ANCESTOR_WALK,
                extra_env={"SPECSTRIDE_TEST_PID": str(os.getpid())})
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            ancestors = (Path(capture) / "ancestors").read_text().split()
            self.assertTrue(ancestors, "no ancestor environ captured")
            for pid in ancestors:
                raw = (Path(capture) / ("anc.%s.env" % pid)).read_bytes()
                self.assert_no_overlay_in(raw, "ancestor %s" % pid)
            taps = (Path(capture) / "tap").read_text().split()
            for pid in taps:
                raw = (Path(capture) / ("tap.%s.env" % pid)).read_bytes()
                self.assert_no_overlay_in(raw, "stream tap %s" % pid)
            # control: the HARNESS env does carry the overlay (the walk above
            # would prove nothing if the harness env were empty of it)
            harness = self.read_env(capture)
            self.assertIn("specstride-overlay.codex.", harness["HOME"])

    def test_binary_under_home_path_and_literal_tilde_entry(self):
        body = RECORDER_BODY  # records $0 into <n>.arg0
        for label, tilde in (("absolute", False), ("literal-tilde", True)):
            with self.subTest(case=label):
                with tempfile.TemporaryDirectory(prefix="overlay-iso-") as tmp:
                    home = Path(tmp) / "home"
                    bindir = home / "bin"
                    write_fake_bin(bindir, "codex", body)
                    tree, workdir, capture, env = self.make_case(
                        tmp, self.FULL_DECL)
                    env["PATH"] = ("~/bin" if tilde else str(bindir)) \
                        + os.pathsep + BASE_PATH
                    done = run_proposer(tree, workdir, "codex", env)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    arg0 = (Path(capture) / "1.arg0").read_text()
                    self.assertEqual(arg0, str(bindir / "codex"))

    def test_unusable_tmpdir_fails_the_launch_with_the_exact_message(self):
        with tempfile.TemporaryDirectory(prefix="overlay-iso-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL)
            broken_tmp = Path(tmp) / "tmp" / "does-not-exist"
            env["TMPDIR"] = str(broken_tmp)
            done = run_proposer(tree, workdir, "codex", env)
            self.assertIn(
                "proposer.sh: could not create overlay for backend 'codex' "
                "in %s: " % broken_tmp, done.stderr)
            self.assertEqual([p.name for p in Path(capture).glob("*")], [],
                             "the fake binary ran without isolation")
            self.assertNotEqual(done.returncode, 0)

    def test_read_only_tree_is_removed_and_failure_warns_once(self):
        ro_body = r'''mkdir -p "$HOME/ro/inner"
touch "$HOME/ro/inner/f"
chmod -R a-w "$HOME/ro"
'''
        with tempfile.TemporaryDirectory(prefix="overlay-iso-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=ro_body)
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [],
                             "a read-only tree inside the overlay stopped removal")

        # A removal that still fails: override remove_overlay in the COPY only
        # to the exact one-warning-then-give-up behaviour, and check the pass outcome is unchanged.
        with tempfile.TemporaryDirectory(prefix="overlay-iso-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL)
            module = tree / "lib" / "backend_overlay.py"
            override = textwrap.dedent('''

                # test override: removal always fails, warning only
                def remove_overlay(root, *, warn=sys.stderr):
                    print("specstride: warning: could not remove overlay %s: "
                          "injected failure" % (root,), file=warn)
                    return False
            ''')
            source = module.read_text()
            anchor = 'if __name__ == "__main__":'
            module.write_text(source.replace(anchor, override + "\n" + anchor, 1))
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            warnings = [l for l in done.stderr.splitlines()
                        if "could not remove overlay" in l]
            self.assertEqual(len(warnings), 1, done.stderr)
            self.assertRegex(warnings[0],
                             r"could not remove overlay .*/specstride-overlay\.codex\.")

    def _start_sleeping_pass(self, tmp, decl):
        tree, workdir, capture, env = self.make_case(
            tmp, decl, body_extra=HARNESS_PID_RECORD + "sleep 300\n")
        proc = run_proposer_popen(tree, workdir, "codex", env)
        self.addCleanup(self._safe_kill, proc)
        return proc, workdir, capture

    @staticmethod
    def _safe_kill(proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)

    def _kill_tree(self, pid, sig=signal.SIGTERM):
        """TERM a process and its descendants, the way kill_tree does."""
        def descendants(root):
            out = subprocess.run(["ps", "-o", "pid=", "--ppid", str(root)],
                                 capture_output=True, text=True)
            kids = [int(x) for x in out.stdout.split()]
            for kid in list(kids):
                kids.extend(descendants(kid))
            return kids
        for victim in descendants(pid) + [pid]:
            try:
                os.kill(victim, sig)
            except ProcessLookupError:
                pass

    def _pidfile_pid(self, workdir):
        pidfile = Path(workdir) / ".specstride" / "proposer.pid"
        if not self.wait_until(pidfile.exists, timeout=60):
            self.fail("proposer never wrote its PIDFILE")
        return int(pidfile.read_text().strip())

    def test_stop_now_kills_the_pass_and_the_reap_removes_the_root(self):
        with tempfile.TemporaryDirectory(prefix="overlay-stop-") as tmp:
            proc, workdir, capture = self._start_sleeping_pass(
                tmp, self.FULL_DECL)
            pass_pid = self._pidfile_pid(workdir)
            self.assertTrue(
                self.wait_until(
                    lambda: (Path(capture) / "harness.pid").exists(),
                    timeout=60),
                "fake codex never recorded its pid")
            harness_pid = int((Path(capture) / "harness.pid").read_text())
            self.assertTrue(self.wait_until(lambda: list_overlays(Path(tmp) / "tmp"),
                                            timeout=60),
                            "no overlay root appeared")
            # the same semantics `specstride stop --now` uses: TERM the pass
            # subshell's whole tree (the PID in the PIDFILE) mid-pass
            self._kill_tree(pass_pid, signal.SIGTERM)
            proc.wait(timeout=120)
            self.assertTrue(
                self.wait_until(lambda: not list_overlays(Path(tmp) / "tmp"), timeout=60),
                "post-wait reap did not remove the root")
            time.sleep(1.5)
            with self.assertRaises(ProcessLookupError):
                os.kill(harness_pid, 0)
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    def test_sigkill_proposer_leftover_swept_by_next_startup(self):
        with tempfile.TemporaryDirectory(prefix="overlay-kill-") as tmp:
            proc, workdir, _ = self._start_sleeping_pass(tmp, self.FULL_DECL)
            real_tmp = Path(tmp) / "tmp"

            def _marked_root():
                roots = list_overlays(real_tmp)
                # the root exists between mktemp -d and populate's marker; a
                # leftover to sweep is only real once the marker is readable
                if len(roots) == 1 and os.path.exists(
                        os.path.join(roots[0], "specstride-overlay.json")):
                    return roots[0]
                return None

            self.assertTrue(self.wait_until(lambda: _marked_root(), timeout=60),
                            "no overlay root appeared")
            leftover = _marked_root()
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=60)
            self.assertTrue(os.path.exists(leftover), "no leftover to sweep")
            decoy = _write_overlay_dir(tmp, "specstride-overlay.decoy.aaa",
                                       os.getpid(),
                                       backend_overlay._proc_start_time(os.getpid()))
            tree2, work2, _capture2, env2 = self.make_case(
                Path(tmp) / "second", None, body_extra=":\n")
            # the second run must share the FIRST run's fake real TMPDIR (the
            # sweep globs that directory); make_case under tmp/second would
            # otherwise give it a private one
            env2.update(_fake_home_env(tmp))
            done = run_proposer(tree2, work2, "codex", env2)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn(
                "specstride: removed stale overlay %s (owner %d gone)"
                % (leftover, proc.pid), done.stderr)
            self.assertFalse(os.path.exists(leftover))
            self.assertTrue(decoy.exists(),
                            "the sweep removed a live owner's overlay")

    def _signal_scope_case(self, sig):
        tick_body = HARNESS_PID_RECORD + r'''while :; do
  echo tick >> "$HOME/tick"
  sleep 1
done &
while :; do sleep 5; done
'''
        with tempfile.TemporaryDirectory(prefix="overlay-sig-") as tmp:
            proc, workdir, capture = self._start_sleeping_pass(tmp, self.FULL_DECL)
            self._pidfile_pid(workdir)
            self.assertTrue(
                self.wait_until(lambda: (Path(capture) / "harness.pid").exists(),
                                timeout=60),
                "fake codex never recorded its pid")
            harness_pid = int((Path(capture) / "harness.pid").read_text())
            root = None
            def _root():
                roots = list_overlays(Path(tmp) / "tmp")
                return roots[0] if roots else None
            root = self.wait_until(lambda: _root()) and _root()
            self.assertTrue(root)
            os.kill(proc.pid, sig)
            rc = proc.wait(timeout=120)
            self.assertNotEqual(rc, 0, "the proposer ignored the signal")
            self.assertTrue(self.wait_until(lambda: not os.path.exists(root),
                                            timeout=60),
                            "the overlay survived the signal")
            with self.assertRaises(ProcessLookupError):
                os.kill(harness_pid, 0)
            time.sleep(2.5)  # the tick loop would need its overlay home back
            self.assertFalse(os.path.exists(root),
                             "a probe reappeared under the removed root")
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    def test_sigint_in_pass_scope_cleans_up(self):
        self._signal_scope_case(signal.SIGINT)

    def test_sigterm_in_pass_scope_cleans_up(self):
        self._signal_scope_case(signal.SIGTERM)

    def test_concurrent_runs_share_the_tmp_without_stealing(self):
        with tempfile.TemporaryDirectory(prefix="overlay-conc-") as tmp:
            procs = []
            for index in (1, 2):
                sub = Path(tmp) / ("run%d" % index)
                tree, workdir, capture, env = self.make_case(
                    sub, self.FULL_DECL, body_extra=PROBE_LISTING, body_pre=PRE_HOMES_LOG)
                procs.append((run_proposer_popen(tree, workdir, "codex", env),
                              Path(capture) / "homes.log"))
            for proc, _ in procs:
                self.assertEqual(proc.wait(timeout=600), 0, proc.stderr.read())
            roots = []
            for proc, homes_log in procs:
                stderr = proc.stderr.read()
                self.assertNotIn("removed stale overlay", stderr)
                seen = [l.split(" ")[0]
                        for l in homes_log.read_text().splitlines()]
                self.assertEqual(len(seen), 1)
                roots.append(os.path.dirname(seen[0]))
            self.assertNotEqual(roots[0], roots[1])
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])


class NoDeclarationUnchangedTest(_ProposerOverlayCase):
    """With no declaration a codex pass starts exactly one overlay process."""

    def test_only_the_single_startup_call_and_no_overlay_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="nodecl-") as tmp:
            tmp = Path(tmp)
            real_python3 = shutil.which("python3")
            shim_dir = tmp / "pyshim"
            shim_body = ("#!/bin/bash\n"
                         "printf '%s\\n' \"$*\" >> \"$SPECSTRIDE_PY_LOG\"\n"
                         'exec "' + real_python3 + '" "$@"\n')
            write_fake_bin(shim_dir, "python3", shim_body)
            tree, workdir, capture, env = self.make_case(tmp, None)
            env["SPECSTRIDE_PY_LOG"] = str(tmp / "py.log")
            env["PATH"] = str(shim_dir) + os.pathsep + env["PATH"]
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            log = (tmp / "py.log").read_text().splitlines()
            overlay_calls = [l for l in log if "backend_overlay.py" in l]
            self.assertEqual(len(overlay_calls), 1,
                             "more than one backend_overlay.py process: %r"
                             % overlay_calls)
            self.assertIn(" startup ", " %s " % overlay_calls[0])
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])
            self.assertNotIn("overlay", done.stderr.lower(), done.stderr)


class GitIdentityUnitTest(_DeclarationTableCase):
    """T025: git identity resolution and its env-file records, in process.

    Every git call runs with HOME, XDG_CONFIG_HOME and GIT_CONFIG_NOSYSTEM
    pointed at the test's temp dirs, so the host's own git config never
    leaks into what is being asserted.
    """

    maxDiff = None

    def git_env(self, tmp, path=True):
        tmp = Path(tmp)
        home = tmp / "home"
        xdg = tmp / "xdg"
        home.mkdir(parents=True, exist_ok=True)
        xdg.mkdir(parents=True, exist_ok=True)
        return {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": ("/usr/local/bin:/usr/bin:/bin" if path
                     else str(tmp / "empty-bin")),
        }

    def write_gitconfig(self, tmp, body):
        tmp = Path(tmp)
        (tmp / "home").mkdir(parents=True, exist_ok=True)
        (tmp / "home" / ".gitconfig").write_text(body)

    IDENTITY = {"GIT_AUTHOR_NAME": "Op Name",
                "GIT_AUTHOR_EMAIL": "op@example.com",
                "GIT_COMMITTER_NAME": "Op Name",
                "GIT_COMMITTER_EMAIL": "op@example.com"}

    def test_global_user_config_resolves_all_four(self):
        with tempfile.TemporaryDirectory(prefix="gitid-") as tmp:
            self.write_gitconfig(tmp, "[user]\n"
                                      "\tname = Op Name\n"
                                      "\temail = op@example.com\n")
            identity = backend_overlay.resolve_git_identity(
                None, self.git_env(tmp))
        self.assertEqual(identity, self.IDENTITY)

    def test_author_committer_keys_win_over_user(self):
        with tempfile.TemporaryDirectory(prefix="gitid-") as tmp:
            self.write_gitconfig(tmp, "[user]\n"
                                      "\tname = User Name\n"
                                      "\temail = user@example.com\n"
                                      "[author]\n"
                                      "\tname = Author Name\n"
                                      "[committer]\n"
                                      "\temail = committer@example.com\n")
            identity = backend_overlay.resolve_git_identity(
                None, self.git_env(tmp))
        self.assertEqual(identity, {
            "GIT_AUTHOR_NAME": "Author Name",
            "GIT_AUTHOR_EMAIL": "user@example.com",
            "GIT_COMMITTER_NAME": "User Name",
            "GIT_COMMITTER_EMAIL": "committer@example.com"})

    def test_repo_level_user_config_wins_over_global(self):
        with tempfile.TemporaryDirectory(prefix="gitid-") as tmp:
            self.write_gitconfig(tmp, "[user]\n"
                                      "\tname = Op Name\n"
                                      "\temail = op@example.com\n")
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config",
                            "user.name", "Repo Name"], check=True)
            identity = backend_overlay.resolve_git_identity(
                str(repo), self.git_env(tmp))
        self.assertEqual(identity, {
            "GIT_AUTHOR_NAME": "Repo Name",
            "GIT_AUTHOR_EMAIL": "op@example.com",
            "GIT_COMMITTER_NAME": "Repo Name",
            "GIT_COMMITTER_EMAIL": "op@example.com"})

    def test_var_already_in_environ_is_omitted(self):
        with tempfile.TemporaryDirectory(prefix="gitid-") as tmp:
            self.write_gitconfig(tmp, "[user]\n"
                                      "\tname = Op Name\n"
                                      "\temail = op@example.com\n")
            env = self.git_env(tmp)
            env["GIT_AUTHOR_NAME"] = "Operator's Own"
            identity = backend_overlay.resolve_git_identity(None, env)
        self.assertNotIn("GIT_AUTHOR_NAME", identity)
        for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                    "GIT_COMMITTER_EMAIL"):
            self.assertIn(var, identity)

    def test_nothing_configured_resolves_to_nothing(self):
        with tempfile.TemporaryDirectory(prefix="gitid-") as tmp:
            identity = backend_overlay.resolve_git_identity(
                None, self.git_env(tmp))
        self.assertEqual(identity, {})

    def test_workdir_outside_a_repo_still_resolves_global(self):
        with tempfile.TemporaryDirectory(prefix="gitid-") as tmp:
            self.write_gitconfig(tmp, "[user]\n"
                                      "\tname = Op Name\n"
                                      "\temail = op@example.com\n")
            plain = Path(tmp) / "not-a-repo"
            plain.mkdir()
            identity = backend_overlay.resolve_git_identity(
                str(plain), self.git_env(tmp))
        self.assertEqual(identity, self.IDENTITY)

    def test_git_missing_from_path_resolves_to_nothing(self):
        with tempfile.TemporaryDirectory(prefix="gitid-") as tmp:
            self.write_gitconfig(tmp, "[user]\n"
                                      "\tname = Op Name\n"
                                      "\temail = op@example.com\n")
            (Path(tmp) / "empty-bin").mkdir()
            identity = backend_overlay.resolve_git_identity(
                None, self.git_env(tmp, path=False))
        self.assertEqual(identity, {})

    # ── the populate records (T028) ──────────────────────────────────────

    def _populate(self, tmp, decl, workdir):
        self.use_table({"codex": decl})
        root = Path(tmp) / "root"
        root.mkdir(mode=0o700)
        workdir.mkdir(parents=True, exist_ok=True)
        backend_overlay.populate(root, "codex", "pass", os.getpid(),
                                 str(workdir), self.git_env(tmp))
        return backend_overlay._read_env_file(root)

    def test_populate_writes_identity_records_for_a_home_declaration(self):
        with tempfile.TemporaryDirectory(prefix="gitid-") as tmp:
            self.write_gitconfig(tmp, "[user]\n"
                                      "\tname = Op Name\n"
                                      "\temail = op@example.com\n")
            records = self._populate(tmp, {"vars": ("HOME",)},
                                     Path(tmp) / "repo")
        sets = {(n, v) for kind, n, v in records if kind == "set"}
        for var, value in self.IDENTITY.items():
            self.assertIn((var, value), sets)
        self.assertIn(("GIT_CONFIG_GLOBAL",
                       str(Path(tmp) / "root" / "specstride-overlay.gitconfig")),
                      sets)
        self.assertNotIn(("GIT_CONFIG_GLOBAL", None),
                         {(n, v) for kind, n, v in records if kind == "unset"})

    def test_populate_tmpdir_only_declaration_still_sets_gitconfig(self):
        with tempfile.TemporaryDirectory(prefix="gitid-") as tmp:
            self.write_gitconfig(tmp, "[user]\n"
                                      "\tname = Op Name\n"
                                      "\temail = op@example.com\n")
            records = self._populate(tmp, {"vars": ("TMPDIR",)},
                                     Path(tmp) / "repo")
        sets = {(n, v) for kind, n, v in records if kind == "set"}
        self.assertIn(("GIT_CONFIG_GLOBAL",
                       str(Path(tmp) / "root" / "specstride-overlay.gitconfig")),
                      sets)
        for var, value in self.IDENTITY.items():
            self.assertIn((var, value), sets)
        unsets = {n for kind, n, _ in records if kind == "unset"}
        self.assertNotIn("GIT_CONFIG_GLOBAL", unsets)


# ── US2 end to end: commits inside the overlay keep the operator's identity ──

# T026's fake codex: a real git commit in the cwd the harness was given
# (the work directory), the commit's identity and env recorded. `{config}`
# is a per-case git command prefix (never str.format — the bash braces stay).
GIT_COMMIT_BODY = r'''
cap="${SPECSTRIDE_TEST_CAPTURE:?SPECSTRIDE_TEST_CAPTURE not set}"
n="${SPECSTRIDE_TEST_N:-1}"
env -0 > "$cap/$n.env"
git init -q . 2>/dev/null
''' + "{config}" + r'''
echo x > file.txt
git add file.txt
git commit -qm "overlay commit" 2> "$cap/$n.commit.err"
git log -1 --format='%an|%ae|%cn|%ce' > "$cap/$n.ident" 2>/dev/null
[[ -n "${GIT_CONFIG_GLOBAL:-}" ]] && cp "$GIT_CONFIG_GLOBAL" "$cap/$n.globalcfg" 2>/dev/null
find "$(dirname "$HOME")" -type f -print0 2>/dev/null | xargs -0 -r sha256sum > "$cap/$n.overlay.hashes" 2>/dev/null
mkdir -p .specstride/gates
echo ok > .specstride/gates/GATE1-EVIDENCE.md
'''

OP_GITCONFIG = "[user]\n\tname = Op Name\n\temail = op@example.com\n"
OP_IDENT = "Op Name|op@example.com|Op Name|op@example.com"


class ProposerGitIdentityTest(_ProposerOverlayCase):
    """T026: US2 end to end through a proposer pass with a declared codex."""

    maxDiff = None

    def git_case(self, tmp, decl, config="", gitconfig=OP_GITCONFIG,
                 extra_env=None, workdir_setup=None):
        tmp = Path(tmp)
        home = tmp / "home"
        home.mkdir(parents=True, exist_ok=True)
        if gitconfig is not None:
            (home / ".gitconfig").write_text(gitconfig)
        env = {"GIT_CONFIG_NOSYSTEM": "1"}
        env.update(extra_env or {})
        tree, workdir, capture, full_env = self.make_case(
            tmp, decl, body_extra=config + GIT_COMMIT_BODY, extra_env=env)
        if workdir_setup is not None:
            workdir_setup(workdir)
        return tree, workdir, capture, full_env

    def read_ident(self, capture, n=1):
        path = Path(capture) / ("%d.ident" % n)
        return path.read_text().strip() if path.exists() else ""

    def test_a_global_gitconfig_identity_lands_on_the_commit(self):
        with tempfile.TemporaryDirectory(prefix="gitid-e2e-") as tmp:
            tree, workdir, capture, env = self.git_case(
                tmp, {"vars": ("HOME",)})
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self.read_ident(capture), OP_IDENT)

    def test_b_repo_level_identity_in_the_workdir_wins(self):
        def setup(workdir):
            workdir.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", str(workdir)], check=True)
            subprocess.run(["git", "-C", str(workdir), "config",
                            "user.name", "Repo Name"], check=True)

        with tempfile.TemporaryDirectory(prefix="gitid-e2e-") as tmp:
            tree, workdir, capture, env = self.git_case(
                tmp, {"vars": ("HOME",)}, workdir_setup=setup)
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self.read_ident(capture),
                             "Repo Name|op@example.com|"
                             "Repo Name|op@example.com")

    def test_c_operator_exports_win_and_pass_through(self):
        with tempfile.TemporaryDirectory(prefix="gitid-e2e-") as tmp:
            tree, workdir, capture, env = self.git_case(
                tmp, {"vars": ("HOME",)},
                extra_env={"GIT_AUTHOR_NAME": "Operator Author",
                           "GIT_COMMITTER_EMAIL": "operator@example.com"})
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(
                self.read_ident(capture),
                "Operator Author|op@example.com|"
                "Op Name|operator@example.com")
            seen = self.read_env(capture)
            self.assertEqual(seen["GIT_AUTHOR_NAME"], "Operator Author")
            self.assertEqual(seen["GIT_COMMITTER_EMAIL"],
                             "operator@example.com")

    def test_d_no_identity_anywhere_behaves_exactly_like_no_overlay(self):
        outcomes = {}
        for label, decl in (("declared", {"vars": ("HOME",)}),
                            ("undeclared", None)):
            with tempfile.TemporaryDirectory(prefix="gitid-e2e-") as tmp:
                tree, workdir, capture, env = self.git_case(
                    tmp, decl, gitconfig=None)
                done = run_proposer(tree, workdir, "codex", env)
                stderr = re.sub(r"\d{4}-\d\d-\d\dT[\d:+-]+", "<TS>",
                                done.stderr).replace(str(tmp), "<T>")
                ident = self.read_ident(capture)
                outcomes[label] = (done.returncode, stderr, ident)
                if label == "declared":
                    seen = self.read_env(capture)
                    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                                "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
                        self.assertNotIn(var, seen)
        self.assertEqual(outcomes["declared"], outcomes["undeclared"])

    def test_e_git_config_global_write_stays_out_of_the_real_gitconfig(self):
        with tempfile.TemporaryDirectory(prefix="gitid-e2e-") as tmp:
            tree, workdir, capture, env = self.git_case(
                tmp, {"vars": ("HOME",)},
                config="git config --global user.name Changed\n")
            real_gitconfig = Path(tmp) / "home" / ".gitconfig"
            before = real_gitconfig.read_bytes()
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(real_gitconfig.read_bytes(), before,
                             "the real .gitconfig was mutated")
            self.assertEqual(self.read_ident(capture), OP_IDENT)

    def test_f_operator_git_config_global_is_replaced_but_honoured(self):
        with tempfile.TemporaryDirectory(prefix="gitid-e2e-") as tmp:
            op_file = Path(tmp) / "op-gitconfig"
            op_file.write_text("[user]\n\tname = Op2 Name\n"
                               "\temail = op2@example.com\n")
            before = op_file.read_bytes()
            tree, workdir, capture, env = self.git_case(
                tmp, {"vars": ("HOME",)},
                config="git config --global user.name Changed\n",
                extra_env={"GIT_CONFIG_GLOBAL": str(op_file)})
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            seen = self.read_env(capture)
            self.assertIn("specstride-overlay.codex.", seen["HOME"])
            root = os.path.dirname(seen["HOME"])
            self.assertEqual(seen["GIT_CONFIG_GLOBAL"],
                             os.path.join(root, "specstride-overlay.gitconfig"))
            self.assertEqual(self.read_ident(capture),
                             "Op2 Name|op2@example.com|"
                             "Op2 Name|op2@example.com")
            self.assertEqual(op_file.read_bytes(), before,
                             "the operator's GIT_CONFIG_GLOBAL file was mutated")
            overlay_gitconfig = (Path(capture) / "1.globalcfg").read_text()
            self.assertIn("Changed", overlay_gitconfig)

    def test_g_tmpdir_only_declaration_writes_the_overlay_gitconfig(self):
        with tempfile.TemporaryDirectory(prefix="gitid-e2e-") as tmp:
            tree, workdir, capture, env = self.git_case(
                tmp, {"vars": ("TMPDIR",)},
                config="git config --global user.name Changed\n")
            real_gitconfig = Path(tmp) / "home" / ".gitconfig"
            before = real_gitconfig.read_bytes()
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(real_gitconfig.read_bytes(), before,
                             "the real .gitconfig was mutated")
            self.assertEqual(self.read_ident(capture), OP_IDENT)
            overlay_gitconfig = (Path(capture) / "1.globalcfg").read_text()
            self.assertIn("Changed", overlay_gitconfig)

    def test_h_nothing_under_the_overlay_copies_the_real_home(self):
        with tempfile.TemporaryDirectory(prefix="gitid-e2e-") as tmp:
            tree, workdir, capture, env = self.git_case(
                tmp, {"vars": ("HOME",)})
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            seen = self.read_env(capture)
            root = os.path.dirname(seen["HOME"])
            self.assertIn("specstride-overlay.codex.", root)
            empty = hashlib.sha256(b"").hexdigest()
            overlay_hashes = {
                line.split(None, 1)[0]
                for line in (Path(capture) / "1.overlay.hashes")
                .read_text().splitlines() if line.strip()}
            home_hashes = {h for _, _, h in snapshot_tree(Path(tmp) / "home")}
            shared = (overlay_hashes & home_hashes) - {empty}
            self.assertEqual(shared, set(),
                             "the overlay copied a file from the real home")


# ── US3: the attempt scope — one overlay per proposer invocation ─────────────

ATTEMPT_LOG_BODY = r'''# append-after-count: `$lines` is how many lines $HOME/log had BEFORE
# this invocation of the fake binary (pass N sees N-1 earlier lines).
lines=0
[[ -f "$HOME/log" ]] && lines="$(wc -l < "$HOME/log")"
lines="${lines//[[:space:]]/}"
printf '%s\n' "$lines" >> "$cap/before.log"
printf '%s\n' "$HOME" >> "$cap/homes.log"
echo "$HOME" >> "$HOME/log"
# only pass 3 completes the run, so -n 3 really runs three passes
if (( lines >= 2 )); then
  mkdir -p .specstride/gates && echo ok > .specstride/gates/GATE1-EVIDENCE.md
else
  rm -f .specstride/gates/GATE1-EVIDENCE.md
fi
'''


class ProposerAttemptScopeTest(_ProposerOverlayCase):
    """SPECSTRIDE_<BACKEND>_OVERLAY=attempt: one overlay per invocation.

    Every pass of one proposer run shares a single specstride-overlay.* root;
    it is removed on every exit path (normal, SIGINT, SIGTERM) and never
    created when the run stops before pass 1. A value on a backend without a
    declaration warns once and changes nothing.
    """

    FULL_DECL = ProposerPassScopeTest.FULL_DECL

    def test_attempt_three_passes_share_one_root_in_order(self):
        # (a) -n 3: every pass saw the same root, pass N saw N-1 earlier
        # lines, and the root is gone after the proposer exits.
        with tempfile.TemporaryDirectory(prefix="overlay-att-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=ATTEMPT_LOG_BODY)
            env["SPECSTRIDE_CODEX_OVERLAY"] = "attempt"
            done = run_proposer(tree, workdir, "codex", env, n=3)
            self.assertEqual(done.returncode, 0, done.stderr)
            homes = (Path(capture) / "homes.log").read_text().splitlines()
            self.assertEqual(len(homes), 3, homes)
            roots = {os.path.dirname(h) for h in homes}
            self.assertEqual(len(roots), 1, "passes saw different roots")
            self.assertIn("specstride-overlay.codex.", roots.pop())
            before = (Path(capture) / "before.log").read_text().splitlines()
            self.assertEqual(before, ["0", "1", "2"],
                             "pass N did not see exactly N-1 earlier lines")
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [],
                             "attempt overlay survived the run")

    def _assert_two_fresh_passes(self, tmp, capture):
        """The T014 (d) per-pass assertions, reused for unset and =pass."""
        homes = [l.split(" ")[0]
                 for l in (Path(capture) / "homes.log").read_text().splitlines()]
        self.assertEqual(len(homes), 2)
        self.assertNotEqual(homes[0], homes[1], "pass 2 reused pass 1's root")
        for seen in homes:
            self.assertIn("specstride-overlay.codex.", seen)
            self.assertTrue(seen.endswith("/HOME"))
        self.assertEqual(
            (Path(capture) / "homes.log").read_text().splitlines()[1].split(" ")[-1],
            "pre:clean", "pass 2 saw pass 1's $HOME/.probe")
        self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    no_evidence_first_pass = ProposerPassScopeTest.NO_EVIDENCE_FIRST_PASS

    def test_unset_scope_stays_per_pass(self):
        # (b) unset behaves exactly as before US3: a fresh root per pass.
        with tempfile.TemporaryDirectory(prefix="overlay-att-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL,
                body_extra=PROBE_LISTING + self.no_evidence_first_pass,
                body_pre=PRE_HOMES_LOG)
            done = run_proposer(tree, workdir, "codex", env, n=2)
            self.assertEqual(done.returncode, 0, done.stderr)
            self._assert_two_fresh_passes(tmp, capture)

    def test_pass_scope_stays_per_pass(self):
        # (b) =pass behaves exactly as US1: a fresh root per pass.
        with tempfile.TemporaryDirectory(prefix="overlay-att-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL,
                body_extra=PROBE_LISTING + self.no_evidence_first_pass,
                body_pre=PRE_HOMES_LOG,
                extra_env={"SPECSTRIDE_CODEX_OVERLAY": "pass"})
            done = run_proposer(tree, workdir, "codex", env, n=2)
            self.assertEqual(done.returncode, 0, done.stderr)
            self._assert_two_fresh_passes(tmp, capture)

    @staticmethod
    def _safe_kill(proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)

    def _pidfile_pid(self, workdir):
        pidfile = Path(workdir) / ".specstride" / "proposer.pid"
        if not self.wait_until(pidfile.exists, timeout=60):
            self.fail("proposer never wrote its PIDFILE")
        return int(pidfile.read_text().strip())

    def _attempt_signal_case(self, sig, expected_rc):
        # (c) shared body: signal the proposer mid-pass-1, assert the exact
        # exit code, the harness gone and the root gone.
        with tempfile.TemporaryDirectory(prefix="overlay-att-sig-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL,
                body_extra=HARNESS_PID_RECORD + "sleep 300\n")
            env["SPECSTRIDE_CODEX_OVERLAY"] = "attempt"
            proc = run_proposer_popen(tree, workdir, "codex", env)
            self.addCleanup(self._safe_kill, proc)
            self._pidfile_pid(workdir)
            self.assertTrue(
                self.wait_until(
                    lambda: (Path(capture) / "harness.pid").exists(), timeout=60),
                "fake codex never recorded its pid")
            harness_pid = int((Path(capture) / "harness.pid").read_text())

            def _root():
                roots = list_overlays(Path(tmp) / "tmp")
                return roots[0] if roots else None
            root = self.wait_until(lambda: _root()) and _root()
            self.assertTrue(root, "no attempt overlay appeared")
            os.kill(proc.pid, sig)
            self.assertEqual(proc.wait(timeout=120), expected_rc,
                             "wrong exit code after the signal")
            self.assertTrue(
                self.wait_until(lambda: not os.path.exists(root), timeout=60),
                "the attempt overlay survived the signal")
            with self.assertRaises(ProcessLookupError):
                os.kill(harness_pid, 0)
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    def test_sigint_in_attempt_scope_exits_130(self):
        self._attempt_signal_case(signal.SIGINT, 130)

    def test_sigterm_in_attempt_scope_exits_143(self):
        self._attempt_signal_case(signal.SIGTERM, 143)

    def test_bogus_value_exits_3_before_pass_1(self):
        # (d) an invalid value stops the run before pass 1.
        with tempfile.TemporaryDirectory(prefix="overlay-bad-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=":\n",
                extra_env={"SPECSTRIDE_CODEX_OVERLAY": "bogus"})
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 3, done.stderr)
            self.assertIn("SPECSTRIDE_CODEX_OVERLAY='bogus' is invalid; "
                          "accepted values: pass, attempt", done.stderr)
            self.assertFalse(capture.exists(), "the fake binary ran")
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    def _nodecl_pass(self, tmp, backend, var, value):
        """One pass on a tree with NO declarations, `var` explicitly set."""
        tmp = Path(tmp)
        tree = copy_tree(tmp / "tree")
        workdir = tmp / "work"
        capture = tmp / "capture"
        env = _fake_home_env(tmp)
        env["SPECSTRIDE_TEST_CAPTURE"] = str(capture)
        env["SPECSTRIDE_TEST_N"] = "1"
        env.update(backend_launcher_env(tmp / "bin", backend, tmp))
        env[var] = value
        done = run_proposer(tree, workdir, backend, env)
        return done, capture

    def _fixture_launch(self, backend):
        name = backend.replace(":", "_").replace("/", "_")
        fixture = json.loads(
            (FIXTURE_DIR / (name + ".json")).read_text())
        self.assertEqual(fixture["backend"], backend)
        return fixture["launch"]

    def test_claude_attempt_without_declaration_warns_once_and_is_baseline(self):
        # (e) a valid value on a backend without a declaration: exactly one
        # no-declaration warning, and the launch is the claude baseline.
        with tempfile.TemporaryDirectory(prefix="overlay-ndc-") as tmp:
            done, capture = self._nodecl_pass(
                tmp, "claude", "SPECSTRIDE_CLAUDE_OVERLAY", "attempt")
            self.assertEqual(done.returncode, 0, done.stderr)
            warnings = [l for l in done.stderr.splitlines()
                        if l.startswith("specstride: warning:")]
            self.assertEqual(len(warnings), 1, done.stderr)
            self.assertIn("SPECSTRIDE_CLAUDE_OVERLAY is set but backend "
                          "'claude' has no overlay", warnings[0])
            observed = normalise_capture(capture, tmp)
            # the knob itself passes through to the harness env (SPECSTRIDE_*
            # is never masked); every other byte must equal the baseline
            observed["env"].pop("SPECSTRIDE_CLAUDE_OVERLAY", None)
            self.assertEqual(observed, self._fixture_launch("claude"))
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    def test_dsh_attempt_without_declaration_warns_once_and_is_baseline(self):
        # (e) same for dsh:<provider>/<model>: one warning naming `dsh`.
        backend = "dsh:zai/glm-5.3"
        with tempfile.TemporaryDirectory(prefix="overlay-ndd-") as tmp:
            done, capture = self._nodecl_pass(
                tmp, backend, "SPECSTRIDE_DSH_OVERLAY", "attempt")
            self.assertEqual(done.returncode, 0, done.stderr)
            warnings = [l for l in done.stderr.splitlines()
                        if l.startswith("specstride: warning:")]
            self.assertEqual(len(warnings), 1, done.stderr)
            self.assertIn("'dsh'", warnings[0])
            observed = normalise_capture(capture, tmp)
            observed["env"].pop("SPECSTRIDE_DSH_OVERLAY", None)
            self.assertEqual(observed, self._fixture_launch(backend))
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    def test_claude_bogus_value_exits_3(self):
        # (f) the value is validated for EVERY backend, declared or not.
        with tempfile.TemporaryDirectory(prefix="overlay-badc-") as tmp:
            done, capture = self._nodecl_pass(
                tmp, "claude", "SPECSTRIDE_CLAUDE_OVERLAY", "bogus")
            self.assertEqual(done.returncode, 3, done.stderr)
            self.assertIn("SPECSTRIDE_CLAUDE_OVERLAY='bogus' is invalid; "
                          "accepted values: pass, attempt", done.stderr)
            self.assertFalse(capture.exists(), "the fake binary ran")

    def test_attempt_creation_failure_exits_1_before_pass_1(self):
        # (g) a fake real TMPDIR that cannot host the overlay (here: it does
        # not exist, which also defeats root's DAC override): the overlay
        # cannot be created, the run stops before pass 1 with the
        # creation-failure message.
        with tempfile.TemporaryDirectory(prefix="overlay-mk-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=":\n",
                extra_env={"SPECSTRIDE_CODEX_OVERLAY": "attempt"})
            env["TMPDIR"] = str(Path(tmp) / "tmp" / "absent")
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 1, done.stderr)
            self.assertRegex(
                done.stderr,
                r"proposer\.sh: could not create overlay for backend 'codex'")
            self.assertFalse(capture.exists(), "the fake binary ran")
            self.assertEqual(
                list_overlays(Path(tmp) / "tmp"), [],
                "something was created under the (real) fake TMPDIR")

    def test_evidence_already_present_never_creates_the_overlay(self):
        # (h) resume-to-critic: exit 0 before pass 1, nothing left behind.
        with tempfile.TemporaryDirectory(prefix="overlay-res-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=":\n",
                extra_env={"SPECSTRIDE_CODEX_OVERLAY": "attempt"})
            evidence = workdir / ".specstride" / "gates" / "GATE1-EVIDENCE.md"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text("# already done\n")
            done = run_proposer(tree, workdir, "codex", env)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("nothing to do", done.stderr)
            self.assertFalse(capture.exists(), "the fake binary ran")
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])

    def test_empty_prompt_never_creates_the_overlay(self):
        # (h) an empty prompt exits 1 before pass 1, no overlay left.
        with tempfile.TemporaryDirectory(prefix="overlay-emp-") as tmp:
            tree, workdir, capture, env = self.make_case(
                tmp, self.FULL_DECL, body_extra=":\n",
                extra_env={"SPECSTRIDE_CODEX_OVERLAY": "attempt"})
            done = run_proposer(tree, workdir, "codex", env,
                                prompt_text="   \n\t\n")
            self.assertEqual(done.returncode, 1, done.stderr)
            self.assertIn("prompt is empty", done.stderr)
            self.assertFalse(capture.exists(), "the fake binary ran")
            self.assertEqual(list_overlays(Path(tmp) / "tmp"), [])


class OrchestratorScopeTest(unittest.TestCase):
    """The orchestrator validates the scope knob as config (E_SPEC) itself.

    A bogus SPECSTRIDE_<BACKEND>_OVERLAY stops the run with exit 3 before any
    proposer attempt starts; a valid value for a backend without a declaration
    produces no warning from the orchestrator (it validates with --quiet and
    leaves the no-declaration warning to the proposer's own startup call).
    """

    ORCHESTRATOR = REPO / "orchestrator.sh"

    MINIMAL_SPEC = """# Minimal scope-validation fixture

## Phase 1 — Deliver
### Acceptance criteria
- [ ] Create an independently readable durable result
"""

    def _run_orchestrator(self, tmp, extra_env, spec=True):
        tmp = Path(tmp)
        workdir = tmp / "work"
        workdir.mkdir(parents=True)
        # hermetic: never inherit this session's own SPECSTRIDE_* state
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("SPECSTRIDE_")}
        env.update({
            "SPECSTRIDE_AGENT_STREAM": "false",
            "SPECSTRIDE_GIT_COMMITS": "off",
        })
        env.update(extra_env)
        argv = ["/usr/bin/bash", str(self.ORCHESTRATOR),
                "--workdir", str(workdir),
                "--proposer", "codex",
                "--critic", "prime",
                "--verification", "off",
                "--max-iter", "1",
                "--no-live"]
        if spec:
            spec_path = tmp / "spec.md"
            spec_path.write_text(self.MINIMAL_SPEC)
            argv += ["--specs", str(spec_path)]
        result = subprocess.run(argv, cwd=str(workdir), env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=300)
        return result, workdir

    def test_bogus_scope_is_e_spec_before_any_attempt(self):
        with tempfile.TemporaryDirectory(prefix="orch-bad-") as tmp:
            result, workdir = self._run_orchestrator(
                tmp, {"SPECSTRIDE_CODEX_OVERLAY": "bogus"})
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 3, output)
            self.assertIn("SPECSTRIDE_CODEX_OVERLAY='bogus' is invalid; "
                          "accepted values: pass, attempt", output)
            # no proposer attempt started: no event stream, no evidence file
            self.assertEqual(list(workdir.rglob("events.jsonl")), [])
            self.assertEqual(list(workdir.rglob("GATE*-EVIDENCE.md")), [])

    def test_attempt_without_declaration_is_quiet(self):
        # =attempt for a backend without a declaration: the orchestrator's own
        # scope check passes silently (--quiet) and the run moves on to the
        # next config check (spec resolution), which stops it with E_SPEC.
        with tempfile.TemporaryDirectory(prefix="orch-quiet-") as tmp:
            result, _workdir = self._run_orchestrator(
                tmp, {"SPECSTRIDE_CODEX_OVERLAY": "attempt"}, spec=False)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 3, output)
            # the failure is spec RESOLUTION (it got past the scope check),
            # not the scope knob
            self.assertIn("tried:", output)
            self.assertNotIn("is invalid; accepted values", output)
            self.assertNotIn("specstride: warning:", output)


def _main(argv):
    if len(argv) < 4 or argv[1] != "--record-baseline":
        print("usage: test_backend_overlay.py --record-baseline <tree> <outdir> "
              "<backend>…", file=sys.stderr)
        return 2
    tree, outdir, *backends = argv[2:]
    record_baseline(tree, outdir, backends)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))


# ── US4: the CLI critic helpers run inside a per-call overlay (T033) ─────────

class CriticOverlayTest(_DeclarationTableCase):
    """The critic's CLI shell-outs run each call inside a removed-afterwards
    overlay; without a declaration their subprocess kwargs are today's."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="critic-ov-test."))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.workdir = self.tmp / "work"
        self.workdir.mkdir()
        self.captures = self.tmp / "captures"
        self.captures.mkdir()
        self.fake_bin = self.tmp / "bin"
        self.fake_bin.mkdir()
        self.addCleanup(self._clear_cache)

    # ── fake binaries ────────────────────────────────────────────────────
    def _recorder(self, name, tail="printf 'critic-ok\\n'"):
        """A fake backend binary: record its env -0, then behave as told."""
        return write_fake_bin(self.fake_bin, name, "#!/usr/bin/env bash\n" +
            textwrap.dedent("""
            n=$(cat "$CAPTURE_DIR/count" 2>/dev/null || echo 0); n=$((n+1))
            echo "$n" > "$CAPTURE_DIR/count"
            env -0 > "$CAPTURE_DIR/env.$n"
        """) + tail + "\n")

    def _captured_env(self, n):
        data = (Path(self.captures) / ("env.%d" % n)).read_bytes()
        out = {}
        for field in data.split(b"\0"):
            if field:
                k, _, v = field.partition(b"=")
                out[k.decode()] = v.decode("utf-8", "replace")
        return out

    def _captured_count(self):
        f = self.captures / "count"
        return int(f.read_text()) if f.exists() else 0

    def _real_tmp(self):
        return os.environ.get("TMPDIR") or "/tmp"

    def _overlay_roots(self):
        return glob.glob(os.path.join(self._real_tmp(), "specstride-overlay.*"))

    def _prime_env(self, **extra):
        env = {"CAPTURE_DIR": str(self.captures),
               "SPECSTRIDE_PRIME_AGENT_BIN": str(self.fake_bin / "prime-agent"),
               "TMPDIR": self._real_tmp()}
        env.update(extra)
        return env

    # ── (a) the empty table yields None and creates nothing ─────────────
    def test_a_empty_table_yields_none_and_creates_no_dir(self):
        self.use_table({})
        with backend_overlay.critic_overlay("prime", str(self.workdir)) as ov_env:
            self.assertIsNone(ov_env)
        self.assertEqual(self._overlay_roots(), [])

    # ── (b) a declaration yields a critic-scope env, removed either way ──
    def test_b_declared_env_is_critic_scope_and_removed(self):
        self.use_table({"prime": {"vars": ("HOME",)}})
        with backend_overlay.critic_overlay("prime", str(self.workdir)) as ov_env:
            self.assertIsNotNone(ov_env)
            root = os.path.dirname(ov_env["HOME"])
            self.assertTrue(os.path.isdir(root))
            self.assertIn("specstride-overlay.prime.", root)
            marker = json.loads(
                (Path(root) / "specstride-overlay.json").read_text())
            self.assertEqual(marker["scope"], "critic")
            self.assertEqual(marker["owner_pid"], os.getpid())
        self.assertFalse(os.path.exists(root))

    def test_b_root_gone_after_exception_inside_block(self):
        self.use_table({"prime": {"vars": ("HOME",)}})
        root = None
        with self.assertRaises(ValueError):
            with backend_overlay.critic_overlay(
                    "prime", str(self.workdir)) as ov_env:
                root = ov_env["HOME"]
                raise ValueError("boom")
        self.assertIsNotNone(root)
        self.assertFalse(os.path.exists(root))

    # ── (c) call_prime_shell: per-call overlay in every outcome ─────────
    def _prime_shell_case(self, tail, timeout=None, **env_extra):
        self.use_table({"prime": {"vars": ("HOME",)}})
        self._recorder("prime-agent", tail)
        env = self._prime_env(**env_extra)
        with mock.patch.dict(os.environ, env, clear=False):
            critic.call_prime_shell("prompt", None, timeout or 30,
                                    str(self.workdir))
        n = self._captured_count()
        self.assertEqual(n, 1)
        seen = self._captured_env(n)
        root = os.path.dirname(seen["HOME"])
        self.assertIn("specstride-overlay.prime.", root)
        return seen, root

    def test_c_prime_shell_normal_return_root_gone(self):
        seen, root = self._prime_shell_case(tail="printf 'critic-ok\\n'")
        self.assertEqual(seen.get("SPECSTRIDE_PRIME_AGENT_BIN"),
                         str(self.fake_bin / "prime-agent"))
        self.assertFalse(os.path.exists(root))
        self.assertEqual(self._overlay_roots(), [])

    def test_c_prime_shell_nonzero_exit_root_gone(self):
        with mock.patch.dict(os.environ, self._prime_env(), clear=False):
            with self.assertRaises(RuntimeError) as caught:
                # a fresh recorder whose tail exits 7
                self.use_table({"prime": {"vars": ("HOME",)}})
                self._recorder("prime-agent", tail="exit 7")
                critic.call_prime_shell("prompt", None, 30, str(self.workdir))
        self.assertIn("Prime Agent critic exit 7", str(caught.exception))
        root = os.path.dirname(self._captured_env(1)["HOME"])
        self.assertIn("specstride-overlay.prime.", root)
        self.assertFalse(os.path.exists(root))

    def test_c_prime_shell_timeout_root_gone(self):
        with mock.patch.dict(os.environ, self._prime_env(), clear=False):
            with self.assertRaises(RuntimeError) as caught:
                self.use_table({"prime": {"vars": ("HOME",)}})
                self._recorder("prime-agent", tail="sleep 10")
                critic.call_prime_shell("prompt", None, 1, str(self.workdir))
        self.assertIn("timed out", str(caught.exception))
        self.assertFalse(os.path.exists(self._captured_env(1)["HOME"]))

    def test_c_two_prime_calls_see_different_roots(self):
        self.use_table({"prime": {"vars": ("HOME",)}})
        self._recorder("prime-agent")
        with mock.patch.dict(os.environ, self._prime_env(), clear=False):
            critic.call_prime_shell("p1", None, 30, str(self.workdir))
            critic.call_prime_shell("p2", None, 30, str(self.workdir))
        root1 = os.path.dirname(self._captured_env(1)["HOME"])
        root2 = os.path.dirname(self._captured_env(2)["HOME"])
        self.assertNotEqual(root1, root2)
        self.assertFalse(os.path.exists(root1))
        self.assertFalse(os.path.exists(root2))

    def test_c_attempt_variable_changes_nothing(self):
        self.use_table({"prime": {"vars": ("HOME",)}})
        self._recorder("prime-agent")
        with mock.patch.dict(os.environ, self._prime_env(
                **{"SPECSTRIDE_PRIME_OVERLAY": "attempt"}), clear=False):
            critic.call_prime_shell("p1", None, 30, str(self.workdir))
            critic.call_prime_shell("p2", None, 30, str(self.workdir))
        root1 = os.path.dirname(self._captured_env(1)["HOME"])
        root2 = os.path.dirname(self._captured_env(2)["HOME"])
        self.assertNotEqual(root1, root2)
        self.assertFalse(os.path.exists(root1))
        self.assertFalse(os.path.exists(root2))

    # ── (d) call_prime_critic and call_bebop_shell ──────────────────────
    def test_d_prime_critic_normal_return_root_gone(self):
        self.use_table({"prime": {"vars": ("HOME",)}})
        self._recorder("prime-agent")
        env = self._prime_env(**{"SPECSTRIDE_AGENT_STREAM": "false"})
        with mock.patch.dict(os.environ, env, clear=False):
            result = critic.call_prime_critic("prompt", None, 30,
                                              str(self.workdir))
        self.assertEqual(result.status, "success")
        root = os.path.dirname(self._captured_env(1)["HOME"])
        self.assertIn("specstride-overlay.prime.", root)
        self.assertFalse(os.path.exists(root))

    def _write_bebop_sh(self):
        bebop_sh = self.tmp / "bebop.sh"
        bebop_sh.write_text(textwrap.dedent("""
            bebop() {
              n=$(cat "$CAPTURE_DIR/count" 2>/dev/null || echo 0); n=$((n+1))
              echo "$n" > "$CAPTURE_DIR/count"
              env -0 > "$CAPTURE_DIR/env.$n"
              echo 'bebop-ok'
            }
        """))
        return str(bebop_sh)

    def test_d_bebop_shell_signature_env_and_identity(self):
        self.assertEqual(
            list(inspect.signature(critic.call_bebop_shell).parameters),
            ["prompt", "backend", "timeout"])
        self.use_table({"bebop": {"vars": ("HOME",)}})
        home = self.tmp / "fakehome"
        (home / ".config").mkdir(parents=True)
        (home / ".gitconfig").write_text(
            "[user]\n\tname = Op Us\n\temail = op@example.com\n")
        env = {"CAPTURE_DIR": str(self.captures),
               "BEBOP_SH": self._write_bebop_sh(),
               "HOME": str(home),
               "XDG_CONFIG_HOME": str(home / ".config"),
               "GIT_CONFIG_NOSYSTEM": "1"}
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith("GIT_")}
        clean.update(env)
        with mock.patch.dict(os.environ, clean, clear=True):
            out = critic.call_bebop_shell("prompt", "claude", 30)
        self.assertEqual(out, "bebop-ok\n")
        seen = self._captured_env(1)
        root = os.path.dirname(seen["HOME"])
        self.assertIn("specstride-overlay.bebop.", root)
        # IS_SANDBOX and the rest of its own env entries still reach the harness
        self.assertEqual(seen.get("IS_SANDBOX"), "1")
        self.assertEqual(seen.get("BEBOP_SH"), str(self.tmp / "bebop.sh"))
        # the identity was resolved in the critic's current directory (workdir
        # is None): the global config of the env it passed in
        self.assertEqual(seen.get("GIT_AUTHOR_NAME"), "Op Us")
        self.assertEqual(seen.get("GIT_AUTHOR_EMAIL"), "op@example.com")
        self.assertEqual(seen.get("GIT_CONFIG_GLOBAL"),
                         os.path.join(root, "specstride-overlay.gitconfig"))
        self.assertFalse(os.path.exists(root))

    # ── (e) declaration failures and unusable temp roots ────────────────
    def test_e_bad_declaration_raises_runtimeerror_binary_never_ran(self):
        self.use_table({"prime": {"vars": ()}})  # empty vars are invalid
        self._recorder("prime-agent")
        with mock.patch.dict(os.environ, self._prime_env(), clear=False):
            with self.assertRaisesRegex(RuntimeError,
                                        "^prime critic overlay: "):
                critic.call_prime_shell("prompt", None, 30, str(self.workdir))
        self.assertEqual(self._captured_count(), 0)

    def test_e_bebop_bad_declaration_raises_runtimeerror(self):
        self.use_table({"bebop": {"vars": ("PATH",)}})  # PATH is a reserved variable
        self._write_bebop_sh()
        with mock.patch.dict(os.environ,
                             {"CAPTURE_DIR": str(self.captures),
                              "BEBOP_SH": str(self.tmp / "bebop.sh")},
                             clear=False):
            with self.assertRaisesRegex(RuntimeError,
                                        "^bebop critic overlay: "):
                critic.call_bebop_shell("prompt", "claude", 30)
        self.assertEqual(self._captured_count(), 0)

    def test_e_unwritable_tmp_root_raises_runtimeerror_binary_never_ran(self):
        self.use_table({"prime": {"vars": ("HOME",)}})
        self._recorder("prime-agent")
        missing = str(self.tmp / "no-such-tmproot")
        with mock.patch.dict(os.environ, self._prime_env(), clear=False), \
             mock.patch.object(backend_overlay, "_real_tmpdir",
                               return_value=missing):
            with self.assertRaisesRegex(RuntimeError,
                                        "^prime critic overlay: "):
                critic.call_prime_shell("prompt", None, 30, str(self.workdir))
        self.assertEqual(self._captured_count(), 0)

    # ── (f) the empty table: subprocess kwargs are exactly today's ──────
    def test_f_empty_table_subprocess_kwargs_unchanged(self):
        self.use_table({})
        self._recorder("prime-agent")
        self._write_bebop_sh()
        env = self._prime_env(**{"BEBOP_SH": str(self.tmp / "bebop.sh"),
                                 "SPECSTRIDE_AGENT_STREAM": "false"})
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=0,
                                               stdout="critic-ok\n",
                                               stderr="")) as run:
            critic.call_prime_shell("prompt", None, 30, str(self.workdir))
            self.assertNotIn("env", run.call_args.kwargs)  # prime: no env kwarg
            critic.call_prime_critic("prompt", None, 30, str(self.workdir))
            self.assertNotIn("env", run.call_args.kwargs)
            expected_bebop_env = dict(os.environ)
            expected_bebop_env.setdefault("IS_SANDBOX", "1")
            critic.call_bebop_shell("prompt", "claude", 30)
            self.assertEqual(run.call_args.kwargs.get("env"),
                             expected_bebop_env)  # same dict content as today
        self.assertEqual(self._captured_count(), 0)
