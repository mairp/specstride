# Google Antigravity as a Specstride backend

**Status:** Planned · **Researched:** 2026-09-26 against Antigravity CLI (`agy`) 1.2.11,
linux-x64 · **Verdict:** proposer **go-with-caveats**; critic **go**, as a direct
Gemini API call (provider `gemini`, not through `agy`)

## Summary

- **What Antigravity is now.** It is no longer an IDE only. Google ships the
  closed-source `agy` CLI, with a documented headless mode that Google describes as "for
  CI pipelines". It replaced Gemini CLI for personal, Pro, Ultra and free accounts on
  2026-06-18.
- **What was confirmed live.** An API key works in place of browser sign-in:
  `GEMINI_API_KEY` plus `settings.json` `{"modelProvider":"gemini"}`. Also confirmed:
  - a 200 KiB prompt goes in through NDJSON on stdin;
  - the `init`/`step_update`/`result` stream;
  - exit codes 1, 2 and 3, and a structured `AGY_ERROR` line on stderr;
  - a throwaway `HOME` keeps all of its state out of the real home.
- **Not tested.** No real model turn ran, because there is no Google key on the host.
- **Caveats:**
  - `agy` updates itself on every run, with no opt-out found.
  - Whether the cwd becomes the workspace is **conflicting**. One report on 1.1.27 says
    it doesn't, and `--add-dir <repo>` is needed for workspace rules and for writes to be
    allowed.
  - The default tool list is large: browser tools, image generation, web search and
    subagents.
  - With no credentials it waits silently for about 60 s for OAuth before exiting.
  - The old headless permission hang (#548) was fixed in 1.1.3; such tools are now
    soft-denied. `--dangerously-skip-permissions` is reported to have its own silent
    tool-call failures.
- **Spelling:** `antigravity[:<agy-model-slug>]` for the proposer, and
  `gemini[:<model>]` for the critic.

## Verified facts

| id | fact | confidence | source |
|---|---|---|---|
| P1 | `agy -p "<prompt>"` runs once and exits. For big prompts: `agy --input-format stream-json --output-format stream-json < msg.ndjson` (one `{"event":"user","message":{"content":…}}` line). | verified-live (flags, stdin path); documented (support) | [contract P1](../research/harnesses/antigravity-contract.md); antigravity.google/docs/cli/headless |
| P2 | `-p` takes an argument, so the 128 KiB limit applies, and a bare `-p` consumes the next flag (rc 2). A 204,877-byte NDJSON prompt on stdin reached the model API. | verified-live | contract P2, inv3a/3b |
| P3 | `--dangerously-skip-permissions` → `permission_mode: "always-proceed"` (seen live). Without it, tools that need approval are soft-denied, with rc 0 and `denied_actions`. The headless docs say so; CHANGELOG 1.1.3 (2026-07-16) says it "fixed headless (`-p`) runs hanging … now soft-denies such tools"; a community comment on #548 confirms `denied_actions` on 1.2.11. #548 is still open, but for allow-rule matching, not the hang. Narrow `settings.json` `permissions.allow` rules often don't match in headless mode (#1054, #548), though wildcard (`command(*)`) and `regex:` grants reportedly work. The issue body also reports silent tool-call failures under `--dangerously-skip-permissions`. | verified-live (flag); documented (soft-deny, changelog + community) | contract P3; CHANGELOG 1.1.3; issues #548, #1054 |
| P4 | There's no `--cwd`. `init.cwd` is the process cwd (seen live), and the docs say it "operates in your current directory by default". A community report on #548 (1.1.27) says instead that `agy` "does not adopt the current directory as its workspace", reads no `AGENTS.md`/`.agents/`, and needs `--add-dir <repo>`. Per CHANGELOG 1.1.3, writes outside the workspace are refused even in always-proceed mode. All state is under `$HOME/.gemini/`, and a throwaway `HOME` isolated it fully (~232 KB per run). | verified-live (`init.cwd`, HOME isolation); **conflicting** (workspace activation) | contract P4; issue #548 comment; CHANGELOG 1.1.3 |
| P5 | Every `-p` run starts a new `conversation_id`. A knowledge/brain/summaries store persists in `HOME`. | verified-live (new id); inferred (knowledge leak) | contract P5 |
| P6 | `--model <agy slug>` and `--effort`. In API-key mode the models are Gemini only. An unknown model fails with no fallback. | documented | contract P6; CHANGELOG 1.1.12, 1.1.25 |
| P7 | When the directory is an active workspace (see P4), `AGENTS.md`/`GEMINI.md`/`.agents/rules` load, within a 20k-token rules budget, and there's no flag to turn them off. 8 built-in skills (~56 KB) install on first run. `--disable-slash-commands` only stops slash-command expansion. The default tool list has ~20 `browser_*` tools, `generate_image`, `search_web`, `schedule` and subagents. | verified-live (skills, tools); documented (rules) | contract P7 |
| P8 | NDJSON records: `init{cwd,tools,permission_mode}`, `step_update{step_type,state,text_delta?,tool_info?,usage?}`, `result{status,response,usage,denied_actions?,…}`. No cost field. Text mode prints only at the end. | verified-live (shape, on an error run); documented (tool_info) | contract P8 |
| P9 | Exit codes: 0 success, 1 auth, 2 usage, 3 model/agent error. `result.status` ∈ {SUCCESS, ERROR, CANCELED, …}. `AGY_ERROR:{…,retryable,…}` on stderr. Soft-denied tools still exit 0. | verified-live (1/2/3, AGY_ERROR) | contract P9 |
| P10 | The API-key path never starts an account session. Seen live: a dummy key failed at `generativelanguage.googleapis.com`. With **no credentials**, it printed an OAuth URL and then waited silently ~60 s before exiting 1. | verified-live | contract P10 |
| C1 | Critic: direct Gemini HTTP. Either the OpenAI-compatible `…/v1beta/openai/chat/completions` (Bearer key) or native `:generateContent` (`x-goog-api-key`). Both endpoints exist (live probe). A bad key returns **400**, not 401, and the OpenAI-compatible error body is a JSON **array**. | verified-live (endpoints) | contract C1 |
| C2 | `gemini-3.8-flash`: 1,048,576 input / 65,536 output tokens. `gemini-3.1-pro-preview` has the same figures. | documented | contract C2; ai.google.dev model pages |
| O1 | No standalone plan; access comes with Google AI Free/Pro/Ultra. Google staff: running the official binary as a subprocess with its cached account credentials is supported. Accounts seen as "circumventing usage limits" have been banned. | documented | ecosystem O1 |
| O2 | Installed with a curl script into `~/.local/bin`, or by downloading a verified tarball from the release manifest. There were 54 releases from 1.0.4 (2026-06-01) to 1.2.11 (2026-09-25), nearly daily since August. Headless timeouts and error formats changed along the way (1.2.6, 1.2.10). Since 1.2.9, a headless run waits for daemon children and background tasks, up to a 30-minute cap. The binary self-updates every run. | verified-live (`gh api …/releases`, updater ran); documented | ecosystem O2; contract Risks; CHANGELOG 1.2.9 |
| O4 | A fixed overhead of ~23-25k tokens per call is reported. On a Pro plan, 5 hours of quota runs out in about 2 hours. | documented (community + staff acknowledgment) | ecosystem O4 |

**The auth conflict is resolved in favour of the live test.** The ecosystem research
says there is "no BYO API key" (citing issue #78, June 2026). The contract research found
that API-key mode shipped in 1.1.13 and confirmed it live, so #78 is out of date.

## Mapping to the backend contract

| Touchpoint | What Antigravity needs |
|---|---|
| `run_agent()` (`proposer.sh:394-499`) | An `antigravity|antigravity:*` arm. A throwaway `HOME` overlay (SH-2) seeded with `.gemini/antigravity-cli/settings.json` = `{"modelProvider":"gemini"}` (it holds no secret). `cd "$WORKDIR"`. The prompt as one NDJSON line on stdin. `--input-format stream-json --output-format stream-json --dangerously-skip-permissions --disable-slash-commands [--model]`, plus `--add-dir "$WORKDIR"` if AG-0 shows it is needed. |
| `run_iteration()` (L1408-1510) | stream-json is always on. `--provider-format agy-stream-json`. |
| Stream adapter | A new `lib/agy_stream.py` through SH-3. `init` → `agent_init`. `step_update.tool_info` → `agent_tool`. `text_delta` → text. `result` → `agent_result`. `AGY_ERROR` → error classification using `retryable`; the adapter sees it only because `run_iteration` merges stderr into stdout (`proposer.sh:1535,1538`). |
| `critic_call()` (`lib/critic.py:2337`) | A new `gemini[:model]` provider → `call_openai_chat` against the Gemini OpenAI-compatible base with `GEMINI_API_KEY`. `antigravity` as a critic name is an alias for it. |
| `_critic_context_tokens()` (L177) | A Gemini entry through SH-7. |
| Preflight | SH-4: require `GEMINI_API_KEY`, so the 60 s OAuth wait can't happen. |

## Roadmap items

### AG-0
**Spike: a credentialed `agy` run and workspace activation**
- **Goal:** turn the Antigravity facts that the proposer arm relies on into
  verified-live facts, and settle whether headless mode can hang on a permission.
- **Why:**
  - P4: it's unclear whether the cwd becomes the workspace.
  - P3: the reported silent tool-call failures under `--dangerously-skip-permissions`.
  - P8: there's no live tool-call sample yet.
  - O2: auto-update can't be switched off.
  - O4: the token overhead is unmeasured.
  - P5: cross-pass knowledge may persist.
- **Acceptance criteria:**
  - Using one `GEMINI_API_KEY` in a throwaway `HOME`, record live evidence for:
    1. "reply OK";
    2. `hello.txt` written with `--dangerously-skip-permissions`;
    3. a stream-json sample with `tool_info`, saved as the AG-3 fixture;
    4. whether the cwd becomes the workspace: run the same write from `$WORKDIR` with
       and without `--add-dir "$WORKDIR"`, check that the file lands, and check whether
       a planted `AGENTS.md` is quoted by the model;
    5. the same write **without** the skip flag, run under `timeout 300`: a regression
       check that it is soft-denied (rc 0 + `denied_actions`) rather than hanging;
    6. under the skip flag, a pass with several tool calls, checked for tool calls that
       fail silently (the #548 body's report);
    7. whether a wildcard `permissions.allow` grant (`command(*)`) works headless, so a
       scoped mode could replace the skip flag;
    8. the input tokens of a one-word prompt, to measure the fixed overhead;
    9. whether a read-only install directory, or blocking the updater host, stops
       self-update without failing the run;
    10. where the version can be read from (there's no `--version`), for SH-5;
    11. the longest silent gap in stream-json during a long reasoning step, and after
        the answer while background tasks finish (the 1.2.9 30-minute cap).
  - Results go into `roadmap/research/harnesses/antigravity-contract.md` as an
    addendum.
- **Depends on:** — · **Size:** M · **Confidence:** conflicting (P4) and documented
  (hence the spike).

### AG-1
**A `gemini` critic backend**
- **Goal:** `--critic gemini[:<model>]` judges passes through the Gemini API, with no
  CLI involved.
- **Why:** C1 and C2. This is the smallest item and doesn't depend on `agy` at all; it's
  a direct HTTP call like `codex`.
- **Touchpoints:** `lib/critic.py` (`critic_call` L2337-2379, `call_openai_chat`
  L1899, SH-7 table), SH-1, `.env.example` (`GEMINI_API_KEY`,
  `SPECSTRIDE_GEMINI_CRITIC_MODEL`, `SPECSTRIDE_GEMINI_BASE_URL`).
- **Acceptance criteria:**
  - Given `--critic gemini`, then `call_openai_chat` is called with base
    `https://generativelanguage.googleapis.com/v1beta/openai`, the key from
    `GEMINI_API_KEY` and the model `SPECSTRIDE_GEMINI_CRITIC_MODEL` (default
    `gemini-3.8-flash`).
  - Given an invalid key, when the API returns HTTP 400 with the JSON-array body
    `[{"error":{"code":400,"message":"Please pass a valid API key","status":"INVALID_ARGUMENT"}}]`,
    then the critic reads the `HTTPError` body (array or object) and reports
    `auth: GEMINI_API_KEY invalid`, instead of today's generic
    `critic call failed: HTTP Error 400: Bad Request` (`_http_json`,
    `lib/critic.py:1865-1869`; handled at L2714).
  - Given `gemini-3.8-flash`, then the context budget uses 1,048,576 tokens.
  - A live check with a real key returns a parsed verdict.
- **Tests:** `lib/test_critic.py`. `lib/_test_http.py`'s `CaptureServer` currently
  replies with a status and no body, so either extend it with a configurable response
  body or stub `urlopen` in the test.
- **Depends on:** SH-1, SH-7 · **Size:** S · **Confidence:** verified-live (endpoints);
  documented (success shape).

### AG-2
**A minimal Antigravity proposer arm**
- **Goal:** `specstride run --proposer antigravity[:slug]` runs a real Ralph pass that
  edits files in the workdir.
- **Why:** P1, P2, P4 and P10 are verified live; AG-0 settles P3.
- **Touchpoints:** `proposer.sh:394-499`, SH-2 (a `HOME` overlay with the seeded
  `settings.json`, plus git identity passthrough), SH-4 (the `GEMINI_API_KEY`
  preflight), SH-1.
- **Acceptance criteria:**
  - Given a fake `agy`, when a pass runs, then:
    - its cwd is the workdir;
    - stdin is exactly one NDJSON `user` line containing the full prompt;
    - argv has `--input-format stream-json --output-format stream-json
      --dangerously-skip-permissions --disable-slash-commands`, plus `--model` only when
      a slug is given;
    - `$HOME/.gemini/antigravity-cli/settings.json` in the overlay is
      `{"modelProvider":"gemini"}`, and the overlay is removed afterwards.
  - Given no `GEMINI_API_KEY`, then the run stops before pass 1 in under 5 s, naming the
    variable (the SH-4 preflight entry for `antigravity`).
  - Given a real `agy` and a key, when a phase asks for `hello.txt`, then the file exists
    and the pass exits 0.
- **Tests:** a new `lib/test_antigravity_backend.py`.
- **Depends on:** AG-0, SH-1, SH-2, SH-4 · **Size:** M · **Confidence:** conflicting
  today (P4, workspace activation); AG-0 settles it. The path up to the model call is
  verified-live.

### AG-3
**An Antigravity stream adapter**
- **Goal:** Antigravity passes produce the standard `events.jsonl` events, and failures
  are classified from `AGY_ERROR`.
- **Why:** P8 and P9.
- **Touchpoints:** a new `lib/agy_stream.py` through SH-3.
- **Acceptance criteria:**
  - Given the AG-0 fixture, then the adapter emits:
    - `agent_init`, with the tools count from `init.tools`;
    - `agent_tool` for each `step_update` with `tool_info`, with names mapped
      (`write_to_file`→Write, `replace_file_content`/`multi_replace_file_content`→Edit,
      `run_command`→Bash);
    - `evidence_writing` on the expected file;
    - `agent_result`, with `result.usage` tokens and `cost_usd: null`.
  - Given `result.status: ERROR` and an `AGY_ERROR` line with `retryable:true`, then the
    result carries `retryable` so the orchestrator can tell a transient provider error
    from a real failure.
  - Given `result.denied_actions` is not empty, then a `tools_denied` diagnostic is
    emitted, even though rc is 0.
- **Tests:** `lib/test_agent_stream*.py` and `lib/test_telemetry_parity.py`.
- **Depends on:** AG-0, AG-2, SH-3, SH-6 · **Size:** M · **Confidence:** verified-live
  (record shapes); the tool names are documented until AG-0.

### AG-4
**Controlling `agy` self-updates**
- **Goal:** the `agy` binary can't change between passes of one run without Specstride
  noticing, and preferably can't change at all.
- **Why:** O2: it self-updates on every run, with no opt-out found, and releases come
  almost daily.
- **Touchpoints:** SH-5, `wiki/Configuration.md` (install from the verified tarball in
  the release manifest), and whatever AG-0 finds for blocking updates.
- **Acceptance criteria:**
  - Given any pass, then `producer.json` records `harness_version` (through SH-5).
  - If AG-0 finds a way to block updates, then the arm applies it by default, and a
    3-pass run spanning a new upstream release records the same version on every pass.
  - If AG-0 finds none, the docs say so, and the SH-5 drift warning is on by default for
    `antigravity`.
- **Depends on:** AG-0, SH-5 · **Size:** S · **Confidence:** unknown (hence it depends
  on AG-0).

### AG-5
**A context and tool budget for Antigravity passes**
- **Goal:** operators can see, and cap, the fixed context an Antigravity pass spends
  before the phase prompt.
- **Why:**
  - P7: the rules budget is up to 20k tokens, and ~56 KB of built-in skills install.
  - The default tool list is large.
  - O4: an overhead of ~23-25k tokens per call is reported.
  - The phase prompt's budget doesn't account for any of this today.
- **Touchpoints:** the AG-2 arm; the `init.tools` count and the first-step usage from
  the AG-3 adapter.
- **Acceptance criteria:**
  - Given an Antigravity pass, then `producer.json` records `fixed_overhead_tokens`,
    from the AG-0 measurement or the first step's `usage`.
  - Given a vendor flag that restricts tools or turns off rules (watch-list 2 below),
    then the arm passes it by default. Until one exists, the docs tell operators to keep
    `.agents/` and `GEMINI.md` out of target workdirs they don't want loaded.
- **Depends on:** AG-0, AG-3 · **Size:** S · **Confidence:** documented.

## Watch list: what Google would need to ship

These items clear remaining caveats rather than blockers. For each, the check that shows
it has shipped:

1. **Auto-update opt-out or version pin.** Look for a `--no-update`/`autoUpdate` setting
   in `agy --help` or the install docs, and "auto-update" in the CHANGELOG. When it
   appears, AG-4 switches from warning on drift to preventing it.
2. **A way to turn off rules and restrict tools** (e.g. `--no-rules`,
   `--allowed-tools`). Check `agy --help` and antigravity.google/docs/rules. When it
   appears, AG-5 applies it by default.
3. **Auth fails fast with no tty.** Re-run the no-credential smoke test; it should exit
   non-zero in under 5 s. SH-4 stays as a second check either way.
4. **Scoped allow-rules that reliably match in headless mode.** AG-0 item 7 checks
   wildcard grants now. Watch #548 and #1054 for fixes to narrow-rule matching. Once
   scoped grants work, a follow-up to AG-2 can replace `--dangerously-skip-permissions`.
5. **The silent tool-call failures under `--dangerously-skip-permissions`** fixed, per a
   release note. AG-0 item 6 measures them now.
6. **A cost field in `result`.** Grep the CHANGELOG for "cost". AG-3 would then fill in
   `cost_usd`.

## Risks and unknowns

- **Workspace activation** (high until AG-0). If the cwd isn't adopted as the
  workspace, writes into `$WORKDIR` may be refused even in always-proceed mode, and the
  target repo's rules won't load. AG-0 item 4 decides whether the arm needs
  `--add-dir`.
- **Silent tool-call failures under `--dangerously-skip-permissions`** (medium,
  reported in the #548 body). AG-0 item 6 checks for them. The old permission hang was
  fixed in 1.1.3, and AG-0 item 5 keeps it as a regression check.
- **Post-answer waits.** Since 1.2.9, a headless run can wait up to 30 minutes for
  background tasks after the answer. SH-6 should classify a kill in that window as a
  harness hang after the final record.
- **Self-updating closed-source binary** (high): behaviour can change between passes.
  AG-4 and SH-5 cover this.
- **Account bans** for automation Google reads as "circumventing usage limits". Use the
  metered `GEMINI_API_KEY` path, not an account login. The docs must say so.
- **Fixed per-pass overhead** (medium) makes many small passes expensive. AG-5 makes it
  visible.
- **Knowledge persisting across passes** if a `HOME` is ever reused. SH-2 per-pass
  overlays avoid it.
- **The API-key path trails account mode** (the 1.2.9 note on reducing discrepancies
  with the non-API-key sign-in path).

## Open questions for the operator

1. Is a `GEMINI_API_KEY` (metered Gemini API billing) acceptable for AG-0 and for
   production runs, rather than a Google AI Pro/Ultra account login?
2. Should AG-1 (the `gemini` critic) ship straight after the shared prerequisites, ahead
   of any proposer work, as the recommended order in [README.md](README.md) proposes?
