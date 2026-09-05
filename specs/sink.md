# bed sink integration — Specification

> BED-side sink for the bbsengine6 thin-client conversion.

## 1. Why this exists

`bbsengine6.io` currently writes to stdout (door mode). For the
thin-client BED conversion, every `bbsengine6.io.echo()` /
`inputchoice()` / etc. call site needs to route its output to a
WebSocket envelope (`echo`, `menu`, etc.) on behalf of the
connected client. The sink infrastructure is the seam that makes
this work without touching every call site.

The dependency direction is: `bbsengine6` (sink protocol +
codecs) → `bed` (`BEDSink` + `ThinClientIOSink`) → game repos.

This spec covers the bed side. The bbsengine6 sink protocol,
codec primitives, and `on_connect_hook` plumbing are the
prerequisite work tracked in `bbsengine6/TODO.md` (Phases 0–5).

## 2. BEDSink (server-side)

`BEDSink` is a per-connection adapter that implements the
`bbsengine6.io.sink.Sink` protocol. It is installed via
`WebSocketServer.on_connect_hook` (option e: the hook owns the
message loop).

### 2.1 Shape

`BEDSink(websocket, server, router)` holds three references:

- `self.websocket`: the per-connection WebSocket (so it can call
  `server.send_to(websocket, envelope)`).
- `self.server`: a reference to the `WebSocketServer` (so it can
  call `send_to`).
- `self.router`: a reference to the per-process `MessageRouter`
  (from `bbsengine6/net/router.py`, gained the `MessageRouterMixin`
  API in bbsengine6 Phase 5).

Each method builds the appropriate BED envelope and calls
`await self.server.send_to(self.websocket, envelope)`:

- `echo(text, **kwargs)`: calls `bbsengine6.io.echo_render(text,
  **kwargs)` to get the rendered string, builds an `echo`
  envelope (per `echo.md`), sends it.
- `inputchoice(prompt, options, default="", **kwargs)`: builds a
  `menu` envelope (per `menu.md`), awaits `menu_reply`. The
  `request_id` is allocated via `router.next_request_id(websocket)`;
  the future is `router.get_pending_request(websocket, request_id)`.
- `inputstring(prompt, default="", **kwargs)`: builds an
  `inputstring` envelope, awaits `inputstring_reply`.
- `inputboolean`, `inputinteger`, `inputchar`, `inputdate`,
  `inputfilename`, `inputpassword`: analogous.

The `BEDSink` does NOT own the message loop. It only owns the
outgoing-send side. Incoming `*_reply` messages are dispatched
by `WebSocketServer.dispatch_message` →
`MessageRouter.handle_message` → the right service handler (e.g.
`IOServiceHandler` for `menu_reply`, `inputstring_reply`, etc.).
The `IOServiceHandler` calls
`router.resolve_pending_request(websocket, request_id, value)` to
resolve the future in the `BEDSink`.

**No new `MessageRouter` is created.** The `BEDSink` is a
per-connection writer-adapter that uses the existing per-process
`MessageRouter` (loaded via `--router`) for session access and
pending-request resolution. The `MessageRouter` is the
incoming-dispatch side; the `BEDSink` is the outgoing-send side.

### 2.2 Backward compat

Door-mode game routers (which run in a process without
`WebSocketServer` / `BED`) don't install a `BEDSink`; they get
the default `DefaultSink` behavior.

### 2.3 on_connect_hook

`bed/main.py` registers an `on_connect_hook` that:

1. Builds a per-connection `BEDSink(websocket, server, router)`.
2. Installs the sink via `token = set_io_sink(bed_sink)` (from
   `bbsengine6.io.sink`).
3. Runs the message loop (reads envelopes from the WebSocket,
   dispatches to `router.handle_message`, sends responses).
4. In the `finally` block, calls `reset_io_sink(token)` and
   `router.cleanup_session(websocket)`.

The hook signature is
`async def on_connect_hook(websocket, router)`. The `router` is
the per-process `MessageRouter` (passed in by the
`WebSocketServer`).

## 3. ThinClientIOSink (client-side)

`ThinClientIOSink(websocket)` is the client-side `Sink`
implementation. Each method builds the appropriate envelope and
sends it over the WebSocket to the BED process; the response is
awaited and returned to the caller.

The thin client uses this `IOSink` to replace the existing
`sys.modules['bbsengine6.io']` swap in `empyre/io_bridge.py`
(and equivalent in casino / murdermotel / etc.). The
`sys.modules` swap continues to work as a v1 default; the
`IOSink` is a future option.

## 4. Codec integration

- **Phase 3 — `echo_render`**: `BEDSink.echo` calls
  `bbsengine6.io.echo_render(text, **kwargs)` to get the rendered
  string, ships in `echo` envelope's `text` field. The thin
  client renders `text` verbatim. No client-side MCI rendering.
- **Phase 4 — `mci.parse`**: `BEDSink.echo` calls
  `bbsengine6.io.mci.parse(text)` to get the token list, ships
  in `echo` envelope's `payload.mci` field. The `mci` field is
  optional in v1 (a future client can ignore it). The `text`
  field is always populated.

## 5. Decisions (v1)

- `BEDSink` is installed via the `WebSocketServer.on_connect_hook`
  (option e: the hook owns the message loop). The hook signature
  is `async def on_connect_hook(websocket, router)`.
- The `BEDSink` does not own the message loop. It only owns the
  outgoing-send side. The `MessageRouter` is the incoming-dispatch
  side. The `IOServiceHandler` resolves pending-request futures
  via `router.resolve_pending_request(...)`.
- The thin-client `IOSink` lives in `bed/client/io_sink.py`
  (shared across all games). The `sys.modules` swap continues to
  work as the v1 default; the `IOSink` is a future option.
- `BEDSink.echo` populates the `echo` envelope's `text` field via
  `bbsengine6.io.echo_render` and the `mci` field via
  `bbsengine6.io.mci.parse`.

## 6. Open follow-ups

- [ ] `bed/sinks/bed_sink.py` — `BEDSink` class.
- [ ] `bed/client/io_sink.py` — `ThinClientIOSink`.
- [ ] `bed/main.py` — register `on_connect_hook` that installs
      the sink and owns the message loop.
- [ ] `bed/tests/test_bed_sink.py` — `BEDSink.echo` builds an
      `echo` envelope and calls `server.send_to` (not a write to
      stdout); `BEDSink.inputchoice` builds a `menu` envelope,
      records the pending request, returns the hotkey; etc.
- [ ] `bed/tests/test_bed_sink_on_connect.py` — sink installed
      via the hook, persists for connection lifetime, reset on
      disconnect, no leak across connections.
- [ ] `bed/tests/test_thin_client_io_sink.py` — thin-client
      sink sends envelopes over WebSocket.
- [ ] `bed/tests/test_bed_sink_echo_render.py` — `BEDSink.echo`
      populates `text` via `echo_render`.
- [ ] `bed/tests/test_bed_sink_mci.py` — `BEDSink.echo`
      populates `mci` via `mci.parse`.

### bbsengine6 prerequisites (blocking)

- [ ] `bbsengine6/io/sink.py` — `Sink` protocol, `DefaultSink`,
      `set_io_sink` / `reset_io_sink` (Phase 0).
- [ ] `bbsengine6/io/echo_render.py` (Phase 1).
- [ ] `bbsengine6/io/mci.py` — `mci.parse` / `mci.render`
      (Phase 2).
- [ ] `bbsengine6/io.echo` returns the rendered string (Phase 3).
- [ ] Sink-based variants for other primitives (Phase 4).
- [ ] `WebSocketServer.on_connect_hook` (Phase 5; partial).

## 7. Adoption

- All consumers (empyre, casino, murdermotel, mistermcfeely,
  zoid6, bbsengine6 TUI) switch to thin-client `IOSink` via
  `sys.modules` swap once `BEDSink` ships.
- Door mode (legacy TUI) is unaffected — door mode is
  host-driven, no sink involved.

## 8. See also

- `echo.md` — the `echo` envelope BEDSink produces.
- `menu.md` — the `menu` envelope BEDSink.inputchoice produces.
- `../handbook/ARCHITECTURE.md` §4 — bbsengine6-side
  prerequisites (Sink Infrastructure series).
- `bbsengine6/TODO.md` — sink infrastructure phases.
- `bbsengine6/handbook/specs/` — sibling spec tree.
