# Cursor as a Specstride backend

**Status:** Planned · **Researched:** 2026-09-26 against Cursor Agent CLI
`2026.09.26-dd393fe` (binary `agent`, legacy `cursor-agent`) · **Verdict:** proposer
**go-with-caveats**, critic **go-with-caveats** (CLI only). Both are blocked on a
credentialed spike.

## Summary

- `agent -p` is documented for scripts and CI. What was checked live, with no
  credentials:
  - the install, with `HOME`/`XDG_*` overridden;
  - the flag list;
  - a 200 KiB prompt on stdin is read, while as an argument it fails with E2BIG;
  - the config overlay;
  - auth failures exit 1 in under 2 s, with no hang.
- **No agent turn was run.** There is no `CURSOR_API_KEY` on the host, so everything
  after the auth check is taken from the docs or read from the shipped code.
- **Blockers:**
  - Auto-loaded `AGENTS.md`, `CLAUDE.md`, `.cursor/rules` and skills can only be turned
    off with a hidden flag (`--exclude-workspace-context`).
  - Cursor offers no model API, so the critic has to run the full agent in read-only
    `--mode ask`.
  - Headless hangs have been reported on the forum across several releases.
- **Spelling:** `cursor[:<cursor-model-slug>]`. Cursor slugs aren't `provider/model`.

## Verified facts

| id | fact | confidence | source |
|---|---|---|---|
| P1 | `agent -p [--output-format text\|json\|stream-json] [--force] [--trust] [--workspace DIR] [--model M]`. Versions are date-hashes from a `/lab/` channel. | documented; verified-live (flags) | [contract P1](../research/harnesses/cursor-contract.md); cursor.com/docs/cli/headless |
| P2 | Stdin is read in full when no prompt argument is given. A 205 KB stdin prompt reached the auth check. A 200 KiB argument fails with rc 126. | inferred (code); verified-live (argument E2BIG, stdin reaches auth) | contract P2 |
| P3 | `--force` (`--yolo`) means "Run Everything". Without it, the allowlist applies (default `Shell(ls)`) and other requests are auto-denied by a headless handler, with no prompt. The docs contradict each other on writes without `--force` ("changes are only proposed" vs "full write access in non-interactive mode"). Without `--trust`, an untrusted workspace exits 1. `--sandbox enabled` exists. | documented (flags; conflicting on writes); inferred (auto-deny handler) | contract P3, Risks |
| P4 | Config lives in `$CURSOR_CONFIG_DIR`, else `$XDG_CONFIG_HOME/cursor`, else `~/.cursor`. Some paths are hardwired to `~/.cursor/{projects,worktrees}`. The XDG overlay kept the real `~` untouched. | verified-live (XDG); inferred (fixed paths) | contract P4, smoke 10 |
| P5 | Each `-p` run starts a new chat unless `--resume`/`--continue` is given. | documented | contract P5 |
| P6 | `--model <cursor-slug>` (e.g. `claude-opus-4-8[context=1m]`). The account default is probably `auto` (router). The CLI can't use BYO provider keys. | documented; unknown (default) | contract P6 |
| P7 | `.cursor/rules`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, skills and `mcp.json` load from the workspace. There is no documented switch. The hidden `--exclude-workspace-context` exists. An open feature request asks for a switch. | documented (load); inferred (hidden flag) | contract P7; [ecosystem](../research/harnesses/cursor-ecosystem.md) (forum 138971) |
| P8 | stream-json record types: `system/init`, `user`, `assistant`, `tool_call` started/completed, and a final `result` (success only). `usage` is camelCase tokens with no cost; forum reports say token usage in stream-json is unreliable. Text mode prints only at the end. | documented; inferred (usage, tool kinds); disputed (usage presence) | contract P8; ecosystem O3 |
| P9 | Exit codes: 0, 1 (any error, no `result` record), 130, 143. | verified-live (1); inferred (the rest) | contract P9 |
| P10 | `CURSOR_API_KEY` or `--api-key`. Per the ecosystem report, free/Hobby keys work for the CLI (only the Cloud Agents API needs a paid plan). `agent status --format json` exits 0 even when logged out, so the preflight must read `isAuthenticated`. | verified-live (status, auth failures); documented (plan) | contract P10; ecosystem O1 |
| C1 | Cursor has no model-inference API; its Cloud Agents API is async repo work. The critic must use the CLI: `--mode ask --output-format json`, an empty workspace, and read `.result`. There is no public "no tools" flag. | documented | contract C1; cursor.com/docs/api |
| C2 | Cursor publishes no context table per model. Composer 2.5 advertises 200K, but usable context is reported at 70-120K. | unknown / documented | contract C2; ecosystem models table |
| O1 | The terms have no automation ban, and staff say embedding Cursor in an orchestrator is intended use. There are no published CLI rate limits. The forum reports that concurrent headless runs fail. | documented | ecosystem O1 |
| O2 | The installer always pulls the latest version and the CLI auto-updates. An older version can be fetched by URL (`downloads.cursor.com/lab/2026.07.01-41b2de7/linux/x64/agent-cli-package.tar.gz` returned HTTP 200; the `production/` channel returns 403) and run as `versions/<ver>/cursor-agent`. The hidden `--disable-auto-update` stops updates. The default model changed to Composer 2.5 on 2026-06-22. | documented (auto-update); verified-live (fetch by URL); inferred (`--disable-auto-update`) | ecosystem O2; contract P4/Risks; verifier download check 2026-09-26 |
| O4 | Forum threads report `-p` hangs with no output across releases, including 2026.07.01, after the April stdin-pipe fix. | documented (forum); not reproduced | ecosystem stability section |

## Mapping to the backend contract

| Touchpoint | What Cursor needs |
|---|---|
| `run_agent()` (`proposer.sh:394-499`) | A `cursor|cursor:*` arm: the absolute binary path (resolved before overlaying `HOME`), `-p --force --trust --workspace "$WORKDIR" --output-format stream-json --disable-auto-update`, and the prompt through `SPECSTRIDE_STDIN_FILE`. |
| `run_iteration()` (L1408-1510) | stream-json is always on, because text mode is silent until the end and would trip the idle watchdog. `--provider-format cursor-stream-json`. |
| Stream adapter | A new `CursorAdapter` through SH-3. Tool kind = the key of `tool_call` minus `ToolCall`. Target from `args.path`/`args.command`. Usage from camelCase fields. No `result` + rc≠0 = `missing_terminal`. |
| `critic_call()` (`lib/critic.py:2337`) | `cursor[:slug]` → a new `call_cursor_shell` (ask mode, json, empty `mkdtemp` workspace, overlay env). |
| `_critic_context_tokens()` (L177) | There is no reliable table. Require `SPECSTRIDE_CRITIC_CONTEXT_TOKENS`, or fall back to `_DEFAULT_CONTEXT_TOKENS`. |
| Preflight | SH-4: `agent status --format json` → `isAuthenticated:true`. |

## Roadmap items

### CU-0
**Spike: a credentialed Cursor run**
- **Goal:** turn every Cursor fact that the proposer arm and the adapter depend on into a
  verified-live fact.
- **Why:** no agent turn was run, because the host has no `CURSOR_API_KEY`. P2
  (model-side), P3 (auto-deny), P7 (the hidden flag), P8 (the real stream) and C1 (ask
  mode is tool-less) are all inferred or documented only.
- **Acceptance criteria:**
  - Using a `CURSOR_API_KEY` (try a free/Hobby key first) in a scratch overlay, record
    live evidence for:
    1. "reply OK";
    2. `hello.txt` written under `--force --trust`;
    3. the same prompt without `--force`: denied, merely proposed, or applied (the docs
       disagree), plus the rc, and whether it hangs;
    4. a 200 KiB stdin prompt accepted by the model;
    5. a real stream-json sample with tool calls, saved as the CU-2 fixture, including
       whether `usage` is actually present;
    6. whether `--exclude-workspace-context` drops a planted `AGENTS.md` (by asking the
       model to quote it);
    7. whether `--mode ask` with an empty workspace makes any tool call;
    8. whether `--disable-auto-update` stops the update, and whether a second concurrent
       `-p` run fails;
    9. whether a config-only overlay (without `HOME`) leaks into `~/.cursor/projects`;
    10. whether `--exclude-workspace-context` is listed in the version's bundle and how
        an unknown option is handled (exit code), as the probe for CU-3.
  - Results go into `roadmap/research/harnesses/cursor-contract.md` as an addendum.
- **Depends on:** — · **Size:** M · **Confidence:** inferred (hence the spike).

### CU-1
**A minimal Cursor proposer arm**
- **Goal:** `specstride run --proposer cursor[:slug]` runs a real Ralph pass that edits
  files in the workdir.
- **Why:** P1-P6 and P10. CU-0 verifies them.
- **Touchpoints:** `proposer.sh:394-499`, SH-1, SH-2 (config-only overlay:
  `CURSOR_CONFIG_DIR` + `XDG_CONFIG_HOME` + `XDG_CACHE_HOME`, or `HOME` if CU-0 shows
  it's needed), SH-4.
- **Acceptance criteria:**
  - Given a fake `agent`, when a pass runs, then the fake sees:
    - the prompt on stdin, not in argv;
    - `-p --force --trust --workspace <workdir> --output-format stream-json
      --disable-auto-update`;
    - the overlay env;
    - `--model` only when a slug was given (a slug together with `--model` is rejected,
      like `dsh`).
  - Given a real CLI and a key, when a phase asks for `hello.txt`, then the file exists
    and the pass exits 0.
  - Given `agent status --format json` reports `isAuthenticated:false` (rc 0), when a run
    starts, then it stops before pass 1 and says to set `CURSOR_API_KEY` (through SH-4).
- **Tests:** a new `lib/test_cursor_backend.py`.
- **Depends on:** CU-0, SH-1, SH-2, SH-4 · **Size:** M · **Confidence:** inferred today
  (P2, P3); CU-0 turns it into verified-live.

### CU-2
**A Cursor stream adapter**
- **Goal:** Cursor passes produce the standard `events.jsonl` events: tool calls,
  evidence, token usage, and a final result or `missing_terminal`.
- **Why:** P8 and P9. Every failure is "exit 1 + stderr + no `result`".
- **Touchpoints:** a new `lib/cursor_stream.py` through SH-3.
- **Acceptance criteria:**
  - Given the CU-0 live fixture, then the adapter emits:
    - `agent_init` (model from `system/init`);
    - `agent_tool` for each `tool_call/started`, with normalised names;
    - `evidence_writing` on an edit to the expected file;
    - `agent_result` with tokens when `usage` is present (null otherwise) and
      `cost_usd: null`.
  - Given a stream with no `result` and rc 1, then it emits `missing_terminal`, and the
    last non-JSON line is kept as the reason. `run_iteration` merges stderr into stdout
    (`proposer.sh:1535,1538`), so the adapter can't tell them apart.
  - Given unknown record types (`connection`, `retry`, `interaction_query`), then they
    are ignored without a schema error, and an `interaction_query` rejection is logged
    as a diagnostic.
- **Tests:** `lib/test_agent_stream*.py` and `lib/test_telemetry_parity.py`.
- **Depends on:** CU-0, CU-1, SH-3, SH-6 · **Size:** M · **Confidence:** documented
  until CU-0.

### CU-3
**Guard against auto-loaded context for Cursor**
- **Goal:** Cursor passes don't carry the target repo's rules, skills or
  `AGENTS.md`/`CLAUDE.md` unless the operator opts in.
- **Why:** P7, the same risk as the `claude` skill-overflow incident. The only switch is
  a hidden flag that may disappear.
- **Touchpoints:** the CU-1 arm and the SH-4 preflight.
- **Acceptance criteria:**
  - Given default settings, then `--exclude-workspace-context` is passed.
  - Given `SPECSTRIDE_PROPOSER_SKILLS=1`, then it is not passed.
  - Given a CLI version where the probe chosen by CU-0 item 10 (for example, the option
    string missing from the installed version's bundle) says the flag is gone, then the
    preflight warns once and the arm runs without it. The flag's absence is recorded in
    `producer.json`.
- **Depends on:** CU-0, CU-1, SH-4, SH-5 · **Size:** S · **Confidence:** inferred
  (hidden flag).

### CU-4
**Cursor as a critic**
- **Goal:** `--critic cursor:<slug>` returns a verdict from a read-only Cursor run.
- **Why:** C1-C3. There's no HTTP path.
- **Touchpoints:** `lib/critic.py` (`critic_call`, a new `call_cursor_shell`,
  `_critic_context_tokens`).
- **Acceptance criteria:**
  - Given `cursor:<slug>`, then the argv is
    `[agent, -p, --mode, ask, --output-format, json, --trust, --workspace, <empty tmp>,
    --disable-auto-update, --model, <slug>]` (plus `--exclude-workspace-context` per
    CU-3), with the prompt on stdin, and the verdict is parsed from `.result`.
  - Given `cursor` without a slug, or with `auto`, then the critic refuses to start,
    because a non-deterministic router can't be budgeted.
  - Given no `SPECSTRIDE_CRITIC_CONTEXT_TOKENS`, then a warning names the fallback window.
  - If CU-0 shows that ask mode still calls tools, this item is re-scoped before it
    starts.
- **Tests:** `lib/test_critic.py`, with a fake binary.
- **Depends on:** CU-0, SH-1, SH-2, SH-7 · **Size:** M · **Confidence:** documented.

### CU-5
**Pin the Cursor version**
- **Goal:** a run uses a fixed Cursor CLI version and records it.
- **Why:** O2: the installer tracks latest, the CLI self-updates, and there's a lab
  channel. The default model changed silently on 2026-06-22.
- **Touchpoints:** `wiki/Configuration.md` (install a named tarball,
  `downloads.cursor.com/lab/<ver>/linux/x64/agent-cli-package.tar.gz`, then point
  `SPECSTRIDE_CURSOR_BIN` at `versions/<ver>/cursor-agent`), SH-5.
- **Acceptance criteria:**
  - Given the documented install, then `producer.json.harness_version` equals the pinned
    version on every pass of a multi-pass run.
- **Depends on:** CU-0 (confirms `--disable-auto-update`), SH-5 · **Size:** S ·
  **Confidence:** verified-live (fetch by URL); inferred (`--disable-auto-update`).

## Risks and unknowns

- **Nothing past the auth check has been exercised.** Every item after CU-0 may be
  re-scoped by it.
- **Hidden flags.** `--exclude-workspace-context`, `--disable-auto-update`,
  `--exclude-tools` and `--single-turn` are internal and may change without notice.
- **Headless hangs** recur on the forum. SH-6 makes the kills diagnosable.
- **Overlaying `HOME`** hides `~/.gitconfig`/`~/.ssh` from the agent's shell (SH-2 covers
  git identity), and a 558 MB install matters for CI images.
- **Cost:** the stream reports tokens only, and pricing is plan credit pools, so
  Specstride can't report USD for Cursor passes.
- **Team admins** can turn off "Run Everything", after which `--force` exits 1 with a
  clear message.

## Open questions for the operator

1. Is a Cursor API key available for CU-0? A free key should do for the CLI. Without
   one, the whole Cursor track stays at the spike.
2. For the Cursor critic, is full agent overhead per verdict acceptable? The alternative
   is to keep the critic on another backend and use Cursor as proposer only.
