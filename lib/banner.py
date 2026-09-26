#!/usr/bin/env python3
"""banner.py: the Specstride startup splash.

The mark is the track folded into an S: a switchback rail with gate posts. Below
it, the same rail runs straight and shows the feature's real phase states, so the
splash doubles as status. Nothing advances until the critic approves it.

Usage:
  banner.py                          auto-detect width, color depth and background
  banner.py --phases 5 --states A,A,C,P,P
                                     live rail: A approved, R rejected, C current,
                                     P pending (missing states are pending)
  banner.py --plain                  ASCII, no escapes (the run.log copy)
  banner.py --bg dark|light          force the background
  banner.py --color always|never|auto
  banner.py --no-motion              print the final frame only
  banner.py --preview                every tier x background x depth, labelled

Appearance switches (documented in wiki/CLI-Reference.md):
  SPECSTRIDE_BANNER=off     print nothing (CI logs, screen readers)
  SPECSTRIDE_MOTION=0       never animate (also: CI set, TERM=dumb, not a TTY)
  SPECSTRIDE_COLOR=16|256|truecolor|none   override the detected color depth
  SPECSTRIDE_ASCII=1        ASCII glyphs only (also: a non-UTF-8 locale)
  SPECSTRIDE_BANNER_BG=dark|light          skip background detection
  NO_COLOR / FORCE_COLOR    the usual meaning; see lib/theme.py for the order

Width tiers: 72+ columns full mark, name, caption and rail; 40-71 compact mark,
name and rail; under 40 columns (or under 14 rows) one line.
"""
import argparse
import atexit
import os
import shutil
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import specstride_env  # noqa: E402  (legacy env names map onto SPECSTRIDE_*)
specstride_env.apply()
import theme as T  # noqa: E402

# ── the mark (the single source for terminal, README and web) ────────────────
MARK_FULL = [
    "┏━━━━┿━━━━┿━━━━╸",
    "┃",
    "┗━━━━┿━━━━┿━━━━┓",
    "               ┃",
    "╺━━━━┿━━━━┿━━━━┛",
]
MARK_FULL_ASCII = [
    ".====+====+====-",
    "|",
    "'====+====+====.",
    "               |",
    "-====+====+===='",
]
MARK_COMPACT = ["┏━━┿━━╸", "┗━━┿━━┓", "╺━━┿━━┛"]
MARK_COMPACT_ASCII = [".==+==-", "'==+==.", "-==+=='"]
MARK_LINE, MARK_LINE_ASCII = "┗┓", "'."

NAME = "specstride"
DESCRIPTION = "From specs to tested code."
CAPTION = "Every step, signed off."

INDENT = "  "
FULL_MIN_COLS, COMPACT_MIN_COLS, MIN_ROWS = 72, 40, 14
FRAME_SECONDS, FRAMES = 0.075, 9

STATE_ROLE = {"A": "approved", "R": "reject", "C": "accent", "P": "muted"}
STATE_GLYPH = {"A": "approved", "R": "rejected", "C": "current", "P": "pending"}
ASCII_WORD = {"A": "ok", "R": "rej", "C": "now", "P": "-"}
ASCII_SHORT = {"A": "+", "R": "x", "C": ">", "P": "."}


# ── states ──────────────────────────────────────────────────────────────────

def parse_states(phases=None, states=None):
    """-> list of 'A'|'R'|'C'|'P', or None for the decorative rail."""
    given = [s.strip().upper()[:1] for s in (states or "").split(",") if s.strip()]
    given = [s if s in STATE_ROLE else "P" for s in given]
    if phases is None:
        return given[:40] or None
    n = max(1, min(40, int(phases)))
    return (given + ["P"] * n)[:n]


def frontier(states):
    """Index of the first gate that isn't approved (None when all are)."""
    for i, s in enumerate(states):
        if s != "A":
            return i
    return None


# ── the rail ────────────────────────────────────────────────────────────────
# A rail is laid out as a list of segments; each segment is (text, role) where
# role 'track' means "walked or ahead, decided at paint time by its column".

class Cell:
    __slots__ = ("ch", "kind", "gate")

    def __init__(self, ch, kind, gate=None):
        self.ch, self.kind, self.gate = ch, kind, gate


def _label(state, num, ascii_, spaced):
    if ascii_:
        return f"{num} {ASCII_WORD[state]}" if spaced else f"{num}{ASCII_SHORT[state]}"
    g = T.glyph(STATE_GLYPH[state])
    return f"{g} {num}" if spaced else f"{g}{num}"


def _group_label(state, count, ascii_, first=1):
    if ascii_:
        return f"{first}-{first + count - 1} {ASCII_WORD[state]}"
    return f"{T.glyph(STATE_GLYPH[state])}{T.glyph('times')}{count}"


def layout_rail(states, width, ascii_=False, lead=4, tail=4, max_gap=10):
    """Fit the rail into `width` columns. Returns (gates, labels, length):
    gates is a list of (col, index or None, state); labels is a list of
    (col, text, state). A None index is a collapsed run."""
    n = len(states)
    f = frontier(states)
    f = n - 1 if f is None else f

    def attempt(start, k, spaced, gap):
        pre, post = start, n - start - k
        cols, labels, gates = [], [], []
        col = lead
        if pre:
            pl = _group_label("A", pre, ascii_)
            labels.append((0, pl, "A"))
            col = max(lead, T.display_width(pl) + 2)
        for j in range(k):
            i = start + j
            gates.append((col, i, states[i]))
            labels.append((col, _label(states[i], i + 1, ascii_, spaced), states[i]))
            cols.append(col)
            col += gap
        end = cols[-1] + 1
        if post:
            rest = states[start + k:]
            st = rest[0] if all(s == rest[0] for s in rest) else "P"
            gl = _group_label(st, post, ascii_, start + k + 1)
            labels.append((cols[-1] + gap, gl, st))
            end = cols[-1] + gap + T.display_width(gl)
        length = max(end + tail, max(c + T.display_width(t) for c, t, _ in labels))
        # labels must not collide
        for (c1, t1, _), (c2, _t, _s) in zip(labels, labels[1:]):
            if c1 + T.display_width(t1) >= c2:
                return None
        return gates, labels, length

    for spaced in (True, False):
        for k in range(n, 0, -1):
            start = max(0, min(f - 1 if k > 1 else f, n - k))
            widest = max(T.display_width(_label(states[i], i + 1, ascii_, spaced))
                         for i in range(start, start + k))
            for gap in range(max_gap, widest + 1, -1):
                got = attempt(start, k, spaced, gap)
                if got and got[2] <= width:
                    return got
    return None


def render_rail(states, width, th, ascii_=False, compact=False, progress=None):
    """Two lines: the rail and its labels, at most `width` columns each.
    `progress` (a column) draws an animation frame: track walked up to the
    cursor, labels only for gates already passed."""
    lead, tail, gap = (3, 3, 7) if compact else (4, 4, 10)
    if states is None:  # decorative: five posts, all ink
        cells = []
        for c in range(lead + 4 * gap + 1 + tail):
            is_gate = c >= lead and (c - lead) % gap == 0 and c <= lead + 4 * gap
            cells.append(T.glyph("gate" if is_gate else "track", ascii_))
        line = "".join(cells)[:width]
        return [th.paint("brand", line), ""]
    got = layout_rail(states, width, ascii_, lead, tail, gap)
    if got is None:
        return [_summary(states, th, ascii_)[:width], ""]
    gates, labels, length = got
    f = frontier(states)
    all_done = f is None
    gate_at = {c for c, _i, _s in gates}
    front_col = None
    if not all_done:
        front_col = next(c for c, i, s in gates if i == f)
    walked_to = length if all_done else front_col  # columns < walked_to are earned

    out, role_run, buf = [], None, []

    def flush():
        if buf:
            out.append(th.paint(role_run, "".join(buf)))

    for c in range(length):
        if c in gate_at:
            ch = T.glyph("gate", ascii_)
            if progress is not None:
                role = "brand" if c <= progress else "muted"
            elif c == front_col:
                role = "accent"
            else:
                role = "brand" if c < walked_to else "muted"
        else:
            earned = c < walked_to if progress is None else c < progress
            if all_done and c == length - 1 and progress is None:
                ch = T.glyph("end", ascii_)
            elif progress is not None and c == progress:
                ch = T.glyph("tip", ascii_)
            else:
                ch = T.glyph("track" if earned else "ahead", ascii_)
            role = "brand" if earned else "muted"
            if progress is not None and c == progress:
                role = "accent"
        if role != role_run:
            flush()
            role_run, buf = role, []
        buf.append(ch)
    flush()
    rail = "".join(out)

    lab, col = [], 0
    for c, text, st in labels:
        if progress is not None and c > progress:
            continue
        lab.append(" " * (c - col))
        role = "muted" if progress is not None and st == "C" else STATE_ROLE[st]
        lab.append(th.paint(role, text))
        col = c + T.display_width(text)
    return [rail, "".join(lab)]


def _summary(states, th, ascii_):
    f = frontier(states)
    n = len(states)
    if f is None:
        return th.paint("approved", f"{T.glyph('approved', ascii_)} {n}/{n}")
    st = states[f]
    return th.paint(STATE_ROLE[st], f"{T.glyph(STATE_GLYPH[st], ascii_)}{f + 1}/{n}")


def oneline_rail(states, th, ascii_):
    """States on the posts: ━✓━✓━•╍·╍· with runs of 3+ collapsed."""
    if states is None:
        return ""
    f = frontier(states)
    tokens, i = [], 0
    while i < len(states):
        j = i
        while j < len(states) and states[j] == states[i]:
            j += 1
        run = j - i
        st = states[i]
        g = T.glyph(STATE_GLYPH[st], ascii_)
        earned = f is None or i < f
        sep = th.paint("brand" if earned or i == f else "muted",
                       T.glyph("track" if earned or i == f else "ahead", ascii_))
        if run >= 3:
            tokens.append(sep + th.paint(STATE_ROLE[st], g + T.glyph("times", ascii_) + str(run)))
        else:
            for _ in range(run):
                tokens.append(sep + th.paint(STATE_ROLE[st], g))
        i = j
    return "".join(tokens)


# ── tiers ───────────────────────────────────────────────────────────────────

def tier_for(cols, rows):
    if rows < MIN_ROWS or cols < COMPACT_MIN_COLS:
        return "line"
    return "full" if cols >= FULL_MIN_COLS else "compact"


def _head(tier, th, ascii_):
    """The mark with the name beside it (the part that never animates)."""
    name = th.bold + th.paint("brand", NAME) + (th.reset if th.on else "")
    if tier == "full":
        m = MARK_FULL_ASCII if ascii_ else MARK_FULL
        ink = [th.paint("brand", ln) for ln in m]
        pad = [len(ln) for ln in m]
        col = 16 + 4

        def beside(k, text):
            return INDENT + ink[k] + " " * (col - pad[k]) + text
        return [INDENT + ink[0], beside(1, name), beside(2, DESCRIPTION),
                beside(3, th.paint("muted", CAPTION)), INDENT + ink[4]]
    m = MARK_COMPACT_ASCII if ascii_ else MARK_COMPACT
    ink = [th.paint("brand", ln) for ln in m]
    return [INDENT + ink[0], INDENT + ink[1] + "   " + name,
            INDENT + ink[2] + "   " + DESCRIPTION]


def render(states=None, cols=80, rows=24, th=None, ascii_=False, tier=None, progress=None):
    """The splash as a list of lines, none wider than `cols`."""
    th = th or T.Theme("none")
    tier = tier or tier_for(cols, rows)
    if tier == "line":
        mark = MARK_LINE_ASCII if ascii_ else MARK_LINE
        head = th.paint("brand", mark) + " " + th.bold + th.paint("brand", NAME) + \
            (th.reset if th.on else "")
        indent = INDENT if cols >= 30 else ""
        base = T.display_width(indent + mark + " " + NAME)
        room = cols - base - 2
        rail = oneline_rail(states, th, ascii_) if states else ""
        if rail and T.display_width(rail) > room:
            rail = _summary(states, th, ascii_)
            if T.display_width(rail) > room:
                rail = ""
        line = indent + head + ("  " + rail if rail else "")
        if T.display_width(line) > cols:  # absurdly narrow: the name alone
            line = NAME[:max(0, cols)]
        return [line]
    lines = _head(tier, th, ascii_) + [""]
    lines += [INDENT + ln if ln else "" for ln in
              render_rail(states, cols - len(INDENT) - 1, th, ascii_,
                          compact=(tier == "compact"), progress=progress)]
    return lines


def frames(states, cols, rows, th, ascii_):
    """The motion: 9 frames. The cursor walks earned track to the current gate,
    each passed gate settles into its state, and the last frame lands the
    accent on the current gate. Only the two rail lines change."""
    tier = tier_for(cols, rows)
    final = render(states, cols, rows, th, ascii_, tier)
    if tier == "line" or states is None:
        return [final]
    width = cols - len(INDENT) - 1
    got = layout_rail(states, width, ascii_, *((3, 3, 7) if tier == "compact" else (4, 4, 10)))
    if got is None:
        return [final]
    gates, _labels, length = got
    f = frontier(states)
    target = length - 1 if f is None else next(c for c, i, s in gates if i == f)
    out = []
    walk = FRAMES - 3  # frames 0..6 walk, 7..8 settle
    for k in range(FRAMES - 1):
        p = min(target, round(target * k / walk)) if walk else target
        out.append(render(states, cols, rows, th, ascii_, tier, progress=p))
    out.append(final)
    return out


# ── output ──────────────────────────────────────────────────────────────────

HIDE, SHOW = "\033[?25l", "\033[?25h"
SYNC_ON, SYNC_OFF = "\033[?2026h", "\033[?2026l"


def _write(fd_stream, text):
    fd_stream.write(text)
    fd_stream.flush()


def play(frame_list, out=None, sleep=time.sleep):
    """Draw `frame_list` in place: one write per frame, synchronized output,
    cursor hidden. Cursor and SGR are restored whatever happens."""
    out = out or sys.stdout
    restored = []

    def restore(*_):
        if not restored:
            restored.append(1)
            try:
                _write(out, T.RESET + SHOW)
            except Exception:
                pass

    def on_signal(signum, _frame):
        restore()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    old = {}
    atexit.register(restore)
    try:
        for s in (signal.SIGINT, signal.SIGTERM):
            try:
                old[s] = signal.signal(s, on_signal)
            except ValueError:  # not the main thread
                pass
        first = frame_list[0]
        _write(out, HIDE + "\n".join(first) + "\n")
        moving = 2  # only the rail and its labels are redrawn
        for fr in frame_list[1:]:
            sleep(FRAME_SECONDS)
            buf = [SYNC_ON, "\033[%dA" % moving]
            for ln in fr[-moving:]:
                buf.append("\r\033[K" + ln + "\n")
            buf.append(SYNC_OFF)
            _write(out, "".join(buf))
    finally:
        restore()
        for s, h in old.items():
            try:
                signal.signal(s, h)
            except ValueError:
                pass
        atexit.unregister(restore)


def motion_allowed(stream=None, environ=None, no_motion=False):
    environ = os.environ if environ is None else environ
    stream = stream or sys.stdout
    if no_motion or environ.get("SPECSTRIDE_MOTION", "") == "0":
        return False
    if environ.get("CI") or environ.get("TERM", "") == "dumb":
        return False
    return T._isatty(stream)


def banner_off(environ=None):
    environ = os.environ if environ is None else environ
    return environ.get("SPECSTRIDE_BANNER", "").lower() in ("off", "0", "false", "no")


def preview(states):
    """Every tier x background x depth, plus the motion frames and the log copy."""
    states = states or parse_states(5, "A,A,C,P,P")
    out = []
    tiers = (("full", 80, 24), ("compact", 56, 20), ("line", 36, 12))
    for tier, cols, rows in tiers:
        for bg in ("dark", "light"):
            for depth in ("16", "256", "truecolor"):
                out.append(f"── {tier} tier, {cols} columns, {bg} background, {depth} colors")
                out += render(states, cols, rows, T.Theme(depth, bg), tier=tier)
                out.append("")
    for tier, cols, rows in tiers[:2]:
        out.append(f"── motion, {tier} tier: {FRAMES} frames, {int(FRAME_SECONDS * 1000)} ms each")
        for k, fr in enumerate(frames(states, cols, rows, T.Theme("16", "dark"), False)):
            out.append(f"   frame {k}, {int(k * FRAME_SECONDS * 1000)} ms")
            out += fr[-2:]
        out.append("")
    out.append("── plain (the run.log copy)")
    out += render(states, 80, 24, T.Theme("none"), ascii_=True, tier="full")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="banner.py", description="Specstride startup splash")
    ap.add_argument("--plain", action="store_true", help="ASCII, no escapes")
    ap.add_argument("--bg", choices=("dark", "light"))
    ap.add_argument("--color", choices=("always", "never", "auto"), default="auto")
    ap.add_argument("--phases", type=int)
    ap.add_argument("--states", default="")
    ap.add_argument("--no-motion", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--cols", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--rows", type=int, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    states = parse_states(args.phases, args.states)
    if args.preview:
        sys.stdout.write("\n".join(preview(states)) + "\n")
        return 0
    if banner_off():
        return 0

    size = shutil.get_terminal_size((80, 24))
    cols, rows = args.cols or size.columns, args.rows or size.lines
    plain = args.plain or (args.color != "always" and not T._isatty(sys.stdout))
    if plain:
        # the log copy: full-width ASCII, no escapes, whatever the terminal is
        lines = render(states, max(cols, 80), 24, T.Theme("none"), ascii_=True, tier="full")
        sys.stdout.write("\n".join(lines) + "\n")
        return 0

    th = T.theme_for(args.color, sys.stdout, bg=args.bg)
    ascii_ = T.ascii_mode()
    frame_list = frames(states, cols, rows, th, ascii_)
    if len(frame_list) > 1 and motion_allowed(no_motion=args.no_motion):
        play(frame_list)
    else:
        sys.stdout.write("\n".join(frame_list[-1]) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
