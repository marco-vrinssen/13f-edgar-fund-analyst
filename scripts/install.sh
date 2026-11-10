#!/bin/bash
# Links the CLI and the skill into place and loads the weekly LaunchAgent. Safe to run again.
# WEEKDAY (0-7, Sunday is 0 or 7) and HOUR set the schedule, default Saturday 09:00 local time,
# after the week's EDGAR filings are in.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
label="13f-edgar-fund-analyst.weekly"
plist="$HOME/Library/LaunchAgents/$label.plist"
log="$HOME/Library/Logs/13f-edgar-fund-analyst.log"

mkdir -p "$HOME/.local/bin" "$HOME/.claude/skills" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
ln -sfn "$repo/bin/13f" "$HOME/.local/bin/13f"
ln -sfn "$repo/skills/13f-fund-analyst" "$HOME/.claude/skills/13f-fund-analyst"

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$repo/scripts/weekly-check.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>${WEEKDAY:-6}</integer>
    <key>Hour</key><integer>${HOUR:-9}</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
echo "Linked 13f into ~/.local/bin and the skill into ~/.claude/skills."
echo "Weekly check loaded as $label, log at $log."
