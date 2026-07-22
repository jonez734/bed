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
`__message_recipient` fire `pg_notify()`.

- [ ] Add `pg_notify_channel()` function in `message.sql` to centralize
      the channel name (`engine_message_recipient`).
- [ ] Add AFTER INSERT trigger on `__message_recipient` that calls
      `pg_notify('engine_message_recipient', json_build_object(
        'message_id', NEW.message_id,
        'recipient_moniker', NEW.recipient_moniker,
        'status', NEW.status,
        'urgency', (SELECT urgency FROM engine.__message WHERE id = NEW.message_id),
        'datestamp', NEW.datedelivered
      )::text)`.
- [ ] Add AFTER UPDATE trigger on `__message_recipient` for status changes
      (read/delivered).
- [ ] Add `LISTEN engine_message_recipient` test in `test_message_lib.py`.

## Phase 2: Bed MessageService

Add a `bed.api.message.MessageService` that owns a long-lived async PG
connection, subscribes to `engine_message_recipient`, and fans out to
connected WebSocket clients by moniker.

- [ ] Create `bed/src/bed/api/message.py` with `MessageService(BaseService)`:
  - [ ] `__init__(args, session_manager)` — store args/sessions, init
        internal state (`self._subscribed_monikers`, `self._listener_task`).
  - [ ] `register_all(server)` — call `server.register_service(self,
        ["message_subscribe", "message_unsubscribe", "message_list_pending"])`.
  - [ ] `async handle_message(server, websocket, path, message)` — switch
        on `message.get("type")`, dispatch to internal handlers.
  - [ ] `async _handle_subscribe(websocket, message)` — store the
        websocket in `self._subscribed_monikers[moniker]`, return
        `{"type": "message_subscribe_result", "moniker": ..., "ok": True}`.
  - [ ] `async _handle_unsubscribe(websocket, message)` — remove the
        subscription, return result envelope.
  - [ ] `async _handle_list_pending(websocket, message)` — query
        `message.get_pending_messages(moniker)`, return list.
  - [ ] `async start_listener()` — open dedicated `AsyncConnection`,
        execute `LISTEN engine_message_recipient`, then loop
        `await conn.notifies()` in a `while not stop_event.is_set()` block.
  - [ ] `async stop_listener()` — set stop event, cancel task, close
        async connection.
  - [ ] `async _dispatch_notification(payload: str)` — parse the JSON
        payload, look up `self._subscribed_monikers[recipient_moniker]`,
        if present: `await server.send_to(ws, {"type": "message",
          "channel": ..., "message_id": ..., "urgency": ...,
          "request_id": "<server>:<seq>"})`.
  - [ ] `HANDLED_TYPES = ("message_subscribe", "message_unsubscribe",
        "message_list_pending")`.

## Phase 3: Wire MessageService into BED

- [ ] Modify `bed/src/bed/main.py:BED._start_services()` (or equivalent):
  - [ ] After `AuthService.register_all` and after the
        `MessageRouterClass.register_all`, create a `MessageService` and
        call its `register_all(self.server)`.
  - [ ] Start the listener task: `asyncio.create_task(
        self.message_service.start_listener())`.
  - [ ] Store the task handle on `self._message_listener_task`.
  - [ ] Cancel the task in `BED.stop()` (similar to `_gc_task` cleanup
        at `bed/main.py:403-409`).
- [ ] Add `message_service` to `bed/src/bed/data/bed.json` so it's
  discoverable (even if disabled by default).

## Phase 4: Add `send_to` to WebSocketServer

The `MessageService._dispatch_notification` needs a way to send a
message to a specific websocket. The current `WebSocketServer` may only
have `broadcast`. Check `bbsengine6/net/transport.py:663-697`.

- [ ] Verify `WebSocketServer` has a `send_to(websocket, message)` method.
- [ ] If not, add it: lock the websockets set, send JSON to the target
  websocket only, handle disconnection gracefully.

## Phase 5: Update bbsengine6 consumers

Replace polling with WebSocket-subscription on connect.

- [ ] Modify `io/getch.py:_check_notifications()`:
  - [ ] On first call, attempt to subscribe via the bed WebSocket
        client (send `{"type": "message_subscribe", "moniker": ...}`).
  - [ ] Maintain a local in-memory counter for unread messages; bed
        pushes increments via `{"type": "message", ...}` envelopes.
  - [ ] `_check_notifications` becomes: read the local counter, no DB
        hit at all.
- [ ] Modify `bottombar.py:_get_notification_status()`:
  - [ ] Return from the same local counter used by `getch.py`.
  - [ ] Status string: `f"F2: messages ({count})"` (same as today).
- [ ] Update the F2 key handler in `getch.py` to:
  - [ ] Send `{"type": "message_list_pending", "moniker": ...}` to bed.
  - [ ] Display the returned list with colors from `message.*color` echo
        variables (already added in migration Phase 3).
  - [ ] Mark messages read by calling `bbsengine6.message.mark_read()`
        after display.

## Phase 6: Bed client subscription

The current `BedConnection` (`bed/client/connection.py:108-157`) is
request/reply only. Add a subscription mode.

- [ ] Modify `bed/src/bed/client/connection.py`:
  - [ ] Add `subscribe(handler: Callable[[dict], None])` method that
        starts a background `asyncio.create_task(self._recv_loop(handler))`.
  - [ ] `_recv_loop` consumes server-pushed messages (no `request_id`)
        and dispatches to `handler` without closing the connection.
  - [ ] Keep `send()` working in parallel with the recv loop.
  - [ ] On disconnect, both the recv loop and pending sends raise
        `BedUnavailable`.

## Phase 7: Tests

- [ ] `bed/src/bed/tests/test_message_service.py`:
  - [ ] `test_subscribe_unsubscribe` — register websocket, send
        `message_subscribe`, verify state.
  - [ ] `test_dispatch_notification` — manually inject a payload,
        verify the right websocket receives it.
  - [ ] `test_list_pending` — pre-populate `__message_recipient`,
        verify `message_list_pending` returns the messages.
  - [ ] `test_listener_lifecycle` — start, stop, verify the async
        connection is closed cleanly.
- [ ] `test_message_lib.py`: add a test that `LISTEN engine_message_recipient`
  receives a payload after `store_message()` + recipient insert.
- [ ] `test_getch.py` (new): test that `_check_notifications` no longer
  hits the DB on subsequent calls when bed subscription is active.
- [ ] `test_bottombar.py`: update to verify the status string is sourced
  from the local counter, not from `message.get_unread_count()`.

## Phase 8: Configuration

- [ ] Update `bed/src/bed/data/bed.json`:
  - [ ] Add `message_service` entry under `services` (or a new
        top-level `notification` section), `enabled: true`,
        `modulepath: "bed.api.message"`.
- [ ] Update `zoid6/src/zoid6/data/bed.json` to enable the new service.
- [ ] Update `bbsengine6` config docs to describe the new architecture.

## Phase 9: Migration of existing callers

- [ ] Remove the DB-poll code paths from `getch.py` and `bottombar.py`
  (keep them as fallback for when bed is unreachable).
- [ ] Add a `--no-bed-fallback` flag to disable polling entirely.
- [ ] Document: the bbsengine6 TUI can now run without a local bed
  instance for read-only display, but notifications require bed.

## Out of scope

- IMAP email polling (separate `postoffice` service, already exists).
- Multi-process bed fanout (single bed instance is sufficient for now).
- Replay/queue persistence for disconnected clients (use
  `message_list_pending` on reconnect — already part of Phase 5).
- Authentication/authorization for `message_subscribe` (currently
  assume the WebSocket is already authenticated via bed's existing
  auth handshake).

## Dependencies

- Phase 1 (PG NOTIFY triggers) is independent.
- Phase 2 (MessageService) depends on Phase 1.
- Phase 3 (BED integration) depends on Phase 2.
- Phase 4 (send_to) is independent and can be done first.
- Phase 5 (consumers) depends on Phase 3 and Phase 6.
- Phase 6 (client subscription) is independent and can be done in
  parallel with Phase 2-3.
- Phase 7 (tests) follows the implementation.
- Phase 8-9 are deployment/migration.
