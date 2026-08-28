"""bed.config - load and merge bed configuration.

Historically this module owned its own JSON loading, env-var parsing,
deep-merge, and path-expansion helpers. As of 2026 those primitives
have moved to :mod:`bbsengine6.config` so downstream apps (zoidoffice,
asimov, achilles, ...) can share a single precedence chain. This
module is now a thin facade: it owns the bed-specific policy (where
to look for the config file, the recoverable-error semantics, the
``ConfigIORecoverableError`` sentinel) and delegates the generic
machinery to ``bbsengine6.config``.

The public API (:func:`load_config`, :class:`ConfigIORecoverableError`,
:func:`_peek_autorestart`, :func:`get_package_data_path`) is
unchanged so existing callers and tests continue to work.
"""

from __future__ import annotations

import errno
import json
import os
import socket
from pathlib import Path
from typing import Any

from bbsengine6 import config as be6_config
from bbsengine6 import io

# Re-export the path-key suffix set from bbsengine6.config for any
# downstream callers that imported it from here. The set is identical
# (``bed.config`` and ``bbsengine6.config`` agree on the convention).
PATH_KEY_SUFFIXES = be6_config.PATH_KEY_SUFFIXES


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


def _peek_autorestart(config_file: str) -> bool | None:
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
) -> dict[str, Any]:
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

    # Phase 1: expand `${VAR}` and `~` everywhere in the file content
    # (only string leaves; non-string scalars pass through). This
    # matches the original bed.config behavior of expanding paths
    # AND env vars.
    config = be6_config.expand_value(config, env=os.environ)

    # Phase 2: layer env-var-derived config on top of file config.
    env_config = _load_from_env(env_prefix)
    env_config = be6_config.expand_value(env_config, env=os.environ)
    config = be6_config.deep_merge(config, env_config)

    # Phase 3: path-shaped keys run through safe_path (no symlink
    # resolution). This is the original bed.config behavior; we
    # re-run it after the env merge because env-supplied values may
    # also be path-shaped (see test_load_config_expands_env_supplied_path).
    config = be6_config.expand_paths(config)

    # Phase 4: caller-supplied overrides win last.
    config = be6_config.deep_merge(config, overrides)
    return config


def _merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge override into base config.

    Re-exported for backward compatibility (the original
    ``bed.config`` exposed this as a private helper; nothing in
    ``bed/`` outside this module calls it but downstream tooling
    may patch it in tests).
    """
    return be6_config.deep_merge(base, override)


def _load_from_env(prefix: str) -> dict[str, Any]:
    """
    Load configuration from environment variables.

    Variable format: BED_<SECTION>_<KEY>=value or BED_KEY=value
    Example: BED_DEBUG=true, BED_BED_AUTORESTART=true

    Re-exported for backward compatibility (the original
    ``bed.config`` exposed this as a private helper).
    """
    config: dict[str, Any] = {}
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


__all__ = [
    "PATH_KEY_SUFFIXES",
    "_RECOVERABLE_ERRNOS",
    "ConfigIORecoverableError",
    "_is_recoverable_load_error",
    "_load_from_env",
    "_merge_config",
    "_peek_autorestart",
    "get_package_data_path",
    "load_config",
]