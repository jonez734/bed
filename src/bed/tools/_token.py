"""Shared token-file plumbing for ``bed.tools.*`` CLI scripts.

Read-side helpers (path resolution, permission checks, file reading,
argparse registration of ``--token-file``) live here so multiple
tools (``auth``, ``bank``, ``message``, ...) can reuse them. The
write-side helpers (``_write_token_file`` / ``_truncate_token_file``)
stay in :mod:`bed.tools.auth` because they are auth-flow-specific
(``login`` / ``reconnect`` / ``refresh`` / ``revoke`` semantics).

Default token path: ``$XDG_RUNTIME_DIR/bed.token`` when set and
writable, else ``/tmp/bed-<uid>/bed.token``. The parent directory is
created mode 0700 on demand so the file can be created mode 0600 by
the auth tool without leaking the token through a world-readable
parent.
"""

from __future__ import annotations

import argparse
import os
import stat
import tempfile
from typing import Any


def build_token_file_arg(parentparser: argparse.ArgumentParser) -> None:
    """Register the ``--token-file`` flag on ``parentparser``.

    The flag is optional (``default=None``). Callers that want a
    concrete path should follow up with
    :func:`ensure_token_file_arg` to fill the default in-place.
    """
    parentparser.add_argument(
        "--token-file",
        dest="token_file",
        default=None,
        help=(
            "Path to the bearer-token file. Defaults to "
            "$XDG_RUNTIME_DIR/bed.token (or /tmp/bed-<uid>/bed.token "
            "if XDG_RUNTIME_DIR is unset). Tools that read the token "
            "use it to authenticate the bed WebSocket before sending "
            "any service requests."
        ),
    )


def default_token_path() -> str:
    """Return the default token-file path.

    Honours ``$XDG_RUNTIME_DIR`` (per-session scoping) when set;
    falls back to ``/tmp/bed-<uid>/bed.token`` so the private
    directory can be created mode 0700 owned by the current user.
    The parent directory is created on demand.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        path = os.path.join(runtime, "bed.token")
        _ensure_parent_dir(path, mode=0o700)
        return path
    fallback = os.path.join(
        tempfile.gettempdir(), f"bed-{os.getuid()}", "bed.token"
    )
    _ensure_parent_dir(fallback, mode=0o700)
    return fallback


def _ensure_parent_dir(path: str, *, mode: int) -> None:
    """Create the parent directory of ``path`` with ``mode`` if missing.

    Never raises for an already-correctly-permissioned existing dir;
    raises :class:`PermissionError` if the dir exists but cannot be
    made ``mode`` (so the caller can render a clear error instead of
    a confusing ``OSError`` from a later ``open()``).
    """
    parent = os.path.dirname(path) or "."
    try:
        st = os.stat(parent)
    except FileNotFoundError:
        os.makedirs(parent, mode=mode, exist_ok=True)
        try:
            os.chmod(parent, mode)
        except OSError:
            pass
        return
    if not stat.S_ISDIR(st.st_mode):
        raise PermissionError(f"{parent} is not a directory")
    perms = stat.S_IMODE(st.st_mode)
    if perms & 0o077:
        raise PermissionError(
            f"{parent} has overly-permissive mode {oct(perms)}; "
            f"expected {oct(mode)} or stricter"
        )


def check_token_file_perms(path: str) -> None:
    """Refuse to read/write a token file whose perms are too loose."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode):
        raise PermissionError(f"{path} is not a regular file")
    perms = stat.S_IMODE(st.st_mode)
    if perms & 0o077:
        raise PermissionError(
            f"{path} has overly-permissive mode {oct(perms)}; "
            f"refusing to use"
        )


def read_token_file(path: str) -> str:
    """Return the token stored in ``path`` (``""`` if missing/empty)."""
    try:
        check_token_file_perms(path)
    except FileNotFoundError:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def resolve_token(args: Any) -> str:
    """Return the token to use for reconnect/refresh/revoke/wire calls.

    Precedence: ``--token`` (explicit flag) > ``--token-file`` (read
    from disk) > ``""`` (caller will render a missing_token error).
    """
    tok = getattr(args, "token", None)
    if tok:
        return tok
    path = getattr(args, "token_file", None)
    if path:
        return read_token_file(path)
    return ""


def ensure_token_file_arg(args: Any) -> None:
    """Populate ``args.token_file`` with the default path if absent.

    Done in-place so subcommands can pass ``args`` straight through.
    """
    if getattr(args, "token_file", None) is None:
        args.token_file = default_token_path()
