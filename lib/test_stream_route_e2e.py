"""End-to-end Story 1: a new stream format is one adapter plus a registry entry.

Everything here runs through a TEMPORARY COPY of the tracked tree
(`routed_tree`), to which the quickstart's three edits are applied
programmatically: the test adapter module, one seam-table row, and
``stream="specstride-test-v1"`` on the ``codex`` registry entry. A fake ``codex``
on PATH replays hand-written fixtures, so no real harness is needed.
"""

import difflib
import json
import os
import shutil
import subprocess
import sys
import pytest

_LIB = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(_LIB)

FIXTURE_STREAMS = os.path.join(_LIB, "fixtures", "stream-test-v1")

CONTRACT_METADATA_KEYS = {
    "attempt", "backend", "contract", "expected_evidence", "feature",
    "invocation_id", "iteration", "observability_mode", "phase",
    "provider_format", "role", "run_id",
}


def _git_ls_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def _apply_quickstart_edits(tree, stream="specstride-test-v1"):
    """The quickstart's three edits (SC-001), applied to the tree copy."""
    # 1. Adapter module.
    shutil.copy(os.path.join(_LIB, "fixtures", "stream_test_adapter.py"),
                os.path.join(tree, "lib", "stream_test_adapter.py"))
    # 2. One import + one row, inside the marked seam block of agent_stream.py.
    tap = os.path.join(tree, "lib", "agent_stream.py")
    text = open(tap, encoding="utf-8").read()
    end_marker = "FORMATS = build_registry(STREAM_FORMATS)"
    assert end_marker in text
    row = (
        "from stream_test_adapter import StreamTestAdapter\n"
        + "\n"
        + "STREAM_FORMATS += (\n"
        + '    StreamFormat("specstride-test-v1", StreamTestAdapter,\n'
        + '                 capability=Capability("specstride test stream selected"),\n'
        + '                 has_init_record=False, synth_init=True,\n'
        + '                 synth_terminal=True, no_activity=True),\n'
        + ")\n"
    )
    text = text.replace(end_marker, row + end_marker, 1)
    open(tap, "w", encoding="utf-8").write(text)
    # 3. Registry value on the codex entry.
    backends = os.path.join(tree, "lib", "backends.py")
    text = open(backends, encoding="utf-8").read()
    codex = ('    Backend("codex", (("proposer", Qualifier("none")),'
             ' ("critic", Qualifier("none")))),')
    assert codex in text
    if stream is not None:
        assert codex.endswith("))),")
        text = text.replace(
            codex,
            codex[:-4] + ')), stream="%s"),' % stream,
            1)
    open(backends, "w", encoding="utf-8").write(text)


def routed_tree(tmp_path, stream="specstride-test-v1"):
    """Copy every git-tracked path into tmp_path/tree and apply the 3 edits."""
    tree = tmp_path / "tree"
    tree.mkdir()
    for rel in _git_ls_files():
        dest = tree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = os.path.join(REPO, rel)
        if os.path.islink(src):
            os.symlink(os.readlink(src), dest)
        else:
            shutil.copy(src, dest)
    _apply_quickstart_edits(str(tree), stream=stream)
    return tree


def _write_fake_codex(tmp_path, tree):
    """A fake `codex` that records argv and replays a fixture stream."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fixture_dir = tree / "lib" / "fixtures" / "stream-test-v1"
    script = bin_dir / "codex"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "# Fake codex: record argv, replay a fixture stream, write the gate.\n"
        'printf \'%s\\n\' "$*" >> "$FAKE_ARGV_LOG"\n'
        'count=$(( $(cat "$FAKE_COUNT" 2>/dev/null || echo 0) + 1 ))\n'
        'echo "$count" > "$FAKE_COUNT"\n'
        'stream="$(mktemp)"\n'
        'sed "s|{EVIDENCE}|$FAKE_EVIDENCE|g" '
        '"$FAKE_FIXTURE_DIR/$FAKE_CASE.jsonl" > "$stream"\n'
        # The gate file appears only when the test wants the proposer to see it.'
        'if [[ "$count" -ge 2 || "${FAKE_WRITE_EVIDENCE:-1}" == "1" ]]; then cp "$stream" "$FAKE_EVIDENCE"; fi\n'
        'cat "$stream"\n'
        'exit "${FAKE_EXIT:-0}"\n'
    )
    script.chmod(0o755)
    return bin_dir, str(fixture_dir)


def _run_proposer(tree, bin_dir, tmp_path, *, case="camel", fake_exit=0,
                  agent_stream="true", extra_args=(), prompt="add the widget module",
                  n_iters=1, write_evidence_first=True):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    evidence = (tmp_path / "gate.md").resolve()
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text(prompt)
    events = tmp_path / "events.jsonl"
    argv_log = tmp_path / "codex-argv.log"
    count_file = tmp_path / "codex-count"
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPECSTRIDE_")}
    env["PATH"] = "%s:%s" % (bin_dir, env["PATH"])
    env.update({
        "SPECSTRIDE_EVENTS": str(events),
        "SPECSTRIDE_RUN_ID": "run-e2e",
        "SPECSTRIDE_AGENT_STREAM": agent_stream,
        "FAKE_ARGV_LOG": str(argv_log),
        "FAKE_COUNT": str(count_file),
        "FAKE_FIXTURE_DIR": str(tree / "lib" / "fixtures" / "stream-test-v1"),
        "FAKE_CASE": case,
        "FAKE_EVIDENCE": str(evidence),
        "FAKE_EXIT": str(fake_exit),
        "FAKE_WRITE_EVIDENCE": "1" if write_evidence_first else "0",
    })
    cmd = ["timeout", "120", "bash", str(tree / "proposer.sh"),
           "--backend", "codex", "-n", str(n_iters), "-e", str(evidence),
           "-f", str(prompt_file), "-w", str(work), *extra_args]
    proc = subprocess.run(cmd, cwd=str(work), env=env, capture_output=True,
                          text=True, timeout=150)
    return {
        "proc": proc, "work": work, "events": events, "argv_log": argv_log,
        "evidence": evidence,
    }


def _read_events(run):
    path = run["events"]
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _invocation_dir(run):
    base = run["work"] / ".specstride" / "features" / "default" / "debug" / (
        "invocations") / "run-e2e" / "proposer"
    dirs = sorted(p for p in base.rglob("*") if p.is_dir() and p.name.startswith("inv-"))
    return dirs[0] if dirs else None


# ── T036: the happy route (FR-017, Story 1 sc.1–3) ───────────────────────────

@pytest.mark.parametrize("fake_exit", [0, 3])
@pytest.mark.parametrize("case", ["camel", "nested"])
def test_e2e_routed_pass_produces_structured_events(tmp_path, case, fake_exit):
    tree = routed_tree(tmp_path)
    bin_dir, _ = _write_fake_codex(tmp_path, tree)
    run = _run_proposer(tree, bin_dir, tmp_path, case=case, fake_exit=fake_exit,
                        n_iters=2, write_evidence_first=False)
    events = _read_events(run)

    # The recorded argv carries the prompt (both passes).
    assert run["argv_log"].exists()
    recorded = run["argv_log"].read_text()
    assert "add the widget module" in recorded

    # Pass 1 is the reconciled pass (no gate file yet): slice its events out.
    pass1 = [e for e in events if e.get("iteration") == 1
             and e["event"].startswith(("agent_", "evidence_"))]
    starts = [e for e in pass1 if e["event"] == "agent_observability"]
    assert len(starts) == 1
    assert starts[0]["mode"] == "structured"
    assert starts[0]["provider_format"] == "specstride-test-v1"
    assert starts[0]["reason"] == "specstride test stream selected"
    assert starts[0]["supported_signals"] == "init,text,tool,evidence,result"

    # A synthesised agent_init before any other agent_* capture event.
    assert pass1[1]["event"] == "agent_init"

    tools = [e for e in pass1 if e["event"] == "agent_tool"]
    assert len(tools) == 1 and tools[0]["tool"] == "Write"
    evidence_writes = [e for e in pass1 if e["event"] == "evidence_writing"]
    assert len(evidence_writes) == 1 and evidence_writes[0]["tool"] == "Write"
    results = [e for e in events if e["event"] == "agent_result"]
    assert len(results) == 1

    # metadata.json: exactly the contract keys, sorted, 2-space, trailing \n.
    inv = _invocation_dir(run)
    assert inv is not None
    metadata_path = inv / "metadata.json"
    text = metadata_path.read_text()
    metadata = json.loads(text)
    assert set(metadata) == CONTRACT_METADATA_KEYS
    assert metadata["provider_format"] == "specstride-test-v1"
    assert metadata["observability_mode"] == "structured"
    assert metadata["backend"] == "codex"
    assert metadata["contract"] == "specstride-invocation/v1"
    assert metadata["run_id"] == "run-e2e"
    assert text == json.dumps(metadata, sort_keys=True, indent=2) + "\n"

    producer = json.loads((inv / "producer.json").read_text())
    assert producer["producer_exit_code"] == fake_exit
    assert producer["parser_exit_code"] == 0

    result = json.loads((inv / "result.json").read_text())
    assert result.get("no_activity") is False

    if fake_exit == 0:
        assert run["proc"].returncode == 0, run["proc"].stderr


# ── T037: structural assertions (SC-001, FR-007) ─────────────────────────────

def test_three_edits_only(tmp_path):
    tree = routed_tree(tmp_path)
    expected = set(_git_ls_files()) | {"lib/stream_test_adapter.py"}
    actual = set()
    for root, _dirs, files in os.walk(tree):
        for name in files:
            path = os.path.join(root, name)
            actual.add(os.path.relpath(path, tree))
    assert actual == expected  # one new file, nothing deleted

    modified = set()
    for rel in sorted(expected - {"lib/stream_test_adapter.py"}):
        old = open(os.path.join(REPO, rel), "rb").read() if os.path.exists(
            os.path.join(REPO, rel)) else None
        new = (tree / rel).read_bytes() if (tree / rel).exists() else None
        if old != new:
            modified.add(rel)
    assert modified == {"lib/agent_stream.py", "lib/backends.py"}

    # The agent_stream.py diff lies entirely inside the marked table block.
    start_marker = "# ── stream formats (the seam) ──"
    end_marker = "FORMATS = build_registry(STREAM_FORMATS)"
    orig = open(os.path.join(REPO, "lib", "agent_stream.py")).read()
    new = (tree / "lib" / "agent_stream.py").read_text()
    o_lines = orig.splitlines()
    n_lines = new.splitlines()
    start = next(i for i, l in enumerate(o_lines) if start_marker in l)
    end = next(i for i, l in enumerate(n_lines) if end_marker in l)
    sm = difflib.SequenceMatcher(a=o_lines, b=n_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        assert i1 >= start and j2 <= end + 1, (tag, i1, i2, j1, j2)


def test_run_iteration_text_unchanged(tmp_path):
    tree = routed_tree(tmp_path)

    def extract(path):
        text = open(path, encoding="utf-8").read()
        begin = text.index("run_iteration() {")
        end = text.index("\n}\n", begin)
        return text[begin:end]

    assert extract(os.path.join(REPO, "proposer.sh")) == extract(
        str(tree / "proposer.sh"))


# ── T038: degradation (FR-016, Story 1 sc.4–6) ───────────────────────────────

def _observability(events):
    return [e for e in events if e["event"] == "agent_observability"]


def test_degrade_unregistered_stream_format(tmp_path):
    tree = routed_tree(tmp_path, stream="nope")
    bin_dir, _ = _write_fake_codex(tmp_path, tree)
    run = _run_proposer(tree, bin_dir, tmp_path)
    events = _read_events(run)
    obs = _observability(events)
    assert len(obs) == 1 and obs[0]["mode"] == "raw-text"
    assert "nope" in obs[0]["reason"]
    assert not [e for e in events if e["event"] in ("agent_init", "agent_tool")]
    assert _invocation_dir(run) is None
    # The pass ran and the proposer returned normally (no breaker charge).
    assert run["evidence"].exists()
    assert run["proc"].returncode == 0, run["proc"].stderr


@pytest.mark.parametrize("extra_args", [(), ("-j",)],
                         ids=["no_j", "with_j"])
def test_degrade_switch_off(tmp_path, extra_args):
    tree = routed_tree(tmp_path)
    bin_dir, _ = _write_fake_codex(tmp_path, tree)
    run = _run_proposer(tree, bin_dir, tmp_path, agent_stream="false",
                        extra_args=extra_args)
    events = _read_events(run)
    obs = _observability(events)
    assert len(obs) == 1 and obs[0]["mode"] == "raw-text"
    assert obs[0]["reason"].startswith(
        "structured capture disabled by SPECSTRIDE_AGENT_STREAM=false")
    # The adapter was never invoked: no invocation dir, no agent_* stream events.
    assert _invocation_dir(run) is None
    assert not [e for e in events if e["event"].startswith("agent_init")
                or e["event"].startswith("agent_tool")]


def test_degrade_tap_missing(tmp_path):
    tree = routed_tree(tmp_path)
    (tree / "lib" / "agent_stream.py").unlink()
    bin_dir, _ = _write_fake_codex(tmp_path, tree)
    run = _run_proposer(tree, bin_dir, tmp_path)
    events = _read_events(run)
    obs = _observability(events)
    assert len(obs) == 1 and obs[0]["mode"] == "raw-text"
    assert obs[0]["reason"].startswith("stream tap unavailable (agent_stream.py or python3 missing)")
    assert _invocation_dir(run) is None
    assert run["proc"].returncode == 0, run["proc"].stderr


def test_degrade_backend_without_stream(tmp_path):
    tree = routed_tree(tmp_path, stream=None)
    bin_dir, _ = _write_fake_codex(tmp_path, tree)
    run = _run_proposer(tree, bin_dir, tmp_path)
    events = _read_events(run)
    # Today's unstructured behaviour: no announcement from the new block.
    assert not _observability(events)
    assert _invocation_dir(run) is None
    assert run["proc"].returncode == 0, run["proc"].stderr


# ── T031: bash shims ─────────────────────────────────────────────────────────

def _bash(script):
    return subprocess.run(["bash", "-c", script], cwd=REPO,
                          capture_output=True, text=True)


def test_shim_backend_stream_empty_and_rc_zero():
    proc = _bash("source specstride-lib.sh; specstride_backend_stream codex; echo rc=$?")
    assert proc.returncode == 0
    assert proc.stdout == "rc=0\n"


def test_shim_stream_formats_lists_canonical():
    proc = _bash("source specstride-lib.sh; specstride_stream_formats")
    assert proc.returncode == 0
    assert proc.stdout == "claude\nprime-v3\n"


def test_shim_invocation_model_table():
    proc = _bash(
        "source specstride-lib.sh; out=$(specstride_invocation_model prime:v ''); "
        "echo \"[$out]rc=$?\"")
    assert proc.stdout == "[]rc=0\n"
    proc = _bash(
        "source specstride-lib.sh; out=$(specstride_invocation_model dsh:prov/m ''); "
        "echo \"[$out]rc=$?\"")
    assert proc.stdout == "[prov/m]rc=0\n"


def test_shim_work_without_python3(tmp_path):
    bin_dir = tmp_path / "nopython-bin"
    bin_dir.mkdir()
    for tool in ("bash", "dirname", "date", "sed"):
        source = shutil.which(tool)
        assert source, tool
        (bin_dir / tool).symlink_to(source)
    env = dict(os.environ)
    env["PATH"] = str(bin_dir)
    script = ("source specstride-lib.sh; "
              "specstride_backend_stream codex; echo \"s:$?\"; "
              "specstride_stream_formats; echo \"f:$?\"; "
              "specstride_invocation_model dsh:prov/m ''; echo \"i:$?\"")
    proc = subprocess.run(["bash", "-c", script], cwd=REPO, env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout == "s:0\nf:0\ni:0\n"
