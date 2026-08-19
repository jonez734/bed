import argparse
import os
from typing import Tuple

from bbsengine6.common import safe_path
from bbsengine6.database import buildargs as databasebuildargs


DEFAULT_BED_NAME = "bed"


def _default_secret_path(name: str = DEFAULT_BED_NAME) -> str:
    """Default value for --bed-secret: ``~/.config/bed/<name>.secret``.

    With the default name ``"bed"`` this resolves to the historical
    ``~/.config/bed/bed.secret`` path, so existing installations are
    unaffected. Custom names (``--bed-name mybbs`` or
    ``bed.name = "mybbs"`` in bed.json) yield ``~/.config/bed/mybbs.secret``,
    letting multiple bed instances on one host keep their secrets side by
    side. The ``~`` is expanded at parse time via ``os.path.expanduser``.
    """
    return os.path.expanduser(f"~/.config/bed/{name}.secret")


def _config_path_type(value: str) -> str:
    """argparse type= for --config: expand ~ and $VAR via safe_path,
    with symlinks NOT resolved so the textual form is preserved."""
    return safe_path(value, resolve_symlinks=False)


def _bind_spec(value: str) -> Tuple[str, int]:
    """argparse type= for ``--bind HOST:PORT``.

    Returns ``(host, port)``. Host may be a literal IPv4 / IPv6
    address, a hostname, or ``localhost`` (which resolves to both A
    and AAAA records at bind time). Port must be an integer in
    ``[1, 65535]``. Bare IPv6 addresses (``::1``) need to be wrapped
    in brackets to keep ``:`` unambiguous: ``--bind '[::1]:8765'``.

    Raises ``argparse.ArgumentTypeError`` with the offending value
    so the operator sees which --bind entry failed to parse.
    """
    if not value or ":" not in value:
        raise argparse.ArgumentTypeError(
            f"--bind expects HOST:PORT, got: {value!r}"
        )
    # Bracketed IPv6 literal: '[::1]:8765'
    if value.startswith("["):
        end = value.find("]")
        if end == -1 or end + 1 >= len(value) or value[end + 1] != ":":
            raise argparse.ArgumentTypeError(
                f"--bind expects HOST:PORT, got: {value!r} "
                "(missing ']' or ':' after bracket)"
            )
        host_str = value[1:end]
        port_str = value[end + 2:]
    else:
        host_str, _, port_str = value.rpartition(":")
        if not host_str or not port_str:
            raise argparse.ArgumentTypeError(
                f"--bind expects HOST:PORT, got: {value!r}"
            )
    try:
        port_int = int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--bind port must be an integer, got: {port_str!r} "
            f"in {value!r}"
        )
    if not (1 <= port_int <= 65535):
        raise argparse.ArgumentTypeError(
            f"--bind port out of range [1, 65535], got: {port_int}"
        )
    return (host_str, port_int)


def buildargs(parentparser: argparse.ArgumentParser) -> None:
    """Add BED arguments to parent parser."""
    databasebuildargs(parentparser)
    parentparser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Host to bind to (sugar for a single --bind HOST:PORT). "
            "Ignored when --bind is given at least once. "
            "(default: 127.0.0.1)"
        ),
    )
    parentparser.add_argument(
        "--port",
        type=int,
        default=8765,
        help=(
            "Port to listen on (sugar for a single --bind HOST:PORT). "
            "Ignored when --bind is given at least once. "
            "(default: 8765)"
        ),
    )
    parentparser.add_argument(
        "--bind",
        action="append",
        type=_bind_spec,
        default=None,
        metavar="HOST:PORT",
        help=(
            "Add one (host, port) bind. Repeatable; one listener "
            "socket per --bind entry, plus one socket per address "
            "family when a host name resolves to both A and AAAA "
            "(e.g. --bind localhost:8765 yields both 127.0.0.1 and "
            "::1 listeners). Bracketed IPv6 literals use "
            "--bind '[::1]:8765'. When --bind is given, --host and "
            "--port are ignored."
        ),
    )
    parentparser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parentparser.add_argument(
        "--foreground",
        "-f",
        action="store_true",
        help="Run in foreground (don't daemonize)",
    )
    parentparser.add_argument(
        "--pidfile",
        help="Path to PID file",
    )
    parentparser.add_argument(
        "--bed-name",
        dest="bed_name",
        default=DEFAULT_BED_NAME,
        help=(
            "Logical name for this bed instance. Used to derive the "
            "default --bed-secret path (``~/.config/bed/<name>.secret``) "
            "and surfaced by the ``ping`` reply alongside the version. "
            "Pick a per-instance name when running multiple bed daemons "
            "on the same host so their secret files do not collide. "
            "Empty string is treated as the default ``bed``. "
            "(default: bed)"
        ),
    )
    parentparser.add_argument(
        "--router",
        default="bbsengine6.net.defaultrouter.DefaultRouter",
        help=(
            "Module path to MessageRouter class. Built-in example: "
            "'bbsengine6.net.defaultrouter.DefaultRouter' (no-credential stub "
            "for development and wscat smoke tests). "
            "Any router other than DefaultRouter will additionally be wired "
            "to bed's AuthService (bearer tokens, reconnect, refresh, revoke)."
        ),
    )
    parentparser.add_argument(
        "--bed-secret",
        dest="bed_secret",
        default=None,
        help=(
            "Path to the HMAC secret + per-instance UUID used to sign "
            "bearer tokens. Auto-created mode 0600 on first run. "
            "bed refuses to start if the file is world/group readable. "
            "When omitted, the path is derived from --bed-name as "
            "~/.config/bed/<name>.secret (so the default name 'bed' "
            "yields ~/.config/bed/bed.secret, preserving existing installs)."
        ),
    )
    parentparser.add_argument(
        "--token-ttl",
        dest="token_ttl",
        type=int,
        default=900,
        help=(
            "Bearer-token time-to-live in seconds. Default 900 (15 minutes), "
            "matches bbsengine6.session.updatelastactivity's idle window. "
            "Only consulted by AuthService; the DefaultRouter stub is unaffected."
        ),
    )
    parentparser.add_argument(
        "--token-persistence",
        dest="token_persistence",
        choices=("none", "memory", "db"),
        default="memory",
        help=(
            "Where issued tokens are stored. 'memory' (default) is the "
            "v1 in-process Dict[token, TokenRecord]; tokens are lost on "
            "bed restart. 'db' persists to engine.__bed_token (schema in "
            "bed/data/sql/bed_token.sql). 'none' disables AuthService "
            "entirely (same as using DefaultRouter)."
        ),
    )
    parentparser.add_argument(
        "--credential-provider",
        dest="credential_provider",
        choices=("password", "moniker-only"),
        default="password",
        help=(
            "How AuthService validates login credentials. 'password' "
            "(default) calls bbsengine6.member.checkpassword. "
            "'moniker-only' accepts any password once the moniker resolves."
        ),
    )
    parentparser.add_argument(
        "--bed-instance-id",
        dest="bed_instance_id",
        default=None,
        help=(
            "Override the per-bed UUID baked into issued tokens. By default "
            "a UUIDv4 is generated on first run and persisted next to the "
            "secret. Use this to share an instance id across bed processes "
            "behind a load balancer (out of scope for v1)."
        ),
    )
    parentparser.add_argument(
        "--autorestart",
        action="store_true",
        default=None,
        help="Enable auto-restart on crash (default: off)",
    )
    parentparser.add_argument(
        "--restart-delay",
        type=int,
        default=None,
        help="Seconds to wait before restarting (default: from bed.json, or 5)",
    )
    parentparser.add_argument(
        "--max-restarts",
        type=int,
        default=None,
        help="Max consecutive restarts before giving up (default: from bed.json, or 10)",
    )
    parentparser.add_argument(
        "--restart-on-bind-failure",
        action="store_true",
        default=None,
        help="Retry when the listening port is already in use or bind is "
        "denied (EADDRINUSE/EACCES). Default: off — bed exits 2 so "
        "systemd does not loop. Honors restart_delay / max_restarts.",
    )
    parentparser.add_argument(
        "--config",
        dest="config_file",
        required=True,
        type=_config_path_type,
        help="Path to a JSON config file (required). Supports ~ and $VAR. "
        "No fallback search is performed — the path must be provided explicitly.",
    )
    parentparser.add_argument(
        "--no-message-service",
        dest="no_message_service",
        action="store_true",
        default=False,
        help="Disable the in-process MessageService (PG LISTEN/NOTIFY "
        "fanout to connected WebSocket clients).",
    )
    parentparser.add_argument(
        "--no-bank-service",
        dest="no_bank_service",
        action="store_true",
        default=False,
        help="Disable the in-process BankService (bed-native handler for "
        "bank_balance / bank_add / bank_remove / bank_history).",
    )
