# bed — BBS Engine Daemon

A small WebSocket daemon that sits in front of a `bbsengine6` game router
(empyre, casino, mistermcfeely, murdermotel, zoid6, …), terminates
JSON-over-WebSocket, and lets the game own the wire protocol.

## Quick start

```bash
pip install -e .
bed --router zoid6.api.handler.MonikerAuthRouter
```

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

The unit at `src/bed/daemon/bed.service` runs as `User=bed` and uses
`ExecStart=/var/lib/bed/venv/bin/bed --no-autorestart`.  Any Python package
installed into that venv (router games, database drivers, …) is available
at runtime.

In another terminal:

```bash
wscat -c ws://127.0.0.1:8765
> {"type":"auth","moniker":"alice","password":"…"}
< {"type":"auth_result","success":true,"moniker":"alice",…,
   "token":"…","session_id":"…","expires_at":"…","balance":0}
```

## Routers

| FQCN                                                | behavior                                               |
|-----------------------------------------------------|--------------------------------------------------------|
| `bbsengine6.net.defaultrouter.DefaultRouter`        | no-credential stub; wscat / development                |
| `zoid6.api.handler.MonikerAuthRouter`               | verifies the moniker exists; any password accepted     |
| `zoid6.api.MessageRouter`                           | full zoid6 unified router                              |
| any custom router                                   | your game; `bed` wires AuthService alongside          |

`bed` automatically registers `AuthService` (bearer tokens,
reconnect, refresh, revoke) before any non-`DefaultRouter` runs. See
[`docs/BED_AUTH.md`](docs/BED_AUTH.md) for the wire protocol, TTL
knobs, and threat model.

## CLI flags

```
--host HOST              default: 127.0.0.1
--port PORT              default: 8765
--router DOTTED.NAME     default: bbsengine6.net.defaultrouter.DefaultRouter
--config PATH            default: packaged bed/data/bed.json
--bed-secret PATH        default: ~/.config/bed/bed.secret
--token-ttl SECONDS      default: 900
--token-persistence MODE default: memory  (none | memory | db)
--credential-provider N  default: password  (password | moniker-only)
--bed-instance-id UUID   default: auto-generated, persisted with the secret
--autorestart            (default: from bed.json, or True)
--restart-delay N
--max-restarts N
--pidfile PATH
--foreground / -f
--debug
```

Run `bed --help` for the authoritative list.

## Configuration

`bed.json` (CLI > file > argparse default):

```json
{
  "bed":   {"autorestart": true, "restart_delay": 5, "max_restarts": 10},
  "auth":  {"bed_secret_path": "~/.config/bed/bed.secret",
            "token_ttl": 900, "token_persistence": "memory",
            "credential_provider": "password", "bed_instance_id": null},
  "bind":  {"host": "0.0.0.0", "port": 8765},
  "database": {"name": "…", "host": "…", "port": 5432,
               "user": "…", "password": "…"}
}
```

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
│   │                       CredentialProvider, error envelopes, secret loader
│   ├── data/
│   │   ├── bed.json        packaged default config
│   │   └── sql/bed_token.sql   optional DB token-store schema
│   ├── main.py             BED daemon entry point
│   ├── lib.py              argparse
│   ├── config.py           bed.json loader
│   └── tests/              pytest
├── docs/
│   └── BED_AUTH.md         bearer-token protocol reference
├── pyproject.toml
└── Makefile
```

## Tests

```bash
cd bed
PYTHONPATH=src:../bbsengine6/py/src pytest
```

## License

GPL-2.0-or-later.
