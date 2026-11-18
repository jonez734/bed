"""Store a token for a front end: the one public way to write a token file (owner-only, mode 0600).

Only a front end calls this, and only after a successful login (bbsengine6 section 7-auth). It reuses bed's own
atomic writer, so the file's mode and the directory's permissions follow the same rules as ``bed auth login``.
"""

from __future__ import annotations

from bed.tools.auth import _write_token_file


def store_token(path: str, token: str) -> None:
    """Write ``token`` to ``path`` atomically, owner-only (0600). Raises on failure; nothing is written on a refusal."""
    _write_token_file(path, token)
