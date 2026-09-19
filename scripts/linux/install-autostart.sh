#!/usr/bin/env bash
#
# Start DCCore with the system, on Linux (#547, Proposal 6).
#
# Writes a systemd USER unit, ~/.config/systemd/user/dccore.service, that
# runs start-dccore.sh - the launcher, not oserve.py directly, because the
# launcher is what puts the working directory right (every data path is
# relative; see docs/INSTALL.md) - and enables it now and at every login.
# remove-autostart.sh takes it out again.
#
# A user unit needs no root and stores no password. It starts when you log
# in; to have it start at boot without a login, once:
#
#     loginctl enable-linger $USER
#
# Portable sh apart from the shebang: no bashisms, no `readlink -f`.

cd "$(cd "$(dirname "$0")" && pwd -P)/../.." || exit 1
ROOT="$(pwd -P)"

# Autostart on a tree that has never been set up would ask the setup
# questions to nobody at every login. Once by hand first.
if [ ! -f "admin_config.py" ] && [ ! -f "settings.conf" ]; then
    echo
    echo "  DCCore is not set up yet. Run ./scripts/linux/start-dccore.sh once"
    echo "  first - it asks the setup questions - then this file."
    echo
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo
    echo "  systemctl was not found, so this is not a systemd system. Start"
    echo "  the launcher from whatever your init uses (a cron @reboot line,"
    echo "  an rc.local entry) with the working directory set to:"
    echo "      $ROOT"
    echo
    exit 1
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/dccore.service"
mkdir -p "$UNIT_DIR" || exit 1

cat > "$UNIT" <<EOF
# Written by scripts/linux/install-autostart.sh - re-run it after moving the
# folder; remove-autostart.sh deletes this file.
[Unit]
Description=DCCore IRC file server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/scripts/linux/start-dccore.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload || exit 1
systemctl --user enable --now dccore.service || exit 1

echo
echo "  Done: DCCore is running now and starts at every login."
echo "      systemctl --user status dccore      how it is doing"
echo "      journalctl --user -u dccore -f      its output"
echo "      systemctl --user stop dccore        stop it (until next login)"
echo "  To start it at boot without logging in, once:"
echo "      loginctl enable-linger $USER"
echo "  ./scripts/linux/remove-autostart.sh undoes this."
echo
