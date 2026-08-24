# =====================================================================
# PLATFORM_COMPAT.PY - the few places where Linux and Windows differ
# =====================================================================
# The daemon is otherwise plain Python and runs unchanged on both. This module
# exists so the handful of genuine differences live in ONE place instead of
# being scattered through dcc.py and irc.py as platform checks.
#
# Every function here is a no-op or an identity on Linux, so nothing about the
# production behaviour changes. The point is that a Windows build stops needing
# a fork of the shared code.
# ---------------------------------------------------------------------

import os
import shutil
import socket
import sys

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------
# The rar binary
# ---------------------------------------------------------------------
def rar_command(configured=None):
    """Return the rar executable to invoke, or None if it cannot be found.

    dcc.py used the bare string "rar", which relies on it being on PATH under
    that exact name. That holds on the Linux container and does not on Windows,
    where WinRAR installs rar.exe outside PATH entirely.

    A configured absolute path always wins, so an operator can point at a
    specific build without touching code.
    """
    if configured:
        if os.path.isfile(configured):
            return configured
        found = shutil.which(configured)
        if found:
            return found

    for candidate in ("rar", "rar.exe"):
        found = shutil.which(candidate)
        if found:
            return found

    if IS_WINDOWS:
        # WinRAR does not add itself to PATH, so look where it actually installs.
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if not base:
                continue
            candidate = os.path.join(base, "WinRAR", "rar.exe")
            if os.path.isfile(candidate):
                return candidate

    return None


# ---------------------------------------------------------------------
# Listening sockets
# ---------------------------------------------------------------------
def prepare_listener(sock):
    """Apply the correct address-reuse option for this platform.

    These two flags have the SAME NAME and OPPOSITE MEANINGS:

      POSIX   SO_REUSEADDR lets a new listener bind a port still in TIME_WAIT
              from a previous connection. Without it, a DCC port stays
              unusable for a couple of minutes after every transfer, which
              matters here because there are only eleven of them.

      Windows SO_REUSEADDR lets a DIFFERENT PROCESS bind a port this one is
              already listening on, and it may then receive the connection.
              On a DCC listener that is a hijack: the leecher's client
              connects and gets somebody else's socket. The Windows option
              with the POSIX meaning is SO_EXCLUSIVEADDRUSE, which explicitly
              forbids that.

    dcc.py set SO_REUSEADDR unconditionally, so a Windows build would have
    shipped that hole.
    """
    if IS_WINDOWS:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        return sock

    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return sock


def apply_keepalive(sock, idle=10, interval=2, count=3):
    """Turn on TCP keepalive, with the tuning knobs where they exist.

    SO_KEEPALIVE is portable. The three timing options are Linux-specific, so
    they stay guarded - on Windows the system defaults apply, which are slower
    but still catch a dead link.
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if hasattr(socket, "TCP_KEEPIDLE"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, idle)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, count)
    return sock


# ---------------------------------------------------------------------
# Long paths
# ---------------------------------------------------------------------
def long_path(path):
    """Make a path usable past the Windows 260-character MAX_PATH limit.

    Identity on Linux. On Windows, prefixing an ABSOLUTE path with \\\\?\\ opts
    into the extended-length API.

    This is not theoretical for a music library:

        Artist\\Album Name (Year)\\CD2\\12 - A Long Classical Track Title.flac

    nests past 260 characters without trying, and the failure is an
    unhelpful FileNotFoundError on a file that is plainly there.
    """
    if not IS_WINDOWS or not path:
        return path

    text = str(path)
    if text.startswith("\\\\?\\"):
        return text

    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):
        # UNC share: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute


def describe():
    """One line for the startup log, so the platform in use is never a guess."""
    rar = rar_command(getattr(sys.modules.get("config"), "RAR_BINARY", None))
    return (
        f"platform={'windows' if IS_WINDOWS else 'posix'} "
        f"python={sys.version_info.major}.{sys.version_info.minor} "
        f"rar={rar or 'NOT FOUND'}"
    )
