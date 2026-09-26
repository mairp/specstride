# OpenCode as a Specstride backend

**Status:** Planned · **Researched:** 2026-09-26 against OpenCode v1.18.32 (npm
`opencode-ai`, `anomalyco/opencode` commit 545f51d) · **Verdict:** proposer
**go-with-caveats**, critic **go-with-caveats** (direct HTTP)

## Summary

- `opencode run` is a documented headless command. Real model runs worked live: stdin
  prompts up to 200 KiB, file writes, a JSONL stream, exit codes 0/1 with a JSON `error`
  record, and a complete `XDG_*` + `TMPDIR` overlay.
- It never hangs on a permission prompt in `run` mode; it auto-rejects.
- The risks are upstream: open hang bugs in `run` (it hangs at startup, or prints its
  final record and never exits), the stream only prints when a step finishes, a v2
  rewrite is in progress, and it ships more than 20 releases a month.
- **Spelling:** `opencode[:provider/model]`, the same shape as `dsh:<provider/model>`.
- **Critic:** call the named provider's API directly (`call_openai_chat` /
  `call_claude`). OpenCode has no model API of its own.

## Verified facts

| id | fact | confidence | source |
|---|---|---|---|
| P1 | `opencode run [message..]` runs one prompt and exits when the session goes idle. Recommended: `opencode run --format json --title <t> -m <p/m> --dir "$WORKDIR" < prompt_file`. | verified-live | [contract P1](../research/harnesses/opencode-contract.md); opencode.ai/docs/cli |
| P2 | A 204,878-byte prompt on stdin worked (rc 0, 80,741 input tokens). As an argument it fails with E2BIG (rc 126). A non-TTY stdin that never closes makes `run` block. | verified-live | contract P2, smoke `big`/`big-argv` |
| P3 | Permission requests are auto-rejected in `run` unless `--auto` is given. `--auto` (aliases `--yolo`, `--dangerously-skip-permissions`) also approves `external_directory`. There's no OS sandbox. Deny rules can be passed with `OPENCODE_PERMISSION`. | verified-live (`--auto` writes); source-read (auto-reject) | contract P3; `run.ts` L801-821 |
| P4 | `HOME` + four `XDG_*` + `TMPDIR` pointed at scratch isolated everything. XDG without `HOME` was not tested. Without `TMPDIR`, runs share `/tmp/opencode`. npm postinstall runs `opencode --version` with the caller's env and writes to the real home. | verified-live | contract P4, smoke `iso`, `install` |
| P5 | Each `run` creates a new session unless `-c`/`-s` is passed. | verified-live | contract P5 |
| P6 | `-m provider/model` is split on the first `/`. There's no default model and no env var for the model. | verified-live | contract P6 |
| P7 | Global/project `AGENTS.md`/`CLAUDE.md` and `.claude`/`.agents` skills load by default. `OPENCODE_DISABLE_CLAUDE_CODE`, `OPENCODE_DISABLE_EXTERNAL_SKILLS` and `permission.skill=deny` turn skills off (live). `OPENCODE_DISABLE_PROJECT_CONFIG` drops the project's root `AGENTS.md`/`CLAUDE.md` from the system prompt (`instruction.ts` L123). Subdirectory `AGENTS.md`/`CLAUDE.md`/`CONTEXT.md` files are still attached when the model reads files under them (`Instruction.resolve`, no flag check). | verified-live (skills); source-read (project config, nested files) | contract P7; `oc@545f51d:packages/opencode/src/session/instruction.ts` |
| P8 | `--format json` gives JSONL `step_start`/`tool_use`/`step_finish`(tokens, cost)/`text`/`error`. There's no init record, no model name and no result record. Records are written only when a part finishes. | verified-live | contract P8 + stream sample |
| P9 | rc 0 = idle with no error. rc 1 = any error, together with a JSON `error` (`APIError`, `UnknownError`). Hitting the max-steps limit also gives rc 0. | verified-live (0/1); inferred (signals, max-steps) | contract P9 |
| P10 | Provider keys come from their usual env vars or `{env:VAR}` in config. OAuth is optional. | verified-live | contract P10 |
| C1 | There's no tool-less mode. An agent with `permission: {"*":"deny"}` resolves every tool to off (checked at config level only). Direct HTTP is recommended. | verified-live (config) | contract C1 |
| C2 | The window comes from the models.dev catalog: `gpt-5.4-mini` 400k total / 272k input. The catalog's `gpt-5.5` figure is 1,050,000 (critic.py says 1,000,000). | verified-live (catalog) | contract C2 |
| cost | Leaving out `--title` triggers a second model call that re-sends the whole prompt to name the session. | verified-live | contract smoke `big` |
| O1 | MIT licence. Using a subscription OAuth login (e.g. Claude Pro) from automation has led to at least one Anthropic account ban. | documented | [ecosystem O1](../research/harnesses/opencode-ecosystem.md); issue #6930 |
| O2 | 134 v1 releases in about 6 months. `run` flags and permissions changed in at least 10 of about 20 cycles. v2 (`opencode.ai/v2/install`) removes `--command`/`--port` and has open `run` regressions. | verified-live (release count); documented | ecosystem O2 |
| O4 | Open headless hangs: #35870, #38723 (~56% startup hang reported), #47709, #46142, #44782, #40747, #44901. JSON bugs: #50514 (rc 0 with empty output), #49300, #47972. | documented | ecosystem O4 |

## Mapping to the backend contract

| Touchpoint | What OpenCode needs |
|---|---|
| `run_agent()` (`proposer.sh:394-499`) | An `opencode|opencode:*` arm. Prompt through `SPECSTRIDE_STDIN_FILE`. Args `run --title … -m … --dir "$WORKDIR"`, with no `--auto`. The `OPENCODE_PERMISSION` deny rules, the overlay (SH-2), and `OPENCODE_DISABLE_AUTOUPDATE=1`. |
| `run_iteration()` (L1408-1510) | Add `--format json` on the structured path, `--provider-format opencode-json`, and a `metadata.json` like Prime's. The skill guard is env vars, not `--disable-slash-commands`. |
| Stream adapter | A new `lib/opencode_stream.py` `OpenCodeAdapter` through the SH-3 seam. It maps tool names (`write`→Write, `edit`/`apply_patch`→Edit, `bash`→Bash) and the `filePath` target, fakes `agent_init` from the `-m` value, and adds up usage and cost from `step_finish`. |
| `critic_call()` (`lib/critic.py:2337`) | Handle `opencode[:provider/model]`: route `openai/*` or an OpenAI-compatible base to `call_openai_chat`, and `anthropic/*` to `call_claude`. |
| `_critic_context_tokens()` (L177) | Look up by model id (SH-7). |
| Name lists, `.env.example` | Through SH-1. New knobs: `SPECSTRIDE_OPENCODE_BIN`, `SPECSTRIDE_OPENCODE_PERMISSION`, `SPECSTRIDE_OPENCODE_CRITIC_MODEL`. |

## Roadmap items

### OC-1
**A minimal OpenCode proposer arm**
- **Goal:** `specstride run --proposer opencode:<provider>/<model>` runs a real Ralph pass
  that edits files in the workdir.
- **Why:** P1, P2, P3, P4 and P6 were all verified live on v1.18.32.
- **Touchpoints:** `proposer.sh:394-499` (a new arm; reject `--model` together with a
  suffix, like `dsh` does at L406-409), SH-1 registry entry, SH-2 overlay
  (`XDG_CONFIG/DATA/CACHE/STATE_HOME` + `TMPDIR`).
- **Acceptance criteria:**
  - Given a fake `opencode` on `PATH`, when a pass runs, then the fake sees:
    - the whole prompt on stdin, and nothing in argv;
    - `--title`, `-m <provider/model>`, `--dir <workdir>`, and no `--auto`;
    - `OPENCODE_PERMISSION` containing `"external_directory":"deny"`;
    - every `XDG_*` variable and `TMPDIR` inside a temporary dir, which is gone
      afterwards.
  - Given the overlay without `HOME` (the default, so the operator's git identity keeps
    working), when a real pass runs, then nothing is created under the real
    `~/.config/opencode`, `~/.local/share/opencode`, `~/.cache/opencode` or
    `/tmp/opencode`. If something is, the overlay adds `HOME` (SH-2 git passthrough).
  - Given a real OpenCode v1.18.x and a working provider key, when a phase asks for
    `hello.txt`, then the file exists and the pass exits 0.
  - Given `opencode` is not on `PATH`, then the arm exits 127 with a message naming
    `SPECSTRIDE_OPENCODE_BIN`.
- **Tests:** new `lib/test_opencode_backend.py`, modelled on `lib/test_prime_backend.py`.
- **Depends on:** SH-1, SH-2 · **Size:** M · **Confidence:** verified-live (with `HOME`
  overridden); inferred (XDG-only overlay, checked by the second criterion).

### OC-2
**Guard against auto-loaded context for OpenCode**
- **Goal:** an OpenCode pass doesn't pick up host or repo skills unless the operator asks
  for them.
- **Why:** P7. This is the same class of risk as the `claude` backend's skill-overflow
  incident (the `--disable-slash-commands` comment at `proposer.sh:1430-1437`).
- **Touchpoints:** the OC-1 arm's env.
- **Acceptance criteria:**
  - Given default settings, when a pass runs, then `OPENCODE_DISABLE_CLAUDE_CODE=1`,
    `OPENCODE_DISABLE_EXTERNAL_SKILLS=1` and `permission.skill=deny` are set.
  - Given `SPECSTRIDE_PROPOSER_SKILLS=1`, then none of them are set.
  - Given `SPECSTRIDE_OPENCODE_PROJECT_CONFIG=0`, then `OPENCODE_DISABLE_PROJECT_CONFIG=1`
    is set, and the docs say this also drops the repo's own `AGENTS.md`.
  - A live check: `opencode debug skill` in the overlay lists only `customize-opencode`.
  - A live check: with `OPENCODE_DISABLE_PROJECT_CONFIG=1`, a planted root `AGENTS.md`
    is not quoted by the model. A planted `sub/AGENTS.md` is recorded as still attached
    after a `read` of `sub/x`, and the docs note it.
- **Tests:** `lib/test_opencode_backend.py`.
- **Depends on:** OC-1 · **Size:** S · **Confidence:** verified-live (skills);
  source-read (project config).

### OC-3
**An OpenCode stream adapter**
- **Goal:** with `--agent-stream`, OpenCode passes produce the same `events.jsonl` events
  as the `claude` and `prime` backends: tool calls, evidence, usage, cost and a final
  result.
- **Why:** P8 and P9. Without the tool-name and `filePath` mapping, evidence detection
  silently never fires.
- **Touchpoints:** a new `lib/opencode_stream.py` through SH-3, and `run_iteration()`
  (adds `--format json`).
- **Acceptance criteria:**
  - Given the live sample recorded in
    [opencode-contract.md](../research/harnesses/opencode-contract.md#stream-sample),
    when it is replayed through the adapter, then it emits:
    - `agent_init` (model = the `-m` value);
    - one `agent_tool` `Write hello.txt`;
    - `evidence_writing` when `hello.txt` is the expected evidence;
    - `agent_result` success, with summed tokens and `cost_usd`.
  - Given a stream that ends with no `step_finish(reason=stop)` and no `error`, then
    the adapter emits `missing_terminal`.
  - Given an `error` record with `name: APIError`, then the result is an error with
    `reason_code=provider_error`, and it keeps `statusCode`/`isRetryable`.
  - Given no text and no successful tool in a pass that exits 0 (upstream #44267/#50514),
    then a `suspicious_empty_pass` diagnostic is emitted.
- **Tests:** `lib/test_agent_stream*.py` (the fixture comes from the sample) and
  `lib/test_telemetry_parity.py`.
- **Depends on:** OC-1, SH-3, SH-6 · **Size:** M · **Confidence:** verified-live.

### OC-4
**OpenCode as a critic, through direct HTTP**
- **Goal:** `--critic opencode:<provider>/<model>` judges passes with the same model an
  OpenCode proposer would use, without starting OpenCode.
- **Why:** C1 and C2. OpenCode has no model API of its own. The CLI adds an ~8k-token
  system prompt, `AGENTS.md`, a title call and the startup hang risk.
- **Touchpoints:** `lib/critic.py` `critic_call()` L2337-2379 and SH-7.
- **Acceptance criteria:**
  - Given `opencode:openai/<m>`, then `call_openai_chat` is called with `OPENAI_BASE_URL`
    and `OPENAI_API_KEY`.
  - Given `opencode:anthropic/<m>`, then `call_claude` is called.
  - Given any other provider, then the error names the supported prefixes and
    `SPECSTRIDE_OPENCODE_CRITIC_BASE_URL`/`_API_KEY` for an OpenAI-compatible endpoint.
- **Tests:** `lib/test_critic.py`.
- **Depends on:** SH-1, SH-7 · **Size:** S · **Confidence:** verified (repo read of the
  existing HTTP helpers).

### OC-5
**Pin the OpenCode version and install it cleanly**
- **Goal:** operators install a known-good OpenCode v1.18.x without touching their real
  home, and every run records the version it used.
- **Why:** O2: the fast release cadence and the v2 rewrite. P4: npm's postinstall writes
  to the real `$HOME`.
- **Touchpoints:** `wiki/Configuration.md` (install steps: `npm install --prefix …
  opencode-ai@<pin>` with the overlay env exported, or `--ignore-scripts`), SH-5.
- **Acceptance criteria:**
  - Given the documented install command, then nothing is created under the real `~` or
    `/tmp/opencode`.
  - Given a pass, then `producer.json.harness_version` is `1.18.x`.
  - Given a v2 binary (`opencode2`, or a version ≥ 2), then the preflight warns that v2 is
    unsupported.
- **Tests:** `lib/test_opencode_backend.py` (version parsing).
- **Depends on:** OC-1, SH-4, SH-5 · **Size:** S · **Confidence:** verified-live (the
  leak); inferred (the clean install command, which was recommended but not run).

### OC-6
**Spike: the facts OpenCode still leaves unverified**
- **Goal:** turn the remaining `inferred`/`unknown` OpenCode facts into verified ones, or
  record them as limits.
- **Why:** these were not tested live:
  - the SIGTERM/SIGINT exit codes;
  - what happens at the `steps` cap (rc 0?);
  - copying `auth.json` into the overlay for OAuth-only providers;
  - concurrent passes sharing one per-attempt overlay (SQLite locking);
  - how long a long `bash` tool goes with no stdout.
- **Acceptance criteria:**
  - One report under `roadmap/research/harnesses/` gives each fact a live source.
  - The report recommends `IDLE_TIMEOUT` guidance for OpenCode passes.
  - The report says whether `SPECSTRIDE_OPENCODE_OVERLAY=attempt` (SH-2) is safe.
- **Depends on:** OC-1 · **Size:** S · **Confidence:** inferred (hence the spike).

### OC-7
**Spike: `opencode serve` as the integration point**
- **Goal:** decide, from measurements, whether the proposer should drive a long-lived
  `opencode serve` over its HTTP/SSE API instead of running `opencode run` per pass.
- **Why:** O4. Several of the worst hang bugs are specific to the one-shot `run`
  lifecycle. The server exposes an OpenAPI 3.1 schema at `/doc`. It has its own open SSE
  bugs, though (#46733, #43519).
- **Acceptance criteria:**
  - Run 50 trivial passes on each of `run` and `serve`+HTTP, on the pinned version.
  - Record the hang rate, median wall time and exit/terminal clarity for each.
  - Recommend keep or switch, as a short research doc.
- **Depends on:** OC-3 · **Size:** M · **Confidence:** documented (hence the spike).

## Risks and unknowns

- **Upstream hangs** (high). The watchdog will fire sometimes; SH-6 makes the kill
  diagnosable.
- **Output only when a step finishes** (medium). A long tool call is silent on stdout,
  so `IDLE_TIMEOUT` has to allow for the longest expected tool. OC-6 measures this.
- **v2 migration** (medium). v2 removes flags and changes the process model. OC-5 keeps
  runs on v1 until a separate item ports the arm.
- **Cost figures are 0** for custom providers that have no `cost` config, and subagent
  cost is left out of session totals (#45417).
- **The OAuth-subscription ban risk** (#6930). The docs must steer operators to metered
  API keys.
- **The recorded run used a local gateway model** (`gemini-2.5-flash-lite`), because
  the host's `OPENAI_API_KEY` is rejected by OpenAI. The behaviour of OpenAI or
  Anthropic models themselves was not exercised.

## Open questions for the operator

1. Default for the repo's own `AGENTS.md`: load it, which is OpenCode's default and
   useful when the target repo documents itself, or drop the root one with
   `OPENCODE_DISABLE_PROJECT_CONFIG`? Nested ones load either way.
2. Overlay per pass (clean, but re-downloads the models catalog and provider packages)
   or per attempt (warm)? The default in OC-1 is per pass.
