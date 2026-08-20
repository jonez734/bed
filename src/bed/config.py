import errno
import json
import os
import socket
from pathlib import Path
from typing import Any, Dict, Optional

from bbsengine6 import io
from bbsengine6.common import safe_path

_PATH_KEY_SUFFIXES = ("_path", "_file", "_dir", "_socket", "_log")

# Errnos that look like transient FS / network conditions. When the
# explicit config path raises one of these (or a socket DNS failure /
# permission denied), ``load_config``'s behavior depends on the
# ``autorestart`` argument (see :func:`load_config`). Operator-error
# errnos (``ENOENT``, ``EISDIR``, ``ENOTDIR``, ``ELOOP``,
# ``ENAMETOOLONG``, ``EMFILE``, ``ENOSPC``) and JSON syntax errors
# are NOT recoverable here -- they always propagate so silent
# fallback never masks a real config bug.
_RECOVERABLE_ERRNOS = frozenset({
    errno.EACCES,       # 13  permission denied (also caught as PermissionError)
    errno.EIO,          # 5   disk / storage I/O error
    errno.ESTALE,       # 116 stale NFS file handle
    errno.ETXTBSY,      # 26  file open for writing by another process
    errno.ENETUNREACH,  # 101 network unreachable (network mount)
    errno.EHOSTUNREACH, # 113 host unreachable (network mount)
    errno.ECONNREFUSED, # 111 connection refused (network mount)
    errno.ETIMEDOUT,    # 110 connection timed out (network mount)
})


def _is_recoverable_load_error(exc: BaseException) -> bool:
    """True when ``exc`` looks like a transient FS / network condition
    that the operator's ``autorestart`` policy should govern.

    DNS failures (any ``socket.gaierror``), DNS / network timeouts
    (``socket.timeout``), and permission denied
    (``PermissionError`` / ``OSError`` with ``errno == EACCES``) are
    always recoverable. Other ``OSError`` instances are recoverable
    only when their ``errno`` is in :data:`_RECOVERABLE_ERRNOS`.
    """
    if isinstance(exc, (socket.gaierror, socket.timeout, PermissionError)):
        return True
    if isinstance(exc, OSError):
        return getattr(exc, "errno", None) in _RECOVERABLE_ERRNOS
    return False


def _peek_autorestart(config_file: str) -> Optional[bool]:
    """Read just ``bed.autorestart`` from the config file without
    performing path expansion, env merge, or override merge.

    Returns ``False`` when the key is missing or not a boolean
    (matches the established ``get_restart_config`` default in
    ``bed.main``), so the caller's fail-safe posture is the same as
    the runtime restart policy. Returns ``None`` only when the file
    can't be opened or parsed at all, so the caller can distinguish
    "file unreachable" from "key absent or mis-typed".
    """
    try:
        with open(config_file) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    bed_cfg = data.get("bed", {})
    if not isinstance(bed_cfg, dict):
        return None
    val = bed_cfg.get("autorestart")
    if isinstance(val, bool):
        return val
    return False


class ConfigIORecoverableError(Exception):
    """Raised by :func:`load_config` when the explicit config file
    failed with a transient FS / network error AND the caller said
    ``autorestart=False``.

    Callers should emit a ``level="error"`` message (already done by
    :func:`load_config` before raising) and exit with a distinct,
    systemd-blocked status code (currently ``3``) so the operator
    notices the failure rather than having the daemon silently use
    the packaged default.
    """


def get_package_data_path(filename: str) -> Path:
    """Get path to a file in the bed package data directory."""
    return Path(__file__).parent / "data" / filename


def load_config(
    config_file: str,
    env_prefix: str = "BED_",
    *,
    autorestart: bool = False,
    **overrides: Any,
) -> Dict[str, Any]:
    """
    Load bed configuration from an explicit config file path (required).

    Priority order:
    1. Command line / overrides (highest)
    2. Environment variables
    3. Config file (lowest)

    Variable format: BED_<SECTION>_<KEY>=value or BED_KEY=value

    If the explicit ``config_file`` raises a transient FS / network
    error (see :data:`_RECOVERABLE_ERRNOS` plus
    :class:`socket.gaierror`, :class:`socket.timeout`, and
    :class:`PermissionError`):

    - ``autorestart=True``  -> emit ``level="warning"`` naming the
      original failure and the full absolute path of the fallback
      JSON (``bed/data/bed.json``) now in use, then load the
      packaged default and return it.
    - ``autorestart=False`` -> emit ``level="error"`` naming the
      original failure and the full absolute path that failed, then
      raise :class:`ConfigIORecoverableError`. The caller is
      expected to ``sys.exit(3)`` (a systemd-blocked status).

    Operator errors (``FileNotFoundError``, ``IsADirectoryError``,
    ``NotADirectoryError``, ``ELOOP``, ``ENAMETOOLONG``, ``EMFILE``,
    ``ENOSPC``) and JSON syntax errors always propagate so silent
    fallback never masks a real config bug.
    """
    try:
        with open(config_file) as f:
            config = json.load(f)
    except BaseException as e:
        if not _is_recoverable_load_error(e):
            raise
        if autorestart:
            fallback_path = str(get_package_data_path("bed.json"))
            io.echo(
                f"Loading config from {config_file} failed with "
                f"{type(e).__name__}: {e}; "
                f"falling back to default JSON at {fallback_path}",
                level="warning",
            )
            with open(fallback_path) as f:
                config = json.load(f)
        else:
            io.echo(
                f"Loading config from {config_file} failed with "
                f"{type(e).__name__}: {e}; "
                f"autorestart is off, refusing to fall back to "
                f"packaged default",
                level="error",
            )
            raise ConfigIORecoverableError(str(e)) from e
    io.echo(f"bed.json config path: {config_file}")
    config = _expand_paths(config)

    env_config = _load_from_env(env_prefix)
    env_config = _expand_paths(env_config)
    config = _merge_config(config, env_config)

    config = _expand_paths(config)
    config = _merge_config(config, overrides)

    return config


def _merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge override into base config."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value
    return result


def _load_from_env(prefix: str) -> Dict[str, Any]:
    """
    Load configuration from environment variables.

    Variable format: BED_<SECTION>_<KEY>=value or BED_KEY=value
    Example: BED_DEBUG=true, BED_BED_AUTORESTART=true
    """
    config = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue

        config_key = key[len(prefix):]

        if "_" in config_key:
            parts = config_key.split("_", 1)
            section = parts[0].lower()
            key_name = parts[1].lower()

            if section not in config:
                config[section] = {}

            if value.lower() in ("true", "false"):
                config[section][key_name] = value.lower() == "true"
            elif value.isdigit():
                config[section][key_name] = int(value)
            else:
                config[section][key_name] = value
        else:
            key_name = config_key.lower()
            if value.lower() in ("true", "false"):
                config[key_name] = value.lower() == "true"
            elif value.isdigit():
                config[key_name] = int(value)
            else:
                config[key_name] = value

    return config


def _expand_paths(value: Any, *, key: str | None = None) -> Any:
    """Recursively expand path-shaped values via bbsengine6.common.safe_path,
    which handles ``~`` and ``$VAR`` plus normalization/abspath, with symlinks
    NOT resolved so the textual form is preserved.

    Non-path keys (module names like ``bed.api.message``, hostnames like
    ``127.0.0.1``, mode strings like ``memory``) and all non-string values
    pass through unchanged.
    """
    if isinstance(value, str):
        if key is not None and any(key.endswith(s) for s in _PATH_KEY_SUFFIXES):
            return safe_path(value, resolve_symlinks=False)
        return value
    if isinstance(value, dict):
        return {k: _expand_paths(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_paths(v, key=key) for v in value]
    return value
