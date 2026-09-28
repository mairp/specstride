#!/usr/bin/env python3
"""The throwaway config-home overlay for backend harness processes.

A proposer pass (or a critic call) can give its backend a private, disposable
HOME-like directory tree instead of the operator's real one, so the backend
never mutates the operator's config and leaves nothing behind. Which variables
a backend wants overlaid is DECLARED per backend, and the only source of those
declarations is the backend registry in lib/backends.py: each registry entry
may carry an optional `overlay` value of the shape
{"vars": ("HOME", "XDG_CONFIG_HOME", ...), "seeds": ({...}, ...)}.

DECLARATIONS below is DERIVED from that registry — it is never a hand-written
table, so there is exactly one place a backend's overlay is described. In the
shipped tree no registry entry sets `overlay`, so the table is empty and every
backend runs exactly as before.

This module owns, in one place:

- validating the declaration table (once per process, the first time it is
  used — a bad entry stops every run before pass 1, so it cannot hide);
- the per-backend scope setting: which environment variable selects the scope
  (SPECSTRIDE_<BACKEND>_OVERLAY), which values are accepted (unset/empty,
  "pass" or "attempt"), and the warning printed when a backend without a
  declaration is given a valid value;
- creating, applying and removing overlay trees, and reaping/sweeping the ones
  an owner that was hard-killed left behind.

It is stdlib-only apart from its sibling `backends`, works as a CLI
(`python3 lib/backend_overlay.py <verb> ...`) for the proposer and
orchestrator shells, and as an importable module (`import backend_overlay`)
for the critic — exactly the way lib/critic.py reaches its siblings.

The registry import is guarded: a missing or broken lib/backends.py must
degrade to "no backend has an overlay" (the table is simply empty and the
scope word is `none` for every backend, exit 0), never to a traceback.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass, field

try:  # the registry is the only source of overlay declarations
    import backends  # noqa: E402  (sibling module; lib/ is on sys.path)
    DECLARATIONS = {b.name: b.overlay for b in backends.REGISTRY
                    if b.overlay is not None}
except Exception:  # a missing or erroring registry = nobody declares an overlay
    DECLARATIONS = {}


class DeclarationError(ValueError):
    """The declaration table is invalid (names the backend and the field)."""


class ScopeError(ValueError):
    """The scope variable holds a value outside pass/attempt."""


class OverlayError(OSError):
    """An overlay tree could not be created/removed (names backend + cause)."""


# The XDG base directories. When a declaration overlays HOME, every one of
# these that the declaration does NOT itself overlay is explicitly unset for
# the harness, so no half-real, half-overlay config mix can happen. The
# overlay's own global git config is a separate, always-present file and is
# never part of `unset`.
XDG_BASE_VARS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME", "XDG_STATE_HOME")

# Variables a declaration may never name: they carry launch-critical or
# identity state the harness must keep from the real environment.
_RESERVED_VARS = ("PATH", "PWD", "SHELL",
                  "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                  "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")

_KEY_RE = re.compile(r"^[a-z][a-z0-9]*$")
_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SEED_KEYS = {"var", "path", "content"}
_CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|passw(or)?d|bearer|authorization)\b")


@dataclass(frozen=True)
class Seed:
    """One file to place inside the overlay: relative path under `var`'s dir."""
    var: str
    path: str
    content: str


@dataclass(frozen=True)
class Declaration:
    """One backend's overlay: the vars to redirect, the seeds, the unsets."""
    key: str
    vars: tuple
    seeds: tuple = ()
    # Derived: the XDG base dirs to explicitly unset (only meaningful when
    # HOME itself is overlaid). Computed on construction when left None.
    unset: tuple = field(default=None)

    def __post_init__(self):
        if self.unset is None:
            if "HOME" in self.vars:
                object.__setattr__(
                    self, "unset",
                    tuple(v for v in XDG_BASE_VARS if v not in self.vars))
            else:
                object.__setattr__(self, "unset", ())


# Whole-table validation runs once per process, the first time it is needed;
# every later call reuses the result.
_DECLARATIONS_CACHE = None


def _fail(key, message):
    raise DeclarationError("overlay declaration '%s': %s" % (key, message))


def _validate_entry(key, entry):
    """Check one declaration against every rule; return a frozen Declaration."""
    if not isinstance(key, str) or not _KEY_RE.match(key) or ":" in key:
        _fail(key, "key must be a registry name without qualifier")
    if key == "dsh":
        _fail(key, "dsh keeps its DSH_HOME overlay and cannot declare one")
    if not isinstance(entry, dict):
        _fail(key, "missing 'vars'")
    for field_name in entry:
        if field_name not in ("vars", "seeds"):
            _fail(key, "unknown field '%s'" % (field_name,))
    if "vars" not in entry:
        _fail(key, "missing 'vars'")
    raw_vars = entry["vars"]
    if not isinstance(raw_vars, (tuple, list)) or not raw_vars:
        _fail(key, "bad variable ''")
    variables = []
    for name in raw_vars:
        if not isinstance(name, str) or not _VAR_RE.match(name):
            _fail(key, "bad variable '%s'" % (name,))
        if name in _RESERVED_VARS or name.startswith("SPECSTRIDE_"):
            _fail(key, "variable '%s' is reserved" % (name,))
        if name in variables:
            _fail(key, "duplicate variable '%s'" % (name,))
        variables.append(name)
    raw_seeds = entry.get("seeds", ())
    if not isinstance(raw_seeds, (tuple, list)):
        _fail(key, "seed #0 must have var, path, content")
    seeds = []
    seen = set()
    for index, raw in enumerate(raw_seeds):
        if not isinstance(raw, dict) or set(raw) != _SEED_KEYS:
            _fail(key, "seed #%d must have var, path, content" % index)
        s_var, s_path, s_content = raw["var"], raw["path"], raw["content"]
        if s_var not in variables:
            _fail(key, "seed '%s:%s' names undeclared variable '%s'"
                  % (s_var, s_path, s_var))
        bad_path = (
            not isinstance(s_path, str) or not s_path
            or s_path.startswith("/")
            or "\x00" in s_path
            or any(part in ("..", ".", "") for part in s_path.split("/")))
        if bad_path:
            _fail(key, "seed '%s:%s' path must be relative without '..'"
                  % (s_var, s_path))
        if not isinstance(s_content, str) or _CREDENTIAL_RE.search(s_content):
            _fail(key, "seed '%s:%s' content looks like a credential; "
                       "seeds must not carry secrets" % (s_var, s_path))
        if (s_var, s_path) in seen:
            _fail(key, "duplicate seed '%s:%s'" % (s_var, s_path))
        seen.add((s_var, s_path))
        seeds.append(Seed(var=s_var, path=s_path, content=s_content))
    return Declaration(key=key, vars=tuple(variables), seeds=tuple(seeds))


def load_declarations():
    """Validate the whole declaration table once per process and cache it."""
    global _DECLARATIONS_CACHE
    if _DECLARATIONS_CACHE is None:
        _DECLARATIONS_CACHE = {key: _validate_entry(key, entry)
                               for key, entry in DECLARATIONS.items()}
    return _DECLARATIONS_CACHE


def declaration_key(backend):
    """The registry name without its qualifier: 'prime:qwen' -> 'prime'."""
    return backend.split(":", 1)[0]


def declaration_for(backend):
    """Look up one backend's declaration; None when it declares none."""
    return load_declarations().get(declaration_key(backend))


def scope_var(backend):
    """The scope variable for a backend: SPECSTRIDE_<KEY>_OVERLAY.

    None when the upper-cased registry name is not a valid shell identifier.
    """
    upper = declaration_key(backend).upper()
    if not _VAR_RE.match(upper):
        return None
    return "SPECSTRIDE_%s_OVERLAY" % (upper,)


def _scope_is_valid(value):
    # unset, empty, "pass" and "attempt" are all accepted spellings.
    return value in ("", "pass", "attempt")


def _scope_is_explicit(value):
    return value in ("pass", "attempt")


def scope_for(backend, environ):
    """Resolve the scope word for a backend from the environment.

    One of "none", "pass" or "attempt". The value is validated for EVERY
    backend (a bogus value is an error even where it would have no effect);
    a backend without a declaration always resolves to "none".
    """
    var = scope_var(backend)
    raw = environ.get(var) if var is not None else None
    if raw is not None and not _scope_is_valid(raw):
        raise ScopeError("%s='%s' is invalid; accepted values: pass, attempt"
                         % (var, raw))
    if declaration_for(backend) is None:
        return "none"
    return raw or "pass"


def scope_warning(backend, environ):
    """The warning for setting a scope on a backend that declares no overlay.

    Returns the warning text only when the scope variable is EXPLICITLY set
    to a valid non-empty value ("pass" or "attempt") and the backend has no
    declaration — otherwise None. (The caller prints it on stderr.)
    """
    if declaration_for(backend) is not None:
        return None
    var = scope_var(backend)
    raw = environ.get(var) if var is not None else None
    if not _scope_is_explicit(raw):
        return None
    return ("specstride: warning: %s is set but backend '%s' has no overlay; "
            "ignoring it" % (var, declaration_key(backend)))


# ── creating, applying and removing overlay trees (US1) ─────────────────────

import contextlib  # noqa: E402
import json  # noqa: E402  (stdlib; kept next to the tree operations it serves)
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

MARKER_NAME = "specstride-overlay.json"
MARKER_CONTRACT = "specstride-overlay/v1"
ENV_FILE_NAME = "specstride-overlay.env"
GITCONFIG_NAME = "specstride-overlay.gitconfig"
# Shown verbatim to the operator when an overlay cannot be created (the creation-failure rule).
CREATION_FAILURE = ("proposer.sh: could not create overlay for backend "
                    "'%(backend)s' in %(dir)s: %(cause)s")
# One line per stale overlay the startup sweep removes (research R4).
SWEEP_LINE = "specstride: removed stale overlay %(root)s (owner %(pid)s gone)"
# One line when removal fails after the chmod-and-retry (the removal-warning rule).
REMOVAL_FAILURE = ("specstride: warning: could not remove overlay "
                   "%(root)s: %(cause)s")


@dataclass(frozen=True)
class Overlay:
    """A created overlay tree, for Python callers (the critic)."""
    root: str
    backend: str
    scope: str


def _real_tmpdir(environ):
    """The operator's real temp directory, before any overlay applies."""
    return environ.get("TMPDIR") or "/tmp"


def _proc_start_time(pid):
    """Field 22 (starttime) of /proc/<pid>/stat, or None when unreadable.

    The comm field can hold spaces and parentheses, so the split happens
    after the LAST `)`; starttime is then field 22 overall (index 20 of the
    remainder, which starts at field 3).
    """
    try:
        with open("/proc/%d/stat" % int(pid), "rb") as handle:
            data = handle.read().decode("utf-8", "surrogateescape")
        rest = data[data.rindex(")") + 2:].split()
        return int(rest[20])
    except (OSError, ValueError, IndexError):
        return None


def owner_is_live(pid, start):
    """Whether (pid, start) names a running owner (data-model §2, research R4).

    Live = the PID exists (kill 0 succeeds or raises EPERM) AND either the
    recorded start is null, or the current start can't be read, or they are
    equal. Anything else is dead.
    """
    try:
        os.kill(int(pid), 0)
    except PermissionError:  # exists, owned by someone else
        pass
    except OSError:
        return False
    if start is None:
        return True
    current = _proc_start_time(pid)
    if current is None:
        return True
    return current == int(start)


def _write_atomic(path, data):
    """Write bytes to `<path>.tmp` then os.replace — never a half file."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _seed_target_ok(var_dir, target):
    """The seed's resolved path must stay under the var dir (no symlink escape)."""
    try:
        resolved = os.path.realpath(target)
        allowed = os.path.realpath(var_dir)
    except OSError:
        return False
    return resolved == allowed or resolved.startswith(allowed + os.sep)


# ── git identity (US2, research R7) ──────────────────────────────────────────

# Each exported variable, in the order the env file records them, with the
# git config keys it resolves from (first set wins; the generic `user.*` key
# comes second). Never `git var`: it invents a hostname-derived identity when
# nothing is configured, and an identity is never invented here.
GIT_IDENTITY_KEYS = (
    ("GIT_AUTHOR_NAME", "author.name", "user.name"),
    ("GIT_AUTHOR_EMAIL", "author.email", "user.email"),
    ("GIT_COMMITTER_NAME", "committer.name", "user.name"),
    ("GIT_COMMITTER_EMAIL", "committer.email", "user.email"),
)

_GIT_TIMEOUT = 10  # seconds; `git config --get` reads a file, it never hangs


def _git_config_get(environ, workdir, key):
    """One `git config --get <key>` in the REAL (pre-overlay) environment.

    Without `-C` when `workdir` is None (the bebop critic resolves in the
    caller's cwd that way). Returns the stripped value, or None when unset.
    """
    command = ["git"]
    if workdir:
        command += ["-C", str(workdir)]
    command += ["config", "--get", key]
    result = subprocess.run(command, env=dict(environ), check=False,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=_GIT_TIMEOUT)
    if result.returncode != 0:
        return None
    value = result.stdout.decode("utf-8", "surrogateescape").strip()
    return value or None


def resolve_git_identity(workdir, environ):
    """The GIT_AUTHOR_* / GIT_COMMITTER_* values to export (research R7).

    For each of the four variables: the specific key first (`author.name`),
    then the generic `user.*` one — which is exactly git's own precedence,
    repository config before global, because `git config --get` applies it.
    Runs in `environ`, the operator's real environment BEFORE any overlay
    variable exists. Only set values are returned, and never one the operator
    already exported (that one passes through the inherited environment
    untouched). Nothing is configured, git is missing, or the subprocess
    fails → {} (an identity is never invented). No config file is
    ever copied into the overlay.
    """
    identity = {}
    for var, specific, generic in GIT_IDENTITY_KEYS:
        if var in environ:
            continue
        for key in (specific, generic):
            try:
                value = _git_config_get(environ, workdir, key)
            except (OSError, subprocess.SubprocessError):
                return {}
            if value:
                identity[var] = value
                break
    return identity


def populate(root, backend, scope, owner_pid, workdir, environ):
    """Fill an existing empty 0700 directory as one backend's overlay.

    Order (contracts/helper-interfaces.md §A): marker → one 0700 subdirectory
    per declared var → seeds (0600, parents 0700, resolved path pinned under
    root/<var>) → the git identity records → the NUL-delimited env file.
    Raises OverlayError naming the backend and the cause; the CALLER removes
    `root` on failure.
    """
    decl = declaration_for(backend)
    if decl is None:
        raise OverlayError("backend '%s' declares no overlay" % (backend,))
    root = os.fspath(root)

    def fail(exc):
        raise OverlayError("could not populate overlay for backend '%s': %s"
                           % (backend, exc)) from exc

    try:
        # 1. the marker, first, so a half-populated tree is still recognisable
        marker = {
            "contract": MARKER_CONTRACT,
            "backend": decl.key,
            "scope": scope,
            "owner_pid": int(owner_pid),
            "owner_start": _proc_start_time(owner_pid),
            "created_at": time.time(),
            "vars": list(decl.vars),
        }
        _write_atomic(os.path.join(root, MARKER_NAME),
                      json.dumps(marker).encode("utf-8"))
        # 2. one private 0700 subdirectory per declared variable
        var_dirs = {}
        for var in decl.vars:
            var_dir = os.path.join(root, var)
            os.mkdir(var_dir, 0o700)
            var_dirs[var] = var_dir
        # 3. the seeds
        for seed in decl.seeds:
            target = os.path.join(var_dirs[seed.var], seed.path)
            parent = os.path.dirname(target)
            parts = []
            while parent and not os.path.isdir(parent):
                parts.append(parent)
                parent = os.path.dirname(parent)
            for part in reversed(parts):
                os.mkdir(part, 0o700)
            if not _seed_target_ok(var_dirs[seed.var], target):
                raise OverlayError(
                    "seed '%s:%s' resolves outside %s"
                    % (seed.var, seed.path, var_dirs[seed.var]))
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, seed.content.encode("utf-8"))
            finally:
                os.close(fd)
        # 4. the env file: every unset first, then every set (harness-env.md)
        records = bytearray()
        for name in decl.unset:
            records += b"unset\0" + name.encode("utf-8") + b"\0"
        for var in decl.vars:
            records += (b"set\0" + var.encode("utf-8") + b"\0"
                        + os.path.join(root, var).encode("utf-8") + b"\0")
        records += (b"set\0GIT_CONFIG_GLOBAL\0"
                    + os.path.join(root, GITCONFIG_NAME).encode("utf-8") + b"\0")
        # 5. the operator's git identity (US2), resolved in the real
        # environment before any overlay variable existed
        for var, value in resolve_git_identity(workdir, environ).items():
            records += (b"set\0" + var.encode("utf-8") + b"\0"
                        + value.encode("utf-8", "surrogateescape") + b"\0")
        _write_atomic(os.path.join(root, ENV_FILE_NAME), bytes(records))
    except OverlayError:
        raise
    except OSError as exc:
        fail(exc)


def _read_env_file(root):
    """Parse `<root>/specstride-overlay.env` into ordered records."""
    path = os.path.join(os.fspath(root), ENV_FILE_NAME)
    with open(path, "rb") as handle:
        data = handle.read()
    fields = data.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    records = []
    i = 0

    def text(raw):
        return raw.decode("utf-8", "surrogateescape")

    while i < len(fields):
        kind = text(fields[i])
        i += 1
        if kind == "unset":
            if i >= len(fields):
                raise OverlayError("truncated 'unset' record in %s" % path)
            records.append(("unset", text(fields[i]), None))
            i += 1
        elif kind == "set":
            if i + 1 >= len(fields):
                raise OverlayError("truncated 'set' record in %s" % path)
            records.append(("set", text(fields[i]), text(fields[i + 1])))
            i += 2
        else:
            raise OverlayError("unknown overlay env record kind '%s' in %s"
                               % (kind, path))
    return records


def harness_env(overlay_or_root, base_env):
    """The environment the harness sees: base minus unsets, plus the sets.

    Every other variable — PATH, credentials, SPECSTRIDE_* — passes through
    unchanged. Accepts an Overlay or a root path.
    """
    root = (overlay_or_root.root if isinstance(overlay_or_root, Overlay)
            else os.fspath(overlay_or_root))
    env = dict(base_env)
    for kind, name, value in _read_env_file(root):
        if kind == "unset":
            env.pop(name, None)
        else:
            env[name] = value
    return env


def make_overlay(backend, scope, owner_pid, workdir, environ, tmp_root=None):
    """Create a fresh overlay tree; remove it and re-raise on failure."""
    key = declaration_key(backend)
    try:
        root = tempfile.mkdtemp(prefix="specstride-overlay.%s." % key,
                                dir=tmp_root or _real_tmpdir(environ))
    except OSError as exc:
        # the critic helper turns every overlay-creation failure into
        # RuntimeError, so an unusable temp root is an OverlayError like any
        # other creation failure
        raise OverlayError("could not create overlay for backend '%s' in %s: %s"
                           % (key, tmp_root or _real_tmpdir(environ), exc)) from exc
    try:
        populate(root, backend, scope, owner_pid, workdir, environ)
    except Exception:
        remove_overlay(root)
        raise
    return Overlay(root=root, backend=key, scope=scope)


@contextlib.contextmanager
def critic_overlay(backend, workdir, environ=None):
    """One CLI critic call's overlay: a fresh tree, removed on the way out.

    Yields None when the backend declares no overlay (the caller keeps its
    subprocess kwargs exactly as they were), else the environment dict the
    harness process must see. The scope variable (SPECSTRIDE_*_OVERLAY) is
    deliberately NOT consulted: a critic call is always per call. The tree is
    removed when the block exits, whether normally, by exception, or through a
    subprocess timeout the caller turns into another exception.
    """
    if environ is None:
        environ = os.environ
    decl = load_declarations().get(declaration_key(backend))
    if decl is None:
        yield None
        return
    overlay = make_overlay(backend, "critic", os.getpid(), workdir,
                           dict(environ))
    try:
        yield harness_env(overlay, dict(environ))
    finally:
        remove_overlay(overlay.root)


def _rmtree(root, handler):
    """shutil.rmtree with whichever error-handler spelling this Python has."""
    kwargs = ({"onexc": handler}
              if "onexc" in shutil.rmtree.__doc__ or sys.version_info >= (3, 12)
              else {"onerror": handler})
    shutil.rmtree(root, **kwargs)


def remove_overlay(root, *, warn=sys.stderr):
    """Remove an overlay tree; one chmod-and-retry per entry, never raise.

    Returns True when the tree is gone, False after printing exactly one
    warning line naming the root (the removal-warning rule). Symlinks are unlinked,
    never followed.
    """
    root = os.fspath(root)
    errors = []
    retried = set()

    def handler(func, path, exc):
        message = exc if isinstance(exc, OSError) else exc[1]
        if path in retried:
            if not errors:
                errors.append(str(message))
            return
        retried.add(path)
        try:
            parent = os.path.dirname(path)
            os.chmod(parent, os.lstat(parent).st_mode | 0o700)
            os.chmod(path, os.lstat(path).st_mode | 0o600)
        except OSError:
            pass
        try:
            func(path)
        except OSError as second:
            if not errors:
                errors.append(str(second))

    try:
        _rmtree(root, handler)
    except OSError as exc:  # e.g. the root itself vanished / is a symlink
        if not errors:
            errors.append(str(exc))
    if not os.path.exists(root) and not os.path.islink(root):
        return True
    print(REMOVAL_FAILURE % {"root": root,
                             "cause": errors[0] if errors else "unknown error"},
          file=warn)
    return False


def _marker(root):
    """The overlay's marker dict, or None when absent / foreign / unparsable."""
    try:
        with open(os.path.join(root, MARKER_NAME), "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("contract") != MARKER_CONTRACT:
        return None
    return data


def _candidate_roots(tmp_root):
    """Glob the real temp dir; yield (root, marker) we own as real directories."""
    for path in sorted(glob.glob(os.path.join(tmp_root, "specstride-overlay.*"))):
        try:
            info = os.lstat(path)  # lstat: never reach through a symlink
            if not stat.S_ISDIR(info.st_mode):
                continue
            if info.st_uid != os.getuid():
                continue
            marker = _marker(path)
        except OSError:
            continue
        if marker is not None:
            yield path, marker


def reap(owner_pid, scope=None, tmp_root=None):
    """Remove THIS owner's overlays (no liveness check); return the count."""
    owner_pid = int(owner_pid)
    count = 0
    try:
        candidates = list(_candidate_roots(tmp_root or _real_tmpdir(os.environ)))
    except OSError:
        return 0
    for path, marker in candidates:
        try:
            if marker.get("owner_pid") != owner_pid:
                continue
            if scope is not None and marker.get("scope") != scope:
                continue
            if remove_overlay(path):
                count += 1
        except OSError:
            continue
    return count


def sweep(tmp_root=None):
    """Remove overlays whose owner is gone; print one line per removal."""
    count = 0
    try:
        candidates = list(_candidate_roots(tmp_root or _real_tmpdir(os.environ)))
    except OSError:
        return 0
    for path, marker in candidates:
        try:
            pid = marker.get("owner_pid")
            if pid is None or not isinstance(pid, int):
                continue
            if owner_is_live(pid, marker.get("owner_start")):
                continue
            print(SWEEP_LINE % {"root": path, "pid": pid}, file=sys.stderr)
            if remove_overlay(path):
                count += 1
        except OSError:
            continue
    return count


# ── CLI (the shells reach this module only through here) ─────────────────────

def _run_scope_command(backend, warn=True):
    """Print the scope word; print the no-declaration warning when it applies."""
    scope = scope_for(backend, os.environ)
    print(scope)
    if warn:
        warning = scope_warning(backend, os.environ)
        if warning:
            print(warning, file=sys.stderr)


def _main(argv):
    parser = argparse.ArgumentParser(
        prog="backend_overlay.py",
        description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True,
                                metavar="{startup,scope,populate,remove,reap,sweep}")
    p_startup = sub.add_parser(
        "startup", help="sweep stale overlays, validate the declarations, "
                        "print the scope word")
    p_startup.add_argument("backend")
    p_scope = sub.add_parser("scope", help="print the scope word")
    p_scope.add_argument("backend")
    p_scope.add_argument("--quiet", action="store_true",
                         help="suppress the no-declaration warning")
    p_populate = sub.add_parser(
        "populate", help="fill an existing empty directory as an overlay")
    p_populate.add_argument("backend")
    p_populate.add_argument("dir")
    p_populate.add_argument("--scope", required=True, choices=("pass", "attempt"))
    p_populate.add_argument("--owner-pid", type=int, required=True)
    p_populate.add_argument("--workdir", required=True)
    p_remove = sub.add_parser("remove", help="remove one overlay tree")
    p_remove.add_argument("root")
    p_reap = sub.add_parser("reap", help="remove one owner's overlays")
    p_reap.add_argument("--owner-pid", type=int, required=True)
    p_reap.add_argument("--scope", default=None)
    sub.add_parser("sweep", help="remove overlays whose owner is gone")
    args = parser.parse_args(argv)
    if args.verb == "populate":
        try:
            populate(args.dir, args.backend, args.scope, args.owner_pid,
                     args.workdir, dict(os.environ))
        except (OverlayError, DeclarationError) as exc:
            print(CREATION_FAILURE % {"backend": args.backend, "dir": args.dir,
                                      "cause": exc}, file=sys.stderr)
            return 1
        return 0
    if args.verb == "remove":
        remove_overlay(args.root)
        return 0
    if args.verb == "reap":
        reap(args.owner_pid, scope=args.scope)
        return 0
    if args.verb == "sweep":
        sweep()
        return 0
    try:
        if args.verb == "startup":
            sweep()
            load_declarations()
            _run_scope_command(args.backend)
        else:
            load_declarations()
            _run_scope_command(args.backend, warn=not args.quiet)
    except (DeclarationError, ScopeError) as exc:
        print(exc, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
