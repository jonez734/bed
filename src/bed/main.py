#!/usr/bin/env python3
# bed/main.py
# BED - BBS Engine Daemon
# WebSocket server that loads a router module dynamically

import argparse
import asyncio
import errno
import os
import signal
import socket
import sys
from typing import Any, Dict, List, Optional, Tuple, Type

from bbsengine6 import io
from bbsengine6.common import safe_path
from bbsengine6.database import getpool, parse_dsn, set_current_role
from bbsengine6.module import load as bbs_module_load
from bbsengine6.net import WebSocketServer

from . import config, lib
from ._configpath import resolve_config_path
from .api import (
    AuthService,
    BankService,
    InMemoryTokenStore,
    DBTokenStore,
    MessageService,
    PingService,
    SessionRegistry,
    get_provider,
    load_or_create_secret,
)
from .startup import ensure_startup


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
        "bind": parser.get_default("bind"),
        "databasename": parser.get_default("databasename"),
        "databasehost": parser.get_default("databasehost"),
        "databaseport": parser.get_default("databaseport"),
        "databaseuser": parser.get_default("databaseuser"),
        "databasepassword": parser.get_default("databasepassword"),
        "bed_name": parser.get_default("bed_name"),
        "bed_secret": parser.get_default("bed_secret"),
        "token_ttl": parser.get_default("token_ttl"),
        "token_persistence": parser.get_default("token_persistence"),
        "credential_provider": parser.get_default("credential_provider"),
        "no_message_service": parser.get_default("no_message_service"),
        "no_bank_service": parser.get_default("no_bank_service"),
    }
    return _BED_DEFAULTS


def _expand_user(value):
    """Expand a leading ~ in a path/host string. Pass through non-strings
    unchanged (e.g. None, ints) so a misconfigured JSON entry does not crash
    the config-apply path."""
    if isinstance(value, str):
        return os.path.expanduser(value)
    return value


def _write_pidfile(path: str) -> int:
    """Atomically claim the pidfile. Returns the open fd on success.

    Return values:
        >=0  file descriptor; caller is responsible for closing and
             removing the file on shutdown.
        -1   write/IO failure (warning logged); caller should disable
             cleanup and continue.
        -2   live-pid collision; caller should sys.exit(1).

    Stale-dead pids (the recorded process is gone) are overwritten
    with a warning. POSIX-only: relies on os.kill(pid, 0) for liveness.
    """
    import time

    existing_pid = None
    try:
        with open(path, "r") as f:
            existing_pid = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        pass

    if existing_pid is not None and existing_pid != os.getpid():
        try:
            os.kill(existing_pid, 0)
        except ProcessLookupError:
            io.echo(
                f"Stale pidfile {path} (pid {existing_pid} not running), "
                f"overwriting",
                level="warning",
            )
        except PermissionError:
            io.echo(
                f"Refusing to start: pidfile {path} contains live pid "
                f"{existing_pid}",
                level="error",
            )
            return -2
        else:
            io.echo(
                f"Refusing to start: pidfile {path} contains live pid "
                f"{existing_pid}",
                level="error",
            )
            return -2

    # Fresh start: O_EXCL so a concurrent invocation cannot also claim
    # the file. Stale overwrite: O_TRUNC, since the file is already
    # known to be ours to claim.
    if existing_pid is None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC

    last_error: Optional[OSError] = None
    for attempt in (0, 1):
        try:
            fd = os.open(path, flags, 0o644)
        except FileExistsError as e:
            # Fresh-start case only — stale-overwrite uses O_TRUNC
            # which never raises FileExistsError.
            if existing_pid is not None:
                raise
            last_error = e
            if attempt == 1:
                io.echo(
                    f"Failed to claim pidfile {path}: raced with another "
                    f"start",
                    level="warning",
                )
                return -1
            time.sleep(0.05)
        except OSError as e:
            io.echo(f"Failed to write pidfile {path}: {e}", level="warning")
            return -1
        else:
            break
    else:  # pragma: no cover - loop completed without break
        if last_error is not None:
            io.echo(
                f"Failed to claim pidfile {path}: {last_error}",
                level="warning",
            )
        return -1

    try:
        os.write(fd, f"{os.getpid()}\n".encode())
        return fd
    except OSError as e:
        io.echo(f"Failed to write pidfile {path}: {e}", level="warning")
        try:
            os.close(fd)
            os.unlink(path)
        except OSError:
            pass
        return -1


def _remove_pidfile(path: str) -> None:
    """Remove the pidfile. Idempotent: missing file is not an error."""
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        io.echo(f"Failed to remove pidfile {path}: {e}", level="warning")


def _apply_config_section(
    args: argparse.Namespace,
    cfg: dict,
    section_name: str,
    fields: dict,
) -> None:
    """Generic config-apply helper.

    For each ``(cli_arg, (cfg_key, coerce, expand_user))`` in ``fields``,
    if ``cfg[section_name][cfg_key]`` exists AND ``args.<cli_arg>`` still
    equals its argparse default, set ``args.<cli_arg>`` from the config.

    Args:
        args: argparse namespace to mutate.
        cfg: full loaded config dict.
        section_name: top-level key in ``cfg`` (e.g. ``"bind"``).
        fields: mapping of ``cli_arg -> (config_key, coerce_fn, expand_user_bool)``.
            ``coerce_fn`` may be ``None`` for no coercion; ``expand_user_bool``
            routes the value through ``_expand_user`` (used for paths/hosts).

    Returns nothing; mutates ``args`` in place.
    """
    sect = cfg.get(section_name)
    if not isinstance(sect, dict):
        return
    defaults = _get_bed_defaults()
    for cli_arg, (cfg_key, coerce, expand_user) in fields.items():
        if cfg_key not in sect:
            continue
        if getattr(args, cli_arg, None) != defaults.get(cli_arg):
            continue
        val = sect[cfg_key]
        if expand_user:
            val = _expand_user(val)
        if coerce is not None:
            val = coerce(val)
        setattr(args, cli_arg, val)


def _diff_config_section(
    args: argparse.Namespace,
    cfg: dict,
    section_name: str,
    fields: dict,
) -> list:
    """SIGHUP-time diff helper.

    For each ``(cli_arg, (cfg_key, coerce, expand_user))`` in ``fields``,
    return ``(cli_arg, new_value)`` when the freshly-loaded config value
    (after ``_expand_user`` and ``coerce``) differs from
    ``args.<cli_arg>``.

    Unlike :func:`_apply_config_section`, this does NOT skip when the
    CLI passed an explicit value — SIGHUP is operator-initiated reload,
    so every config change is a candidate for application or warning.
    """
    sect = cfg.get(section_name)
    if not isinstance(sect, dict):
        return []
    diffs = []
    for cli_arg, (cfg_key, coerce, expand_user) in fields.items():
        if cfg_key not in sect:
            continue
        val = sect[cfg_key]
        if expand_user:
            val = _expand_user(val)
        if coerce is not None:
            val = coerce(val)
        if val != getattr(args, cli_arg, None):
            diffs.append((cli_arg, val))
    return diffs


BIND_FIELDS = {
    "host": ("host", str, True),
    "port": ("port", int, False),
}


def _apply_bind_list_config(args: argparse.Namespace, cfg: dict) -> None:
    """Apply a ``bind`` list (multi-bind) from a loaded config when the
    CLI did not pass ``--bind``.

    Accepts two shapes:

    * List: ``"bind": [{"host": "...", "port": ...}, ...]`` (the
      canonical form for multi-bind).
    * Dict: ``"bind": {"host": "...", "port": ...}`` (legacy single-bind;
      mirrors what ``_apply_bind_config`` reads under the hood).

    The list form takes precedence when both are present (the dict
    form's ``host``/``port`` keys would otherwise be applied first by
    ``_apply_bind_config``). CLI takes precedence over both, so we
    only set ``args.binds`` when the operator did not pass ``--bind``
    on the command line.

    A bad entry (missing ``host``, missing ``port``, non-integer
    ``port``) is logged and skipped so a single bad row in a JSON
    config does not prevent the daemon from starting — the operator
    still gets a clear ERROR log line.
    """
    if getattr(args, "bind", None):
        # Operator already specified --bind on the command line. Honor
        # CLI over config without complaint.
        return

    bind_section = cfg.get("bind")
    entries: List[Dict[str, Any]] = []
    if isinstance(bind_section, list):
        entries = [e for e in bind_section if isinstance(e, dict)]
    elif isinstance(bind_section, dict):
        # Legacy single-bind dict. If host/port keys are present and
        # the operator did not set them via CLI, treat as a 1-element
        # list. The legacy ``_apply_bind_config`` already handled the
        # host/port pair; we just need to mirror it into args.binds so
        # the multi-bind code path is the single source of truth.
        if "host" in bind_section or "port" in bind_section:
            entries = [bind_section]
    if not entries:
        return

    parsed: List[Tuple[str, int]] = []
    for idx, entry in enumerate(entries):
        host_val = entry.get("host")
        port_val = entry.get("port")
        if not isinstance(host_val, str) or not host_val:
            io.echo(
                f"bind[{idx}]: missing or non-string 'host' "
                f"(got: {host_val!r}); skipping",
                level="error",
            )
            continue
        if isinstance(port_val, int) and not isinstance(port_val, bool):
            port_int = port_val
        else:
            try:
                port_int = int(port_val)
            except (TypeError, ValueError):
                io.echo(
                    f"bind[{idx}] ({host_val!r}): missing or non-integer "
                    f"'port' (got: {port_val!r}); skipping",
                    level="error",
                )
                continue
        if not (1 <= port_int <= 65535):
            io.echo(
                f"bind[{idx}] ({host_val!r}): port {port_int} out of range "
                f"[1, 65535]; skipping",
                level="error",
            )
            continue
        parsed.append((host_val, port_int))

    if parsed:
        args.binds = parsed


def _resolve_binds(args: argparse.Namespace) -> List[Tuple[str, int]]:
    """Build the final ``List[Tuple[str, int]]`` that
    ``WebSocketServer(binds=...)`` will receive.

    Precedence (highest first): ``--bind`` CLI flag,
    ``bed.json`` ``bind`` list/dict, the legacy
    ``--host``/``--port`` pair (single element), then the argparse
    defaults of ``("127.0.0.1", 8765)``.
    """
    cli_binds = getattr(args, "bind", None) or []
    if cli_binds:
        return [tuple(b) for b in cli_binds]
    cfg_binds = getattr(args, "binds", None) or []
    if cfg_binds:
        return [tuple(b) for b in cfg_binds]
    return [(args.host, args.port)]


DATABASE_FIELDS = {
    "databasename": ("name", str, False),
    "databasehost": ("host", str, True),
    "databaseport": ("port", int, False),
    "databaseuser": ("user", str, False),
    "databasepassword": ("password", str, False),
}

BED_NAME_FIELDS = {
    "bed_name": ("name", str, False),
}

AUTH_FIELDS = {
    "bed_secret": ("bed_secret_path", str, True),
    "token_ttl": ("token_ttl", int, False),
    "token_persistence": ("token_persistence", str, False),
    "credential_provider": ("credential_provider", str, False),
}


def _apply_bind_config(args: argparse.Namespace, cfg: dict) -> None:
    """Apply bind.host/bind.port from a loaded config when the CLI did not
    explicitly set them (i.e. args still equal argparse defaults)."""
    _apply_config_section(args, cfg, "bind", BIND_FIELDS)


def _apply_bed_name_config(args: argparse.Namespace, cfg: dict) -> None:
    """Apply bed.name from a loaded config when the CLI did not explicitly
    set --bed-name.

    Empty / missing ``name`` falls back to the argparse default (``"bed"``)
    so a misconfigured JSON entry does not produce a literal empty string
    in the secret-path derivation. The default "bed" preserves the
    historical ``~/.config/bed/bed.secret`` path for existing installs.
    """
    _apply_config_section(args, cfg, "bed", BED_NAME_FIELDS)
    defaults = _get_bed_defaults()
    if (
        args.bed_name == defaults["bed_name"]
        or not isinstance(args.bed_name, str)
        or not args.bed_name.strip()
    ):
        args.bed_name = defaults["bed_name"]
    else:
        args.bed_name = args.bed_name.strip()


def _apply_database_config(args: argparse.Namespace, cfg: dict) -> None:
    """Apply database.* from a loaded config when the CLI did not explicitly
    set those flags. The shipped factory config only carries name/host/port;
    user and password stay on the CLI or env.

    Accepts both nested (``{"database": {"user": ...}}``) and flat
    (``{"databaseuser": ...}``) keys.  The flat form is produced when
    environment variables like ``BED_DATABASEUSER`` are loaded by
    ``_load_from_env`` – the underscore-free name doesn't get nested
    under a ``"database"`` section, so we must handle it here as a
    fallback.

    A libpq-style ``"dsn"`` key (e.g. ``"host=db.local port=5432 dbname=myapp
    user=bed"``) is also accepted as a shorthand for the individual
    components.  Recognized components are ``dbname``, ``host``, ``port``,
    ``user``, ``password``; only those that the CLI did not set explicitly
    are applied, so ``dsn`` plays nicely with explicit ``--database*`` flags.
    """
    _apply_config_section(args, cfg, "database", DATABASE_FIELDS)
    defaults = _get_bed_defaults()
    if "databaseuser" in cfg and args.databaseuser == defaults["databaseuser"]:
        args.databaseuser = cfg["databaseuser"]
    if "databasepassword" in cfg and args.databasepassword == defaults["databasepassword"]:
        args.databasepassword = cfg["databasepassword"]

    db_sect = cfg.get("database")
    if isinstance(db_sect, dict) and isinstance(db_sect.get("dsn"), str):
        dsn = db_sect["dsn"]
        dsn_parts = parse_dsn(dsn)
        if (
            "dbname" in dsn_parts
            and args.databasename == defaults["databasename"]
        ):
            args.databasename = dsn_parts["dbname"]
        if (
            "host" in dsn_parts
            and args.databasehost == defaults["databasehost"]
        ):
            args.databasehost = dsn_parts["host"]
        if (
            "port" in dsn_parts
            and args.databaseport == defaults["databaseport"]
        ):
            try:
                args.databaseport = int(dsn_parts["port"])
            except (TypeError, ValueError):
                io.echo(
                    f"Ignoring non-integer port in database.dsn: "
                    f"{dsn_parts['port']!r}",
                    level="warning",
                )
        if "user" in dsn_parts and args.databaseuser == defaults["databaseuser"]:
            args.databaseuser = dsn_parts["user"]
        if (
            "password" in dsn_parts
            and args.databasepassword == defaults["databasepassword"]
        ):
            args.databasepassword = dsn_parts["password"]


def _apply_auth_config(args: argparse.Namespace, cfg: dict) -> None:
    """Apply auth.* from a loaded config when the CLI did not explicitly
    set those flags. Mirrors _apply_bind_config / _apply_database_config."""
    _apply_config_section(args, cfg, "auth", AUTH_FIELDS)
    # bed_instance_id defaults to None, not a placeholder string, so its
    # default check is "is None" rather than equality with a default value.
    auth = cfg.get("auth")
    if isinstance(auth, dict) and "bed_instance_id" in auth and args.bed_instance_id is None:
        args.bed_instance_id = auth["bed_instance_id"]


def _apply_websocket_config(args: argparse.Namespace, cfg: dict) -> None:
    """Apply ``websocket.*`` (WebSocket keepalive) from a loaded config.

    Reads ``websocket.ping_interval`` and ``websocket.ping_timeout``
    (seconds) from the JSON config and stashes them on ``args`` so
    :class:`bbsengine6.net.WebSocketServer` picks them up at
    construction time. Both default to ``None`` (use the
    websockets library's 20s/20s default) when the section or
    keys are missing. CLI flags ``--ws-ping-interval`` /
    ``--ws-ping-timeout`` take precedence over the JSON values
    when supplied.
    """
    ws = cfg.get("websocket")
    if not isinstance(ws, dict):
        return
    if (
        "ping_interval" in ws
        and getattr(args, "ws_ping_interval", None) is None
    ):
        args.ws_ping_interval = ws["ping_interval"]
    if (
        "ping_timeout" in ws
        and getattr(args, "ws_ping_timeout", None) is None
    ):
        args.ws_ping_timeout = ws["ping_timeout"]


class BED:
    """BBS Engine Daemon - WebSocket server with dynamic router loading."""

    DEFAULT_ROUTER_FQCN = "bbsengine6.net.defaultrouter.DefaultRouter"

    def __init__(
        self, args: argparse.Namespace, MessageRouterClass: Optional[Type] = None
    ):
        self.args = args
        self.MessageRouterClass = MessageRouterClass
        self.name: str = (
            getattr(args, "bed_name", None) or lib.DEFAULT_BED_NAME
        )
        self.server: Optional[WebSocketServer] = None
        self.router: Any = None
        self.auth_service: Optional[AuthService] = None
        self.token_store: Any = None
        self.message_service: Optional[MessageService] = None
        self._message_listener_task: Optional[asyncio.Task] = None
        self.bank_service: Optional[BankService] = None
        self._ping_service: Optional[PingService] = None
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

    def _final_binds(self) -> List[Tuple[str, int]]:
        """Compute the ``List[Tuple[str, int]]`` that
        ``WebSocketServer(binds=...)`` will receive at start() time.

        Mirrors the precedence order in
        :func:`_resolve_binds` but is a method so SIGHUP reload can
        re-read ``self.args`` and pick up config-driven changes
        without going through ``main_async`` again. Returns a list of
        ``(host, port)`` tuples; never raises on a malformed arg.
        """
        cli_binds = getattr(self.args, "bind", None) or []
        if cli_binds:
            return [tuple(b) for b in cli_binds]
        cfg_binds = getattr(self.args, "binds", None) or []
        if cfg_binds:
            return [tuple(b) for b in cfg_binds]
        host = getattr(self.args, "host", "127.0.0.1") or "127.0.0.1"
        port = getattr(self.args, "port", 8765) or 8765
        try:
            port_int = int(port)
        except (TypeError, ValueError):
            port_int = 8765
        return [(host, port_int)]

    async def start(self) -> None:
        """Start the daemon.

        Order is deliberate: do everything that can fail BEFORE constructing
        the WebSocketServer so a partial start never leaves a server with
        half-registered handlers. DB connection failure raises so the
        autorestart loop (or systemd) sees a real error rather than an
        idle daemon pretending to be healthy.
        """
        db_args = argparse.Namespace()
        db_args.databasename = self.args.databasename
        db_args.databasehost = self.args.databasehost
        db_args.databaseport = self.args.databaseport
        db_args.databaseuser = self.args.databaseuser
        db_args.databasepassword = self.args.databasepassword
        db_args.debug = getattr(self.args, "debug", False)
        db_args.config_file = self.args.config_file

        try:
            db_args.pool = getpool(db_args)
            with db_args.pool.connection():
                pass
        except Exception as e:
            io.echo(f"Database connection failed: {e}", level="error")
            io.echo(
                "Please ensure PostgreSQL is running with correct credentials",
                level="error",
            )
            raise

        # Auth objects (secret/token-store/provider/session-registry) are
        # constructed BEFORE the server so that ``_start_auth`` failures
        # cannot leak a half-wired WebSocketServer.
        self._session_registry = SessionRegistry()
        if self._auth_enabled():
            await self._start_auth(db_args)

        self.server = WebSocketServer(
            binds=list(self._final_binds()),
            ping_interval=getattr(self.args, "ws_ping_interval", None),
            ping_timeout=getattr(self.args, "ws_ping_timeout", None),
        )

        try:
            if self.auth_service is not None:
                self.auth_service.register_all(self.server)

            if self.MessageRouterClass is not None:
                # Mirror the msg_kwargs / bank_kwargs blocks below so
                # the router sees the same session registry + token
                # wiring MessageService and BankService get. Without
                # this the router falls back to its own fresh
                # CasinoSessionManager and the per-op _check_access
                # gate cannot find the session AuthService just bound,
                # which surfaces to clients as a spurious
                # ``not_authenticated`` envelope on the first
                # gameplay op after auth. Token kwargs stay empty
                # when auth is disabled (DefaultRouter + no auth) so
                # the router's legacy / door-mode fallback stays
                # intact for tests.
                router_kwargs: Dict[str, Any] = {}
                if (
                    self.auth_service is not None
                    and self.token_store is not None
                ):
                    router_kwargs = {
                        "session_registry": self._session_registry,
                        "secret": getattr(
                            self.auth_service, "secret", None
                        ),
                        "token_store": self.token_store,
                        "instance_id": getattr(
                            self.auth_service, "instance_id", None
                        ),
                    }
                self.router = self.MessageRouterClass(db_args, **router_kwargs)
                self.router.register_all(self.server)

            if not getattr(self.args, "no_message_service", False):
                # When auth is enabled, hand MessageService the same
                # HMAC secret / token store / instance id the auth
                # service uses, so it can re-verify
                # ``state.auth_service_token`` on every message op and
                # route the claim-derived ``moniker`` / ``is_sysop``
                # into ``bbsengine6.message.access()``. When auth is
                # disabled, none of those are available and the
                # message service falls back to session-only
                # authorization (legacy / --token-persistence=none
                # mode). Mirrors the bank_kwargs block below.
                msg_kwargs: Dict[str, Any] = {}
                if (
                    self.auth_service is not None
                    and self.token_store is not None
                ):
                    msg_kwargs = {
                        "secret": getattr(self.auth_service, "secret", None),
                        "token_store": self.token_store,
                        "instance_id": getattr(
                            self.auth_service, "instance_id", None
                        ),
                    }
                self.message_service = MessageService(
                    db_args, self._session_registry, **msg_kwargs
                )
                self.message_service.register_all(self.server)
                self._message_listener_task = asyncio.create_task(
                    self.message_service.start_listener()
                )

            if not getattr(self.args, "no_bank_service", False):
                # When auth is enabled, hand BankService the same
                # HMAC secret / token store / instance id the auth
                # service uses, so it can re-verify
                # ``state.auth_service_token`` on every bank op and
                # route the claim-derived ``moniker`` / ``is_sysop``
                # into ``bbsengine6.bank.access()``. When auth is
                # disabled, none of those are available and the bank
                # service falls back to session-only authorization
                # (legacy / --token-persistence=none mode).
                bank_kwargs: Dict[str, Any] = {}
                if (
                    self.auth_service is not None
                    and self.token_store is not None
                ):
                    bank_kwargs = {
                        "secret": getattr(self.auth_service, "secret", None),
                        "token_store": self.token_store,
                        "instance_id": getattr(
                            self.auth_service, "instance_id", None
                        ),
                    }
                self.bank_service = BankService(
                    db_args, self._session_registry, **bank_kwargs
                )
                self.bank_service.register_all(self.server)

            # PingService is registered LAST so its ``["ping"]`` entry
            # overwrites whatever the router (or bbsengine6's built-in
            # DefaultRouter) registered first. bbsengine6's
            # register_service emits a WARNING on the overwrite, so the
            # swap is visible in the log; the swap is intentional:
            # every bed instance surfaces its own ``name`` + ``version``
            # on the wire regardless of which router is loaded.
            self._ping_service = PingService(
                db_args, self._session_registry, name=self.name
            )
            self._ping_service.register_all(self.server)

            session_registry = self._session_registry

            async def _pre_dispatch(websocket: Any, message: Dict[str, Any]) -> None:
                # Pre-dispatch runs BEFORE the service handler. Its only
                # job is to install the right PostgreSQL role for the
                # DB queries the handler is about to make. The
                # SessionState may not be populated yet (e.g. for the
                # ``auth`` message itself, which is what populates
                # it); for those cases we leave the current role
                # untouched.
                ws_id = str(websocket.id)
                state = session_registry.get_by_websocket(ws_id)
                if state is not None:
                    set_current_role(state.moniker)

            async def _post_dispatch(
                websocket: Any,
                message: Dict[str, Any],
                response: Any,
            ) -> None:
                # Post-dispatch runs AFTER the service handler. By
                # this point AuthService has bound the SessionState
                # for the ``auth`` message, so the log line emitted
                # here carries the populated loginid/moniker for
                # every message, including the auth message itself.
                # ``state.loginid`` is set by the credential provider
                # from ``engine.__member.loginid``. When no session
                # has been bound yet (pre-auth traffic, or a session
                # whose credential lookup came back empty) we render
                # the fields as ``unbound`` rather than printing
                # blank values that look like a zero-length loginid.
                ws_id = str(websocket.id)
                state = session_registry.get_by_websocket(ws_id)
                if state is not None:
                    moniker = state.moniker
                    loginid = state.loginid
                    session_id = state.session_id
                else:
                    moniker = None
                    loginid = None
                    session_id = ws_id
                # Marker emitted when the websocket has no bound
                # SessionState (e.g. pre-auth traffic). Keeps the
                # log line greppable while clearly distinguishing
                # "no auth yet" from "auth but loginid is empty".
                UNBOUND = "unbound"
                loginid_str = loginid or UNBOUND
                moniker_str = moniker or UNBOUND
                msg_type = message.get("type") or ""
                if msg_type in ("bank_add", "bank_remove"):
                    amount = message.get("amount")
                    description = message.get("description") or ""
                    io.echo(
                        f"router: in session_id={session_id} "
                        f"loginid={loginid_str} "
                        f"moniker={moniker_str} type={msg_type} "
                        f"amount={amount} description={description}",
                        level="debug",
                    )
                else:
                    io.echo(
                        f"router: in session_id={session_id} "
                        f"loginid={loginid_str} "
                        f"moniker={moniker_str} type={msg_type}",
                        level="debug",
                    )
                if msg_type.startswith("bank_") and isinstance(response, dict):
                    ok = response.get("ok")
                    if ok is False:
                        io.echo(
                            f"router: out session_id={session_id} "
                            f"loginid={loginid_str} "
                            f"moniker={moniker_str} type={msg_type} "
                            f"ok=False code={response.get('code') or ''} "
                            f"message={response.get('message') or ''}",
                            level="debug",
                        )
                    else:
                        out_fields = []
                        for key in (
                            "balance",
                            "new_balance",
                            "transfer_id",
                            "amount",
                            "transactions",
                            "transfers",
                            "accounts",
                        ):
                            if key in response:
                                out_fields.append(f"{key}={response[key]}")
                        io.echo(
                            f"router: out session_id={session_id} "
                            f"loginid={loginid_str} "
                            f"moniker={moniker_str} type={msg_type} "
                            f"ok=True {' '.join(out_fields)}",
                            level="debug",
                        )

            self.server._pre_dispatch = _pre_dispatch
            self.server._post_dispatch = _post_dispatch

            await self.server.start()
        except Exception:
            # If anything between server construction and start() raises,
            # tear down whatever was started and let the autorestart loop
            # decide whether to retry.
            await self._cleanup_partial_start()
            raise

        self._running = True

        bound_addrs = getattr(self.server, "_bound_addrs", None) or []
        if len(bound_addrs) > 1:
            binds_summary = ", ".join(
                f"{fam} {host}:{port}" for fam, host, port in bound_addrs
            )
            io.echo(
                f"BED started on {len(bound_addrs)} listeners: "
                f"{binds_summary}"
            )
        else:
            io.echo(f"BED started on {self.args.host}:{self.args.port}")
        if self.MessageRouterClass:
            io.echo(
                f"Router: {self.MessageRouterClass.__module__}.{self.MessageRouterClass.__name__}",
            )
        if self.auth_service is not None:
            self._gc_task = asyncio.create_task(self._gc_loop())
        if self.message_service is not None:
            io.echo("BED MessageService: LISTEN engine_message_recipient")
        if self.bank_service is not None:
            io.echo("BED BankService: bank_balance/add/remove/history")
        if self._ping_service is not None:
            from bed._version import __version__
            io.echo(
                f"BED PingService: name={self.name} version={__version__}"
            )
        io.echo(f"Registered services: {self.server.list_services()}")

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            io.echo("BED cancelled")

    async def _cleanup_partial_start(self) -> None:
        """Tear down whatever BED.start() managed to construct before the
        exception that triggered cleanup. Best-effort; never raises."""
        if self._message_listener_task is not None and not self._message_listener_task.done():
            self._message_listener_task.cancel()
            try:
                await self._message_listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._message_listener_task = None
        if self.message_service is not None:
            try:
                await self.message_service.stop_listener()
            except Exception as e:
                io.echo(f"BED cleanup: message_service.stop_listener failed: {e}", level="warning")
            self.message_service = None
        if self.bank_service is not None:
            self.bank_service = None
        if self.server is not None:
            try:
                await self.server.stop()
            except Exception as e:
                io.echo(f"BED cleanup: server.stop failed: {e}", level="warning")
            self.server = None

    async def _start_auth(self, db_args: argparse.Namespace) -> None:
        """Load secret, build token store + provider, construct AuthService.

        ``self._session_registry`` is created by ``start()`` BEFORE this
        is called; we just consume it here. AuthService is constructed but
        NOT registered against the server here — ``start()`` registers
        services against the freshly-constructed ``WebSocketServer``.
        """
        explicit_secret = getattr(self.args, "bed_secret", None)
        if explicit_secret:
            secret_path = safe_path(explicit_secret, resolve_symlinks=False)
        else:
            name = getattr(self.args, "bed_name", lib.DEFAULT_BED_NAME) or lib.DEFAULT_BED_NAME
            secret_path = safe_path(
                lib._default_secret_path(name), resolve_symlinks=False
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
        # InMemoryTokenStore emits one InMemoryTokenStore.debug line
        # on every mutation -- unconditional, no --debug gate, so
        # ``token_revoked`` anomalies are debuggable from a normal
        # operator log. The lines are short, distinct, and tagged so
        # a single grep picks them out.
        if persistence == "db":
            self.token_store = DBTokenStore(db_args)
        else:
            self.token_store = InMemoryTokenStore()

        provider = get_provider(getattr(self.args, "credential_provider", "password"))
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
        io.echo(
            f"BED AuthService: instance={instance_id[:8]}… "
            f"ttl={ttl}s persistence={persistence} "
            f"provider={getattr(self.args, 'credential_provider', 'password')}",
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
        if self.message_service is not None:
            await self.message_service.stop_listener()
        if self.server:
            await self.server.stop()
        io.echo("BED stopped")

    async def restart(self) -> None:
        """Restart the daemon."""
        await self.stop()
        await self.start()


def buildargs(parentparser: argparse.ArgumentParser) -> None:
    """Add BED arguments to parent parser."""
    return lib.buildargs(parentparser)


def get_restart_config(
    args: argparse.Namespace,
    cfg: Optional[dict] = None,
) -> tuple[bool, int, int, bool]:
    """Get restart policy from args and a config dict.
    Priority: CLI flag > cfg["bed"] > hardcoded defaults.

    Returns ``(autorestart, restart_delay, max_restarts, restart_on_bind_failure)``.
    ``restart_on_bind_failure`` controls in-process retry of
    ``EADDRINUSE``/``EACCES`` from ``WebSocketServer.start()``; default
    is False so a stuck port does not get restarted in a tight loop.
    """
    bed_config = cfg.get("bed", {}) if isinstance(cfg, dict) else {}

    if args.autorestart is not None:
        autorestart = args.autorestart
    else:
        autorestart = bed_config.get("autorestart", False)

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

    if getattr(args, "restart_on_bind_failure", None) is not None:
        restart_on_bind_failure = args.restart_on_bind_failure
    else:
        restart_on_bind_failure = bed_config.get(
            "restart_on_bind_failure", False
        )

    return autorestart, restart_delay, max_restarts, restart_on_bind_failure


def load_router_class(router_path: str) -> Type:
    """Load a router class from a module path."""
    if (
        router_path == "default"
        or router_path == "bbsengine6.net.defaultrouter.DefaultRouter"
    ):
        from bbsengine6.net.defaultrouter import DefaultRouter

        return DefaultRouter

    module_path, class_name = router_path.rsplit(".", 1)
    # args=None disables bbsengine6.module.load's debug-time reload
    # (module.py:270-274) so the long-running daemon does not silently
    # re-import its router. Traceback-on-failure is handled by load().
    module = bbs_module_load(None, module_path)
    return getattr(module, class_name)


def _reload_config_and_apply(
    args: argparse.Namespace,
    bed: "BED",
    autorestart_ref: list,
    restart_delay_ref: list,
    max_restarts_ref: list,
    restart_on_bind_failure_ref: Optional[list] = None,
) -> None:
    """SIGHUP handler body: reload config from disk, apply live knobs to
    the running bed, and warn about structural changes that require a
    restart.

    Args:
        args: the argparse namespace shared by ``main_async``.
        bed: the currently-running ``BED`` instance.
        autorestart_ref / restart_delay_ref / max_restarts_ref /
        restart_on_bind_failure_ref: single-element lists containing
        the loop-local variables from ``main_async``. We pass them by
        reference because SIGHUP runs on the event loop and cannot
        capture loop locals via closure for mutation.

    Behavior:
        - Live knobs (token_ttl, debug) are applied to the running daemon.
        - autorestart / restart_delay / max_restarts /
          restart_on_bind_failure are updated in place.
        - Structural keys (bind.*, database.*, token_persistence,
          credential_provider, bed_secret_path, bed_instance_id) are
          detected but not applied; the operator is told a restart is
          required.
    """
    io.echo("Received SIGHUP, reloading config")
    try:
        new_config = config.load_config(args.config_file)
    except (ValueError, OSError, config.ConfigIORecoverableError) as e:
        # SIGHUP keeps the established "reload if possible, otherwise
        # stay up with the old config" semantics. The daemon is already
        # running and the operator did not ask for a restart loop on
        # config-reload failure, so we just log and return.
        io.echo(f"Config reload failed: {e}", level="error")
        return

    # Live: token_ttl — mutate the running AuthService's ttl_seconds so
    # freshly minted tokens pick up the new value immediately.
    if bed.auth_service is not None:
        ttl_diffs = _diff_config_section(
            args, new_config, "auth",
            {"token_ttl": ("token_ttl", int, False)},
        )
        for cli_arg, new_val in ttl_diffs:
            bed.auth_service.ttl_seconds = int(new_val)
            setattr(args, cli_arg, int(new_val))
            io.echo(
                f"Live reload: {cli_arg}={new_val} applied (AuthService)",
            )

    # Live: autorestart / restart_delay / max_restarts — update the loop
    # locals that drive the autorestart policy on the next crash.
    bed_cfg = new_config.get("bed", {}) if isinstance(new_config, dict) else {}
    if isinstance(bed_cfg, dict):
        if "autorestart" in bed_cfg:
            new_ar = bool(bed_cfg["autorestart"])
            if new_ar != autorestart_ref[0]:
                io.echo(
                    f"Live reload: autorestart={new_ar} applied",
                )
                autorestart_ref[0] = new_ar
        if "restart_delay" in bed_cfg:
            new_rd = int(bed_cfg["restart_delay"])
            if new_rd != restart_delay_ref[0]:
                io.echo(
                    f"Live reload: restart_delay={new_rd}s applied",
                )
                restart_delay_ref[0] = new_rd
        if "max_restarts" in bed_cfg:
            new_mr = int(bed_cfg["max_restarts"])
            if new_mr != max_restarts_ref[0]:
                io.echo(
                    f"Live reload: max_restarts={new_mr} applied",
                )
                max_restarts_ref[0] = new_mr
        if (
            "restart_on_bind_failure" in bed_cfg
            and restart_on_bind_failure_ref is not None
        ):
            new_robf = bool(bed_cfg["restart_on_bind_failure"])
            if new_robf != restart_on_bind_failure_ref[0]:
                io.echo(
                    f"Live reload: restart_on_bind_failure={new_robf} applied",
                )
                restart_on_bind_failure_ref[0] = new_robf

    # Structural: any of these require a full restart.
    structural_diffs = []
    structural_diffs.extend(
        _diff_config_section(args, new_config, "bind", BIND_FIELDS)
    )
    # Multi-bind list shape (``bind: [...]``) is structural too. The
    # legacy dict form is covered by ``_diff_config_section`` above; the
    # list form is its own key, so compare the resolved ``args.binds``
    # to what the freshly-loaded config would produce.
    old_binds = list(getattr(args, "binds", None) or [])
    if old_binds or isinstance(new_config.get("bind"), list):
        new_binds_section = new_config.get("bind")
        new_binds: List[Tuple[str, int]] = []
        if isinstance(new_binds_section, list):
            for entry in new_binds_section:
                if not isinstance(entry, dict):
                    continue
                host_val = entry.get("host")
                port_val = entry.get("port")
                if (
                    isinstance(host_val, str)
                    and host_val
                    and isinstance(port_val, int)
                    and not isinstance(port_val, bool)
                ):
                    new_binds.append((host_val, port_val))
        elif isinstance(new_binds_section, dict):
            host_val = new_binds_section.get("host")
            port_val = new_binds_section.get("port")
            if (
                isinstance(host_val, str)
                and host_val
                and isinstance(port_val, int)
                and not isinstance(port_val, bool)
            ):
                new_binds.append((host_val, port_val))
        if old_binds != new_binds:
            structural_diffs.append(
                ("binds", list(new_binds) if new_binds else old_binds)
            )
    structural_diffs.extend(
        _diff_config_section(args, new_config, "bed", BED_NAME_FIELDS)
    )
    structural_diffs.extend(
        _diff_config_section(args, new_config, "database", DATABASE_FIELDS)
    )
    for cli_arg, new_val in _diff_config_section(
        args, new_config, "auth", {
            "token_persistence": ("token_persistence", str, False),
            "credential_provider": ("credential_provider", str, False),
            "bed_secret": ("bed_secret_path", str, True),
            "bed_instance_id": ("bed_instance_id", str, False),
        }
    ):
        structural_diffs.append((cli_arg, new_val))
    if structural_diffs:
        keys = ", ".join(f"{k}={v!r}" for k, v in structural_diffs)
        io.echo(
            f"Config reload: structural changes detected ({keys}); "
            f"restart required for these to take effect",
            level="warning",
        )


async def main_async() -> None:
    """Async main entry point."""
    parser = argparse.ArgumentParser(description="BED - BBS Engine Daemon")
    buildargs(parser)
    args = parser.parse_args()

    args.config_file = resolve_config_path(args.config_file)
    # Resolve the load-time autorestart policy BEFORE attempting the
    # full load so we know whether a transient FS / network error
    # on the config path should fall back to the packaged default or
    # be treated as a fatal startup failure. Precedence matches
    # ``get_restart_config`` below: CLI --autorestart wins; otherwise
    # peek the JSON for bed.autorestart; otherwise False (fail-safe).
    if args.autorestart is not None:
        startup_autorestart = bool(args.autorestart)
    else:
        peeked = config._peek_autorestart(args.config_file)
        startup_autorestart = peeked if peeked is not None else False
    try:
        loaded_config = config.load_config(
            args.config_file, autorestart=startup_autorestart
        )
    except config.ConfigIORecoverableError:
        # ``load_config`` already emitted the level="error" message
        # naming the failure and the path. Exit with a distinct code
        # so the systemd unit (RestartPreventExitStatus=2 3) blocks
        # the loop and operators can tell this apart from exit 2
        # (permanent bind failure) and exit 1 (general load failure).
        sys.exit(3)
    except (ValueError, OSError) as e:
        io.echo(
            f"Failed to load config file {args.config_file}: {e}", level="error"
        )
        sys.exit(1)
    _apply_bind_config(args, loaded_config)
    _apply_bind_list_config(args, loaded_config)
    _apply_bed_name_config(args, loaded_config)
    _apply_database_config(args, loaded_config)
    _apply_auth_config(args, loaded_config)
    _apply_websocket_config(args, loaded_config)
    # Compute the final bind list after every config source has had
    # its say. ``_resolve_binds`` prefers ``--bind`` (CLI) > JSON
    # ``bind`` list > legacy ``--host``/``--port``.
    args.binds = _resolve_binds(args)

    autorestart, restart_delay, max_restarts, restart_on_bind_failure = (
        get_restart_config(args, loaded_config)
    )

    try:
        router_class = load_router_class(args.router)
        io.echo(
            f"Loaded router module "
            f"{args.router.rsplit('.', 1)[0]} from {router_class.__module__}",
        )
    except Exception:
        # bbsengine6.module.load() already emitted io.echo_traceback(...).
        io.echo("BED exiting: router load failed", level="error")
        sys.exit(1)

    if not ensure_startup(args):
        io.echo(
            "Startup failed: database bootstrap incomplete. "
            "Ensure PostgreSQL is running and credentials are correct.",
            level="error",
        )
        sys.exit(1)

    restart_count = 0
    shutdown_requested = False
    # Mutable holder so signal handlers always see the *current* bed,
    # including the brief window between bed assignment and start().
    bed_holder: list = [None]
    autorestart_ref: list = [autorestart]
    restart_delay_ref: list = [restart_delay]
    max_restarts_ref: list = [max_restarts]
    restart_on_bind_failure_ref: list = [restart_on_bind_failure]
    loop = asyncio.get_event_loop()

    def signal_handler() -> None:
        nonlocal shutdown_requested
        io.echo("Received shutdown signal")
        shutdown_requested = True
        current = bed_holder[0]
        if current is not None:
            asyncio.create_task(current.stop())

    def sighup_handler() -> None:
        current = bed_holder[0]
        if current is None:
            io.echo(
                "SIGHUP received before bed is running; ignoring (config "
                "already loaded at startup)",
                level="warning",
            )
            return
        _reload_config_and_apply(
            args,
            current,
            autorestart_ref,
            restart_delay_ref,
            max_restarts_ref,
            restart_on_bind_failure_ref,
        )

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        loop.add_signal_handler(signal.SIGHUP, sighup_handler)
    except (NotImplementedError, OSError):
        pass

    pidfile_path = getattr(args, "pidfile", None)
    pidfile_fd = None
    if pidfile_path:
        pidfile_fd = _write_pidfile(pidfile_path)
        if pidfile_fd == -2:
            sys.exit(1)
        if pidfile_fd < 0:
            pidfile_path = None

    try:
        while not shutdown_requested:
            bed = BED(args, router_class)
            bed_holder[0] = bed

            try:
                await bed.start()
                restart_count = 0
            except Exception as e:
                bed_holder[0] = None
                if shutdown_requested:
                    break
                io.echo_traceback(f"BED error: {e}")

                current_autorestart = autorestart_ref[0]
                current_restart_delay = restart_delay_ref[0]
                current_max_restarts = max_restarts_ref[0]
                current_restart_on_bind_failure = restart_on_bind_failure_ref[0]

                is_permanent_bind_failure = (
                    isinstance(e, OSError)
                    and getattr(e, "errno", None)
                    in (errno.EADDRINUSE, errno.EACCES)
                )
                # ``socket.gaierror`` (host did not resolve) and
                # ``OSError`` from the resolve phase are treated the
                # same as a permanent bind failure: a typo'd host name
                # will not start working via in-process retry, so exit
                # 2 unless the operator explicitly opted into retrying.
                is_unresolvable_bind = isinstance(e, socket.gaierror)
                is_permanent_bind_failure = (
                    is_permanent_bind_failure or is_unresolvable_bind
                )

                if is_permanent_bind_failure and not current_restart_on_bind_failure:
                    await bed.stop()
                    if is_unresolvable_bind:
                        io.echo(
                            f"BED refusing to start: bind host did not "
                            f"resolve: {e}. Check --bind / bind entries in "
                            f"bed.json and /etc/hosts.",
                            level="error",
                        )
                    else:
                        io.echo(
                            f"BED refusing to restart on bind failure: {e}. "
                            f"Free the port, run as a user with bind permission, "
                            f"or set restart_on_bind_failure=true to override.",
                            level="error",
                        )
                    sys.exit(2)

                if (
                    current_autorestart
                    or (
                        is_permanent_bind_failure
                        and current_restart_on_bind_failure
                    )
                ):
                    restart_count += 1
                    if restart_count > current_max_restarts:
                        io.echo(
                            f"Max restarts ({current_max_restarts}) reached, giving up",
                            level="error",
                        )
                        await bed.stop()
                        break

                    io.echo(
                        f"Auto-restarting in {current_restart_delay}s "
                        f"(attempt {restart_count}/{current_max_restarts})",
                        level="warning",
                    )
                    await bed.stop()
                    await asyncio.sleep(current_restart_delay)
                    continue
                else:
                    await bed.stop()
                    raise

            bed_holder[0] = None
            if shutdown_requested or not autorestart_ref[0]:
                break
    finally:
        if pidfile_fd is not None and pidfile_fd >= 0:
            try:
                os.close(pidfile_fd)
            except OSError:
                pass
        if pidfile_path:
            _remove_pidfile(pidfile_path)


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
