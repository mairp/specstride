# Cursor CLI (`cursor-agent`) — Ecosystem Research

Researched 2026-09-26, all sources accessed 2026-09-26. This document does not run live
smoke tests (that is the contract agent's job); it is a docs/changelog/forum/ToS sweep.

## O1-O4 table

| Q | Answer | Confidence | Source |
|---|---|---|---|
| O1 (ToS/plan for unattended looped use) | ToS Section 1.5 bans reverse engineering, using outputs to train a competing model, reselling/renting "the Service", scraping, and republishing unreplicated benchmark results. It does **not** contain a "no automation"/"no bot" clause. Cursor staff (Kevin Neilson) publicly confirmed on the forum that "embedding Cursor as a backend AI service in your product's tech stack is a supported and explicitly intended use" — i.e. wrapping `cursor-agent` inside another orchestrator (Specstride) is allowed, so long as Cursor itself isn't resold standalone. | documented (ToS text) / documented (forum, official reply) | https://cursor.com/terms-of-service (accessed 2026-09-26); https://forum.cursor.com/t/api-sdk-terms-of-use-question/159741 (accessed 2026-09-26) |
| O1 (plan/seat requirement) | Free/Hobby plan API keys can use the headless CLI but **not** the Background/Cloud Agents API. Paid plans (Pro $20, Pro+ $60, Ultra $200, Teams $40/seat+) get a dollar-denominated monthly credit pool; overage falls back to on-demand billing or the free "Auto" router (unlimited, doesn't touch the pool, but Cursor retired the flat Auto per-token rate on 2026-08-24). Team/Enterprise pays a flat $0.25/M-token "Cursor Token Rate" surcharge on top of any BYO-key third-party model spend. No CLI-specific seat is documented beyond the general plan. | documented | https://cursor.com/help/models-and-usage/usage-limits (accessed 2026-09-26); aggregated pricing summaries: https://coworker.ai/blog/cursor-pricing, https://www.eesel.ai/blog/cursor-pricing (accessed 2026-09-26) |
| O1 (rate limits) | No published numeric requests/min or requests/hour limit for the CLI itself. The one hard, documented number found is for the Cloud Agents API's "List GitHub Repositories" endpoint: 1/user/minute, 30/user/hour. Community forum reports concurrent headless `cursor-agent` invocations racing each other in non-TTY environments — one process exits immediately with status 1 and no output — which looks like an undocumented concurrency limit, not a published rate limit. | documented (API limit) / inferred (CLI concurrency ceiling) | https://docs.cursor.com/en/background-agent/api/overview (accessed 2026-09-26); https://forum.cursor.com/t/concurrent-headless-cursor-agent-invocations-fail-without-delay/142677 (accessed 2026-09-26) |
| O2 (install method) | `curl https://cursor.com/install -fsS | bash` (macOS/Linux/WSL) or `irm 'https://cursor.com/install?win32=true' | iex` (Windows). Binary lands in `~/.local/bin`. | documented | https://cursor.com/docs/cli/installation (accessed 2026-09-26) |
| O2 (version pinning) | **No documented way to pin an install to a specific CLI version.** The installer always fetches latest, and "Cursor CLI will try to auto-update by default." The only pinned-version path found anywhere is the unofficial third-party `cursor-agent` **PyPI** wrapper (`pip install cursor-agent==1.0.0`), which is not the vendor binary. A `--git-ref` flag exists (added 2026-07-13) but it pins a **plugin marketplace** ref, not the CLI's own version. | documented (no pin) / unknown (whether an undocumented env var like `CURSOR_CLI_VERSION` exists) | https://cursor.com/docs/cli/installation (accessed 2026-09-26); https://pypi.org/project/cursor-agent/ (accessed 2026-09-26); https://cursor.com/docs/cli/changelog (accessed 2026-09-26, 2026-07-13 entry) |
| O2 (release cadence) | Roughly weekly-to-biweekly changelog entries; see the 6-month sweep below. | documented | https://cursor.com/docs/cli/changelog (accessed 2026-09-26) |
| O3 (cost visibility) | `agent status`/`agent about --format json` exist (added 2026-06-09) but per the 2026-04 changelog entry, `stream-json` gained "per-turn token usage and `request_id`" — however an open forum feature request ("Include Token Usage in Stream-JSON Output", still open as of the search) says the agent receives token-delta updates from the server but **does not surface them** in `stream-json` output reliably; a second forum thread ("Can cursor CLI output the token usage?") confirms users still don't have a clean per-run cost figure as of this sweep. Net: partial, flaky token/cost visibility — present in the changelog's stated intent, contradicted by live user reports. No dollar-cost figure is emitted at all; only token counts, and those are disputed. | documented (changelog claim) contradicted by inferred (forum reports) | https://cursor.com/docs/cli/changelog (accessed 2026-09-26, April 2026 entry); https://forum.cursor.com/t/include-token-usage-in-stream-json-output/146980 (accessed 2026-09-26); https://forum.cursor.com/t/can-cursor-cli-output-the-token-usage/156872 (accessed 2026-09-26) |
| O4 (unsuitability factors) | (1) Closed-source, proprietary binary — no source to audit or vendor for reproducibility; (2) no version pinning on install, so a Ralph loop's harness version can silently drift under it (`agent update` and auto-update both mutate the binary in place); (3) recurring, still-open forum reports of `-p`/headless hangs across multiple 2026 releases (see Headless-interface stability below) — this is the single biggest risk for Specstride's idle watchdog, which kills a hung pass with no diagnosis; (4) `--force`/`--yolo` is required for unattended writes, which is the same trust model as Claude/Codex's danger flags, not a special problem, but combined with (2)+(3) it means an unattended loop can be running a binary that changed under it and can hang with zero stdout. None of these are fatal by themselves; together they argue for a spike before committing. | documented (closed-source, no pin) / documented (forum hang reports) | see sources above and Headless-interface stability section |

## Models & context windows

| Model (as named in Cursor) | Provider | Context window | Confidence | Source |
|---|---|---|---|---|
| Composer 2.5 (default CLI model since 2026-06-22) | Cursor in-house (reportedly built on Moonshot Kimi K2.5) | Advertised 200K tokens; multiple independent reports put **usable** context at 70K-120K after Cursor's internal truncation/re-summarization | documented (advertised) / inferred (usable) | https://cursor.com/docs/models-and-pricing (accessed 2026-09-26); https://www.futureproofing.dev/resources/ai-native-team/cursor-composer-2-5-explained (accessed 2026-09-26) |
| Claude Opus 4.8 / Opus 5 / Opus 5.5, Claude 4.6 Sonnet, Claude Fable 5 / 5.1 | Anthropic (via Cursor) | 200K advertised; Cursor's own agent loop reports a "312K-token effective working context" as of April 2026 regardless of underlying model's native max (Cursor manages its own context assembly on top of the vendor context) | documented / inferred | https://cursor.com/docs/models/claude-opus-5 (accessed 2026-09-26); aggregator: https://www.builder.io/blog/cursor-vs-claude-code (accessed 2026-09-26) |
| GPT-5.3 Codex, GPT-5.5, GPT-5.6 Sol/Terra/Luna | OpenAI (via Cursor) | Not separately documented by Cursor; presumed to inherit OpenAI's published context for each snapshot | unknown | https://cursor.com/help/models-and-usage/available-models (accessed 2026-09-26) |
| Gemini 3.1 Pro, Gemini 3.5/3.8 Flash | Google (via Cursor) | Not documented by Cursor | unknown | https://cursor.com/help/models-and-usage/available-models (accessed 2026-09-26) |
| Grok Build 0.1, Grok 4.3/4.5/4.6/4.7 | xAI (via Cursor) | Grok 4.7's "long-context tier" is called out specifically: up to a 500K window past 256K tokens | documented | https://cursor.com/help/models-and-usage/available-models (accessed 2026-09-26) |
| Auto | Cursor router (picks among the above) | N/A — routed | documented | https://cursor.com/help/models-and-usage/usage-limits (accessed 2026-09-26) |

**Naming for `--model`:** models are passed as free-text slugs, e.g. `agent -p "..." --model "gpt-5"`, `/opus`, `/composer`, `/fast` shortcuts exist in interactive mode (added 2026-07-06). No single canonical machine-readable model-ID list with context windows was found on cursor.com; the closest thing is `https://cursor.com/help/models-and-usage/available-models`, which is prose, not a table with context-window columns for every model (only Grok 4.7 and Composer 2.5 get an explicit number). This is a real gap for `_critic_context_tokens()`, which needs a per-model number, not "advertised 200K, actually less." **Recommend a spike** (`agent --model <name> -p "..."` + inspect `status --format json` or provider docs) rather than trusting the marketing page.

## Headless-interface stability (6-month changelog sweep, print mode / flags / output / permission)

Source for all dated entries: https://cursor.com/docs/cli/changelog (accessed 2026-09-26).

- **2026-08-26**: persistent sessions (`agent persist`), `--computer-use --share-desktop`, `--spawn` hook for worker reconnection.
- **2026-08-11**: subagent model selection via `/config`; headless single-turn runs now wait for subagents before exiting (behavior change — previously could exit before subagent work finished, a correctness-relevant fix for anything scripting on exit code/output).
- **2026-07-20**: `--trust` usable in interactive sessions (workspace-trust bypass); model catalog auto-refreshes every 10 min in long sessions.
- **2026-07-13**: `--git-ref` for plugin marketplace pinning (not CLI version pinning); Max Mode variants preserved headless via `--model`; marketplace `--format json`.
- **2026-07-06**: `/opus`, `/composer`, `/fast` model shortcuts; new installs default to Auto routing; `user-invocable: false` skill frontmatter.
- **2026-06-29**: `--add-dir` (repeatable, multi-root); Enter-twice to flush queued follow-ups.
- **2026-06-22**: `--auto-review` approval workflow; `--workspace` scoped sessions; **Composer 2.5 becomes the default CLI model.**
- **2026-06-09**: `--format json` for `status`/`about`; plan mode works with `-p`; model picker shows Max Mode state.
- **2026-05-20**: `/skill-name` invocations now work in `-p` print mode; repeatable `--worker-dir`.
- **2026-05-14**: fix — `-p` runs were hanging/slow with slow stdio MCP servers (a headless-specific bugfix, implying this was previously a real failure mode); high-effort model configs supported headless.
- **2026-05-07**: `--plugin-dir`; skills now load in "interactive, headless, and editor-integration modes" (implying they previously didn't load uniformly headless).
- **2026-04 (undated)**: fix — `-p` runs no longer block when spawned with an **open stdin pipe** (directly relevant to Specstride, which pipes the prompt on stdin per the `claude` arm's pattern — this was a real headless-hang bug that got fixed, but see forum reports below showing it recurred/wasn't fully fixed); `--yolo` and `--force` (synonym) added for full auto-approval; per-turn token usage + `request_id` claimed in `stream-json`; repeatable `-H/--header`; `--mode plan` startup flag; `--worktree`/`-w` isolated-branch flag.
- **2026-03 (undated)**: `--force` formalized as "Run Everything" approval mode, separated from mode-cycling; headless transcript format described as "Claude Code-compatible JSONL"; `/auto-run` toggle.
- **2026-01-16 / 2026-01-08**: `--mode plan|ask` startup flags, `/plan <prompt>`, `--trust`/`--force` for untrusted workspaces, `--continue` to resume most recent chat.

**Pattern:** the print/headless path has changed in nearly every single one of the ~13 releases in this window — new flags, a default-model change (Composer 2.5, 2026-06-22) that silently changes what an unpinned install runs, and at least two headless-specific hang bugfixes (2026-04 stdin-pipe hang, 2026-05-14 slow-stdio-MCP-server hang). This is a high rate of change for a component Specstride would pin behavior against; combined with no version-pinning at install, an unattended loop is exposed to upstream behavior/default-model drift with no way to freeze it.

**Community reports of headless/CI hangs and permission problems** (all forum.cursor.com, Bug Reports category, accessed 2026-09-26):
- "Cursor CLI hangs if launched with CI=1" — https://forum.cursor.com/t/cursor-cli-hangs-if-launched-with-ci-1/128375
- "Cursor agent -p (print/headless mode) hangs indefinitely and never returns" — https://forum.cursor.com/t/cursor-agent-p-print-headless-mode-hangs-indefinitely-and-never-returns/150246
- "Cursor-agent -p hangs with zero output on 2026.07.01-41b2de7 (macOS Intel) — all output formats, even trivial prompts" — https://forum.cursor.com/t/cursor-agent-p-hangs-with-zero-output-on-2026-07-01-41b2de7-macos-intel-all-output-formats-even-trivial-prompts/164841 (i.e. this recurred in July 2026, *after* the April fix above)
- "Cursor-agent can not return from simple command under headless mode" — https://forum.cursor.com/t/cursor-agent-can-not-return-from-simple-command-under-headless-mode/132641
- "Concurrent headless cursor-agent invocations fail without delay" — https://forum.cursor.com/t/concurrent-headless-cursor-agent-invocations-fail-without-delay/142677
- "Regression: CLI commands spawned by Cursor agent freeze indefinitely" — https://forum.cursor.com/t/regression-cli-commands-spawned-by-cursor-agent-freeze-indefinitely/153660
- "Cursor CLI hangs with user permissions" (hangs with no output unless run as root/sudo; Ctrl+C flushes buffered output) — https://forum.cursor.com/t/cursor-cli-hangs-with-user-permissions/155627
- "Cli: Permission allowlist written to global config instead of project-level `.cursor/cli.json`" (a permission granted once should stick per-project but writes to the wrong file, so the approval prompt keeps reappearing) — https://forum.cursor.com/t/cli-permission-allowlist-written-to-global-config-instead-of-project-level-cursor-cli-json/160343

Confidence for all of the above: **documented** (they are the forum threads themselves, i.e. primary user reports), but **not independently reproduced by this agent** (no live smoke test was run here — see the contract agent's report for that). Net signal: hangs in `-p`/headless mode are a recurring, multi-release pattern, not a single fixed incident, which is exactly the failure mode Specstride's idle watchdog is designed to catch but can't diagnose.

## Alternative integration seams

- **Cloud Agents API** (formerly "Background Agents API"), public beta: `https://docs.cursor.com/en/background-agent/api/overview`. Lets a caller launch/manage a cloud-hosted agent against a repo over HTTP, with Server-Sent Events streaming, Basic or Bearer auth via a user or service-account API key, and structured results (git branch, PR, token usage matching the team usage-events endpoint). **Free-plan API keys cannot use it** — Background Agent API access requires a paid plan; only the headless CLI is free-plan-accessible.
  - As a **proposer** seam: not a good fit for Specstride's per-pass model — it's a cloud-hosted, asynchronous, multi-turn agent working in its own remote workspace/branch, not a one-shot local pass in `$WORKDIR` with a fresh stdin prompt. It would require pulling results back via git rather than running in-place.
  - As a **critic** seam: more promising in principle (tool-less, direct HTTP call, closer to how `claude`/`codex` critics work today per `lib/critic.py`), but the API is designed to run a repo-editing agent, not to answer a one-shot "judge this evidence" prompt — it doesn't obviously expose a way to say "no tools, just this text back." Would need a spike to confirm it can be forced into a stateless, tool-off judging role; absent that, the plain CLI (`cursor-agent -p --output-format json` with tools/MCP disabled per-project) is the closer match to the `dsh`/`prime`/`bebop` critic pattern already in `lib/critic.py`.
  - Rate-limited on at least one endpoint (List GitHub Repositories: 1/user/minute, 30/user/hour) — a possible constraint if the critic path polls status.
  - Confidence: documented (API exists, auth, beta status, plan gating) / inferred (unsuitability for Specstride's one-shot local pass model, since no direct test of forcing a stateless judge-only call was performed).

## `.cursor/rules`, AGENTS.md, MCP auto-load and disabling

- **Rules auto-load**: `.cursor/rules` directory (project), `AGENTS.md` at project root, and (per one CLI-docs fetch) `CLAUDE.md` at project root are all auto-detected and applied with **no configuration required**. This is exactly the auto-loaded-context risk flagged in the prompt's "operator memory" note (the pass-1 skill-overflow incident with Claude). Confidence: documented. Source: https://cursor.com/docs/cli/using (accessed 2026-09-26).
- **Disabling rules**: **no documented flag** (`--no-rules` or equivalent does not exist per docs or forum search). A 2026 forum feature request ("Is it possible to disable usage rules from AGENTS.md? Cursor and Codex needs separate rules set") is still open, i.e. as of this sweep there is no supported off-switch — the only workaround the community has found is not having the files, or scoping rule frontmatter (`alwaysApply`, glob triggers) so a given rule doesn't match. Confidence: documented (absence confirmed by an open, unresolved feature request). Source: https://forum.cursor.com/t/is-it-possible-to-disable-usage-rules-from-agents-md-cursor-and-codex-needs-separete-rules-set/138971 (accessed 2026-09-26).
- **MCP auto-load**: `mcp.json` is auto-discovered with precedence project → global → nested, same servers/tools as the editor, with **no documented flag to disable MCP loading entirely** for a headless run. Only per-server disable is documented: `agent mcp disable <server>` (writes to a per-project-slug store) and `agent mcp list` / `list-tools`. `--approve-mcps` only auto-approves, it does not turn MCP off. Confidence: documented (both the auto-load mechanism and the absence of a blanket disable). Sources: https://cursor.com/docs/cli/mcp (accessed 2026-09-26); https://forum.cursor.com/t/mcp-access-in-headless-mode/136709 (accessed 2026-09-26).
- **Net risk for Specstride**: same class of risk as the documented Claude pass-1 skill-overflow incident — a project that happens to carry `.cursor/rules`, `AGENTS.md`, `CLAUDE.md` or an `mcp.json` (very plausible for Specstride's own target workdirs, which already use `AGENTS.md`-adjacent conventions) will have that content silently injected into every pass's context with no supported CLI switch to suppress it. The only confirmed mitigation is filesystem-level: keep those files out of the workdir Cursor sees, or move the run into a directory that doesn't inherit them — the `dsh`-style throwaway-config-home trick doesn't apply here since rules/AGENTS.md live in the **target repo**, not in a Cursor config home.

## Sources

- https://cursor.com/docs/cli/headless (accessed 2026-09-26)
- https://cursor.com/docs/cli/overview (accessed 2026-09-26)
- https://cursor.com/docs/cli/using (accessed 2026-09-26)
- https://cursor.com/docs/cli/changelog (accessed 2026-09-26)
- https://cursor.com/docs/cli/installation (accessed 2026-09-26)
- https://cursor.com/docs/cli/reference/permissions (accessed 2026-09-26)
- https://cursor.com/docs/cli/mcp (accessed 2026-09-26)
- https://cursor.com/docs/background-agent/api/overview (accessed 2026-09-26)
- https://docs.cursor.com/en/background-agent/api/overview (accessed 2026-09-26)
- https://cursor.com/terms-of-service (accessed 2026-09-26)
- https://cursor.com/help/models-and-usage/usage-limits (accessed 2026-09-26)
- https://cursor.com/help/models-and-usage/available-models (accessed 2026-09-26)
- https://cursor.com/docs/models-and-pricing (accessed 2026-09-26)
- https://cursor.com/docs/models/claude-opus-5 (accessed 2026-09-26)
- https://forum.cursor.com/t/api-sdk-terms-of-use-question/159741 (accessed 2026-09-26)
- https://forum.cursor.com/t/cursor-cli-hangs-if-launched-with-ci-1/128375 (accessed 2026-09-26)
- https://forum.cursor.com/t/cursor-agent-p-print-headless-mode-hangs-indefinitely-and-never-returns/150246 (accessed 2026-09-26)
- https://forum.cursor.com/t/cursor-agent-p-hangs-with-zero-output-on-2026-07-01-41b2de7-macos-intel-all-output-formats-even-trivial-prompts/164841 (accessed 2026-09-26)
- https://forum.cursor.com/t/cursor-agent-can-not-return-from-simple-command-under-headless-mode/132641 (accessed 2026-09-26)
- https://forum.cursor.com/t/concurrent-headless-cursor-agent-invocations-fail-without-delay/142677 (accessed 2026-09-26)
- https://forum.cursor.com/t/regression-cli-commands-spawned-by-cursor-agent-freeze-indefinitely/153660 (accessed 2026-09-26)
- https://forum.cursor.com/t/cursor-cli-hangs-with-user-permissions/155627 (accessed 2026-09-26)
- https://forum.cursor.com/t/cli-permission-allowlist-written-to-global-config-instead-of-project-level-cursor-cli-json/160343 (accessed 2026-09-26)
- https://forum.cursor.com/t/is-it-possible-to-disable-usage-rules-from-agents-md-cursor-and-codex-needs-separete-rules-set/138971 (accessed 2026-09-26)
- https://forum.cursor.com/t/mcp-access-in-headless-mode/136709 (accessed 2026-09-26)
- https://forum.cursor.com/t/include-token-usage-in-stream-json-output/146980 (accessed 2026-09-26)
- https://forum.cursor.com/t/can-cursor-cli-output-the-token-usage/156872 (accessed 2026-09-26)
- https://pypi.org/project/cursor-agent/ (accessed 2026-09-26)
- Pricing aggregators (secondary, cross-checked against cursor.com/help pages above): https://coworker.ai/blog/cursor-pricing, https://www.eesel.ai/blog/cursor-pricing (accessed 2026-09-26)
