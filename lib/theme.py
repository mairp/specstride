#!/usr/bin/env python3
"""theme.py: the one source of truth for how Specstride looks in a terminal.

Stdlib only. The banner (lib/banner.py) and the live presenter (lib/present.py)
both import it. Callers name a ROLE; they never write escape codes themselves.

Roles
  brand     the mark, the name, walked track. "Ink": always the terminal's own
            foreground (SGR 39), so it survives any 16-color remap.
  accent    the current gate and the cursor. Nothing else.
  approved  ✓ gates, APPROVED verdicts, run complete.
  warn      diagnosing, MALFORMED, stuck, halt.
  reject    ✗ gates, REJECTED verdicts.
  muted     track ahead, timestamps, captions, dividers.

Color depth is resolved in this order (first match wins):
  1. an explicit --color=always|never|auto from the caller
  2. NO_COLOR set and non-empty       -> none
  3. FORCE_COLOR set and non-empty    -> color, even when not a TTY
  4. not a TTY, or TERM=dumb          -> none
  5. COLORTERM=truecolor|24bit        -> truecolor
  6. TERM contains 256color           -> 256 (quantized to the xterm cube)
  7. otherwise                        -> 16
  SPECSTRIDE_COLOR=16|256|truecolor|none then overrides the result.

Background: SPECSTRIDE_BANNER_BG -> COLORFGBG -> OSC 11 query of /dev/tty -> dark.
The query is skipped inside tmux or screen (TMUX / STY), and a late reply is
drained so it can't leak into the shell.

Glyphs: box drawing and block elements are safe; ✓ ✗ • · → › and braille are
mostly safe; sextants, octants, emoji and anything that can take emoji
presentation are forbidden. SPECSTRIDE_ASCII=1, or a non-UTF-8 locale, maps
every glyph to plain ASCII.
"""
import os
import re
import select
import sys
import unicodedata

ROLES = ("brand", "accent", "approved", "warn", "reject", "muted")

# role -> dark hex, light hex, 16-color SGR on dark, 16-color SGR on light.
# brand is ink: the hexes are for the README and the web; terminals get SGR 39.
# muted on light is dim (2), not 90: some light themes (Solarized Light) map
# bright black to near-black, which would read louder than the ink.
PALETTE = {
    "brand":    {"dark": "#E8E6DF", "light": "#1F2328", "16": ("39", "39"), "ink": True},
    "accent":   {"dark": "#F07CC0", "light": "#B0287A", "16": ("95", "35")},
    "approved": {"dark": "#4ADE80", "light": "#1A7F37", "16": ("32", "32")},
    "warn":     {"dark": "#F5B342", "light": "#9A5B00", "16": ("33", "33")},
    "reject":   {"dark": "#FF6B6B", "light": "#C62828", "16": ("31", "31")},
    "muted":    {"dark": "#8A93A6", "light": "#5E6778", "16": ("90", "2")},
}

DEPTHS = ("none", "16", "256", "truecolor")
RESET = "\033[0m"


def _flag(environ, name):
    return bool(environ.get(name, ""))


def _isatty(stream):
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def resolve_depth(arg=None, stream=None, environ=None):
    """The color depth for `stream`: one of DEPTHS. See the module docstring."""
    environ = os.environ if environ is None else environ
    stream = sys.stdout if stream is None else stream
    arg = (arg or "auto").lower()
    term = environ.get("TERM", "")

    def by_terminal():
        if environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
            return "truecolor"
        if "256color" in term:
            return "256"
        return "16"

    if arg == "never":
        depth = "none"
    elif arg == "always":
        depth = by_terminal()
    elif _flag(environ, "NO_COLOR"):
        depth = "none"
    elif _flag(environ, "FORCE_COLOR"):
        depth = by_terminal()
    elif not _isatty(stream) or term == "dumb":
        depth = "none"
    else:
        depth = by_terminal()

    override = environ.get("SPECSTRIDE_COLOR", "").lower()
    if override in DEPTHS:
        depth = override
    return depth


# ── background ──────────────────────────────────────────────────────────────

def _drain(fd, wait=0.05):
    """Swallow whatever the terminal still sends (a late OSC 11 reply)."""
    try:
        while select.select([fd], [], [], wait)[0]:
            if not os.read(fd, 256):
                break
    except OSError:
        pass


def _query_osc11(timeout=0.2):
    fd = os.open("/dev/tty", os.O_RDWR)
    try:
        import termios
        import tty
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            os.write(fd, b"\033]11;?\033\\")
            resp = b""
            while select.select([fd], [], [], timeout)[0]:
                resp += os.read(fd, 64)
                if b"\\" in resp or b"\a" in resp:
                    break
            _drain(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)
        m = re.search(rb"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", resp)
        if m:
            r, g, b = (int(m.group(i)[:2], 16) for i in (1, 2, 3))
            lum = (r * 299 + g * 587 + b * 114) // 1000
            return "light" if lum >= 128 else "dark"
        return None
    finally:
        os.close(fd)


def detect_bg(stream=None, environ=None, query=None):
    """'dark' or 'light'. `query` is the OSC 11 prober (injectable for tests)."""
    environ = os.environ if environ is None else environ
    stream = sys.stdout if stream is None else stream
    v = environ.get("SPECSTRIDE_BANNER_BG", "").lower()
    if v in ("dark", "light"):
        return v
    fgbg = environ.get("COLORFGBG", "")
    if fgbg:
        m = fgbg.split(";")[-1]
        if m.isdigit():
            return "light" if int(m) >= 11 else "dark"
    # tmux and screen answer OSC 11 unreliably (or late, into the shell): skip it.
    if environ.get("TMUX") or environ.get("STY"):
        return "dark"
    try:
        if _isatty(stream):
            got = (query or _query_osc11)()
            if got in ("dark", "light"):
                return got
    except Exception:
        pass
    return "dark"


# ── color ───────────────────────────────────────────────────────────────────

def _hex(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


_CUBE = (0, 95, 135, 175, 215, 255)


def to_256(rgb):
    """Nearest xterm-256 index (6x6x6 cube or the 24-step gray ramp)."""
    def near(v):
        return min(range(6), key=lambda i: abs(_CUBE[i] - v))
    ci = [near(c) for c in rgb]
    cube = tuple(_CUBE[i] for i in ci)
    cube_idx = 16 + 36 * ci[0] + 6 * ci[1] + ci[2]
    avg = sum(rgb) // 3
    gi = max(0, min(23, round((avg - 8) / 10)))
    gray = (8 + 10 * gi,) * 3

    def dist(a):
        return sum((x - y) ** 2 for x, y in zip(a, rgb))
    return cube_idx if dist(cube) <= dist(gray) else 232 + gi


class Theme:
    """Role -> escape codes for one (depth, bg). Every method returns plain
    text when depth is 'none'."""

    def __init__(self, depth="16", bg="dark"):
        self.depth = depth if depth in DEPTHS else "16"
        self.bg = bg if bg in ("dark", "light") else "dark"
        self.on = self.depth != "none"

    def sgr(self, role):
        if not self.on:
            return ""
        spec = PALETTE[role]
        if spec.get("ink"):
            return "\033[39m"
        if self.depth == "16":
            return "\033[%sm" % spec["16"][0 if self.bg == "dark" else 1]
        rgb = _hex(spec[self.bg])
        if self.depth == "256":
            return "\033[38;5;%dm" % to_256(rgb)
        return "\033[38;2;%d;%d;%dm" % rgb

    @property
    def reset(self):
        return RESET if self.on else ""

    @property
    def bold(self):
        return "\033[1m" if self.on else ""

    def paint(self, role, text):
        if not self.on or not text:
            return text
        return self.sgr(role) + text + RESET


def theme_for(arg=None, stream=None, environ=None, bg=None):
    """Convenience: resolve depth and background together."""
    depth = resolve_depth(arg, stream, environ)
    if depth == "none":
        return Theme("none", bg or "dark")
    return Theme(depth, bg or detect_bg(stream, environ))


# ── glyphs ──────────────────────────────────────────────────────────────────

# name -> (unicode, ascii). Every glyph here is one column wide.
GLYPHS = {
    "track":     ("━", "="),
    "ahead":     ("╍", "-"),
    "gate":      ("┿", "+"),
    "tip":       ("╸", ">"),
    "end":       ("╸", "-"),
    "approved":  ("✓", "ok"),
    "rejected":  ("✗", "x"),
    "current":   ("•", ">"),
    "pending":   ("·", "."),
    "times":     ("×", "x"),
    "divider":   ("┄", "-"),
    "arrow":     ("→", "->"),
    "prompt":    ("›", ">"),
    "quote":     ("│", "|"),
}

# Explicit display widths for the non-ASCII glyphs we draw. There is no
# wcwidth in the stdlib, so this table is the contract; everything in it is 1.
WIDTH = {ch: 1 for ch in "━╍┿╸╺┏┓┗┛┃✓✗•·×┄→›│◆⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"}

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(s):
    return _ANSI.sub("", s)


def display_width(s):
    w = 0
    for ch in strip_ansi(s):
        if ch in WIDTH:
            w += WIDTH[ch]
        elif unicodedata.combining(ch):
            continue
        elif unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 2
        else:
            w += 1
    return w


def ascii_mode(environ=None):
    """True when glyphs must be pure ASCII."""
    environ = os.environ if environ is None else environ
    if environ.get("SPECSTRIDE_ASCII", "") not in ("", "0"):
        return True
    loc = environ.get("LC_ALL") or environ.get("LC_CTYPE") or environ.get("LANG") or ""
    if not loc:
        return False
    return not re.search(r"utf-?8", loc, re.I)


def glyph(name, ascii_=False):
    return GLYPHS[name][1 if ascii_ else 0]


# Characters that can take emoji presentation (from Unicode emoji-data, BMP),
# plus the whole Misc Symbols block, which renders unpredictably.
_EMOJI_BMP = set(
    "©®‼⁉™ℹ↔↕↖↗↘↙↩↪⌚⌛⌨⏏⏩⏪⏫⏬⏭⏮⏯⏰⏱⏲⏳⏸⏹⏺Ⓜ▪▫▶◀◻◼◽◾"
    "✂✅✈✉✊✋✌✍✏✒✔✖✝✡✨✳✴❄❇❌❎❓❔❕❗❣❤➕➖➗➡➰➿⤴⤵⬅⬆⬇⬛⬜⭐⭕〰〽㊗㊙"
)


def forbidden(ch):
    """True for glyphs Specstride never draws: sextants/octants (U+1FB00
    block), emoji, and anything that can take emoji presentation."""
    cp = ord(ch)
    return (0x1FB00 <= cp <= 0x1FBFF or 0x1F000 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x26FF or cp == 0xFE0F or ch in _EMOJI_BMP)
