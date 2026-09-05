# Architecture

## 1. Dependency graph

```
games (empyre, casino, murdermotel, mistermcfeely, postoffice, zoid6)
    │
    ▼
zoid6 (unified router — MessageRouter + bank module + channel)
    │
    ▼
bed (this repo — WebSocket daemon, AuthService, MessageService,
     BankService, sink lifecycle)
    │
    ▼
bbsengine6 (DB, member/auth, message, bank ledger, io primitives,
            net/WebSocketServer, session)
    │
    ▼
websockets (transport)
```

The dependency direction is one-way. `bbsengine6` does not import
`bed`. Games do not import `bed` directly; they go through `zoid6`
(consumers like `casino` and `empyre` may also use the bed client
library directly without going through a zoid6 daemon — see
`handbook/ADOPTERS.md`).

`bed` owns:
- Daemon lifecycle (start/stop/restart, PID file, multi-bind).
- Bearer-token auth (`AuthService`).
- Server-push notifications (`MessageService`).
- Bank handler (`BankService`).
- Cross-cutting wire-protocol services (echo, menu, help, key_f2,
  sink — see `specs/`).

`bbsengine6` owns:
- I/O protocol, DB access, business logic, TUI.
- The `WebSocketServer` transport.
- The `MessageRouter` base class and per-service dispatch.
- The bank ledger (`bbsengine6.bank.BankService`).

## 2. Code map (`bed/src/bed/`)

```
src/bed/
├── _configpath.py        Config path resolver (CLI > env > FHS > packaged)
├── _version.py           Auto-stamped by `make version`
├── api/
│   ├── auth.py           AuthService — bearer-token issue/verify/refresh/revoke
│   ├── bank.py           BankService — bed-native 4-wire bank handler
│   ├── credential_provider.py   PasswordCredentialProvider, MonikerOnlyCredentialProvider
│   ├── errors.py         Error envelope helpers + code constants
│   ├── handler.py        BaseService, register_service
│   ├── message.py        MessageService — PG LISTEN/NOTIFY subscriber
│   ├── ping.py           PingService — identity-aware pong
│   ├── secret.py         HMAC secret loader (0600 enforced, v1/v2 format)
│   ├── session.py        SessionRegistry — per-websocket state
│   └── token_store.py    InMemoryTokenStore, DBTokenStore
├── client/               BedConnection, BedBankClient, BedMessageClient,
│                         BedBankServiceClient, BedMessageServiceClient, probe
├── config.py             bed.json loader (delegates generic machinery to bbsengine6.config)
├── daemon/               bed.service, bed.sysusers, bed.tmpfiles
├── data/
│   ├── bed.json          Packaged default config
│   └── sql/bed_token.sql Optional DB token-store schema
├── defaultrouter.py      DefaultRouter stub (no-credential)
├── lib.py                argparse
├── main.py               BED daemon entry point
├── startup.py            Database bootstrap (bbsengine6.startup + bed role)
└── tools/                auth, bank, message, ping console-script CLIs
```

## 3. Code migrated FROM `bed` TO `bbsengine6`

| Was in `bed` | Now in `bbsengine6` | Notes |
|---|---|---|
| In-memory `SessionManager` base | `bbsengine6.session.core.SessionManager` | `bed.api.session.SessionRegistry` extends the bbsengine6 class |
| `send_to(ws, msg)` helper | `bbsengine6/net/transport.py` | `MessageService._dispatch_notification` consumes |
| `MessageRouterMixin` API | `bbsengine6/net/router.py` | `next_request_id(ws)`, `get_pending_request(ws, id)`, `resolve_pending_request(ws, id, value)`, `cleanup_session(ws)` |
| Per-connection bottombar plumbing | `bbsengine6/bottombar.py` | `registry_for(name)`, `set_context_for`, `render_for`, `set_active_registry`, `reset_active_registry`, `_active_registry` ContextVar |

The migration is one-way: bed extends/consumes, bbsengine6 owns
the base. For migration candidates that may move in a future
release (e.g. `BedMessageClient`, tilde-expansion helpers), see
the per-spec "Open follow-ups" sections in `specs/`.

## 4. bbsengine6-side prerequisites

Each bed service depends on certain bbsengine6 pieces. This table
makes the dependencies explicit so readers know what blocks each
bed service.

| Bed service / feature | Needs from bbsengine6 | Status |
|---|---|---|
| `AuthService` | `bbsengine6.member.{checkpassword,issysop,getcredits,moniker_exists}` | done |
| `MessageService` | `bbsengine6.net.transport.send_to` | done |
| `MessageService` | `bbsengine6.message.{get,set,bump,clear}_local_unread_count` | done |
| `MessageService` | `engine.__message_recipient` table + PG NOTIFY trigger | done |
| `bed.client.*` | `bbsengine6.net.WebSocketServer` | done |
| `bed.api.session.SessionRegistry` | `bbsengine6.session.core.SessionManager` | done |
| Per-connection bottombar | `bbsengine6.bottombar.registry_for(name)`, `set_active_registry`, `reset_active_registry`, `_active_registry` | done |
| `MenuService` (inputchoice semantics) | `bbsengine6.io.inputchoice` | done |
| `BEDSink` | `bbsengine6.io.sink.Sink` protocol + `set_io_sink`/`reset_io_sink` | pending |
| `BEDSink` | `WebSocketServer.on_connect_hook` | pending |
| `BEDSink.echo` text | `bbsengine6.io.echo_render` | pending |
| `BEDSink.echo` mci | `bbsengine6.io.mci.parse` | pending |
| `EchoService` MCI codec | MCI codec must be strict superset of `bbsengine6.io.echo` tokenizer | pending |

The pending items are all in the **Sink Infrastructure** series in
the bbsengine6 handbook. Once those land, `BEDSink` and
`ThinClientIOSink` can proceed.

## 5. Cross-repo symbols

This is the high-level relationship map. Detailed file:line
call-outs are intentionally omitted — they drift. See the
consumer's own repo for per-file detail.

| Bed symbol | Consumer |
|---|---|
| `bed.api.auth.AuthService` | `zoid6` (MonikerAuthRouter + MessageRouter) |
| `bed.client.connection.BedConnection` | empyre, casino, murdermotel, mistermcfeely, zoid6 |
| `bed.client.bank.BedBankClient` | empyre, casino |
| `bed.client.messageservice.BedMessageServiceClient` | empyre (planned), casino (planned) |
| `bed.api.bank.BankService` | empyre (planned), casino (planned) |
| `bed.api.message.MessageService` | zoid6 (auto-wired), bbsengine6 TUI (cold cache fallback) |
| `bed.api.ping.PingService` | every consumer (liveness probe) |
| `bed.api.menu.MenuService` (planned) | casino (primary), murdermotel, empyre, zoid6, mistermcfeely, bbsengine6 bank |
| `bed.api.help.HelpService` (planned) | murdermotel (primary: lobby, play, rabidwolf help callables) |
| `bed.api.key_f2.KeyF2Service` (planned) | murdermotel, empyre, casino, mistermcfeely/postoffice, zoid6 |
| `bed.api.echo.EchoService` (planned) | empyre (primary), casino, mistermcfeely/postoffice, murdermotel, zoid6, bbsengine6 bank |
| `bed.sinks.BEDSink` (planned) | every consumer (server-side, installed via `on_connect_hook`) |
| `bed.client.io_sink.ThinClientIOSink` (planned) | every consumer (client-side) |

Per-consumer pointer table is in `handbook/ADOPTERS.md`.

## 6. See also

- `handbook/BED_AUTH.md` — bearer-token wire protocol.
- `handbook/ADOPTERS.md` — per-game consumer pointers.
- `handbook/FHS.md` — install-path tree.
- `../specs/` — per-service specs.
- `bbsengine6/handbook/specs/` — sibling spec tree.
