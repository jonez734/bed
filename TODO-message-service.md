# TODO: Server-push message notifications via bed + PostgreSQL LISTEN/NOTIFY

Replace the idle-poll notification pattern in `getch.py`/`bottombar.py`
with server-push via `bed` subscribing to PostgreSQL `LISTEN`/`NOTIFY`
triggers on `__message_recipient` inserts.

## Current state

- `getch.py:_check_notifications()` and `bottombar.py:_get_notification_status()`
  poll the DB on every input loop tick.
- `bed` has no PostgreSQL LISTEN/NOTIFY infrastructure.
- `bbsengine6` has an `AsyncConnectionPool` at `database.py:2152-2196` that bed
  does not currently use.
- Bed services register via `server.register_service(service, msg_types)` and
  subclass `BaseService` (`bed/api/handler.py`).

## Phase 1: PostgreSQL NOTIFY triggers

Add triggers to `message.sql` / `message_groups.sql` so INSERTs/UPDATEs on
`__message_recipient` fire `pg_notify()`. **STATUS: COMPLETE**

- [x] Add `pg_notify_channel()` function in `message.sql` to centralize
      the channel name (`engine_message_recipient`).
- [x] Add AFTER INSERT trigger on `__message_recipient` that calls
      `pg_notify('engine_message_recipient', json_build_object(...))`.
- [x] Add AFTER UPDATE trigger on `__message_recipient` for status changes
      (read/delivered).
- [ ] Add `LISTEN engine_message_recipient` test in `test_message_lib.py`.

## Phase 2: Bed MessageService

Add a `bed.api.message.MessageService` that owns a long-lived async PG
connection, subscribes to `engine_message_recipient`, and fans out to
connected WebSocket clients by moniker. **STATUS: COMPLETE**

- [x] Create `bed/src/bed/api/message.py` with `MessageService(BaseService)`:
  - [x] `__init__(args, session_manager)` — store args/sessions, init
        internal state.
  - [x] `register_all(server)` — call `server.register_service(self, [...])`.
  - [x] `async handle_message(server, websocket, path, message)`.
  - [x] `async _handle_subscribe(websocket, message)`.
  - [x] `async _handle_unsubscribe(websocket, message)`.
  - [x] `async _handle_list_pending(message)`.
  - [x] `async start_listener()` — open dedicated `AsyncConnection`,
        LISTEN, loop `await conn.notifies()`.
  - [x] `async stop_listener()` — cleanup.
  - [x] `async _dispatch_notification(payload: str)` — parse JSON, fan
        out via `server.send_to(ws, ...)`.
  - [x] `HANDLED_TYPES = ("message_subscribe", "message_unsubscribe",
        "message_list_pending")`.

## Phase 3: Wire MessageService into BED

**STATUS: COMPLETE**

- [x] Modify `bed/src/bed/main.py:BED.start()`:
  - [x] After `MessageRouterClass.register_all`, create `MessageService`
        and call `register_all(self.server)`.
  - [x] Start listener task.
  - [x] Cleanup in `BED.stop()`.
- [x] Add `message_service` to `bed/src/bed/data/bed.json` (enabled by
      default).
- [x] Add `--no-message-service` CLI flag in `bed/src/bed/lib.py`.

## Phase 4: Add `send_to` to WebSocketServer

**STATUS: COMPLETE (already existed)**

- [x] `WebSocketServer.send_to(websocket, message)` already at
      `bbsengine6/net/transport.py:699-701`.

## Phase 5: Update bbsengine6 consumers

**STATUS: PARTIAL** — local cache is in place; F2 handler refactor pending.

- [x] Modify `io/getch.py:_check_notifications()`:
  - [x] Read from local cache first.
  - [x] Fall back to DB on first call (cold cache).
- [x] Modify `bottombar.py:_get_notification_status()`:
  - [x] Read from local cache first.
  - [x] Same fall-back behavior.
- [ ] Update the F2 key handler in `getch.py` to send
      `message_list_pending` to bed (TBD; current behavior fetches via
      `message.get_queue` which is DB-backed; future work to switch to
      bed push).
- [x] Add `bbsengine6.message.get_local_unread_count`,
      `set_local_unread_count`, `bump_local_unread_count`,
      `clear_local_unread_cache`.

> **Phase 11 (2026-09-01, bbsengine6):** the local-cache functions
> referenced above (`get_local_unread_count`,
> `set_local_unread_count`, `bump_local_unread_count`,
> `clear_local_unread_cache`) moved from
> `bbsengine6/message/lib.py` to `bbsengine6/message/cache.py`.
> The package surface is unchanged. See
> `bbsengine6/TODO-message-migration.md` Phase 11.

## Phase 6: Bed client subscription

**STATUS: COMPLETE**

- [x] Modify `bed/src/bed/client/connection.py`:
  - [x] Add `subscribe(handler)` and `unsubscribe(handler)` methods.
  - [x] Background `_recv_loop` consumes server-pushed messages.
  - [x] `send()` uses `_recv_match` to skip non-matching messages
        (pushes them to handlers).
  - [x] On disconnect, recv loop and sends both fail.

## Phase 7: Tests

- [x] `bed/src/bed/tests/test_message_service.py`:
  - [x] `test_message_service_registers_handled_types`.
  - [x] `test_subscribe_adds_to_subscribed_map`.
  - [x] `test_unsubscribe_removes_from_map`.
  - [x] `test_dispatch_notification_sends_to_subscribed_websocket`.
  - [x] `test_dispatch_notification_no_subscriber_is_noop`.
  - [x] `test_dispatch_notification_bad_payload_is_noop`.
  - [x] `test_dispatch_notification_removes_dead_subscriber`.
  - [x] `test_list_pending_returns_db_messages`.
  - [x] `test_list_pending_rejects_empty_moniker`.
  - [x] `test_lifecycle_start_stop_is_idempotent`.
- [x] `bbsengine6/py/tests/test_message_local_cache.py`:
  - [x] Cache init, set, get, bump, clear, separation across monikers.
- [ ] `test_message_lib.py`: end-to-end `LISTEN engine_message_recipient`
      test (requires live DB; deferred).
- [ ] `test_getch.py`: verify `_check_notifications` no longer hits DB
      on warm cache (deferred; covered by local cache tests).

## Phase 8: Configuration

**STATUS: COMPLETE**

- [x] Update `bed/src/bed/data/bed.json`:
  - [x] Add `message_service` entry, `enabled: true`,
        `modulepath: "bed.api.message"`.
- [ ] Update `zoid6/src/zoid6/data/bed.json` to enable the new service.
- [ ] Update `bbsengine6` config docs to describe the new architecture.

## Phase 9: Migration of existing callers

**STATUS: PARTIAL**

- [x] Keep DB-poll as fallback for when bed is unreachable.
- [ ] Add a `--no-bed-fallback` flag to disable polling entirely.
- [ ] Document: the bbsengine6 TUI can now run without a local bed
      instance for read-only display, but notifications require bed.

## Summary

The server-push notification pipeline is functional:

```
[INSERT/UPDATE __message_recipient]
        |
        v
[AFTER trigger fires pg_notify('engine_message_recipient', ...)]
        |
        v
[bed MessageService LISTEN loop] -- [psycopg AsyncConnection]
        |
        v
[MessageService._dispatch_notification: lookup ws by recipient_moniker]
        |
        v
[WebSocketServer.send_to(ws, {"type": "message", ...})]
        |
        v
[BedMessageServiceClient push handler updates local cache]
        |
        v
[bbsengine6.message.set_local_unread_count / bump_local_unread_count]
        |
        v
[getch.py / bottombar.py read from local cache — no DB hit]
```

Tests cover subscription state, dispatch logic, list_pending, and
lifecycle. Local cache tests cover the read path. End-to-end DB LISTEN
tests are deferred (require live PG; existing tests cover the
in-process behavior).

## Out of scope

- IMAP email polling (separate `postoffice` service, already exists).
- Multi-process bed fanout (single bed instance is sufficient for now).
- Replay/queue persistence for disconnected clients (use
  `message_list_pending` on reconnect — part of Phase 5, deferred).
- Authentication/authorization for `message_subscribe` (currently
  assume the WebSocket is already authenticated via bed's existing
  auth handshake).
