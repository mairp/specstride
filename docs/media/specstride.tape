# The README hero: the splash with a live rail, then a recorded run of phase 3
# (a rejection, the diagnostician, the accelerator, an approval) replayed from
# docs/media/demo-events.jsonl through the real presenter.
#
# Render from the repo root (needs charmbracelet/vhs):
#   vhs docs/media/specstride.tape
# It writes docs/media/specstride.gif and the still docs/media/specstride.png.

Output docs/media/specstride.gif

Set Shell bash
Set FontSize 16
Set Width 1100
Set Height 680
Set Padding 28
Set TypingSpeed 45ms
Set Theme { "name": "specstride", "background": "#1E1E1E", "foreground": "#D4D4D4", "cursor": "#F07CC0", "selection": "#3A3A3A", "black": "#1E1E1E", "red": "#FF6B6B", "green": "#4ADE80", "yellow": "#F5B342", "blue": "#8B9CFF", "magenta": "#F07CC0", "cyan": "#5EE0C8", "white": "#D4D4D4", "brightBlack": "#8A93A6", "brightRed": "#FF6B6B", "brightGreen": "#4ADE80", "brightYellow": "#F5B342", "brightBlue": "#8B9CFF", "brightMagenta": "#F07CC0", "brightCyan": "#5EE0C8", "brightWhite": "#FFFFFF" }

Hide
Type "set +m; export PS1='$ ' SPECSTRIDE_BANNER_BG=dark LANG=C.UTF-8" Enter
Type `specstride() ( python3 lib/banner.py --phases 5 --states A,A,C,P,P; printf '\n  feature  add-session-auth\n  agents   proposer codex, critic claude\n  log      .specstride/features/add-session-auth/run.log\n\n'; E=$(mktemp); (while IFS= read -r l; do printf '%s\n' "$l" >> "$E"; sleep 0.45; done < docs/media/demo-events.jsonl) & python3 lib/present.py --events "$E" --mode timeline --follow & P=$!; sleep 9; kill $P; wait 2>/dev/null; printf '\r\033[K'; rm -f "$E" )` Enter
Type "clear" Enter
Show

Sleep 400ms
Type "specstride run"
Sleep 300ms
Enter
Sleep 10s
Screenshot docs/media/specstride.png
Sleep 2s
