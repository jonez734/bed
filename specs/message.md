# bed MessageService — Specification

> **Audience:** implementers working on `bed` and downstream
> consumers (`zoid6`, `empyre`, `casino`, `murdermotel`, `mistermcfeely`,
> the `bbsengine6` TUI). This is the entry-point spec for the bed-side
> server-push notification pipeline.
>
> **See also:**
> - [`SPEC.md`](../SPEC.md) — bed daemon entry point (what works, what
>   doesn't, v1/v1.1/v1.2/v1.3/v1.4/v2 phase gates, code that moved
>   to `bbsengine6`, bbsengine6-side prerequisites for each bed
>   service).
> - [`README.md`](../README.md) — quick-start, CLI flags, routers,
>   console scripts, layout.
> - [`TODO-message-service.md`](../TODO-message-service.md) — the
>   9-phase plan whose execution produced this code (most phases
>   complete; Phase 5 F2 migration and Phase 9 TUI fallback removal
>   are the remaining gaps).
> - [`docs/BED_AUTH.md`](../docs/BED_AUTH.md) — bearer-token protocol
>   reference. The message service consumes tokens minted by
>   `bed.api.auth.AuthService` and re-verifies them on every op.
> - `bbsengine6/handbook/specs/notify.md` — the unified message
>   system's authoritative spec. This spec covers only the **bed-side
>   surface**; the storage layer (`bbsengine6.message`) is upstream.
> - [`CHANGELOG.md`](../CHANGELOG.md) — release history.

---

## 1. Overview

`bed.MessageService` is the bed-side runtime for the engine's
server-push notification pipeline. It owns a long-lived PostgreSQL
`LISTEN` connection on the `engine_message_recipient` channel and
fans every `NOTIFY` payload out to the connected WebSocket client
whose moniker matches the row's `recipient_moniker`.

The pipeline:

```
[INSERT/UPDATE engine.__message_recipient]
        |
        |  (AFTER INSERT/UPDATE triggers
        |   engine.__message_recipient_notify())
        v
[pg_notify('engine_message_recipient', json payload)]
        |
        v
[bed MessageService LISTEN loop] -- [psycopg AsyncConnection, autocommit]
        |
        v
[MessageService._dispatch_notification:
   json.loads(payload) -> lookup ws by recipient_moniker]
        |
        v
[WebSocketServer.send_to(ws, {"type": "message", ...})]
        |
        v
[BedMessageServiceClient push handler]
        |
        v
[bbsengine6.message.{set,bump,clear}_local_unread_count]
        |
        v
[getch.py / bottombar.py read from local cache — no DB hit]
```

The pipeline replaces the previous idle-poll pattern in
`getch.py:_check_notifications()` and
`bottombar.py:_get_notification_status()` (every input tick queried
the DB). With MessageService enabled, those readers consult a
process-local cache that the bed client's recv loop keeps current.

### 1.1 What this spec covers

- DB layer: the `engine.__message_recipient` table, the
  `__message_recipient_notify()` trigger function, and the
  `engine_message_recipient` NOTIFY channel.
- Wire protocol: three client message types (`message_subscribe`,
  `message_unsubscribe`, `message_list_pending`) and the server-push
  `message` envelope.
- Authorization: the five-gate flow that runs on every op
  (session, wire token, session token, shape, `bbsengine6.message.access()`).
- Server runtime: `MessageService` lifecycle (`start_listener` /
  `stop_listener`), the `_listen_loop` reconnect/backoff, the
  per-moniker subscription map, and the dispatch path.
- Client runtime: `BedMessageServiceClient` (subscribe / unsubscribe
  / list_pending) and the `BedConnection` push-handler integration.
- Local cache: `bbsengine6.message.{get,set,bump,clear}_local_unread_count`.
- Configuration: `bed.json` `message_service` section + the
  `--no-message-service` CLI flag.
- Testing: `test_message_service.py` (~2,360 lines) and the
  bbsengine6 `test_message_local_cache.py` companion.

### 1.2 What this spec does NOT cover

- The unified message system's storage layer (`bbsengine6.message.*`).
  See `bbsengine6/handbook/specs/notify.md` for that.
- Authentication flow (the `auth`/`reconnect`/`auth_refresh`/`auth_revoke`
  messages). See `docs/BED_AUTH.md`.
- The IMAP postoffice service. The postoffice is a separate in-process
  service and does not flow through MessageService.
- Multi-process bed fanout. A single bed instance per host is the
  current scope; horizontal fanout would need a separate design.

---

## 2. Database layer

### 2.1 Tables

File: `bbsengine6/py/src/bbsengine6/sql/message.sql`.

```sql
create table engine.__message (
    "id" bigserial unique not null primary key,
    "channel" text not null,
    "sender_moniker" citext constraint fk_message_sender_moniker
        references engine.__member(moniker) on update cascade on delete set null,
    "content" text not null,
    "data" jsonb,
    "urgency" engine.notify_urgency_enum default 'ROUTINE'::engine.notify_urgency_enum,
    "template" text,
    "template_vars" jsonb,
    "datestamp" timestamptz default now()
);
```

```sql
create table engine.__message_recipient (
    "id" bigserial unique not null primary key,
    "message_id" bigint not null constraint fk_message_recipient_message
        references engine.__message(id) on delete cascade,
    "recipient_moniker" citext not null constraint fk_message_recipient_recipient
        references engine.__member(moniker) on update cascade on delete cascade,
    "status" text not null default 'pending'::text,  -- pending, delivered, read
    "datedelivered" timestamptz,
    "dateread" timestamptz,
    unique(message_id, recipient_moniker)
);
```

Indexes:

- `idx_engine_message_recipient_msg` on `__message_recipient(message_id)`
- `idx_engine_message_recipient_recipient` on `__message_recipient(recipient_moniker)`
- `idx_engine_message_recipient_status` on `__message_recipient(status)`

Grants: `web, sysop, term` get ALL on both tables and their `id_seq`
sequences.

### 2.2 Trigger function

`engine.__message_recipient_notify()` — fires on every INSERT and
UPDATE of `__message_recipient` and emits a NOTIFY on the
`engine_message_recipient` channel.

```sql
create or replace function engine.__message_recipient_notify()
returns trigger
language plpgsql
as $$
declare
    payload jsonb;
    msg_urgency engine.notify_urgency_enum;
begin
    select urgency into msg_urgency
    from engine.__message
    where id = NEW.message_id;

    payload := jsonb_build_object(
        'message_id', NEW.message_id,
        'recipient_id', NEW.id,
        'recipient_moniker', NEW.recipient_moniker,
        'status', NEW.status,
        'urgency', msg_urgency,
        'datestamp', coalesce(NEW.datedelivered, now())
    );

    perform pg_notify('engine_message_recipient', payload::text);
    return NEW;
end;
$$;
```

The payload is JSON `text` (not `jsonb`) so `psycopg.AsyncConnection.notifies()`
can deliver it as a plain `str` to the bed listener.

### 2.3 Triggers

```sql
create trigger trg_message_recipient_insert
    after insert on engine.__message_recipient
    for each row
    execute function engine.__message_recipient_notify();

create trigger trg_message_recipient_update
    after update of status, datedelivered, dateread
        on engine.__message_recipient
    for each row
    when (OLD.status is distinct from NEW.status
          or OLD.datedelivered is distinct from NEW.datedelivered
          or OLD.dateread is distinct from NEW.dateread)
    execute function engine.__message_recipient_notify();
```

- INSERT trigger fires unconditionally on every new recipient row.
- UPDATE trigger only fires when `status`, `datedelivered`, or
  `dateread` actually changed (so a no-op `mark_delivered` on a row
  that's already `delivered` doesn't generate a duplicate push).
- The trigger function's `EXECUTE` is granted to `web, sysop, term`.

### 2.4 Channel name

`NOTIFY_CHANNEL = "engine_message_recipient"` (constant in
`bed/api/message.py:102`). Centralizing the name in the trigger
function plus a Python constant means a rename touches only the
SQL file and the constant.

---

## 3. Wire protocol

### 3.1 Client requests

All three request types share the shape
`{"type": "<wire_type>", "moniker": "<user>", ...}`.

#### 3.1.1 `message_subscribe`

```json
{"type": "message_subscribe", "moniker": "alice"}
```

Optional `"token": "<bearer>"` field — preferred over the
session-bound snapshot. When present, it is HMAC-verified against
the bed's `secret` and checked against the `token_store` and
`instance_id`. See Section 5.

Response:

```json
{"type": "message_subscribe_result", "ok": true,  "moniker": "alice"}
{"type": "message_subscribe_result", "ok": false, "moniker": "alice",
 "code": "<error_code>", "message": "<human readable>",
 "recoverable": <bool>}
```

#### 3.1.2 `message_unsubscribe`

```json
{"type": "message_unsubscribe", "moniker": "alice"}
```

Response:

```json
{"type": "message_unsubscribe_result", "ok": true,  "moniker": "alice"}
{"type": "message_unsubscribe_result", "ok": false, "moniker": "alice",
 "code": "<error_code>", "message": "<human readable>",
 "recoverable": <bool>}
```

#### 3.1.3 `message_list_pending`

```json
{"type": "message_list_pending", "moniker": "alice"}
```

Server fetches via `bbsengine6.message.get_pending_messages(moniker,
limit=100)` and returns up to 100 rows ordered by `m.datestamp DESC`,
filtering `r.status IN ('pending', 'delivered')`.

Response:

```json
{"type": "message_list_pending_result", "ok": true,  "moniker": "alice",
 "messages": [<row>, ...]}
{"type": "message_list_pending_result", "ok": false, "moniker": "alice",
 "messages": [],
 "code": "<error_code>", "message": "<human readable>",
 "recoverable": <bool>}
```

Each `messages[]` row is the dict shape produced by
`bbsengine6.message.get_pending_messages`:

```json
{
  "id": 12345,
  "channel": "casino:table:blackjack-1",
  "sender_moniker": "system",
  "content": "...",
  "data": null,
  "urgency": "ROUTINE",
  "template": null,
  "template_vars": null,
  "datestamp": "2026-08-15T18:31:02+00:00",
  "status": "pending",
  "datedelivered": null,
  "dateread": null
}
```

### 3.2 Server-push envelope

```json
{
  "type": "message",
  "channel": "engine_message_recipient",
  "message_id": 12345,
  "recipient_id": 67890,
  "recipient_moniker": "alice",
  "status": "pending",
  "urgency": "ROUTINE",
  "datestamp": "2026-08-15T18:31:02.123456+00:00",
  "request_id": "server:msg:1"
}
```

Fields are sourced verbatim from the NOTIFY payload except:

- `type` is fixed at `"message"` (the wire-protocol discriminator).
- `channel` is fixed at `"engine_message_recipient"` (echoed so the
  client can multiplex without parsing `type`).
- `request_id` is a monotonically increasing `server:msg:<N>` from
  `MessageService._seq` so push envelopes can be correlated even
  though they are unsolicited.

`urgency` is one of `ROUTINE`, `IMPORTANT`, `URGENT`, `CRITICAL`
(matching the `engine.notify_urgency_enum`). `status` is one of
`pending`, `delivered`, `read`.

### 3.3 Error codes

Codes returned in the `"code"` field of the various `_result`
envelopes:

| code                          | meaning                                                    |
|-------------------------------|------------------------------------------------------------|
| `missing_moniker`             | `"moniker"` is empty / whitespace after strip              |
| `not_authenticated`           | no bound session AND no wire token (legacy unauthenticated) |
| `token_invalid`               | HMAC verify failed or shape invalid                        |
| `token_revoked`               | token not in the `token_store`                             |
| `bed_instance_mismatch`       | token issued by a different bed instance                   |
| `token_expired`               | `expires_at` claim is in the past                          |
| `forbidden`                   | `bbsengine6.message.access()` returned False               |
| `database_error`              | `get_pending_messages` raised (envelope carries traceback) |

Codes mirror the auth service so clients can reuse the same
reconnect / refresh logic.

### 3.4 Subscription lifetime

A subscription lives from a successful `message_subscribe` until one
of:

- A matching `message_unsubscribe` arrives.
- The subscribed WebSocket disconnects (`MessageService._dispatch_notification`
  drops the map entry on `send_to` failure, which is the path taken
  when the underlying socket is already gone).
- The bed process restarts (the in-memory map does not persist).
- The listener is stopped (`stop_listener`).

Clients should call `message_list_pending` on reconnect to recover
any events that arrived while they were disconnected.

---

## 4. Service registration

`MessageService` is one of the three services `BED.start()` registers
alongside any non-default router. See `bed/src/bed/main.py:461-494`.

### 4.1 Wiring order

`BED.start()` runs:

1. `await self._start_auth(db_args)` (only when auth is enabled —
   `token_persistence != "none"` AND router is not the bbsengine6
   no-credential stub).
2. `WebSocketServer(host, port)` constructed.
3. If `auth_service` was constructed, `auth_service.register_all(server)`.
4. If `MessageRouterClass` was provided, instantiate and
   `router.register_all(server)`.
5. **If `--no-message-service` is NOT set**, construct `MessageService`,
   `register_all(server)`, and schedule `start_listener()` as an
   asyncio task.
6. If `--no-bank-service` is NOT set, do the same for `BankService`.

MessageService runs AFTER the router so any router-registered
`message_*` handler is shadowed by the service (the router must not
register overlapping types; if it does, `WebSocketServer.register_service`
warns on the swap).

### 4.2 Token-aware wiring

When auth is enabled, `BED.start()` passes the same `secret`,
`token_store`, and `instance_id` the auth service uses to the
`MessageService` constructor. This lets `_check_access` re-verify
`state.auth_service_token` on every message op (defense-in-depth
against a token revoked since the WS opened).

When auth is disabled (legacy mode or `--token-persistence=none`),
the message service falls back to session-bound authorization
without token re-verification. `bbsengine6.message.access()` is
still called and still applies the same per-op rules.

### 4.3 Opt-out

`--no-message-service` (parsed in `bed/src/bed/lib.py`) suppresses
construction of `MessageService` entirely. The bed startup banner
(`main.py:653`) prints `BED MessageService: LISTEN engine_message_recipient`
only when the service is active.

`bed.json`:

```json
"message_service": {
  "enabled": true,
  "modulepath": "bed.api.message",
  "description": "Server-push notifications via PG LISTEN/NOTIFY on engine_message_recipient"
}
```

The `enabled` flag and `--no-message-service` CLI flag are
redundant-but-orthogonal: the config is the persistent default, the
CLI is the per-invocation override. Both are honored.

---

## 5. Authorization — the five gates

Every op (`subscribe`, `unsubscribe`, `list_pending`) runs the same
five-gate pipeline in `MessageService._check_access`
(`bed/api/message.py:438-512`). The gates run in order; the first
failure short-circuits and returns the envelope to the client.

### Gate 1 — Session resolve

`_get_or_bind_session_for(self_ref, websocket, message)`:

1. Look up the SessionState bound to `websocket.id` via
   `self_ref.sessions.get_by_websocket(ws_id)`.
2. If bound, return it.
3. If not bound, attempt a **lazy bind** from the wire token:
   - When `message["token"]` is non-empty, validate it (Gate 2)
     and synthesize a fresh `SessionState` (or rebind an existing
     `session_id` from the claims).
   - When `message["token"]` is empty/absent, return
     `not_authenticated()`.

The lazy-bind fallback exists because the CLI's `bed message`
subcommand runs each per-op call under a fresh `asyncio.run` (one
per subcommand) which closes its event loop and forces
`BedConnection` to open a new WebSocket on the next call. Each new
WebSocket is a fresh `websocket.id` in the server's eyes, so without
this fallback the session registered by the prior `auth reconnect`
would be unreachable and every message op would return
`not_authenticated`.

### Gate 2 — Wire token (preferred)

`_validate_wire_token(self_ref, message)` — when `message["token"]`
is non-empty:

1. `_decode_token(token, secret)` (HMAC verify, shape check). On
   `TokenError`, return the envelope (`token_invalid`,
   `token_revoked`, `bed_instance_mismatch`, etc., depending on the
   subclass).
2. Compare `claims.expires_at` to `_now()`. If expired, delete the
   record from `token_store` (best-effort, swallowed) and return
   `token_expired` (`recoverable=true`).
3. Look up the record in `token_store`. If absent, return
   `token_revoked`.
4. Compare `store_record.bed_instance_id` to `self.instance_id`. If
   different, delete the record and return `bed_instance_mismatch`.

On success the decoded claims dict is stashed on
`message["claims"]` so `bbsengine6.message.access()` can prefer
claim-derived `moniker` / `is_sysop` over the in-memory session.

### Gate 3 — Session token (fallback)

`_validate_session_token(self_ref, state)` — only runs when Gate 2
was a no-op (wire token absent). Reads
`state.auth_service_token` (set by the auth flow at WS bind time,
or by the lazy-bind fallback above) and runs the same
`_validate_token_against_store` pipeline.

When Gate 1 ran the lazy-bind fallback it sets
`state.auth_service_token = wire_token` so subsequent ops still
have a bound snapshot.

### Gate 4 — Wire-shape validation

`_validate_shape(op, message)` — `bbsengine6.message.access()`
intentionally does NOT check wire-shape invariants. The handler
does, because envelope codes are a wire-protocol concern. Currently:

- All three ops require a non-empty `moniker` (else
  `missing_moniker`).

### Gate 5 — `bbsengine6.message.access()`

`bbsengine6.message.access(args, op, session=state, message=message)`.
The bbsengine6.message package owns the op vocabulary and the
per-op policy; bed is a thin consumer.

The policy (`bbsengine6/message/__init__.py:155-219`) is:

- `op="run"` with no `session` kwarg → `True` (module-load probe).
- `session=None` → `False` (claims alone cannot grant access —
  the session registry must also recognize the caller).
- `op in ("subscribe", "unsubscribe", "list_pending")` →
  - `True` if claim-derived `is_sysop` is true.
  - Else `True` only when the case-insensitive claim-derived
    `moniker` matches `message["moniker"]`.
  - Else `False`.

When access returns False the handler returns a `forbidden`
envelope with code `forbidden` and message `"Operation not
permitted for this account"`.

### Why five gates and not one

- Session lookup catches the legacy case where no token was ever
  presented (the WS was opened with `bbsengine6`'s
  `DefaultRouter` and never sent `auth`).
- Wire token (Gate 2) catches a token revoked since WS open even if
  the session-bound snapshot is stale. The CLI reads its
  `--token-file` on every subcommand, so this is the freshest view
  the server has.
- Session token (Gate 3) handles the legacy case where the WS is
  long-lived and the client does not send a per-call token.
- Wire-shape (Gate 4) keeps envelope codes (`missing_moniker`)
  inside the wire-protocol layer, not the policy layer.
- `bbsengine6.message.access()` (Gate 5) is the policy decision
  itself, encoded in the package that owns the message system.

Mirrors `bed.api.bank.BankService._check_access` and
`bed.api.auth.AuthService._check_access` so a token minted by
AuthService is consumable by either service without
re-implementation.

---

## 6. Server runtime — `MessageService`

File: `bed/src/bed/api/message.py`.

### 6.1 Construction

```python
def __init__(
    self,
    args: Any,
    session_manager: Any,
    *,
    secret: Optional[bytes] = None,
    token_store: Optional[TokenStore] = None,
    instance_id: Optional[str] = None,
    clock: Optional[Any] = None,
) -> None:
```

- `args` — bed argparse namespace, used for `make_dsn(self.args)` so
  the listener connects to the same DB the rest of bed uses.
- `session_manager` — `SessionRegistry` (from `bed.api.session`),
  used by `_get_or_bind_session_for`.
- `secret`, `token_store`, `instance_id` — token-aware wiring
  (Section 5). All optional; when any is missing the service falls
  back to session-only authorization.
- `clock` — injectable time source for deterministic expiry tests
  (mirrors AuthService).

Internal state:

- `_subscribed: Dict[str, Any]` — moniker → websocket.
- `_subscribed_lock: asyncio.Lock` — guards map mutations.
- `_listener_task: Optional[asyncio.Task]` — the `_listen_loop`
  coroutine.
- `_stop_event: asyncio.Event` — signals the loop to exit.
- `_async_conn: Optional[psycopg.AsyncConnection]` — the dedicated
  LISTEN connection (bypasses the bbsengine6 `ConnectionPool` because
  LISTEN registrations are per-connection).
- `_seq: int` — monotonic counter for `request_id` generation.
- `_clock: Optional[Callable[[], float]]` — time source.

### 6.2 `register_all(server)`

```python
def register_all(self, server: Any) -> None:
    self.server = server
    server.register_service(self, list(self.HANDLED_TYPES))
```

`HANDLED_TYPES = ("message_subscribe", "message_unsubscribe",
"message_list_pending")`. The dict lookup `_OP_TO_HANDLER`
(`bed/api/message.py:731-735`) maps each op verb to the private
handler coroutine, mirroring the dispatch shape in
`bed/api/bank.py:831-841`.

### 6.3 Lifecycle

#### `start_listener`

Idempotent — a second call while the task is still running is a
no-op. Creates `asyncio.create_task(self._listen_loop(), name="bed-message-listener")`.

Called from `BED.start()` as an unawaited task
(`asyncio.create_task(self.message_service.start_listener())`),
so the start sequence can return without waiting for the first
LISTEN to complete.

#### `stop_listener`

1. Set `_stop_event`.
2. Cancel `_listener_task`, await it (suppress
   `CancelledError`/`Exception`).
3. `_close_async_conn()` — idempotent.
4. Log `"MessageService: listener stopped"`.

Called from `BED.stop()` (cleanup path) and from the SIGTERM /
SIGINT handler. Mirrors the `BedSink` / auth / bank stop hooks in
the same file.

### 6.4 `_listen_loop`

```python
async def _listen_loop(self) -> None:
    if psycopg is None:
        logger.error("MessageService: psycopg not installed; listener disabled")
        return

    dsn = make_dsn(self.args)
    backoff = 1.0
    while not self._stop_event.is_set():
        try:
            self._async_conn = await psycopg.AsyncConnection.connect(
                dsn, autocommit=True
            )
            async with self._async_conn.cursor() as cur:
                await cur.execute(f"LISTEN {NOTIFY_CHANNEL}")
            logger.info("MessageService: LISTEN %s established", NOTIFY_CHANNEL)
            backoff = 1.0

            while not self._stop_event.is_set():
                notifies = await self._async_conn.notifies(timeout=1.0)
                for n in notifies:
                    await self._dispatch_notification(n.payload)
        except asyncio.CancelledError:
            raise
        except (psycopg.Error, OSError) as e:
            logger.warning(
                "MessageService: listener error (will retry in %.1fs): %s",
                backoff, e,
            )
            await self._close_async_conn()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=backoff
                )
                return
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)
        finally:
            await self._close_async_conn()
```

Key points:

- Uses a **dedicated** `psycopg.AsyncConnection` (NOT the bbsengine6
  `ConnectionPool`). LISTEN registrations are per-connection and
  psycopg's pool would cycle connections out from under the
  registration.
- `autocommit=True` — psycopg requires it for LISTEN/NOTIFY
  (transactions holding LISTEN registrations see NOTIFYs only on
  commit).
- `notifies(timeout=1.0)` — the timeout gives the loop a periodic
  chance to notice `_stop_event.is_set()` without needing to cancel
  the task from another coroutine.
- **Exponential backoff** on connect / LISTEN / notify errors:
  1s → 2s → 4s → 8s → 16s → 30s (capped). Backoff resets to 1s on
  successful reconnect.
- The backoff wait uses `asyncio.wait_for(_stop_event.wait(),
  timeout=backoff)` — if `_stop_event` fires during the wait, the
  loop returns immediately (no further reconnect attempts).
- All exceptions are caught: `asyncio.CancelledError` is re-raised
  (so `stop_listener`'s `await self._listener_task` can finish);
  `psycopg.Error` and `OSError` trigger the backoff. `Exception` is
  NOT caught here — only connection-class errors are recoverable.

### 6.5 `_dispatch_notification`

```python
async def _dispatch_notification(self, payload: str) -> None:
    try:
        data = json.loads(payload)
    except (TypeError, ValueError) as e:
        logger.warning("MessageService: bad payload %r: %s", payload, e)
        return
    if not isinstance(data, dict):
        logger.warning("MessageService: payload not a JSON object: %r", payload)
        return

    recipient = data.get("recipient_moniker")
    if not recipient:
        return
    async with self._subscribed_lock:
        ws = self._subscribed.get(recipient)
    if ws is None or self.server is None:
        return

    self._seq += 1
    envelope = {
        "type": "message",
        "channel": NOTIFY_CHANNEL,
        "message_id": data.get("message_id"),
        "recipient_id": data.get("recipient_id"),
        "recipient_moniker": recipient,
        "status": data.get("status"),
        "urgency": data.get("urgency"),
        "datestamp": data.get("datestamp"),
        "request_id": f"server:msg:{self._seq}",
    }
    try:
        await self.server.send_to(ws, envelope)
    except Exception as e:
        logger.warning("MessageService: send_to failed for %s: %s", recipient, e)
        async with self._subscribed_lock:
            self._subscribed.pop(recipient, None)
```

Behaviors:

- A malformed JSON payload logs a warning and is silently dropped
  (no client receives anything). The trigger function is the only
  producer; a malformed payload means the trigger itself is broken.
- A payload that is not a JSON object is dropped for the same
  reason.
- A payload with empty / missing `recipient_moniker` is dropped
  silently — the trigger always sets it, so this would indicate
  data corruption upstream.
- A recipient with no subscription is a no-op (the row was inserted
  while the user was disconnected; the user will pick it up on
  `message_list_pending` after reconnect).
- `send_to` failures remove the dead entry from `_subscribed` so
  the next push does not retry on a closed socket.

### 6.6 `_handle_subscribe`

Runs `_check_access(websocket, "subscribe", message)`. On success,
adds `moniker → websocket` to `_subscribed` under the lock.

The first `subscribe` per moniker from a given WS is the "real"
subscription; a duplicate `subscribe` for the same moniker
overwrites the entry but does NOT register a second WS — the
client may call `subscribe` again after a network blip to refresh
the binding, but the server-side map remains a single entry per
moniker.

### 6.7 `_handle_unsubscribe`

Runs `_check_access(websocket, "unsubscribe", message)`. On success,
`self._subscribed.pop(moniker, None)`. The result envelope does not
distinguish "was subscribed" from "was not subscribed" — both
return `ok=true, moniker=...`.

### 6.8 `_handle_list_pending`

Runs `_check_access(websocket, "list_pending", message)`. On
success, calls `bbsengine6.message.get_pending_messages(moniker,
limit=100)`. On DB error, emits a traceback via
`bbsengine6.io.echo_traceback` and returns
`ok=false, code=database_error, messages=[]`. A missing-table
warning is NOT raised here — `get_pending_messages` would raise
`psycopg.errors.UndefinedTable`, which is caught by the `except
Exception` and returned as `database_error` so the client sees the
error envelope rather than a generic `ok=false`.

### 6.9 Handler dispatch

```python
_TYPE_TO_OP = {
    "message_subscribe": "subscribe",
    "message_unsubscribe": "unsubscribe",
    "message_list_pending": "list_pending",
}

_OP_TO_HANDLER = {
    "subscribe": MessageService._handle_subscribe,
    "unsubscribe": MessageService._handle_unsubscribe,
    "list_pending": MessageService._handle_list_pending,
}
```

`handle_message(server, websocket, path, message)` looks up the op,
returns `None` for unknown types (so other services can handle
them), and otherwise dispatches via the dict. Mirrors the
`bank.py:831-841` flat-dispatch pattern.

---

## 7. Client runtime — `BedMessageServiceClient`

File: `bed/src/bed/client/messageservice.py`.

```python
class BedMessageServiceClient:
    def __init__(self, connection: BedConnection) -> None: ...
    async def subscribe(self, moniker: str) -> Dict[str, Any]: ...
    async def unsubscribe(self, moniker: Optional[str] = None) -> Dict[str, Any]: ...
    async def list_pending(self, moniker: Optional[str] = None) -> Dict[str, Any]: ...

def get_message_client(connection: BedConnection) -> BedMessageServiceClient: ...
def reset_message_client() -> None: ...
```

### 7.1 `subscribe(moniker)`

1. Strip and validate `moniker`. Empty → return
   `{ok: False, code: "missing_moniker", message: "moniker is required"}`.
2. Idempotency check — if `_subscribed_moniker == moniker` AND a
   handler is registered, return
   `{ok: True, moniker, already_subscribed: True}` without a wire
   round-trip.
3. Send `{"type": "message_subscribe", "moniker": moniker}` via the
   shared `BedConnection`. On `BedUnavailable`, return
   `{ok: False, code: "bed_unavailable", message: ...}`.
4. If the reply is `ok=False`, surface the envelope unchanged
   (caller sees the server's `code`).
5. Register a `_push` handler on `BedConnection`:
   - Filters `type == "message"` AND `recipient_moniker == moniker`.
   - On `status == "read"` → `bump_local_unread_count(moniker, -1)`.
   - On `status == "pending"` (or any non-read status) →
     `bump_local_unread_count(moniker, +1)`.
   - On any cache update exception, log a warning and continue.
6. Track `_subscribed_moniker` and `_handler`.
7. Immediately call `list_pending(moniker)`. If it returns `ok=True`,
   `set_local_unread_count(moniker, len(messages))`. This seed is
   the authoritative cold-cache value; pushes that arrive
   afterward are deltas.
8. If `list_pending` raised `BedUnavailable`, swallow — the user is
   disconnected, so the cache will be empty until they reconnect.

### 7.2 `unsubscribe(moniker=None)`

If `moniker` is None, fall back to `_subscribed_moniker`. If no
target is set, return `{ok: True, moniker: None,
no_subscription: True}` without a wire round-trip.

Otherwise, send `{"type": "message_unsubscribe", "moniker":
target}`. On `BedUnavailable`, return
`{ok: False, code: "bed_unavailable", message: ...}`. On success,
unsubscribe the `_push` handler from `BedConnection` (best-effort
— exceptions are swallowed so a disconnect during teardown does
not mask the server's `ok=True` reply).

### 7.3 `list_pending(moniker=None)`

If no target, return
`{ok: False, code: "missing_moniker", ..., messages: []}`. Otherwise
send `{"type": "message_list_pending", "moniker": target}` and
return the server's envelope unchanged. `BedUnavailable` returns
`{ok: False, code: "bed_unavailable", ..., messages: []}`.

### 7.4 Process-wide singleton

```python
_module_client: Optional[BedMessageServiceClient] = None

def get_message_client(connection: BedConnection) -> BedMessageServiceClient:
    global _module_client
    if _module_client is None or _module_client._conn is not connection:
        _module_client = BedMessageServiceClient(connection)
    return _module_client

def reset_message_client() -> None:
    global _module_client
    _module_client = None
```

`get_message_client(connection)` returns a process-wide client
bound to the supplied `BedConnection`. A new connection replaces
the old client. `reset_message_client` drops the cache (used by
tests; does not call `unsubscribe` on the server).

### 7.5 Push-handler integration with `BedConnection`

`BedConnection` (file: `bed/src/bed/client/connection.py`)
maintains a list of `PushHandler` callables. On every WS recv, the
background `_recv_loop`:

1. Parses the frame.
2. If `type` matches the in-flight request's `_recv_match`,
   resolves the request and skips the handlers.
3. Otherwise, dispatches the frame to every registered handler
   in order.

Push handlers are added with `await conn.subscribe(handler)` and
removed with `await conn.unsubscribe(handler)`. On disconnect the
recv loop and any in-flight `send()` both fail, and the client
must reconnect (the `bed` package has no auto-reconnect logic —
see `bbsengine6/TODO.md` "Bed Disconnect: No Auto-Reconnect (Known
Limitation, 2026-07-22)").

---

## 8. Local cache

File: `bbsengine6/py/src/bbsengine6/message/lib.py:627-680`.

The cache is the "no DB hit on every input tick" optimization that
the entire pipeline exists to enable.

```python
_local_unread_cache: Dict[str, int] = {}
_local_unread_cache_lock: Optional[Any] = None

def get_local_unread_count(moniker: str) -> int:
    """Return -1 if not cached (caller falls back to DB)."""

def set_local_unread_count(moniker: str, count: int) -> None:
    """Clamped to max(0, int(count))."""

def bump_local_unread_count(moniker: str, delta: int = 1) -> None:
    """Atomically adjust by delta, clamped to max(0, ...)."""

def clear_local_unread_cache() -> None:
    """Drop every entry (e.g. on logout)."""
```

Properties:

- **Process-local**: a single Python process serves multiple BBS
  sessions in multi-user TUI hosts, so the count is shared across
  all monikers in that process. In a typical BED/TUI deployment
  there is one TUI process per user, so this is fine. Callers
  that need authoritative counts fall back to
  `bbsengine6.message.get_unread_count()` (DB-backed).
- **Lazy lock**: `_ensure_local_lock()` imports `threading.Lock`
  only when the cache is first touched. Tests that never call
  any cache function do not pay the import cost.
- **`get_local_unread_count` returns -1 on miss**, so the caller
  can distinguish "unknown" from "0 unread". A typical
  `_check_notifications` path is:
  ```python
  cached = message.get_local_unread_count(moniker)
  if cached < 0:
      count = message.get_unread_count(moniker)
      message.set_local_unread_count(moniker, count)
  else:
      count = cached
  ```

### 8.1 Update paths

| Source                                       | Calls                                |
|----------------------------------------------|--------------------------------------|
| `BedMessageServiceClient.subscribe` warm-up  | `set_local_unread_count(moniker, N)` |
| `BedMessageServiceClient._push` (pending)    | `bump_local_unread_count(moniker, +1)` |
| `BedMessageServiceClient._push` (read)       | `bump_local_unread_count(moniker, -1)` |
| `bbsengine6.message.mark_read` (DB write)    | (no cache update — clients update via push) |
| Logout / session end                         | `clear_local_unread_cache()`         |

The `+1` / `-1` semantics map cleanly onto the trigger's NOTIFY
payload: every `pending` insert pushes +1, every `read` UPDATE
pushes -1.

### 8.2 Read paths

| Reader                                   | Path                                  |
|------------------------------------------|---------------------------------------|
| `bbsengine6/io/getch.py:_check_notifications` | local cache → DB fallback on -1     |
| `bbsengine6/bottombar.py:_get_notification_status` | local cache → DB fallback on -1 |
| F2 key handler (still DB-backed, see Section 13) | `message.get_queue` (TODO Phase 5) |

---

## 9. Configuration

### 9.1 `bed.json` section

```json
"message_service": {
  "enabled": true,
  "modulepath": "bed.api.message",
  "description": "Server-push notifications via PG LISTEN/NOTIFY on engine_message_recipient"
}
```

The section is informational in the current code path (the service
is constructed unconditionally when `--no-message-service` is not
set); `enabled` is reserved for a future per-config opt-out
(currently the CLI flag wins).

### 9.2 CLI flags

- `--no-message-service` — parsed in `bed/src/bed/lib.py`. Suppresses
  `MessageService` construction entirely (the listener never starts;
  `message_list_pending` is still served by the router fallback if
  one exists, otherwise the wire types return 404).
- `--no-bank-service` — orthogonal; suppresses `BankService` only.
- The general `--no-message-*` shape mirrors the `--no-bank-*`
  shape and keeps the dependency direction
  `consumer → bed` (bed does not know about zoid6 / empyre).

### 9.3 Precedence

CLI > `bed.json` > argparse default, same as every other bed
knob. The `bed.json` `message_service.enabled` is read but not
acted on by the current code; the CLI flag is the binding knob.

### 9.4 Identity-aware defaults

`MessageService` itself has no instance-name awareness (it is
identified by the DSN it listens on, not by `bed.name`). Two bed
instances running on the same host will both receive every NOTIFY
and each push only to its own subscribers; cross-instance fanout
is out of scope.

---

## 10. Error handling & failure modes

### 10.1 Listener can't connect

`_listen_loop` catches `psycopg.Error` and `OSError`, logs a
warning, and backs off (1s → 30s cap). The bed daemon stays up;
clients that try `message_subscribe` get the normal reply (the
service is still alive, just not pushing). The listener resumes
automatically when the DB is reachable again.

### 10.2 Listener can't LISTEN

Same backoff path as 10.1. A bad channel name would loop forever
in the backoff path — `engine_message_recipient` is the only
supported channel, and the trigger function is the only writer.

### 10.3 Malformed payload

`_dispatch_notification` logs and drops. The trigger is the only
producer; a malformed payload means the trigger is broken and the
log line is the diagnostic.

### 10.4 Dead subscriber

`_dispatch_notification` catches `Exception` from `send_to` and
removes the entry from `_subscribed`. The next NOTIFY for that
moniker is a no-op (no subscriber). The client will pick up the
message on `message_list_pending` after reconnect.

### 10.5 DB down at startup

`BED.start()` runs `db_args.pool.connection()` BEFORE constructing
`WebSocketServer`, so a DB outage at startup fails the daemon's
start sequence (the autorestart loop or systemd sees a real error).
Once the daemon is up, a DB outage after `MessageService.start_listener()`
is running falls into the backoff path (10.1).

### 10.6 Auth-mismatch scenario

A session whose token was minted by a different bed instance is
rejected at Gate 2 (`bed_instance_mismatch`) and the record is
deleted from `token_store`. Subsequent calls with the same token
will return `token_revoked`.

### 10.7 Lazy-bind fallback failure

If a wire token is present but invalid (Gate 2), Gate 1 returns
the envelope (`token_invalid` / `token_revoked` /
`bed_instance_mismatch` / `token_expired`). The handler does NOT
proceed to Gates 3-5 — a session that cannot be established
cannot be authorized.

---

## 11. Testing

### 11.1 `bed/src/bed/tests/test_message_service.py` (~2,360 lines)

Coverage (per `bed/TODO-message-service.md` Phase 7 and the current
test surface):

- `test_message_service_registers_handled_types` — `HANDLED_TYPES`
  is `("message_subscribe", "message_unsubscribe",
  "message_list_pending")`.
- `test_subscribe_adds_to_subscribed_map` — successful `subscribe`
  populates `_subscribed[moniker] = ws`.
- `test_unsubscribe_removes_from_map` — successful `unsubscribe`
  drops the entry.
- `test_dispatch_notification_sends_to_subscribed_websocket` — a
  NOTIFY payload for a subscribed moniker produces a `message`
  envelope via `server.send_to`.
- `test_dispatch_notification_no_subscriber_is_noop` — a payload
  for an unsubscribed moniker is silently dropped.
- `test_dispatch_notification_bad_payload_is_noop` — non-JSON or
  non-object payloads are logged + dropped, no `send_to` call.
- `test_dispatch_notification_removes_dead_subscriber` — a
  `send_to` exception removes the map entry.
- `test_list_pending_returns_db_messages` — successful
  `list_pending` returns the DB-backed rows.
- `test_list_pending_rejects_empty_moniker` — missing `moniker`
  returns `code=missing_moniker`.
- `test_lifecycle_start_stop_is_idempotent` — `start_listener` is
  a no-op when already running; `stop_listener` is safe to call
  twice.

Additional coverage (added beyond the original Phase 7 plan):

- Five-gate authorization tests — wire-token preferred over
  session-token, claim-derived moniker / is_sysop preferred over
  session attributes, lazy-bind fallback from a valid wire token
  when the WS is unbound, sysop can subscribe to any moniker, and
  self-moniker check.
- `token_expired` is checked BEFORE the store lookup so a token
  whose clock has run out surfaces as `token_expired` even when
  the in-memory store's lazy-GC has purged the record.
- Token-aware wiring: a service constructed without `secret` /
  `token_store` / `instance_id` falls back to session-bound
  authorization.
- `bbsengine6.message.access()` returns `False` on
  `session=None` even with valid claims.
- Push handler integration with `BedMessageServiceClient`.

### 11.2 `bbsengine6/py/tests/test_message_local_cache.py`

Cache init / set / get / bump / clear, separation across monikers,
clamping at zero, `-1` on miss, lazy-lock import.

### 11.3 Out-of-test surface (deferred)

- End-to-end `LISTEN engine_message_recipient` test in
  `bbsengine6/py/tests/test_message_lib.py` — requires a live PG,
  deferred per `TODO-message-service.md` Phase 7.
- `_check_notifications` no-DB-hit verification — covered indirectly
  by the local cache tests; a `getch.py`-level smoke test is
  deferred.

### 11.4 Test infrastructure

- `bed/src/bed/tests/conftest.py` — `stub_credential_provider`,
  `live_daemon_reachable`, `live_host`, `live_port` fixtures.
- `bed/src/bed/tests/_auth_helpers.py` — `StubCredentialProvider`,
  `_start_bed_with_auth`, `BedServerContext`, `_send_and_recv`,
  `LIVE_HOST/PORT`, `_live_daemon_reachable`. Sibling of
  `conftest.py` because pytest's package import mode does not make
  `conftest` importable from test files.

### 11.5 Running the suite

```bash
cd bed
PYTHONPATH=src:../bbsengine6/py/src pytest src/bed/tests/test_message_service.py -q
```

Integration-only tests (those that hit a real daemon) are marked
`@pytest.mark.integration` and skipped in the default run.

---

## 12. Security

### 12.1 Threat model

- **Unauthenticated subscribe**: the wire-token gate (Gate 2) and
  the lazy-bind fallback (Gate 1) reject subscribe attempts from
  unbound sockets without a valid token. The 5-gate flow closes
  the historical gap where any WS could subscribe to any moniker.
- **Cross-user read**: `bbsengine6.message.access()` denies
  non-sysop callers from reading another user's queue. The
  `list_pending` op is filtered by the session's own moniker.
- **Token replay**: `bed_instance_mismatch` rejects tokens minted
  by a different bed instance; the matching record is deleted so
  the same token cannot be reused.
- **Token expiry**: `token_expired` is checked BEFORE the store
  lookup so a token whose clock has run out cannot be confused
  with a revoked token by a stale in-memory store.
- **Claim-derived authorization**: when a wire token is present,
  `bbsengine6.message.access()` prefers the HMAC-verified
  `claims.moniker` / `claims.is_sysop` over the in-memory session
  attributes. A compromised session registry cannot elevate a
  caller past the access policy.

### 12.2 Out-of-scope

- Wire-level encryption — depends on the WS deployment (TLS in
  reverse-proxy / systemd unit, plain WS in dev).
- Rate limiting of `message_subscribe` calls — currently
  unbounded; the per-call token verify is the cost.
- IMAP / postoffice — separate service.

---

## 13. Open work

Per `bed/TODO-message-service.md` and `bed/SPEC.md`:

### 13.1 Phase 5 (partial)

F2 key handler in `bbsengine6/io/getch.py` still DB-backed
(`message.get_queue`). Plan is to switch to `message_list_pending`
via bed push.

### 13.2 Phase 8 (open)

- `zoid6/src/zoid6/data/bed.json` — enable the message service by
  default in the zoid6-shipped config.
- `bbsengine6` config docs — describe the new architecture (the
  bed daemon owns the push path; the bbsengine6 TUI reads the
  local cache).

### 13.3 Phase 9 (partial)

- A `--no-bed-fallback` flag to disable TUI polling entirely.
- Documentation that the bbsengine6 TUI can run without a local
  bed instance for read-only display, but notifications require
  bed.

### 13.4 Tests (deferred)

- End-to-end `LISTEN engine_message_recipient` test
  (`test_message_lib.py`).
- `getch.py` no-DB-on-warm-cache smoke test.

### 13.5 v1.1 GA gate (per `bed/SPEC.md:251-257`)

- All 9 phases of `bed/TODO-message-service.md` checked.
- `zoid6` `bed.json` enables message service by default.
- F2 key handler in `getch.py` migrated from `message.get_queue`
  (DB) to `message_list_pending` (bed push).
- End-to-end DB LISTEN test in `test_message_lib.py`.

### 13.6 Future (v2+)

- Multi-process bed fanout — would need a shared subscription
  registry (etcd / Redis / PG advisory locks).
- Replay / queue persistence for disconnected clients — current
  workaround is `message_list_pending` on reconnect.
- GPG key support for message signing — tracked in
  `bbsengine6/TODO.md` Section 657, engine-side.

---

## 14. CLI tool — `bed message`

The `bed message` console script is the operator-facing surface for
the unified message system. It mirrors the bank tool's two-backend
shape: one CLI, two transports, the same wire-protocol vocabulary as
the server-side `MessageService` and the same `bbsengine6.message.*`
calls as the TUI's direct-DB path.

### 14.1 Entry point

```
pip install .
# registers 'message' as a console-script entry point
```

| Script    | Module                    | Purpose                                       |
|-----------|---------------------------|-----------------------------------------------|
| `message` | `bed.tools.message:main`  | subscribe / pending / send / mark_read / mark_delivered / watch |

File: `bed/src/bed/tools/message.py`.

### 14.2 Subcommand vocabulary

The CLI exposes seven subcommands. Each is mapped to a backend
through `_BED_ONLY_SUBCMDS` and `_DIRECT_ONLY_SUBCMDS`
(`message.py:79-80`):

| Subcommand       | Backend  | Auth required | Handler                         |
|------------------|----------|---------------|---------------------------------|
| `subscribe`      | `bed`    | yes           | `message_subscribe`             |
| `unsubscribe`    | `bed`    | yes           | `message_unsubscribe`           |
| `watch`          | `bed`    | yes           | `message_watch`                 |
| `pending`        | either   | yes (bed)     | `message_pending`               |
| `send`           | direct   | no            | `message_send`                  |
| `mark_read`      | direct   | no            | `message_mark_read`             |
| `mark_delivered` | direct   | no            | `message_mark_delivered`        |

Two subcommands straddle the line:

- `pending` works on either backend: in `bed` mode it calls the
  WS handler (the live view from the server); in `direct` mode it
  calls `bbsengine6.message.get_pending_messages(moniker)` against
  the local DB. Both return the same shape.
- The DB-only subcommands (`send` / `mark_read` / `mark_delivered`)
  are forced to `direct` mode regardless of the operator's flags
  (see 14.4).

### 14.3 Backend selection

`main_with_args` (`message.py:820-869`) resolves the backend before
any subcommand work:

1. Read `args.subcommand`.
2. If the subcommand is in `_DIRECT_ONLY_SUBCMDS`, set
   `args.direct = True` (see 14.4).
3. Call `bed.tools._routing.select_backend(args)`. With
   `args.direct = True`, the bed probe is skipped and the backend is
   `"direct"`. With `args.direct = False`, `select_backend` probes
   `args.bed_host:args.bed_port`; on success the backend is `"bed"`,
   on failure `BedNotReachable` is raised and the CLI exits non-zero
   with the bundled operator message.
4. Run `_backend_guard(args, sub)` to reject subcommands the chosen
   backend cannot service (e.g. `subscribe` in `direct` mode).
5. If the backend is `"bed"` and the subcommand needs a session
   (`subscribe` / `unsubscribe` / `watch` / `pending`), call
   `_authenticate_ws(args)` to bind the token to the WS via
   `auth reconnect`.
6. Resolve the actor moniker via `_resolve_moniker(args)`
   (precedence: `--moniker` > claim-derived > `member.getcurrentmoniker`).
7. Dispatch to the subcommand handler.

### 14.4 DB-only subcommands are forced to direct mode

`send`, `mark_read`, and `mark_delivered` are DB-only: bed's
`MessageService` registers only `message_subscribe` /
`message_unsubscribe` / `message_list_pending` (Section 3.1), so
routing these ops through the WS would always 404. New messages flow
through the local DB and surface to bed via the
`engine_message_recipient` NOTIFY trigger, so there is no
server-side wire handler for them.

Previously the CLI required `--direct` to route them through the
local DB. With the bed daemon unreachable (no `--direct` set),
`select_backend` raised `BedNotReachable` and the CLI exited non-zero
even though the op needed no daemon. The fix lives at the top of
`main_with_args` (`message.py:835-838`):

```python
sub = getattr(args, "subcommand", None)

if sub in _DIRECT_ONLY_SUBCMDS:
    args.direct = True
```

Forcing `args.direct = True` before `select_backend` runs means
the bed probe is skipped entirely, the operator never has to pass
`--direct`, and the "bed unreachable; rerun with --direct" exit no
longer fires for these subcommands. The `_backend_guard` rejection
of direct subcommands in `bed` mode becomes unreachable for these
three subs (kept as a defensive no-op).

`_DIRECT_ONLY_SUBCMDS` (`message.py:80`) is the single source of
truth for which subcommands force direct mode. Adding a new
DB-only subcommand means adding it to the set; the rest of the
auto-routing follows from that one line.

### 14.5 CLI flags

The CLI inherits every flag the bank tool exposes (mirrors
`bed.tools._routing.build_client_args`):

```
--bed-host HOST                default: localhost
--bed-port PORT                default: 8765
--bed-path PATH                default: /
--bed-call-timeout SECONDS     default: 5.0
--bed-probe-timeout SECONDS    default: 0.25
--direct                       (forced on for DB-only subcommands; optional otherwise)
--token-file PATH              default: $XDG_RUNTIME_DIR/bed.token or /tmp/bed-<uid>/bed.token
--moniker NAME                 target member moniker (defaults to current user)
--sysop                        bypass sysop privilege check
--debug                        enable debug logging
```

Per-subcommand flags (`buildargs`, `message.py:92-200`):

| Subcommand       | Flags                                                                   |
|------------------|-------------------------------------------------------------------------|
| `send`           | `--to MONIKER` (repeatable), `--channel NAME` (default `cli.message`), `--urgency {ROUTINE,IMPORTANT,URGENT,CRITICAL}` (default `ROUTINE`), `--template BODY`, `--content BODY` |
| `mark_read`      | `--message-id ID` (required)                                            |
| `mark_delivered` | `--message-id ID` (required)                                            |

`--content` and `--template` are mutually exclusive on `send`.
Exactly one must be set, otherwise `_resolve_send_content`
(`message.py:622-647`) renders an error and exits non-zero.

### 14.6 Authorization on the CLI

The CLI runs the same per-op policy the server does, just on the
client side. There are two gates:

- `_check_access(args, op, session_moniker=moniker, **kwargs)`
  (`message.py:256-300`) is the WS-op gate. It builds a synthetic
  `SessionState` from the claim-derived or `--moniker` flag and
  delegates to `bbsengine6.message.access(args, op, session=synth,
  message=msg)`. Used by `subscribe` / `unsubscribe` / `pending` /
  `watch`.
- `_check_self_or_sysop(args, op, actor, target)`
  (`message.py:302-336`) is the DB-only gate. `bbsengine6.message.access`
  only recognizes the three wire-protocol verbs, so for
  `send` / `mark_read` / `mark_delivered` the CLI applies the same
  self-or-sysop rule locally: a member may target themselves; a
  sysop (claim-derived or `--sysop` flag) may target anyone. Mirrors
  the per-op policy for the WS verbs without requiring a
  bbsengine6-side extension.

The session-bound gate (empty actor moniker → denial) is checked
first in `_check_access`, mirroring the WS handler's session gate so
the two surfaces agree on what "unauthenticated" means.

### 14.7 Token lifecycle on the CLI

In `bed` mode the CLI drives the same auth flow the server expects:

1. `_token.ensure_token_file_arg(args)` fills `args.token_file`
   with the XDG / `/tmp/bed-<uid>` default when the operator didn't
   pass `--token-file`.
2. `_token.read_token_file(args.token_file)` reads the bearer
   token. Missing or empty → `_MISSING_TOKEN_HINT` error and a
   non-zero exit.
3. `BedAuthServiceClient(get_bed_connection(args)).reconnect(token)`
   rebinds the token to the WS. On success, the claim-derived
   `moniker` / `is_sysop` are stashed on `args` (so `_check_access`
   can use them).
4. If the server rotated the token (reply.token differs from the
   input), write the rotated token back to the file at mode 0600 so
   subsequent runs pick it up.
5. The same token is sent on every subsequent WS call
   (`BedMessageServiceClient.subscribe` /
   `BedMessageServiceClient.list_pending`).

In `direct` mode no token is required; the actor moniker is
resolved from `--moniker` or the local DB.

### 14.8 CLI subcommand handlers

- `message_subscribe` (`message.py:549-562`) — runs the access
  gate, then `_BedMessageFacade.subscribe(moniker)`. Prints
  `"subscribed moniker=..."` or `"... (already subscribed)"` on
  idempotency.
- `message_unsubscribe` (`message.py:565-579`) — symmetric to
  subscribe; prints `"unsubscribed moniker=..."` or `"no active
  subscription"`.
- `message_pending` (`message.py:600-619`) — backend-aware. In
  `bed` mode calls `_BedMessageFacade.list_pending(moniker)`; in
  `direct` mode calls `bbsengine6.message.get_pending_messages(
  moniker, limit=100)`. Renders via `_render_pending`
  (`message.py:583-597`): `#id  [URGENCY]  from=<sender>  status=<s>
  <content>` per row.
- `message_send` (`message.py:649-699`) — runs
  `_check_self_or_sysop("send", actor, actor)`, resolves the body
  (raw `--content` or rendered `--template`), then
  `bbsengine6.message.store_message_with_checks(...)`. Prints
  `"stored #<id> channel=... urgency=... to=..."` on success;
  rate-limit / system-disabled / no-recipients cases print a
  one-line error and exit non-zero.
- `message_mark_read` / `message_mark_delivered`
  (`message.py:714-748`) — runs `_check_self_or_sysop` against the
  resolved target (`--moniker` for sysops, actor otherwise), then
  `bbsengine6.message.mark_read` / `mark_delivered`. Print
  `"marked #<id> read for <target>"`.
- `message_watch` (`message.py:752-806`) — runs the access gate,
  calls `_BedMessageFacade.subscribe(moniker)`, then registers a
  `_push` handler on the underlying `BedConnection` and tails live
  pushes until interrupted. Prints `msg #<id>  [URGENCY]  status=...
  to=...` per envelope. On exit (Ctrl-C / EOF / disconnect)
  unsubscribes the WS binding and prints `"stopped watching"`.

### 14.9 Why two backends

The WS-bound ops (`subscribe` / `unsubscribe` / `list_pending` /
`watch`) need a live bed daemon because the operator is asking the
server to register a NOTIFY fanout or to surface live pushes. The
DB-only ops (`send` / `mark_read` / `mark_delivered`) are pure DB
writes/updates — there's no reason to bounce them through the WS.

Forcing direct mode for the DB-only subcommands (14.4) keeps the CLI
honest: it never asks the operator to pass `--direct` for ops that
have no daemon-side surface, and it never exits with "bed
unreachable" when bed isn't needed.

The pending list straddles both because both views are useful: in
`bed` mode the server reads the same DB rows the CLI would, so the
two paths return the same data; in `direct` mode the CLI reads
the DB directly without paying a WS round-trip. Both are correct.

---

## 15. CLI vs server — terminology

A small glossary so the same word doesn't mean two different things
across the spec:

| Term                  | Server (`bed.api.message`)           | CLI (`bed.tools.message`)                |
|-----------------------|--------------------------------------|------------------------------------------|
| `moniker` (in message)| recipient of a NOTIFY push           | actor's moniker (sender / owner)         |
| `subscribe`           | add to per-moniker subscription map  | call `message_subscribe` over WS         |
| `unsubscribe`         | drop from per-moniker map            | call `message_unsubscribe` over WS       |
| `send`                | n/a (no handler)                     | store a new message in local DB          |
| `mark_read` / `mark_delivered` | n/a (no handler)            | update `__message_recipient` in local DB |
| `pending`             | read pending rows for a moniker      | backend-aware; WS or DB                  |
| `watch`               | n/a (server pushes via subscription) | subscribe + tail live pushes             |

---

## 16. File map

| File                                                   | Role                                      |
|--------------------------------------------------------|-------------------------------------------|
| `bed/src/bed/api/message.py`                           | `MessageService`, `_listen_loop`, handlers, 5-gate `_check_access` |
| `bed/src/bed/tools/message.py`                         | `bed message` CLI (Section 14); two-backend routing + auto-direct for DB-only subcommands |
| `bed/src/bed/main.py:380-509`                          | `BED.start()` wires MessageService       |
| `bed/src/bed/main.py:647-685, 770-775`                 | `BED.stop()` / cleanup paths              |
| `bed/src/bed/lib.py`                                   | `--no-message-service` CLI flag           |
| `bed/src/bed/data/bed.json:30-34`                      | `message_service` config block            |
| `bed/src/bed/client/messageservice.py`                 | `BedMessageServiceClient`, push handler   |
| `bed/src/bed/client/connection.py`                     | `subscribe` / `unsubscribe` push-handler plumbing |
| `bbsengine6/py/src/bbsengine6/sql/message.sql:52-92`   | NOTIFY trigger function and triggers      |
| `bbsengine6/py/src/bbsengine6/message/__init__.py:155-219` | `bbsengine6.message.access()` policy  |
| `bbsengine6/py/src/bbsengine6/message/lib.py:627-680`  | Local unread cache                        |
| `bbsengine6/py/src/bbsengine6/message/lib.py:362-421` | `get_pending_messages` (DB fetch for list_pending) |
| `bed/src/bed/tests/test_message_service.py` (~2,360 LOC) | Service unit tests                     |
| `bbsengine6/py/tests/test_message_local_cache.py`      | Local cache tests                         |
| `bed/TODO-message-service.md`                          | 9-phase plan                              |
| `bed/SPEC.md`                                          | Bed daemon entry-point spec               |
| `bed/docs/BED_AUTH.md`                                 | Bearer-token protocol (Gate 2/3)          |
| `bbsengine6/handbook/specs/notify.md`                  | Unified message system (upstream)         |
| `bed/CHANGELOG.md`                                     | Release history                           |

---

## 17. Versioning

This spec tracks the bed daemon. Phase gates per `bed/SPEC.md`:

- **v1.0** (current stable) — daemon core, AuthService,
  MessageService, BankService, FHS install.
- **v1.1** (in flight) — MessageService GA + cross-repo adoption.
  Gate: all 9 phases of `bed/TODO-message-service.md` checked;
  zoid6 `bed.json` enables by default; F2 handler migrated;
  end-to-end DB LISTEN test.
- **v1.2 / v1.3 / v1.4 / v2** — design-only; not affected by this
  spec beyond what is listed in Section 13.
