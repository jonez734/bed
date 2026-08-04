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

### systemd service (dedicated venv)

The system Python may be too new for `bed`'s requirement (`>=3.9,<3.13`).
Create a dedicated venv under the service's own directory:

```bash
sudo -u bed python3.12 -m venv /var/lib/bed/venv
sudo -u bed /var/lib/bed/venv/bin/pip install -e /path/to/bed
sudo -u bed /var/lib/bed/venv/bin/pip install -e /path/to/empyre   # router game
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
and builds wheels for all three into `/tmp` so the `bed` user can install
them (the `bed` user may not have access to the source tree).

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

The unit at `src/bed/daemon/bed.service` runs as `User=bed` and uses
`ExecStart=/var/lib/bed/venv/bin/bed --config /etc/bed/bed.json`.  Any Python package
installed into that venv (router games, database drivers, …) is available
at runtime.

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
│   ├── tools/              bank, ping console-script CLIs
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

`pip install .` registers four entry points:

| Script         | Module                  | Purpose                                       |
|----------------|-------------------------|-----------------------------------------------|
| `bed`          | `bed.main:main`         | the WebSocket daemon                          |
| `bed-startup`  | `bed.startup:main`      | standalone database bootstrap                 |
| `bank`         | `bed.tools.bank:main`   | standalone bank CLI (balance, add, remove, history, transfer) |
| `ping`         | `bed.tools.ping:main`   | smoke-test WebSocket + auth round-trip        |

## Tests

```bash
cd bed
PYTHONPATH=src:../bbsengine6/py/src pytest src/bed/tests
```

Six modules, ~4,148 LOC:

- **`test_auth_service.py`** (~1,252 lines) — bearer token
  encode/decode, AuthService, fuzz for decode / secret / dispatch.
- **`test_bed.py`** (~1,223 lines) — BED server lifecycle, config
  parsing, pidfile, mocked DB.
- **`test_bank_service.py`** (~710 lines) — bed-native BankService
  + BedBankServiceClient.
- **`test_message_service.py`** (~425 lines) — MessageService
  registration / dispatch / list_pending.
- **`test_startup.py`** (~332 lines) — role creation, idempotency,
  main flow.
- **`test_client.py`** (~206 lines) — Phase 3 asyncio hardening
  (running_loop, weakref cache, push handlers).

## License

GPL-2.0-or-later.
