#!/usr/bin/env python3
"""Legacy environment names keep working (Specstride was formerly Wiggum).

Rule: SPECSTRIDE_<X> set wins; else the legacy WIGGUM_<X>; else the built-in
default. One deprecation line per process when a legacy name was used. Covered on
both sides: the bash mapping in specstride-lib.sh (sourced by the CLI, the
orchestrator and the proposer) and its python mirror lib/specstride_env.py.
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIB_SH = os.path.join(ROOT, "specstride-lib.sh")
SPECSTRIDE = os.path.join(ROOT, "specstride")

# (suffix, built-in default the harness applies, new value, old value)
SAMPLE = [
    ("HOME", "/opt/default-home", "/opt/new-home", "/opt/old-home"),
    ("PROPOSER_TIMEOUT", "3600", "5400", "1800"),
    ("SPEC_FORMAT", "", "speckit-tasks", "native"),
    ("LIVE_DETAIL", "tools", "full", "milestones"),
    ("CRITIC", "claude", "codex", "dsh"),
    ("MAX_REJECTS", "3", "5", "7"),
    ("MAX_ITER", "30", "40", "50"),
    ("LOKI_URL", "http://localhost:3100", "http://new:3100", "http://old:3100"),
    ("OTEL_URL", "http://localhost:4318", "http://new:4318", "http://old:4318"),
    ("LEARNING", "off", "apply", "suggest"),
    ("AGENT_STREAM", "true", "false", "true-old"),
    ("FEATURE", "", "feat-new", "feat-old"),
]
NEW = "SPECSTRIDE_"
OLD = "WIGGUM_"
NOTICE_MARK = "deprecated %s* variables read as %s*" % (OLD, NEW)


def base_env():
    """The caller's env without any Specstride or legacy knobs."""
    return {k: v for k, v in os.environ.items() if not k.startswith((NEW, OLD))}


def notices(stderr):
    return [l for l in stderr.splitlines() if NOTICE_MARK in l]


# ── bash: a tiny harness that sources specstride-lib.sh like every script does ──
def bash_read(extra_env):
    lines = ['. "%s"' % LIB_SH]
    for suffix, default, _new, _old in SAMPLE:
        lines.append('printf "%%s=%%s\\n" %s "${%s%s:-%s}"' % (suffix, NEW, suffix, default))
    # sourcing (i.e. mapping) twice more in one process must not repeat the notice
    lines.append("specstride_env_compat; specstride_env_compat")
    env = base_env()
    env.update(extra_env)
    p = subprocess.run(["bash", "-c", "\n".join(lines)], capture_output=True,
                       text=True, env=env, timeout=20)
    assert p.returncode == 0, p.stderr
    got = dict(l.split("=", 1) for l in p.stdout.splitlines())
    return got, p.stderr


def test_bash_new_name_wins_over_old():
    env = {}
    for suffix, _d, new, old in SAMPLE:
        env[NEW + suffix] = new
        env[OLD + suffix] = old
    got, err = bash_read(env)
    assert got == {s: new for s, _d, new, _o in SAMPLE}
    assert notices(err) == []          # nothing legacy was actually used


def test_bash_old_name_alone_is_honored_with_one_notice():
    got, err = bash_read({OLD + s: old for s, _d, _n, old in SAMPLE})
    assert got == {s: old for s, _d, _n, old in SAMPLE}
    assert len(notices(err)) == 1
    for suffix, *_ in SAMPLE:
        assert OLD + suffix in notices(err)[0]


def test_bash_neither_name_gives_the_default():
    got, err = bash_read({})
    assert got == {s: d for s, d, _n, _o in SAMPLE}
    assert notices(err) == []


def test_bash_dotenv_goes_through_the_same_mapping(tmp_path):
    """The orchestrator's .env sequence: caller env beats .env, and a .env that
    still uses legacy names is mapped after it is sourced."""
    dotenv = tmp_path / ".env"
    dotenv.write_text("%sMAX_ITER=11\n%sCRITIC=codex\n%sLEARNING=apply\n" % (OLD, OLD, NEW))
    script = """
. "%s"
_caller_env="$(export -p)"
set -a; . "%s"; set +a
eval "$_caller_env"
specstride_env_compat
echo "MAX_ITER=${SPECSTRIDE_MAX_ITER:-30}"
echo "CRITIC=${SPECSTRIDE_CRITIC:-claude}"
echo "LEARNING=${SPECSTRIDE_LEARNING:-off}"
""" % (LIB_SH, dotenv)
    env = base_env()
    env[OLD + "CRITIC"] = "dsh"            # the caller's legacy export beats .env
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env=env, timeout=20)
    got = dict(l.split("=", 1) for l in p.stdout.splitlines())
    assert got == {"MAX_ITER": "11", "CRITIC": "dsh", "LEARNING": "apply"}
    assert len(notices(p.stderr)) == 1


def test_orchestrator_maps_again_after_sourcing_dotenv():
    """The harness above mirrors orchestrator.sh; pin that it really does this."""
    src = open(os.path.join(ROOT, "orchestrator.sh")).read()
    i = src.index('eval "$_caller_env"')
    assert "specstride_env_compat" in src[i:i + 300]
    for script in ("orchestrator.sh", "proposer.sh", "specstride"):
        assert '. "$SCRIPT_DIR/specstride-lib.sh"' in open(os.path.join(ROOT, script)).read()


def test_bash_notice_prints_once_across_the_cli_process_tree(tmp_path):
    """A real CLI call (bash + several python children) prints ONE notice: the
    parent exports the mapped names, so children see nothing legacy-only."""
    wd = tmp_path / "proj"
    (wd / ".specstride" / "features" / "default").mkdir(parents=True)
    (wd / ".specstride" / "events.jsonl").write_text(
        json.dumps({"event": "run_start", "run_id": "r1", "phases": "1"}) + "\n")
    env = base_env()
    env.update({OLD + "LIVE_DETAIL": "full", OLD + "TELEMETRY_ENABLED": "false"})
    p = subprocess.run(["bash", SPECSTRIDE, "status", "-w", str(wd)],
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    assert len(notices(p.stderr)) == 1


# ── python: lib/specstride_env.py ────────────────────────────────────────────
PY_PROBE = r"""
import json, os, sys
sys.path.insert(0, %r)
import specstride_env
defaults = json.loads(sys.argv[1])
specstride_env.apply(); specstride_env.apply()        # idempotent, one notice
import critic, present, specstride_spec              # each maps at import too
out = {s: os.environ.get("SPECSTRIDE_" + s, d) for s, d in defaults.items()}
out["_get"] = {s: specstride_env.get(s, d) for s, d in defaults.items()}
print(json.dumps(out))
""" % HERE


def py_read(extra_env):
    env = base_env()
    env.update(extra_env)
    defaults = {s: d for s, d, _n, _o in SAMPLE}
    p = subprocess.run([sys.executable, "-c", PY_PROBE, json.dumps(defaults)],
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out.pop("_get") == out      # get() and the mapped environ agree
    return out, p.stderr


def test_python_new_name_wins_over_old():
    env = {}
    for suffix, _d, new, old in SAMPLE:
        env[NEW + suffix] = new
        env[OLD + suffix] = old
    got, err = py_read(env)
    assert got == {s: new for s, _d, new, _o in SAMPLE}
    assert notices(err) == []


def test_python_old_name_alone_is_honored_with_one_notice():
    got, err = py_read({OLD + s: old for s, _d, _n, old in SAMPLE})
    assert got == {s: old for s, _d, _n, old in SAMPLE}
    assert len(notices(err)) == 1


def test_python_neither_name_gives_the_default():
    got, err = py_read({})
    assert got == {s: d for s, d, _n, _o in SAMPLE}
    assert notices(err) == []


def test_python_helper_unit():
    sys.path.insert(0, HERE)
    import io
    import specstride_env
    env = {OLD + "CRITIC": "dsh", NEW + "MAX_ITER": "9", OLD + "MAX_ITER": "1"}
    assert specstride_env.get("CRITIC", "claude", environ=env) == "dsh"
    assert specstride_env.get("SPECSTRIDE_MAX_ITER", "30", environ=env) == "9"
    assert specstride_env.get("LEARNING", "off", environ=env) == "off"
    buf = io.StringIO()
    assert specstride_env.apply(environ=env, stream=buf) == [OLD + "CRITIC"]
    assert env[NEW + "CRITIC"] == "dsh" and env[NEW + "MAX_ITER"] == "9"


@pytest.mark.parametrize("module", ["critic", "present", "specstride_spec", "learn",
                                    "banner", "agent_stream", "dsh_plugin_requests",
                                    "verification_failure_digest"])
def test_every_env_reading_module_applies_the_mapping(module):
    env = base_env()
    env[OLD + "PROBE_ONLY"] = "x"
    code = ("import sys, os; sys.path.insert(0, %r); import %s; "
            "print(os.environ.get('SPECSTRIDE_PROBE_ONLY'))" % (HERE, module))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "x"
    assert len(notices(p.stderr)) == 1
