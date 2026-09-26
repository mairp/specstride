"""banner.py: width tiers, the live rail, plain mode, motion safety."""

import io
import os
import subprocess
import sys

import pytest

import banner as B
import theme as T

HERE = os.path.dirname(os.path.abspath(__file__))
FIVE = B.parse_states(5, "A,A,C,P,P")
FORTY = B.parse_states(40, ",".join(["A"] * 31 + ["C"] + ["P"] * 8))


def run(*args, **env):
    full = {k: v for k, v in os.environ.items()
            if k not in ("NO_COLOR", "FORCE_COLOR", "SPECSTRIDE_BANNER", "CI")}
    full.update(env)
    p = subprocess.run([sys.executable, os.path.join(HERE, "banner.py"), *args],
                       capture_output=True, text=True, env=full, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stdout


def all_outputs():
    """Every rendering the banner can produce, for glyph and wording checks."""
    out = []
    for states in (None, FIVE, FORTY, B.parse_states(1, "C")):
        for cols in (20, 36, 56, 80):
            for ascii_ in (False, True):
                for fr in B.frames(states, cols, 24, T.Theme("truecolor", "dark"), ascii_):
                    out += fr
    out += B.preview(None)
    return out


@pytest.mark.parametrize("states", [None, FIVE, FORTY, B.parse_states(12, "A,A,R")],
                         ids=["decorative", "five", "forty", "twelve"])
def test_every_tier_fits_its_width(states):
    for cols in range(20, 201):
        for rows in (10, 24):
            th = T.Theme("truecolor", "dark")
            for fr in B.frames(states, cols, rows, th, False):
                for line in fr:
                    assert T.display_width(line) <= cols, (cols, rows, line)


def test_tier_boundaries():
    assert B.tier_for(72, 24) == "full"
    assert B.tier_for(71, 24) == "compact"
    assert B.tier_for(40, 24) == "compact"
    assert B.tier_for(39, 24) == "line"
    assert B.tier_for(200, 13) == "line"


def _plain_rail(states, cols=80):
    lines = B.render(states, cols, 24, T.Theme("none"))
    return lines[-2], lines[-1]


def test_rail_reflects_states():
    rail, labels = _plain_rail(B.parse_states(5, "A,A,R,P,P"))
    assert labels.split() == ["✓", "1", "✓", "2", "✗", "3", "·", "4", "·", "5"]
    assert rail.count("┿") == 5
    # walked track stops at the frontier gate; everything after is ahead
    walked, ahead = rail.strip().split("┿", 3)[:3], rail.strip().rsplit("┿", 2)[-1]
    assert "╍" not in "".join(walked) and set(ahead) == {"╍"}


def test_rail_with_one_phase():
    rail, labels = _plain_rail(B.parse_states(1, "C"))
    assert rail.count("┿") == 1 and labels.split() == ["•", "1"]


def test_rail_with_forty_phases_compresses_instead_of_wrapping():
    for cols in (40, 56, 80, 200):
        rail, labels = _plain_rail(FORTY, cols)
        assert "×" in labels or cols == 200
        assert "• 32" in labels or "•32" in labels
        assert "\n" not in rail


def test_all_approved_closes_the_track():
    rail, labels = _plain_rail(B.parse_states(3, "A,A,A"))
    assert rail.rstrip().endswith("╸") and "╍" not in rail
    assert labels.count("✓") == 3


def test_missing_states_are_pending_and_extra_are_dropped():
    assert B.parse_states(4, "A") == ["A", "P", "P", "P"]
    assert B.parse_states(2, "A,A,A") == ["A", "A"]
    assert B.parse_states(99, "") == ["P"] * 40


def test_current_gate_is_the_only_accent():
    th = T.Theme("16", "dark")
    rail, labels = B.render(FIVE, 80, 24, th)[-2:]
    accent = th.sgr("accent")
    assert rail.count(accent) == 1 and labels.count(accent) == 1


def test_plain_output_contains_no_escapes():
    out = run("--plain", "--phases", "5", "--states", "A,A,C,P,P")
    assert "\x1b" not in out
    assert out.isascii()
    assert "3 now" in out and "specstride" in out


def test_non_tty_output_is_plain():
    out = run("--phases", "3", "--states", "A,C")
    assert "\x1b" not in out and out.isascii()


def test_banner_off_prints_nothing():
    assert run(SPECSTRIDE_BANNER="off") == ""
    assert run("--plain", SPECSTRIDE_BANNER="off") == ""


def test_ascii_mode_renders_only_ascii():
    for cols in (30, 56, 80):
        for line in B.render(FIVE, cols, 24, T.Theme("16", "dark"), ascii_=True):
            assert T.strip_ansi(line).isascii()


class Boom(Exception):
    pass


def test_cursor_is_restored_when_animation_fails_midway():
    out = io.StringIO()
    frames = B.frames(FIVE, 80, 24, T.Theme("16", "dark"), False)
    assert len(frames) == B.FRAMES
    calls = []

    def sleep(_):
        calls.append(1)
        if len(calls) == 3:
            raise Boom()
    with pytest.raises(Boom):
        B.play(frames, out=out, sleep=sleep)
    text = out.getvalue()
    assert text.startswith(B.HIDE)
    assert text.endswith(T.RESET + B.SHOW)


def test_animation_is_short_and_synchronized():
    out = io.StringIO()
    slept = []
    frames = B.frames(FIVE, 80, 24, T.Theme("16", "dark"), False)
    B.play(frames, out=out, sleep=slept.append)
    assert sum(slept) <= 0.7
    text = out.getvalue()
    assert text.count(B.SYNC_ON) == len(frames) - 1 == text.count(B.SYNC_OFF)
    assert "\x1b[?1049h" not in text  # never the alternate screen
    assert text.endswith(B.SHOW)


def test_final_frame_equals_the_static_render():
    th = T.Theme("16", "dark")
    assert B.frames(FIVE, 80, 24, th, False)[-1] == B.render(FIVE, 80, 24, th)


@pytest.mark.parametrize("env, tty, no_motion, want", [
    ({}, True, False, True),
    ({"CI": "true"}, True, False, False),
    ({"TERM": "dumb"}, True, False, False),
    ({"SPECSTRIDE_MOTION": "0"}, True, False, False),
    ({}, False, False, False),
    ({}, True, True, False),
])
def test_motion_gating(env, tty, no_motion, want):
    class S(io.StringIO):
        def isatty(self):
            return tty
    assert B.motion_allowed(S(), env, no_motion) is want


def test_no_forbidden_glyphs_in_any_output():
    for line in all_outputs():
        bad = [ch for ch in line if T.forbidden(ch)]
        assert not bad, (line, bad)


def test_the_old_splash_is_gone():
    text = "\n".join(all_outputs()).lower() + run("--plain").lower()
    for word in ("unpossible", "springfield", "wig" + "gum"):
        assert word not in text
