#!/usr/bin/env python3
# bed/main.py
# BED - BBS Engine Daemon
# WebSocket server that loads a router module dynamically

import argparse
import asyncio
import importlib
import signal
import sys
from typing import Any, Optional, Type

from bbsengine6 import io
from bbsengine6.database import getpool
from bbsengine6.net import WebSocketServer

from . import config, lib


class BED:
    """BBS Engine Daemon - WebSocket server with dynamic router loading."""

    def __init__(self, args: argparse.Namespace, MessageRouterClass: Optional[Type] = None):
        self.args = args
        self.MessageRouterClass = MessageRouterClass
        self.server: Optional[WebSocketServer] = None
        self.router: Any = None
        self._running = False

    async def start(self) -> None:
        """Start the daemon."""
        self.server = WebSocketServer(
            host=self.args.host,
            port=self.args.port,
        )

        db_args = argparse.Namespace()
        db_args.databasename = self.args.databasename
        db_args.databasehost = self.args.databasehost
        db_args.databaseport = self.args.databaseport
        db_args.databaseuser = self.args.databaseuser
        db_args.databasepassword = self.args.databasepassword
        db_args.debug = getattr(self.args, "debug", False)

        try:
            db_args.pool = getpool(db_args)
            with db_args.pool.connection() as conn:
                pass
        except Exception as e:
            io.echo(f"Database connection failed: {e}", level="error")
            io.echo("Please ensure PostgreSQL is running with correct credentials", level="error")
            return

        if self.MessageRouterClass is not None:
            self.router = self.MessageRouterClass(db_args)
            self.router.register_all(self.server)

        await self.server.start()
        self._running = True

        io.echo(f"BED started on {self.args.host}:{self.args.port}", level="info")
        if self.MessageRouterClass:
            io.echo(f"Router: {self.MessageRouterClass.__module__}.{self.MessageRouterClass.__name__}", level="info")
        io.echo(f"Registered services: {self.server.list_services()}", level="info")

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            io.echo("BED cancelled", level="info")

    async def stop(self) -> None:
        """Stop the daemon."""
        self._running = False
        if self.server:
            await self.server.stop()
        io.echo("BED stopped", level="info")

    async def restart(self) -> None:
        """Restart the daemon."""
        await self.stop()
        await self.start()


def buildargs(parentparser: argparse.ArgumentParser) -> None:
    """Add BED arguments to parent parser."""
    return lib.buildargs(parentparser)


def get_autorestart_config(args: argparse.Namespace) -> tuple[bool, int, int]:
    """Get autorestart config from args or bed.json defaults."""
    bed_config = config.load_config().get("bed", {})

    if args.no_autorestart:
        autorestart = False
    elif args.autorestart is not None:
        autorestart = args.autorestart
    else:
        autorestart = bed_config.get("autorestart", True)

    restart_delay = args.restart_delay if args.restart_delay is not None else bed_config.get("restart_delay", 5)
    max_restarts = args.max_restarts if args.max_restarts is not None else bed_config.get("max_restarts", 10)

    return autorestart, restart_delay, max_restarts


def load_router_class(router_path: str) -> Type:
    """Load a router class from a module path."""
    if router_path == "default" or router_path == "bbsengine6.net.defaultrouter.DefaultRouter":
        from bbsengine6.net.defaultrouter import DefaultRouter
        return DefaultRouter

    module_path, class_name = router_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


async def main_async() -> None:
    """Async main entry point."""
    parser = argparse.ArgumentParser(description="BED - BBS Engine Daemon")
    buildargs(parser)
    args = parser.parse_args()

    autorestart, restart_delay, max_restarts = get_autorestart_config(args)

    try:
        router_class = load_router_class(args.router)
    except Exception as e:
        io.echo(f"Failed to load router class '{args.router}': {e}", level="error")
        sys.exit(1)

    restart_count = 0
    loop = asyncio.get_event_loop()
    bed = None

    def signal_handler() -> None:
        io.echo("Received shutdown signal", level="info")
        if bed:
            asyncio.create_task(bed.stop())

    def sighup_handler() -> None:
        io.echo("Received SIGHUP, reloading config", level="info")
        new_config = config.reload_config()
        io.echo(f"Config reloaded: {new_config}", level="info")

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        loop.add_signal_handler(signal.SIGHUP, sighup_handler)
    except (NotImplementedError, OSError):
        pass

    while True:
        bed = BED(args, router_class)

        try:
            await bed.start()
            restart_count = 0
        except Exception as e:
            io.echo_traceback(f"BED error: {e}")

            if autorestart:
                restart_count += 1
                if restart_count > max_restarts:
                    io.echo(f"Max restarts ({max_restarts}) reached, giving up", level="error")
                    await bed.stop()
                    break

                io.echo(f"Auto-restarting in {restart_delay}s (attempt {restart_count}/{max_restarts})", level="warning")
                await bed.stop()
                await asyncio.sleep(restart_delay)
                continue
            else:
                await bed.stop()
                raise

        if not autorestart:
            break


def main() -> None:
    """Main entry point."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main_async())
    except Exception as e:
        io.echo_traceback(f"BED fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
