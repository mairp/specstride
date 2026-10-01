"""The pinned claude harness (#109): argv, the one --settings JSON, harness_config."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import agent_stream  # noqa: E402
import claude_harness  # noqa: E402
import specstride_env  # noqa: E402
from observability_policy import ObservabilityPolicy  # noqa: E402

LIB = Path(__file__).parent
PROPOSER = LIB.parent / "proposer.sh"

USER = {
    "model": "opus", "effortLevel": "high",
    "env": {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318", "CLAUDE_CODE_ENABLE_TELEMETRY": "1"},
    "enabledPlugins": {"autoharness@autoharness": True},
    "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "touch x"}]}]},
}


def _settings(args):
    assert args.count("--settings") == 1, args
    return json.loads(args[args.index("--settings") + 1])


# ── the helper ───────────────────────────────────────────────────────────────

def test_pinned_args_drop_every_setting_source_and_mcp():
    args = claude_harness.harness_args(inherit=False, model_given=False, user=USER)
    assert args[:3] == ["--setting-sources", "", "--strict-mcp-config"]
    settings = _settings(args)
    # carried: env, effort and (no --model given) the operator's model …
    assert settings == {"env": USER["env"], "effortLevel": "high", "model": "opus"}
    # … never plugins or hooks
    assert "enabledPlugins" not in settings and "hooks" not in settings


def test_pinned_args_leave_model_to_an_explicit_flag():
    settings = _settings(claude_harness.harness_args(inherit=False, model_given=True, user=USER))
    assert "model" not in settings


def test_extra_env_merges_into_the_single_settings_json():
    """claude does not merge two --settings flags (the last replaces the first), so
    caller env (e.g. OTEL_RESOURCE_ATTRIBUTES) must land in the same object."""
    args = claude_harness.harness_args(inherit=False, model_given=True, user=USER,
                                       extra_env={"OTEL_RESOURCE_ATTRIBUTES": "a=b",
                                                  "CLAUDE_CODE_ENABLE_TELEMETRY": "0"})
    env = _settings(args)["env"]
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "a=b"
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "0"  # the caller wins
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://collector:4318"


def test_pinned_without_user_settings_passes_no_settings_flag():
    assert claude_harness.harness_args(inherit=False, model_given=True, user={}) == [
        "--setting-sources", "", "--strict-mcp-config"]


def test_inherit_pins_nothing():
    assert claude_harness.harness_args(inherit=True, model_given=False, user=USER) == []
    args = claude_harness.harness_args(inherit=True, model_given=False, user=USER,
                                       extra_env={"K": "v"})
    assert args == ["--settings", '{"env":{"K":"v"}}']


def _cli(*argv, env=None):
    out = subprocess.run([sys.executable, str(LIB / "claude_harness.py"), "args", *argv],
                         capture_output=True, env=env, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.decode().split("\0")[:-1]


def test_cli_reads_claude_config_dir_and_honours_the_env_opt_in(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps(USER))
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path)}
    env.pop(claude_harness.INHERIT_ENV, None)
    args = _cli("--model-given", "--env", "A=1", env=env)
    assert args[:3] == ["--setting-sources", "", "--strict-mcp-config"]
    assert _settings(args)["env"]["A"] == "1"
    assert _cli(env={**env, claude_harness.INHERIT_ENV: "1"}) == []


def test_cli_degrades_on_an_unreadable_settings_file(tmp_path):
    (tmp_path / "settings.json").write_text("{not json")
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path)}
    env.pop(claude_harness.INHERIT_ENV, None)
    assert _cli("--model-given", env=env) == ["--setting-sources", "", "--strict-mcp-config"]


# ── harness_config, from the child's own init record ─────────────────────────

INIT = {
    "type": "system", "subtype": "init", "model": "claude-haiku", "tools": ["Bash", "Read"],
    "plugins": [{"name": "autoharness", "path": "/p"}],
    "mcp_servers": [{"name": "plugin:autoharness:stage_skill", "status": "connected"}],
    "skills": ["learned-skill"], "slash_commands": ["a", "b"],
}


def test_harness_config_is_taken_from_init(monkeypatch):
    monkeypatch.delenv(claude_harness.INHERIT_ENV, raising=False)
    fields = claude_harness.harness_config(INIT)
    assert fields["plugins"] == ["autoharness"]
    assert fields["mcp_servers"] == ["plugin:autoharness:stage_skill"]
    assert (fields["skills"], fields["slash_commands"], fields["tools"]) == (1, 2, 2)
    assert fields["setting_sources"] == "" and fields["inherit_plugins"] is False
    assert len(fields["fingerprint"]) == 16
    # the fingerprint moves with what the child loaded, and with the opt-in
    assert claude_harness.harness_config(dict(INIT, plugins=[]))["fingerprint"] != fields["fingerprint"]
    monkeypatch.setenv(claude_harness.INHERIT_ENV, "1")
    inherited = claude_harness.harness_config(INIT)
    assert inherited["inherit_plugins"] is True
    assert inherited["setting_sources"] == "user,project,local"
    assert inherited["fingerprint"] != fields["fingerprint"]


def test_harness_config_degrades_on_a_malformed_init():
    odd = {"type": "system", "subtype": "init", "plugins": "nope", "mcp_servers": [None, 3, {}],
           "skills": {"x": 1}, "tools": None}
    fields = claude_harness.harness_config(odd)
    assert fields["plugins"] == [] and fields["mcp_servers"] == [] and fields["tools"] == 0
    assert claude_harness.harness_config("not a dict") is None


def test_an_init_without_plugin_fields_keeps_the_legacy_event_sequence():
    adapter = agent_stream.ClaudeAdapter(ObservabilityPolicy())
    outcome = adapter.consume({"type": "system", "subtype": "init", "model": "m", "tools": []})
    assert [name for name, _ in outcome.events] == ["agent_init"]


def test_the_claude_adapter_emits_harness_config_after_agent_init():
    adapter = agent_stream.ClaudeAdapter(ObservabilityPolicy())
    outcome = adapter.consume(INIT)
    assert [name for name, _ in outcome.events] == ["agent_init", "harness_config"]


# ── end to end: proposer.sh against a fake claude on PATH ────────────────────

FAKE_CLAUDE = r"""#!/bin/bash
printf '%s\0' "$@" > "$ARGV_LOG"
cat > /dev/null
printf '%s\n' "$INIT_LINE"
printf '%s\n' '{"type":"result","subtype":"success","total_cost_usd":0,"num_turns":1,"usage":{"output_tokens":1},"duration_ms":1,"is_error":false,"result":"ok"}'
"""


def _run_proposer(tmp_path, *extra, inherit_env=None, init=INIT, model=True, more_env=None):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake = fake_bin / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text(json.dumps(USER))
    work = tmp_path / "work"
    (work / ".specstride").mkdir(parents=True, exist_ok=True)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("do the phase\n")
    events = work / ".specstride" / "events.jsonl"
    env = {k: v for k, v in os.environ.items()
           if not k.startswith((specstride_env.ENV_PREFIX, specstride_env.LEGACY_ENV_PREFIX,
                                  "CLAUDE_CONFIG_DIR"))}
    env.update({
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "HOME": str(home),
        "SPECSTRIDE_EVENTS": str(events),
        "SPECSTRIDE_WATCHDOG_TICK": "1",
        "SPECSTRIDE_PROPOSER_PROGRESS_TIMEOUT": "0",
        "SPECSTRIDE_PROPOSER_REPEAT_LIMIT": "0",
        "ARGV_LOG": str(tmp_path / "argv.bin"),
        "INIT_LINE": json.dumps(init),
    })
    if inherit_env is not None:
        env[claude_harness.INHERIT_ENV] = inherit_env
    env.update(more_env or {})
    argv = ["bash", str(PROPOSER), "-w", str(work),
            "-e", str(work / ".specstride" / "features" / "f" / "gates" / "GATE1-EVIDENCE.md"),
            "-f", str(prompt), "--backend", "claude", "-n", "1", "-s", "0",
            "--feature", "f", "--phase", "1", "--timeout", "60"]
    if model:
        argv += ["--model", "claude-haiku"]
    subprocess.run(argv + list(extra), text=True, capture_output=True, env=env, timeout=120)
    sent = (tmp_path / "argv.bin").read_bytes().decode().split("\0")[:-1]
    lines = [json.loads(l) for l in events.read_text().splitlines()] if events.exists() else []
    return sent, lines


def test_proposer_pins_the_claude_child_by_default(tmp_path):
    argv, events = _run_proposer(tmp_path)
    assert argv[:4] == ["-p", "--setting-sources", "", "--strict-mcp-config"]
    settings = _settings(argv)
    assert settings["env"] == USER["env"] and "model" not in settings  # --model given
    assert "--disable-slash-commands" in argv and argv[argv.index("--model") + 1] == "claude-haiku"
    harness = [e for e in events if e["event"] == "harness_config"]
    assert len(harness) == 1
    assert harness[0]["plugins"] == ["autoharness"] and harness[0]["inherit_plugins"] is False


def test_proposer_carries_the_operator_model_when_none_is_given(tmp_path):
    argv, _ = _run_proposer(tmp_path, model=False)
    assert _settings(argv)["model"] == "opus" and "--model" not in argv


@pytest.mark.parametrize("how", ["flag", "env"])
def test_proposer_inherit_plugins_opt_in(tmp_path, how):
    if how == "flag":
        argv, events = _run_proposer(tmp_path, "--inherit-plugins")
    else:
        argv, events = _run_proposer(tmp_path, inherit_env="1")
    assert "--setting-sources" not in argv and "--strict-mcp-config" not in argv
    assert "--settings" not in argv
    harness = [e for e in events if e["event"] == "harness_config"]
    assert harness and harness[0]["inherit_plugins"] is True


def test_proposer_survives_a_malformed_init(tmp_path):
    odd = {"type": "system", "subtype": "init", "plugins": 7, "mcp_servers": "x"}
    _argv, events = _run_proposer(tmp_path, init=odd)
    names = [e["event"] for e in events]
    assert "agent_result" in names
    harness = [e for e in events if e["event"] == "harness_config"]
    assert harness and harness[0]["plugins"] == []


def test_trace_attributes_ride_in_the_one_pinned_settings_json(tmp_path):
    """#112: the run-trace nesting sets OTEL_RESOURCE_ATTRIBUTES for the child. A
    second --settings flag would replace the pinned one whole (claude does not
    merge them), dropping the operator's env and model; it must go through the
    helper into the same object."""
    import ralph_otel_spans
    state = tmp_path / "otel-state"
    state.mkdir()
    tracker = ralph_otel_spans.SpanTracker({}, state_dir=str(state))
    tracker.on_event("run_start", {"run_id": "run-112", "ts": "100.0"})
    tracker.on_event("proposer_start", {"run_id": "run-112", "ts": "101.0", "phase": "1",
                                        "attempt": "1"})
    traced = {"SPECSTRIDE_OTEL_ENABLED": "true", "SPECSTRIDE_RUN_ID": "run-112",
              "SPECSTRIDE_OTEL_STATE_DIR": str(state)}
    argv, _ = _run_proposer(tmp_path, model=False, more_env=traced)
    env = _settings(argv)["env"]  # asserts exactly one --settings
    assert "specstride.run_id=run-112" in env["OTEL_RESOURCE_ATTRIBUTES"]
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == USER["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"]
    assert _settings(argv)["model"] == "opus"
    assert argv[:4] == ["-p", "--setting-sources", "", "--strict-mcp-config"]
