# bed (BBS Engine Daemon) — Specification

> **Status (2026-08-19):** v1 stable (daemon core, AuthService, MessageService, BankService, FHS install, multi-bind). v1.1 in flight (MessageService GA + cross-repo adoption). v2 design-only.
>
> This file is the **entry point** for understanding `bed`. For per-item line numbers, see:
>
> - `bed/TODO.md` — line-numbered open work + cross-references
> - `bed/TODO-message-service.md` — phased plan for server-push notifications
> - `bed/docs/BED_AUTH.md` — bearer-token auth wire protocol + v2 design
> - `bed/FHS.md` — FHS/UAPI compliance design
> - `bbsengine6/TODO.md`, `bbsengine6/TODO-BOTTOMBAR.md` — engine-side dependencies
>
> Last updated: 2026-08-19

---

## Table of Contents

1. [Purpose & Scope](#1-purpose--scope)
2. [Status](#2-status)
3. [v1 — What Works Now](#3-v1--what-works-now)
4. [Code Migrated FROM bed TO bbsengine6](#4-code-migrated-from-bed-to-bbsengine6)
5. [v1 — What Doesn't Work Yet](#5-v1--what-doesnt-work-yet)
6. [Phase Gates](#6-phase-gates)
7. [v2 — Future Plans](#7-v2--future-plans)
8. [bbsengine6-side Gaps (prerequisites)](#8-bbsengine6-side-gaps-prerequisites)
9. [Cross-Reference Map](#9-cross-reference-map)
10. [Authoritative File Index](#10-authoritative-file-index)
11. [Out of Scope / Known Limitations](#11-out-of-scope--known-limitations)

---

## 1. Purpose & Scope

### 1.1 What `bed` is

`bed` is a small WebSocket daemon that sits in front of a bbsengine6 game router (empyre, casino, mistermcfeely, murdermotel, zoid6, etc.), terminates JSON-over-WebSocket, and lets the game own the wire protocol. It was extracted from the meta-repo on **2026-06-27** into its own repository. License: GPL-2.0-or-later. Python 3.9–3.12.

### 1.2 What `bed` is NOT

- It is **not** a generic WebSocket server. It is purpose-built for BBS engine games.
- It is **not** a notification daemon. The `notifyd` specs in `bbsengine6/handbook/specs/BBSENGINE6_NOTIFYD_*.md` are dead (10 files marked SUPERSEDED 2026-07-22). See Section 11.
- It is **not** a migration source for bbsengine6. It is a sibling/peer project that *consumes* bbsengine6. See Section 4 for the small set of code that DID move bed → bbsengine6.

### 1.3 Architectural rule

```
bbsengine6  ──owns──>  I/O protocol, DB, business logic, TUI
bed          ──owns──>  Daemon lifecycle, auth, server-push, cross-cutting wire-protocol services
games (empyre, casino, …)  ──consume──>  bbsengine6 (business) + bed (transport)
```

Dependency direction: `games` → `bed` → `bbsengine6`. `bbsengine6` does not import `bed`.

### 1.4 The two "bed" files

There are two files with "bed" in the name; both refer to BBS Engine Daemon:

| File | Role | Size |
|---|---|---|
| `bed/src/bed/main.py` | **Full production daemon** — `BED` class (617 lines), argparse, lifecycle | 21,382 B |
| `bbsengine6/py/src/bbsengine6/bed.py` | **Thin shim** — minimal `BED` class (169 lines) for `python -m bbsengine6.bed` | small |

Per the `BBSENGINE6_NOTIFYD_OVERVIEW.md` 2026-07-22 banner: *"The actual bbsengine6 daemon is `bed.py`"*. In production deployments the daemon is the `bed` package; the in-tree shim is for development/CLI convenience only.

---

## 2. Status

### 2.1 v1 stable features

- WebSocket daemon core (start/stop/restart, PID file, `--config` optional with `BED_CONFIG`/`/etc/bed/bed.json`/packaged-default fallback)
- Dynamic router loading (`--router FQCN`)
- `AuthService` — bearer-token auth (HMAC-SHA256, 15-min TTL)
- Token storage (`InMemoryTokenStore`, `DBTokenStore`, `none`)
- `MessageService` — PG LISTEN/NOTIFY push notifications (auto-wired with any non-default router)
- `BankService` — bed-native empyre-shape bank handler (4 wire types; auto-wired alongside MessageService)
- `bed.client.*` — connection / bank / bankservice / messageservice / probe / singleton helpers
- Database bootstrap (`bed.startup` runs `bbsengine6.startup` + creates `bed` PG role; also auto-invoked at daemon start)
- FHS-compliant install (Makefile, systemd unit, sysusers, tmpfiles, factory config + env file)
- SELinux integration (`semanage` + `restorecon` automatic)
- 4 CLI scripts: `bed`, `bed-startup`, `bank`, `ping`

### 2.2 Test count

6 test modules, ~4,148 lines:
- `bed/src/bed/tests/test_bed.py` — ~1,223 lines
- `bed/src/bed/tests/test_auth_service.py` — ~1,252 lines
- `bed/src/bed/tests/test_startup.py` — 332 lines
- `bed/src/bed/tests/test_message_service.py` — 425 lines
- `bed/src/bed/tests/test_bank_service.py` — 710 lines (new — bed-native BankService)
- `bed/src/bed/tests/test_client.py` — 206 lines (Phase 3 asyncio hardening)

Plus: `bed/tests/scripts/stop_bed.sh` (SIGTERM/SIGKILL test helper).

### 2.3 v1.1 in flight

- FHS default-config path drift (see Section 5.2)
- Cross-repo adoption of `MessageService` (zoid6, empyre, casino, murdermotel, mistermcfeely)
- `--no-bed-fallback` flag for bbsengine6 TUI
- F2 key handler migration (`getch.py`) from DB-poll to `message_list_pending`

### 2.4 v2 design-only

- Multi-instance load balancing (Path A + Path B) — see `bed/docs/BED_AUTH.md`
- Per-game style palettes, `menu_multi` primitive, `key_f2` paging, linger tuning, tilde fix Option C

---

## 3. v1 — What Works Now

| Feature | Module / File | Test | Notes |
|---|---|---|---|
| `BED` daemon class | `bed/src/bed/main.py:617` | `test_bed.py` | start/stop/restart, autorestart, restart_delay, max_restarts, restart_on_bind_failure, multi-bind (`--bind`, JSON `bind` list, `localhost` → dual-stack) |
| CLI argparse | `bed/src/bed/lib.py:147-156` | `test_bed.py::TestConfigFlag` | --host, --port, --bind (repeatable), --router, --config, --pidfile, --autorestart, --restart-on-bind-failure, --debug, --foreground |
| Default host | `bed/src/bed/main.py` | n/a | `127.0.0.1` (was `localhost`, ambiguous for server bind). Multi-bind available via `--bind` (CLI) or `bind: [...]` in `bed.json`; `localhost` in `--bind` resolves to both A and AAAA listeners. |
| Multi-bind (dual-stack) | `bed/src/bed/lib.py` + `bed/src/bed/main.py` + `bbsengine6/net/transport.py` | `test_bed.py::TestBindMulti`, `TestBindMultiStart`, `test_transport_multibind.py` | One daemon can listen on multiple `(host, port)` pairs; each name-based entry fans out via `getaddrinfo(AF_UNSPEC)`. State shared across listeners. See `README.md` Multi-bind section. |
| PID file (atomic) | `bed/src/bed/main.py:68-` | `test_bed.py::TestPidfile` | O_EXCL TOCTOU retry, stale-overwrite, live-collision exit 1 |
| `bed.json` loader | `bed/src/bed/config.py:21-48` | `test_bed.py` | CLI > file > argparse default; BED_* env-var support; deep-merge. Transient FS/network load failures (`socket.gaierror`, `PermissionError`, `EIO`, `ESTALE`, `ETXTBSY`, `ENETUNREACH`, `EHOSTUNREACH`, `ECONNREFUSED`, `ETIMEDOUT`) honor `autorestart`: fall back to packaged default if `autorestart=True`, else raise `ConfigIORecoverableError` so `main_async` exits `3`. Operator errors (`FileNotFoundError`, `IsADirectoryError`, JSONDecodeError) always propagate (exit `1`). |
| `bed.json` path resolver | `bed/src/bed/_configpath.py` | `test_bed.py::TestConfigFlag` | Optional `--config`: `$BED_CONFIG` > `/etc/bed/bed.json` if present > packaged `bed/data/bed.json` (wheel default). Mirrors `zoid6/main.py:_resolve_config_path`. |
| Missing-config error | `bed/src/bed/main.py:336-345` | `test_bed.py` | Exits 1 with `Config file not found:` |
| Recoverable-config error | `bed/src/bed/config.py:ConfigIORecoverableError` + `bed/src/bed/main.py:1181-1187` | `test_bed.py::TestMainAsyncConfigIOFallback`, `TestConfigLoadFallback` | Exit 3 (systemd `RestartPreventExitStatus=2 3`) when a transient FS/network error hits the explicit config path AND the resolved `autorestart` is False. CLI `--autorestart` wins; otherwise a peek-read of `bed.autorestart` from the JSON; otherwise False (fail-safe). SIGHUP reload keeps the old config and does not exit. |
| Dynamic router loading | `bed/src/bed/main.py` | `test_bed.py` | `bbsengine6.module.load()` resolves FQCN (traceback on failure via `io.echo_traceback`, info-log on success); passes `args=None` to suppress the debug-reload branch so the long-running daemon never re-imports its router |
| `AuthService` (bearer) | `bed/src/bed/api/auth.py:412` | `test_auth_service.py:744` | HMAC-SHA256, 15-min TTL, websocket_id binding, instance check |
| `TokenStore` (memory) | `bed/src/bed/api/token_store.py` | `test_auth_service.py` | Default; in-process dict |
| `TokenStore` (db) | `bed/src/bed/api/token_store.py` | `test_bed_token_persistence.py` (planned) | Opt-in; `engine.__bed_token` created lazily |
| HMAC secret loader | `bed/src/bed/api/secret.py` | `test_auth_service.py` | v1 binary, v2 JSON; refuses 0600 violation |
| `CredentialProvider` protocol | `bed/src/bed/api/credential_provider.py` | `test_auth_service.py` | `PasswordCredentialProvider`, `MonikerOnlyCredentialProvider` |
| Error envelopes | `bed/src/bed/api/errors.py` | `test_auth_service.py` | `token_expired`, `token_invalid`, `token_revoked`, `bed_instance_mismatch` |
| `MessageService` | `bed/src/bed/api/message.py:267` | `test_message_service.py:250` | subscribe/unsubscribe/list_pending, LISTEN `engine_message_recipient` |
| Wire MessageService | `bed/src/bed/main.py` (BED.start) | `test_bed.py` | Instantiated, started, registered with WebSocketServer |
| `--no-message-service` flag | `bed/src/bed/lib.py` | `test_bed.py` | Disables MessageService for tests |
| `bed.client.BedConnection` | `bed/src/bed/client/connection.py:326` | (client-side, not bed tests) | subscribe/unsubscribe, background _recv_loop, _recv_match |
| `bed.client.BedBankClient` | `bed/src/bed/client/bank.py` | (client-side) | empyre shape; subclass of `BedMessageClient` |
| `bed.client.BedBankServiceClient` | `bed/src/bed/client/bankservice.py` | (client-side) | High-level wrapper: get_balance / add_funds / remove_funds / get_history; soft-failure envelopes |
| `bed.client.BedMessageServiceClient` | `bed/src/bed/client/messageservice.py` | (client-side) | Push handler; sets bbsengine6 local cache |
| `bed.client.probe_bed` | `bed/src/bed/client/probe.py` | (client-side) | Synchronous TCP probe |
| `bed.client.singleton` | `bed/src/bed/client/singleton.py` | (client-side) | `get_bed_connection`, `reset_bed_connection` |
| `bed.startup` | `bed/src/bed/startup.py` | `test_startup.py:332` | Runs `bbsengine6.startup` then creates `bed` PG role |
| `bed.defaultrouter.DefaultRouter` | `bed/src/bed/defaultrouter.py` | `test_bed.py` | Registers `AuthService` + `BankServiceHandler` (9 bank message types) |
| `BankService` (bed-native) | `bed/src/bed/api/bank.py` | `test_bank_service.py` | Registers `bank_balance` / `bank_add` / `bank_remove` / `bank_history`; delegates to `bbsengine6.bank.BankService` |
| Wire BankService | `bed/src/bed/main.py` (BED.start) | `test_bank_service.py` | Auto-registered alongside MessageService; opt out with `--no-bank-service` |
| `--no-bank-service` flag | `bed/src/bed/lib.py` | `test_bank_service.py` | Disables the bed-native BankService |
| FHS install chain | `bed/Makefile` | n/a (install) | `install-sysusers` → `install-tmpfiles` → `install-venv` → `install-systemd` → `install-etc` |
| `/etc/bed/bed.json` factory | `bed/usr/share/factory/etc/bed/bed.json` | n/a | FHS factory default; installed by `install-etc` |
| systemd unit | `bed/src/bed/daemon/bed.service` | n/a | `Type=simple`, `User=bed`, `Restart=on-failure`, `TimeoutStopSec=30s`; `bed.service` ships generic; per-game `zoid6-bed.service` etc. live in the game repo and pass `--config` + `--router` |
| SIGHUP config reload | `bed/src/bed/main.py` | n/a | wires SIGHUP to `config.reload_config()` (informational log; live server keeps startup args; true reload = `systemctl restart bed`) |
| systemd install | `bed/Makefile` (`install-systemd`) | n/a | copies `bed.service` to `/etc/systemd/system/`, runs `daemon-reload`; operator must `enable --now` after review |
| Environment file | `/etc/zoid6/bed.env` (zoid6 example) | n/a | `BED_DATABASEUSER`/`BED_DATABASEPASSWORD`; `BBSENGINE6_DB*` env vars honored by `bbsengine6`'s `databasebuildargs` |
| Journal logs | `bed/src/bed/main.py` | n/a | `SyslogIdentifier=bed`; `bed` writes via `bbsengine6.io.echo` → stdout/stderr → journal |
| sysusers + tmpfiles | `bed/src/bed/daemon/bed.sysusers`, `bed.tmpfiles` | n/a | Creates `bed` user/group; `/etc/bed`, `/var/log/bed`, `/var/lib/bed` |
| SELinux | `bed/Makefile` (install-venv) | n/a | `semanage fcontext` + `restorecon` |
| CLI scripts | `bed/pyproject.toml` | n/a | `bed`, `bed-startup`, `bank`, `ping` |

---

## 4. Code Migrated FROM bed TO bbsengine6

These items were originally in `bed` and have moved (or are now better-owned by) `bbsengine6`. The migration is one-way: bed extends/consumes, bbsengine6 owns the base.

| Was in `bed` | Now in `bbsengine6` | Date | Notes |
|---|---|---|---|
| In-memory `SessionManager` base | `bbsengine6.session.core.SessionManager` | pre-2026-07-22 | `bed.api.session.SessionRegistry` extends the bbsengine6 class |
| `send_to(ws, msg)` helper | `bbsengine6/net/transport.py:699-701` | pre-2026-07-22 | `MessageService._dispatch_notification` consumes |
| `MessageRouterMixin` API (for BEDSink) | `bbsengine6/net/router.py` (Phase 5) | pre-2026-07-22 | `next_request_id(ws)`, `get_pending_request(ws, id)`, `resolve_pending_request(ws, id, value)`, `cleanup_session(ws)` |
| Per-connection bottombar plumbing | `bbsengine6/bottombar.py` Phase 4a | 2026-07-22 | `registry_for(name)`, `set_context_for`, `render_for`, `set_active_registry`, `reset_active_registry`, `_active_registry` ContextVar |

### 4.1 Migration candidates (future)

These items are currently bed-local but could/should move to bbsengine6 once the abstractions stabilize:

- **Tilde expansion in `_apply_*_config`** — `bed/src/bed/main.py:90-115` (and `_apply_bind_config:57-67`, `_apply_database_config:70-87`). The pattern is generic; could become `bbsengine6.config.expand_user_deep(obj)`.
- **`BedMessageClient` base class** — `bed/src/bed/client/messages.py`. Currently a thin transport wrapper. Could move to `bbsengine6.net.client` once multiple non-bed consumers exist.
- **`bed.defaultrouter.DefaultRouter` `BankServiceHandler`** — `bed/src/bed/defaultrouter.py`. Already wraps `bbsengine6.bank.api.handler.BankServiceHandler`; could collapse to a re-export once all games adopt the bbsengine6 router directly. **Now superseded** by the bed-native `bed.api.bank.BankService` (4-message empyre shape) for callers that don't need the 9-message full bank surface (transfers, pending, sysop list-all).
- **`BBSENGINE6_NOTIFYD_*.md` specs (10 files)** — already marked SUPERSEDED in place; eventual deletion is a bbsengine6-side cleanup.

---

## 5. v1 — What Doesn't Work Yet

### 5.1 Service implementations (designed, not built)

| Service | Planned file | Wire types | Source |
|---|---|---|---|
| `EchoService` | `bed/api/echo.py` | `echo`, `echo_batch`, `echo_ack`, `echo_nack`, `echo_cancel` | `bed/TODO.md:217-299` |
| `Fragment` + `FragmentQueue` | `bed/api/fragment.py` | (queue primitives) | `bed/TODO.md:219` |
| Style schema + MCI codec | `bed/api/style.py` | (codec) | `bed/TODO.md:223` |
| `MenuService` | `bed/api/menu.py` | `menu`, `menu_reply`, `menu_timeout`, `menu_cancel` | `bed/TODO.md:597-762` |
| `MenuValidator` | `bed/api/menu_validator.py` | (validator) | `bed/TODO.md:599` |
| Server-side menu timeout | `bed/api/menu_timeout.py` | (asyncio.Timer) | `bed/TODO.md:606` |
| `HelpService` | `bed/api/help.py` | `help`, `help_result`, `help_error` | `bed/TODO.md:609` |
| `KeyF2Service` | `bed/api/key_f2.py` | `key_f2`, `key_f2_result`, `key_f2_empty`, `key_f2_error` | `bed/TODO.md:886` |
| `KeyF2Channels` resolver | `bed/api/key_f2_channels.py` | (resolver) | `bed/TODO.md:889` |
| `KeyF2Items` builder | `bed/api/key_f2_items.py` | (builder) | `bed/TODO.md:893` |
| `BEDSink` | `bed/sinks/bed_sink.py` | (implements `bbsengine6.io.sink.Sink`) | `bed/TODO.md:977` |
| `ThinClientIOSink` | `bed/client/io_sink.py` | (implements `bbsengine6.io.sink.Sink`) | `bed/TODO.md:1068` |

### 5.2 Infrastructure gaps

| Gap | File | Status | Source |
|---|---|---|---|
| FHS default-config path drift | `bed/src/bed/_configpath.py` + `bed/src/bed/config.py` | **resolved (two-step)** | Step 1 (`8124105`): `--config` became `required=True` so `/etc/bed/bed.json` typo exits loudly. Step 2 (this release): `--config` is **optional** and `bed/_configpath.resolve_config_path()` walks `$BED_CONFIG` > `/etc/bed/bed.json` if present > packaged default. FHS hosts still ship `/etc/bed/bed.json` via `install-etc`; non-FHS invocations (`deploy-venv`, `bed --foreground`) get the packaged default automatically. |
| `bed.service` not passing `--config /etc/bed/bed.json` | `bed/src/bed/daemon/bed.service` | **resolved** | `bed.service` ships with `ExecStart=/var/lib/bed/venv/bin/bed --config /etc/bed/bed.json` |
| End-to-end DB LISTEN tests | `bed/src/bed/tests/test_message_lib.py` (in bbsengine6) | deferred | `bed/TODO-message-service.md` Phase 7 |
| `zoid6/src/zoid6/data/bed.json` not updated | `zoid6/src/zoid6/data/bed.json` | open | `bed/TODO-message-service.md` Phase 8 |
| `bbsengine6` config docs not updated | (docs) | open | `bed/TODO-message-service.md` Phase 8 |
| No `--no-bed-fallback` flag for TUI | `bbsengine6/.../main.py` | open | `bed/TODO-message-service.md` Phase 9 |
| F2 key handler still DB-backed | `bbsengine6/io/getch.py` | open | `bed/TODO-message-service.md` Phase 5 |
| `_check_notifications` not yet verified to skip DB on warm cache | `bbsengine6/...` | deferred | `bed/TODO-message-service.md` Phase 5 |
| Test for `_recv_loop` skipping non-matching messages | `bed/src/bed/tests/` | open | `bed/TODO-message-service.md` Phase 7 |
| zoid6 dependency on `bed` in `pyproject.toml` | `zoid6/src/pyproject.toml` | open | `bed/TODO.md:1331` |

### 5.3 Bug fixes (3 known + 2 design)

| Bug | File | Fix | Source |
|---|---|---|---|
| `_apply_auth_config` overwrites CLI `--bed-secret` with literal `~` | `bed/src/bed/main.py:90-115` | Option D: wrap `auth["bed_secret_path"]` in `os.path.expanduser` | `bed/TODO.md:1616-1824` |
| Same tilde bug in `_apply_bind_config` | `bed/src/bed/main.py:57-67` | Same fix | `bed/TODO.md:1780` |
| Same tilde bug in `_apply_database_config` | `bed/src/bed/main.py:70-87` | Same fix | `bed/TODO.md:1782` |
| SIGTERM/SIGKILL gap | `bed/src/bed/main.py:370-389` | Promote stale-pidfile detection (done); process-group cleanup (done); linger tuning (deferred) | `bed/TODO.md:1828-2115` |
| Two-Makefile consolidation | `bed/Makefile` vs `bed/src/Makefile` | OUTDIR mismatch; standardize version labels | `bed/src/Makefile:50-51` |

### 5.4 Cross-repo adoption (per-game)

| Game | Files | Phase | Blocker |
|---|---|---|---|
| **empyre** | `empyre/src/empyre/services/player.py:56-109` (convert `_BedPlayerClient` to `BedMessageClient` subclass) | Phase 0a (auth) → Phase 1 (IO shim) → Phase 2 (menu) | None — bed ready |
| **empyre** | `empyre/io_bridge.py` (inputchoice → menu) | Phase 2 | `MenuService` (bed) |
| **casino** | `casino/src/casino/services/bank_client.py` (subclass `BedMessageClient`) | Phase 0a | None — bed ready |
| **casino** | `casino/src/casino/games/{blackjack,poker,roulette}/`, `lobby/`, `api/handler.py` (inputchoice → menu) | Phase 1 | `MenuService` (bed) |
| **casino** | `casino/dal/player.py:92` NULL-credits crash | n/a (FIXED `casino/03be20c`) | done |
| **murdermotel** | `murdermotel/lobby.py:74` (`help=lobbyhelp`), `play.py:446` (`help=playgroundhelp`), `rabidwolf.py:531` (`help=help`) | Phase 1 (menu) → Phase 2 (key_f2) | `MenuService` + `HelpService` (bed) |
| **mistermcfeely/postoffice** | adopt `key_f2` | Phase 1 | `KeyF2Service` (bed) |
| **zoid6** | `zoid6/src/zoid6/api/handler.py:13` add `list_services` handler | Phase 1 | None — MessageRouter ready |
| **zoid6** | `zoid6/src/zoid6/data/bed.json` fix `bank.modulepath`→`bbsengine6.bank.api.handler`, `channel.enabled`→`false` | Phase 1 | None |
| **zoid6** | `zoid6/src/pyproject.toml` add `bed>=0.0.1.dev2026` | Phase 1 | bed wheel |
| **bbsengine6 bank service** | `bbsengine6.bank.api.handler.MessageRouter` adopts `EchoService`, `menu`, bearer token | Phase 7.2.1 | `EchoService` + `MenuService` (bed) + sink protocol (bbsengine6) |

---

## 6. Phase Gates

### 6.1 v1.0 — SHIPPED (current)

- Daemon core, `--config`, `--router`, PID file, dynamic loading — done
- `AuthService` (bearer tokens) — done
- `MessageService` (PG LISTEN/NOTIFY) — done
- `bed.client.*` library — done
- FHS install chain — done
- Test count ≥ 4 modules, ~2,098 lines — done

**Criteria to call v1.0 done:** all 6 above. ✅

### 6.2 v1.1 — MessageService GA

- All 9 phases of `bed/TODO-message-service.md` checked
- zoid6 `bed.json` enables message service by default
- F2 key handler in `getch.py` migrated from `message.get_queue` (DB) to `message_list_pending` (bed push)
- `--no-bed-fallback` flag added to bbsengine6 TUI
- End-to-end DB LISTEN test in `test_message_lib.py`
- bbsengine6 config docs updated

**Blocker list:**
- F2 key handler is in bbsengine6, not bed — coordination needed
- `--no-bed-fallback` is a bbsengine6 TUI flag, not bed

### 6.3 v1.2 — Sink Infrastructure Adoption

**Bed side:**
- `BEDSink` implemented in `bed/sinks/bed_sink.py`
- `BEDSink` installed via `WebSocketServer.on_connect_hook`
- `ThinClientIOSink` in `bed/client/io_sink.py`
- `BEDSink.echo` calls `bbsengine6.io.echo_render` and `bbsengine6.io.mci.parse`

**bbsengine6 side (prerequisites):**
- `bbsengine6/io/sink.py` with `Sink` protocol, `DefaultSink`, `set_io_sink`/`reset_io_sink` — Phase 0
- `bbsengine6/io/echo_render.py` — Phase 1
- `bbsengine6/io/mci.py` with `mci.parse`/`mci.render` — Phase 2
- `echo()` returns the rendered string — Phase 3
- Sink-based variants for other primitives — Phase 4
- `MessageRouterMixin` + `on_connect_hook` — Phase 5

**Game side (adoption):**
- empyre, casino, murdermotel, mistermcfeely, zoid6 each switch to thin-client `IOSink` via `sys.modules` swap

### 6.4 v1.3 — Menu + Help + KeyF2

**Bed side:**
- `MenuService` (validate, timeout, single-pick)
- `HelpService` (callable on demand, 10 req/s rate limit)
- `KeyF2Service` (queries `key_f2_visible=true` channels, max 50 items, 5 req/s)
- `key_f2.max_items`, `key_f2.rate_limit`, `key_f2.channel_allow_list` in `bed.json`

**Game side:**
- casino: primary `MenuService` adopter (replaces `bbsengine6.io.inputchoice`)
- murdermotel: primary `HelpService` adopter (lobby/play/rabidwolf help callables)
- all games: primary `KeyF2Service` adopters

**bbsengine6 side:** none (services live in bed)

### 6.5 v1.4 — Echo / MCI codec

**Bed side:**
- `EchoService` (at-least-once with reconnect-resume)
- `Fragment` + `FragmentQueue` (per-session, per-stream ordered)
- `Style` schema + MCI codec
- `echo.ack_timeout` in `bed.json` (default 30s)

**bbsengine6 side:** `io.echo_render` (Phase 1) + `io.mci.parse` (Phase 2) are the codec roots.

**Game side:** empyre (Phase 1 of empyre/TODO.md), casino, mistermcfeely/postoffice, murdermotel, zoid6, bbsengine6 bank service.

### 6.6 v1.5 — Multi-Bind (DONE)

- One daemon listens on multiple `(host, port)` pairs via `--bind`
  (repeatable) and the JSON `bind` list.
- Each name-based entry resolves via `getaddrinfo(AF_UNSPEC)` so a
  single `localhost` produces both IPv4 and IPv6 listeners.
- State (services, session manager, channel state, pre/post
  dispatch hooks) is shared across every listener — a service
  registered once reaches every bind.
- Partial-bind failure (EADDRINUSE on the second bind, EACCES on a
  privileged port, `gaierror` on a typo'd host) closes already-
  opened sockets before re-raising so no port is held by a half-
  started daemon.
- `restart_on_bind_failure` semantics extend to multi-bind; the
  error message distinguishes "free the port" from "check
  /etc/hosts".
- SIGHUP reload detects bind-list changes as structural.

Backend: `WebSocketServer(binds=...)` in `bbsengine6/net/transport.py`.
CLI plumbing: `bed/src/bed/lib.py:_bind_spec` and the `--bind`
argparse flag. Config plumbing: `bed/src/bed/main.py:_apply_bind_list_config`
and `_resolve_binds`. Tests: `bbsengine6/py/tests/test_transport_multibind.py`
(10) + `bed/src/bed/tests/test_bed.py::TestBindMulti` (15) +
`TestBindMultiStart` (5).

### 6.7 v2.0 — Multi-Instance Load Balancing

**Path A (minimum viable, "no interactive password prompt on rebalance"):**
- A1: shared signing key (`--bed-secret-source {file,env}`)
- A2: softened `bed_instance_id` check (`auth.allow_cross_instance_reconnect`)
- A3: shared token store (DB required) — `--token-persistence=db` becomes mandatory
- A4: cross-node reconnect handling

**Path B (full shared state):**
- DB-backed `SessionRegistry`
- Per-connection UUID (replace process-local `id(websocket)`)
- `next_request_id` becomes `SELECT FOR UPDATE`

**Prerequisites:** v1.0–v1.5 must be stable. Path A → Path B progression with sticky-sessions check first.

**Detailed design:** see `bed/docs/BED_AUTH.md` v2 Roadmap section.

### 6.8 Implementation order (per `bed/TODO.md:951-963`)

The original 12-step implementation order from `bed/TODO.md` (note: items 1–3 are complete; items 4–12 are open):

1. `bed/api/auth.py` + `bed/api/token_store.py` (in-memory) + `bed/api/credential_provider.py` protocol — **DONE**
2. `bed.main.BED.start` wires `AuthService` first; `DefaultRouter` keeps its stub `auth` — **DONE**
3. Tests: `bed/tests/test_auth_service.py` (issue/validate/expire/refresh/revoke/replay/cross-instance) — **DONE**
4. `bed/api/echo.py` + `bed/api/fragment.py` + `bed/api/style.py` for the `echo` / `echo_ack` push channel — open (Phase v1.4)
5. `bed/api/menu.py` + `bed/api/menu_validator.py` + `bed/api/menu_timeout.py` for the `menu` envelope (positional + unconditional kwargs from `inputchoice`) — open (Phase v1.3)
6. `bed/api/help.py` for the `help` / `help_result` / `help_error` round-trip (F1, per-menu help pulled on demand) — open (Phase v1.3)
7. `bed/api/key_f2.py` + `bed/api/key_f2_channels.py` + `bed/api/key_f2_items.py` for the `key_f2` / `key_f2_result` / `key_f2_empty` / `key_f2_error` round-trip (F2, session-level new-messages query) — open (Phase v1.3)
8. Wire the `menu` + `help` + `key_f2` pieces into casino (primary menu driver; primary F2 driver for tournament announcements) — open (Phase v1.3)
9. Wire into murdermotel (primary callable-`help` driver; primary F2 driver for motel events) — open (Phase v1.3)
10. Wire into empyre (primary echo / listbox / inputstring driver) — open (Phase v1.4)
11. Wire into mistermcfeely, zoid6, bank service — same `CredentialProvider` swap, no protocol changes — open (Phase v1.3)
12. (Optional) DB-backed `TokenStore` for `--token-persistence=db` — open

### 6.8 Session plan: post-bring-up cleanup (2026-06-28, per `bed/TODO.md:2118-2250`)

The bed+casino bring-up session on 2026-06-28 discovered four pre-existing bugs and one missing feature across the zoid6, bed, and casino repos. The bring-up used workarounds that are no longer needed once the fixes land.

**Bugs and status:**

| # | Item | Status | Fix path |
|---|---|---|---|
| 1 | `casino/dal/player.py:92` crashes on `NULL` credits | **FIXED** `casino/03be20c` | `read NULL credits as 0 in get_player_balance and place_bet`. Casino TODO rewritten in `casino/8b3417e` to focus on per-hand money-flow rework |
| 2 | `zoid6/src/zoid6/data/bed.json` `bank` and `channel` `modulepath`s do not resolve | open | `bank.modulepath` → `bbsengine6.bank.api.handler`; `channel.enabled` → `false` (channel service does not exist yet) |
| 3 | `bed/src/bed/main.py:101` `_apply_auth_config` tilde bug | open | wrap the JSON value in `os.path.expanduser` at three call sites (`_apply_bind_config`, `_apply_database_config`, `_apply_auth_config`) |
| 4 | `zoid6/src/zoid6/api/handler.py:13` `MessageRouter` does not register `list_services` | open | add a `ListServicesService` class, register it first in `MessageRouter.register_all` |
| 5 | `bed` `--pidfile` CLI arg exists but is never written | open | write the pidfile at the top of `bed/src/bed/main.py:main_async`, remove it in a `try/finally` around the autorestart loop |

**Execution plan (7 commits across 4 repos):**

1. **`bed`**: tilde fix (3 lines in `_apply_*_config` + regression tests in `bed/src/bed/tests/test_bed.py::TestConfigFlag`)
2. **`zoid6`**: `list_services` handler (1 new class + 1 new method + 1 test in `zoid6/src/zoid6/tests/test_bed_startup.py`)
3. **`zoid6`**: bank/channel JSON fix (2 lines in `zoid6/src/zoid6/data/bed.json` + assertion list update in `zoid6/src/zoid6/tests/test_config.py`)
4. **`casino`**: delete the superseded `--pidfile` entry in the `## BED (BBS Engine Daemon) Improvements` section (no code change in casino)
5. **`bed`**: add `## \`--pidfile\` PID file management` section to `bed/TODO.md`
6. **`bed`**: pidfile lifecycle implementation — `bed/src/bed/main.py:main_async` write/remove + `bed/src/bed/tests/test_bed.py::TestPidfile` + `bed/tests/scripts/stop_bed.sh` + `bed/README.md` "PID file" subsection
7. **End-to-end verification**: re-run `pytest zoid6/src/zoid6/tests/ -q` and `pytest bed/src/bed/tests/ -q` in `/home/opencode/data/work/.venv312`; restart bed with `--pidfile /tmp/bed-test.pid --bed-secret /home/opencode/.config/bed/bed.secret` and verify the pidfile lifecycle, the 56+ message types from `list_services`, the absence of `Failed to import bank` / `Failed to import channel` lines, and that `--bed-secret` is honored

**Pre-execution cleanup (Step 0):** Before any commit lands, four bed processes were racing on `127.0.0.1:8765` via `SO_REUSEPORT` (pids 3668500, 3797060, 3802229, 3803184). The user's prior approval covers `kill 3797060 3802229 3803184` (SIGTERM, graceful) to leave only pid 3668500 (the user's intended instance) listening. The signal handler at `bed/src/bed/main.py:370-373` calls `asyncio.create_task(bed.stop())` on SIGTERM, which awaits `self.server.stop()` and releases the port.

---

## 7. v2 — Future Plans

| Feature | Source | v? |
|---|---|---|
| Multi-instance auth Path A | `bed/docs/BED_AUTH.md` v2 | v2.1 |
| Multi-instance auth Path B | `bed/docs/BED_AUTH.md` v2 | v2.2 |
| Per-game style palettes (echo/menu) | `bed/TODO.md` Future | v2 |
| `menu_multi` primitive (e.g. casino "select lucky numbers") | `bed/TODO.md` Section 707 | v2 |
| `key_f2` paging via `listbox` envelope | `bed/TODO.md` Section 880 | v2 |
| Per-channel `key_f2_priority` | `bed/TODO.md` Section 882 | v2 |
| Tilde fix Option C (sentinel-based explicit-set detection) | `bed/TODO.md` Section 1763 | v2 |
| WebSocket socket options (linger tuning) | `bed/TODO.md` Section 2083 | v2 |
| Bed auto-reconnect on disconnect (BBS side) | `bbsengine6/TODO.md` "Bed Disconnect" | v2+ (engine-side) |
| `Type=notify` systemd readiness signaling | `bed/TODO.md:1599-1612` | v2 | Add `sd_notify("READY=1")` in `bed.main.main_async` after `await self.server.start()`; requires soft dep on `systemd` PyPI package |
| Blurb replies (DB-backed) | `bbsengine6/TODO_BLURBS.md` | v2+ (engine-side) |
| Per-member PG roles RLS follow-up | `bbsengine6/TODO_RLS.md` | v2+ (engine-side) |
| SQL filename convention refactor | `bbsengine6/TODO-sql-filenames.md` | v2+ (engine-side) |
| GPG key support for message signing | `bbsengine6/TODO.md` Section 657 | v2+ (engine-side) |
| Postoffice IMAP poller (bed.json ships; poller pending) | `bbsengine6/TODO.md` Phase 1G | v2+ |

---

## 8. bbsengine6-side Gaps (prerequisites)

Each bed service depends on certain bbsengine6 pieces. This table makes the dependencies explicit so readers know what blocks each bed service.

| Bed service / feature | Needs from bbsengine6 | Status | Source |
|---|---|---|---|
| `AuthService` | `bbsengine6.member.{checkpassword,issysop,getcredits,moniker_exists}` | ✅ done | n/a |
| `MessageService` | `bbsengine6.net.transport.send_to` (`:699-701`) | ✅ done | `bbsengine6/TODO-message-migration.md` Phase 8 |
| `MessageService` | `bbsengine6.message.{get,set,bump,clear}_local_unread_count` | ✅ done | n/a |
| `MessageService` | `engine.__message_recipient` table + PG NOTIFY trigger | ✅ done | `bbsengine6/TODO-message-migration.md` Phase 2 |
| `bed.client.*` | `bbsengine6.net.WebSocketServer` | ✅ done | n/a |
| `bed.api.session.SessionRegistry` | `bbsengine6.session.core.SessionManager` base class | ✅ done | (migrated Section 4) |
| Per-connection bottombar | `bbsengine6.bottombar.registry_for(name)`, `set_context_for`, `render_for`, `set_active_registry`, `reset_active_registry`, `_active_registry` ContextVar | ✅ done (Phase 4a, 2026-07-22) | `bbsengine6/TODO-BOTTOMBAR.md` |
| `MenuService` (inputchoice semantics) | `bbsengine6.io.inputchoice` (already exists) | ✅ done | n/a |
| `BEDSink` | `bbsengine6.io.sink.Sink` protocol + `set_io_sink`/`reset_io_sink` | ❌ Phase 0 pending | `bbsengine6/TODO.md` Section 1216-1567 |
| `BEDSink` | `bbsengine6.net.router.MessageRouterMixin` | ✅ done (Phase 5) | (migrated Section 4) |
| `BEDSink` | `WebSocketServer.on_connect_hook` | ❌ Phase 5 partial pending | `bbsengine6/TODO.md` Section 1535 |
| `ThinClientIOSink.echo` text | `bbsengine6.io.echo_render` | ❌ Phase 1 pending | `bbsengine6/TODO.md` Section 1234 |
| `ThinClientIOSink.echo` mci | `bbsengine6.io.mci.parse` | ❌ Phase 2 pending | `bbsengine6/TODO.md` Section 1275 |
| `BEDSink.echo` | `bbsengine6.io.echo` returns rendered string (currently no return value) | ❌ Phase 3 pending | `bbsengine6/TODO.md` Section 1346 |
| `BEDSink` for primitives | Sink-based variants for inputstring/integer/boolean/etc. | ❌ Phase 4 pending | `bbsengine6/TODO.md` Section 1400 |
| `EchoService` MCI codec | MCI codec must be strict superset of `bbsengine6.io.echo` tokenizer | ❌ Phase 1+2 pending | `bed/TODO.md:226,260,262` |
| `key_f2` | `bbsengine6.io.bottombar` per-conn routing | ✅ done (Phase 4a) | n/a |
| `auth` messages | `bbsengine6.member.checkpassword` | ✅ done | n/a |
| `defaultrouter` bank | `bbsengine6.bank.api.handler.BankServiceHandler` | ✅ done | n/a |
| `defaultrouter` session | `bbsengine6.bank.api.handler.SessionManager` | ✅ done | n/a |
| `BBSENGINE6_NOTIFYD_*.md` | (engine-side cleanup of dead specs) | ❌ pending | n/a |
| `MonikerAuthRouter` | `bbsengine6.member.moniker_exists` | ✅ done | n/a |

**Summary:** 14 of 19 bbsengine6 prerequisites are ✅ done. The 5 open ones are all in the **Sink Infrastructure** series (Phases 0–4 of `bbsengine6/TODO.md` Section 1216-1567). Once those land, v1.2 (Sink Infrastructure Adoption) can proceed.

---

## 9. Cross-Reference Map

### 9.1 Bed → bbsengine6 dependencies

| Bed symbol | bbsengine6 symbol | Location |
|---|---|---|
| `bed.main.BED` | `bbsengine6.net.WebSocketServer` | `bbsengine6/net/transport.py` |
| `bed.api.auth.AuthService` | `bbsengine6.member.checkpassword`, `.issysop`, `.getcredits` | `bbsengine6/member/lib.py` |
| `bed.api.credential_provider.MonikerOnlyCredentialProvider` | `bbsengine6.member.moniker_exists` | `bbsengine6/member/lib.py` |
| `bed.api.message.MessageService` | `bbsengine6.net.transport.send_to` | `bbsengine6/net/transport.py:699-701` |
| `bed.api.message.MessageService` | `bbsengine6.message.get_pending_messages` | `bbsengine6/message.py` |
| `bed.api.session.SessionRegistry` | `bbsengine6.session.core.SessionManager` | `bbsengine6/session/core.py` |
| `bed.client.messageservice.BedMessageServiceClient` | `bbsengine6.message.{get,set,bump,clear}_local_unread_count` | `bbsengine6/message.py` |
| `bed.defaultrouter.DefaultRouter` | `bbsengine6.bank.api.handler.BankServiceHandler` | `bbsengine6/bank/api/handler.py` |
| `bed.defaultrouter.DefaultRouter` | `bbsengine6.bank.api.handler.SessionManager` | `bbsengine6/bank/api/handler.py` |
| `bed.startup.ensure_startup` | `bbsengine6.startup.lib.runmodule` | `bbsengine6/startup/lib.py` |
| `bed.main` | `bbsengine6.database.{getpool,set_current_role,make_dsn,connect,cursor,buildargs}` | `bbsengine6/database.py` |
| `bed.main` | `bbsengine6.io.{echo,echo_traceback,inputinteger,inputstring,inputchoice}` | `bbsengine6/io/` |
| `bed.api.handler.SessionManager` (re-export) | `bbsengine6.session.SessionManager` | `bbsengine6/session/` |
| `bed.lib` (default --router) | `bbsengine6.net.defaultrouter.DefaultRouter` | `bbsengine6/net/defaultrouter.py` |
| `zoid6.api.handler.MonikerAuthRouter` | `bbsengine6.member.moniker_exists` | `zoid6/api/handler.py` |
| `bed.tests.test_bed` | `bbsengine6.module.is_importable` | `bbsengine6/module.py` |

### 9.2 Bed → game adoption

| Bed symbol | Game consumer | Game file |
|---|---|---|
| `bed.api.auth.AuthService` | zoid6 (MonikerAuthRouter) | `zoid6/api/handler.py` |
| `bed.client.connection.BedConnection` | empyre, casino, murdermotel, mistermcfeely, zoid6 | (per-game clients) |
| `bed.client.bank.BedBankClient` | empyre, casino | `empyre.bed_client`, `casino/services/bank_client.py` |
| `bed.client.messageservice.BedMessageServiceClient` | empyre (planned), casino (planned) | `empyre/services/player.py:56-109`, `casino/services/bank_client.py` |
| Future `bed.api.menu.MenuService` | casino (primary), murdermotel, empyre, zoid6, mistermcfeely, bbsengine6 bank | per-game `inputchoice` call sites |
| Future `bed.api.help.HelpService` | murdermotel (primary: lobby.py:74, play.py:446, rabidwolf.py:531) | `murdermotel/{lobby,play,rabidwolf}.py` |
| Future `bed.api.key_f2.KeyF2Service` | murdermotel, empyre, casino, mistermcfeely/postoffice, zoid6 | per-game |
| Future `bed.api.echo.EchoService` | empyre (Phase 1), casino, mistermcfeely/postoffice, murdermotel, zoid6, bbsengine6 bank | per-game |
| Future `bed.sinks.BEDSink` | all (server-side) | installed by bed |
| Future `bed.client.io_sink.ThinClientIOSink` | all (client-side) | `bed/client/` |

### 9.3 bbsengine6 → bed (consumer relationships)

| bbsengine6 symbol | Consumed by bed | Where in bed |
|---|---|---|
| `net.WebSocketServer.on_connect_hook` (planned) | `BEDSink` install | `bed/main.py` (planned v1.2) |
| `io.sink.Sink` (planned) | `BEDSink`, `ThinClientIOSink` | `bed/sinks/`, `bed/client/` |
| `io.echo_render` (planned) | `BEDSink.echo` text field | `bed/sinks/bed_sink.py` |
| `io.mci.parse` (planned) | `BEDSink.echo` mci field | `bed/sinks/bed_sink.py` |
| `bottombar.registry_for(name)` | per-conn registry on connect | `bed/main.py` (planned v1.2) |
| `bottombar.set_active_registry(reg)` | per-conn routing | `bed/main.py` (planned v1.2) |
| `bottombar.reset_active_registry(token)` | per-conn cleanup | `bed/main.py` (planned v1.2) |
| `net.router.MessageRouterMixin` | `BEDSink` request/response plumbing | `bed/sinks/bed_sink.py` |

### 9.4 Quick-link index of all referenced files

**bed/ (this repo):**
- `README.md`, `CHANGELOG.md`, `FHS.md`, `TODO.md`, `TODO-message-service.md`
- `docs/BED_AUTH.md`
- `src/bed/__init__.py`, `_version.py`, `main.py`, `lib.py`, `config.py`, `defaultrouter.py`, `startup.py`
- `src/bed/api/`: `__init__.py`, `auth.py`, `credential_provider.py`, `errors.py`, `handler.py`, `message.py`, `secret.py`, `session.py`, `token_store.py`
- `src/bed/client/`: `__init__.py`, `bank.py`, `connection.py`, `exceptions.py`, `messages.py`, `messageservice.py`, `probe.py`, `singleton.py`
- `src/bed/daemon/`: `bed.service`, `bed.sysusers`, `bed.tmpfiles`
- `src/bed/data/`: `bed.json`, `sql/bed_token.sql`
- `src/bed/tests/`: `test_auth_service.py`, `test_bed.py`, `test_message_service.py`, `test_startup.py`
- `src/bed/tools/`: `bank.py`, `ping.py`
- `tests/scripts/`: `stop_bed.sh`
- `usr/share/factory/etc/bed/`: `bed.json`, `bed.env`
- `Makefile`, `pyproject.toml`

**bbsengine6/ (sibling repo):**
- `README.md`, `NOTES.md`, `router.md`
- `TODO.md`, `TODO-BOTTOMBAR.md`, `TODO-message-migration.md`, `TODO-notify.md` (SUPERSEDED), `TODO-notify-encryption.md` (SUPERSEDED), `TODO_BACKEND.md`, `TODO_BLURBS.md`, `TODO_RLS.md`, `TODO-sql-filenames.md`
- `handbook/`: `index.md`, `QUICKSTART.md`, `SETUP.md`, `PRODUCTION_DEPLOYMENT.md`, `SECURITY.md`, `database.md`, `listbox.md`, `module.md`, `util.md`, `blurb_demo.md`, `SMOOTHSTATE.md`, `ROUTER.md`, `JSON_HANDLING_GUIDE.md`, `WEBSOCKET_REALTIME_PLAN.md`, `RUNTIME_CONVERSION.md`, `BBSENGINE6_PHP_SPL.md`, `APACHE_*.md`, `NOTIFY_*.md` (historical), `NOTIFY_TESTING.md`, `README_NOTIFY.md`, `NET_LAYER_GUIDE.md`
- `handbook/specs/`: `index.md`, `architecture.md`, `dependencies.md`, `decisions.md`, `flows.md`, `BESTPRACTICE.md`, `console.md`, `database.md`, `web.md`, `modules.md`, `listbox.md`, `module.md`, `md2tpl.md`, `notify.md`, `NOTIFY_MESSAGING.md` (historical), `BLURB_SPEC.md`, `blurb.md`, `FOLDER_SPEC.md`, `NET_LAYER_SPEC.md`, `member.md`, `util.md`, `pg-ident-auth.md`, `bottombar.md`, plus 10 `BBSENGINE6_NOTIFYD_*.md` (all SUPERSEDED)
- `handbook/csrf/`: 10 files (CSRF implementation + zoid6 audit)
- `py/src/bbsengine6/`: `bed.py` (shim), `bottombar.py`, `message.py`, `module.py`, `database.py`, `engine.py`, `net/` (transport, router, defaultrouter, integration, registry, address), `bank/`, `member/`, `io/`, `session/`, `startup/`, `backend/`, `console/`, `examples/`, `dist/`
- `py/src/bbsengine6/tests/`: many test files including `test_message_lib.py`, `test_bottombar.py`, `test_screen.py`, `test_net_frames/`

**Per-game repos (external, referenced by bed/TODO.md):**
- empyre (`empyre/TODO.md`, `empyre/src/empyre/services/player.py`, `empyre/src/empyre/lib.py`, `empyre/src/empyre/io_bridge.py`, many town/combat/maint files)
- casino (`casino/TODO.md`, `casino/src/casino/{services,lobby,api,games,lib}.py`)
- murdermotel (`murdermotel/TODO.md`, `murdermotel/{lobby,play,rabidwolf}.py`)
- mistermcfeely/postoffice (`mistermcfeely/TODO.md`, `mistermcfeely/src/postoffice/`)
- zoid6 (`zoid6/TODO.md`, `zoid6/src/zoid6/{api/handler.py, data/bed.json, pyproject.toml, tests/}`)
- achilles, vulcan, socrates, letteredolive, moneyday, rgs, mhc, teos, zoidoffice (no TODO.md yet per `bed/TODO.md:316-324`)

---

## 10. Authoritative File Index

### 10.1 bed/ files

| File | Role | Size |
|---|---|---|
| `bed/README.md` | Quick-start, CLI flags, config layout, tests | 8,687 B |
| `bed/CHANGELOG.md` | Reverse-chronological history since extraction | 8,798 B |
| `bed/FHS.md` | FHS/UAPI compliance design | 3,625 B |
| `bed/TODO.md` | Line-numbered open work + cross-references | 106,229 B |
| `bed/TODO-message-service.md` | Phased plan for server-push notifications | 7,209 B |
| `bed/docs/BED_AUTH.md` | Bearer-token auth wire protocol + v2 design | n/a (401 lines) |
| **`bed/SPEC.md`** | **This file — entry-point spec** | n/a |

### 10.2 bbsengine6/ files referenced

See `handbook/specs/index.md` for the full spec index. Critical specs:

| File | Status |
|---|---|
| `handbook/specs/architecture.md` | Authoritative |
| `handbook/specs/flows.md` | Authoritative |
| `handbook/specs/decisions.md` | Authoritative |
| `handbook/specs/database.md` | Authoritative |
| `handbook/specs/NET_LAYER_SPEC.md` | Authoritative for net layer (47 tests) |
| `handbook/specs/bottombar.md` | Authoritative for bottombar |
| `handbook/specs/member.md` | Authoritative for member/recipient validation (survivors of notify→message migration) |
| `handbook/specs/NOTIFY_MESSAGING.md` | HISTORICAL (survived from notify→message migration) |
| `handbook/specs/notify.md` | Authoritative for current message system |
| `handbook/specs/BBSENGINE6_NOTIFYD_*.md` (10 files) | **ALL SUPERSEDED 2026-07-22 — do not implement** |

### 10.3 Per-game repos referenced

| App | Repo / path | Status | Benefits from `bed` |
|---|---|---|---|
| **empyre** | `/home/opencode/data/work/empyre` | Has TODO.md, Phase 0a references `AuthService` | **Primary driver.** Thin-client BED conversion; reconnect across network blips and `bed` restarts is critical for long empyre sessions (turns can take minutes) |
| **casino** | `/home/opencode/data/work/casino` | Has TODO.md | **Strong fit.** Lobby browsing, spectator mode, multi-table clients, bot accounts |
| **mistermcfeely** | `/home/opencode/data/work/mistermcfeely` | Has TODO.md | **Strong fit.** Token-bounded IMAP-style sessions, mail-client reconnection without re-entering IMAP password |
| **murdermotel** | `/home/opencode/data/work/murdermotel` | Has TODO.md | **Strong fit.** Long-lived investigation/investment sessions; reconnect mid-night without re-login |
| **zoid6** | `/home/opencode/data/work/zoid6` | Has TODO.md | **Strong fit.** Dashboard / shared-wallet / chat clients that stay open for hours |
| **postoffice** (in mistermcfeely) | `/home/opencode/data/work/mistermcfeely` | Has TODO.md | **Strong fit.** Mail client holds a token instead of an IMAP password |
| **bank service** | `bbsengine6.bank.api.handler.MessageRouter` | Tracked in `bbsengine6/TODO.md` Phase 7.2.1 | **Strong fit.** Financial clients should not re-send credentials on reconnect |
| **BBS door mode (legacy TUI)** | n/a | n/a | Not affected — door mode is host-driven, no token involved |
| achilles, vulcan, socrates, letteredolive, moneyday, rgs, mhc, teos, zoidoffice | (per-game repos) | No TODO.md yet | Will benefit once the game grows a network surface. Add cross-reference when one is created |

**Cross-reference convention:** When `bed`'s `AuthService` was implemented, each game repo with a `TODO.md` (casino, mistermcfeely, murdermotel, zoid6) got a one-line note: *"See `bed/TODO.md` 'Bearer token' — adopt `bed.api.auth.AuthService` for BED-mode authentication and reconnect. Replaces per-game `auth` implementations."* Empyre already references it as Phase 0a.

---

## 11. Out of Scope / Known Limitations

### 11.1 Bed-side

- **No bed auto-reconnect on disconnect (BBS side).** When the bed WebSocket disconnects, the BBS side does not auto-reconnect. This is a `bed`-package limitation documented in `bbsengine6/TODO.md` "Bed Disconnect: No Auto-Reconnect (Known Limitation, 2026-07-22)". Sites: `bed/src/bed/client/connection.py:252-273`, `bed/src/bed/client/messageservice.py:23-95`, `bed/src/bed/api/auth.py:236-296`.
- **FHS default-config path drift.** Resolved in two steps. Step 1 (`8124105`) made `--config` `required=True` so `/etc/bed/bed.json` typos fail loudly. Step 2 (this release) made `--config` optional and added `bed/_configpath.resolve_config_path()` so `bed` (no flags) walks `$BED_CONFIG` → `/etc/bed/bed.json` if present → the packaged default. FHS hosts continue to ship `/etc/bed/bed.json` via `install-etc`; the fallback only fires when the operator has not run `install-etc`. See `bed/FHS.md` "## Default config path".
- **Linger tuning deferred.** WebSocket socket options (linger, keep-alive) are deferred to a future `## WebSocket socket options` section. `bed/TODO.md:2083`.
- **Two-Makefile consolidation pending.** `bed/Makefile` and `bed/src/Makefile` differ in OUTDIR (`../dist/` vs `/srv/repo/bed/`); entry points and version label names need to be standardized. `bed/src/Makefile:50-51`.
- **Single-instance auth only in v1.** Multi-instance load balancing (shared signing key, cross-node reconnect, shared session registry) is v2 design-only. See `bed/docs/BED_AUTH.md` v2 Roadmap.
- **End-to-end DB LISTEN tests deferred.** Require a live PostgreSQL; not run in CI.
- **No `--no-bed-fallback` flag for TUI.** The bbsengine6 TUI still has a DB-poll fallback path; planned removal is in `bed/TODO-message-service.md` Phase 9.
- **`bed/Makefile` `--autorestart` default mismatch.** CHANGELOG entry 66f389f fixed the docstring (default is False); README may still need re-read.

### 11.2 Engine-side (affects bed)

- **10 `BBSENGINE6_NOTIFYD_*.md` specs are dead.** All marked SUPERSEDED 2026-07-22. The 193 claimed tests, EventBus, daemon process, systemd unit, and CLI do not exist. The closest replacement is the Postoffice IMAP poller in `bbsengine6/TODO.md` Phase 1G — ships `bed.json` only; actual poller still pending.
- **Sink infrastructure Phases 0–4 are pending.** Without these, `BEDSink` + `ThinClientIOSink` cannot be built. See Section 8.
- **Blurb replies, GPG signing, RLS, SQL filenames, per-member PG roles, bank-as-services** — all bbsengine6-side work that bed does not depend on but may want to be aware of for downstream features.

### 11.3 Migration: notify → message

The `bbsengine6.notify` package was deleted in Phase 7 of `bbsengine6/TODO-message-migration.md` (2026-07-22). Survivors:

- `bbsengine6.member.moniker_exists`
- `bbsengine6.member.group_exists`
- `bbsengine6.member.get_group_members`

Current spec: `handbook/specs/member.md` "Recipient Validation & Group Management (v1.0)". Bed stays untouched by this migration per `bbsengine6/TODO-message-migration.md:209-211` ("`bed` is a WebSocket daemon with no terminal. It does not touch notifications today.").

---

## Appendix A: Versioned v1 Decisions (extracted from `bed/TODO.md`)

For convenience, the explicit v1-default decisions scattered across `bed/TODO.md`:

### Bearer token v1 (lines 94–97)
- In-memory token store (default)
- 15-min TTL
- HMAC-SHA256
- Token bound to `websocket_id`
- `bed_instance_id` mismatch → reject + force re-`auth`
- Keep `DefaultRouter` stub `auth` working for development
- Logout on TCP-reset does NOT invalidate token
- **Future:** shared-secret across `bed` instances behind a load balancer (v2)

### Echo v1 (lines 271–284)
- At-least-once with reconnect-resume
- In-order per stream
- One outstanding `flush:true` per session
- `echo`/`echo_ack` mandatory
- `flush:false` echoes may be dropped under memory pressure
- `payload.mci` round-trips through codec
- 30s default `ack_timeout`
- `bed.json` key `echo.ack_timeout`
- v1: thin client renders `text` only; server pre-transcodes if it cares
- **Future:** per-game style palettes

### Menu v1 (lines 681–716)
- `options` is uppercase single-char string
- `default` is hotkey on Enter/timeout
- `noneok=true` + Enter → `menu_reply{noneok_picked:true}`
- Envelope does NOT include `help` or `f2_handler`
- `help` invoked on demand
- Help rate-limited at 10 req/s
- `timeout` is server-side via asyncio.Timer
- `rewriteprompt=true` does the one-shot substitution
- No multi-pick
- `menu_cancel` after late `menu_reply` is silent no-op
- Thin client never sends `cancelled:true`
- **Future:** per-game style palettes, `menu_multi` primitive

### KeyF2 v1 (lines 866–879)
- Queries all subscribed channels with `key_f2_visible=true`
- Optionally restricted by `key_f2.channel_allow_list`
- Result capped at `key_f2.max_items` (default 50)
- Requires auth
- Rate-limited at 5 req/s
- Does not clear unread state
- **Future:** wrap result in `listbox` envelope for paging; per-channel `key_f2_priority`

### Sink integration v1 (lines 1121–1138)
- `BEDSink` installed via `WebSocketServer.on_connect_hook` (option e)
- `BEDSink` does not own the message loop
- Thin-client `IOSink` lives in `bed/client/io_sink.py`
- `sys.modules` swap continues to work as v1 default
- `BEDSink.echo` populates `text` via `bbsengine6.io.echo_render` and `mci` via `bbsengine6.io.mci.parse`

### Tilde fix v1 (line 1763–1769)
- Apply Option D (expand `~` in `_apply_*_config`)
- Apply to `_apply_auth_config`, `_apply_bind_config`, `_apply_database_config`
- Add regression tests in `TestConfigFlag`
- **Future:** Option C (sentinel-based detection)

### SIGTERM/SIGKILL v1 (line 2070–2085)
- Promote stale-pidfile detection (Option A) into the pidfile commit: `kill -0` + `O_EXCL`
- `stop_bed.sh` follows SIGTERM-then-SIGKILL with process-group kill
- Document `--token-persistence=db` as recommended production default
- **Deferred:** linger tuning (Option B) to future `## WebSocket socket options` section

---

## Appendix B: Cross-Reference Convention

Every open item in `bed/TODO.md` ends with a `## Cross-references` block. When reading that file:

- Exact `bed/src/bed/main.py` line numbers are cited for the affected code.
- Exact `bed/src/bed/lib.py` line numbers are cited for the affected CLI argument.
- `bed/src/bed/tests/test_bed.py::TestConfigFlag` / `TestPidfile` class names are cited for the test harness.
- Corresponding section names in `zoid6/TODO.md`, `casino/TODO.md`, `murdermotel/TODO.md`, `mistermcfeely/TODO.md`, `bbsengine6/TODO.md` are cited for cross-repo impact.
- `bbsengine6/notify/daemon/daemon.py:118-119, 162` is the reference SIGTERM/SIGINT pattern.

This spec preserves that convention at the file level (Section 10) and section level (Section 3, Section 4, Section 5, Section 6, Section 7, Section 8, Section 9).
