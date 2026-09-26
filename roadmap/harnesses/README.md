# New harness backends: OpenCode, Cursor, Google Antigravity

**Status:** Planned · **Researched:** 2026-09-26 · **Line numbers:** Specstride `main`
649c11d. The research files cite e953aa6; `lib/critic.py` lines past L1384 are 15 lower
there, and `proposer.sh` lines past L1649 differ. · **Prompt:**
[prompts/new-harness-backends-research.md](../prompts/new-harness-backends-research.md)

This directory turns the harness research into spec-sized roadmap items. Each item below
and in the per-harness files is meant to become one Spec Kit feature
(`/speckit.specify`) and one PR.

| Harness | Roadmap | Proposer | Critic | Tested live |
|---|---|---|---|---|
| OpenCode | [opencode.md](opencode.md) | go-with-caveats | go-with-caveats (direct HTTP) | v1.18.32, real model turns |
| Cursor | [cursor.md](cursor.md) | go-with-caveats (blocked on credentials) | go-with-caveats (CLI only) | 2026.09.26-dd393fe, up to the auth gate |
| Google Antigravity (`agy`) | [antigravity.md](antigravity.md) | go-with-caveats | go (direct Gemini HTTP, not `agy`) | 1.2.11, up to the model API |
| Gemini CLI | not written | n/a | n/a | not researched (see below) |

The raw research, with every source and confidence level, is in
[../research/harnesses/](../research/harnesses/): one `<harness>-contract.md` (the
questionnaire and live smoke tests) and one `<harness>-ecosystem.md` (licence, terms,
release churn, open issues) per harness.

## Why Gemini CLI was not researched

The prompt's gate adds Gemini CLI only if Antigravity cannot run one unattended headless
pass. It can. Antigravity CLI (`agy`) has a documented print mode (`-p`,
`--output-format stream-json`, `--input-format stream-json`), and auth by API key
(`GEMINI_API_KEY` with `modelProvider: gemini`) never opens a browser. The live test
reached `generativelanguage.googleapis.com` and got the documented exit codes. Google also
stopped serving Gemini CLI to personal, Pro, Ultra and free accounts on 2026-06-18 and
names `agy` as its successor. Gemini CLI still works only under Gemini Code Assist
Standard/Enterprise licences. A short pre-research paragraph is kept in
[antigravity-ecosystem.md](../research/harnesses/antigravity-ecosystem.md) in case that
changes.

The Google **critic** path (a direct Gemini API call) does not depend on either CLI. It
is item [AG-1](antigravity.md#ag-1).

## Comparison against the backend contract

| Question | OpenCode | Cursor | Antigravity |
|---|---|---|---|
| P1 headless entrypoint | `opencode run` (documented) | `agent -p` (documented, beta) | `agy -p` / NDJSON stdin (documented) |
| P2 prompt > 128 KiB | stdin ✅ live (200 KiB) | stdin (reaches the auth gate; model acceptance unknown) | `--input-format stream-json` stdin ✅ live (200 KiB) |
| P3 unattended permissions | asks are auto-rejected, never hang ✅; no `--auto`, deny rules instead | `--force --trust`; without them it auto-denies (inferred), though the docs contradict each other | `--dangerously-skip-permissions`; without it, tools are soft-denied (rc 0) since 1.1.3; narrow allow-rules often fail to match headless |
| P4 throwaway config home | `HOME` + `XDG_*` + `TMPDIR` ✅ live (XDG without `HOME` untested) | `HOME` + `XDG_*` ✅ live (`CURSOR_CONFIG_DIR` inferred); some paths are fixed under `~/.cursor` | `HOME` overlay ✅ live; whether cwd becomes the workspace is **conflicting** (may need `--add-dir`) |
| P5 fresh session per pass | ✅ | ✅ | ✅ (a cross-session "knowledge" store exists) |
| P6 model naming | `provider/model` (same as `dsh`) | Cursor slugs; default `auto` router | `agy` slugs, Gemini-only in API-key mode |
| P7 auto-loaded context can be turned off | ✅ env vars + `skill: deny` | ❌ hidden `--exclude-workspace-context` only | ❌ rules always load; `--disable-slash-commands` only stops expansion |
| P8 structured stream | JSONL, no init/result record | stream-json, Claude-like but different | NDJSON `init`/`step_update`/`result` |
| P9 exit codes | 0/1 (+ JSON `error`) | 0/1/130/143, no error `result` | 0/1/2/3 + `AGY_ERROR` on stderr |
| P10 unattended auth | provider env keys ✅ | `CURSOR_API_KEY` (free keys reportedly work for the CLI) | `GEMINI_API_KEY` ✅ (but no creds = a silent 60 s OAuth wait) |
| C1 critic path | direct HTTP to the named provider | CLI `--mode ask` only (no model API) | direct Gemini HTTP |
| O2 version pinning | npm version pin ✅; v2 rewrite in flight | versioned tarball URL ✅ live + hidden `--disable-auto-update` | none found; self-updates every run; 54 releases 2026-06-01 → 09-25 |
| Open hang reports | many (`run` startup, never-exits #44901) | many (forum, several releases) | none open; the #548 permission hang was fixed in 1.1.3 |
| Cost in stream | USD per step (models.dev providers) | tokens only | tokens only |

## Recommended integration order

1. **Shared prerequisites SH-1 → SH-3**, because every harness needs them and they fix
   existing duplication.
2. **AG-1, the `gemini` critic.** It is the smallest item and doesn't depend on any CLI;
   it's a direct HTTP call like `codex`.
3. **OpenCode (OC-1 → OC-4).** It is the only harness proven live end to end. It's open
   source, `provider/model` naming matches `dsh`, and its critic reuses existing HTTP
   code. Pin v1.18.x.
4. **Antigravity (AG-0 spike → AG-2 → AG-3).** The headless path is proven up to the
   model call. The spike needs one `GEMINI_API_KEY` and must settle whether the cwd
   becomes the workspace (or needs `--add-dir`) before the arm lands.
5. **Cursor (CU-0 spike → …).** Last, because nothing past the auth gate is verified, it
   needs a `CURSOR_API_KEY`, its context guard and tool restriction rely on hidden flags,
   and the critic must pay full agent overhead.

## Shared prerequisites

These are written once here. The per-harness items depend on them rather than repeating
them. Line numbers are against `main` 649c11d.

### SH-1
**Backend name registry**
- **Goal:** there is one list of backend names and spellings, and every help text, error
  message and doc is checked against it.
- **Why:** the list `dsh | claude | codex | bebop | prime` is copied by hand into
  `orchestrator.sh:60-64` (help; the defaults are at L194-195), `proposer.sh:40,497`,
  `lib/critic.py:17,2379,2469`, `.env.example:11-27`, `README.md`,
  `wiki/Configuration.md` and `reversed/`. Each new harness would otherwise touch all
  of them.
- **Touchpoints:** those above, plus a new registry file (for example
  `lib/backends.py` with a `--list` mode that bash can call).
- **Acceptance criteria:**
  - Given the registry lists a backend, when `orchestrator.sh --help`,
    `proposer.sh`'s unknown-backend error and `critic.py --help` are rendered, then
    each shows exactly the registry's names.
  - Given a doc or help text names a backend that is not in the registry (or leaves one
    out), when the test suite runs, then a guard test fails and names the file.
- **Tests:** a new `lib/test_backend_registry.py` in the style of `lib/test_rename_guard.py`.
- **Depends on:** none · **Size:** M · **Confidence:** verified (repo read).

### SH-2
**Generic throwaway config-home overlay**
- **Goal:** any CLI backend can run in a per-pass (or per-attempt) throwaway config home
  that leaves nothing global behind.
- **Why:** all three harnesses keep state, sessions and self-update data in `HOME` or
  `XDG_*`. Each was isolated live with `HOME` overridden (OpenCode: `HOME` + `XDG_*` +
  `TMPDIR`; Cursor: `HOME` + `XDG_*`; Antigravity: `HOME`). An overlay without `HOME`
  (XDG only, or `CURSOR_CONFIG_DIR`) is untested. Today only `dsh` has an overlay
  (`dsh_make_home_overlay`, `proposer.sh:366`).
- **Touchpoints:** `proposer.sh` (a new `make_backend_overlay <backend> <dir>` next to
  `dsh_make_home_overlay`), and the `lib/critic.py` shell-out helpers.
- **Acceptance criteria:**
  - Given a backend declares its overlay variables, when a pass runs, then those
    variables point into a `mktemp -d` directory that is removed afterwards, even if the
    pass is killed by the watchdog.
  - Given `HOME` is overlaid, when the agent runs `git commit`, then the operator's git
    identity still applies, passed through as `GIT_AUTHOR_*`/`GIT_COMMITTER_*` or a
    copied `.gitconfig`.
  - Given `SPECSTRIDE_<BACKEND>_OVERLAY=attempt`, when several passes run in one attempt,
    then they share one overlay (warm caches) and it is removed when the attempt ends.
- **Tests:** a new `lib/test_backend_overlay.py`, using a fake binary that records its
  env and writes into `$HOME`/`$XDG_*`.
- **Depends on:** none · **Size:** M · **Confidence:** verified-live (all three overlays).

### SH-3
**A seam for new stream adapters**
- **Goal:** adding a harness's JSONL stream means writing one adapter class, not editing
  `agent_stream.py`'s control flow.
- **Why:** none of the three streams matches `claude-stream-json` or `prime-v3`. All
  three need the same shared behaviour:
  - a made-up `agent_init` when the stream has none (OpenCode);
  - a `missing_terminal` result when the stream ends without a result record;
  - tool-name normalisation, because `looks_like_evidence` (`agent_stream.py:110`) only
    knows `Write/Edit/MultiEdit/NotebookEdit/Bash`.

  The target-path gaps are:
  - OpenCode's `filePath` is lowercased to `filepath` by
    `ObservabilityPolicy.extract_target_paths` (`lib/observability_policy.py:147-163`),
    which is in neither `TARGET_KEYS` set (`agent_stream.py:23`,
    `observability_policy.py:22`);
  - `tool_target` (`agent_stream.py:96`) does not recurse.
  Nested `path` keys (Cursor) are already found.
- **Touchpoints:** `lib/agent_stream.py` (`select_provider_adapter` and
  `observability_start`, L262-294; `looks_like_evidence`; `tool_target`; `TARGET_KEYS`),
  `lib/observability_policy.py` (`TARGET_KEYS`),
  `lib/prime_stream.py` (the pattern), `proposer.sh` `run_iteration()` (L1443-1502,
  the structured-branch condition and `--provider-format`).
- **Acceptance criteria:**
  - Given a registered adapter for provider format `X`, when `proposer.sh` runs a
    backend whose registry entry says `stream: X`, then its stdout goes through that
    adapter, with no backend-name checks added to `run_iteration()`.
  - Given an adapter maps a tool to `Write` with target `hello.txt`, and `hello.txt` is
    the expected evidence, when the event arrives, then `evidence_writing` fires.
- **Tests:** `lib/test_agent_stream*.py` and `lib/test_telemetry_parity.py` (existing
  backends keep emitting exactly what they do today).
- **Depends on:** SH-1 · **Size:** M · **Confidence:** verified (repo read).

### SH-4
**Per-backend auth preflight**
- **Goal:** a run whose backend has no working credentials stops before pass 1 with a
  clear message, instead of being killed by the watchdog.
- **Why:**
  - `agy` with no credentials sits silently for about 60 s waiting for OAuth before it
    exits 1 (verified live).
  - `agent status --format json` exits 0 even when logged out, so its exit code can't be
    used (verified live).
  - OpenCode and Cursor fail fast, but only once a pass has started.
- **Touchpoints:** `orchestrator.sh` (pre-launch checks), plus a new `preflight` entry in
  the SH-1 registry for each backend.
- **Acceptance criteria:**
  - Given a registry entry whose `preflight` command fails (tested with a fake backend),
    when a run starts, then it exits non-zero before pass 1, within 5 s, and prints the
    preflight's message.
  - Given an existing backend with no preflight entry, then launch behaviour is
    unchanged.
  - The harness-specific preflights (Antigravity: `GEMINI_API_KEY` set; Cursor:
    `agent status --format json` → `isAuthenticated`) are acceptance criteria of AG-2 and
    CU-1.
- **Tests:** `lib/test_specstride_cli.py` or a new test with fake binaries.
- **Depends on:** SH-1 · **Size:** S · **Confidence:** verified-live.

### SH-5
**Recording harness versions and detecting drift**
- **Goal:** every pass records the exact harness version it ran, and a run warns (or
  stops, if configured) when the version changes mid-run.
- **Why:** all three harnesses change fast:
  - OpenCode shipped 134 releases in 6 months, with a v2 rewrite in progress.
  - Cursor self-updates by default.
  - `agy` self-updates on every run, with no opt-out found.
  An unattended loop can end up on a different binary between passes.
- **Touchpoints:** `proposer.sh` `preserve_producer_status` (L512), the `producer.json`
  sidecar. Today its only caller is the structured branch of `run_iteration()` (L1540),
  so `dsh`/`codex` and unstructured passes never write it. This item also calls it on
  the unstructured path. Plus the SH-1 registry (a per-backend `version` command) and
  `specstride status`.
- **Acceptance criteria:**
  - Given any CLI backend, structured or not, when a pass finishes, then `producer.json`
    exists and has a `harness_version` field.
  - Given pass N and pass N+1 report different versions, when the second finishes, then
    the run log shows a `harness_version_changed` warning, and with
    `SPECSTRIDE_PIN_HARNESS=1` the run stops with a distinct exit code.
- **Tests:** `lib/test_invocation_artifacts.py`.
- **Depends on:** SH-1 · **Size:** S · **Confidence:** verified-live (for the churn);
  `agy` has no `--version` flag, so its source is settled in AG-0.

### SH-6
**Telling a harness hang apart from an agent stall**
- **Goal:** when the stream has already delivered its final record but the process
  doesn't exit, the pass is recorded as a harness hang, not as an agent stall.
- **Why:**
  - OpenCode #44901: it prints its final `step_finish` and never exits.
  - Cursor forum reports: `-p` hangs with no output.
  - Antigravity 1.2.9: a headless run waits for daemon children and background tasks, up
    to a 30-minute cap, after the answer.
  Today the idle watchdog kills all of these without knowing which kind of failure it
  was.
- **Touchpoints:** `proposer.sh` `run_with_idle_watchdog` (L821),
  `watchdog_kill_class` (L1633), and the SH-3 adapters.
- **Acceptance criteria:**
  - Given an adapter saw its final record, when the process is still alive
    `SPECSTRIDE_TERMINAL_GRACE` seconds later (default 30), then it is killed and the
    result is classified as `harness_hang_after_terminal`, keeping the adapter's result.
- **Tests:** `lib/test_proposer_watchdog.py`, with a fake binary that prints a final
  record and sleeps.
- **Depends on:** SH-3 · **Size:** S · **Confidence:** documented (upstream issues);
  none of these hangs was reproduced here.

### SH-7
**Critic context windows looked up by model id**
- **Goal:** a critic backend that routes to someone else's API (OpenCode → OpenAI or
  Anthropic, `gemini` → Google) gets the right context window from the model id.
- **Why:** `_critic_context_tokens()` (`lib/critic.py:177`) switches on the provider
  name, and its tables are per provider (`_CLAUDE_…` L154, `_CODEX_…` L172). Also:
  - OpenCode's catalog (models.dev) gives `gpt-5.5` 1,050,000 tokens, where the table
    says 1,000,000.
  - The budget should use each model's input limit, not its total context. For example
    `gpt-5`/`gpt-5.4-mini` are 272,000 input out of 400,000, and `gpt-5.5` is 922,000
    out of 1,050,000.
- **Touchpoints:** `lib/critic.py:154-213`.
- **Acceptance criteria:**
  - Given a model id present in the table with an input limit (for example `gpt-5`
    through the existing `codex` critic), when the grounding budget is computed, then it
    uses the input limit (272,000), not the total context.
  - The provider-routed lookups (`opencode:openai/<m>`, `gemini:<m>`) are acceptance
    criteria of OC-4 and AG-1.
  - Given an unknown model id, then it falls back to `_DEFAULT_CONTEXT_TOKENS`, and
    `SPECSTRIDE_CRITIC_CONTEXT_TOKENS` still wins.
- **Tests:** `lib/test_critic.py`.
- **Depends on:** none · **Size:** S · **Confidence:** verified-live (catalog query);
  every figure must be checked against the vendor's own docs before it goes in.

## Host facts found along the way

- The `OPENAI_API_KEY` in this host's environment is rejected by api.openai.com with a
  401 ("Incorrect API key"). That affects today's `codex` critic path as well, not just
  OpenCode.
- No `CURSOR_API_KEY` or Google key is available on this host. CU-0 and AG-0 need one.
- The OpenCode live runs used `LITELLM_API_KEY` from the environment against the local
  gateway (`localhost:4000`), because the OpenAI key was rejected.

## Corrections to the raw research

An adversarial verification pass checked the roadmaps against the research, the code and
the live web, and its findings are already applied above. The raw files under
`../research/harnesses/` are kept as the agents wrote them (with scratch paths and IDs
scrubbed). Where they disagree with the roadmaps, the roadmaps win:

- **antigravity-ecosystem.md O4** calls the #548 headless permission hang open. It was
  fixed in 1.1.3 (CHANGELOG: soft-deny instead of hanging), and a community report
  confirms it on 1.1.27/1.2.11. #548 stays open for allow-rule matching.
- **antigravity-ecosystem.md** says there's "no BYO API key". `GEMINI_API_KEY` with
  `modelProvider: gemini` shipped in 1.1.13, and #78 is closed.
- **antigravity-ecosystem.md O2** says "~15 releases". There were 54, from 2026-06-01
  to 2026-09-25.
- **antigravity-contract.md P4** says the workspace is the cwd. A community report says
  `--add-dir` is needed, so this is conflicting and goes to AG-0.
- **cursor-contract.md P10** says API keys need a paid plan. The ecosystem report says
  free keys work for the CLI.
- **cursor-ecosystem.md O2** says versions can't be pinned. Fetching a versioned tarball
  by URL works (checked live).
- **opencode-ecosystem.md** says no switch disables `AGENTS.md`.
  `OPENCODE_DISABLE_PROJECT_CONFIG` drops the root file, and nested files still attach.
