#!/bin/bash
# Weekly job: writes a 13F report only when a tracked fund has a new filing.
# Claude writes the full report through the 13f-fund-analyst skill. Without Claude, a data-only note is written.
set -uo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$repo/bin:$PATH"
echo "== $(date '+%Y-%m-%d %H:%M') weekly 13F check"

13f check
case $? in
  0) ;;
  1) exit 0 ;;
  *) echo "check failed"; exit 1 ;;
esac

note="$(13f note-path)" || exit 1
claude_bin="${CLAUDE_BIN:-$(command -v claude || true)}"
if [ -n "$claude_bin" ]; then
  (cd "$repo" && "$claude_bin" -p \
    "Use the 13f-fund-analyst skill to write the 13F report for the new filings and save it with 13f note." \
    --allowedTools "Bash(13f:*)" "Skill") || echo "claude run failed"
fi

if [ ! -f "$note" ]; then
  echo "no report from Claude, writing a data-only note"
  13f report || exit 1
fi
13f mark-reported
