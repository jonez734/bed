# bed — BBS Engine Daemon

A small WebSocket daemon that sits in front of a `bbsengine6` game router
(empyre, casino, mistermcfeely, murdermotel, zoid6, …), terminates
JSON-over-WebSocket, and lets the game own the wire protocol.

> **See [`SPEC.md`](SPEC.md)** for the entry-point specification: what
> works now, what doesn't, future plans beyond v1.0, the v1/v1.1/v1.2/v1.3/v1.4/v2
> phase gates, code that moved from `bed` to `bbsengine6`, and the
> bbsengine6-side prerequisites for each bed service.
>
> This README is a quick-start. `SPEC.md` is the spec. `TODO.md` is the
> line-numbered open work. `docs/BED_AUTH.md` is the bearer-token auth
> wire protocol. `CHANGELOG.md` is the history. `FHS.md` is the FHS/UAPI
> design.

## Quick start

```bash
pip install -e .
# --config is REQUIRED — there is no fallback search.
bed --config /etc/bed/bed.json --router zoid6.api.handler.MonikerAuthRouter
```

For development without an FHS install, point `--config` at the packaged
default:

```bash
bed --config "$(python -c 'import bed.data, os; print(os.path.dirname(bed.data.__file__) + "/bed.json")')" \
    --router zoid6.api.handler.MonikerAuthRouter
```

The `zoid6` console script (from the [`zoid6`](../zoid6/) package)
automatically resolves `/etc/zoid6/bed.json` → packaged default if no
override is set, so most callers want:

```bash
pip install -e . -e ../bbsengine6/py -e ../zoid6/src
zoid6
```

### Database setup

Bed bootstraps the database automatically on daemon start (since the
`008b9d1` commit). If you want to run the bootstrap standalone, or
audit the schema without starting the daemon:

```bash
bed-startup
# or: python -m bed.startup
```

`bed-startup` runs `bbsengine6.startup` first (creating the `engine`
schema, core roles `member`/`web`/`sysop`/`term`, and all SECURITY
DEFINER functions), then creates the `bed` role with LOGIN and grants
it USAGE on the `engine` schema.  The role is idempotent — re-running
after the role already exists is a no-op.

### systemd service (per-service venv)

The system Python may be too new for `bed`'s requirement (`>=3.9,<3.13`).
`bed` owns a per-service venv at `/var/lib/bed/venv` (owned by `bed:bed`).
Consumers of bed (`zoid6`, games) own their own venvs and install the bed
wheel into theirs via `pip install`. The dep direction is `zoid6 → bed`;
bed does not depend on zoid6.

One-command install:

```bash
cd /path/to/bed && sudo make install && sudo systemctl enable --now bed
```

Install the systemd unit and start the service:

```bash
cd /path/to/bed/src && sudo make install-systemd && sudo systemctl restart bed
```

#### Prerequisites

`make install-venv` (called by `make install`) requires `build`,
`setuptools`, and `wheel` in the invoking user's pip environment:

```bash
pip install --user build setuptools wheel
```

It also expects local sibling repos (`../bbsengine6/py`, `../getdate_next`)
and builds wheels for all three into `/tmp` so they can be installed into the
shared venv via `sudo -u $(VENV_OWNER) $(VENV_DIR)/bin/pip install` (the venv
owner may not have access to the source tree).

#### SELinux

On systems with SELinux enforcing (Fedora, RHEL, CentOS), the venv binaries
under `/var/lib/bed/venv/bin/` get labeled `var_lib_t` by default.
systemd cannot execute scripts with this context — it causes a 203/EXEC error.

`make install-venv` adds a `semanage` rule and runs `restorecon` automatically
when available. If you install manually, run:

```bash
sudo semanage fcontext -a -t bin_t "/var/lib/bed/venv/bin(/.*)?"
sudo restorecon -R /var/lib/bed/venv/bin/
```

Without `semanage`, `restorecon` will restore the default `var_lib_t` label
and the 203 error will persist.

#### One-command install

```bash
cd /path/to/bed && sudo make install && sudo systemctl enable --now bed
```

This chains: `install-sysusers` → `install-tmpfiles` → `install-venv` →
`install-systemd` → `install-etc`.

The unit at `src/bed/daemon/bed.service` runs as `User=bed` and uses a
templated `ExecStart=@VENV_DIR@/bin/bed --config /etc/bed/bed.json`
(`install-systemd` substitutes `$(VENV_DIR)` → `/var/lib/bed/venv`).
bed does not share this venv with any other service; any consumer that
needs `import bed` installs the bed wheel into its own per-service venv.

In another terminal:

```bash
wscat -c ws://127.0.0.1:8765
> {"type":"auth","moniker":"alice","password":"…"}
< {"type":"auth_result","success":true,"moniker":"alice",…,
   "token":"…","session_id":"…","expires_at":"…","balance":0}
```

Or use the `ping` console script for a no-credential smoke test:

```bash
pip install -e .
ping --url ws://127.0.0.1:8765
```

The `bank` console script provides a standalone CLI for balance, add,
remove, history, transfer request/approve/reject, and list-all.

## Routers

| FQCN                                                | behavior                                               |
|-----------------------------------------------------|--------------------------------------------------------|
| `bbsengine6.net.defaultrouter.DefaultRouter`        | no-credential stub; wscat / development                |
| `bed.defaultrouter.DefaultRouter`                   | bank + auth services                                   |
| `zoid6.api.handler.MonikerAuthRouter`               | verifies the moniker exists; any password accepted     |
| `zoid6.api.handler.MessageRouter`                   | full zoid6 unified router                              |
| any custom router                                   | your game; `bed` wires AuthService alongside          |

`bed` automatically registers `AuthService` (bearer tokens,
reconnect, refresh, revoke) before any non-`DefaultRouter` runs. When
the router is anything other than the bbsengine6 no-credential stub,
`MessageService` (server-push via PG `LISTEN`/`NOTIFY` on
`engine_message_recipient`) and `BankService` (bed-native empyre
shape: `bank_balance` / `bank_add` / `bank_remove` / `bank_history`)
are also auto-registered. Opt out with `--no-message-service` or
`--no-bank-service`. Using `bed.defaultrouter.DefaultRouter` exposes
the full 9-message bank surface (`bank_balance`, `bank_add`,
`bank_remove`, `bank_transfer_request`, `bank_transfer_approve`,
`bank_transfer_reject`, `bank_pending`, `bank_history`,
`bank_list_all`) via `bbsengine6.bank.BankServiceHandler`. See
[`docs/BED_AUTH.md`](docs/BED_AUTH.md) for the auth wire protocol, TTL
knobs, and threat model.

## CLI flags

```
--host HOST                default: 127.0.0.1
--port PORT                default: 8765
--router DOTTED.NAME       default: bbsengine6.net.defaultrouter.DefaultRouter
--config PATH              REQUIRED (no fallback search)
--bed-secret PATH          default: ~/.config/bed/bed.secret
--token-ttl SECONDS        default: 900
--token-persistence MODE   default: memory  (none | memory | db)
--credential-provider N    default: password  (password | moniker-only)
--bed-instance-id UUID     default: auto-generated, persisted with the secret
--autorestart              (default: from bed.json, or False)
--restart-delay N          default: 5
--max-restarts N           default: 10
--pidfile PATH
--foreground / -f
--debug
--no-message-service       Disable in-process MessageService (PG LISTEN/NOTIFY)
--no-bank-service          Disable in-process bed-native BankService
```

Run `bed --help` for the authoritative list.

## Configuration

`bed.json` (CLI > file > argparse default):

```json
{
  "bed":   {"autorestart": false, "restart_delay": 5, "max_restarts": 10},
  "auth":  {"bed_secret_path": "~/.config/bed/bed.secret",
            "token_ttl": 900, "token_persistence": "memory",
            "credential_provider": "password", "bed_instance_id": null},
  "database": {"name": "…", "host": "…", "port": 5432,
               "user": "bed", "password": "…"}
}
```

The shipped default config (`src/bed/data/bed.json`, byte-identical
to `usr/share/factory/etc/bed/bed.json`) disables `autorestart` so
that systemd owns the restart loop. Set it to `true` for
foreground-only deployments without systemd.

### `restart_on_bind_failure`

`bed` distinguishes two failure modes. When the listening port is
already in use (`EADDRINUSE`) or the bind is denied (`EACCES`,
e.g. trying to bind a privileged port without root), the daemon
exits with status **2** and the systemd unit
(`daemon/bed.service`) is configured with
`RestartPreventExitStatus=2` so it does **not** spin on that
condition — otherwise a stuck port would silently restart every
`RestartSec=5s` forever, indistinguishable from a healthy crash
loop.

If you want in-process retries on bind failures (e.g. you hold the
port from a sidecar that comes up later), set
`restart_on_bind_failure: true` in `bed.json` or pass
`--restart-on-bind-failure` on the command line. The retries honor
`restart_delay` and `max_restarts` exactly like the general
`autorestart` loop. Default is `false`.

### PID file

`bed --pidfile /var/run/bed.pid` writes the daemon's pid to
`/var/run/bed.pid` on startup and removes it on shutdown. The
pidfile lifetime matches the daemon's lifetime, not the per-
restart instance lifetime: autorestart keeps the same pid, so
the pidfile is never removed and re-created during a restart.

The systemd unit at `bed/src/bed/daemon/bed.service` does
**not** use `--pidfile` — systemd tracks the main pid
natively. `--pidfile` is for foreground / dev / test
invocations.

Test-cleanup recipe: `kill $(cat /tmp/bed.pid)`, or use the
helper at `bed/tests/scripts/stop_bed.sh <pidfile>` which
sends SIGTERM, waits up to 5 seconds, then escalates to
SIGKILL on the process group as a fallback. The 5s grace is
shorter than the systemd unit's 30s `TimeoutStopSec` because
tests should fail fast; the operator can override the
systemd unit if a longer grace is desired.

When `--pidfile` is set, a startup error (e.g. the path's
parent directory does not exist) logs a warning and
continues without the pidfile. The daemon does not refuse
to start over a missing pidfile.

If the pidfile already exists on startup:

- **Stale pid** (the recorded process is gone) — bed logs a
  warning and overwrites. A SIGKILL'd predecessor does not
  block the next start.
- **Live pid** (the recorded process is still running) — bed
  logs an error and exits with status 1. This prevents two
  bed processes from silently sharing a pidfile.

The pidfile is opened with `O_EXCL` and retried once on a
TOCTOU race with another start, so two concurrent
invocations cannot both believe they own the file.

## Layout

```
bed/
├── src/bed/
│   ├── api/                AuthService, TokenStore, SessionRegistry,
│   │                       CredentialProvider, error envelopes,
│   │                       secret loader, MessageService, BankService
│   ├── client/             BedConnection, BedBankClient,
│   │                       BedBankServiceClient, BedMessageClient,
│   │                       BedMessageServiceClient, probe, singleton
│   ├── daemon/
│   │   ├── bed.service     systemd unit file
│   │   ├── bed.sysusers    systemd-sysusers config (creates bed user/group)
│   │   └── bed.tmpfiles    systemd-tmpfiles config (/var/log/bed, /var/lib/bed)
│   ├── tools/              auth, bank, message, ping console-script CLIs
│   ├── data/
│   │   ├── bed.json        packaged default config
│   │   └── sql/bed_token.sql   optional DB token-store schema
│   ├── _version.py         auto-stamped by `make version`
│   ├── main.py             BED daemon entry point
│   ├── startup.py          database bootstrap (bbsengine6 startup + bed role)
│   ├── lib.py              argparse
│   ├── config.py           bed.json loader
│   ├── defaultrouter.py    DefaultRouter stub
│   └── tests/              pytest (~4,148 LOC across 6 modules)
│                          Router load uses `bbsengine6.module.load()`; tracebacks on failure are emitted via `io.echo_traceback()`.
├── docs/
│   └── BED_AUTH.md         bearer-token protocol reference
├── usr/
│   └── share/factory/etc/bed/
│       ├── bed.json        FHS factory default config
│       └── bed.env         FHS factory env file (BED_DATABASE_USER=bed)
├── tests/
│   └── scripts/
│       └── stop_bed.sh     SIGTERM→SIGKILL pidfile-driven stop helper
├── pyproject.toml
├── src/Makefile            thin shim; canonical at root
└── Makefile                root install chain
```

## Console scripts

`pip install .` registers five entry points:

| Script         | Module                  | Purpose                                       |
|----------------|-------------------------|-----------------------------------------------|
| `bed`          | `bed.main:main`         | the WebSocket daemon                          |
| `bed-startup`  | `bed.startup:main`      | standalone database bootstrap                 |
| `auth`         | `bed.tools.auth:main`   | standalone auth CLI (login, reconnect, refresh, revoke) |
| `bank`         | `bed.tools.bank:main`   | standalone bank CLI (balance, add, remove, history, transfer) |
| `message`      | `bed.tools.message:main` | standalone message CLI (subscribe, pending, send, mark read/delivered, watch) — see [`specs/message.md`](specs/message.md) § 14 |
| `ping`         | `bed.tools.ping:main`   | smoke-test WebSocket + auth round-trip        |

### `bed message` quick reference

The message CLI mirrors the bank tool's two-backend shape. The
WS-bound ops (`subscribe` / `unsubscribe` / `watch` / `pending`)
talk to the bed daemon via WebSocket and require a valid
`--token-file` (run `bed auth login` first). The DB-backed ops
(`send` / `mark_read` / `mark_delivered`) are always routed to the
local DB through `bbsengine6.message.*` — `--direct` is implicit
for them, so the operator never has to pass it and the CLI never
exits with "bed unreachable" for these subcommands. `--direct` is
still honored when passed explicitly.

```bash
# Subscribe to NOTIFY fanout for alice (bed mode, needs auth)
bed message subscribe --moniker alice

# Tail live pushes until Ctrl-C
bed message watch --moniker alice

# List pending messages (bed or DB; backend-aware)
bed message pending --moniker alice

# Send a message (always direct)
bed message send --to bob --content "hello"

# Mark a message read for the actor (always direct)
bed message mark_read --message-id 12345
```

The full subcommand/flag table, the auto-direct-mode rationale,
and the per-handler dispatch are in
[`specs/message.md`](specs/message.md) § 14.

## Tests

```bash
cd bed
PYTHONPATH=src:../bbsengine6/py/src pytest src/bed/tests
```

Nine modules plus a shared helper module and a pytest `conftest`:

- **`test_auth_service.py`** (~1,824 lines) — bearer token
  encode/decode, AuthService unit + dispatch fuzz, loginid plumbing,
  pre/post-dispatch hooks, `bbsengine6.auth.access` delegation.
- **`test_auth_integration.py`** (~750 lines) — wire-level
  end-to-end against a real in-process `WebSocketServer`+`AuthService`
  (login, reconnect, refresh, revoke), `BedAuthServiceClient` envelope
  logic against a loopback transport, optional live-daemon test.
- **`test_auth_tool.py`** (~843 lines) — `bed.tools.auth` CLI surface
  with `_auth_service` mocked (buildargs, token-file plumbing,
  precedence, --direct guard, main_with_args dispatch).
- **`test_auth_tool_integration.py`** (~700 lines, new) — full
  CLI end-to-end through real `BedAuthServiceClient` and real
  token file: `auth_login` / `auth_reconnect` / `auth_refresh` /
  `auth_revoke` driven through `BedServerContext` (a daemon-threaded
  in-process bed server) plus `main_with_args` dispatch through
  `select_backend` / `probe_bed`. Marked `@pytest.mark.integration`.
- **`test_bank_service.py`** (~1,800 lines) — bed-native BankService
  + BedBankServiceClient.
- **`test_bank_integration.py`** (~1,580 lines) — wire-level bank
  operations + optional live-daemon test.
- **`test_bank_tool.py`** (~1,300 lines) — `bed.tools.bank` CLI
  surface with `_bank_service` mocked.
- **`test_bed.py`** (~1,223 lines) — BED server lifecycle, config
  parsing, pidfile, mocked DB.
- **`test_message_service.py`** (~700 lines) — MessageService
  registration / dispatch / list_pending.
- **`test_startup.py`** (~332 lines) — role creation, idempotency,
  main flow.
- **`test_client.py`** (~225 lines) — Phase 3 asyncio hardening
  (running_loop, weakref cache, push handlers).
- **`test_tools_routing.py`** (~131 lines) — `bed.tools._routing`
  (--bed-* flags, select_backend, BedNotReachable).
- **`_auth_helpers.py`** — shared helpers for the auth integration
  tests (StubCredentialProvider, _start_bed_with_auth,
  BedServerContext, _send_and_recv, LIVE_HOST/PORT,
  _live_daemon_reachable). Sibling of `conftest.py` because pytest's
  package import mode (test dir has `__init__.py`) does not make
  `conftest` importable from test files.
- **`conftest.py`** — pytest fixtures (`stub_credential_provider`,
  `live_daemon_reachable`, `live_host`, `live_port`) used by
  any pytest test that opts in.

## License

GPL-2.0-or-later.
