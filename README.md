# bed — BBS Engine Daemon

A small WebSocket daemon that sits in front of a `bbsengine6` game router
(empyre, casino, mistermcfeely, murdermotel, zoid6, …), terminates
JSON-over-WebSocket, and lets the game own the wire protocol.

## Quick start

```bash
pip install -e .
bed --router zoid6.api.handler.MonikerAuthRouter
```

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
--host HOST              default: 0.0.0.0
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
