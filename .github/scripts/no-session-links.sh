#!/usr/bin/env bash
# no-session-links.sh — no AI-assistant session or conversation may be exposed by this repository.
#
# Fails when the vendor's .ai domain (any URL or mention) or a session trailer appears in:
#   * a tracked file;
#   * a commit message — the pushed commits on `push`, the pull request's commits otherwise;
#   * a pull request's title, body, comments, reviews or review comments.
# On a pull request (every PR, comment and review event) it also sets the commit status
# `no-session-links` on the PR's head, which the Mergify queue requires, so a comment added after
# CI ran still blocks the merge.
#
# The log names WHERE a hit is (file:line, commit, comment id), never the matched text: a public
# CI log must not republish the link it refused.
#
# The vendor name is spelled in octal escapes, so this file and the workflow never match
# themselves or a plain name search.
#
# Environment (set by .github/workflows/no-session-links.yml): GH_TOKEN, GITHUB_REPOSITORY,
# EVENT_NAME, PR_NUMBER (empty on push), PUSH_BEFORE, PUSH_AFTER.
set -euo pipefail

vendor="$(printf '\143\154\141\165\144\145')"
re="${vendor}\\.ai|^[[:space:]]*${vendor}-session:"
hits=0
hit() { printf 'FAIL %s\n' "$1" >&2; hits=1; }
has() { printf '%s' "$1" | grep -qiE "$re"; }

# ---- tracked files (the checked-out tree)
while IFS= read -r loc; do
  [[ -n "$loc" ]] && hit "tracked file $loc"
done < <(git grep -nIiE "$re" -- . | cut -d: -f1,2 || true)

# ---- commit messages of a push
if [[ "${EVENT_NAME:-}" == push && -n "${PUSH_AFTER:-}" ]]; then
  if [[ -z "${PUSH_BEFORE:-}" || "$PUSH_BEFORE" =~ ^0+$ ]]; then range="$PUSH_AFTER^!"; else range="$PUSH_BEFORE..$PUSH_AFTER"; fi
  for c in $(git rev-list "$range"); do
    has "$(git log -1 --format=%B "$c")" && hit "commit ${c:0:12} message"
  done
fi

# ---- a pull request: its commits and its whole conversation
if [[ -n "${PR_NUMBER:-}" ]]; then
  api="repos/$GITHUB_REPOSITORY"
  pr="$(gh api "$api/pulls/$PR_NUMBER")"
  head="$(jq -r .head.sha <<<"$pr")"
  has "$(jq -r '.title // ""' <<<"$pr")" && hit "PR #$PR_NUMBER title"
  has "$(jq -r '.body // ""' <<<"$pr")" && hit "PR #$PR_NUMBER body"
  scan_list() { # $1 = api path, $2 = jq expression yielding "<id>\t<base64 text>", $3 = label
    while IFS=$'\t' read -r id b64; do
      [[ -z "$id" ]] && continue
      has "$(printf '%s' "$b64" | base64 -d)" && hit "PR #$PR_NUMBER $3 $id"
    done < <(gh api --paginate "$1" --jq "$2")
  }
  scan_list "$api/pulls/$PR_NUMBER/commits?per_page=100" '.[] | [.sha[0:12], (.commit.message | @base64)] | @tsv' "commit"
  scan_list "$api/issues/$PR_NUMBER/comments?per_page=100" '.[] | [(.id|tostring), ((.body // "") | @base64)] | @tsv' "comment"
  scan_list "$api/pulls/$PR_NUMBER/reviews?per_page=100" '.[] | [(.id|tostring), ((.body // "") | @base64)] | @tsv' "review"
  scan_list "$api/pulls/$PR_NUMBER/comments?per_page=100" '.[] | [(.id|tostring), ((.body // "") | @base64)] | @tsv' "review comment"

  if [[ "$hits" -eq 0 ]]; then state=success; desc="no session or conversation link"; else state=failure; desc="a session or conversation link is exposed — see the run log"; fi
  gh api -X POST "$api/statuses/$head" -f state="$state" -f context=no-session-links -f description="$desc" \
    -f target_url="${GITHUB_SERVER_URL:-https://github.com}/$GITHUB_REPOSITORY/actions/runs/${GITHUB_RUN_ID:-}" >/dev/null
fi

if [[ "$hits" -ne 0 ]]; then
  echo "no-session-links: FAIL" >&2
  exit 1
fi
echo "no-session-links: PASS"
