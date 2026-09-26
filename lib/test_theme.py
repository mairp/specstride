"""theme.py: color-depth order, background detection, glyph tiers."""

import io

import pytest

import theme as T


class Tty(io.StringIO):
    def isatty(self):
        return True


class Pipe(io.StringIO):
    def isatty(self):
        return False


TTY = {"TERM": "xterm"}


@pytest.mark.parametrize("arg, env, stream, want", [
    ("always", {"NO_COLOR": "1"}, Pipe(), "16"),                      # 1 explicit wins
    ("never", {"FORCE_COLOR": "1", **TTY}, Tty(), "none"),            # 1 explicit wins
    (None, {"NO_COLOR": "1", **TTY}, Tty(), "none"),                  # 2
    (None, {"FORCE_COLOR": "1"}, Pipe(), "16"),                       # 3
    (None, {"TERM": "xterm-256color"}, Pipe(), "none"),               # 4 not a TTY
    (None, {"TERM": "dumb"}, Tty(), "none"),                          # 4 dumb
    (None, {"COLORTERM": "truecolor", **TTY}, Tty(), "truecolor"),    # 5
    (None, {"COLORTERM": "24bit", **TTY}, Tty(), "truecolor"),        # 5
    (None, {"TERM": "screen-256color"}, Tty(), "256"),                # 6
    (None, TTY, Tty(), "16"),                                         # 7
])
def test_depth_resolution_order(arg, env, stream, want):
    assert T.resolve_depth(arg, stream, env) == want


def test_no_color_beats_force_color():
    assert T.resolve_depth(None, Tty(), {"NO_COLOR": "1", "FORCE_COLOR": "1"}) == "none"


def test_empty_no_color_is_ignored():
    assert T.resolve_depth(None, Tty(), {"NO_COLOR": "", **TTY}) == "16"


@pytest.mark.parametrize("value", ["16", "256", "truecolor", "none"])
def test_specstride_color_overrides_the_result(value):
    env = {"SPECSTRIDE_COLOR": value, "COLORTERM": "truecolor", **TTY}
    assert T.resolve_depth(None, Tty(), env) == value


def test_no_color_produces_no_escape_at_all():
    th = T.theme_for(None, Tty(), {"NO_COLOR": "1", **TTY})
    text = "".join(th.paint(r, "x") + th.sgr(r) for r in T.ROLES) + th.reset + th.bold
    assert "\x1b" not in text


def test_force_color_without_a_tty_produces_color():
    th = T.theme_for(None, Pipe(), {"FORCE_COLOR": "1"}, bg="dark")
    assert "\x1b[" in th.paint("approved", "ok")


@pytest.mark.parametrize("bg", ["dark", "light"])
def test_sixteen_color_path_never_emits_extended_sgr(bg):
    th = T.Theme("16", bg)
    text = "".join(th.paint(r, "x") for r in T.ROLES)
    assert "38;2" not in text and "38;5" not in text


def test_brand_is_ink_in_every_depth():
    for depth in ("16", "256", "truecolor"):
        assert T.Theme(depth, "dark").sgr("brand") == "\x1b[39m"


def test_muted_is_dim_on_light_sixteen():
    assert T.Theme("16", "light").sgr("muted") == "\x1b[2m"
    assert T.Theme("16", "dark").sgr("muted") == "\x1b[90m"


def test_256_quantizes_into_the_xterm_palette():
    code = T.Theme("256", "dark").sgr("accent")
    idx = int(code.split(";")[-1].rstrip("m"))
    assert 16 <= idx <= 255


def test_ascii_env_maps_every_glyph_to_ascii():
    assert T.ascii_mode({"SPECSTRIDE_ASCII": "1", "LANG": "en_US.UTF-8"})
    for name in T.GLYPHS:
        assert T.glyph(name, True).isascii()


def test_non_utf8_locale_means_ascii():
    assert T.ascii_mode({"LANG": "C"})
    assert T.ascii_mode({"LC_ALL": "en_US.ISO-8859-1", "LANG": "en_US.UTF-8"})
    assert not T.ascii_mode({"LANG": "en_US.UTF-8"})
    assert not T.ascii_mode({})


def test_background_env_and_colorfgbg():
    assert T.detect_bg(Tty(), {"SPECSTRIDE_BANNER_BG": "light"}) == "light"
    assert T.detect_bg(Tty(), {"COLORFGBG": "0;15"}) == "light"
    assert T.detect_bg(Tty(), {"COLORFGBG": "15;0"}) == "dark"


@pytest.mark.parametrize("var", ["TMUX", "STY"])
def test_tmux_and_screen_skip_the_osc11_query(var):
    calls = []

    def probe():
        calls.append(1)
        return "light"
    assert T.detect_bg(Tty(), {var: "/tmp/tmux-0/default,1,0"}, query=probe) == "dark"
    assert calls == []


def test_osc11_query_is_used_on_a_plain_tty():
    assert T.detect_bg(Tty(), {}, query=lambda: "light") == "light"
    assert T.detect_bg(Pipe(), {}, query=lambda: "light") == "dark"


def test_display_width_ignores_escapes_and_counts_wide():
    assert T.display_width("\x1b[32m✓ 1\x1b[0m") == 3
    assert T.display_width("━┿╍") == 3
    assert T.display_width("漢") == 2


def test_forbidden_glyphs():
    for ch in "⚖💬⬆▶✔\U0001FB00\U0001FB3C":
        assert T.forbidden(ch), ch
    for ch in "━╍┿✓✗•·→›⠹┏┛│":
        assert not T.forbidden(ch), ch
