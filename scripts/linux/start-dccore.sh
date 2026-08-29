#!/usr/bin/env bash
#
# DCCore launcher for Linux.
#
# The important line is the cd below. Every data path in config.py is
# relative - ./data/bans.txt, ./lists - so they resolve against the
# working directory. Running this from a cron job, a systemd unit with
# no WorkingDirectory set, or a symlink on PATH would otherwise start
# the daemon with a working directory that is not the repository, and
# it would quietly create an empty data folder somewhere else and boot
# with no bans, no queue and no list.
#
# Usage:
#   ./scripts/linux/start-dccore.sh          check the setup, then start the daemon
#   ./scripts/linux/start-dccore.sh check    check the setup and stop

cd "$(dirname "$(readlink -f "$0")")/../.." || exit 1

# --- find an interpreter ------------------------------------------------
PY=""
if command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
fi

if [ -z "$PY" ]; then
    echo
    echo "  Python was not found."
    echo
    echo "  Install Python 3.10 or newer (most distributions: your package"
    echo "  manager's \"python3\" package), then run this again."
    echo
    exit 1
fi

# --- check-only mode ------------------------------------------------------
if [ "$1" = "check" ]; then
    "$PY" scripts/linux/check-setup.py
    exit $?
fi

# --- refuse to start without a local config --------------------------------
if [ ! -f "local_config.py" ]; then
    echo
    echo "  No local_config.py found."
    echo
    echo "  Copy local_config.py.sample to local_config.py and fill it in."
    echo "  Without it the daemon uses the defaults in config.py, which"
    echo "  point at somebody else's live bot and channels."
    echo
    exit 1
fi

# --- refuse to start on a broken or dangerous config ------------------------
# check-setup.py fails on a missing music directory, and on a config still
# pointing at the production bot's nick or channels. That second one is worth
# blocking: it would put a near-identical second bot into live trading
# channels, which can get the other operator banned too.
if ! "$PY" scripts/linux/check-setup.py >/dev/null 2>&1; then
    echo
    echo "  Setup check failed - not starting. Details:"
    echo
    "$PY" scripts/linux/check-setup.py
    echo
    exit 1
fi

# --- go ---------------------------------------------------------------------
echo
echo "  Starting DCCore.  Press Ctrl-C to stop it."
echo
"$PY" oserve.py
RC=$?

echo
if [ "$RC" -eq 0 ]; then
    echo "  DCCore exited normally."
else
    echo "  DCCore exited with code $RC."
fi
echo
exit $RC
