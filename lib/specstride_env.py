"""specstride_env.py — Specstride's names and its legacy compatibility (stdlib only).

Specstride was formerly Wiggum. This module is the python mirror of the
compatibility block in specstride-lib.sh; keep the two in step.

  * Environment: SPECSTRIDE_<X> set wins; else the legacy <prefix><X>; else the
    caller's built-in default. ``apply()`` copies every legacy variable into its
    SPECSTRIDE_ name in ``os.environ`` (so plain ``os.environ.get("SPECSTRIDE_X")``
    reads keep working and child processes inherit the new names) and prints ONE
    deprecation line per process when a legacy name was actually used.
  * State dir: ``state_dirname(workdir)`` is STATE_DIRNAME, except a workdir that
    has only the legacy directory keeps using it (never moved: live runs commit into
    it), with a one-line notice per process tree.

Modules that read SPECSTRIDE_* variables call ``apply()`` at import time.
"""

import os
import sys

STATE_DIRNAME = ".specstride"
LEGACY_STATE_DIRNAME = ".wiggum"
ENV_PREFIX = "SPECSTRIDE_"
LEGACY_ENV_PREFIX = "WIGGUM_"
# Exported by specstride-lib.sh too, so a notice printed by bash is not repeated.
STATE_NOTICE_FLAG = "SPECSTRIDE_STATE_NOTICE_SHOWN"

_env_notice_shown = False


def apply(environ=None, stream=None):
    """Map legacy variables onto SPECSTRIDE_ names (new name wins). Returns the
    legacy names that were used. Idempotent; the notice prints once per process."""
    global _env_notice_shown
    env = os.environ if environ is None else environ
    used = []
    for old in sorted(k for k in list(env) if k.startswith(LEGACY_ENV_PREFIX)):
        new = ENV_PREFIX + old[len(LEGACY_ENV_PREFIX):]
        if new in env:
            continue
        env[new] = env[old]
        used.append(old)
    if used and not _env_notice_shown:
        _env_notice_shown = True
        print("specstride: deprecated %s* variables read as %s*: %s (rename them)"
              % (LEGACY_ENV_PREFIX, ENV_PREFIX, " ".join(used)),
              file=stream or sys.stderr)
    return used


def get(name, default=None, environ=None):
    """Read SPECSTRIDE_<name> (``name`` with or without the prefix), falling back to
    the legacy name, then ``default``."""
    env = os.environ if environ is None else environ
    suffix = name[len(ENV_PREFIX):] if name.startswith(ENV_PREFIX) else name
    if ENV_PREFIX + suffix in env:
        return env[ENV_PREFIX + suffix]
    return env.get(LEGACY_ENV_PREFIX + suffix, default)


def state_dirname(workdir, stream=None):
    """The state-dir NAME to use under ``workdir`` (see the module docstring)."""
    workdir = workdir or "."
    if (not os.path.isdir(os.path.join(workdir, STATE_DIRNAME))
            and os.path.isdir(os.path.join(workdir, LEGACY_STATE_DIRNAME))):
        if not os.environ.get(STATE_NOTICE_FLAG):
            os.environ[STATE_NOTICE_FLAG] = "1"
            print("specstride: using legacy state dir %s (no %s/ there; it is not moved)"
                  % (os.path.join(workdir, LEGACY_STATE_DIRNAME), STATE_DIRNAME),
                  file=stream or sys.stderr)
        return LEGACY_STATE_DIRNAME
    return STATE_DIRNAME


def state_dir(workdir, stream=None):
    """``<workdir>/<state_dirname(workdir)>``."""
    return os.path.join(workdir, state_dirname(workdir, stream))
