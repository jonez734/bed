# bed (BBS Engine Daemon) — Specification

> **Status (2026-09-04):** v1 stable. v1.1 in flight (MessageService
> GA + cross-repo adoption). v1.2+ design-only.
>
> This file is the **entry point** for understanding `bed`.
> Per-topic detail lives in the linked specs.
>
> Last updated: 2026-09-04

## 1. Purpose & Scope

### 1.1 What `bed` is

`bed` is a small WebSocket daemon that sits in front of a
bbsengine6 game router (empyre, casino, mistermcfeely,
murdermotel, zoid6, etc.), terminates JSON-over-WebSocket, and
lets the game own the wire protocol. It was extracted from the
meta-repo on **2026-06-27** into its own repository. License:
GPL-2.0-or-later. Python 3.9–3.12.

### 1.2 What `bed` is NOT

- It is **not** a generic WebSocket server. It is purpose-built
  for BBS engine games.
- It is **not** a notification daemon. The `notifyd` specs in
  `bbsengine6/handbook/specs/BBSENGINE6_NOTIFYD_*.md` are dead
  (10 files marked SUPERSEDED 2026-07-22).
- It is **not** a migration source for bbsengine6. It is a
  sibling/peer project that *consumes* bbsengine6. See
  `handbook/ARCHITECTURE.md` §3 for the small set of code that
  DID move bed → bbsengine6.

### 1.3 Architectural rule

```
bbsengine6  ──owns──>  I/O protocol, DB, business logic, TUI
bed          ──owns──>  Daemon lifecycle, auth, server-push,
                        cross-cutting wire-protocol services
games (empyre, casino, …) ──consume──>  bbsengine6 + bed
```

Dependency direction: `games` → `bed` → `bbsengine6`.
`bbsengine6` does not import `bed`. Per-consumer pointers in
`handbook/ADOPTERS.md`.

### 1.4 Doc map

| Doc | Purpose |
|---|---|
| `README.md` | Quickstart, CLI flags, console scripts, layout |
| `SPEC.md` (this file) | Entry point: purpose, status, phase gates |
| `TODO.md` | Open work + cross-references |
| `CHANGELOG.md` | Release history |
| `handbook/BED_AUTH.md` | Bearer-token wire protocol (authoritative) |
| `handbook/ARCHITECTURE.md` | Dep graph, code map, prerequisites |
| `handbook/FHS.md` | FHS/UAPI install-path tree |
| `handbook/ADOPTERS.md` | Per-game consumer pointers |
| `specs/auth.md` | AuthService server + client + CLI + ops |
| `specs/message.md` | MessageService server + client + CLI + ops |
| `specs/bank.md` | BankService server + client + CLI + ops |
| `specs/ping.md` | PingService identity-aware liveness probe |
| `specs/echo.md` | Push-based text channel (planned) |
| `specs/menu.md` | Single-pick option list (planned) |
| `specs/help.md` | F1 per-menu help (planned) |
| `specs/key_f2.md` | Session-level new-messages query (planned) |
| `specs/sink.md` | BEDSink + ThinClientIOSink (planned) |
| `specs/postoffice.md` | IMAP-style mail delivery |

## 2. Status

### 2.1 v1 stable features

- WebSocket daemon core (start/stop/restart, PID file, multi-bind).
- Dynamic router loading (`--router FQCN`).
- `AuthService` — bearer-token auth (HMAC-SHA256, 15-min TTL).
- `MessageService` — PG LISTEN/NOTIFY push notifications
  (auto-wired with any non-default router).
- `BankService` — bed-native empyre-shape bank handler.
- `bed.client.*` — connection / bank / messageservice / probe /
  singleton helpers.
- `PingService` — identity-aware liveness probe (`name` +
  `version` in `pong`).
- Database bootstrap (`bed.startup` runs `bbsengine6.startup` +
  creates `bed` PG role; also auto-invoked at daemon start).
- FHS-compliant install (Makefile, systemd unit, sysusers,
  tmpfiles, factory config + env file).
- SELinux integration (`semanage` + `restorecon` automatic).
- 5 CLI scripts: `bed`, `bed-startup`, `auth`, `bank`, `message`,
  `ping`.

### 2.2 v1.1 in flight

- MessageService GA + cross-repo adoption (zoid6, empyre, casino,
  murdermotel, mistermcfeely).
- `--no-bed-fallback` flag for bbsengine6 TUI.
- F2 key handler migration (`getch.py`) from DB-poll to
  `message_list_pending`.

### 2.3 v2 design-only

- Multi-instance load balancing (Path A + Path B) — see
  `handbook/BED_AUTH.md` §"v2 roadmap".
- Per-game style palettes, `menu_multi` primitive, `key_f2`
  paging, linger tuning.

## 3. Implemented services

| Service | Spec | Wire types |
|---|---|---|
| `PingService` | `specs/ping.md` | `ping` → `pong{name, version, timestamp}` |
| `AuthService` | `specs/auth.md` + `handbook/BED_AUTH.md` | `auth`, `reconnect`, `auth_refresh`, `auth_revoke` |
| `MessageService` | `specs/message.md` | `message_subscribe`, `message_unsubscribe`, `message_list_pending`; server-push `message` |
| `BankService` | `specs/bank.md` | `bank_balance`, `bank_add`, `bank_remove`, `bank_history` |

## 4. Planned services (v1.2+)

| Service | Spec | Wire types |
|---|---|---|
| `EchoService` | `specs/echo.md` | `echo`, `echo_batch`, `echo_ack`, `echo_nack`, `echo_cancel` |
| `MenuService` | `specs/menu.md` | `menu`, `menu_reply`, `menu_timeout`, `menu_cancel` |
| `HelpService` | `specs/help.md` | `help`, `help_result`, `help_error` |
| `KeyF2Service` | `specs/key_f2.md` | `key_f2`, `key_f2_result`, `key_f2_empty`, `key_f2_error` |
| `BEDSink` | `specs/sink.md` | (implements `bbsengine6.io.sink.Sink`) |

## 5. Code migrated FROM `bed` TO `bbsengine6`

See `handbook/ARCHITECTURE.md` §3 for the migration map
(SessionManager base, `send_to`, `MessageRouterMixin`,
bottombar plumbing). The migration is one-way.

## 6. Phase gates

### 6.1 v1.0 — SHIPPED

Daemon core, `--config`, `--router`, PID file, dynamic loading,
`AuthService`, `MessageService`, `bed.client.*`, FHS install.
**All criteria met.**

### 6.2 v1.1 — MessageService GA

- All 9 phases of `specs/message.md` §13 Open work checked.
- zoid6 `bed.json` enables message service by default.
- F2 key handler in `getch.py` migrated from `message.get_queue`
  (DB) to `message_list_pending` (bed push).
- `--no-bed-fallback` flag added to bbsengine6 TUI.
- End-to-end DB LISTEN test in `test_message_lib.py`.

### 6.3 v1.2 — Sink Infrastructure Adoption

Bed side: `BEDSink` + `ThinClientIOSink` (see `specs/sink.md`).
bbsengine6 side: sink protocol, `echo_render`, `mci.parse`,
`on_connect_hook` (see `handbook/ARCHITECTURE.md` §4).

### 6.4 v1.3 — Menu + Help + KeyF2

Bed side: `MenuService`, `HelpService`, `KeyF2Service` (see
their respective specs under `specs/`).

### 6.5 v1.4 — Echo / MCI codec

Bed side: `EchoService`, `Fragment` + `FragmentQueue`, `Style`
schema + MCI codec (see `specs/echo.md`).

### 6.6 v1.5 — Multi-Bind (DONE)

One daemon listens on multiple `(host, port)` pairs via `--bind`
and the JSON `bind` list. State shared across listeners. Partial-
bind failure closes already-opened sockets before exit.

### 6.7 v2.0 — Multi-Instance Load Balancing

Path A (shared signing key, softened instance check, shared DB
token store) + Path B (DB-backed `SessionRegistry`, per-connection
UUID). See `handbook/BED_AUTH.md` §"v2 roadmap".

## 7. v2 — Future Plans

| Feature | Source |
|---|---|
| Multi-instance auth Path A | `handbook/BED_AUTH.md` |
| Multi-instance auth Path B | `handbook/BED_AUTH.md` |
| Per-game style palettes (echo/menu) | `specs/echo.md`, `specs/menu.md` |
| `menu_multi` primitive | `specs/menu.md` |
| `key_f2` paging via `listbox` envelope | `specs/key_f2.md` |
| Tilde fix Option C (sentinel-based explicit-set detection) | `TODO.md` |
| WebSocket socket options (linger tuning) | `TODO.md` |
| `Type=notify` systemd readiness signaling | `TODO.md` |
| Per-member PG roles RLS follow-up | `bbsengine6/TODO_RLS.md` |
| Postoffice IMAP poller | `specs/postoffice.md` |

## 8. See also

- `handbook/ARCHITECTURE.md` — dep graph + code map +
  bbsengine6-side prerequisites.
- `handbook/ADOPTERS.md` — per-game consumer pointers.
- `handbook/BED_AUTH.md` — bearer-token wire protocol.
- `handbook/FHS.md` — FHS/UAPI install-path tree.
- `../specs/` — per-service specs.
- `bbsengine6/handbook/specs/` — sibling spec tree.
