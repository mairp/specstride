#!/usr/bin/env python3
"""The pinned, minimal harness configuration for every `claude` child.

A `claude -p` started in a workdir loads the operator's user settings — every
enabled plugin with its hooks, MCP servers and SessionStart context — plus the
target repo's project/local settings and CLAUDE.md. A run's behaviour then
depends on whatever the operator switched on for interactive use (issue #109:
a self-learning plugin wrote under the workdir, forked reflector sessions on
the same account and injected a learned-skill index the critic never saw).

This module is the ONE place that decides how a claude child is configured.
proposer.sh's `claude)` arm of run_agent asks it for the extra argv; the
stream tap (lib/agent_stream.py) asks it which setting sources a pass ran with
so the `harness_config` event can say so. Every claude child (proposer,
accelerator, the agents a `reverse` run launches) goes through that one arm.

Pinned (the default), verified on claude 2.1.286 by reading the child's own
stream-json `init` record:

- `--setting-sources ""`  — no user, project or local settings file is read,
  so no plugin, no hook and no settings-declared MCP server is loaded, and no
  CLAUDE.md is auto-loaded (Principle VIII: auto-loaded context is off by
  default). OAuth keeps working.
- `--strict-mcp-config`   — also drops MCP servers that come from outside the
  settings files (~/.claude.json, Claude account connectors).
- `--settings <json>`     — the ONLY settings layer left. It carries a small
  allowlist from the operator's user settings that changes neither tools nor
  context — `env` (e.g. the OTEL exporter variables), `effortLevel` and, when
  the run gave no --model, `model` — plus any env the caller adds. claude
  does not merge two --settings flags (the last one replaces the first whole),
  so callers that need settings MUST pass them through here, never as a second
  flag.

`--disable-slash-commands` stays where it was (proposer.sh's shared args,
SPECSTRIDE_PROPOSER_SKILLS).

Inherited (SPECSTRIDE_PROPOSER_INHERIT_PLUGINS=1 / --proposer-inherit-plugins):
nothing is pinned; the child loads every setting source as an interactive
session would. Extra env, if any, still arrives as one --settings JSON.

Stdlib-only; never raises out of the CLI (an unreadable settings file just
carries nothing over).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

PINNED_SETTING_SOURCES = ""
INHERITED_SETTING_SOURCES = "user,project,local"
# User-settings keys carried into the pinned --settings layer. None of them adds
# a tool, a hook, a server or context; they keep the run on the operator's model,
# effort and telemetry, which dropping the user source would otherwise lose.
CARRY_KEYS = ("env", "effortLevel", "model")
INHERIT_ENV = "SPECSTRIDE_PROPOSER_INHERIT_PLUGINS"


def inherit_requested(environ=None) -> bool:
    value = (environ if environ is not None else os.environ).get(INHERIT_ENV, "")
    return value.strip().lower() in ("1", "true", "yes", "on")


def setting_sources(inherit: bool) -> str:
    return INHERITED_SETTING_SOURCES if inherit else PINNED_SETTING_SOURCES


def user_settings_path(environ=None) -> str:
    environ = environ if environ is not None else os.environ
    base = environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        environ.get("HOME") or os.path.expanduser("~"), ".claude")
    return os.path.join(base, "settings.json")


def load_user_settings(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pinned_settings(user: dict, *, model_given: bool, extra_env=None) -> dict:
    """The one --settings object for a pinned child."""
    out = {}
    env = user.get("env")
    merged = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
    merged.update(extra_env or {})
    if merged:
        out["env"] = merged
    effort = user.get("effortLevel")
    if isinstance(effort, str) and effort:
        out["effortLevel"] = effort
    model = user.get("model")
    if not model_given and isinstance(model, str) and model:
        out["model"] = model
    return out


def harness_args(*, inherit: bool, model_given: bool, extra_env=None, user=None) -> list:
    if inherit:
        return ["--settings", _dump({"env": dict(extra_env)})] if extra_env else []
    settings = pinned_settings(user or {}, model_given=model_given, extra_env=extra_env)
    args = ["--setting-sources", PINNED_SETTING_SOURCES, "--strict-mcp-config"]
    if settings:
        args += ["--settings", _dump(settings)]
    return args


def _names(value) -> list:
    """Names out of an init list whose items are strings or {"name": ...} dicts."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        name = item.get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name:
            out.append(name)
    return sorted(set(out))


def harness_config(init: dict, environ=None):
    """The `harness_config` event fields for one claude child, or None.

    Taken from the child's own stream-json `init` record, never guessed: only an
    init that reports `plugins` or `mcp_servers` yields one (an init without
    them — older CLIs, recorded fixtures — yields None, so the established event
    sequence stays byte-identical). Odd shapes degrade to empty lists; this
    never raises (Principle V). The fingerprint hashes everything that shapes the
    child's tools and context, so two passes with the same fingerprint ran under
    the same harness setup (lib/learn.py flags evaluations across a change).
    """
    try:
        if not isinstance(init, dict) or not ("plugins" in init or "mcp_servers" in init):
            return None
        inherit = inherit_requested(environ)
        fields = {
            "setting_sources": setting_sources(inherit),
            "inherit_plugins": inherit,
            "plugins": _names(init.get("plugins")),
            "mcp_servers": _names(init.get("mcp_servers")),
            "skills": _names(init.get("skills")),
            "slash_commands": _names(init.get("slash_commands")),
            "tools": _names(init.get("tools")),
        }
        digest = hashlib.sha256(_dump(fields).encode("utf-8")).hexdigest()[:16]
        return {
            "fingerprint": digest,
            "setting_sources": fields["setting_sources"],
            "inherit_plugins": inherit,
            "plugins": fields["plugins"],
            "mcp_servers": fields["mcp_servers"],
            "skills": len(fields["skills"]),
            "slash_commands": len(fields["slash_commands"]),
            "tools": len(fields["tools"]),
        }
    except Exception:  # noqa: BLE001 — observability never breaks the loop
        return None


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    args_p = sub.add_parser("args", help="print the extra claude argv, NUL-separated")
    args_p.add_argument("--inherit", action="store_true",
                        help="inherit every setting source (default: the %s env)" % INHERIT_ENV)
    args_p.add_argument("--model-given", action="store_true",
                        help="the caller passes --model, so the user's model is not carried")
    args_p.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                        help="extra env for the child's --settings layer (repeatable)")
    args_p.add_argument("--user-settings", default=None,
                        help="user settings.json (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    ns = parser.parse_args(argv)
    extra = {}
    for item in ns.env:
        key, sep, value = item.partition("=")
        if sep and key:
            extra[key] = value
    inherit = ns.inherit or inherit_requested()
    user = {} if inherit else load_user_settings(ns.user_settings or user_settings_path())
    out = harness_args(inherit=inherit, model_given=ns.model_given, extra_env=extra, user=user)
    sys.stdout.write("".join(arg + "\0" for arg in out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
