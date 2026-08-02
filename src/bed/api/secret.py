# bed/api/secret.py
# HMAC secret + per-instance UUID for bed's bearer-token system.

from __future__ import annotations

import json
import os
import secrets
import stat
import tempfile
import uuid
from typing import Tuple


SECRET_BYTES = 32
SECRET_HEX_LEN = SECRET_BYTES * 2

CURRENT_VERSION = 2
VERSION_KEY = "__bed_secret_version"
HMAC_KEY = "hmac"
INSTANCE_KEY = "instance_id"


class InsecureSecretError(RuntimeError):
    """Raised when the secret file's permissions are not 0600 (or stricter)."""


class SecretFormatError(RuntimeError):
    """Raised when the secret file exists but cannot be parsed."""


def _is_world_or_group_readable(mode: int) -> bool:
    return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


def _write_secret_file(path: str, payload: dict) -> None:
    """Atomically write ``payload`` as JSON to ``path`` with mode 0600.

    Uses ``tempfile.mkstemp`` in the same directory + ``os.replace`` to
    avoid the rename-across-filesystems gotcha, then explicitly
    ``os.fchmod`` on the open fd *before* close so the umask cannot leak
    permissions on any platform.  On any error the temp file is removed
    and the destination is left untouched — the upgrade path cannot
    destroy an existing good secret.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".bed-secret-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    os.replace(tmp_path, path)
    os.chmod(path, 0o600)


def _read_secret_file(path: str) -> Tuple[bytes, str]:
    """Read the secret file and return (hmac_secret_bytes, instance_id).

    Handles v1 (raw 32-byte binary) and v2 (JSON with version=2). Refuses
    to read any file that is world- or group-readable.
    """
    st = os.stat(path)
    if _is_world_or_group_readable(st.st_mode):
        raise InsecureSecretError(
            f"bed secret file {path!r} is world/group accessible "
            f"(mode=0o{stat.S_IMODE(st.st_mode):o}); refusing to start. "
            f"Run `chmod 600 {path}`."
        )

    with open(path, "rb") as f:
        raw = f.read()

    stripped = raw.strip()
    if stripped.startswith(b"{"):
        try:
            payload = json.loads(stripped.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise SecretFormatError(
                f"bed secret file {path!r} is JSON but unparseable: {e}"
            ) from e
        version = int(payload.get(VERSION_KEY, 0))
        if version != CURRENT_VERSION:
            raise SecretFormatError(
                f"bed secret file {path!r} has unknown version {version!r}"
            )
        hmac_hex = payload.get(HMAC_KEY, "")
        instance_id = payload.get(INSTANCE_KEY, "")
        if (
            not isinstance(hmac_hex, str)
            or len(hmac_hex) != SECRET_HEX_LEN
            or not all(c in "0123456789abcdef" for c in hmac_hex)
        ):
            raise SecretFormatError(
                f"bed secret file {path!r} has malformed hmac field"
            )
        if not isinstance(instance_id, str) or not instance_id:
            raise SecretFormatError(
                f"bed secret file {path!r} has malformed instance_id"
            )
        return bytes.fromhex(hmac_hex), instance_id

    if len(stripped) != SECRET_BYTES:
        raise SecretFormatError(
            f"bed secret file {path!r} has unexpected length {len(stripped)} "
            f"(expected {SECRET_BYTES} raw bytes for v1)"
        )
    hmac_bytes = bytes(stripped)
    instance_id = str(uuid.UUID(int=secrets.randbits(128)))
    payload = {
        VERSION_KEY: CURRENT_VERSION,
        HMAC_KEY: hmac_bytes.hex(),
        INSTANCE_KEY: instance_id,
    }
    try:
        _write_secret_file(path, payload)
    except Exception as e:
        from bbsengine6 import io

        io.echo(
            f"bed secret: v1->v2 upgrade of {path!r} failed; "
            f"leaving original intact ({e})",
            level="warning",
        )
        return hmac_bytes, instance_id
    from bbsengine6 import io

    io.echo(
        f"bed secret: v1->v2 upgrade of {path!r} completed; "
        f"new instance_id={instance_id[:8]}… "
        f"(previously had no instance_id, all existing tokens invalidated)",
        level="warning",
    )
    return hmac_bytes, instance_id


def load_or_create_secret(
    path: str,
    *,
    explicit_instance_id: str | None = None,
) -> Tuple[bytes, str]:
    """Return (hmac_secret_bytes, instance_id), creating the file if missing.

    The file is mode 0600, owned by the current user. On first run, a fresh
    HMAC secret and a fresh UUIDv4 are generated. The `explicit_instance_id`
    override (from `--bed-instance-id`) replaces whatever would be read or
    generated, and is *not* persisted back to disk (it's a one-off override
    for tests / multi-instance hosts).
    """
    if os.path.isfile(path):
        hmac_bytes, persisted_id = _read_secret_file(path)
        if explicit_instance_id is not None:
            return hmac_bytes, explicit_instance_id
        return hmac_bytes, persisted_id

    directory = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(directory):
        os.makedirs(directory, mode=0o700, exist_ok=True)

    hmac_bytes = secrets.token_bytes(SECRET_BYTES)
    instance_id = (
        explicit_instance_id
        if explicit_instance_id is not None
        else str(uuid.UUID(int=secrets.randbits(128)))
    )
    payload = {
        VERSION_KEY: CURRENT_VERSION,
        HMAC_KEY: hmac_bytes.hex(),
        INSTANCE_KEY: instance_id,
    }
    _write_secret_file(path, payload)
    return hmac_bytes, instance_id
