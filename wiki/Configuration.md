# Configuration

Everything is set in `.env` (copy from `.env.example`; the real `.env` is gitignored).

**Precedence: built-in defaults < `.env` < CLI flags.** As of `cc5ffa0`, an exported caller env
var also overrides `.env` (the orchestrator lets the caller's environment win over sourced
values), so `SPECSTRIDE_PROPOSER_TIMEOUT=… specstride run …` is honored.

## Backends (pick one per role)

Pick one per role; each role has its own accepted list:

Proposer backends: `dsh[:provider/model] | claude | codex | bebop[:name] | prime[:variant]`

Critic backends: `dsh[:provider/model] | claude | codex | bebop | prime[:variant]`

Both lists are generated from `lib/backends.py`, the single backend registry, and
checked against it by `lib/test_backend_registry.py`; to add a new backend, add it
to that registry first, then to its dispatch arm and these doc lines.

- **`dsh`** — DeepSeek Harness's `headless` profile. It uses the provider/model in
  `$DSH_HOME/settings.yaml` (this host selects `gpt-5.6-sol` through Compass STAGE).
  The proposer gets the normal harness tools; the critic runs with a temporary
  tool-disabling patch. This is the default proposer backend.
- **`claude`** — Anthropic. Claude Code CLI (proposer) + Messages API (critic). The `main`
  default; a clone plus an Anthropic key runs out of the box.
- **`codex`** — OpenAI. Codex CLI (proposer) + Chat Completions (critic). Ships, but
  **UNVERIFIED** (no Codex CLI on the author's host to test against).
- **`bebop`** — a local selector → Compass/qwen via a shim (host-specific).
- **`prime[:variant]`** — bare `prime` invokes the out-of-the-box `prime-agent`
  executable and lets its normal configuration select the default provider/model.
  No custom variants are required. `prime:<variant>` invokes the optional
  `prime <variant>` fleet launcher instead.

Portable example: `specstride run --proposer prime --critic prime`. Fleet example:
`specstride run --proposer prime:sol --critic prime:judge`.

### Model-requested DSH plugins

A DSH proposer can request installation of a missing profile plugin, but only from
an operator-owned exact allowlist. Enable it with comma-separated, version-pinned
registry specs:

```bash
SPECSTRIDE_DSH_PLUGIN_ALLOWLIST='@acme/dsh-browser@1.4.2,@acme/dsh-db@2.0.1' \
  specstride run --proposer dsh ...
```

When existing tools are insufficient, the model writes the fixed contract
`specstride-dsh-plugin-request/v1` to the active feature's
`.specstride/features/<feature>/dsh-plugin-request.json` and stops. Between passes,
Specstride validates the JSON and literal package specs, then executes:

```bash
dsh plugin --profile headless add --save-exact <approved-specs...>
```

Specstride never evaluates request content through a shell. Package ranges, tags,
URLs, git references, paths, unlisted specs, duplicate entries, extra JSON keys,
symlinks, and oversized requests are rejected and halt the proposer visibly.
Successful requests and receipts are archived below
`.specstride/features/<feature>/plugin-installs/`; a `plugin_installed` event records
the profile and packages. Installation changes the persistent DSH profile, so the
plugin is available to the next fresh proposer pass and later DSH sessions. Review
plugin provenance and lifecycle scripts before adding a spec to the allowlist.
The tool-free DSH critic never receives this request protocol.

### Prime observability parity

Prime Agent gets the **same structured `agent_*` stream tap** as `claude`: its
headless output is parsed into `.specstride/events.jsonl` events (`agent_init` /
`agent_tool` / `agent_text` / `agent_result`, plus `evidence_writing`), so the
timeline, watch card, and telemetry sinks show Prime tool calls and run totals
just like a Claude proposer. This is gated by the same `SPECSTRIDE_AGENT_STREAM` knob
and honors the same redaction/payload/retention policy (below).

**Schema compatibility.** The tap dispatches on the provider's stream schema, not
the backend name: `claude`/`codex` emit the Claude `stream-json` schema and Prime
emits `prime-v3`. Both are recognized structured schemas and start each invocation
in `structured` mode. An unrecognized or unparseable schema starts (or degrades)
into `degraded` mode — a text/result-only capability that is always announced via
an `agent_observability` event, never silently dropped. See
[On-Disk-Contract](On-Disk-Contract#the-event-stream) for the event fields.

**Registry-routed formats.** For any other backend, the stream schema comes from
the backend's registry entry (`lib/backends.py`), not from a backend-name check:
a backend whose entry names a `stream` format is parsed by that format's adapter,
routed purely by that value. Every failure degrades to the raw-text path with an
announced reason. `SPECSTRIDE_AGENT_STREAM=false` turns that off too — even with
`-j` (unlike `prime`, where `-j` keeps the tap on).

## Key knobs

See `.env.example` for the full set. The load-bearing ones:

| Variable | Default | Meaning |
|---|---|---|
| `SPECSTRIDE_PROPOSER` / `SPECSTRIDE_CRITIC` | `dsh` / `claude` | backend per role |
| `SPECSTRIDE_DSH_BIN` / `SPECSTRIDE_DSH_PROFILE` | `dsh` / `headless` | DeepSeek Harness executable and profile |
| `SPECSTRIDE_DSH_MODEL` / `SPECSTRIDE_DSH_CRITIC_MODEL` | empty | optional DSH model override, e.g. `zai/glm-5.3`; bare `glm-*` maps to `zai`, `qwen3.8-27b` maps to LiteLLM `local-high/qwen3.8-27b-q5`. A plain `--critic dsh` calls, and sizes its prompts for, `SPECSTRIDE_DSH_CRITIC_MODEL`, else `SPECSTRIDE_DSH_MODEL` (a `dsh:<ref>` qualifier beats both); the run header prints the resolved model and window |
| `SPECSTRIDE_DSH_PROVIDER` / `SPECSTRIDE_DSH_CRITIC_PROVIDER` | empty | provider for bare DSH model ids when they are not `glm-*` |
| `SPECSTRIDE_DSH_REASONING_EFFORT` / `SPECSTRIDE_DSH_CRITIC_REASONING_EFFORT` | empty | optional DSH reasoning override such as `high` or `max` |
| `SPECSTRIDE_DSH_PLUGIN_ALLOWLIST` | empty | comma-separated exact `package@semver` specs the DSH proposer may request and install |
| `SPECSTRIDE_DSH_PLUGIN_TIMEOUT` | `600` | timeout in seconds for one approved profile installation |
| `SPECSTRIDE_PRIME_AGENT_BIN` | `prime-agent` | standard Prime Agent executable used by bare `prime` |
| `SPECSTRIDE_PRIME_FLEET_BIN` | `prime` | optional fleet launcher used by `prime:<variant>` |
| `SPECSTRIDE_PRIME_BIN` | — | legacy alias for the fleet launcher override |
| `SPECSTRIDE_OPENCODE_OVERLAY` | `pass` | throws the backend's harness a throwaway config home instead of your real one, so a run cannot touch global config, carry memory between runs, or leave files behind. `<BACKEND>` is the backend name without its qualifier, upper-cased; this example is for bare `opencode` and its qualified forms. `pass` = fresh one per proposer pass; `attempt` = one per attempt (warm caches); any other value stops the run before the first pass (exit 3). A backend with no such config home ignores the knob with one warning; the critic always uses its own per-call copy |
| `SPECSTRIDE_MAX_REJECTS` | `3` | reject attempts per phase before halt (exit 2) |
| `SPECSTRIDE_MAX_ITER` | — | max headless proposer iterations per pass |
| `SPECSTRIDE_PROPOSER_TIMEOUT` | `1800` | per-pass timeout (seconds) |
| `SPECSTRIDE_PROPOSER_INHERIT_PLUGINS` | `0` | `1` = a `claude` proposer/accelerator pass loads the operator's Claude Code user, project and local settings (plugins, hooks, MCP servers, CLAUDE.md), as an interactive session would. Default: a pinned, minimal child (`--setting-sources "" --strict-mcp-config`, plus one `--settings` JSON carrying only your user settings' `env`, `effortLevel` and, when no `--model` is given, `model`). Also `--proposer-inherit-plugins`; saved in `last-run.conf`. [Learning](Learning) evaluation excludes runs made this way. See [Harness isolation](#harness-isolation-claude) |
| `SPECSTRIDE_PROPOSER_SKILLS` | `0` | `1` = don't pass `--disable-slash-commands` to a `claude`/`bebop` pass (skills and slash commands back on) |
| `SPECSTRIDE_CRITIC_TIMEOUT` | `300` | per-critic-call timeout (seconds) |
| `SPECSTRIDE_MAX_WALL_MIN` | `0` | whole-run wall-clock budget (0 = unlimited) |
| `SPECSTRIDE_CRITIC_GROUNDING` | on | critic's read-only grounding pass |
| `SPECSTRIDE_GIT_COMMITS` | auto | per-phase git checkpoint behavior |
| `SPECSTRIDE_CONTEXT_BUDGET` | ~24000 | chars of design-doc context injected (Spec Kit / OpenSpec) |
| `SPECSTRIDE_LIVE_DETAIL` | `tools` | live-view verbosity: `milestones \| tools \| full` |
| `SPECSTRIDE_AGENT_STREAM` | `true` | structured stream tap (`agent_*` events) for `claude`/`codex`/`prime` and any backend whose registry entry names a `stream` format; `false` = legacy raw path (for registry-routed backends this wins even with `-j`) |
| `SPECSTRIDE_SPEC_FORMAT` | auto | force `native \| speckit-tasks \| openspec-change` |
| `SPECSTRIDE_FEATURE` | dir basename / `default` | feature namespace |
| `SPECSTRIDE_LEARNING` | unset (= `off`) | the [learning loop](Learning): unset/`off` = inert; `suggest` = write per-phase observations at `phase_done`; `apply` = also read applied values into the run |
| `SPECSTRIDE_LEARNING_THROUGH` | unset | the `run_id` of the last `applied.json` decision a mixture-of-loops contract bound. `resolve` ignores every `apply` after it until a re-derivation binds it; reverts after it (an auto-revert, `--revert`, `--off`) still count, since they only move a knob toward its default. A bound value is therefore an upper bound on what runs, not a promise; the `arm` on `proposer_cap` records what did |
| `SPECSTRIDE_YIELD_POLL` | `30` | how often a yielded pass's resume predicate is polled (seconds). Setting it at all, even to 30, overrides a learned `yield_poll_interval` |

## Harness isolation (claude)

A `claude -p` started in a workdir would otherwise load everything an interactive session
loads: every enabled plugin with its hooks, MCP servers and SessionStart context, your user
hooks, and the target repo's `.claude/settings*.json` and `CLAUDE.md`. A run then depends on
what you turned on for interactive use. For example, a self-learning plugin writes under the
workdir (and into phase commits), forks reflector sessions on the same account, and injects
context the critic never sees.

So every `claude` child Specstride starts runs pinned (`lib/claude_harness.py`, the one place
that builds its harness flags):

- `--setting-sources ""`: no settings file is read, so no plugin, hook, settings-declared MCP
  server or auto-loaded `CLAUDE.md`. OAuth still works.
- `--strict-mcp-config`: no MCP server from `~/.claude.json` or Claude account connectors either.
- One `--settings` JSON: your user settings' `env` (e.g. the OTEL exporter variables),
  `effortLevel`, and `model` when the run passes no `--model`. Nothing else is carried. claude
  does not merge two `--settings` flags (the last one replaces the first), so anything else a
  pass needs in settings goes into this same object. With [OTEL traces](Telemetry) on, the
  child's `OTEL_RESOURCE_ATTRIBUTES` (its place in the run trace) is added there, overriding
  the same key from your `env`.

Each pass records what its child actually loaded as a `harness_config` event, read from the
child's own stream-json `init` record: `plugins`, `mcp_servers`, the `skills`,
`slash_commands` and `tools` counts, `setting_sources`, `inherit_plugins`, and a 16-hex
`fingerprint` over all of it. An `init` without plugin fields (older CLIs) emits none; an odd
one degrades to empty lists.

At start the orchestrator also logs two non-fatal `preflight_warning` events
(`lib/preflight.py`): `plugin_unignored_writes` (untracked, unignored files under the
workdir's `.claude/` while a plugin is enabled; phase commits run `git add -A`) and
`concurrent_claude_session` (another live Claude Code process with hooks or plugins in the
same workdir, whose writes the reverse guard can't tell from the proposer's).

`specstride doctor` runs the same checks on demand, before a launch, and prints the harness a
`claude` pass would get.

A pinned child still reports two built-in plugins in `init.plugins`: `cc-plugin-agents-md` and
`cc-plugin-telemetry`. They are left on deliberately (#113). With them switched off
(`enabledPlugins: {"…@builtin": false}`), nothing observable changed on claude 2.1.286: a canary
in the workdir's `AGENTS.md` stayed out of the child's context, and the OTEL metrics export
reached the collector either way. Their names are CLI internals, and `harness_config` records
them in every pass's fingerprint.

`--proposer-inherit-plugins` / `SPECSTRIDE_PROPOSER_INHERIT_PLUGINS=1` turns pinning off.

## Privacy controls

Every captured record — live output, local `.specstride/events.jsonl`, invocation debug
artifacts, and any remote sink — passes through `lib/observability_policy.py` **before**
it is displayed or written. These are conservative, audited defaults baked into the code
(not env knobs); adjust them in-code if project policy requires it. `.env.example` lists
them so operators know exactly what is retained.

- **Redaction (always on).** Credential/authorization/secret-looking keys and values
  (`api_key`, `token`, `bearer …`, `sk-…`, `gh?_…`) become `[REDACTED]`; provider
  "thinking"/reasoning content is dropped entirely.
- **Payload limits (per field, bytes).** assistant text 4096, tool input 2048, tool
  output 4096, diagnostics 1024, extracted target paths 512 (max 8 paths). Oversized
  content is truncated and the record is flagged `truncated=true` with original/retained
  byte counts — never silently cut.
- **Raw retention.** Raw provider prompt/response capture is **disabled by default**. When
  enabled it expires after **7 days**; redacted metadata + the terminal result are kept
  **30 days**. The policy enforces `metadata_retention_days >= raw_retention_days`, so a
  summary always outlives the raw content it describes. The policy version
  (`specstride-retention/v1`) travels with retained artifacts so a later sweep stays
  interpretable. See [On-Disk-Contract](On-Disk-Contract#retention).

## Raw-text fallback

Setting `SPECSTRIDE_AGENT_STREAM=false` (or `--no-live`'s legacy path) turns **off** structured
capture for both Prime and `claude` and restores the legacy raw tee'd output path: no
per-tool `agent_*` events, and the redaction/payload policy above no longer applies, so raw
provider text lands in `run.log`. Use it only when you accept that. A `--telemetry` run in
this mode still ships to Loki via the old shipper. The tap also degrades to this path
automatically if `lib/agent_stream.py` or `python3` is missing.

## Verification (pre-loop test automation)

`--verification off | plan | required` (or the equivalent env); `required` is the default.
It derives a `VerificationPlan v1`, injects its obligations into proposer + critic, runs
fixed-argv tests before each approval, and runs a cumulative release gate. `plan` skips gate
execution; `off` disables verification explicitly. The default human projection and safe,
non-overwriting scaffolds live under `<workdir>/testautomation/<feature>/`. Overrides via
`--test-plan /abs/path` and `--generate-tests /abs/dir` must be absolute, resolve inside the
workdir, and not target a final-path symlink. See [Getting Started](Getting-Started).

## Branches

Code is provider-agnostic and lives entirely on `main`. Branches differ *only* in `.env`
defaults:

- **`main`** — defaults the proposer to `dsh` and the critic to `claude`.
- **`bebop`** — overlay; defaults both roles to `bebop compass` (author's host).
- **`codex-demo`** — overlay; defaults both roles to `codex` (OpenAI-only demo).

Next: [Telemetry](Telemetry) · [Hardening](Hardening) · [Home](Home)
