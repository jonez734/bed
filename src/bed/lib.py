import argparse

from bbsengine6.database import buildargs as databasebuildargs

from . import config as _bed_config


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
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)",
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
        help="Module path to MessageRouter class (default: bbsengine6.net.defaultrouter.DefaultRouter)",
    )
    parentparser.add_argument(
        "--autorestart",
        action="store_true",
        default=None,
        help="Enable auto-restart on crash (default: from bed.json, or True)",
    )
    parentparser.add_argument(
        "--no-autorestart",
        action="store_true",
        default=False,
        help="Disable auto-restart on crash",
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
