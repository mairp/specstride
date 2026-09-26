# Prompt — research OpenCode, Cursor and Google Antigravity as Specstride backends

You are an autonomous research agent running unattended. Nobody will answer questions.
Make routine decisions yourself, record them in the final report, and stop only when a
step below is impossible (say why and what remains). Written 2026-09-26 against
Specstride `main` e953aa6. Where this prompt and any other document disagree, this prompt
wins.

**You are writing roadmaps, not code.** The output is a set of per-harness roadmap
documents whose items are sized so that the operator can turn each one into a Spec Kit
feature (`/speckit.specify`) later. Do not edit `proposer.sh`, `orchestrator.sh`,
`lib/*.py` or any test. The only files you create or change are listed under *What you
are producing*.

## Background: what a "harness" is here

Specstride runs two independent, pluggable roles:

- **Proposer** — a headless coding-agent CLI that does one pass of phase work in the
  target workdir, then exits. Selected by `--proposer` / `SPECSTRIDE_PROPOSER`.
- **Critic** — a one-shot LLM call that judges the pass's evidence and returns a verdict.
  It must not use tools. Selected by `--critic` / `SPECSTRIDE_CRITIC`.

The backends today are `dsh[:provider/model] | claude | codex | bebop[:name] |
prime[:variant]`. You are researching four candidates:

| Candidate | Why | Roles to research |
|---|---|---|
| **OpenCode** | open-source, provider-agnostic terminal agent | proposer + critic |
| **Cursor** | Cursor's headless agent CLI | proposer + critic |
| **Google Antigravity** | Google's agent-first IDE | proposer + critic |
| **Gemini CLI** | *conditional*, see step 2 | proposer + critic |

Gemini CLI comes in only if Antigravity cannot run unattended. The operator's rule is:
if Antigravity has no supported way to run one headless, non-interactive, scriptable
pass, the Antigravity roadmap records the blocker and what would remove it, and a fourth
roadmap covers Gemini CLI as the headless Google path.

## Read these first: the backend contract, as the code defines it

Each new backend has to satisfy every one of these touchpoints. Read them before you
brief any research agent. The research questions below come from them.

1. `proposer.sh:394-499`, `run_agent()`: one `case` arm per backend. Compare the arms:
   - `claude`: the prompt goes in on **stdin**, because a phase prompt can exceed the
     128 KiB single-argument limit.
   - `codex`: the prompt is a positional argument (a known ARG_MAX hazard), and the
     arm is marked UNVERIFIED.
   - `prime`: stdin, plus `--no-session` so every Ralph pass starts fresh, plus
     `--cwd "$WORKDIR"`.
   - `dsh`: a throwaway `DSH_HOME` overlay, so a per-run model choice mutates no
     global config.
   Every arm runs under `run_with_idle_watchdog` (L821), which kills a pass that goes
   quiet on stdout for `IDLE_TIMEOUT`.
2. `proposer.sh:1408-1510`, `run_iteration()`: the structured-stream path. `claude`
   gets `--output-format stream-json` and `--disable-slash-commands` (L1430-1441; the
   comment explains the skill-overflow incident). Structured output is piped through
   `lib/agent_stream.py`. `claude`/`bebop` use the `claude-stream-json` format and
   Prime uses `--provider-format prime-v3` with its own adapter, `lib/prime_stream.py`
   (`PrimeAdapter`, L37). A new harness with a JSONL stream needs either an adapter or
   a documented reason it has none.
3. `lib/critic.py`:
   - The dispatch table is `critic_call()` (L2337-2379). `claude`/`codex` call vendor
     APIs directly with stdlib urllib; `dsh`/`prime`/`bebop` shell out to a CLI with
     tools disabled.
   - Context windows are in `_critic_context_tokens()` (L178-213) and the model tables
     at L154/L172. They size every grounding budget, so a new critic backend needs a
     real context-window figure.
4. Backend names are listed by hand in `orchestrator.sh:60-64,199`,
   `proposer.sh:497`, `lib/critic.py:17,2379,2469`, `.env.example:11-27`, `README.md`,
   `wiki/Configuration.md` and `wiki/CLI-Reference.md`. The `reversed/` spec set also
   mentions them.
5. Tests that pin backend behaviour: `lib/test_prime_backend.py`,
   `lib/test_telemetry_parity.py` (backend parity for the event stream),
   `lib/test_agent_stream*.py`, `lib/test_critic.py`.
6. Operator memory worth knowing:
   - The proposer's pass 1 once overflowed its context because a vendor skill
     auto-loaded, so rules, skills and `AGENTS.md` auto-loading is a first-class risk
     for every harness.
   - The Codex backend shipped UNVERIFIED and stayed that way. This research exists to
     avoid repeating that.

## The research questions (the backend-contract questionnaire)

Every research agent answers all of these for its harness. For each answer it gives a
source (a docs URL with the date accessed, a repo file and commit, or `live:` with the
exact command and trimmed output) and a confidence level: `verified-live`, `documented`,
`inferred` or `unknown`.

**Proposer**
- P1 **Headless entrypoint.** The exact command for one non-interactive pass. Is it
  officially supported or undocumented? What is its stability status?
- P2 **Prompt input.** Stdin, file or argument only? What happens with a prompt over
  128 KiB?
- P3 **Unattended permissions.** How is every tool approval auto-granted (file writes,
  shell)? Is there a sandbox, and can it be scoped to the workdir? What does the
  harness do when it would normally prompt: hang, fail or auto-deny? Hanging matters
  most, because the idle watchdog would kill it with no diagnosis.
- P4 **Workdir and isolation.** How is the cwd set? Where are config, sessions and
  caches kept? Can a run use a throwaway config home (the `dsh` overlay pattern) so
  nothing global mutates and concurrent runs don't collide?
- P5 **Fresh context per pass.** Does every invocation start a new session, or is
  there a flag for that? Is any memory or resumable state persisted between runs?
- P6 **Model selection.** The flag or env var, which providers are allowed, and how an
  arbitrary provider/model is named. Does `--model` map cleanly to Specstride's
  `--model`?
- P7 **Auto-loaded context.** Which rules, skills, `AGENTS.md`, `.cursor/rules`, MCP
  servers etc. load automatically, and can each one be switched off? This is the
  skill-overflow risk.
- P8 **Structured output.** Is there a JSON or JSONL stream? Record its schema with a
  real sample, and map it onto the `agent_stream.py` event vocabulary (`agent_init`,
  tool calls, `agent_result`, token usage, cost). If there is no stream, what does
  stdout carry, and does it produce output regularly enough to keep the idle watchdog
  happy?
- P9 **Exit and terminal state.** List the exit codes. How does the harness report
  success, error, max-turns and interruption? Can a crash be told apart from "finished,
  with a failure"?
- P10 **Auth for unattended use.** Can it use an API key from an env var, or does it
  need a browser/OAuth login? Where are tokens stored? Is headless CI use supported?

**Critic**
- C1 Is there a tool-less one-shot mode, or a direct HTTP API for the same models
  (which is what `claude`/`codex` do)? Recommend one of the two.
- C2 Context window and output limit of the default critic model, for
  `_critic_context_tokens`.
- C3 How is a clean final text answer extracted, with no tool chatter or preamble?

**Operational**
- O1 License and terms of service for automated, unattended, looped use. Any rate
  limits, seat or plan requirements?
- O2 Install method, version pinning and release cadence. How often has the headless
  interface broken between releases? Check the changelog and issues.
- O3 Cost visibility: can a pass report tokens or cost?
- O4 Anything that makes the harness unsuitable. Say so plainly: a verdict of "do not
  integrate" is a valid result.

## What you are producing

```text
roadmap/research/harnesses/<harness>-contract.md   # raw findings, questionnaire answers + evidence
roadmap/research/harnesses/<harness>-ecosystem.md  # docs/changelog/issues/ToS sweep
roadmap/harnesses/README.md                        # cross-harness comparison + shared prerequisites
roadmap/harnesses/opencode.md
roadmap/harnesses/cursor.md
roadmap/harnesses/antigravity.md
roadmap/harnesses/gemini-cli.md                    # only if step 2 triggers it
roadmap/README.md                                  # one index entry per new roadmap, status Planned
```

`<harness>` is one of `opencode`, `cursor`, `antigravity`, `gemini-cli`.

## Steps

### 1. Wave 1: fan out the research (up to 6 agents, launched concurrently)

Use the Agent tool (`subagent_type: general-purpose`) and put every launch in **one
message** so the agents run in parallel. Give each agent a self-contained brief: the
harness name, the full questionnaire above, the "Read these first" touchpoints, the
output path, and the smoke-test rules below. Research agents inherit none of your
context.

For each of OpenCode, Cursor and Antigravity:

- **Contract agent (`model: "opus"`)** answers P1-P10 and C1-C3 and runs the live
  smoke test. It writes `roadmap/research/harnesses/<harness>-contract.md`.
- **Ecosystem agent (`model: "sonnet"`)** answers O1-O4, gathers the model list and
  context windows, and sweeps the changelog, the GitHub issues about headless or CI
  use, and community reports. It writes
  `roadmap/research/harnesses/<harness>-ecosystem.md`.

**Smoke-test rules (live checks are authorised):**
- Install into a scratch prefix, never globally:
  - npm: `npm install --prefix "$SCRATCH/<harness>"`
  - a curl installer: pass an install-dir or `HOME` override
  - Record the exact version installed.
- Credentials come only from the environment or the operator's existing config. Never
  write a key into any file under the repo. With no credential available, record
  `unknown: no credentials on host`, document the auth path, and move on without
  blocking.
- Run at most **three** trivial invocations per harness, in a throwaway git repo
  under scratch (never this repo):
  1. A one-line "reply OK" prompt.
  2. "create hello.txt containing hi" in the headless/unattended mode (this proves the
     permissions work).
  3. The same prompt with structured output on, keeping a trimmed sample of the stream.
  Capture the exit code and the wall time for each.
- Also try one oversized prompt of about 200 KiB through the harness's preferred input
  path (P2). It may fail. Record how it fails.
- Smoke-test artifacts stay in scratch. They are one-off run artifacts, so they are
  never committed.

### 2. Gate: does Gemini CLI enter?

When the Antigravity contract agent finishes, read its P1/P3/P10 verdict. If Antigravity
**cannot** run one unattended headless pass (no supported CLI, interactive login only,
or IDE-only agent), launch a contract agent (opus) and an ecosystem agent (sonnet) for
**Gemini CLI** with the same brief. If Antigravity *can* run headless, skip them, and in
`roadmap/harnesses/README.md` say that Gemini CLI was not researched and why.

### 3. Write the roadmaps (you, not a subagent)

Read every research file yourself; don't rely on the agents' summaries. For each harness,
write `roadmap/harnesses/<harness>.md` using this template:

```markdown
# <Harness> as a Specstride backend

**Status:** Planned · **Researched:** 2026-MM-DD against <harness> vX.Y.Z · **Verdict:**
proposer <go | go-with-caveats | blocked | no-go>, critic <…>

## Summary          ← 5 lines: what works, what blocks, recommended backend spelling
                      (e.g. `opencode[:provider/model]`) and critic path (CLI vs HTTP)
## Verified facts   ← table: question id · answer · confidence · source
## Mapping to the backend contract
                    ← touchpoint by touchpoint (run_agent arm, stream adapter, critic_call,
                      context tokens, name lists, env knobs, docs), what each needs
## Roadmap items    ← see rules below
## Risks and unknowns
## Open questions for the operator
```

**Roadmap item rules.** These are what turn a roadmap into specs, so keep to them:
- Number the items `<PREFIX>-1`, `<PREFIX>-2`, … with `OC` for OpenCode, `CU` for
  Cursor, `AG` for Antigravity and `GM` for Gemini CLI.
- Size each item as **one Spec Kit feature**: one independently shippable,
  independently testable outcome, one PR. If an item needs "and" to describe it,
  split it.
- Each item carries:
  - **Goal**: one sentence, user-visible.
  - **Why**: which verified fact drives it.
  - **Touchpoints**: `file:line` from the list above.
  - **Acceptance criteria**: Given/When/Then, checkable by a test or a live run.
  - **Tests**: which existing test file to extend, or which new one to add.
  - **Depends on**: other item ids, including shared `SH-n` items.
  - **Size**: S/M/L.
  - **Confidence**: the weakest confidence among the facts it rests on.
- Order the items so the first one is the smallest thing that proves the harness can
  run one real proposer pass end to end. Structured-stream parity, the critic and docs
  come after it.
- An item that rests on an `inferred` or `unknown` fact starts with a **spike** item
  that turns the fact into `verified-live`.
- For a blocked harness (possibly Antigravity), the items are the watch-list: what the
  vendor would have to ship, how to detect that it has, and the first item to pick up
  when it does.

**Shared prerequisites.** Some work serves every harness. The obvious one is the
backend name list hand-copied into eight places (touchpoint 4); a single registry would
fix it. Another is a generic JSONL-stream adapter seam next to `PrimeAdapter`. Put that
work **once** in `roadmap/harnesses/README.md` as `SH-1…SH-n` items (same template), and
have the per-harness items depend on them instead of repeating them. The README also
carries:
- the comparison table (harness × P1-P10/C1 verdicts),
- a recommended **order** for integrating the harnesses and why,
- whether Gemini CLI was researched.

### 4. Adversarial verification (1 agent, `model: "opus"`)

Launch one fresh agent. Give it the finished roadmaps and research files and this job:
for every row in each roadmap's *Verified facts* table and every acceptance criterion,
try to show that it is wrong, unsupported by its cited source, or not testable. It may
re-run a smoke test if one is cheap. It returns a list of findings. Fix every finding
that holds up. Downgrade a confidence level rather than delete a fact. List the rejected
findings, with reasons, in the final report.

### 5. Index and ship

- Add one entry per roadmap (plus `roadmap/harnesses/README.md`) to
  `roadmap/README.md` with status **Planned**, in the style of the existing entries.
- Mark this prompt's own entry **Done** in the same file.
- Branch from `main` (`docs/harness-roadmaps`), commit only the files listed under
  *What you are producing*, and open one PR. `main` is protected, and Mergify merges it
  when CI is green.
- Never put conversation links, session IDs or session trailers in commits, PRs or
  docs.

## Budget and stop conditions

- At most 9 subagents in total: 6 in wave 1, 2 for Gemini CLI if they're needed, and 1
  verifier. Don't re-launch an agent that returned thin results; fill the gap yourself
  with WebSearch/WebFetch.
- If a harness's docs or binary can't be reached at all, write its roadmap with every
  fact `unknown`, a verdict of `blocked (unresearched)` and a single spike item, and
  continue with the others.
- Stop after the PR is open.

## Final report (the reply that ends your run)

- The verdict per harness and role.
- The item count per roadmap and per shared list.
- The recommended integration order.
- Whether Gemini CLI was triggered.
- The versions tested live, and what couldn't be tested and why.
- The verifier findings you rejected, with reasons.
- The PR URL.
