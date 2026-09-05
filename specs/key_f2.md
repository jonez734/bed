# bed key_f2 service — Specification

> Session-level new-messages query.

## 1. Why this is its own message type

In this monorepo, `KEY_F2` is **not** a per-menu help callback.
It is the **session-level "list new messages from subscribed
channels" key** — analogous to a mail-client inbox refresh or a
chat-client "new messages" indicator. The user can press `F2` at
any time (on the login screen, mid-menu, mid-prompt, mid-input)
and the server returns a list of unread items across whatever
channels / mailboxes / feeds the member is subscribed to.

Because `F2` is session-level (not bound to any `menu`
`request_id`), it has its own message type and is not a field on
the `menu` envelope. The `inputchoice` `f2_handler` kwarg is
**not** projected to the wire at all; no game in this monorepo
passes `f2_handler=` to `inputchoice`.

## 2. Wire shape

```json
# Client → server: I want the list of new messages
C→S {"type":"key_f2"}

# Server → client: here are the new messages (capped at
# bed.json key_f2.max_items, default 50; excess silently dropped
# with a "+N more" footer in `excess`)
S→C {"type":"key_f2_result",
     "count":3,
     "excess":0,
     "items":[
        {"channel":"postoffice:check_mail",
         "from":"sysop",
         "subject":"Welcome to the BBS",
         "date":"2026-06-25T10:00:00Z",
         "preview":"Welcome! …"},
        {"channel":"empyre:island:42",
         "from":"rex",
         "subject":"island discovered",
         "date":"2026-06-25T11:15:00Z",
         "preview":"…"},
        {"channel":"system:announcements",
         "from":"sysop",
         "subject":"maintenance window",
         "date":"2026-06-25T11:30:00Z",
         "preview":"…"}]}

# Server → client: nothing new
S→C {"type":"key_f2_empty",
     "count":0}

# Server → client: the query can't be served
S→C {"type":"key_f2_error",
     "code":"not_authenticated" | "channel_unavailable" | "key_f2_rate_limited",
     "message":"…"}
```

## 3. Channel-source resolution

The server queries all channels the member is subscribed to,
filtered by per-channel `key_f2_visible: true | false` (column
on `engine.__channel`; default `true` for new channels). The
default behavior is **uniform across all games**: any subscribed
channel with `key_f2_visible=true` is included in the query.

A per-`bed.json` allow-list (`key_f2.channel_allow_list`) can be
set to restrict the query to a specific list of channels (e.g.
just `postoffice:check_mail` and `system:announcements`, omitting
game-specific channels). Default: empty list = no restriction
(all subscribed channels).

In v1, the result is a **flat list** (not a paged `listbox`
envelope). If the list grows past `key_f2.max_items` (default
50), the excess is silently dropped and the `excess` field
reports the count.

## 4. Semantics

- `key_f2` is a **session-level** message type. It is not bound
  to any `request_id` and does not resolve any pending IO
  request.
- The user can press `F2` at any time — on the login screen,
  while blocked on a `menu` or `inputstring` or `listbox`, or
  with no pending IO at all. The result is rendered, then the
  user is returned to wherever they were (or to the main screen
  if nothing was pending).
- The server requires an active `auth` / `reconnect` session. A
  `key_f2` from an unauthenticated session returns
  `key_f2_error{code:"not_authenticated"}`.
- The server rate-limits `key_f2` per session (default 5 req/s,
  configurable via `bed.json` `key_f2.rate_limit` and CLI flag
  `--key-f2-rate-limit`); over-limit requests get
  `key_f2_error{code:"key_f2_rate_limited"}`.
- The thin client may pipeline multiple `key_f2` requests (each
  is independent). The server processes them in order and the
  client matches by arrival order.
- `key_f2` does **not** clear the "unread" state of the returned
  items. Marking items as read is a separate operation (e.g. the
  `postoffice:check_mail` channel has its own "mark as read"
  message type, owned by the postoffice service). v1 of `key_f2`
  is read-only.

## 5. Decisions (v1)

- `key_f2` queries all subscribed channels with
  `key_f2_visible=true`, optionally restricted by `bed.json`
  `key_f2.channel_allow_list` (default: no restriction).
- Result is a flat list, capped at `key_f2.max_items` (default
  50), with `excess` reporting the dropped count.
- `key_f2` requires an active auth session. Unauthenticated
  `key_f2` returns `key_f2_error{code:"not_authenticated"}`.
- `key_f2` is rate-limited per session at 5 req/s (configurable
  via `bed.json` `key_f2.rate_limit` and CLI flag
  `--key-f2-rate-limit`).
- `key_f2` does not clear unread state.
- Future: wrap result in a `listbox` envelope for paging when
  `count > key_f2.max_items`. Per-channel `key_f2_priority` for
  ordering items.

## 6. Open follow-ups

- [ ] `bed/api/key_f2.py` — `KeyF2Service` (registers `key_f2`,
      `key_f2_result`, `key_f2_empty`, `key_f2_error`).
- [ ] `bed/api/key_f2_channels.py` — `resolve_channels(args,
      member) -> List[str]` applying `key_f2.channel_allow_list`
      and per-channel `key_f2_visible`.
- [ ] `bed/api/key_f2_items.py` — `build_items(args, member,
      channel) -> List[Item]` querying each channel's source.
- [ ] `bed.main.BED.start` — register `KeyF2Service` after
      `HelpService` and before any game router.
- [ ] `bed/tests/test_key_f2_service.py` — empty result,
      single-item result, multi-channel result, not-authenticated
      error, channel-unavailable error, rate-limit error,
      no-impact-on-pending-menu, `key_f2.max_items` cap,
      `key_f2.channel_allow_list` filter, per-channel
      `key_f2_visible` filter.
- [ ] `bed/tests/test_key_f2_channels.py`,
      `test_key_f2_items.py`.
- [ ] `bed.json` `key_f2` section: `rate_limit: 5`, `max_items:
      50`, `channel_allow_list: []`.

## 7. Adoption

- **murdermotel**: `F2` is the natural "what's happened in the
  motel overnight" key.
- **empyre**: `F2` is the natural "what's happened on my
  islands" key.
- **casino**: `F2` is the natural "tournament announcements /
  open tables" key.
- **mistermcfeely (postoffice)**: `F2` is the natural "new mail"
  key.
- **zoid6**: `F2` is the natural "dashboard notifications" key.

## 8. See also

- `menu.md` — sibling single-pick option list.
- `help.md` — sibling F1 help-on-demand.
- `echo.md` — sibling push channel.
- `message.md` — sibling server-push notification pipeline.
- `../handbook/ARCHITECTURE.md` — dep graph.
