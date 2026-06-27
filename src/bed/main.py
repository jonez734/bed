#!/usr/bin/env python3
# bed/main.py
# BED - BBS Engine Daemon
# WebSocket server that loads a router module dynamically

import argparse
import asyncio
import importlib
import os
import signal
import sys
from typing import Any, Optional, Type

from bbsengine6 import io
from bbsengine6.database import getpool
from bbsengine6.net import WebSocketServer

from . import config, lib
from .api import (
    AuthService,
    InMemoryTokenStore,
    DBTokenStore,
    SessionRegistry,
    get_provider,
    load_or_create_secret,
)


_BED_DEFAULTS: Optional[dict] = None


def _get_bed_defaults() -> dict:
    """Snapshot of argparse defaults (host, port, database*) used to detect
    whether the user passed those flags explicitly. Computed once."""
    global _BED_DEFAULTS
    if _BED_DEFAULTS is not None:
        return _BED_DEFAULTS

    parser = argparse.ArgumentParser()
    buildargs(parser)
    _BED_DEFAULTS = {
        "host": parser.get_default("host"),
        "port": parser.get_default("port"),
        "databasename": parser.get_default("databasename"),
        "databasehost": parser.get_default("databasehost"),
        "databaseport": parser.get_default("databaseport"),
        "databaseuser": parser.get_default("databaseuser"),
        "databasepassword": parser.get_default("databasepassword"),
        "bed_secret": parser.get_default("bed_secret"),
        "token_ttl": parser.get_default("token_ttl"),
        "token_persistence": parser.get_default("token_persistence"),
        "credential_provider": parser.get_default("credential_provider"),
    }
    return _BED_DEFAULTS


def _apply_bind_config(args: argparse.Namespace, cfg: dict) -> None:
    """Apply bind.host/bind.port from a loaded config when the CLI did not
    explicitly set them (i.e. args still equal argparse defaults)."""
    bind = cfg.get("bind")
    if not isinstance(bind, dict):
        return
    defaults = _get_bed_defaults()
    if "host" in bind and args.host == defaults["host"]:
        args.host = bind["host"]
    if "port" in bind and args.port == defaults["port"]:
        args.port = int(bind["port"])


def _apply_database_config(args: argparse.Namespace, cfg: dict) -> None:
    """Apply database.* from a loaded config when the CLI did not explicitly
    set those flags. The zoid6 bed.json only carries name/host/port; user
    and password stay on the CLI or env."""
    db = cfg.get("database")
    if not isinstance(db, dict):
        return
    defaults = _get_bed_defaults()
    if "name" in db and args.databasename == defaults["databasename"]:
        args.databasename = db["name"]
    if "host" in db and args.databasehost == defaults["databasehost"]:
        args.databasehost = db["host"]
    if "port" in db and args.databaseport == defaults["databaseport"]:
        args.databaseport = int(db["port"])
    if "user" in db and args.databaseuser == defaults["databaseuser"]:
        args.databaseuser = db["user"]
    if "password" in db and args.databasepassword == defaults["databasepassword"]:
        args.databasepassword = db["password"]


def _apply_auth_config(args: argparse.Namespace, cfg: dict) -> None:
    """Apply auth.* from a loaded config when the CLI did not explicitly
    set those flags. Mirrors _apply_bind_config / _apply_database_config."""
    auth = cfg.get("auth")
    if not isinstance(auth, dict):
        return
    defaults = _get_bed_defaults()
    if (
        "bed_secret_path" in auth
        and args.bed_secret == defaults["bed_secret"]
    ):
        args.bed_secret = auth["bed_secret_path"]
    if "token_ttl" in auth and args.token_ttl == defaults["token_ttl"]:
        args.token_ttl = int(auth["token_ttl"])
    if (
        "token_persistence" in auth
        and args.token_persistence == defaults["token_persistence"]
    ):
        args.token_persistence = auth["token_persistence"]
    if (
        "credential_provider" in auth
        and args.credential_provider == defaults["credential_provider"]
    ):
        args.credential_provider = auth["credential_provider"]
    if "bed_instance_id" in auth and args.bed_instance_id is None:
        args.bed_instance_id = auth["bed_instance_id"]


class BED:
    """BBS Engine Daemon - WebSocket server with dynamic router loading."""

    DEFAULT_ROUTER_FQCN = "bbsengine6.net.defaultrouter.DefaultRouter"

    def __init__(
        self, args: argparse.Namespace, MessageRouterClass: Optional[Type] = None
    ):
        self.args = args
        self.MessageRouterClass = MessageRouterClass
        self.server: Optional[WebSocketServer] = None
        self.router: Any = None
        self.auth_service: Optional[AuthService] = None
        self.token_store: Any = None
        self._session_registry: Optional[SessionRegistry] = None
        self._gc_task: Optional[asyncio.Task] = None
        self._running = False

    def _is_default_router(self) -> bool:
        if self.MessageRouterClass is None:
            return True
        fqcn = f"{self.MessageRouterClass.__module__}.{self.MessageRouterClass.__name__}"
        return fqcn == self.DEFAULT_ROUTER_FQCN

    def _auth_enabled(self) -> bool:
        persistence = getattr(self.args, "token_persistence", "memory")
        return persistence != "none" and not self._is_default_router()

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
            io.echo(
                "Please ensure PostgreSQL is running with correct credentials",
                level="error",
            )
            return

        if self._auth_enabled():
            await self._start_auth(db_args)

        if self.MessageRouterClass is not None:
            self.router = self.MessageRouterClass(db_args)
            self.router.register_all(self.server)

        await self.server.start()
        self._running = True

        io.echo(f"BED started on {self.args.host}:{self.args.port}", level="info")
        if self.MessageRouterClass:
            io.echo(
                f"Router: {self.MessageRouterClass.__module__}.{self.MessageRouterClass.__name__}",
                level="info",
            )
        if self.auth_service is not None:
            self._gc_task = asyncio.create_task(self._gc_loop())
        io.echo(f"Registered services: {self.server.list_services()}", level="info")

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            io.echo("BED cancelled", level="info")

    async def _start_auth(self, db_args: argparse.Namespace) -> None:
        """Load secret, build token store + provider, register AuthService."""
        secret_path = getattr(self.args, "bed_secret", None) or os.path.expanduser(
            "~/.config/bed/bed.secret"
        )
        explicit_id = getattr(self.args, "bed_instance_id", None)
        try:
            secret_bytes, instance_id = load_or_create_secret(
                secret_path, explicit_instance_id=explicit_id
            )
        except Exception as e:
            io.echo(
                f"BED refusing to start: cannot load bed secret at "
                f"{secret_path!r}: {e}",
                level="error",
            )
            raise

        persistence = getattr(self.args, "token_persistence", "memory")
        if persistence == "db":
            self.token_store = DBTokenStore(db_args)
        else:
            self.token_store = InMemoryTokenStore()

        provider = get_provider(getattr(self.args, "credential_provider", "password"))
        self._session_registry = SessionRegistry()
        ttl = int(getattr(self.args, "token_ttl", 900) or 900)
        self.auth_service = AuthService(
            args=db_args,
            session_registry=self._session_registry,
            token_store=self.token_store,
            credential_provider=provider,
            secret=secret_bytes,
            instance_id=instance_id,
            ttl_seconds=ttl,
        )
        self.auth_service.register_all(self.server)
        io.echo(
            f"BED AuthService: instance={instance_id[:8]}… "
            f"ttl={ttl}s persistence={persistence} "
            f"provider={getattr(self.args, 'credential_provider', 'password')}",
            level="info",
        )

    async def _gc_loop(self) -> None:
        """Periodic token-store garbage collection. Cancelled by stop()."""
        try:
            while self._running and self.token_store is not None:
                try:
                    self.token_store.gc_expired()
                except Exception as e:
                    io.echo(
                        f"BED token gc error: {e}",
                        level="warning",
                    )
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        """Stop the daemon."""
        self._running = False
        if self._gc_task is not None:
            self._gc_task.cancel()
            try:
                await self._gc_task
            except (asyncio.CancelledError, Exception):
                pass
            self._gc_task = None
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


def get_autorestart_config(
    args: argparse.Namespace,
    cfg: Optional[dict] = None,
) -> tuple[bool, int, int]:
    """Get autorestart config from args, an external config dict (optional),
    or bed.json defaults. Priority: CLI flag > cfg["bed"] > packaged defaults."""
    if cfg is None:
        cfg = config.load_config()
    bed_config = cfg.get("bed", {}) if isinstance(cfg, dict) else {}

    if args.no_autorestart:
        autorestart = False
    elif args.autorestart is not None:
        autorestart = args.autorestart
    else:
        autorestart = bed_config.get("autorestart", True)

    restart_delay = (
        args.restart_delay
        if args.restart_delay is not None
        else bed_config.get("restart_delay", 5)
    )
    max_restarts = (
        args.max_restarts
        if args.max_restarts is not None
        else bed_config.get("max_restarts", 10)
    )

    return autorestart, restart_delay, max_restarts


def load_router_class(router_path: str) -> Type:
    """Load a router class from a module path."""
    if (
        router_path == "default"
        or router_path == "bbsengine6.net.defaultrouter.DefaultRouter"
    ):
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

    loaded_config: Optional[dict] = None
    if args.config_file:
        if not os.path.isfile(args.config_file):
            io.echo(f"Config file not found: {args.config_file}", level="error")
            sys.exit(1)
        try:
            loaded_config = config.load_config(args.config_file)
        except (ValueError, OSError) as e:
            io.echo(
                f"Failed to load config file {args.config_file}: {e}", level="error"
            )
            sys.exit(1)
        packaged_default = str(config.get_package_data_path("bed.json"))
        if os.path.abspath(args.config_file) == os.path.abspath(packaged_default):
            io.echo(f"Using packaged config: {args.config_file}", level="info")
        else:
            io.echo(f"Using config file: {args.config_file}", level="info")
        _apply_bind_config(args, loaded_config)
        _apply_database_config(args, loaded_config)
        _apply_auth_config(args, loaded_config)

    autorestart, restart_delay, max_restarts = get_autorestart_config(
        args, loaded_config
    )

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
                    io.echo(
                        f"Max restarts ({max_restarts}) reached, giving up",
                        level="error",
                    )
                    await bed.stop()
                    break

                io.echo(
                    f"Auto-restarting in {restart_delay}s (attempt {restart_count}/{max_restarts})",
                    level="warning",
                )
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
