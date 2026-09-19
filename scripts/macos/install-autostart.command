#!/bin/sh
#
# Start DCCore when you log in, on macOS (#547, Proposal 6).
#
# Writes a launchd agent, ~/Library/LaunchAgents/com.dccore.bot.plist, that
# runs start-dccore.sh - the launcher, not oserve.py directly, because the
# launcher is what puts the working directory right - and loads it now and
# at every login. Its output goes to ~/Library/Logs/dccore.log.
# remove-autostart.command takes it out again. No administrator needed.
#
# Double-click it in Finder (the first time: right-click, Open, because it
# came from the internet), or run it from Terminal. Plain sh: macOS's
# default shell for .command files is zsh, but /bin/sh is always there.

cd "$(cd "$(dirname "$0")" && pwd -P)/../.." || exit 1
ROOT="$(pwd -P)"

if [ ! -f "admin_config.py" ] && [ ! -f "settings.conf" ]; then
    echo
    echo "  DCCore is not set up yet. Run start-dccore.command once first - it"
    echo "  asks the setup questions - then this file."
    echo
    exit 1
fi

if ! command -v launchctl >/dev/null 2>&1; then
    echo
    echo "  launchctl was not found - this does not look like macOS."
    echo
    exit 1
fi

AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/com.dccore.bot.plist"
LOG="$HOME/Library/Logs/dccore.log"
mkdir -p "$AGENTS" "$HOME/Library/Logs" || exit 1

# The path goes into XML; the three characters XML cares about are escaped.
ROOT_XML=$(printf '%s' "$ROOT" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')
LOG_XML=$(printf '%s' "$LOG" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dccore.bot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$ROOT_XML/scripts/linux/start-dccore.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$ROOT_XML</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG_XML</string>
    <key>StandardErrorPath</key>
    <string>$LOG_XML</string>
</dict>
</plist>
EOF

# Unload first so a re-run after moving the folder picks up the new path.
launchctl unload -w "$PLIST" >/dev/null 2>&1
launchctl load -w "$PLIST" || exit 1

echo
echo "  Done: DCCore is running now and starts at every login."
echo "      tail -f \"$LOG\"                       its output"
echo "      launchctl unload -w \"$PLIST\"    stop it until next login"
echo "  remove-autostart.command undoes this."
echo
