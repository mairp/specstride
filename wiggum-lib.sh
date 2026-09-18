#!/usr/bin/env bash
# wiggum-lib.sh — deprecated alias for specstride-lib.sh (Specstride was formerly
# Wiggum). Scripts that still source this file get the renamed library, plus the
# pre-rename wiggum_<name> spellings of its specstride_<name> functions.
# shellcheck source=/dev/null
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/specstride-lib.sh"
while read -r _specstride_fn; do
  eval "wiggum_${_specstride_fn#specstride_}() { $_specstride_fn \"\$@\"; }"
done < <(declare -F | awk '$3 ~ /^specstride_/ {print $3}')
unset _specstride_fn
