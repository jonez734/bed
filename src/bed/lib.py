import argparse
import os

from bbsengine6.database import buildargs as databasebuildargs

from . import config as _bed_config


def _default_secret_path() -> str:
    """Default value for --bed-secret: ~/.config/bed/bed.secret, with ~
    expanded by the shell at argparse-parse time (we keep the literal
    default here and let os.path.expanduser handle it on use)."""
    return os.path.expanduser("~/.config/bed/bed.secret")


def _default_config_path() -> str:
    """Default value for --config: the bed.json shipped in the bed package's
    data/ subdirectory. Returned as a string so argparse can use it as a
    default without re-resolving at every parse."""
    return str(_bed_config.get_package_data_path("bed.json"))


def buildargs(parentparser: argparse.ArgumentParser) -> None:
    """Add BED arguments to parent parser."""
    databasebuildargs(parentparser)
    parentparser.add_argument(
        "--host",
        default="localhost",
        help="Host to bind to (default: localhost)",
    )
    parentparser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to listen on (default: 8765)",
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
        "--router",
        default="bbsengine6.net.defaultrouter.DefaultRouter",
        help=(
            "Module path to MessageRouter class. Built-in examples: "
            "'bbsengine6.net.defaultrouter.DefaultRouter' (no-credential stub "
            "for development and wscat smoke tests) and "
            "'zoid6.api.handler.MonikerAuthRouter' (verifies the moniker "
            "exists in the database; password still not checked). "
            "Any router other than DefaultRouter will additionally be wired "
            "to bed's AuthService (bearer tokens, reconnect, refresh, revoke)."
        ),
    )
    parentparser.add_argument(
        "--bed-secret",
        dest="bed_secret",
        default=_default_secret_path(),
        help=(
            "Path to the HMAC secret + per-instance UUID used to sign "
            "bearer tokens. Auto-created mode 0600 on first run. "
            "bed refuses to start if the file is world/group readable. "
            "(default: ~/.config/bed/bed.secret)"
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
            "'moniker-only' mirrors zoid6.api.handler.MonikerAuthRouter "
            "and accepts any password once the moniker resolves."
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
        help="Enable auto-restart on crash (default: from bed.json, or False)",
    )
    parentparser.add_argument(
        "--no-autorestart",
        action="store_true",
        default=False,
        help="Disable auto-restart on crash (default: off)",
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
        "--config",
        dest="config_file",
        default=_default_config_path(),
        help="Path to a JSON config file. Defaults to the bed.json shipped in "
        "bed's package data directory. Overrides packaged defaults for "
        "bed.*, bind.*, and database.*. CLI flags for --host, --port, and "
        "database args retain highest priority when explicitly set. No "
        "path expansion is performed.",
    )
