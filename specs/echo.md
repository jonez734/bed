# bed echo service — Specification

> Server-push text-channel envelope with ack-based backpressure.

## 1. Why `bed` owns this

Every BED-hosted game (empyre, casino, mistermcfeely, murdermotel,
zoid6) needs a way to push a render fragment to the connected
client and to know that the fragment was displayed before
continuing (e.g. before issuing the next IO request). Owning the
`echo`/`echo_ack` pair in `bed` gives every game:

- a stable, documented fragment envelope,
- a uniform backpressure / flow-control primitive (`echo_ack`),
- free transport-level framing (chunking, ordering,
  reconnect-resume),
- one place to add tracing, metrics, and rate limiting.

`bed` defines the **envelope and transport contract**. Games
define the **content schema** (`text`, `style`, `style_color`,
`mci`, etc.) inside `echo.payload`.

## 2. Wire shape

```json
# Server → client: push one render fragment (or a batch via echo_batch)
S→C {"type":"echo",
     "request_id":"r42",
     "stream":"main",
     "seq":17,
     "payload":{
       "text":"Welcome to Empyre.\n",
       "style":{"fg":"white","bg":"black","bold":false,"underline":false},
       "mci":{"code":"{f6}","args":{}}},
     "flush":true,
     "ts":"2026-06-25T11:30:01.123Z"}

# Server → client: a batch of fragments sharing one request_id
S→C {"type":"echo_batch",
     "request_id":"r42",
     "stream":"main",
     "seq_start":17,
     "fragments":[
        {"seq":17,"text":"Welcome "},
        {"seq":18,"text":"to "},
        {"seq":19,"text":"Empyre.\n","flush":true}]}

# Client → server: I rendered the fragment / batch
C→S {"type":"echo_ack",
     "request_id":"r42",
     "last_seq":19,
     "rendered_at":"2026-06-25T11:30:01.456Z"}

# Client → server: I can't render this fragment
C→S {"type":"echo_nack",
     "request_id":"r42",
     "last_seq":18,
     "reason":"unknown_mci_code",
     "detail":"code={f99}"}

# Server → client: cancel a pending echo
S→C {"type":"echo_cancel",
     "request_id":"r42",
     "reason":"superseded"}
```

## 3. Semantics

- **At-least-once, in-order delivery.** `seq` is monotonic per
  `stream` per session. `request_id` is monotonic per session.
- **One outstanding `echo_ack` per session.** The server may have
  multiple `echo`s in flight across streams (`main`, `bottombar`,
  `statusline`) but a single client only ever owes one `echo_ack`
  for the most-recently-pushed fragment.
- **`flush:true` means "I'm about to ask the client for input
  next".** The client must render every prior `seq` for that
  stream before sending `echo_ack`. `flush:false` means "more
  fragments coming, no need to ack yet."
- **Reconnect resume.** On `reconnect`, the server replays any
  unacked `echo`/`echo_batch` from the persistent request table.
  The client renders the replay, then sends a single `echo_ack`
  with the highest `seq` it actually showed. Server resumes from
  `last_seq + 1`.
- **Cancellation.** When the game replaces a screen, the server
  sends `echo_cancel` for any in-flight `request_id` that is no
  longer relevant.

## 4. Streams

A session has up to three named render streams, each with its
own `seq`:

- `main` — the primary game UI (menus, listboxes, prompts).
- `bottombar` — BBS-style status bar. Wire payload shape lives
  in `bbsengine6/TODO-BOTTOMBAR.md` Phase 5b
  (`echo{stream:"bottombar"}`).
- `statusline` — optional, for in-game top-of-screen status
  (turn count, bank balance, unread mail).

Each stream is independent: `main` can be paused waiting on
`inputchoice_reply` while `bottombar` continues to receive updates.

## 5. Backpressure rules

- The server **may** issue IO requests (`inputstring`,
  `inputchoice`, etc.) only after the matching `echo_ack` arrives
  for the most-recent `flush:true` echo. This guarantees the
  client has rendered the prompt before the server blocks on input.
- The server **may** push `flush:false` echoes as fast as it
  likes; the client acks them at its own cadence (e.g. on natural
  render boundaries, or on a 50ms timer).
- The client **may** send a single `echo_ack` for a batch by
  setting `last_seq` to the highest `seq` it actually rendered.
  The server treats every `seq` ≤ `last_seq` as acked.
- If the server times out waiting for `echo_ack` (configurable,
  default 30s), it sends `echo_cancel{reason:"ack_timeout"}` and
  proceeds with an error envelope to the client.

## 6. Style / MCI compatibility

- The `payload.style` field is the **canonical** form.
  `payload.mci` is a **legacy escape hatch** for fragments that
  originate from a `bbsengine6.io` shim and haven't been
  transcoded yet.
- The MCI codec MUST be a strict superset of `bbsengine6.io.echo`'s
  tokenizer.
- v1 default: the thin client renders `text` only and ignores
  `style` / `mci`; the server is responsible for any
  pre-transcoding it wants to do.

## 7. Decisions (v1)

- At-least-once delivery with reconnect-resume; in-order per
  stream; one outstanding `flush:true` per session.
- `echo` and `echo_ack` are mandatory on every connection — no
  game may use a different text-push primitive.
- `flush:false` echoes may be dropped by the server under memory
  pressure (low watermark); `flush:true` echoes are always
  delivered.
- `payload.mci` round-trips through the codec.
- 30s default `ack_timeout`, configurable via `bed.json`
  `echo.ack_timeout` and CLI flag `--echo-ack-timeout`.
- Future: per-game style palettes.

## 8. Open follow-ups

- [ ] `bed/api/echo.py` — `EchoService` (registers `echo`,
      `echo_batch`, `echo_ack`, `echo_nack`, `echo_cancel`).
- [ ] `bed/api/fragment.py` — `Fragment` dataclass + `FragmentQueue`.
- [ ] `bed/api/style.py` — canonical style schema + MCI codec stub
      (round-trips `{f6}` / `{labelcolor}` from `bbsengine6.io.echo`).
- [ ] `bed.api.session` — per-session monotonic `request_id`
      counter + per-session `pending_ack` future (shared with
      bearer-token pending-request table).
- [ ] `bed.main.BED.start` — register `EchoService` after
      `AuthService` and before any game router.
- [ ] `bed/tests/test_echo_service.py` — in-order delivery,
      batch chunking, `flush` semantics, `echo_cancel`
      supersession, reconnect-resume, `echo_nack` handling,
      multi-stream independence.
- [ ] `bed/tests/test_echo_mci_roundtrip.py` — MCI codec round-trip.
- [ ] MCI codec prerequisite: `bbsengine6.io.mci.parse` (see
      `handbook/ARCHITECTURE.md` §4).

## 9. Adoption

- **empyre**: primary driver (Phase 1 of `empyre/TODO.md`).
- **casino**: lobby chat and table-event pushes.
- **mistermcfeely (postoffice)**: "new mail" notifications and
  folder rendering.
- **murdermotel**: narrative pushes and status-line updates.
- **zoid6**: dashboard tiles and shared-wallet notifications.
- **bbsengine6 bank service**: transaction notifications.

## 10. See also

- `../handbook/BED_AUTH.md` — bearer-token protocol reference.
- `../handbook/ARCHITECTURE.md` — dep graph + code map.
- `key_f2.md` — sibling session-level new-messages query.
- `menu.md` — sibling single-pick option list.
- `sink.md` — sibling BEDSink / ThinClientIOSink.
- `bbsengine6/handbook/specs/` — sibling spec tree.
