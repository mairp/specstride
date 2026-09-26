# OpenCode — Ecosystem research (O1–O4)

Researched 2026-09-26. Repo: `github.com/anomalyco/opencode` (the `sst/opencode` URL/CLI
alias 301-redirects here — the org appears to have been renamed from `sst` to
`anomalyco`; homepage `opencode.ai` is unaffected). MIT-licensed, TypeScript, ~210k
stars, ~6,254 open issues at time of research. Latest "v1" release: `v1.18.32`
(2026-09-21). A parallel, actively-developed **v2 rewrite** ("opencode2") is shipping
concurrently via a separate installer (`https://opencode.ai/v2/install`), currently
around `v2.0.15`–`v2.0.18`, with materially different headless behavior (see below).
This concurrent-rewrite fact is the single biggest risk for O2/O4 and should gate any
integration decision.

## O1–O4 table

| Q | Answer | Confidence | Source |
|---|---|---|---|
| **O1** License | MIT (`Copyright (c) 2025 opencode`), no field restricting automated/unattended/looped use. | documented | https://raw.githubusercontent.com/anomalyco/opencode/dev/LICENSE (accessed 2026-09-26) |
| **O1** ToS — self-hosted keys | No OpenCode-specific ToS restricts unattended/looped use with a provider API key (BYO key, e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). Rate limits and seat requirements are whatever the upstream provider imposes, not OpenCode's. | inferred | No ToS page found on opencode.ai; absence noted at https://opencode.ai/docs/providers/ (accessed 2026-09-26) |
| **O1** ToS — subscription OAuth | Logging into OpenCode with a **Claude Pro/Max (or ChatGPT) subscription via OAuth** and then driving it programmatically has gotten at least one user's Anthropic account banned; the user was told by Anthropic engineers this "violates their terms of service." OpenCode never restricted this login path in response. **This is a live landmine for a Ralph loop specifically**, since Specstride's whole point is high-volume automated passes — using an OAuth-subscription login (as opposed to a metered API key) as the OpenCode proposer/critic auth is the one auth mode to avoid. | documented (community report, not an Anthropic legal ruling) | https://github.com/anomalyco/opencode/issues/6930 (closed) (accessed 2026-09-26) |
| **O1** OpenCode Zen / Go rate limits, plan reqs | **OpenCode Zen** = pay-as-you-go model gateway run by the OpenCode team, billed per-million-tokens (e.g. observed $0.042–$180/M depending on model), auto-reloads $20 when balance <$5. **OpenCode Go** = flat-fee subscription tier. No published automation-specific rate limit; users report frequent `rate_limit_exceeded` on Go even under quota, and Zen/Go accounts have been suspended with billing continuing (open support complaint). No documented "unattended use" carve-out or restriction either way — it's simply undocumented for this axis. | documented (pricing) / documented (issues) | https://opencode.ai/docs/zen/ (accessed 2026-09-26); https://github.com/anomalyco/opencode/issues/51395, /47634, /47957, /45989, /46515 (accessed 2026-09-26) |
| **O2** Install | npm package `opencode-ai` (`npm install -g opencode-ai` or scoped prefix install), or curl installer. v1 line is on the normal npm `latest` dist-tag (`1.18.32`); v2 is distributed **only** via `curl -fsSL https://opencode.ai/v2/install \| bash` and is not (yet) the npm `latest`. `npm view opencode-ai dist-tags` shows dozens of ephemeral `snapshot-*` tags plus `next`, `beta`, `dev` — i.e. very high release/branch churn, consistent with a fast-moving pre-1.0-feeling project despite the 1.x version number. | verified-live | `npm view opencode-ai versions/dist-tags` run 2026-09-26 |
| **O2** Release cadence | 134 GitHub releases in the last ~6 months (≈ one every 1.3 days) on the v1 line alone, before counting the parallel v2 line. | verified-live | `gh api repos/anomalyco/opencode/releases --paginate` run 2026-09-26 |
| **O2** Headless-interface churn | See dedicated section below — flags, JSON-stream behavior and auto-approve semantics changed repeatedly across the 6-month window, and are changing again right now under v2. | documented | see "Headless-interface stability" section |
| **O3** Cost/token visibility | Session-level cost/token tracking exists (TUI sidebar, `opencode stats`, `/export`) but is not a stable, always-correct number: subagent cost is excluded from session totals (#45417), tiered-context pricing is mis-estimated (#42910), some providers report no usage/cost at all (#45483, #48478), and the `stats` command's cost/token/cache breakdown regressed under v2 vs v1 (#47481). No `/usage` or `/cost` slash command exists yet — it's an open feature request (#41915). Whether the `--format json` event stream itself carries a per-event token/cost field could not be confirmed from docs; issue #40544 shows the stream at minimum doesn't say *which model* produced an event, which is a related gap. **Recommendation: don't rely on OpenCode's own reported cost for the Specstride cost ledger; if a number is needed, compute it independently from provider usage where accessible (e.g. Anthropic/OpenAI direct billing).** | inferred (stream fields) / documented (session-level tracking + its gaps) | https://github.com/anomalyco/opencode/issues/47481, /45417, /42910, /45483, /48478, /41915, /40544 (accessed 2026-09-26) |
| **O4** Unsuitability factors | (1) Concurrent v1/v2 rewrite with a materially different headless architecture (background service, `--command` removed, `--port` removed) actively breaking third-party integrations right now (#50937, #49903). (2) Multiple **open, unresolved indefinite-hang** reports specifically in headless/`opencode run` mode — exactly the idle-watchdog failure mode Specstride is built to avoid diagnosing blind (#35870, #38723 "~56% failure rate observed", #47709, #46142, #40747, #42268 "hung 3 days"). (3) Auto-approve permission flag has at least three overlapping names across the studied window (`--dangerously-skip-permissions`, hidden `--yolo`, documented `--auto`) with the two undocumented ones still functioning as of 2026-09-25 per #51399 — a support/consistency risk for a wrapper script pinning a specific flag. (4) `--format json` reliability bugs are numerous and recent: empty output with exit 0 (#50514), stream that "never exits" (#44901), `UnknownError` on `1.18.23→1.18.29` (#47972), subagent output silently dropped (#49300). (5) The OAuth-subscription-ban risk above. None of these is individually fatal, but together they argue for **go-with-caveats at best, pinned hard to a specific v1.18.x, with the idle watchdog kept aggressive and JSON-stream parsing defensive against empty/partial output.** | documented | issue numbers above, all accessed 2026-09-26 |

## Models & context windows

Providers: OpenCode is provider-agnostic via the Vercel AI SDK plus **Models.dev** as the
model-metadata source (https://opencode.ai/docs/providers/, accessed 2026-09-26).
Documented/inferred provider list includes: OpenCode Zen (`zen/*`, curated gateway run by
the OpenCode team), OpenAI, Anthropic (OAuth *or* API key), Google Vertex AI, Amazon
Bedrock, Azure OpenAI, Groq, OpenRouter, Together AI, Ollama (local), DeepSeek, xAI, and
any OpenAI-compatible endpoint via `@ai-sdk/openai-compatible` + custom `baseURL`. Model
naming is **`provider/model`** (e.g. `anthropic/claude-sonnet-4-5`, `openai/gpt-5.4`),
matching Specstride's own `--model provider/model` convention closely — confirmed
directly in the `run` CLI reference: `--model, -m  Model to use in the form of
provider/model` (https://opencode.ai/docs/cli/, accessed 2026-09-26). There is **no
single universal default model**; it's whatever the user last selected via `/models` or
set in `opencode.json`, so Specstride would need to always pass `--model` explicitly.

Context windows (source: models.dev `api.json`, live-queried 2026-09-26 — this is
OpenCode's own model-metadata backend, so these are the figures OpenCode itself would use
for context budgeting):

| Model | Context | Max output | Notes |
|---|---|---|---|
| `anthropic/claude-sonnet-4-5` | 1,000,000 | 64,000 | likely default "good" pick |
| `anthropic/claude-opus-4-5` | 200,000 | 64,000 | |
| `openai/gpt-5.4` | 1,050,000 | 128,000 | tiered pricing above 272k context |
| `openai/gpt-5.5-pro` | 1,050,000 | 128,000 | most expensive Zen offering ($180/M output) |
| `google/gemini-flash-latest` | 1,048,576 | 65,536 | |
| `zen/big-pickle` (free Zen model) | 200,000 | 32,000 (input cap 160,000) | one of several free Zen models |
| `zen/claude-haiku-4-5` | 200,000 | 64,000 | |

Confidence: **documented** (live API read of models.dev, the same data source OpenCode's
own `/models` command uses), not independently verified against each vendor's own docs.

## Headless-interface stability (releases + issues)

### Releases (last ~6 months, v1 line) that touched the `run`/headless/JSON interface

Extracted from all 134 GitHub release bodies published 2026-03-26→2026-09-21
(`gh api repos/anomalyco/opencode/releases --paginate`, accessed 2026-09-26). Only
entries that changed `run`'s flags, permission/auto-approve behavior, or JSON output are
listed; cosmetic/TUI-only changes are omitted.

| Date | Tag | Change |
|---|---|---|
| 2026-03-26 | v1.3.3 | Respect agent permission configuration for `todowrite` tool. |
| 2026-04-08 | v1.4.0 | Added `opencode run --dangerously-skip-permissions` to auto-approve non-denied permission prompts (the flag's first appearance in this window). |
| 2026-04-19 | v1.14.18 | `--dangerously-skip-permissions` finally documented (had shipped 11 days earlier, undocumented). |
| 2026-05-09 | v1.14.42 | Added an interactive split-footer mode for `opencode run`. |
| 2026-05-13 | v1.14.49 | **"Restore non-interactive `run` exit behavior."** + **"Fix `run --json` output draining."** — i.e. a prior release had regressed both non-interactive exit codes and JSON output draining, fixed here. |
| 2026-05-20 | v1.15.6 | Added shell mode to the `run` prompt; replaced subagent tabs with an on-demand picker in `run`. |
| 2026-06-05 | v1.16.0 | Added `run --replay` for interactive session replay. |
| 2026-06-10 | v1.17.0 | `mcp add` now works in non-interactive flows (previously it didn't). |
| 2026-06-30 | v1.17.12 | **Added a `yolo` mode** to auto-approve permissions — a *second*, functionally overlapping auto-approve mechanism alongside `--dangerously-skip-permissions`. |
| 2026-07-14 | v1.18.0 | Permission auto-accept state now kept separately per server; remote sessions can auto-accept correctly (implies they previously couldn't). |
| 2026-07-24 | v1.18.5 | **"Stop auto-accepting config permissions on current servers"** — a behavior change/regression fix to auto-accept semantics. |
| 2026-08-05 | v1.18.14 | xAI login simplified to a single device-code flow "that works better in headless and remote environments." |
| 2026-08-07 | v1.18.15 | Export full session transcripts as JSON from the UI. |
| 2026-08-21 | v1.18.20 | Answer permission requests triggered by subagents during `opencode run` (previously subagent permission requests during `run` apparently went unanswered/blocked). |
| 2026-09-01 | v1.18.26 | `apply_patch` no longer emits an empty move path in permission metadata. |

Net read: the `run` flags and permission/auto-approve semantics were touched in **at
least 10 of the last ~20 minor/patch cycles** in this window — this is not a stable,
frozen surface. On top of the v1 churn, `--auto` (the flag currently documented at
https://opencode.ai/docs/cli/, accessed 2026-09-26) coexists with the still-functioning
but hidden `--yolo` and `--dangerously-skip-permissions` as of 2026-09-25
(https://github.com/anomalyco/opencode/issues/51399, open) — three names, one behavior,
none formally deprecated.

### The v2 rewrite (concurrent, in progress, as of 2026-09-26)

A separate `opencode2`/v2 CLI is being built and shipped right now via
`curl -fsSL https://opencode.ai/v2/install | bash`, at roughly `v2.0.15`–`v2.0.18`
(https://github.com/anomalyco/opencode/issues/50937, /49034, /51285, /51342, accessed
2026-09-26). Confirmed v2-specific breaks relevant to Specstride's contract:

- `run --command` **removed in v2**, and slash commands are not expanded — "no CLI path
  for headless command invocation" (#49903, open).
- `--port` **removed in v2**; v2 uses a shared background service model instead of a
  one-shot foreground process, breaking tools that spawned `opencode --port <n>`
  (#50937, open, with a shell-function workaround posted 2026-09-26).
- `opencode run` (v2) exits 1 after a transient provider error that the runner itself
  already retried and recovered from — a false-failure signal a Ralph loop would have to
  filter out (#50062, open).
- v2's `config-declared providers never materialize into registry` (#51285, open,
  "v2.0.15") — i.e. `--model provider/model` selection can silently not work in v2 yet.
- V1→V2 session importer skips sessions created after the first V2 launch (#49641, open).

**Implication for O2/O4:** any Specstride integration work aimed at "current OpenCode"
needs to say explicitly which line (v1.18.x vs v2.0.x) it targets, and should probably
pin to v1.18.x for now — v2 is pre-alpha-quality for headless/non-interactive use as of
this research date, per the open issues above, with no GA date found.

### Open/closed issues: headless/CI, hangs, permission prompts, JSON schema

All via `gh search issues ... --repo anomalyco/opencode`, accessed 2026-09-26. Selected
for direct relevance to the Specstride proposer/critic contract (idle watchdog, exit
codes, JSON stream); not exhaustive given ~6,254 open issues total.

**Hangs in headless mode (idle-watchdog risk — the single biggest operational concern):**
- #35870 (open) — "Headless opencode run intermittently hangs at startup."
- #38723 (open) — "`opencode run` intermittently hangs during init — no session created,
  no output, no error (**~56% failure rate observed**)."
- #47709 (open) — hangs after "init count=" before session creation, 1.18.29, WSL2.
- #46142 (open) — WSL2 single `opencode run` deadlocks during bootstrap.
- #44782 (open) — hangs after "event connected", no model request ever made (DeepSeek).
- #40747 (open) — hangs indefinitely when the usage quota is exhausted, instead of
  reporting the error it already has.
- #42268 (closed) — headless `run` "never exits after a fatal non-retryable provider
  error — hung 3 days on 'Monthly usage limit reached.'"
- #40330 (closed) — hangs forever when the provider's baseURL refuses the TCP connection
  (unbounded retry, non-interactive mode never surfaces the retry status).
- #43888 (closed) — non-interactive `run` "hangs forever when a subagent tool hits a
  `permission=external_directory` ask" (v1.18.18).
- #36868 (closed) — `run --auto` hangs indefinitely when a Task **subagent** requests
  permission (i.e. `--auto` does not always cascade to subagents — see also #41730 open,
  same root cause, "do not cascade down to subagents").
- #44556 (open) — `run --session` hangs when the model uses the question tool on an
  externally-created session.
- #17516 (closed, 2026-03-14) — `opencode run` hangs after completing tool calls, process
  never exits.

**Permission prompts / auto-approve in non-interactive mode:**
- #45531 (open) — a `question` permission deny blocks a same-named custom tool in
  headless mode.
- #44267 (open) — when a permission is auto-rejected, the final message is empty (zero
  bytes) — no error surfaced to the caller.
- #41730 (open) — `--auto` doesn't cascade to subagents.
- #36076 (open) — feature request: disable MCP elicitation in headless runs (currently
  MCP servers can still prompt).
- #51399 (open, 2026-09-25) — feature request for a documented "full-auto" mode; body
  confirms `--auto`/`--yolo`/`--dangerously-skip-permissions` all currently coexist, two
  of them hidden from `--help`.

**JSON/structured-output schema and stream reliability:**
- #50514 (open) — `run --format json` intermittently exits 0 with **empty output**.
- #44901 (open) — `run --format=json` emits a terminal `step_finish` event but the
  process **never exits**.
- #47972 (open) — `run --format json` fails with `UnknownError` across 1.18.23→1.18.29.
- #42238 (open) — `--format json` emits auto-compaction internals as ordinary text
  events (schema pollution).
- #40544 (open) — JSON events don't identify which model produced them.
- #49300 (open) — `--format json` silently drops every subagent's output/parts.
- #38638 (closed) — `--format json` didn't emit `message.part.delta` events in real time
  (fixed).
- #44911 (open) — `openapi.json` stopped documenting SSE payload types (`V2Event`,
  `SessionLogItem`) after an "effect beta.107" regen — i.e. the OpenAPI/SDK schema itself
  has silently regressed in coverage.
- #39573 (open) — `opencode run` exits 1 even after auto-compaction recovers from a
  provider 413 (false-failure signal).
- #43622 (open) — session exits silently (exit 0, no error) when a turn returns
  `finish: "unknown"` — a success/failure ambiguity Specstride's exit-code parsing would
  need to guard against.

**Auth/CI-specific:**
- #6930 (closed) — see O1 table; OAuth-subscription automation risks a provider ban.
- #36386 (open) — feature request: MCP OAuth paste-code fallback for headless/SSH
  sessions (implies OAuth-gated MCP servers currently can't complete auth headlessly).

## Alternative integration seams

`opencode serve` starts a long-lived HTTP server (`opencode serve`, optional
`OPENCODE_SERVER_PASSWORD` for basic auth) that exposes a full **OpenAPI 3.1 spec** at
`/doc`, from which OpenCode's own official SDK is code-generated; the TUI itself is
"just" a client of this same API
(https://opencode.ai/docs/server/, accessed 2026-09-26). Endpoints include session
create (`POST /session`), message send (`POST /session/:id/message`), shell execution
(`POST /session/:id/shell`), and permission/auth management. `opencode run --attach
http://host:port` lets a CLI invocation drive an already-running server instead of
spawning its own process
(https://opencode.ai/docs/cli/, accessed 2026-09-26).

**This is a materially better integration seam than the `run` CLI** for a
Ralph-loop proposer, for two reasons the issues above make concrete: (1) several of the
worst hang/exit-code bugs are specific to the one-shot `opencode run` process lifecycle
(#38723's 56% hang rate, #42268's 3-day hang, #43622's silent exit-0-on-unknown-finish)
— a long-lived server the orchestrator polls over HTTP with its own timeout has a clearer
failure signature than "the CLI process itself went idle," which is exactly what
Specstride's idle watchdog currently has to infer blind; (2) the OpenAPI spec gives a
real, versioned schema to adapt against instead of reverse-engineering `--format json`
stdout framing (whose schema itself has churned — see `openapi.json` regression #44911).
The downside: it's more integration work than a stdin/stdout CLI arm (spawn+manage a
server process, HTTP client, SSE parsing per #46733/#43519 below), and the server mode
has its own open bugs (SSE `/event` sometimes delivers only `server.connected` +
heartbeats with no message events, #46733, open; heartbeat comments reset the SSE
`chunkTimeout` so stalled streams never time out, #43519, open) — so it is not free of
the same class of problem, just differently shaped. **Recommendation: worth a spike
(a `serve` + SDK proposer arm) rather than defaulting straight to wrapping `opencode
run`.**

## Rules / AGENTS.md / custom-instructions auto-loading

(Directly relevant to the "proposer pass-1 skill overflow" memory item — this is the
number-one context-budget risk category for any new harness.)

OpenCode auto-loads, **with no flag required**, in this order
(https://opencode.ai/docs/rules/, accessed 2026-09-26):
1. Walks upward from cwd looking for `AGENTS.md` (preferred) or `CLAUDE.md` per directory;
   first match wins per directory, `AGENTS.md` beats `CLAUDE.md` if both exist locally.
2. A global `~/.config/opencode/AGENTS.md`.
3. **Claude Code compatibility fallback**: `~/.claude/CLAUDE.md`, and per the same page,
   `~/.claude/skills/` (Claude Code skills) are also honored via compatibility — this is
   the exact class of auto-load that caused Specstride's documented
   proposer-skill-overflow incident on the `claude` backend, now confirmed to also apply
   to OpenCode by default.
4. `opencode.json`'s `instructions` field can pull in additional file globs or remote
   URLs, merged with `AGENTS.md` automatically.

All project-level content loads into every session by default, with no per-run opt-out
flag found other than disabling the *Claude Code compatibility* layer specifically:
`OPENCODE_DISABLE_CLAUDE_CODE=1` (all Claude Code compat), `..._PROMPT=1` (global
`CLAUDE.md` only), `..._SKILLS=1` (skills only). No documented env var disables
`AGENTS.md`/`opencode.json instructions` loading itself — those are treated as
first-class, always-on project config, not an opt-in extra. **For a Specstride proposer
pass, this means: (a) any `~/.claude/skills` on the host running the loop will
auto-load into every OpenCode pass unless `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` is set
in the throwaway config-home overlay, and (b) a large repo `AGENTS.md` counts against
context on every single pass with no bypass flag.** (documented, from
https://opencode.ai/docs/rules/, accessed 2026-09-26)

## Sources

- https://github.com/anomalyco/opencode (repo, license, issue tracker) — accessed 2026-09-26
- https://opencode.ai/docs/cli/ — accessed 2026-09-26
- https://opencode.ai/docs/ (nav/ToC) — accessed 2026-09-26
- https://opencode.ai/docs/rules/ — accessed 2026-09-26
- https://opencode.ai/docs/permissions/ — accessed 2026-09-26
- https://opencode.ai/docs/providers/ — accessed 2026-09-26
- https://opencode.ai/docs/server/ — accessed 2026-09-26
- https://opencode.ai/docs/zen/ — accessed 2026-09-26
- https://opencode.ai/docs/troubleshooting/ — accessed 2026-09-26
- https://opencode.ai/docs/sdk/ — accessed 2026-09-26
- https://raw.githubusercontent.com/anomalyco/opencode/dev/LICENSE — accessed 2026-09-26
- `npm view opencode-ai versions/dist-tags/license` — run 2026-09-26
- `gh api repos/anomalyco/opencode` and `.../releases --paginate` — run 2026-09-26
- `gh search issues ... --repo anomalyco/opencode` (headless, non-interactive, hangs,
  run --format json, v2, rate limit, tokens/cost, ban/subscription) — run 2026-09-26
- https://models.dev/api.json (live) — accessed 2026-09-26
- GitHub issues cited inline throughout: #6930, #35870, #38723, #47709, #46142, #44782,
  #40747, #42268, #40330, #43888, #36868, #41730, #44556, #17516, #45531, #44267, #36076,
  #51399, #50514, #44901, #47972, #42238, #40544, #49300, #38638, #44911, #39573, #43622,
  #6930, #36386, #50937, #49903, #50062, #51285, #49641, #46733, #43519, #47481, #45417,
  #42910, #45483, #48478, #41915, #51395, #47634, #47957, #45989, #46515
