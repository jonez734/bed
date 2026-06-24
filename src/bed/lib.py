import argparse

from bbsengine6.database import buildargs as databasebuildargs


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
        "--foreground", "-f",
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
