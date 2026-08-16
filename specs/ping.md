# bed PingService — Specification

> **Audience:** implementers working on `bed` and downstream
> consumers (`zoid6`, `empyre`, `casino`, `murdermotel`, `mistermcfeely`,
> the `bbsengine6` TUI, ops tools). This is the entry-point spec for the
> bed instance-identity probe.
>
> **See also:**
> - [`SPEC.md`](../SPEC.md) — bed daemon entry point (what works, what
>   doesn't, v1/v1.1/v1.2/v1.3/v1.4/v2 phase gates, code that moved
>   to `bbsengine6`, bbsengine6-side prerequisites for each bed
>   service).
> - [`README.md`](../README.md) — quick-start, CLI flags, routers,
>   console scripts, layout.
> - [`specs/message.md`](message.md), [`specs/auth.md`](auth.md),
>   [`specs/bank.md`](bank.md) — sibling service specs covering the
>   `bed message`, `bed auth`, and `bed bank` CLIs and the
>   corresponding server-side services.
> - [`CHANGELOG.md`](../CHANGELOG.md) — release history.

---

## 1. Overview

`bed.PingService` is the smallest of bed's in-process services. It
answers the wire-protocol `ping` message with a `pong` envelope that
carries the bed instance's identity (`name` + `version`), so a probe
client can verify it is talking to the expected daemon without
performing an `auth` round-trip. The `timestamp` field on the wire
is echoed back when present so probes can measure round-trip
latency.

The service exists for one reason: a `wscat` operator (or an
automation harness) needs to know which bed daemon they are talking
to before they issue credentials. The `name` field is the per-instance
identifier set via `--bed-name` or `bed.name` in `bed.json`. The
`version` is :data:`bed.__version__` (the wheel's `_version.py`
datestamp/githash).

```json
C→S {"type":"ping", "timestamp": 1700000000.0}
S→C {"type":"pong",
     "name": "bed",
     "version": "0.0.1.dev202608152158",
     "timestamp": 1700000000.0}
```

### 1.1 What this spec covers

- Wire protocol: the single `ping` request and `pong` response shape.
- Service registration: the order `BED.start()` registers services
  in, and why PingService must be LAST so it always wins over any
  `ping` handler the loaded router registered first.
- Server runtime: `PingService` lifecycle, construction, and the
  single handler.
- Configuration: `--bed-name` CLI flag and the `bed.name` JSON key;
  default secret-file path derivation when name is non-default.
- Testing: `test_ping_service.py` (167 lines).

### 1.2 What this spec does NOT cover

- Other bed services (`AuthService` / `BankService` /
  `MessageService`). See the sibling specs.
- The router's own `ping` handler if it has one — PingService
  always wins and the router's handler is silently shadowed (with a
  WARNING logged by `WebSocketServer.register_service`).
- WebSocket-level connectivity. The TCP probe that `bed.client.probe.probe_bed`
  performs is what bed-mode CLIs use to choose between the WS and
  direct-DB transports; PingService is the *application-level*
  handshake after the WS upgrade.

---

## 2. Wire protocol

### 2.1 `ping` request

```json
{"type": "ping"}
```

The `timestamp` field is optional. When present and a float, it is
echoed back in the `pong` envelope so probes can compute round-trip
latency. Any other client-supplied fields are ignored.

### 2.2 `pong` response

```json
{
  "type": "pong",
  "name": "<bed_name>",
  "version": "<bed.__version__>",
  "timestamp": <echoed from request, may be null>
}
```

- `name` is the per-instance bed name set via `--bed-name` or
  `bed.name` in `bed.json`. Defaults to `"bed"`.
- `version` is :data:`bed.__version__` (imported lazily inside the
  handler so a partial install with no `_version.py` fails loudly
  instead of silently returning `None`).
- `timestamp` echoes the request's value verbatim. When the request
  had no `timestamp`, the response has `"timestamp": null`.

### 2.3 No error envelopes

PingService never returns an error envelope. A malformed `ping`
(e.g. `"type": "Ping"` with the wrong case) is filtered by the
`HANDLED_TYPES` match; `handle_message` returns `None` for any
non-`ping` type, leaving the message for the next registered service
or the router fallback.

---

## 3. Service registration

`PingService` is registered alongside the other services inside
`BED.start()` (`bed/src/bed/main.py:523-534`). The wiring order is
deliberate: PingService must be the LAST service to call
`server.register_service(self, ["ping"])` so its registration
overwrites any `["ping"]` handler the router already registered.

### 3.1 Wiring order in `BED.start()`

1. `await self._start_auth(db_args)` — only when auth is enabled
   (`token_persistence != "none"` AND router is not the bbsengine6
   no-credential stub).
2. `WebSocketServer(host, port)` constructed.
3. If `auth_service` was constructed, `auth_service.register_all(server)`.
4. If `MessageRouterClass` was provided, instantiate and
   `router.register_all(server)`. Some routers (e.g. zoid6's
   `MonikerAuthRouter`) register a `ping` handler as a fallback.
5. If `--no-message-service` is NOT set, construct `MessageService`
   and `register_all(server)`.
6. If `--no-bank-service` is NOT set, construct `BankService` and
   `register_all(server)`.
7. **`PingService` is constructed LAST and registered LAST**, so its
   `["ping"]` registration wins over any router-side `["ping"]`.

### 3.2 Why PingService always wins

Every bed instance surfaces its own `name` + `version` regardless of
which router is loaded — that's the contract. Letting a router
reply to `ping` would leak router-specific state into a probe that
should report the bed instance identity. The overwrite is intentional
and `bbsengine6.net.transport.register_service` emits a WARNING on
the swap so the overwrite is visible in the log:

```
WARNING: WebSocketServer.register_service: overwriting handler for
  type 'ping' (was <router_cls>, now PingService)
```

### 3.3 No token gate

PingService requires no authentication, no token, no bound session.
A `ping` against an unbound WS is a valid operation — that's the
whole point of the probe.

---

## 4. Server runtime — `PingService`

File: `bed/src/bed/api/ping.py` (68 lines).

### 4.1 Construction

```python
def __init__(
    self,
    args: Any,
    session_manager: SessionManager,
    name: str,
) -> None:
```

- `args` — bed argparse namespace (unused at the moment but kept for
  parity with the other services and future per-config flags).
- `session_manager` — `SessionManager` from
  `bbsengine6.session` (kept on `self.sessions` via `BaseService.__init__`,
  unused by PingService itself).
- `name` — the per-instance bed name. Stored on `self.name` as a
  string; empty / None values fall back to `"bed"` so the
  default-secret-path derivation stays sane.

### 4.2 `register_all(server)`

```python
def register_all(self, server: Any) -> None:
    server.register_service(self, list(self.HANDLED_TYPES))
```

`HANDLED_TYPES = ("ping",)`. The flat-list call to
`server.register_service` overwrites any prior handler for
`"ping"`. Bed's startup sequence guarantees the previous handler
(if any) came from the router, not from another bed service.

### 4.3 `handle_message`

```python
async def handle_message(
    self,
    server: Any,
    websocket: Any,
    path: str,
    message: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if message.get("type") != "ping":
        return None
    from bed._version import __version__

    return {
        "type": "pong",
        "name": self.name,
        "version": __version__,
        "timestamp": message.get("timestamp"),
    }
```

Key points:

- The `type` filter is defensive: `HANDLED_TYPES` already restricts
  the dispatch in `WebSocketServer`, but checking again inside the
  handler means a future refactor that loosens the dispatch won't
  silently allow non-`ping` messages to return a `pong`.
- `__version__` is imported lazily inside the handler so a bed
  install with no `_version.py` fails loudly (ImportError) instead
  of returning `"pong"` with `version: None`.
- The `websocket` and `path` arguments are unused. They are kept on
  the signature to match `BaseService.handle_message`.
- The return is sync (not awaited); the handler is declared `async`
  only to match the WS dispatch contract.

### 4.4 No lifecycle methods

Unlike `MessageService` (which has `start_listener` /
`stop_listener`), PingService has no background task. It is purely
request/response.

---

## 5. Client runtime — `bed.client.probe`

`PingService` has no dedicated client class because the only consumer
that uses it is `wscat` (or a similar hand-rolled WS client). Bed's
own CLIs (`bed auth`, `bed bank`, `bed message`) use the
`bed.client.probe.probe_bed` TCP probe to decide whether to use the
WS or the direct-DB backend, which is a different layer.

### 5.1 The TCP probe vs. the application-level ping

`probe_bed` (`bed/src/bed/client/probe.py`) is a synchronous TCP
`connect()` to `args.bed_host:args.bed_port` with a
`--bed-probe-timeout`-bounded socket timeout. It does not speak the
WS protocol — it just confirms the daemon's port is open. CLIs use
this to decide whether to call `select_backend` (which raises
`BedNotReachable` when both probe and `--direct` fail) or skip
straight to direct-DB mode.

PingService is the *next* layer: it confirms the daemon is actually
a bed daemon (vs. some other process listening on the same port)
and surfaces the instance identity. A full client would do:

1. TCP probe via `probe_bed`.
2. WS upgrade via `BedConnection`.
3. `{"type": "ping"}` → expect `{"type": "pong", "name": "..."}`.

A v1 simplification is to skip step 3 and rely on the TCP probe plus
the fact that nothing else should be listening on `--bed-port`. The
CLI tools do not currently drive the `ping` round-trip; they assume
any TCP-reachable daemon is a bed daemon.

### 5.2 Reserved for future use

A future CLI flag (e.g. `--bed-require-ping`) could drive the WS
handshake round-trip before authentication. The PingService is in
place to support that; nothing currently uses it.

---

## 6. CLI tool — `bed ping`

`bed ping` is the smoke-test CLI registered in `pyproject.toml`
(`bed = "bed.main:main"` for the daemon; `ping = "bed.tools.ping:main"`
for the probe). It is the thinnest of bed's CLI tools: it sends
`{"type": "ping"}` over the WS and prints the `pong` envelope.

### 6.1 Subcommands

`bed ping` is single-subcommand with no subparsers. Usage:

```
bed ping [--bed-host HOST] [--bed-port PORT] [--bed-path PATH]
         [--bed-call-timeout SECONDS]
```

The CLI uses the same `--bed-*` flags the other CLIs share (via
`bed.tools._routing.build_client_args`). Defaults: `localhost:8765/`,
5-second call timeout.

### 6.2 Output

On success, prints the pong envelope as JSON (or as a formatted
table on TTY). On `BedUnavailable`, prints the standard
"bed unreachable at host:port" message and exits non-zero.

### 6.3 Identity-aware smoke test

`bed ping` is the operator's first check after starting a bed
daemon. The output's `name` + `version` is the canonical instance
identity — it should match the operator's expectation
(e.g. `mybbs` for a custom-named instance).

---

## 7. Configuration

### 7.1 `--bed-name`

```bash
bed --bed-name mybbs               # CLI override
```

```json
"bed": {
  "name": "mybbs"
}
```

CLI > `bed.json` > default `"bed"`. Empty / missing / whitespace-only
names fall back to `"bed"`.

### 7.2 Default secret-file path derivation

The bed HMAC secret file lives at `~/.config/bed/<name>.secret`.
With the default `name = "bed"` this resolves to the historical
`~/.config/bed/bed.secret`, so existing installs are unaffected.
Custom names (e.g. `mybbs`) yield `~/.config/bed/mybbs.secret`,
letting multiple bed daemons share one host without colliding on
the HMAC secret file. An explicit `--bed-secret` flag still wins
over the derived path.

### 7.3 `bed.json` section

```json
"bed": {
  "name": "bed",
  "secret_path": "~/.config/bed/bed.secret",
  ...
}
```

`secret_path` is set by `_apply_bed_name_config` from `name` if not
already set; CLI `--bed-secret` always wins. SIGHUP reload treats
`bed_name` as a structural change (warns "restart required")
because changing it would move the secret file.

### 7.4 Precedence

CLI > `bed.json` > argparse default, same as every other bed knob.

---

## 8. Error handling & failure modes

### 8.1 Router-side `ping` handler

If the loaded router registered a `["ping"]` handler before
PingService did, `WebSocketServer.register_service` logs a WARNING
and overwrites. The router's handler is silently shadowed; nothing
calls it. This is intentional.

### 8.2 Malformed `ping`

A `{"type": "ping", ...}` with extra unknown fields is accepted
verbatim and echoed back in `timestamp` (if it was a float) or
silently dropped (if not). No validation, no error envelope.

### 8.3 `__version__` import failure

The handler does `from bed._version import __version__` lazily. If
the wheel was installed without `_version.py` (rare; a malformed
sdist), the WS frame will raise `ImportError` inside the handler.
`WebSocketServer` logs and closes the socket. The next probe will
fail too. The fix is to reinstall the wheel with a complete
`_version.py`.

### 8.4 Lazy router fallback (legacy)

A WS frame with an unknown `type` (e.g. `"Ping"` with the wrong
case) is not handled by PingService and falls through to the next
registered service. If nothing handles it, the router's
`handle_message` may return a generic error envelope, or the
connection may be closed.

---

## 9. Testing

### 9.1 `bed/src/bed/tests/test_ping_service.py` (167 lines)

Coverage:

- `test_ping_service_registers_handled_types` — `HANDLED_TYPES`
  is `("ping",)`.
- `test_handle_message_returns_pong_with_name_and_version` — the
  handler returns the expected envelope shape.
- `test_handle_message_echoes_timestamp` — `timestamp` on the
  request is echoed back in the response.
- `test_handle_message_ignores_non_ping_types` — a `"type":
  "Ping"` (wrong case) returns `None`, leaving the message for
  the next service.
- `test_constructor_falls_back_to_default_name` — empty / None
  `name` becomes `"bed"`.
- `test_ping_service_register_overwrites_router_handler` — when a
  router has registered `["ping"]` first, `PingService.register_all`
  overwrites it (the WARNING is captured by patching the logger).
- `test_pong_envelope_uses_live_version` — `version` is the same
  string as `bed._version.__version__` at runtime.

### 9.2 Out-of-test surface (deferred)

- End-to-end WS handshake against a real bed daemon. A test in
  `test_ping_integration.py` that opens a real WS, sends `{"type":
  "ping"}`, and asserts the pong envelope is in the same shape as
  the unit tests. Marked `@pytest.mark.integration` and skipped
  in the default run.
- The TCP probe (`bed.client.probe.probe_bed`) is unit-tested in
  `test_tools_routing.py` with mocked sockets.

### 9.3 Running the suite

```bash
cd bed
PYTHONPATH=src:../bbsengine6/py/src pytest src/bed/tests/test_ping_service.py -q
```

---

## 10. Security

### 10.1 Threat model

- **Identity disclosure**: a `ping` reply leaks `name` and
  `version`. Both are non-sensitive (the operator already knows what
  daemon they started), but a low-value attacker can confirm a bed
  daemon is running and at what version. This is acceptable for the
  probe use case; it is no worse than a TCP `connect()`.
- **No auth gate**: `ping` is unauthenticated by design. A flood of
  pings is rate-limited only by the WS dispatch loop. A production
  deployment behind a reverse proxy should still rate-limit at the
  proxy layer if abuse is a concern.

### 10.2 Out-of-scope

- TLS — depends on the WS deployment (TLS in reverse-proxy /
  systemd unit, plain WS in dev).
- Rate limiting — currently unbounded; a future v2 could add a
  per-IP token bucket on the daemon side.
- Probe authentication — the design assumes `ping` is a public
  operation.

---

## 11. Open work

### 11.1 Phase 8 (open)

- A `bed ping` invocation that prints the pong envelope in a
  scannable format (table on TTY, JSON on pipe).
- Optional `--bed-require-ping` flag on the CLIs to do an
  application-level handshake before issuing credentials.

### 11.2 Phase 9 (partial)

- Documentation that the bbsengine6 TUI can run without a local
  bed instance for read-only display, but most ops require bed.
  PingService is the canonical "is bed there?" probe.

### 11.3 v2 roadmap

- An auth-required probe variant: `{"type": "ping_auth"}` returns
  the per-moniker session summary (read-only) without exposing
  `name` / `version` to anonymous probers.
- Heartbeat / keep-alive on the WS using the `ping` round-trip; a
  missed `pong` within a configurable timeout closes the connection.

---

## 12. File map

| File                                                   | Role                                      |
|--------------------------------------------------------|-------------------------------------------|
| `bed/src/bed/api/ping.py`                              | `PingService` class, `handle_message`     |
| `bed/src/bed/main.py:523-534`                          | `BED.start()` wires PingService LAST      |
| `bed/src/bed/main.py:653-685`                          | `BED.stop()` / cleanup paths              |
| `bed/src/bed/main.py:660-665`                          | startup banner prints `name=<bed_name> version=<...>` when active |
| `bed/src/bed/lib.py`                                   | `--bed-name` / `--bed-secret` CLI flags   |
| `bed/src/bed/data/bed.json:1-10`                       | `bed.name` config block                   |
| `bed/src/bed/client/probe.py`                          | TCP probe (used by CLIs for backend selection) |
| `bed/src/bed/tools/ping.py`                            | `bed ping` CLI smoke-test wrapper         |
| `bed/src/bed/tests/test_ping_service.py` (167 LOC)     | Service unit tests                        |
| `bed/src/bed/tests/test_tools_routing.py` (131 LOC)    | TCP probe + `select_backend` routing tests |
| `bed/SPEC.md`                                          | Bed daemon entry-point spec               |
| `bed/README.md`                                        | Quick-start, CLI flags, console scripts   |
| `bed/CHANGELOG.md`                                     | Release history                           |

---

## 13. Versioning

This spec tracks the bed daemon. Phase gates per `bed/SPEC.md`:

- **v1.0** (current stable) — daemon core, AuthService, MessageService,
  BankService, PingService, FHS install.
- **v1.1** (in flight) — MessageService GA + cross-repo adoption;
  PingService rides along unchanged.
- **v1.2 / v1.3 / v1.4 / v2** — design-only; not affected by this
  spec beyond what is listed in Section 11.
