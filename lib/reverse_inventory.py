#!/usr/bin/env python3
"""reverse_inventory.py — the deterministic ground truth of a reverse run.

`specstride --reverse <SRC>` asks an agent to describe a codebase as it is. What
*exists* in that codebase is not a matter for the agent's judgement, so it is
settled here first, without an LLM: a stdlib walk of ``<SRC>`` that records every
file, its size, line count, language and role, the project's manifests and
workspaces, and a fingerprint the source-untouched guard (``reverse.py guard``)
compares against after every phase.

    python3 lib/reverse_inventory.py <SRC> --out <inventory.json> [--md <INVENTORY.md>]
                                     [--exclude <path>]...

The walk
--------
* A git work tree is listed with ``git ls-files -co --exclude-standard`` (tracked
  plus untracked-but-not-ignored). Anything else is walked with ``os.walk``,
  honouring a root ``.gitignore`` (a simple glob subset) and ``DENY_DIRS``.
* The state directories (``.specstride``, its legacy name, Lisa's ``.lisa``) and every
  ``--exclude`` path are never walked in either mode.
* Symlinks are never followed. One that resolves inside ``<SRC>`` is a file with
  ``symlink: true`` and its target; one that escapes is skipped with reason
  ``symlink-escape``.
* A file with a NUL byte in its first 8 KB is ``binary``; one larger than
  ``SPECSTRIDE_REVERSE_MAX_FILE_BYTES`` (default 1 MiB) is ``oversize``. Both
  are skipped, but still fingerprinted, so a change to them is still caught.

Determinism
-----------
Keys and paths are sorted and the body carries no timestamp: two runs over the
same tree give byte-identical JSON. ``fingerprint`` is a sha256 over
``(path, size, sha256)`` of every walked entry; ``git_head`` is ``HEAD`` or null.

Exit codes: 0 ok, 3 invalid input, 2 usage (argparse).
"""
import argparse
import configparser
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - older interpreters just lose TOML facts
    tomllib = None

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import specstride_env  # noqa: E402

SCHEMA_VERSION = 1
E_INVALID = 3

DEFAULT_MAX_FILE_BYTES = 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 20 * 1024 * 1024
DEFAULT_MD_BUDGET = 24000
SNIFF_BYTES = 8192

STATE_DIRS = (specstride_env.STATE_DIRNAME, specstride_env.LEGACY_STATE_DIRNAME, ".lisa")
DENY_DIRS = (".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
             "target", "vendor", "testautomation") + STATE_DIRS

LANGUAGES = {
    ".py": "python", ".pyi": "python", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala", ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".swift": "swift",
    ".lua": "lua", ".pl": "perl", ".r": "r", ".sql": "sql", ".proto": "protobuf",
    ".graphql": "graphql", ".gql": "graphql", ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "css", ".vue": "vue", ".svelte": "svelte",
    ".md": "markdown", ".rst": "rst", ".txt": "text", ".json": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
    ".xml": "xml", ".gradle": "groovy", ".tf": "terraform", ".nix": "nix",
    ".j2": "jinja", ".jinja": "jinja", ".tmpl": "template", ".hbs": "handlebars",
    ".ejs": "ejs", ".mk": "make",
}
FILENAME_LANGUAGES = {
    "Makefile": "make", "GNUmakefile": "make", "Dockerfile": "dockerfile",
    "Containerfile": "dockerfile", "Jenkinsfile": "groovy", "Gemfile": "ruby",
    "Rakefile": "ruby", "Vagrantfile": "ruby", "Justfile": "just", "justfile": "just",
}
SHEBANG_LANGUAGES = (("python", "python"), ("bash", "shell"), ("/sh", "shell"),
                     ("zsh", "shell"), ("node", "javascript"), ("deno", "typescript"),
                     ("ruby", "ruby"), ("perl", "perl"))
CODE_LANGUAGES = {"python", "shell", "javascript", "typescript", "go", "rust", "java",
                  "kotlin", "scala", "ruby", "php", "c", "cpp", "csharp", "swift",
                  "lua", "perl", "r", "vue", "svelte", "html", "css", "groovy"}
CONFIG_LANGUAGES = {"json", "yaml", "toml", "ini", "xml", "make", "dockerfile",
                    "terraform", "nix", "just"}
TEMPLATE_EXT = {".j2", ".jinja", ".tmpl", ".hbs", ".ejs", ".mustache", ".tpl"}
SCHEMA_RE = re.compile(r'(\.schema\.json|\.proto|\.graphql|\.gql|\.avsc|\.xsd|\.sql)$'
                       r'|(^|/)(openapi|swagger|asyncapi)[^/]*\.(ya?ml|json)$'
                       r'|(^|/)(schemas?|migrations)/', re.I)
TEST_RE = re.compile(r'(^|/)(tests?|__tests__|spec|specs?_?tests?|testing)/'
                     r'|(^|/)test_[^/]*\.py$|_test\.(py|go|rb|exs?)$'
                     r'|\.(test|spec)\.[cm]?[jt]sx?$|(^|/)conftest\.py$', re.I)
CI_RE = re.compile(r'^\.github/workflows/|^\.gitlab-ci\.ya?ml$|^\.circleci/'
                   r'|^Jenkinsfile$|^\.travis\.ya?ml$|^azure-pipelines\.ya?ml$'
                   r'|^\.buildkite/|^\.drone\.ya?ml$|^\.woodpecker', re.I)
DOC_NAMES = re.compile(r'^(README|CHANGELOG|CONTRIBUTING|LICENSE|COPYING|NOTICE|AUTHORS'
                       r'|CODE_OF_CONDUCT|SECURITY|AGENTS|CLAUDE)(\.[A-Za-z]+)?$', re.I)

FRAMEWORK_HINTS = {
    # dependency name (lowercase) -> framework label
    "react": "react", "next": "next.js", "vue": "vue", "svelte": "svelte",
    "express": "express", "fastify": "fastify", "koa": "koa", "hono": "hono",
    "@nestjs/core": "nestjs", "vitest": "vitest", "jest": "jest", "mocha": "mocha",
    "typescript": "typescript", "fastapi": "fastapi", "flask": "flask",
    "django": "django", "pytest": "pytest", "click": "click", "typer": "typer",
    "pydantic": "pydantic", "sqlalchemy": "sqlalchemy", "aiohttp": "aiohttp",
    "tokio": "tokio", "actix-web": "actix-web", "axum": "axum", "clap": "clap",
    "serde": "serde", "github.com/gin-gonic/gin": "gin",
    "github.com/spf13/cobra": "cobra", "github.com/labstack/echo": "echo",
}


class InventoryError(Exception):
    pass


# ── small helpers ────────────────────────────────────────────────────────────

def _env_int(name, default, environ):
    raw = specstride_env.get(name, None, environ)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise InventoryError("%s must be a positive integer (got %r)" % (name, raw))
    if value <= 0:
        raise InventoryError("%s must be a positive integer (got %r)" % (name, raw))
    return value


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relpath(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _inside(path, root):
    path = os.path.realpath(path)
    root = os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def is_git_tree(src):
    try:
        out = subprocess.run(["git", "-C", src, "rev-parse", "--is-inside-work-tree",
                              "--show-toplevel"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0:
        return False
    lines = out.stdout.split()
    return bool(lines) and lines[0] == "true" and \
        os.path.realpath(lines[-1]) == os.path.realpath(src)


def git_head(src):
    try:
        out = subprocess.run(["git", "-C", src, "rev-parse", "--verify", "-q", "HEAD"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    head = out.stdout.strip()
    return head if out.returncode == 0 and head else None


def git_status(src):
    """`git status --porcelain` lines (sorted), or None outside a git tree."""
    try:
        out = subprocess.run(["git", "-C", src, "status", "--porcelain=v1", "-z",
                              "--untracked-files=all"],
                             capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    entries = []
    parts = out.stdout.decode("utf-8", "replace").split("\0")
    index = 0
    while index < len(parts):
        item = parts[index]
        index += 1
        if not item:
            continue
        code, path = item[:2], item[3:]
        if code[0] in "RC":          # a rename/copy carries its source next
            index += 1
        entries.append("%s %s" % (code, path))
    return sorted(entries)


# ── ignore rules for the non-git walk ────────────────────────────────────────

def load_gitignore(src):
    """A root .gitignore as (pattern, negate, dir_only, anchored) tuples. This is the
    simple glob subset: no nested .gitignore files, `**` treated as `*`."""
    path = os.path.join(src, ".gitignore")
    rules = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return rules
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        anchored = line.startswith("/") or "/" in line
        line = line.lstrip("/").replace("**/", "").replace("/**", "/*")
        if line:
            rules.append((line, negate, dir_only, anchored))
    return rules


def ignored(rel, is_dir, rules):
    verdict = False
    name = rel.rsplit("/", 1)[-1]
    for pattern, negate, dir_only, anchored in rules:
        if dir_only and not is_dir:
            continue
        hit = fnmatch.fnmatchcase(rel, pattern) if anchored else (
            fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(rel, pattern))
        if hit:
            verdict = not negate
    return verdict


# ── the walk ─────────────────────────────────────────────────────────────────

def _excluded(rel, excludes):
    return any(rel == e or rel.startswith(e + "/") for e in excludes)


def _candidate_paths(src, excludes):
    """Workdir-relative candidate paths, before classification. Returns
    (paths, mode, ignored_dirs)."""
    state_parts = set(STATE_DIRS)
    if is_git_tree(src):
        out = subprocess.run(["git", "-C", src, "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, timeout=300)
        if out.returncode != 0:
            raise InventoryError("git ls-files failed in %s: %s"
                                 % (src, out.stderr.decode("utf-8", "replace").strip()))
        paths = set()
        for rel in out.stdout.decode("utf-8", "surrogateescape").split("\0"):
            if not rel:
                continue
            if state_parts & set(rel.split("/")[:-1]) or _excluded(rel, excludes):
                continue
            paths.add(rel)
        return sorted(paths), "git"
    rules = load_gitignore(src)
    paths = []
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        rel_dir = "" if dirpath == src else _relpath(dirpath, src)
        keep = []
        for name in sorted(dirnames):
            rel = name if not rel_dir else rel_dir + "/" + name
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                # a symlinked directory is recorded as a link, never descended
                if not _excluded(rel, excludes) and not ignored(rel, True, rules):
                    paths.append(rel)
                continue
            if name in DENY_DIRS or _excluded(rel, excludes) or ignored(rel, True, rules):
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            rel = name if not rel_dir else rel_dir + "/" + name
            if _excluded(rel, excludes) or ignored(rel, False, rules):
                continue
            paths.append(rel)
    return sorted(paths), "walk"


def language_of(rel, head):
    name = rel.rsplit("/", 1)[-1]
    if name in FILENAME_LANGUAGES:
        return FILENAME_LANGUAGES[name]
    if name.startswith("Dockerfile") or name.endswith(".dockerfile"):
        return "dockerfile"
    ext = os.path.splitext(name)[1].lower()
    if ext in LANGUAGES:
        return LANGUAGES[ext]
    if head.startswith(b"#!"):
        first = head.split(b"\n", 1)[0].decode("utf-8", "replace")
        for needle, lang in SHEBANG_LANGUAGES:
            if needle in first:
                return lang
    return "unknown"


def role_of(rel, language, head):
    name = rel.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    if CI_RE.search(rel):
        return "ci"
    if TEST_RE.search(rel):
        return "test"
    if SCHEMA_RE.search(rel):
        return "schema"
    if ext in TEMPLATE_EXT or "/templates/" in "/" + rel:
        return "template"
    if DOC_NAMES.match(name) or language in ("markdown", "rst", "text") \
            or rel.startswith("docs/"):
        return "doc"
    if language == "shell" or rel.startswith(("scripts/", "bin/")) or head.startswith(b"#!"):
        return "script"
    if language in CODE_LANGUAGES:
        return "source"
    if language in CONFIG_LANGUAGES or name.startswith((".env", ".editorconfig", ".git")) \
            or name.startswith("compose") or name.startswith("docker-compose"):
        return "config"
    return "other"


def walk(src, excludes=(), environ=None):
    """Walk ``src`` and classify every entry. Returns (files, skipped, mode, total)."""
    environ = dict(os.environ if environ is None else environ)
    max_file = _env_int("SPECSTRIDE_REVERSE_MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES, environ)
    src = os.path.realpath(src)
    excludes = sorted({e.strip("/") for e in excludes if e and e.strip("/")})
    paths, mode = _candidate_paths(src, excludes)
    files, skipped = [], []
    total = 0
    for rel in paths:
        full = os.path.join(src, rel)
        if os.path.islink(full):
            target = os.readlink(full)
            resolved = os.path.realpath(full)
            link_hash = hashlib.sha256(target.encode("utf-8", "surrogateescape")).hexdigest()
            if not _inside(resolved, src):
                skipped.append({"path": rel, "reason": "symlink-escape", "target": target,
                                "bytes": len(target), "sha256": link_hash})
                continue
            files.append({"path": rel, "symlink": True, "target": target,
                          "bytes": len(target), "sha256": link_hash, "lines": 0,
                          "language": "symlink", "role": "other"})
            continue
        if not os.path.isfile(full):
            continue            # tracked but deleted in the work tree; git status shows it
        size = os.path.getsize(full)
        digest = _sha256_file(full)
        with open(full, "rb") as handle:
            head = handle.read(SNIFF_BYTES)
        if b"\0" in head:
            skipped.append({"path": rel, "reason": "binary", "bytes": size, "sha256": digest})
            continue
        if size > max_file:
            skipped.append({"path": rel, "reason": "oversize", "bytes": size,
                            "sha256": digest, "limit": max_file})
            continue
        with open(full, "rb") as handle:
            data = handle.read()
        lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        language = language_of(rel, head)
        files.append({"path": rel, "bytes": size, "lines": lines, "sha256": digest,
                      "language": language, "role": role_of(rel, language, head)})
        total += size
    return files, skipped, mode, total


def fingerprint(files, skipped):
    entries = sorted((e["path"], e["bytes"], e["sha256"]) for e in list(files) + list(skipped))
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


# ── project facts ────────────────────────────────────────────────────────────

def _read_text(path, limit=2 * 1024 * 1024):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return None


def _read_json(path):
    text = _read_text(path)
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value


def _read_toml(path):
    if tomllib is None:
        return None
    text = _read_text(path)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except (ValueError, TypeError):
        return None


def _sorted_names(values):
    return sorted({str(v) for v in values if v})


def _package_json_facts(rel, data):
    facts = {"manifest": rel, "kind": "package.json"}
    if not isinstance(data, dict):
        facts["error"] = "unparseable"
        return facts, set()
    facts["name"] = data.get("name")
    bin_field = data.get("bin")
    if isinstance(bin_field, str):
        facts["bin"] = {str(data.get("name") or ""): bin_field}
    elif isinstance(bin_field, dict):
        facts["bin"] = {str(k): str(v) for k, v in sorted(bin_field.items())}
    exports = data.get("exports")
    targets = []

    def collect(value):
        if isinstance(value, str):
            targets.append(value)
        elif isinstance(value, dict):
            for key in sorted(value):
                collect(value[key])
        elif isinstance(value, list):
            for item in value:
                collect(item)
    collect(exports)
    for key in ("main", "module", "types"):
        if isinstance(data.get(key), str):
            targets.append(data[key])
    if targets:
        facts["exports"] = sorted(set(targets))
    if isinstance(data.get("scripts"), dict):
        facts["scripts"] = {str(k): str(v) for k, v in sorted(data["scripts"].items())}
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if isinstance(workspaces, list):
        facts["workspaces"] = [str(w) for w in workspaces]
    deps = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(data.get(key), dict):
            deps.update(k.lower() for k in data[key])
    return facts, deps


def _pyproject_facts(rel, data, text):
    facts = {"manifest": rel, "kind": "pyproject.toml"}
    deps = set()
    if isinstance(data, dict):
        project = data.get("project") or {}
        poetry = (data.get("tool") or {}).get("poetry") or {}
        facts["name"] = project.get("name") or poetry.get("name")
        scripts = dict(project.get("scripts") or {})
        scripts.update(poetry.get("scripts") or {})
        if scripts:
            facts["scripts"] = {str(k): str(v) for k, v in sorted(scripts.items())}
        for dep in list(project.get("dependencies") or []):
            deps.add(re.split(r'[<>=!~\[; ]', str(dep), maxsplit=1)[0].lower())
        for group in (project.get("optional-dependencies") or {}).values():
            for dep in group:
                deps.add(re.split(r'[<>=!~\[; ]', str(dep), maxsplit=1)[0].lower())
        for key in ("dependencies", "dev-dependencies"):
            deps.update(k.lower() for k in (poetry.get(key) or {}))
        build = (data.get("build-system") or {}).get("build-backend")
        if build:
            facts["build_backend"] = build
    if text and "pytest" in text.lower():
        deps.add("pytest")
    return facts, deps


def _setup_cfg_facts(rel, text):
    facts = {"manifest": rel, "kind": "setup.cfg"}
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text or "")
    except configparser.Error:
        facts["error"] = "unparseable"
        return facts, set()
    if parser.has_option("metadata", "name"):
        facts["name"] = parser.get("metadata", "name")
    if parser.has_option("options.entry_points", "console_scripts"):
        scripts = {}
        for line in parser.get("options.entry_points", "console_scripts").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                scripts[key.strip()] = value.strip()
        facts["scripts"] = dict(sorted(scripts.items()))
    deps = set()
    if parser.has_option("options", "install_requires"):
        for line in parser.get("options", "install_requires").splitlines():
            if line.strip():
                deps.add(re.split(r'[<>=!~\[; ]', line.strip(), maxsplit=1)[0].lower())
    return facts, deps


def _go_mod_facts(rel, text):
    facts = {"manifest": rel, "kind": "go.mod"}
    module = re.search(r'^module\s+(\S+)', text or "", re.M)
    version = re.search(r'^go\s+(\S+)', text or "", re.M)
    if module:
        facts["name"] = module.group(1)
    if version:
        facts["go"] = version.group(1)
    deps = set(m.lower() for m in re.findall(r'^\s*(?:require\s+)?([a-z0-9.\-]+\.[a-z]+/\S+)\s+v',
                                              text or "", re.M))
    return facts, deps


def _cargo_facts(rel, data):
    facts = {"manifest": rel, "kind": "Cargo.toml"}
    deps = set()
    if isinstance(data, dict):
        facts["name"] = (data.get("package") or {}).get("name")
        bins = [b.get("name") for b in data.get("bin") or [] if isinstance(b, dict)]
        if bins:
            facts["bin"] = {str(b): "" for b in sorted(filter(None, bins))}
        members = (data.get("workspace") or {}).get("members")
        if isinstance(members, list):
            facts["workspaces"] = [str(m) for m in members]
        for key in ("dependencies", "dev-dependencies"):
            deps.update(k.lower() for k in (data.get(key) or {}))
    return facts, deps


def _jvm_facts(rel, text):
    facts = {"manifest": rel, "kind": rel.rsplit("/", 1)[-1]}
    artifact = re.search(r'<artifactId>([^<]+)</artifactId>', text or "")
    if artifact:
        facts["name"] = artifact.group(1)
    return facts, set()


def _dockerfile_facts(rel, text):
    facts = {"manifest": rel, "kind": "Dockerfile"}
    for key in ("FROM", "ENTRYPOINT", "CMD", "EXPOSE"):
        values = re.findall(r'^\s*%s\s+(.+?)\s*$' % key, text or "", re.M | re.I)
        if values:
            facts[key.lower()] = values
    return facts, set()


def _compose_facts(rel, text):
    facts = {"manifest": rel, "kind": "compose"}
    services = []
    in_services = False
    for line in (text or "").splitlines():
        if re.match(r'^services:\s*$', line):
            in_services = True
            continue
        if in_services:
            if re.match(r'^\S', line):
                in_services = False
                continue
            m = re.match(r'^  ([A-Za-z0-9_.-]+):\s*$', line)
            if m:
                services.append(m.group(1))
    facts["services"] = sorted(services)
    return facts, set()


def _makefile_facts(rel, text):
    targets = re.findall(r'^([A-Za-z0-9][A-Za-z0-9_./-]*)\s*:(?!=)', text or "", re.M)
    return {"manifest": rel, "kind": "Makefile", "targets": _sorted_names(targets)}, set()


def _workflow_facts(rel, text):
    facts = {"manifest": rel, "kind": "workflow"}
    name = re.search(r'^name:\s*(.+?)\s*$', text or "", re.M)
    if name:
        facts["name"] = name.group(1).strip("'\"")
    jobs, in_jobs = [], False
    for line in (text or "").splitlines():
        if re.match(r'^jobs:\s*$', line):
            in_jobs = True
            continue
        if in_jobs:
            if re.match(r'^\S', line):
                break
            m = re.match(r'^  ([A-Za-z0-9_-]+):\s*$', line)
            if m:
                jobs.append(m.group(1))
    facts["jobs"] = jobs
    return facts, set()


def _pnpm_workspace_globs(text):
    globs, in_packages = [], False
    for line in (text or "").splitlines():
        if re.match(r'^packages:\s*$', line):
            in_packages = True
            continue
        if in_packages:
            m = re.match(r'^\s*-\s*[\'"]?([^\'"#]+?)[\'"]?\s*(#.*)?$', line)
            if m:
                globs.append(m.group(1).strip())
            elif re.match(r'^\S', line):
                in_packages = False
    return globs


def detect_manifests(src, files):
    """Manifest facts, detected frameworks, entry points and workspace units."""
    by_path = {f["path"]: f for f in files}
    manifests, frameworks, entry_points = [], set(), []
    js_workspace_globs, cargo_members = [], []
    for rel in sorted(by_path):
        entry = by_path[rel]
        if entry.get("symlink"):
            continue
        name = rel.rsplit("/", 1)[-1]
        full = os.path.join(src, rel)
        facts = deps = None
        if name == "package.json":
            facts, deps = _package_json_facts(rel, _read_json(full))
            if rel == "package.json" and facts.get("workspaces"):
                js_workspace_globs.extend(facts["workspaces"])
        elif name == "pyproject.toml":
            facts, deps = _pyproject_facts(rel, _read_toml(full), _read_text(full))
        elif name == "setup.cfg":
            facts, deps = _setup_cfg_facts(rel, _read_text(full))
        elif name == "go.mod":
            facts, deps = _go_mod_facts(rel, _read_text(full))
        elif name == "Cargo.toml":
            facts, deps = _cargo_facts(rel, _read_toml(full))
            if rel == "Cargo.toml" and facts.get("workspaces"):
                cargo_members.extend(facts["workspaces"])
        elif name in ("pom.xml", "build.gradle", "build.gradle.kts"):
            facts, deps = _jvm_facts(rel, _read_text(full))
        elif name == "Dockerfile" or name.startswith("Dockerfile.") or name == "Containerfile":
            facts, deps = _dockerfile_facts(rel, _read_text(full))
        elif re.match(r'^(docker-)?compose[^/]*\.ya?ml$', name):
            facts, deps = _compose_facts(rel, _read_text(full))
        elif name in ("Makefile", "GNUmakefile"):
            facts, deps = _makefile_facts(rel, _read_text(full))
        elif rel.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
            facts, deps = _workflow_facts(rel, _read_text(full))
        elif rel == "pnpm-workspace.yaml":
            globs = _pnpm_workspace_globs(_read_text(full))
            js_workspace_globs.extend(globs)
            facts, deps = {"manifest": rel, "kind": "pnpm-workspace", "workspaces": globs}, set()
        if facts is None:
            continue
        manifests.append({k: v for k, v in facts.items() if v not in (None, "", [], {})})
        for dep in deps or ():
            if dep in FRAMEWORK_HINTS:
                frameworks.add(FRAMEWORK_HINTS[dep])
        base = rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""
        for key, target in sorted((facts.get("bin") or {}).items()):
            entry_points.append({"kind": "bin", "name": key, "target": target,
                                 "manifest": rel})
        for key, target in sorted((facts.get("scripts") or {}).items()):
            if facts["kind"] != "package.json":
                entry_points.append({"kind": "console-script", "name": key,
                                     "target": target, "manifest": rel})
        for target in facts.get("exports") or []:
            entry_points.append({"kind": "export", "target": target, "manifest": rel,
                                 "resolved": os.path.normpath(base + target).replace(os.sep, "/")})
        for value in facts.get("entrypoint") or []:
            entry_points.append({"kind": "container-entrypoint", "target": value,
                                 "manifest": rel})
    for entry in files:
        if entry.get("symlink") or entry["role"] not in ("source", "script"):
            continue
        rel = entry["path"]
        text = _read_text(os.path.join(src, rel), 256 * 1024) or ""
        if entry["language"] == "python" and re.search(
                r'^if __name__ == [\'"]__main__[\'"]\s*:', text, re.M):
            entry_points.append({"kind": "python-main", "target": rel})
        elif entry["language"] == "go" and re.search(r'^package main\b', text, re.M) \
                and re.search(r'^func main\(\)', text, re.M):
            entry_points.append({"kind": "go-main", "target": rel})
        elif entry["role"] == "script" and text.startswith("#!"):
            entry_points.append({"kind": "script", "target": rel})
    languages = {}
    for entry in files:
        if entry["language"] not in ("unknown", "symlink"):
            languages[entry["language"]] = languages.get(entry["language"], 0) + 1
    workspaces = detect_workspaces(src, files, js_workspace_globs, cargo_members)
    return {
        "manifests": manifests,
        "languages": dict(sorted(languages.items())),
        "frameworks": sorted(frameworks),
        "entry_points": sorted(entry_points, key=lambda e: json.dumps(e, sort_keys=True)),
        "workspaces": workspaces,
    }


def detect_workspaces(src, files, js_globs, cargo_members):
    """Workspace packages as units (memory: monorepo path resolution). Each glob is
    matched against the directories that hold a manifest of the right kind."""
    units = []
    manifest_dirs = {}
    for entry in files:
        name = entry["path"].rsplit("/", 1)[-1]
        if name in ("package.json", "Cargo.toml") and "/" in entry["path"]:
            manifest_dirs.setdefault(name, set()).add(entry["path"].rsplit("/", 1)[0])
    for kind, globs, manifest in (("js", js_globs, "package.json"),
                                  ("cargo", cargo_members, "Cargo.toml")):
        positive = [g.strip("./").rstrip("/") for g in globs if not g.startswith("!")]
        negative = [g[1:].strip("./").rstrip("/") for g in globs if g.startswith("!")]
        for directory in sorted(manifest_dirs.get(manifest, ())):
            if any(fnmatch.fnmatchcase(directory, g.replace("**", "*")) for g in positive) \
                    and not any(fnmatch.fnmatchcase(directory, g.replace("**", "*"))
                                for g in negative):
                name = None
                if manifest == "package.json":
                    data = _read_json(os.path.join(src, directory, manifest))
                    name = data.get("name") if isinstance(data, dict) else None
                units.append({"kind": kind, "path": directory, "name": name or directory})
    return units


def units_of(files, workspaces):
    """Top-level investigation units: workspace packages when there are any (plus a
    root unit for the rest), else the top-level directories (plus `.` for root files)."""
    units = {}
    if workspaces:
        prefixes = sorted((w["path"] for w in workspaces), key=len, reverse=True)
        for entry in files:
            owner = next((p for p in prefixes if entry["path"].startswith(p + "/")), ".")
            unit = units.setdefault(owner, {"path": owner, "files": 0, "bytes": 0})
            unit["files"] += 1
            unit["bytes"] += entry["bytes"]
    else:
        for entry in files:
            owner = entry["path"].split("/", 1)[0] if "/" in entry["path"] else "."
            unit = units.setdefault(owner, {"path": owner, "files": 0, "bytes": 0})
            unit["files"] += 1
            unit["bytes"] += entry["bytes"]
    return [units[k] for k in sorted(units)]


def specify_facts(src):
    """The existing Spec Kit state: constitution, spec numbers, feature.json. Read
    from the filesystem directly — `specs/` is often git-ignored."""
    root = os.path.join(src, ".specify")
    facts = {
        "present": os.path.isdir(root),
        "constitution": os.path.isfile(os.path.join(root, "memory", "constitution.md")),
        "templates": sorted(n for n in (os.listdir(os.path.join(root, "templates"))
                                        if os.path.isdir(os.path.join(root, "templates"))
                                        else ()) if n.endswith(".md")),
        "feature_json": None,
        "spec_numbers": existing_spec_numbers(src),
    }
    feature = _read_json(os.path.join(root, "feature.json"))
    if isinstance(feature, dict):
        facts["feature_json"] = feature
    return facts


def existing_spec_numbers(src):
    specs = os.path.join(src, "specs")
    if not os.path.isdir(specs):
        return []
    numbers = set()
    for name in os.listdir(specs):
        m = re.match(r'^(\d{3,})-', name)
        if m and os.path.isdir(os.path.join(specs, name)):
            numbers.add(int(m.group(1)))
    return sorted(numbers)


# ── the inventory ────────────────────────────────────────────────────────────

def build(src, excludes=(), environ=None):
    environ = dict(os.environ if environ is None else environ)
    if not os.path.isdir(src):
        raise InventoryError("source is not a directory: %s" % src)
    src = os.path.realpath(src)
    max_total = _env_int("SPECSTRIDE_REVERSE_MAX_TOTAL_BYTES", DEFAULT_MAX_TOTAL_BYTES, environ)
    max_file = _env_int("SPECSTRIDE_REVERSE_MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES, environ)
    files, skipped, mode, total = walk(src, excludes, environ)
    facts = detect_manifests(src, files)
    status = git_status(src) if mode == "git" else None
    return {
        "schema_version": SCHEMA_VERSION,
        "src": src,
        "walk": mode,
        "excludes": sorted({e.strip("/") for e in excludes if e and e.strip("/")}),
        "limits": {"max_file_bytes": max_file, "max_total_bytes": max_total},
        "totals": {"files": len(files), "skipped": len(skipped), "text_bytes": total,
                   "lines": sum(f["lines"] for f in files)},
        "oversize": total > max_total,
        "files": files,
        "skipped": skipped,
        "project": facts,
        "units": units_of(files, facts["workspaces"]),
        "specify": specify_facts(src),
        "fingerprint": fingerprint(files, skipped),
        "git_head": git_head(src) if mode == "git" else None,
        "git_status": status,
    }


def dumps(inventory):
    return json.dumps(inventory, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def render_md(inventory, json_path, budget=DEFAULT_MD_BUDGET):
    """A readable INVENTORY.md capped near ``budget`` characters. When the cap
    bites, per-file rows go first; project facts are kept, and the drop is said."""
    project = inventory["project"]
    head = [
        "# Inventory — %s" % os.path.basename(inventory["src"]),
        "",
        "Deterministic, LLM-free inventory of `%s` (walk: %s). It is the ground truth"
        " for **what exists**. The full record, with every file's hash, is `%s`."
        % (inventory["src"], inventory["walk"], json_path),
        "",
        "- Files: %d text (%d lines, %d bytes); %d skipped"
        % (inventory["totals"]["files"], inventory["totals"]["lines"],
           inventory["totals"]["text_bytes"], inventory["totals"]["skipped"]),
        "- Oversize: %s (limit %d bytes of text)"
        % ("yes" if inventory["oversize"] else "no", inventory["limits"]["max_total_bytes"]),
        "- git HEAD: %s" % (inventory["git_head"] or "not a git work tree"),
        "- Fingerprint: `%s`" % inventory["fingerprint"],
        "- Languages: %s" % (", ".join("%s (%d)" % kv for kv in project["languages"].items())
                             or "none"),
        "- Frameworks: %s" % (", ".join(project["frameworks"]) or "none detected"),
        "- Existing Spec Kit: %s; constitution %s; specs %s"
        % ("yes" if inventory["specify"]["present"] else "no",
           "present" if inventory["specify"]["constitution"] else "absent",
           ", ".join("%03d" % n for n in inventory["specify"]["spec_numbers"]) or "none"),
        "",
        "## Manifests",
        "",
    ]
    for manifest in project["manifests"]:
        rest = {k: v for k, v in manifest.items() if k not in ("manifest", "kind")}
        head.append("- `%s` (%s): %s" % (manifest["manifest"], manifest["kind"],
                                         json.dumps(rest, sort_keys=True)[:600]))
    if not project["manifests"]:
        head.append("- none")
    head += ["", "## Entry points", ""]
    head += ["- %s: `%s`%s" % (e["kind"], e["target"],
                               " (%s)" % e["manifest"] if e.get("manifest") else "")
             for e in project["entry_points"]] or ["- none detected"]
    head += ["", "## Workspaces and units", ""]
    head += ["- workspace %s: `%s` (%s)" % (w["kind"], w["path"], w["name"])
             for w in project["workspaces"]]
    head += ["- unit `%s`: %d files, %d bytes" % (u["path"], u["files"], u["bytes"])
             for u in inventory["units"]]
    head += ["", "## Skipped", ""]
    head += ["- `%s`: %s" % (s["path"], s["reason"]) for s in inventory["skipped"]] or ["- none"]
    head += ["", "## Files", "", "| Path | Lines | Language | Role |", "|---|---|---|---|"]
    rows = ["| `%s` | %d | %s | %s |" % (f["path"], f["lines"], f["language"], f["role"])
            for f in inventory["files"]]
    text = "\n".join(head)
    kept = []
    for row in rows:
        if len(text) + len(row) + 200 > budget:
            break
        kept.append(row)
        text += "\n" + row
    if len(kept) < len(rows):
        text += ("\n\n_%d of %d file rows dropped to stay within %d characters; every "
                 "file is in `%s`._" % (len(rows) - len(kept), len(rows), budget, json_path))
    return text + "\n"


def write_atomic(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="reverse_inventory.py",
                                 description="Deterministic inventory of a source tree")
    ap.add_argument("src")
    ap.add_argument("--out", required=True, help="inventory.json path")
    ap.add_argument("--md", default=None, help="also write INVENTORY.md here")
    ap.add_argument("--exclude", action="append", default=[],
                    help="a SRC-relative path never walked (repeatable)")
    args = ap.parse_args(argv)
    try:
        inventory = build(args.src, args.exclude)
    except InventoryError as exc:
        sys.stderr.write("reverse_inventory: %s\n" % exc)
        return E_INVALID
    write_atomic(args.out, dumps(inventory))
    if args.md:
        budget = _env_int("SPECSTRIDE_CONTEXT_BUDGET", DEFAULT_MD_BUDGET, os.environ)
        write_atomic(args.md, render_md(inventory, os.path.abspath(args.out), budget))
    print(os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
