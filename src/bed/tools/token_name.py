"""The per-player, per-terminal token file name every front end uses (moved here from casino's round view).

    round-view.<moniker>.<tty-or-notty>.<hash>.token

- moniker: lowercased, then validated (it becomes part of a filename).
- tty: the terminal device with "/" replaced by "_" (pts_4), or "notty" when there is none.
- hash: the first 8 hex characters of SHA-256 over "<moniker>|<tty>", or "<moniker>|pid<PID>" when there is no tty.
  With no tty, the PID keeps two scripts for one player from rotating the same file.

The prefix is "round-view" for the round viewer's files. Other front ends name their files with the same rule and their
own prefix, via ``token_file_name(moniker, prefix=...)``.
"""

from __future__ import annotations

import hashlib
import os

from bed.tools._token import validate_account_name

DEFAULT_PREFIX = "round-view"
SUFFIX = "token"


def _tty_name(fd: int) -> str:
    try:
        device = os.ttyname(fd)
    except OSError:
        return ""
    return device.replace("/dev/", "", 1).replace("/", "_") if device else ""


def token_file_name(moniker: str, *, fd: int = 0, pid: int | None = None, prefix: str = DEFAULT_PREFIX) -> str:
    """The file name (not a path) for ``moniker`` at the terminal on ``fd``. Raises ValueError for an unsafe moniker."""
    name = validate_account_name(moniker.strip().lower())
    tty = _tty_name(fd)
    if tty:
        key = f"{name}|{tty}"
        segment = tty
    else:
        pid = os.getpid() if pid is None else pid
        key = f"{name}|pid{pid}"
        segment = "notty"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}.{name}.{segment}.{digest}.{SUFFIX}"


def token_file_path(directory: str, moniker: str, *, fd: int = 0, pid: int | None = None,
                    prefix: str = DEFAULT_PREFIX) -> str:
    """The full path of ``moniker``'s token file in ``directory``."""
    return os.path.join(directory, token_file_name(moniker, fd=fd, pid=pid, prefix=prefix))
