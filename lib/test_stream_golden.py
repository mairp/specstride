"""Byte-identity goldens: the tap's output must not change without review (T008, US4).

Every golden input (Claude and Prime) is replayed through the tap in both modes
and compared byte for byte against the committed goldens, so any refactor that
changes what the tap writes fails here first.
"""

import os
import sys

import pytest

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fixtures", "stream-golden")
if GOLDEN_DIR not in sys.path:
    sys.path.insert(0, GOLDEN_DIR)

import capture  # noqa: E402  (fixture-local module)

REPO_ROOT = os.path.dirname(GOLDEN_DIR.rstrip("/").rsplit(os.sep, 2)[0])
PRIME_INPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "fixtures", "prime-v3")

CASES = []
for input_path, provider_format, mode, prefix in capture.golden_paths():
    CASES.append((input_path, provider_format, mode, prefix))
CASE_IDS = ["%s:%s:%s" % (c[0].parent.name, c[0].stem, c[2]) for c in CASES]


@pytest.mark.parametrize("input_path,provider_format,mode,prefix", CASES, ids=CASE_IDS)
def test_golden_replay_is_byte_identical(input_path, provider_format, mode, prefix):
    captured = capture.replay(input_path, provider_format, mode)
    directory = os.path.dirname(str(prefix))
    base = os.path.basename(str(prefix))
    for kind in ("events", "stdout") + (("sidecar",) if mode == "correlated" else ()):
        golden_path = os.path.join(directory, "%s.%s.golden" % (base, kind))
        with open(golden_path, "rb") as handle:
            expected = handle.read()
        assert captured[kind] == expected, "golden mismatch: %s" % golden_path


def _category_inputs(directory):
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.endswith(".jsonl")
    )


def _assert_full_golden_coverage(input_dir, golden_dir):
    inputs = _category_inputs(input_dir)
    assert inputs, "no golden inputs found in %s" % input_dir
    for input_path in inputs:
        stem = os.path.basename(input_path)[:-len(".jsonl")]
        for mode in ("legacy", "correlated"):
            for kind in ("events", "stdout") + (("sidecar",) if mode == "correlated" else ()):
                golden = os.path.join(
                    golden_dir, "%s.%s.%s.golden" % (stem, mode, kind))
                assert os.path.exists(golden), "missing golden: %s" % golden


def test_golden_set_covers_every_prime_fixture():
    _assert_full_golden_coverage(
        PRIME_INPUT_DIR, os.path.join(GOLDEN_DIR, "prime-v3"))


def test_golden_set_covers_every_claude_fixture():
    _assert_full_golden_coverage(
        os.path.join(GOLDEN_DIR, "claude"), os.path.join(GOLDEN_DIR, "claude"))
